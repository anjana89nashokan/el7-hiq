"""
Step 4 -> Q/A experience ingestion (BigQuery only).

Policy (per agreement):
  - We do NOT ingest raw Q/A from Step 3.5 because answers can be invalid/noisy.
  - We ingest Q/A only when Step 4 marks the related issue as RESOLVED.
  - We skip any issue_id present in Step 3.5 superseded_issue_ids.
  - We keep the payload compact: question + answer + what Step 4 applied (change log summary).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from agents.mapping_apply_review.models import Step4State
from agents.mapping_generation.models import Step2State
from agents.mapping_review.models import Step3State
from utils.evidence_text_extraction_utils import sha256_text
from utils.vectorstore_bigquery_utils import ensure_vectorstore_metadata_table_exists, fetch_existing_chunk_hashes, insert_metadata_rows, utc_now


def _parse_target_from_row_id(row_id: str) -> tuple[str | None, str | None]:
    rid = (row_id or "").strip()
    if not rid:
        return None, None
    rid = rid.split(":", 1)[0]
    if "." not in rid:
        return None, None
    table_id, col = rid.split(".", 1)
    return (table_id or None), (col or None)


def ingest_step4_resolved_qa_to_bigquery(*, step2_state: Step2State, step3_state: Step3State, step4_state: Step4State) -> dict[str, int]:
    """
    Ingest Q/A experience for issues that Step 4 resolved.
    """
    ensure_vectorstore_metadata_table_exists()

    superseded = set(step3_state.superseded_issue_ids or [])
    questions_by_id = {q.question_id: q for q in (step3_state.review_questions or [])}
    answers_by_qid = {a.question_id: a for a in (step3_state.bsa_answers or [])}

    # Pre-index change log by question_id for compact "what we applied" summaries.
    changes_by_qid: dict[str, list[dict[str, Any]]] = {}
    for ch in (step4_state.change_log or []):
        for qid in (ch.question_ids or []):
            changes_by_qid.setdefault(qid, []).append(
                {
                    "row_id": ch.row_id,
                    "field_name": ch.field_name,
                    "after_value": ch.after_value,
                    "source": getattr(ch.source, "value", str(ch.source)),
                }
            )

    created_at = utc_now()
    ingested_at = utc_now()

    rows: list[dict[str, Any]] = []
    inserted = 0

    for ir in (step4_state.issue_resolutions or []):
        if getattr(ir.status, "value", str(ir.status)) != "RESOLVED":
            continue
        if ir.issue_id in superseded:
            continue

        # Determine which question(s) contributed. Prefer Step 4 trace if available.
        used_qids = list(ir.used_question_ids or [])
        if not used_qids:
            # Fallback: infer from Step 3 questions that reference this issue and have answers.
            used_qids = [
                q.question_id
                for q in (step3_state.review_questions or [])
                if ir.issue_id in (q.issue_ids or []) and q.question_id in answers_by_qid
            ]

        if not used_qids:
            continue

        row_id = (ir.affected_row_ids or [None])[0]
        table_id, col = _parse_target_from_row_id(row_id or "")

        for qid in used_qids:
            q = questions_by_id.get(qid)
            a = answers_by_qid.get(qid)
            if not q or not a:
                continue
            if not (a.answer_text or "").strip() and not (a.selected_option_ids or []) and not (a.join_key_pairs or []) and not (a.picked_columns or []):
                continue

            payload = {
                "kind": "QA_FEEDBACK_APPLIED",
                "issue": {
                    "issue_id": ir.issue_id,
                    "issue_type": ir.issue_type,
                    "status": getattr(ir.status, "value", str(ir.status)),
                    "reason_summary": ir.reason_summary,
                },
                "target": {"table_id": table_id, "column": col, "row_id": row_id},
                "question": {
                    "question_id": q.question_id,
                    "priority": getattr(q.priority, "value", str(q.priority)),
                    "kind": getattr(q.kind, "value", str(q.kind)),
                    "question_text": q.question_text,
                    "context_summary": q.context_summary,
                },
                "answer": {
                    "answer_format": getattr(a.answer_format, "value", str(a.answer_format)),
                    "answer_text": a.answer_text,
                    "selected_option_ids": list(a.selected_option_ids or []),
                    "picked_columns": [pc.model_dump() for pc in (a.picked_columns or [])],
                    "join_key_pairs": [jk.model_dump() for jk in (a.join_key_pairs or [])],
                    "selected_rule_type": getattr(a.selected_rule_type, "value", None) if getattr(a, "selected_rule_type", None) else None,
                    "notes": a.notes,
                },
                "applied_changes": changes_by_qid.get(qid, []),
            }

            chunk_text = json.dumps(payload, ensure_ascii=True)
            chunk_hash = sha256_text(chunk_text)
            source_ref = f"{step2_state.metadata.run_id}|{ir.issue_id}|{qid}|QA_APPLIED"

            rows.append(
                {
                    "datapoint_id": str(uuid.uuid4()),
                    "doc_id": str(uuid.uuid4()),
                    "chunk_index": 0,
                    "chunk_hash": chunk_hash,
                    "chunk_text": chunk_text,
                    # Q/A experience is only ingested after Step 4 resolves the issue,
                    # but we still treat it as "MED" authority (helpful signal, not truth).
                    "evidence_type": "BSA_QA_FEEDBACK_APPLIED",
                    "authority_level": "MED",
                    "source_ref": source_ref,
                    "interface_code": step2_state.metadata.interface_code,
                    "target_table_id": table_id,
                    "target_column_name": col,
                    "rule_type": None,
                    "created_at": created_at.isoformat(),
                    "ingested_at": ingested_at.isoformat(),
                    "version": step4_state.metadata.step4_run_id,
                    "is_active": True,
                    "vector_index_id": None,
                    "vector_deployed_index_id": None,
                    "embedding_model": None,
                    "embedding_dimensions": None,
                }
            )

    if rows:
        # Dedupe within BigQuery on (evidence_type, source_ref, chunk_hash).
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
            inserted = len(to_insert)

    return {"qa_feedback_inserted": inserted}

