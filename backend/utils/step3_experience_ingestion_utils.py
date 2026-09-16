"""
Step 3.5 -> Experience ingestion (BigQuery only).

Scope (per current agreement):
  - Ingest table feedback (row-level feedback from the Step 3 table) into BigQuery.
    * If the row has structured edits + feedback: authority_level=HIGH
    * If feedback-only (no structured edits): authority_level=MED

No vector upsert yet for experience/QAs (BigQuery-only).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

from agents.mapping_generation.models import MappingRow, Step2State
from agents.mapping_review.models import Step3Decision, Step3State
from config.settings import config
from utils.evidence_text_extraction_utils import sha256_text
from utils.vectorstore_bigquery_utils import (
    ensure_vectorstore_metadata_table_exists,
    fetch_existing_chunk_hashes,
    insert_metadata_rows,
    utc_now,
)


def _parse_target_from_row_id(row_id: str) -> tuple[str | None, str | None]:
    """
    Row id formats seen in this repo:
      - PRV_MAP.PRV_DSGNTN_CD
      - PRV_DATA.AEDW_PRV_SK:RULE_1
    """
    rid = (row_id or "").strip()
    if not rid:
        return None, None
    rid = rid.split(":", 1)[0]  # strip rule instance suffix
    if "." not in rid:
        return None, None
    table_id, col = rid.split(".", 1)
    return (table_id or None), (col or None)


def _row_mapping_identity_fields_changed(patch) -> bool:
    """
    "Row remap" signal: changes that likely supersede prior issues/questions for the row.
    """
    if not patch:
        return False
    return any(
        [
            patch.rule_type is not None,
            patch.source_entity is not None,
            patch.source_field_names is not None,
            patch.lookup_tables is not None,
        ]
    )


def _row_has_any_structured_edit(patch) -> bool:
    """
    Any table edit beyond free-text feedback.

    Used for authority assignment:
      - HIGH when the BSA made an explicit structured change in the table (even if it's just join/filter text).
      - MED when feedback-only (no structured edits; only reasoning_summary).
    """
    if not patch:
        return False
    return any(
        [
            patch.rule_type is not None,
            patch.source_entity is not None,
            patch.source_field_names is not None,
            patch.lookup_tables is not None,
            patch.join_condition is not None,
            patch.row_filter_text is not None,
            patch.transformation_rules_text is not None,
            patch.special_considerations_text is not None,
        ]
    )


def compute_superseded_issue_ids(*, step2_state: Step2State, decisions: list[Step3Decision]) -> list[str]:
    """
    Superseded issues = issues attached to any row that the BSA "remapped" in the table.

    We intentionally keep this conservative and deterministic:
      - Only PATCH_ROW decisions are considered.
      - A row is "remapped" if identity fields changed (rule_type/source/lookup_tables).
      - Superseded issues are the Step 2 open issues attached to that baseline row.
    """
    baseline_by_id = {r.row_id: r for r in step2_state.column_mappings}
    out: set[str] = set()

    for d in decisions or []:
        if not d or getattr(d.decision_type, "value", str(d.decision_type)) != "PATCH_ROW":
            continue
        patch = d.row_patch
        if not patch or not _row_mapping_identity_fields_changed(patch):
            continue
        base = baseline_by_id.get(patch.row_id)
        if not base:
            continue
        for iid in (base.open_issue_ids or []):
            if iid:
                out.add(iid)

    return sorted(out)


def _build_table_feedback_text(*, baseline: MappingRow, decision: Step3Decision) -> str:
    """
    Canonical text payload stored in BigQuery for table feedback experience.
    """
    patch = decision.row_patch
    # Keep only the context that is shown in the Step 3 table + the BSA patch/feedback.
    baseline_compact = {
        "row_id": baseline.row_id,
        "target_database": baseline.target_database,
        "target_table_id": baseline.target_table.entity_id,
        "target_column_name": baseline.target_column_name,
        "target_logical_attribute_name": baseline.target_logical_attribute_name,
        "target_attribute_business_description": baseline.target_attribute_business_description,
        "target_data_type": baseline.target_data_type,
        "target_default": baseline.target_default,
        "target_nullability": baseline.target_nullability,
        "target_key": baseline.target_key,
        "rule_type": getattr(baseline.rule_type, "value", baseline.rule_type),
        "source_entity": baseline.source_entity.model_dump() if baseline.source_entity else None,
        "source_field_names": list(baseline.source_field_names or []),
        "lookup_tables": [t.model_dump() for t in (baseline.lookup_tables or [])],
        "join_condition": baseline.join_condition.model_dump() if baseline.join_condition else None,
        "row_filter_text": baseline.row_filter_text,
        "transformation_rules_text": baseline.transformation_rules_text,
        "special_considerations_text": baseline.special_considerations_text,
        "needs_review": bool(baseline.needs_review),
        "open_issue_ids": list(baseline.open_issue_ids or []),
    }

    # Make the intended mapping explicit (avoid consumers having to "fallback to baseline" logic).
    effective_rule_type = getattr(patch.rule_type, "value", None) if patch and getattr(patch, "rule_type", None) else getattr(baseline.rule_type, "value", baseline.rule_type)
    effective_source_entity = patch.source_entity.model_dump() if patch and patch.source_entity else (baseline.source_entity.model_dump() if baseline.source_entity else None)
    if patch and patch.source_field_names is not None:
        effective_source_field_names = list(patch.source_field_names or [])
    else:
        effective_source_field_names = list(baseline.source_field_names or [])

    payload = {
        "kind": "TABLE_FEEDBACK",
        # run_id is stored in BigQuery columns; keep payload focused on row context.
        "row_id": baseline.row_id,
        "target": {"table_id": baseline.target_table.entity_id, "column": baseline.target_column_name},
        "baseline_row": baseline_compact,
        "row_patch": patch.model_dump(exclude_none=True) if patch else None,
        "effective_mapping": {
            "rule_type": effective_rule_type,
            "source_entity": effective_source_entity,
            "source_field_names": effective_source_field_names,
        },
    }
    # Keep it JSON (easy for later parsing/backfill).
    return json.dumps(payload, ensure_ascii=True)


def ingest_step3_experience_to_bigquery(
    *,
    step2_state: Step2State,
    step3_state: Step3State,
    answered_by: str | None = None,
) -> dict[str, int]:
    """
    Insert table feedback + relevant Q/A answers into BigQuery.

    Returns simple counters for logging.
    """
    ensure_vectorstore_metadata_table_exists()

    baseline_by_id = {r.row_id: r for r in step2_state.column_mappings}
    # NOTE: Q/A feedback ingestion is intentionally NOT done at Step 3.5.
    # Reason: raw answers can be invalid/noisy; we only ingest Q/A after Step 4 marks the issue RESOLVED.

    ingested_at = utc_now()
    # For experience, "created_at" is when the capture happened (Step 3.5 submit time).
    created_at = utc_now()

    rows: list[dict[str, Any]] = []
    table_feedback_count = 0
    qa_feedback_count = 0

    # 1) Table feedback (from PATCH_ROW decisions only).
    for d in step3_state.decisions or []:
        if not d or d.decision_type.value != "PATCH_ROW":
            continue
        patch = d.row_patch
        if not patch:
            continue
        feedback = (patch.reasoning_summary or "").strip()
        if not feedback:
            continue

        baseline = baseline_by_id.get(patch.row_id)
        if not baseline:
            continue

        # Authority: HIGH if structured mapping fields changed, else MED (feedback-only).
        authority_level = "HIGH" if _row_has_any_structured_edit(patch) else "MED"

        # Keep full context in BigQuery text to support future backfill to Vector Search.
        chunk_text = _build_table_feedback_text(baseline=baseline, decision=d)
        chunk_hash = sha256_text(chunk_text)

        source_ref = f"{step2_state.metadata.run_id}|{patch.row_id}|TABLE"
        table_id, col = _parse_target_from_row_id(patch.row_id)

        rows.append(
            {
                "datapoint_id": str(uuid.uuid4()),
                "doc_id": str(uuid.uuid4()),
                "chunk_index": 0,
                "chunk_hash": chunk_hash,
                "chunk_text": chunk_text,
                "evidence_type": "BSA_TABLE_FEEDBACK",
                "authority_level": authority_level,
                "source_ref": source_ref,
                "interface_code": step2_state.metadata.interface_code,
                "target_table_id": table_id,
                "target_column_name": col,
                # Store the intended rule type for easy filtering later.
                "rule_type": getattr(patch.rule_type, "value", None) if patch and getattr(patch, "rule_type", None) else getattr(baseline.rule_type, "value", baseline.rule_type),
                "created_at": created_at.isoformat(),
                "ingested_at": ingested_at.isoformat(),
                "version": step3_state.metadata.schema_version,
                "is_active": True,
                "vector_index_id": None,
                "vector_deployed_index_id": None,
                "embedding_model": None,
                "embedding_dimensions": None,
            }
        )
        table_feedback_count += 1

    if rows:
        # Dedupe within BigQuery using the agreed key: (evidence_type, source_ref, chunk_hash).
        by_key: dict[tuple[str, str], list[str]] = {}
        for r in rows:
            k = (str(r.get("evidence_type")), str(r.get("source_ref")))
            by_key.setdefault(k, []).append(str(r.get("chunk_hash")))

        existing_hashes: set[str] = set()
        for (et, sr), hs in by_key.items():
            existing_hashes |= fetch_existing_chunk_hashes(evidence_type=et, source_ref=sr, chunk_hashes=hs)

        to_insert = [r for r in rows if str(r.get("chunk_hash")) not in existing_hashes]
        if to_insert:
            insert_metadata_rows(to_insert)

    return {"table_feedback": table_feedback_count, "qa_feedback": qa_feedback_count}
