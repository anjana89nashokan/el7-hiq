from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from utils.gcs_artifact_utils import download_json_uri

from ..prompts import metadata as metadata_prompt
from ..schemas import JudgeMetadataRequest, LayerJudgmentResponse
from .base import run_layer_judge


logger = logging.getLogger(__name__)

# Keys used only as identifiers inside a filespec dict — skip when enumerating
# metadata fields so we don't double-count names as required content.
_FILESPEC_ID_KEYS = {"name", "filespec", "file", "id"}

# Regex for keys like "extracted_file1", "extracted_file2", …
_EXTRACTED_FILE_RE = re.compile(r"^extracted_file\d+$")


# --------------------------------------------------------------------------- #
# Source-artifact normalisation
# --------------------------------------------------------------------------- #


def _normalize_requirement_layer(brd_payload: Dict[str, Any]) -> Dict[str, Any]:
    return brd_payload.get("validated_requirement_layer") or brd_payload


def _normalize_layout(layout_payload: Dict[str, Any]) -> Dict[str, Any]:
    return (
        layout_payload.get("file_layout")
        or layout_payload.get("layout")
        or layout_payload
    )


def _layout_tables(layout: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in (
        "tables", "file_layout_tables", "sheets",
        "layout_tables", "file_tables", "source_tables",
    ):
        value = layout.get(key)
        if isinstance(value, list):
            return value
    return []


# --------------------------------------------------------------------------- #
# Required / produced enumeration
# --------------------------------------------------------------------------- #


def _enumerate_required_items(
    req_layer: Dict[str, Any], layout: Dict[str, Any]
) -> List[dict]:
    """
    Required = every attribute / concept the BRD + layout demand that the
    metadata extractor must produce.

    We enumerate:
      1. ALL fields from each BRD filespec (not just a hardcoded subset).
      2. Every column from every layout table.
      3. BRD requirements (each one is a coverage expectation).
      4. BRD target tables / generic tables.
      5. BRD business rules.
      6. Fallback: if nothing else matched, enumerate top-level BRD sections.
    """
    items: List[dict] = []

    # --- 1. BRD filespec-level fields (ALL keys) ---------------------------
    file_specs: Any = (
        req_layer.get("file_specs")
        or req_layer.get("filespecs")
        or req_layer.get("fileSpecs")
        or req_layer.get("file_specifications")
        or []
    )
    if isinstance(file_specs, dict):
        file_specs_list = list(file_specs.values())
    elif isinstance(file_specs, list):
        file_specs_list = file_specs
    else:
        file_specs_list = []

    for idx, spec in enumerate(file_specs_list):
        spec_name = (
            (isinstance(spec, dict) and (spec.get("name") or spec.get("filespec") or spec.get("file")))
            or f"filespec_{idx}"
        )
        if isinstance(spec, dict):
            for key, value in spec.items():
                if key in _FILESPEC_ID_KEYS:
                    continue
                items.append(
                    {
                        "item_id": f"brd.filespec.{spec_name}.{key}",
                        "category": "brd_filespec_field",
                        "label": f"{spec_name}.{key}",
                        "payload": value,
                    }
                )
        elif spec is not None:
            items.append(
                {
                    "item_id": f"brd.filespec.{idx}",
                    "category": "brd_filespec_field",
                    "label": f"filespec_{idx}",
                    "payload": spec,
                }
            )

    # --- 2. Layout table columns -------------------------------------------
    for t_idx, table in enumerate(_layout_tables(layout)):
        if not isinstance(table, dict):
            continue
        table_name = table.get("name") or table.get("table") or f"table_{t_idx}"
        columns = table.get("columns") or table.get("rows") or table.get("attributes") or []
        if not isinstance(columns, list):
            continue
        for c_idx, col in enumerate(columns):
            col_name = (
                (isinstance(col, dict) and (col.get("name") or col.get("column") or col.get("attribute")))
                or (isinstance(col, str) and col)
                or f"col_{c_idx}"
            )
            items.append(
                {
                    "item_id": f"layout.{table_name}.attribute.{col_name}",
                    "category": "layout_attribute",
                    "label": f"{table_name}.{col_name}",
                    "payload": col,
                }
            )

    # --- 3. BRD requirements -----------------------------------------------
    requirements = req_layer.get("requirements") or []
    if isinstance(requirements, list):
        for r_idx, req in enumerate(requirements):
            label = (
                (isinstance(req, dict) and (req.get("id") or req.get("title") or req.get("name")))
                or (isinstance(req, str) and req[:60])
                or f"req_{r_idx}"
            )
            items.append(
                {
                    "item_id": f"brd.requirement.{r_idx}.{label}",
                    "category": "brd_requirement",
                    "label": str(label),
                    "payload": req,
                }
            )

    # --- 4. BRD target / generic tables ------------------------------------
    target_tables = (
        req_layer.get("target_tables")
        or req_layer.get("generic_tables")
        or []
    )
    if isinstance(target_tables, list):
        for t_idx, table in enumerate(target_tables):
            label = (
                (isinstance(table, dict) and (table.get("name") or table.get("table") or table.get("id")))
                or (isinstance(table, str) and table)
                or f"target_table_{t_idx}"
            )
            items.append(
                {
                    "item_id": f"brd.target_table.{t_idx}.{label}",
                    "category": "brd_target_table",
                    "label": str(label),
                    "payload": table,
                }
            )

    # --- 5. BRD business rules ---------------------------------------------
    business_rules = req_layer.get("business_rules") or []
    if isinstance(business_rules, list):
        for br_idx, rule in enumerate(business_rules):
            label = (
                (isinstance(rule, dict) and (rule.get("id") or rule.get("rule") or rule.get("name")))
                or (isinstance(rule, str) and rule[:60])
                or f"rule_{br_idx}"
            )
            items.append(
                {
                    "item_id": f"brd.business_rule.{br_idx}.{label}",
                    "category": "brd_business_rule",
                    "label": str(label),
                    "payload": rule,
                }
            )

    # --- 6. Fallback: enumerate top-level BRD sections if nothing found ----
    if not items:
        for key, value in req_layer.items():
            if not value:
                continue
            items.append(
                {
                    "item_id": f"brd.section.{key}",
                    "category": "brd_section",
                    "label": key,
                    "payload": (
                        value
                        if not isinstance(value, (dict, list))
                        else f"({type(value).__name__} with {len(value)} entries)"
                    ),
                }
            )

    return items


def _enumerate_produced_items(extracted_metadata: Dict[str, Any]) -> List[dict]:
    """
    Produced = every entry the metadata extractor actually emitted.

    We enumerate:
      1. extracted_filespecs entries.
      2. ALL extracted_file* records (file1, file2, …) — dynamically discovered.
         For each file record: ALL header-level fields + every attribute.
      3. Any other top-level keys in extracted_metadata not already covered.
    """
    items: List[dict] = []
    _covered_keys: set[str] = set()

    # --- 1. extracted_filespecs --------------------------------------------
    filespecs = extracted_metadata.get("extracted_filespecs") or {}
    _covered_keys.add("extracted_filespecs")
    if isinstance(filespecs, dict):
        for key, value in filespecs.items():
            items.append(
                {
                    "item_id": f"metadata.filespecs.{key}",
                    "category": "filespec_field",
                    "label": key,
                    "payload": value,
                }
            )
    elif isinstance(filespecs, list):
        for i, entry in enumerate(filespecs):
            items.append(
                {
                    "item_id": f"metadata.filespecs.{i}",
                    "category": "filespec_field",
                    "label": f"filespec_{i}",
                    "payload": entry,
                }
            )

    # --- 2. extracted_file* records ----------------------------------------
    # Discover all keys matching extracted_file1, extracted_file2, …
    file_keys = sorted(
        k for k in extracted_metadata if _EXTRACTED_FILE_RE.match(k)
    )
    # Also check fallback key names
    for alt_key in ("files", "file_records", "extracted_files"):
        if alt_key in extracted_metadata and not file_keys:
            val = extracted_metadata[alt_key]
            if isinstance(val, list):
                # Treat each list entry as a file record
                for fi, frec in enumerate(val):
                    synth_key = f"extracted_file{fi + 1}"
                    extracted_metadata[synth_key] = frec
                    file_keys.append(synth_key)
                _covered_keys.add(alt_key)

    for file_key in file_keys:
        _covered_keys.add(file_key)
        file_data = extracted_metadata.get(file_key) or {}
        file_list = (
            file_data
            if isinstance(file_data, list)
            else [file_data] if isinstance(file_data, dict) else []
        )
        # Derive a short label from the key, e.g. "extracted_file1" → "file1"
        file_label = file_key.replace("extracted_", "")

        for f_idx, file_rec in enumerate(file_list):
            if not isinstance(file_rec, dict):
                continue
            rec_label = file_label if len(file_list) == 1 else f"{file_label}_{f_idx}"

            # Enumerate ALL header-level fields (not just a hardcoded subset)
            attributes_val = file_rec.get("attributes")
            for hdr_key, hdr_value in file_rec.items():
                if hdr_key == "attributes":
                    continue  # handled separately below
                items.append(
                    {
                        "item_id": f"metadata.{rec_label}.header.{hdr_key}",
                        "category": "file_header_field",
                        "label": f"{rec_label}.{hdr_key}",
                        "payload": hdr_value,
                    }
                )

            # Enumerate per-attribute items
            attributes = attributes_val or []
            if not isinstance(attributes, list):
                continue
            for a_idx, attr in enumerate(attributes):
                attr_name = (
                    (isinstance(attr, dict) and (attr.get("name") or attr.get("attribute") or attr.get("column")))
                    or f"attr_{a_idx}"
                )
                items.append(
                    {
                        "item_id": f"metadata.{rec_label}.attribute.{attr_name}",
                        "category": "file_attribute",
                        "label": f"{rec_label}.{attr_name}",
                        "payload": attr,
                    }
                )

    # --- 3. Other top-level keys not yet covered ---------------------------
    for key, value in extracted_metadata.items():
        if key in _covered_keys or not value:
            continue
        items.append(
            {
                "item_id": f"metadata.top.{key}",
                "category": "metadata_top_level",
                "label": key,
                "payload": value,
            }
        )

    return items


# --------------------------------------------------------------------------- #
# Judge orchestration
# --------------------------------------------------------------------------- #


async def judge_metadata(req: JudgeMetadataRequest) -> LayerJudgmentResponse:
    brd_payload = download_json_uri(req.brd_uri)
    layout_payload = download_json_uri(req.layout_uri)

    req_layer = _normalize_requirement_layer(brd_payload)
    layout = _normalize_layout(layout_payload)

    required_items = _enumerate_required_items(req_layer, layout)
    produced_items = _enumerate_produced_items(req.extracted_metadata)

    user_prompt = metadata_prompt.build_user_prompt(
        brd_requirement_layer=req_layer,
        layout=layout,
        extracted_metadata=req.extracted_metadata,
        required_items=required_items,
        produced_items=produced_items,
    )

    return await run_layer_judge(
        layer="metadata",
        session_id=req.sessionId,
        user_id=req.userId,
        revision_number=req.revision_number,
        system_instruction=metadata_prompt.SYSTEM_INSTRUCTION,
        user_prompt=user_prompt,
        required_items=required_items,
        produced_items=produced_items,
        extra_artifact_context={"brd_uri": req.brd_uri, "layout_uri": req.layout_uri},
    )
