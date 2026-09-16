"""
Step 4 Main Agent - ApplyReview (ADK orchestrator).

Scope for this implementation:
  - Load Step 1 SharedState + Step 2 draft mapping + Step 3.5 capture state
  - Gate on Step 3.5 capture completion
  - Use two LLM subagents (structured + cached):
      A) ReviewInterpreterAgent: row-level intent plan (patch vs feedback vs answers)
      B) PatchAndResolveAgent: issue-centric resolution plans using answers/feedback
  - Apply changes deterministically using the LLM-produced plans (policy-enforced)
  - Persist a timestamped Step4State JSON to RUNS_DIR
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from google.adk.agents import SequentialAgent

from agents.mapping_apply_review.models import (
    ArtifactKind,
    ArtifactRef,
    IssuePlan,
    IssueResolution,
    ManualAction,
    Step4Metadata,
    Step4State,
    Step4Summary,
    WarningType,
)
from agents.mapping_apply_review.sub_agents.review_interpreter_agent import (
    run_review_interpreter_agent,
    review_interpreter_agent,
)
from agents.mapping_apply_review.sub_agents.patch_and_resolve_agent import patch_and_resolve_agent, run_patch_and_resolve_agent
from agents.mapping_apply_review.sub_agents.final_validator_exporter_agent import (
    final_validator_exporter_agent,
    run_final_validator_exporter_agent,
)
from agents.mapping_apply_review.sub_agents.row_text_regenerator_agent import (
    row_text_regenerator_agent,
    run_row_text_regenerator_agent,
)
from agents.mapping_apply_review.sub_agents.review_interpreter_agent.models import (
    ReviewInterpreterBatchRequest,
    RowAnswerInput,
    RowInterpreterInput,
)
from agents.mapping_apply_review.sub_agents.patch_and_resolve_agent.models import IssueContextInput, IssuePlanBatchRequest
from agents.mapping_apply_review.sub_agents.row_text_regenerator_agent.models import (
    RowTextRegenBatchRequest,
    RowTextRegenInput,
)
from agents.mapping_generation.models import Step2State
from agents.mapping_ingestion.models import SharedState
from agents.mapping_review.models import CaptureStatus, Step3State
from config.settings import config
from utils.mapping_artifact_store import save_json

logger = logging.getLogger(__name__)
from utils.step4_apply_review_utils import (
    apply_interpretation_plans,
    extract_step3_patch_drafts,
    normalize_interpretation_plans,
)
from utils.step4_normalization_utils import (
    apply_rule_family_normalization,
    build_allowed_identifiers_for_row,
    build_feedback_locks_from_change_log,
    filter_plans_by_locked_fields,
)


def _enum_str(value: object) -> str:
    return str(getattr(value, "value", value))


step4_main_agent = SequentialAgent(
    name="step4_main_agent",
    sub_agents=[review_interpreter_agent, patch_and_resolve_agent, row_text_regenerator_agent, final_validator_exporter_agent],
    description="Step 4 orchestrator (apply Step 3 capture to Step 2 draft).",
)


def _step4_run_id() -> str:
    return f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:10]}"


def save_step4_state(step4_state: Step4State, output_dir: Path) -> str:
    """
    Persist Step4State as JSON.

    File name convention:
        <run_id>_step4_<step4_run_id>.json
    """
    _ = output_dir
    return save_json(
        "STEP4_STATE",
        step4_state.metadata.run_id,
        step4_state,
        step4_run_id=step4_state.metadata.step4_run_id,
    )


def _answers_by_row_id(*, step3_state: Step3State) -> dict[str, list[RowAnswerInput]]:
    """
    Link answers to rows using Step 3 review_questions: question_id -> row_ids.
    """
    questions_by_id = {q.question_id: q for q in (step3_state.review_questions or [])}
    out: dict[str, list[RowAnswerInput]] = {}
    for ans in (step3_state.bsa_answers or []):
        q = questions_by_id.get(ans.question_id)
        if not q:
            continue
        for rid in (q.row_ids or []):
            out.setdefault(rid, []).append(
                RowAnswerInput(
                    question_id=ans.question_id,
                    priority=q.priority.value,
                    answer_text=ans.answer_text or "",
                )
            )
    return out


async def run_step4_apply_review_pipeline(
    *,
    shared_state: SharedState,
    shared_state_uri: str,
    step2_state: Step2State,
    step2_state_uri: str,
    step3_state: Step3State,
    step3_state_uri: str,
    step3_review_package_uri: str | None = None,
    output_dir: Path | None = None,
) -> tuple[Step4State, str]:
    """
    Step 4 pipeline: interpret + apply + validate + persist.
    """
    if step3_state.metadata.run_id != step2_state.metadata.run_id:
        raise ValueError("Step 2 and Step 3 run_id mismatch.")
    if step3_state.metadata.interface_code != step2_state.metadata.interface_code:
        raise ValueError("Step 2 and Step 3 interface_code mismatch.")

    # Gate on capture completion.
    if step3_state.capture_status != CaptureStatus.COMPLETED:
        raise ValueError(f"Step 4 gated: capture_status={step3_state.capture_status} (must be COMPLETED).")

    # ------------------------------------------------------------------
    # 1) Build work context (fast indexes) and extract raw Step 3 edits
    # ------------------------------------------------------------------

    # Start from Step 2 baseline rows (do not mutate the Step2State object).
    rows_by_id = {r.row_id: r.model_copy(deep=True) for r in (step2_state.column_mappings or [])}

    patch_by_row_id, feedback_by_row_id = extract_step3_patch_drafts(step3_state=step3_state)

    # Link answers to rows using Step 3 questions.
    answers_by_row = _answers_by_row_id(step3_state=step3_state)

    # ------------------------------------------------------------------
    # 2) Subagent A (LLM): ReviewInterpreterAgent -> row intent plans
    #
    # IMPORTANT ordering:
    #   - We do NOT apply Step 3 patches first.
    #   - Instead, we feed baseline row + patch draft + feedback + linked Q/A,
    #     then deterministically apply the resulting intent plan.
    # ------------------------------------------------------------------

    row_items: list[RowInterpreterInput] = []
    for rid, row in rows_by_id.items():
        patch = patch_by_row_id.get(rid)
        feedback = (feedback_by_row_id.get(rid) or "").strip()
        linked_answers = answers_by_row.get(rid, [])
        if not patch and not feedback and not linked_answers:
            continue
        row_items.append(
            RowInterpreterInput(
                row_id=rid,
                target_table_id=row.target_table.entity_id,
                target_column_name=row.target_column_name,
                current_rule_type=_enum_str(row.rule_type),
                current_source_entity_id=(row.source_entity.entity_id if row.source_entity else None),
                current_source_fields=list(row.source_field_names or []),
                bsa_patch_draft=patch.model_dump(exclude_none=True) if patch else None,
                bsa_feedback_text=feedback or None,
                linked_answers=linked_answers,
            )
        )

    row_plans = []
    if row_items:
        row_plans = await run_review_interpreter_agent(request=ReviewInterpreterBatchRequest(items=row_items))
    row_plans = normalize_interpretation_plans(list(row_plans or []))

    # Apply row-level intent plans deterministically.
    raw_answers_concat = "\n".join((a.answer_text or "") for a in (step3_state.bsa_answers or []))
    row_change_log, row_warnings, manual_actions_by_row = apply_interpretation_plans(
        rows_by_id=rows_by_id,
        plans=row_plans,
        raw_feedback_by_row=feedback_by_row_id,
        raw_answers_concat=raw_answers_concat,
    )

    # Lock any fields set by feedback in Subagent A (feedback is authoritative).
    locked_fields_by_row_id = build_feedback_locks_from_change_log(change_log=row_change_log)

    # ------------------------------------------------------------------
    # 3) Subagent B (LLM): issue-centric resolution plans
    #
    # Build issue worklist from:
    #   - all Step 2 open_issues (authoritative)
    #   - issues linked to answered questions
    #   - issues referenced by decisions (Step 3)
    # ------------------------------------------------------------------

    issues_by_id = {i.issue_id: i for i in (step2_state.open_issues or [])}
    answered_question_ids = {a.question_id for a in (step3_state.bsa_answers or []) if (a.answer_text or "").strip()}
    questions_by_id = {q.question_id: q for q in (step3_state.review_questions or [])}

    issue_ids_from_answers: set[str] = set()
    for qid in answered_question_ids:
        q = questions_by_id.get(qid)
        if q:
            issue_ids_from_answers.update(list(q.issue_ids or []))

    issue_ids_from_decisions: set[str] = set()
    for d in (step3_state.decisions or []):
        issue_ids_from_decisions.update(list(d.issue_ids or []))

    issue_worklist_ids = set(issues_by_id.keys()) | issue_ids_from_answers | issue_ids_from_decisions

    # Build per-issue request items and batch by target table for cost consistency.
    issues_by_table: dict[str, list[IssueContextInput]] = {}
    for issue_id in sorted(issue_worklist_ids):
        issue = issues_by_id.get(issue_id)
        if not issue:
            # Unknown issue id referenced by answers/decisions; keep as warning later.
            continue

        affected_row_ids: list[str] = []
        for rid, row in rows_by_id.items():
            if (
                row.target_table.entity_id == issue.target_column.entity_id
                and row.target_column_name == issue.target_column.column_name
            ):
                affected_row_ids.append(rid)

        row_snapshots = []
        for rid in affected_row_ids:
            row = rows_by_id.get(rid)
            if not row:
                continue
            row_snapshots.append(
                {
                    "row_id": row.row_id,
                    "rule_type": _enum_str(row.rule_type),
                    "source_entity": row.source_entity.model_dump() if row.source_entity else None,
                    "source_field_names": list(row.source_field_names or []),
                    "lookup_tables": [e.model_dump() for e in (row.lookup_tables or [])],
                    "join_condition": row.join_condition.model_dump() if row.join_condition else None,
                    "row_filter_text": row.row_filter_text,
                    "transformation_rules_text": row.transformation_rules_text,
                    "special_considerations_text": row.special_considerations_text,
                    "needs_review": row.needs_review,
                }
            )

        feedback_texts = [(feedback_by_row_id.get(rid) or "").strip() for rid in affected_row_ids if (feedback_by_row_id.get(rid) or "").strip()]

        related_answers = []
        for q in (step3_state.review_questions or []):
            if issue_id not in (q.issue_ids or []):
                continue
            if q.question_id not in answered_question_ids:
                continue
            # find the answer text
            ans_text = ""
            for a in (step3_state.bsa_answers or []):
                if a.question_id == q.question_id:
                    ans_text = a.answer_text or ""
                    break
            if ans_text.strip():
                related_answers.append({"question_id": q.question_id, "priority": q.priority.value, "answer_text": ans_text})

        item = IssueContextInput(
            issue_id=issue.issue_id,
            issue_type=issue.issue_type.value,
            severity=issue.severity.value,
            target_table_id=issue.target_column.entity_id,
            target_column_name=issue.target_column.column_name,
            issue_message=issue.message,
            affected_row_ids=affected_row_ids,
            row_snapshots=row_snapshots,
            feedback_texts=feedback_texts,
            answers=related_answers,
        )
        issues_by_table.setdefault(issue.target_column.entity_id, []).append(item)

    issue_plans: list[IssuePlan] = []
    issue_batch_size = max(1, int(getattr(config, "STEP4_ISSUE_BATCH_SIZE", 20)))
    for table_id in sorted(issues_by_table.keys()):
        items = list(issues_by_table.get(table_id) or [])
        if not items:
            continue

        # Chunk within a table to keep prompts small and quality stable.
        for i in range(0, len(items), issue_batch_size):
            chunk = items[i : i + issue_batch_size]
            out = await run_patch_and_resolve_agent(request=IssuePlanBatchRequest(items=chunk))
            issue_plans.extend(list(out or []))

    # Apply row_plans produced by issue resolver deterministically.
    issue_row_plans = []
    for ip in issue_plans:
        issue_row_plans.extend(list(ip.row_plans or []))
    issue_row_plans = normalize_interpretation_plans(list(issue_row_plans or []))
    issue_row_plans = filter_plans_by_locked_fields(plans=issue_row_plans, locked_fields_by_row_id=locked_fields_by_row_id)

    issue_change_log, issue_warnings, manual_actions_by_row_2 = apply_interpretation_plans(
        rows_by_id=rows_by_id,
        plans=issue_row_plans,
        raw_feedback_by_row=feedback_by_row_id,
        raw_answers_concat=raw_answers_concat,
    )
    for rid, acts in (manual_actions_by_row_2 or {}).items():
        manual_actions_by_row.setdefault(rid, []).extend(acts)

    from agents.mapping_apply_review.models import WarningItem  # avoid import cycles

    warnings: list[WarningItem] = list(row_warnings or []) + list(issue_warnings or [])

    # Warn about issue ids referenced by answers/decisions but missing from Step 2 open_issues.
    unknown_issue_ids = (issue_ids_from_answers | issue_ids_from_decisions) - set(issues_by_id.keys())
    for iid in sorted(unknown_issue_ids):
        warnings.append(
            WarningItem(
                warning_id=f"WARN_UNKNOWN_ISSUE_{iid}_{_step4_run_id()}",
                warning_type=WarningType.OTHER,
                severity="WARN",
                message=f"Issue id '{iid}' was referenced by Step 3 inputs but not found in Step 2 open_issues.",
                issue_id=iid,
            )
        )

    # ------------------------------------------------------------------
    # 3.5) Deterministic normalization + Subagent D (LLM): regenerate text fields
    # ------------------------------------------------------------------

    # Normalize rows after A/B (e.g., clear join/lookup artifacts when rule_type changes),
    # and clear stale text fields (unless locked by feedback) so they can be regenerated.
    baseline_row_by_row_id = {r.row_id: r for r in (step2_state.column_mappings or [])}
    for rid, row in rows_by_id.items():
        apply_rule_family_normalization(
            row=row,
            baseline_row=baseline_row_by_row_id.get(rid),
            locked_fields=locked_fields_by_row_id.get(rid, set()),
        )

    if bool(getattr(config, "STEP4_TEXT_REGEN_ENABLED", True)):
        regen_items: list[RowTextRegenInput] = []
        allowlists_by_row: dict[str, set[str]] = {}
        changes_by_row_id: dict[str, list[dict]] = {}
        touched_row_ids: set[str] = set(patch_by_row_id.keys()) | set(feedback_by_row_id.keys()) | set(answers_by_row.keys())
        # Provide recent change context to the regenerator (helps it describe the new intent after rule changes).
        for ch in list(row_change_log or []) + list(issue_change_log or []):
            touched_row_ids.add(ch.row_id)
            changes_by_row_id.setdefault(ch.row_id, []).append(
                {
                    "field_name": ch.field_name,
                    "before_value": ch.before_value,
                    "after_value": ch.after_value,
                    "source": ch.source.value,
                    "rationale": ch.rationale,
                }
            )
        for rid, row in rows_by_id.items():
            # Only regenerate rows touched by Step 3 inputs or Step 4 issue-resolution changes.
            if rid not in touched_row_ids:
                continue
            # Skip rows where all target text fields are locked by feedback.
            locks = locked_fields_by_row_id.get(rid, set())
            if {"transformation_rules_text", "row_filter_text", "special_considerations_text"} <= locks:
                continue

            allow = build_allowed_identifiers_for_row(row)
            allowlists_by_row[rid] = allow
            linked = answers_by_row.get(rid, [])
            regen_items.append(
                RowTextRegenInput(
                    row_id=row.row_id,
                    target_table_id=row.target_table.entity_id,
                    target_column_name=row.target_column_name,
                    rule_type=_enum_str(row.rule_type),
                    target_data_type=row.target_data_type,
                    target_attribute_business_description=row.target_attribute_business_description,
                    bsa_feedback_text=(feedback_by_row_id.get(rid) or "").strip() or None,
                    linked_answers=[a.model_dump() for a in (linked or [])],
                    recent_changes=list(changes_by_row_id.get(rid, []))[-10:],
                    allowlisted_identifiers=sorted(allow),
                    source_entity_id=(row.source_entity.entity_id if row.source_entity else None),
                    source_field_names=list(row.source_field_names or []),
                    lookup_table_ids=[e.entity_id for e in (row.lookup_tables or [])],
                    join_text=(row.join_condition.join_text if row.join_condition else None),
                    row_filter_text=row.row_filter_text,
                    transformation_rules_text=row.transformation_rules_text,
                    special_considerations_text=row.special_considerations_text,
                )
            )

        regen_plans = []
        if regen_items:
            regen_batch_size = max(1, int(getattr(config, "STEP4_TEXT_REGEN_BATCH_SIZE", 20)))
            by_table: dict[str, list[RowTextRegenInput]] = {}
            for item in regen_items:
                by_table.setdefault(item.target_table_id, []).append(item)

            for table_id in sorted(by_table.keys()):
                items = list(by_table.get(table_id) or [])
                if not items:
                    continue
                for i in range(0, len(items), regen_batch_size):
                    chunk = items[i : i + regen_batch_size]
                    out = await run_row_text_regenerator_agent(request=RowTextRegenBatchRequest(items=chunk))
                    # Robustness: if a chunk returns nothing, retry per-row (prevents total loss from one failure).
                    if not out and len(chunk) > 1:
                        for single in chunk:
                            out.extend(await run_row_text_regenerator_agent(request=RowTextRegenBatchRequest(items=[single])))
                    regen_plans.extend(list(out or []))
        regen_plans = normalize_interpretation_plans(list(regen_plans or []))
        regen_plans = filter_plans_by_locked_fields(plans=regen_plans, locked_fields_by_row_id=locked_fields_by_row_id)

        regen_change_log, regen_warnings, manual_actions_by_row_3 = apply_interpretation_plans(
            rows_by_id=rows_by_id,
            plans=regen_plans,
            raw_feedback_by_row=feedback_by_row_id,
            raw_answers_concat=raw_answers_concat,
            allowed_identifiers_by_row_id=allowlists_by_row,
        )
        for rid, acts in (manual_actions_by_row_3 or {}).items():
            manual_actions_by_row.setdefault(rid, []).extend(acts)
        warnings.extend(list(regen_warnings or []))
    else:
        regen_plans = []
        regen_change_log = []

    # ------------------------------------------------------------------
    # 4/5) Subagent C (deterministic): validation + issue ledger
    # ------------------------------------------------------------------

    warnings, manual_actions_by_row, issue_resolutions, _schema_valid_by_row = run_final_validator_exporter_agent(
        shared_state=shared_state,
        step2_state=step2_state,
        step3_state=step3_state,
        rows_by_id=rows_by_id,
        warnings=warnings,
        manual_actions_by_row=manual_actions_by_row,
        issue_plans=issue_plans,
    )

    # 6) Summaries + persist.
    summary = Step4Summary(
        total_rows_in=len(step2_state.column_mappings or []),
        total_rows_out=len(rows_by_id),
        issues_total=len(step2_state.open_issues or []),
        issues_resolved=sum(1 for i in issue_resolutions if i.status.value == "RESOLVED"),
        issues_partially_resolved=sum(1 for i in issue_resolutions if i.status.value == "PARTIALLY_RESOLVED"),
        issues_unresolved=sum(1 for i in issue_resolutions if i.status.value == "UNRESOLVED"),
        warnings_total=len(warnings),
        changes_applied_total=len(row_change_log) + len(issue_change_log) + len(regen_change_log),
    )

    step4_run_id = _step4_run_id()
    metadata = Step4Metadata(
        run_id=step2_state.metadata.run_id,
        interface_code=step2_state.metadata.interface_code,
        step4_run_id=step4_run_id,
        created_at=datetime.utcnow(),
        created_by="Step4MainAgent",
        input_artifacts=[
            ArtifactRef(kind=ArtifactKind.STEP1_SHARED_STATE, uri=shared_state_uri),
            ArtifactRef(kind=ArtifactKind.STEP2_STATE, uri=step2_state_uri),
            ArtifactRef(kind=ArtifactKind.STEP3_CAPTURE_STATE, uri=step3_state_uri),
            *( [ArtifactRef(kind=ArtifactKind.STEP3_REVIEW_PACKAGE, uri=step3_review_package_uri)] if step3_review_package_uri else [] ),
        ],
        step1_metadata_uri=shared_state_uri,
        step2_metadata=step2_state.metadata,
        step3_metadata=step3_state.metadata,
    )

    step4_state = Step4State(
        metadata=metadata,
        capture_status=step3_state.capture_status,
        column_mappings=list(rows_by_id.values()),
        table_common_filters=list(step2_state.table_common_filters or []),
        issue_resolutions=issue_resolutions,
        warnings=warnings,
        interpretation_plans=list(row_plans or []),
        issue_plans=list(issue_plans or []),
        change_log=list(row_change_log) + list(issue_change_log) + list(regen_change_log),
        summary=summary,
    )

    output_dir = output_dir or Path(config.RUNS_DIR)
    output_path = save_step4_state(step4_state, output_dir)

    # Best-effort ingestion of "validated Q/A experience" (BigQuery only).
    # We ingest only issues that Step 4 marked RESOLVED and that are not superseded by Step 3.5 table remaps.
    try:
        from utils.step4_qa_experience_ingestion_utils import ingest_step4_resolved_qa_to_bigquery

        ingest_step4_resolved_qa_to_bigquery(step2_state=step2_state, step3_state=step3_state, step4_state=step4_state)
    except Exception:
        logger.exception("Step 4 Q/A experience ingestion failed (best-effort)")

    return step4_state, output_path


__all__ = [
    "step4_main_agent",
    "run_step4_apply_review_pipeline",
    "save_step4_state",
]
