"""
Step 3.5 (HITL capture) - deterministic utilities.

Scope:
  - Convert UI-edited Step 2 mapping rows + UI answers/feedback into a persisted Step3State.
  - No LLM calls here (Step 4 can interpret feedback text with LLM if needed).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from agents.mapping_generation.models import JoinCondition, MappingRow, Step2State
from agents.mapping_review.models import (
    AnswerFormat,
    BsaAnswer,
    CaptureStatus,
    DecisionType,
    MappingRowPatch,
    ReviewQuestion,
    RowOutcome,
    RowReviewOutcome,
    ResolutionStatus,
    Step3Decision,
    Step3Metadata,
    Step3State,
)
from utils.step3_experience_ingestion_utils import compute_superseded_issue_ids


_EDITABLE_ROW_FIELDS = (
    "rule_type",
    "source_entity",
    "source_field_names",
    "lookup_tables",
    "join_condition",
    "row_filter_text",
    "transformation_rules_text",
    "special_considerations_text",
)


def _slug_id(prefix: str, raw: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")
    safe = safe[:120] if safe else "X"
    return f"{prefix}_{safe}"


def _jsonish_equal(a, b) -> bool:
    return (a.model_dump() if hasattr(a, "model_dump") else a) == (b.model_dump() if hasattr(b, "model_dump") else b)


def _diff_row_patch(*, baseline: MappingRow, edited: MappingRow, feedback: Optional[str]) -> MappingRowPatch | None:
    patch = MappingRowPatch(row_id=baseline.row_id)

    if baseline.rule_type != edited.rule_type:
        patch.rule_type = edited.rule_type

    if not _jsonish_equal(baseline.source_entity, edited.source_entity):
        patch.source_entity = edited.source_entity

    if (baseline.source_field_names or []) != (edited.source_field_names or []):
        patch.source_field_names = list(edited.source_field_names or [])

    if not _jsonish_equal(baseline.lookup_tables, edited.lookup_tables):
        patch.lookup_tables = list(edited.lookup_tables or [])

    # JoinCondition has defaults, so UI can send a minimal object like {"join_text": "..."}.
    if not _jsonish_equal(baseline.join_condition, edited.join_condition):
        patch.join_condition = JoinCondition.model_validate(edited.join_condition) if edited.join_condition else None

    if (baseline.row_filter_text or None) != (edited.row_filter_text or None):
        patch.row_filter_text = edited.row_filter_text

    if (baseline.transformation_rules_text or None) != (edited.transformation_rules_text or None):
        patch.transformation_rules_text = edited.transformation_rules_text

    if (baseline.special_considerations_text or None) != (edited.special_considerations_text or None):
        patch.special_considerations_text = edited.special_considerations_text

    feedback_text = (feedback or "").strip() if feedback is not None else ""
    if feedback_text:
        patch.reasoning_summary = feedback_text
        # Per workflow: feedback implies the row still needs Step 4 attention.
        patch.needs_review = True

    # If nothing changed (and no feedback), drop the patch.
    has_any_change = any(getattr(patch, f) is not None for f in _EDITABLE_ROW_FIELDS) or bool(feedback_text) or patch.needs_review is not None
    return patch if has_any_change else None


def _row_outcome_for_patch(patch: MappingRowPatch) -> RowReviewOutcome:
    # Feedback-only: keep pending so Step 4 can interpret and apply.
    changed_fields = [
        patch.rule_type,
        patch.source_entity,
        patch.source_field_names,
        patch.lookup_tables,
        patch.join_condition,
        patch.row_filter_text,
        patch.transformation_rules_text,
        patch.special_considerations_text,
    ]
    if any(v is not None for v in changed_fields):
        return RowReviewOutcome.MODIFIED
    return RowReviewOutcome.PENDING


def _build_bsa_answers(
    *,
    answers_by_question_id: Dict[str, str],
    questions_by_id: Dict[str, ReviewQuestion],
    answered_by: Optional[str],
) -> List[BsaAnswer]:
    out: List[BsaAnswer] = []
    for qid, text in (answers_by_question_id or {}).items():
        answer_text = (text or "").strip()
        if not answer_text:
            continue

        q = questions_by_id.get(qid)
        answer_format = q.answer_spec.answer_format if q else AnswerFormat.TEXT

        out.append(
            BsaAnswer(
                question_id=qid,
                answered_by=answered_by,
                answered_at=datetime.utcnow(),
                answer_format=answer_format,
                answer_text=answer_text,
            )
        )
    return out


def build_step3_state_from_ui(
    *,
    step2_state: Step2State,
    review_questions: List[ReviewQuestion],
    changed_rows: List[MappingRow],
    answers_by_question_id: Dict[str, str],
    feedbacks_by_row_id: Dict[str, str],
    answered_by: Optional[str],
    created_by: str = "Step3MainAgent",
) -> Step3State:
    baseline_by_id = {r.row_id: r for r in step2_state.column_mappings}
    questions_by_id = {q.question_id: q for q in review_questions or []}

    decisions: List[Step3Decision] = []
    outcomes_by_row: Dict[str, RowOutcome] = {}

    for edited in changed_rows or []:
        baseline = baseline_by_id.get(edited.row_id)
        if not baseline:
            # UI should only send existing rows for now (no ADD/REMOVE flows yet).
            continue

        feedback = (feedbacks_by_row_id or {}).get(edited.row_id)
        patch = _diff_row_patch(baseline=baseline, edited=edited, feedback=feedback)
        if not patch:
            continue

        decision_id = _slug_id("DEC", edited.row_id)
        decision = Step3Decision(
            decision_id=decision_id,
            decision_type=DecisionType.PATCH_ROW,
            question_id=None,
            issue_ids=list(baseline.open_issue_ids or []),
            row_patch=patch,
            created_at=datetime.utcnow(),
            created_by=answered_by,
        )
        decisions.append(decision)

        outcome = _row_outcome_for_patch(patch)
        outcomes_by_row[edited.row_id] = RowOutcome(row_id=edited.row_id, outcome=outcome, decision_ids=[decision_id])

    # Feedback-only rows: UI may submit feedback text without including the full edited row object.
    for row_id, fb in (feedbacks_by_row_id or {}).items():
        if not row_id or row_id in outcomes_by_row:
            continue
        baseline = baseline_by_id.get(row_id)
        if not baseline:
            continue
        fb_text = (fb or "").strip()
        if not fb_text:
            continue
        patch = MappingRowPatch(row_id=row_id, reasoning_summary=fb_text, needs_review=True)
        decision_id = _slug_id("DEC", row_id)
        decisions.append(
            Step3Decision(
                decision_id=decision_id,
                decision_type=DecisionType.PATCH_ROW,
                question_id=None,
                issue_ids=list(baseline.open_issue_ids or []),
                row_patch=patch,
                created_at=datetime.utcnow(),
                created_by=answered_by,
            )
        )
        outcomes_by_row[row_id] = RowOutcome(row_id=row_id, outcome=RowReviewOutcome.PENDING, decision_ids=[decision_id])

    bsa_answers = _build_bsa_answers(
        answers_by_question_id=answers_by_question_id or {},
        questions_by_id=questions_by_id,
        answered_by=answered_by,
    )

    linked_issue_ids = sorted({iid for d in decisions for iid in (d.issue_ids or [])})
    superseded_issue_ids = compute_superseded_issue_ids(step2_state=step2_state, decisions=decisions)

    metadata = Step3Metadata(
        run_id=step2_state.metadata.run_id,
        interface_code=step2_state.metadata.interface_code,
        created_at=datetime.utcnow(),
        created_by=created_by,
        schema_version="step3_state_v1",
        allow_partial_completion=True,
    )

    return Step3State(
        metadata=metadata,
        step2_metadata=step2_state.metadata,
        review_questions=review_questions or [],
        bsa_answers=bsa_answers,
        decisions=decisions,
        row_outcomes=list(outcomes_by_row.values()),
        capture_status=CaptureStatus.COMPLETED,
        resolution_status=ResolutionStatus.NOT_STARTED,
        resolved_issue_ids=[],
        linked_issue_ids=linked_issue_ids,
        superseded_issue_ids=superseded_issue_ids,
    )
