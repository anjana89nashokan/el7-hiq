"""
Step 4 (Apply Review) - deterministic utilities.

Policy:
  - No LLM calls here.
  - Apply Step 3 decisions deterministically and validate against Step 1 schemas.
  - Never modify target identifiers.
  - Keep invalid BSA intent (patch/feedback) but warn and force needs_review=True.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from agents.mapping_generation.models import JoinCondition, MappingRow, OpenIssue, Step2State
from agents.mapping_ingestion.models import SharedState
from agents.mapping_review.models import DecisionType, Step3State
from agents.mapping_ingestion.models import EntityRef as Step1EntityRef
from agents.mapping_generation.models import RuleType as Step2RuleType
from agents.mapping_apply_review.models import (
    AppliedChange,
    ChangeSource,
    ConflictWinner,
    EvidenceSpan,
    InterpretationPlan,
    IssueResolution,
    ManualAction,
    Severity,
    WarningType,
    Step4IssueStatus,
    WarningItem,
)
from agents.mapping_review.models import MappingRowPatch, Step3Decision


_MUTABLE_ROW_FIELDS = (
    "rule_type",
    "source_entity",
    "source_field_names",
    "lookup_tables",
    "join_condition",
    "row_filter_text",
    "transformation_rules_text",
    "special_considerations_text",
    "needs_review",
    "confidence_score",
)


def _rule_type_name(value: object) -> str:
    return str(getattr(value, "value", value)).strip().upper()


def _coerce_step2_rule_type(value: object) -> object:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return value
    # Direct parse first.
    try:
        return Step2RuleType(raw)
    except Exception:
        pass
    # Case/format normalization (e.g., "direct", "if else", "if-else").
    token = raw.upper().replace("-", "_").replace(" ", "_")
    try:
        return Step2RuleType(token)
    except Exception:
        return value


def _slug_id(prefix: str, raw: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")[:120]
    return f"{prefix}_{safe or 'X'}"


def _build_source_index(shared_state: SharedState) -> tuple[set[str], dict[str, set[str]]]:
    """
    Build:
      - known_source_entities: set[file_id]
      - known_columns_by_entity: file_id -> set[column_name]
    """
    schema = shared_state.source_schema
    known_entities: set[str] = set()
    known_cols: dict[str, set[str]] = {}
    for f in (schema.files or []):
        known_entities.add(f.file_id)
        known_cols[f.file_id] = {c.physical_name for c in (f.columns or []) if getattr(c, "physical_name", None)}
    return known_entities, known_cols


def _is_schema_valid_source_ref(
    *,
    shared_state: SharedState,
    row: MappingRow,
) -> tuple[bool, list[str]]:
    """
    Validate source_entity + source_field_names against Step 1 source_schema.

    Returns: (is_valid, messages)
    """
    known_entities, known_cols = _build_source_index(shared_state)
    msgs: list[str] = []

    if row.source_entity is None:
        return True, msgs

    entity_id = row.source_entity.entity_id
    if entity_id not in known_entities:
        msgs.append(f"Unknown source_entity '{entity_id}' (not found in Step 1 source_schema.files).")
        return False, msgs

    cols = known_cols.get(entity_id, set())
    for c in (row.source_field_names or []):
        if c not in cols:
            msgs.append(f"Unknown source field '{entity_id}.{c}' (not found in Step 1 source_schema).")
    return (len(msgs) == 0), msgs


def apply_step3_patches(
    *,
    step2_state: Step2State,
    step3_state: Step3State,
) -> tuple[list[MappingRow], list[AppliedChange], dict[str, str]]:
    """
    Apply Step 3 PATCH_ROW decisions deterministically to Step 2 rows.

    Returns:
      - updated_rows
      - change_log (only for applied patches)
      - feedback_by_row_id (from patch.reasoning_summary)
    """
    by_id: dict[str, MappingRow] = {r.row_id: r for r in (step2_state.column_mappings or [])}
    changes: list[AppliedChange] = []
    feedback_by_row_id: dict[str, str] = {}

    for d in (step3_state.decisions or []):
        if d.decision_type != DecisionType.PATCH_ROW or not d.row_patch:
            continue
        patch = d.row_patch
        row = by_id.get(patch.row_id)
        if not row:
            continue

        if (patch.reasoning_summary or "").strip():
            feedback_by_row_id[patch.row_id] = patch.reasoning_summary.strip()

        # Apply only mutable fields that are present in the patch.
        for field in _MUTABLE_ROW_FIELDS:
            if not hasattr(patch, field):
                continue
            new_val = getattr(patch, field)
            if new_val is None:
                continue

            before = getattr(row, field, None)
            # JoinCondition may be passed as dict from UI; Step3 patch stores JoinCondition model already.
            if field == "join_condition" and isinstance(new_val, dict):
                new_val = JoinCondition.model_validate(new_val)

            if before == new_val:
                continue

            setattr(row, field, new_val)
            changes.append(
                AppliedChange(
                    change_id=_slug_id("CHG", f"{row.row_id}_{field}_{d.decision_id}"),
                    row_id=row.row_id,
                    field_name=field,
                    before_value=before,
                    after_value=new_val,
                    source=ChangeSource.BSA_PATCH,
                    rationale=patch.reasoning_summary,
                    decision_ids=[d.decision_id],
                )
            )

    return list(by_id.values()), changes, feedback_by_row_id


def extract_step3_patch_drafts(*, step3_state: Step3State) -> tuple[dict[str, MappingRowPatch], dict[str, str]]:
    """
    Extract raw Step 3 PATCH_ROW drafts (what the BSA edited) WITHOUT applying them.

    Returns:
      - patch_by_row_id: row_id -> MappingRowPatch
      - feedback_by_row_id: row_id -> reasoning_summary (if any)
    """
    patch_by_row_id: dict[str, MappingRowPatch] = {}
    feedback_by_row_id: dict[str, str] = {}
    for d in (step3_state.decisions or []):
        if d.decision_type != DecisionType.PATCH_ROW or not d.row_patch:
            continue
        patch_by_row_id[d.row_patch.row_id] = d.row_patch
        if (d.row_patch.reasoning_summary or "").strip():
            feedback_by_row_id[d.row_patch.row_id] = d.row_patch.reasoning_summary.strip()
    return patch_by_row_id, feedback_by_row_id


def _evidence_texts_present(*, feedback_text: str, answers_text: str, evidence: list[EvidenceSpan]) -> bool:
    def _norm(s: str) -> str:
        s = (s or "").strip().lower()
        s = s.replace("_", " ")
        s = re.sub(r"\s+", " ", s)
        return s

    norm_feedback = _norm(feedback_text or "")
    norm_answers = _norm(answers_text or "")
    for e in evidence or []:
        ev = _norm(e.evidence_text or "")
        if not ev:
            continue

        # Evidence spans are source-typed. Enforce that the evidence substring appears in
        # the corresponding raw text, not just "somewhere" in a concatenated blob.
        if e.source == "FEEDBACK":
            if ev not in norm_feedback:
                return False
        elif e.source == "ANSWER":
            if ev not in norm_answers:
                return False
        else:
            # Defensive: unknown evidence source should fail closed.
            return False
    return True


def _requires_text_evidence(*, field_name: str, source: ChangeSource) -> bool:
    """
    Explicit rules for evidence-span guard.

    Policy:
      - BSA_PATCH updates are structured and do not require evidence spans.
      - For BSA_FEEDBACK / BSA_ANSWER updates:
          * Identifier-bearing fields MUST include evidence spans and pass substring checks.
          * Pure narrative fields may omit evidence spans (to reduce brittleness).
    """
    if source == ChangeSource.BSA_PATCH:
        return False

    identifier_fields = {
        "source_entity",
        "source_field_names",
        "lookup_tables",
        "join_condition",
    }
    return field_name in identifier_fields


def normalize_interpretation_plans(plans: list[InterpretationPlan]) -> list[InterpretationPlan]:
    """
    Deterministic "policy engine" pass over LLM output.

    Why we need it:
      - The LLM decides conflict_winner, but we enforce a consistent application rule.
      - Prevents accidentally applying both PATCH and FEEDBACK updates for the same field.

    Behavior:
      - For each (row_id, field_name), keep a single winning update.
      - Winner selection depends on plan.conflict_winner.
    """
    normalized: list[InterpretationPlan] = []
    for plan in plans or []:
        by_field: dict[str, list] = {}
        for u in plan.updates or []:
            by_field.setdefault(u.field_name, []).append(u)

        kept = []
        for field_name, updates in by_field.items():
            if len(updates) == 1:
                kept.append(updates[0])
                continue

            # Deterministic precedence depending on conflict_winner.
            if plan.conflict_winner == ConflictWinner.FEEDBACK:
                pref = [ChangeSource.BSA_FEEDBACK, ChangeSource.BSA_ANSWER, ChangeSource.BSA_PATCH, ChangeSource.NORMALIZATION]
            elif plan.conflict_winner == ConflictWinner.PATCH:
                pref = [ChangeSource.BSA_PATCH, ChangeSource.BSA_FEEDBACK, ChangeSource.BSA_ANSWER, ChangeSource.NORMALIZATION]
            else:
                # Default: keep the last update in the list (LLM ordering).
                kept.append(updates[-1])
                continue

            picked = None
            for s in pref:
                for u in updates:
                    if u.source == s:
                        picked = u
                        break
                if picked:
                    break
            kept.append(picked or updates[-1])

        normalized.append(plan.model_copy(update={"updates": kept}))
    return normalized


def apply_interpretation_plans(
    *,
    rows_by_id: dict[str, MappingRow],
    plans: list[InterpretationPlan],
    raw_feedback_by_row: dict[str, str],
    raw_answers_concat: str,
    allowed_identifiers_by_row_id: dict[str, set[str]] | None = None,
) -> tuple[list[AppliedChange], list[WarningItem], dict[str, list[ManualAction]]]:
    """
    Apply LLM interpretation plans deterministically:
      - Only allow updates to known mutable fields
      - Reject hallucinated identifiers: each EvidenceSpan must be a substring of the raw text

    Returns:
      - applied_changes
      - warnings
      - manual_actions_by_row_id (unresolved/vague feedback)
    """
    changes: list[AppliedChange] = []
    warnings: list[WarningItem] = []
    manual_actions_by_row: dict[str, list[ManualAction]] = {}

    def _text_mentions_only_allowed_identifiers(row_id: str, text: str) -> bool:
        """
        Guard for text regeneration: prevent introducing new identifiers in regenerated text.

        We only enforce this when the caller provides an allowlist and the update source is NORMALIZATION.

        Detection heuristic:
          - looks only for dotted identifiers like TABLE.COL (case-insensitive)
          - ignores plain literals/keywords so normal conditional text is not rejected
          - allows anything already in the allowlist
        """
        allow = allowed_identifiers_by_row_id.get(row_id, set()) if allowed_identifiers_by_row_id else set()
        if not allow:
            return True

        # Extract likely identifiers.
        tokens = set()
        for m in re.findall(r"\b[A-Za-z0-9_]{2,}\.[A-Za-z0-9_]{2,}\b", text or ""):
            tokens.add(m)

        # Normalize both sides for case-insensitive matching.
        norm_allow = {a.strip().lower() for a in allow if a and a.strip()}
        for t in tokens:
            if t.strip().lower() in norm_allow:
                continue
            return False
        return True

    for plan in plans or []:
        row = rows_by_id.get(plan.row_id)
        if not row:
            continue

        feedback_text = raw_feedback_by_row.get(plan.row_id, "") or ""
        combined_text = f"{feedback_text}\n{raw_answers_concat or ''}"

        if plan.unresolved:
            row.needs_review = True
            manual_actions_by_row.setdefault(plan.row_id, []).append(
                ManualAction(
                    action_title="Provide exact source table + column name",
                    action_details="Feedback/answers did not include concrete identifiers. Please specify the exact source entity and column name to map from.",
                    suggested_location="Feedback / Source Field",
                )
            )
            warnings.append(
                WarningItem(
                    warning_id=_slug_id("WARN", f"{plan.row_id}_AMBIGUOUS"),
                    warning_type=WarningType.AMBIGUOUS_FEEDBACK,
                    severity=Severity.WARN,
                    message="Feedback/answers were too vague to apply deterministically; row left unchanged and marked needs_review.",
                    row_id=plan.row_id,
                )
            )
            for ev in plan.extracted_phrases or []:
                if ev.evidence_text and ev.evidence_text in combined_text:
                    continue
            continue

        for upd in plan.updates or []:
            if upd.field_name not in _MUTABLE_ROW_FIELDS:
                warnings.append(
                    WarningItem(
                        warning_id=_slug_id("WARN", f"{plan.row_id}_{upd.field_name}"),
                        warning_type=WarningType.OTHER,
                        severity=Severity.WARN,
                        message=f"Ignored update to unsupported field '{upd.field_name}'.",
                        row_id=plan.row_id,
                    )
                )
                continue

            # Evidence-span guard (explicit + consistent):
            #   - For identifier-bearing fields from feedback/answers: require evidence spans + substring match.
            #   - For BSA_PATCH: no evidence required (structured draft is the evidence).
            require_evidence = _requires_text_evidence(field_name=upd.field_name, source=upd.source)
            if require_evidence and not (upd.evidence and len(upd.evidence) > 0):
                warnings.append(
                    WarningItem(
                        warning_id=_slug_id("WARN", f"{plan.row_id}_MISSING_EVIDENCE_{upd.field_name}"),
                        warning_type=WarningType.HALLUCINATION_REJECTED,
                        severity=Severity.ERROR,
                        message=f"Rejected update to '{upd.field_name}' because no evidence spans were provided (hallucination guard).",
                        row_id=plan.row_id,
                    )
                )
                row.needs_review = True
                continue

            if require_evidence and not _evidence_texts_present(
                feedback_text=feedback_text,
                answers_text=raw_answers_concat or "",
                evidence=list(upd.evidence or []),
            ):
                warnings.append(
                    WarningItem(
                        warning_id=_slug_id("WARN", f"{plan.row_id}_HALLUCINATION"),
                        warning_type=WarningType.HALLUCINATION_REJECTED,
                        severity=Severity.ERROR,
                        message="Rejected an update because its evidence span was not found verbatim in feedback/answers (hallucination guard).",
                        row_id=plan.row_id,
                    )
                )
                row.needs_review = True
                continue

            before = getattr(row, upd.field_name, None)
            new_val = upd.new_value
            # Coerce common fields into Step 2 schema types (avoid mutating rows with raw dicts/strings).
            if upd.field_name == "rule_type":
                new_val = _coerce_step2_rule_type(new_val)
            if upd.field_name == "source_entity" and isinstance(new_val, dict):
                try:
                    new_val = Step1EntityRef.model_validate(new_val)
                except Exception:
                    pass
            if upd.field_name == "lookup_tables" and isinstance(new_val, list):
                coerced = []
                for item in new_val:
                    if isinstance(item, dict):
                        try:
                            coerced.append(Step1EntityRef.model_validate(item))
                        except Exception:
                            coerced.append(item)
                    else:
                        coerced.append(item)
                new_val = coerced
            if upd.field_name == "source_field_names" and isinstance(new_val, str):
                new_val = [new_val]
            if upd.field_name == "join_condition" and isinstance(new_val, str):
                new_val = JoinCondition(join_text=new_val)

            # Text regeneration guard (only when caller provides allowlist and update is NORMALIZATION).
            if (
                upd.source == ChangeSource.NORMALIZATION
                and allowed_identifiers_by_row_id is not None
                and upd.field_name in {"transformation_rules_text", "row_filter_text", "special_considerations_text"}
                and isinstance(new_val, str)
                and not _text_mentions_only_allowed_identifiers(plan.row_id, new_val)
            ):
                warnings.append(
                    WarningItem(
                        warning_id=_slug_id("WARN", f"{plan.row_id}_TEXT_IDENTS"),
                        warning_type=WarningType.HALLUCINATION_REJECTED,
                        severity=Severity.ERROR,
                        message="Rejected regenerated text because it mentions identifiers not present in the row allowlist.",
                        row_id=plan.row_id,
                    )
                )
                row.needs_review = True
                continue
            if before == new_val:
                continue

            setattr(row, upd.field_name, new_val)
            changes.append(
                AppliedChange(
                    change_id=_slug_id("CHG", f"{row.row_id}_{upd.field_name}_{plan.plan_id}"),
                    row_id=row.row_id,
                    field_name=upd.field_name,
                    before_value=before,
                    after_value=new_val,
                    source=upd.source,
                    rationale=upd.rationale,
                    decision_ids=[],
                    question_ids=[],
                )
            )

    return changes, warnings, manual_actions_by_row


def build_issue_resolutions(
    *,
    step2_open_issues: list[OpenIssue],
    rows_by_id: dict[str, MappingRow],
    manual_actions_by_row_id: dict[str, list[ManualAction]],
    warnings: list[WarningItem],
) -> list[IssueResolution]:
    """
    Very first-pass issue resolution logic (configurable later).

    Policy:
      - If row lacks required info, mark UNRESOLVED and attach manual actions.
      - If some info present but join/source still ambiguous, mark PARTIALLY_RESOLVED.
    """
    out: list[IssueResolution] = []
    warned_issue_ids = {w.issue_id for w in warnings if w.issue_id}

    for issue in step2_open_issues or []:
        affected_row_ids: list[str] = []
        if issue.target_column:
            for rid, row in rows_by_id.items():
                if (
                    row.target_table.entity_id == issue.target_column.entity_id
                    and row.target_column_name == issue.target_column.column_name
                ):
                    affected_row_ids.append(rid)

        status = Step4IssueStatus.UNRESOLVED
        reason = "Not enough information to resolve."
        manual_actions: list[ManualAction] = []

        # Heuristic per issue type (keep minimal; can evolve).
        for rid in affected_row_ids:
            row = rows_by_id.get(rid)
            if not row:
                continue
            if issue.issue_type.value == "MISSING_SOURCE_FIELD":
                if row.source_entity and (row.source_field_names or []):
                    status = Step4IssueStatus.PARTIALLY_RESOLVED
                    reason = "Source provided, but schema validation may still be required."
            if issue.issue_type.value == "JOIN_UNKNOWN":
                jt = (row.join_condition.join_text if row.join_condition else "") or ""
                if jt and "JOIN_UNKNOWN" not in jt:
                    status = Step4IssueStatus.PARTIALLY_RESOLVED
                    reason = "Join text provided; ensure keys and entities are correct."

            if row.needs_review:
                status = Step4IssueStatus.UNRESOLVED
                reason = "Row requires review."

            manual_actions.extend(manual_actions_by_row_id.get(rid, []))

        if issue.issue_id in warned_issue_ids and status == Step4IssueStatus.PARTIALLY_RESOLVED:
            status = Step4IssueStatus.UNRESOLVED
            reason = "Schema conflicts detected; manual review required."

        out.append(
            IssueResolution(
                issue_id=issue.issue_id,
                issue_type=issue.issue_type.value,
                status=status,
                affected_row_ids=affected_row_ids,
                reason_summary=reason,
                manual_actions=manual_actions,
            )
        )

    return out


def compute_issue_status(
    *,
    issue: OpenIssue,
    affected_rows: list[MappingRow],
    schema_valid_by_row_id: dict[str, bool],
    feedback_row_ids: set[str] | None = None,
) -> tuple[Step4IssueStatus, str]:
    """
    Deterministic status computation (post-apply + validation).

    Notes:
      - We intentionally do NOT rely on LLM confidence for final status.
      - This is a v1 policy engine and can be extended per issue type.
    """
    itype = issue.issue_type.value
    if not affected_rows:
        return Step4IssueStatus.UNRESOLVED, "No affected rows found for this issue."

    # If any affected row is schema-invalid, keep the issue unresolved.
    for r in affected_rows:
        if schema_valid_by_row_id.get(r.row_id) is False:
            return Step4IssueStatus.UNRESOLVED, "Schema validation failed for affected row(s)."

    if itype == "MISSING_SOURCE_FIELD":
        # If the mapping does not require a source field (technical/default/hardcode), treat as resolved (not applicable).
        for r in affected_rows:
            if _rule_type_name(r.rule_type) in {"TECHNICAL", "DEFAULT", "HARDCODE"}:
                return Step4IssueStatus.RESOLVED, "Not applicable: rule_type does not require a source field."
        for r in affected_rows:
            if r.source_entity and (r.source_field_names or []):
                return Step4IssueStatus.RESOLVED, "Source entity and field provided."
        return Step4IssueStatus.UNRESOLVED, "Source entity/field still missing."

    if itype == "JOIN_UNKNOWN":
        # If join is no longer applicable (non-LOOKUP mapping), treat as resolved (superseded).
        for r in affected_rows:
            if _rule_type_name(r.rule_type) != "LOOKUP":
                if feedback_row_ids and r.row_id in feedback_row_ids:
                    return Step4IssueStatus.RESOLVED, "Overwritten by feedback: rule_type is not LOOKUP; join not required."
                return Step4IssueStatus.RESOLVED, "Superseded: rule_type is not LOOKUP; join not required."
        for r in affected_rows:
            jc = r.join_condition
            if not jc or jc.is_unknown or not jc.is_required:
                continue
            if jc.join_keys:
                return Step4IssueStatus.RESOLVED, "Join keys provided."
            if (jc.join_text or "").strip():
                return Step4IssueStatus.PARTIALLY_RESOLVED, "Join text provided but join keys are not explicit."
        return Step4IssueStatus.UNRESOLVED, "Join keys/path still unknown."

    if itype == "MISSING_AK_DEFINITION":
        # If SK is no longer applicable, treat as resolved (superseded).
        for r in affected_rows:
            if _rule_type_name(r.rule_type) != "SK":
                if feedback_row_ids and r.row_id in feedback_row_ids:
                    return Step4IssueStatus.RESOLVED, "Overwritten by feedback: rule_type is not SK; AK definition not required."
                return Step4IssueStatus.RESOLVED, "Superseded: rule_type is not SK; AK definition not required."

        # Otherwise, we cannot deterministically confirm AK completeness; treat as partial when candidates exist.
        for r in affected_rows:
            if r.source_entity and (r.source_field_names or []):
                return Step4IssueStatus.PARTIALLY_RESOLVED, "Natural key candidates provided; confirm AK completeness."
        return Step4IssueStatus.UNRESOLVED, "AK definition still missing."

    if itype == "AMBIGUOUS_MAPPING":
        # If feedback was provided, treat as resolved: BSA explicitly overwrote ambiguity with intent.
        if feedback_row_ids and any(r.row_id in feedback_row_ids for r in affected_rows):
            return Step4IssueStatus.RESOLVED, "Overwritten by feedback: BSA provided explicit intent for this mapping."

        if any(r.needs_review for r in affected_rows):
            return Step4IssueStatus.UNRESOLVED, "Row still marked needs_review."
        return Step4IssueStatus.RESOLVED, "Row no longer needs review."

    if itype == "CONFLICTING_EVIDENCE":
        # Evidence conflicts are informational; BSA feedback resolves the conflict by choosing a final intent.
        if feedback_row_ids and any(r.row_id in feedback_row_ids for r in affected_rows):
            return Step4IssueStatus.RESOLVED, "Overwritten by feedback: BSA decision supersedes conflicting evidence."
        if any(r.needs_review for r in affected_rows):
            return Step4IssueStatus.UNRESOLVED, "Conflicting evidence remains; manual review required."
        return Step4IssueStatus.PARTIALLY_RESOLVED, "Conflicting evidence noted; mapping updated."

    if itype == "SCHEMA_MISMATCH":
        # If row is schema-valid after apply, treat mismatch as resolved (overridden/validated).
        if all(schema_valid_by_row_id.get(r.row_id) is not False for r in affected_rows):
            if feedback_row_ids and any(r.row_id in feedback_row_ids for r in affected_rows):
                return Step4IssueStatus.RESOLVED, "Overwritten by feedback: mapping updated and schema-valid."
            return Step4IssueStatus.RESOLVED, "Mapping updated and schema-valid."
        return Step4IssueStatus.UNRESOLVED, "Schema mismatch remains after apply."

    if itype == "MISSING_TARGET_METADATA":
        # Step 4 cannot invent target metadata; keep unresolved so it is visible for manual completion.
        return Step4IssueStatus.UNRESOLVED, "Target metadata missing; cannot be resolved by Step 4."

    # Default conservative behavior.
    if any(r.needs_review for r in affected_rows):
        return Step4IssueStatus.UNRESOLVED, "Row requires review."
    return Step4IssueStatus.PARTIALLY_RESOLVED, "Updated mapping, but no deterministic completion criteria for this issue type yet."
