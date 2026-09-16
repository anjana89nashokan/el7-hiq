"""
Step 4 - Final validation + issue ledger (deterministic utilities).

Why this exists:
  - Keeps "post-apply validation" in one place (Subagent C responsibility).
  - No LLM calls here.
  - Derives issue statuses from post-apply state + schema validation (not LLM confidence).
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from agents.mapping_apply_review.models import (
    IssuePlan,
    IssueResolution,
    ManualAction,
    WarningItem,
    WarningType,
)
from agents.mapping_generation.models import MappingRow, OpenIssue, Step2State
from agents.mapping_ingestion.models import SharedState
from agents.mapping_review.models import Step3State
from utils.step4_apply_review_utils import _is_schema_valid_source_ref, compute_issue_status


def finalize_post_apply(
    *,
    shared_state: SharedState,
    step2_state: Step2State,
    step3_state: Step3State,
    rows_by_id: Dict[str, MappingRow],
    warnings: List[WarningItem],
    manual_actions_by_row: Dict[str, List[ManualAction]],
    issue_plans: List[IssuePlan],
) -> tuple[List[WarningItem], Dict[str, List[ManualAction]], List[IssueResolution], Dict[str, bool]]:
    """
    Post-apply finalization:
      1) Validate schema references (Step 1 source_schema)
      2) Force needs_review on invalid rows + add typed warnings/manual actions
      3) Build deterministic issue ledger from Step2 open_issues (authoritative)

    Returns:
      - warnings (extended)
      - manual_actions_by_row (extended)
      - issue_resolutions (complete ledger for all Step2 issues)
      - schema_valid_by_row_id (row_id -> bool)
    """
    warnings = list(warnings or [])
    manual_actions_by_row = {k: list(v) for k, v in (manual_actions_by_row or {}).items()}

    # ---- 1) Schema validation against Step 1
    schema_valid_by_row_id: Dict[str, bool] = {}
    now = datetime.utcnow()
    for rid, row in rows_by_id.items():
        ok, msgs = _is_schema_valid_source_ref(shared_state=shared_state, row=row)
        schema_valid_by_row_id[rid] = ok
        if ok:
            continue

        row.needs_review = True
        for msg in msgs:
            wtype = WarningType.INVALID_SOURCE_FIELD
            if "Unknown source_entity" in msg:
                wtype = WarningType.INVALID_SOURCE_ENTITY

            warnings.append(
                WarningItem(
                    warning_id=f"WARN_SCHEMA_{rid}_{int(now.timestamp())}",
                    warning_type=wtype,
                    severity="WARN",
                    message=msg,
                    row_id=rid,
                )
            )
            manual_actions_by_row.setdefault(rid, []).append(
                ManualAction(
                    action_title="Fix invalid schema reference",
                    action_details=msg,
                    suggested_location="Source Table / Source Column",
                )
            )

    # Force needs_review=True for any warned row.
    for w in warnings:
        if w.row_id and w.row_id in rows_by_id:
            rows_by_id[w.row_id].needs_review = True

    # ---- 2) Deterministic issue ledger
    answered_question_ids: Set[str] = {
        a.question_id for a in (step3_state.bsa_answers or []) if (a.answer_text or "").strip()
    }

    # Row ids where the BSA provided feedback text (reasoning_summary) in Step 3.5 capture.
    feedback_row_ids: Set[str] = set()
    for d in (step3_state.decisions or []):
        if getattr(d, "row_patch", None) and getattr(d.row_patch, "reasoning_summary", None):
            if (d.row_patch.reasoning_summary or "").strip():
                feedback_row_ids.add(d.row_patch.row_id)

    issue_plan_by_id = {p.issue_id: p for p in (issue_plans or [])}
    issue_resolutions: List[IssueResolution] = []

    for issue in (step2_state.open_issues or []):
        affected_rows = [
            r
            for r in rows_by_id.values()
            if (
                r.target_table.entity_id == issue.target_column.entity_id
                and r.target_column_name == issue.target_column.column_name
            )
        ]
        status, reason = compute_issue_status(
            issue=issue,
            affected_rows=affected_rows,
            schema_valid_by_row_id=schema_valid_by_row_id,
            feedback_row_ids=feedback_row_ids,
        )

        manual_actions: List[ManualAction] = []
        for r in affected_rows:
            manual_actions.extend(manual_actions_by_row.get(r.row_id, []))

        plan = issue_plan_by_id.get(issue.issue_id)
        if plan:
            manual_actions.extend(list(plan.manual_actions or []))

        used_question_ids = []
        for q in (step3_state.review_questions or []):
            if issue.issue_id in (q.issue_ids or []) and q.question_id in answered_question_ids:
                used_question_ids.append(q.question_id)

        used_decision_ids = [d.decision_id for d in (step3_state.decisions or []) if issue.issue_id in (d.issue_ids or [])]

        issue_resolutions.append(
            IssueResolution(
                issue_id=issue.issue_id,
                issue_type=issue.issue_type.value,
                status=status,
                affected_row_ids=[r.row_id for r in affected_rows],
                reason_summary=reason,
                manual_actions=manual_actions,
                used_decision_ids=used_decision_ids,
                used_question_ids=used_question_ids,
            )
        )

    return warnings, manual_actions_by_row, issue_resolutions, schema_valid_by_row_id
