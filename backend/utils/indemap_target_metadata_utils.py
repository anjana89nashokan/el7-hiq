"""
IndeMap target metadata integration helpers.

Purpose:
  - Provide a single place to fetch target-table metadata JSON from an IndeMap backend.
  - Normalize the returned JSON into the existing Step 1 TargetSchema contract.

Notes:
  - Fetch function is intentionally a placeholder so integration teams can wire
    their internal API/auth transport without changing Step 1 flow.
  - Normalization reuses current TargetSchema semantics so Step 2 remains unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.mapping_ingestion.models import (
    AlternateKeyGroup,
    SCDHints,
    TargetColumn,
    TargetSchema,
    TargetTable,
)
from utils.mapping_ingestion_tools import (
    _adjust_data_type,
    _apply_primary_key_flags,
    _canonicalize_data_type,
    _detect_scd_hints,
    _finalize_primary_keys,
)


def fetch_indemap_table_metadata_for_pair(
    *,
    database_name: str,
    table_name: str,
) -> dict[str, Any]:
    """
    Placeholder hook for fetching one (database_name, table_name) pair.

    Input example:
      database_name="DB_AEDWP1V"
      table_name="PRV_DATA"

    Expected pair response (flexible):
      - either a single table payload:
          {"table": {...}}
      - or batch-like payload:
          {"tables": [...], "not_found": [...]}

    Example success payload (single table form):
      {
        "table": {
          "database": "DB_AEDWP1V",
          "database_name": "DB_AEDWP1V",
          "table_name": "PRV_DATA",
          "logical_name": "IHG-DART-EDW-PROD_DB_AEDWP1V_PRV_DATA",
          "description": "Provider data ...",
          "table_type": "TBL",
          "columns": [
            {
              "attribute_name": "AEDW_PRV_SK",
              "logical_attribute_name": "AEDW Provider Surrogate Key",
              "attribute_description": "A unique identifier ...",
              "data_type": "NUMERIC",
              "length": 18,
              "nullability": False,
              "is_surrogate_key": True,
              "is_code_column": False,
              "is_technical": False,
            }
          ],
          "alternate_keys": [{"name": "AK1", "column_names": ["AEDW_PRV_SK"]}]
        }
      }

    Example not-found payload:
      {
        "tables": [],
        "not_found": [{"database_name": "DB_AEDWP1V", "table_name": "PRV_DATA"}]
      }

    Required table payload fields should be compatible with build_target_schema_from_indemap_json().

    Error contract:
      - Raise RuntimeError for transport/auth/provider failures.
      - Return `not_found` (not exceptions) when the pair is valid but missing in source system.
    """
    raise RuntimeError(
        "IndeMap table metadata provider is not wired yet. "
        "Implement fetch_indemap_table_metadata_for_pair(database_name, table_name) "
        "in server/utils/indemap_target_metadata_utils.py."
    )


def fetch_indemap_target_metadata(
    *,
    db_table_pairs: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Fetch and aggregate target metadata for explicit (database, table) duos.

    Input:
      db_table_pairs = [
        {"database_name": "DB_AEDWP1V", "table_name": "PRV_DATA"},
        {"database_name": "DB_AEDWP1V", "table_name": "PRV_MAP"},
      ]

    Output (normalized aggregate envelope):
      {
        "total_tables": <int>,
        "tables": [...],
        "not_found": [...],
        "timestamp": "<UTC ISO8601>"
      }

    Example output:
      {
        "total_tables": 2,
        "tables": [
          {"database_name": "DB_AEDWP1V", "table_name": "PRV_DATA", "columns": [...]},
          {"database_name": "DB_AEDWP1V", "table_name": "PRV_MAP", "columns": [...]}
        ],
        "not_found": [],
        "timestamp": "2026-02-24T18:20:00+00:00"
      }

    Behavior contract:
      - Calls `fetch_indemap_table_metadata_for_pair` once per unique duo.
      - De-duplicates duplicate input pairs.
      - Fills missing database/table fields from requested pair context.
      - Aggregates not-found pairs without failing the whole request.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in db_table_pairs or []:
        if not isinstance(raw, dict):
            continue
        db = str(raw.get("database_name") or "").strip()
        tbl = str(raw.get("table_name") or "").strip()
        if not db or not tbl:
            continue
        key = (db, tbl)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)

    if not pairs:
        return {
            "total_tables": 0,
            "tables": [],
            "not_found": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    aggregate_tables: list[dict[str, Any]] = []
    aggregate_not_found: list[dict[str, str]] = []

    for db, tbl in pairs:
        pair_payload = fetch_indemap_table_metadata_for_pair(database_name=db, table_name=tbl)
        if not isinstance(pair_payload, dict):
            raise RuntimeError(
                f"Invalid IndeMap response for pair ({db}, {tbl}): expected object."
            )

        tables = list(pair_payload.get("tables") or [])
        if not tables and isinstance(pair_payload.get("table"), dict):
            tables = [pair_payload["table"]]

        if tables:
            for t in tables:
                if not isinstance(t, dict):
                    continue
                # Keep pair context explicit for downstream normalization.
                t.setdefault("database", db)
                t.setdefault("database_name", db)
                t.setdefault("table_name", tbl)
                aggregate_tables.append(t)
        else:
            aggregate_not_found.append({"database_name": db, "table_name": tbl})

        for nf in list(pair_payload.get("not_found") or []):
            if not isinstance(nf, dict):
                continue
            db_nf = str(nf.get("database_name") or db).strip() or db
            tbl_nf = str(nf.get("table_name") or tbl).strip() or tbl
            aggregate_not_found.append({"database_name": db_nf, "table_name": tbl_nf})

    # De-duplicate not_found rows.
    nf_seen: set[tuple[str, str]] = set()
    not_found_unique: list[dict[str, str]] = []
    for item in aggregate_not_found:
        key = (
            str(item.get("database_name") or "").strip(),
            str(item.get("table_name") or "").strip(),
        )
        if not key[0] or not key[1]:
            continue
        if key in nf_seen:
            continue
        nf_seen.add(key)
        not_found_unique.append({"database_name": key[0], "table_name": key[1]})

    return {
        "total_tables": len(aggregate_tables),
        "tables": aggregate_tables,
        "not_found": not_found_unique,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return int(value)
    except Exception:
        return None


def _to_alternate_keys(raw: Any) -> list[AlternateKeyGroup]:
    out: list[AlternateKeyGroup] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        cols = [str(c).strip() for c in (item.get("column_names") or []) if str(c).strip()]
        if not name or not cols:
            continue
        out.append(AlternateKeyGroup(name=name, column_names=cols, description=item.get("description")))
    return out


def _derive_alternate_keys_from_columns(columns: list[TargetColumn]) -> list[AlternateKeyGroup]:
    grouped: dict[str, list[str]] = {}
    for col in columns:
        for ak in (col.alternate_key_groups or []):
            key = str(ak).strip()
            if not key:
                continue
            grouped.setdefault(key, [])
            if col.attribute_name not in grouped[key]:
                grouped[key].append(col.attribute_name)
    return [AlternateKeyGroup(name=name, column_names=cols) for name, cols in grouped.items() if cols]


def _to_target_column(raw: dict[str, Any]) -> TargetColumn | None:
    attr = str(raw.get("attribute_name") or "").strip()
    if not attr:
        return None
    data_type = _adjust_data_type(attr, _canonicalize_data_type(raw.get("data_type")))
    return TargetColumn(
        attribute_name=attr,
        logical_attribute_name=raw.get("logical_attribute_name"),
        attribute_description=raw.get("attribute_description"),
        data_type=data_type,
        length=_safe_int(raw.get("length")),
        precision=_safe_int(raw.get("precision")),
        scale=_safe_int(raw.get("scale")),
        format=raw.get("format"),
        nullability=bool(raw.get("nullability", True)),
        default_value=raw.get("default_value"),
        order_no=_safe_int(raw.get("order_no")),
        is_primary_key=bool(raw.get("is_primary_key", False)),
        is_foreign_key=bool(raw.get("is_foreign_key", False)),
        alternate_key_groups=[
            str(x).strip() for x in (raw.get("alternate_key_groups") or []) if str(x).strip()
        ],
        fk_reference_table=raw.get("fk_reference_table"),
        fk_reference_column=raw.get("fk_reference_column"),
        is_surrogate_key=bool(raw.get("is_surrogate_key", False)) or attr.upper().endswith("_SK"),
        is_code_column=bool(raw.get("is_code_column", False)) or attr.upper().endswith("_CD"),
        is_technical=bool(raw.get("is_technical", False)),
    )


def _to_scd_hints(raw: Any, columns: list[TargetColumn]) -> SCDHints:
    if not isinstance(raw, dict):
        return _detect_scd_hints(columns)
    return SCDHints(
        scd_type_candidate=raw.get("scd_type_candidate", "NONE"),
        eff_dt_column=raw.get("eff_dt_column"),
        exp_dt_column=raw.get("exp_dt_column"),
        current_flag_column=raw.get("current_flag_column"),
        cdc_indicator=raw.get("cdc_indicator"),
        system_generated_columns=list(raw.get("system_generated_columns") or []),
    )


def build_target_schema_from_indemap_json(
    *,
    interface_code: str,
    payload: dict[str, Any],
) -> TargetSchema:
    """
    Normalize IndeMap JSON payload into current TargetSchema model.
    """
    tables_raw = payload.get("tables")
    if not isinstance(tables_raw, list):
        raise RuntimeError("Invalid IndeMap payload: missing 'tables' list.")

    tables: list[TargetTable] = []
    for table_raw in tables_raw:
        if not isinstance(table_raw, dict):
            continue
        table_name = str(table_raw.get("table_name") or "").strip()
        table_id = table_name or str(table_raw.get("table_id") or "").strip()
        if not table_id:
            continue

        columns: list[TargetColumn] = []
        for col_raw in list(table_raw.get("columns") or []):
            if not isinstance(col_raw, dict):
                continue
            col = _to_target_column(col_raw)
            if col:
                columns.append(col)

        pk_cols = [c.attribute_name for c in columns if bool(getattr(c, "is_primary_key", False))]
        pk_cols = _finalize_primary_keys(columns, pk_cols)
        _apply_primary_key_flags(columns, pk_cols)

        alternate_keys = _to_alternate_keys(table_raw.get("alternate_keys"))
        if not alternate_keys:
            alternate_keys = _derive_alternate_keys_from_columns(columns)

        table = TargetTable(
            table_id=table_id,
            database=table_raw.get("database"),
            database_name=table_raw.get("database_name"),
            schema_name=table_raw.get("schema_name"),
            table_name=table_name or table_id,
            logical_name=table_raw.get("logical_name"),
            description=table_raw.get("description"),
            workstream=table_raw.get("workstream"),
            table_type=table_raw.get("table_type"),
            columns=columns,
            primary_key=pk_cols,
            alternate_keys=alternate_keys,
            scd_hints=_to_scd_hints(table_raw.get("scd_hints"), columns),
        )
        tables.append(table)

    by_table_id = {t.table_id: t for t in tables}
    return TargetSchema(interface_code=interface_code, tables=tables, by_table_id=by_table_id)
