"""
Mapping output generation + BigQuery persistence tools.

Builds two outputs from a validated requirement layer JSON stored in GCS:
  - Common Rules        (key/value pairs)
  - Transformation Rules (horizontal table with fixed columns)

BigQuery persistence is a best-effort fallback — permission errors or any
BQ failure are caught and logged; the endpoint continues and returns the
JSON payload regardless.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from utils import local_warehouse as bigquery
from google.api_core.exceptions import NotFound, Forbidden

from config.settings import config
from agents.extract_agent.pipeline_models import TransformationRule

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field-spec table for Common Rules (display label → JSON key)
# ---------------------------------------------------------------------------

_COMMON_RULES_FIELDS: list[tuple[str, str]] = [
    ("Interface Code",               "interface_code"),
    ("History Required",             "history_required"),
    ("Effective Dates From",         "effective_dates_from"),
    ("Effective Dates To",           "effective_dates_to"),
    ("Posted Dates From",            "posted_dates_from"),
    ("Posted Dates To",              "posted_dates_to"),
    ("Rolling Month Requirement",    "rolling_month_requirement"),
    ("Driver Required",              "driver_required"),
    ("Incremental History Required", "incremental_history_required"),
    ("Runout Required",              "runout_required"),
    ("Number of Months",             "number_of_months"),
    ("Sensitive Category List",      "sensitive_category_list"),
    ("De-identity Extract",          "deidentity_extract"),
    ("Comments",                     "comments"),
    ("Last Updated Date",            "last_updated_date"),
]

# Ordered column names for the transformation rules BQ table / response
TRANSFORMATION_RULE_COLUMNS: list[str] = [
    "target_entity",
    "driver_table_required",
    "history_data_pull",
    "common_filter",
    "target_attribute",
    "logical_attribute_name",
    "attribute_description",
    "data_type",
    "length",
    "precision",
    "format",
    "nullable",
    "default_value",
    "order_no",
    "cdc_indicator",
    "key_columns",
    "rule_type",
    "rule_name",
    "source_entity",
    "source_attribute",
    "join",
    "filter",
    "transformation_rule",
    "special_consideration",
    "last_updated",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_common_rules_rows(source: dict) -> list[dict]:
    return [
        {"Field": label, "Value": str(source.get(key) or "")}
        for label, key in _COMMON_RULES_FIELDS
    ]


def _build_transformation_rows(mappings: list[dict], common_rules: dict, file_specs: dict) -> list[dict]:
    """
    Convert pipeline mapping entries into TransformationRule rows.
    Returns a list of dicts matching the TransformationRule schema with proper types.
    """
    rows: list[dict] = []
    target_entity = file_specs.get("physical_file_name") or common_rules.get("interface_code") or ""
    driver_required_raw = common_rules.get("driver_required")
    history_raw = common_rules.get("history_required")
    now_str = datetime.utcnow().strftime("%Y-%m-%d")

    def _to_bool(val) -> Optional[bool]:
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("true", "yes", "y", "1")

    for idx, m in enumerate(mappings or [], start=1):
        row = TransformationRule(
            target_entity=target_entity or None,
            driver_table_required=_to_bool(m.get("driver_table_required", driver_required_raw)),
            history_data_pull=_to_bool(m.get("history_data_pull", history_raw)),
            common_filter=m.get("common_filter") or None,
            target_attribute=m.get("target_field") or None,
            logical_attribute_name=m.get("logical_attribute_name") or m.get("target_field") or None,
            attribute_description=m.get("mapping_evidence") or m.get("attribute_description") or None,
            data_type=m.get("data_type") or None,
            length=int(m["length"]) if m.get("length") is not None else None,
            precision=int(m["precision"]) if m.get("precision") is not None else None,
            format=m.get("format") or None,
            nullable=_to_bool(m.get("nullable")),
            default_value=m.get("default_value") or None,
            order_no=idx,
            cdc_indicator=m.get("cdc_indicator") or None,
            key_columns=m.get("key_columns") or ("Y" if m.get("is_key") else None),
            rule_type=m.get("rule_type") or m.get("match_type") or None,
            rule_name=m.get("rule_name") or None,
            source_entity=m.get("source_table") or None,
            source_attribute=m.get("source_field") or None,
            join=m.get("join") or None,
            filter=m.get("filter") or None,
            transformation_rule=m.get("transformation_rule") or None,
            special_consideration=m.get("special_consideration") or None,
            last_updated=m.get("last_updated") or now_str,
        )
        rows.append(row.model_dump())
    return rows


def _get_bq_client() -> bigquery.Client:
    import os
    from google.oauth2 import service_account

    if (
        hasattr(config, "CREDENTIALS_PATH")
        and config.CREDENTIALS_PATH
        and os.path.exists(config.CREDENTIALS_PATH)
    ):
        credentials = service_account.Credentials.from_service_account_file(
            config.CREDENTIALS_PATH
        )
        return bigquery.Client(credentials=credentials, project=config.BQ_PROJECT_ID)
    return bigquery.Client(project=config.BQ_PROJECT_ID)


def _ensure_bq_table(full_table_id: str, schema: list[bigquery.SchemaField]) -> None:
    client = _get_bq_client()
    try:
        client.get_table(full_table_id)
    except NotFound:
        client.create_table(bigquery.Table(full_table_id, schema=schema))
        logger.info("Created BQ table: %s", full_table_id)


def _overwrite_bq_table(full_table_id: str, rows: list[dict]) -> None:
    client = _get_bq_client()
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    job = client.load_table_from_json(rows, full_table_id, job_config=job_config)
    job.result()
    logger.info("Wrote %d rows to %s (job=%s)", len(rows), full_table_id, job.job_id)


def _try_persist_to_bq(full_table_id: str, rows: list[dict], schema: list[bigquery.SchemaField]) -> dict:
    """
    Best-effort BQ persistence. Returns a status dict — never raises.
    """
    try:
        _ensure_bq_table(full_table_id, schema)
        _overwrite_bq_table(full_table_id, rows)
        return {"table": full_table_id, "rows_written": len(rows), "status": "ok"}
    except (Forbidden, Exception) as exc:
        logger.warning("BQ persistence skipped for %s: %s", full_table_id, exc)
        return {"table": full_table_id, "rows_written": 0, "status": "skipped", "reason": str(exc)}


# ---------------------------------------------------------------------------
# BQ schemas
# ---------------------------------------------------------------------------

_COMMON_RULES_SCHEMA = [
    bigquery.SchemaField("Field", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("Value", "STRING"),
]

_TRANSFORMATION_RULES_SCHEMA = [
    bigquery.SchemaField("target_entity",          "STRING"),
    bigquery.SchemaField("driver_table_required",   "BOOL"),
    bigquery.SchemaField("history_data_pull",       "BOOL"),
    bigquery.SchemaField("common_filter",           "STRING"),
    bigquery.SchemaField("target_attribute",        "STRING"),
    bigquery.SchemaField("logical_attribute_name",  "STRING"),
    bigquery.SchemaField("attribute_description",   "STRING"),
    bigquery.SchemaField("data_type",               "STRING"),
    bigquery.SchemaField("length",                  "INTEGER"),
    bigquery.SchemaField("precision",               "INTEGER"),
    bigquery.SchemaField("format",                  "STRING"),
    bigquery.SchemaField("nullable",                "BOOL"),
    bigquery.SchemaField("default_value",           "STRING"),
    bigquery.SchemaField("order_no",                "INTEGER"),
    bigquery.SchemaField("cdc_indicator",           "STRING"),
    bigquery.SchemaField("key_columns",             "STRING"),
    bigquery.SchemaField("rule_type",               "STRING"),
    bigquery.SchemaField("rule_name",               "STRING"),
    bigquery.SchemaField("source_entity",           "STRING"),
    bigquery.SchemaField("source_attribute",        "STRING"),
    bigquery.SchemaField("join",                    "STRING"),
    bigquery.SchemaField("filter",                  "STRING"),
    bigquery.SchemaField("transformation_rule",     "STRING"),
    bigquery.SchemaField("special_consideration",   "STRING"),
    bigquery.SchemaField("last_updated",            "STRING"),
]


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------

def generate_mapping_output(
    gcs_output_uri: str,
    session_id: str,
    interface_code: Optional[str] = None,
    mappings: Optional[list[dict]] = None,
) -> dict:
    """
    1. Download the validated_requirement_layer JSON from GCS.
    2. Build common_rules rows (key/value) and transformation_rules rows (table).
    3. Attempt BQ persistence for both — failures are non-fatal (fallback).
    4. Return both datasets as JSON along with BQ persistence status.

    Parameters
    ----------
    gcs_output_uri : str
        GCS URI of the validated_requirement_layer.json.
    session_id : str
        Used to derive BQ table names.
    interface_code : str, optional
        Overrides the interface_code in common_rules when provided.
    mappings : list[dict], optional
        Pipeline mapping entries used to build transformation_rules rows.
        When None/empty, transformation_rules will be an empty list.
    """
    from utils.gcs_artifact_utils import download_json_uri

    # 1. Load JSON
    payload = download_json_uri(gcs_output_uri)
    common_rules: dict = dict(payload.get("common_rules") or {})
    file_specs: dict = dict(payload.get("file_specs") or {})
    file_attrs: dict = dict(payload.get("file_attributes_mapping") or {})

    if not file_specs.get("file_type") and file_attrs.get("file_type"):
        file_specs["file_type"] = file_attrs["file_type"]

    if interface_code:
        common_rules["interface_code"] = interface_code

    # 2. Build row sets
    common_rules_rows = _build_common_rules_rows(common_rules)
    transformation_rows = _build_transformation_rows(mappings, common_rules, file_specs)

    # 3. BQ persistence (best-effort fallback)
    uid = session_id.replace("-", "_")
    cr_table_id = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.common_rules_{uid}"
    tr_table_id = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.transformation_rules_{uid}"

    cr_bq_status = _try_persist_to_bq(cr_table_id, common_rules_rows, _COMMON_RULES_SCHEMA)
    tr_bq_status = _try_persist_to_bq(tr_table_id, transformation_rows, _TRANSFORMATION_RULES_SCHEMA)

    # 4. Return JSON payload
    return {
        "common_rules": common_rules_rows,
        "transformation_rules": transformation_rows,
        "bq_persistence": {
            "common_rules": cr_bq_status,
            "transformation_rules": tr_bq_status,
        },
    }
