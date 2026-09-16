"""
Step 2 (AG3) post-processing utilities.

Scope:
  - Deterministic helpers only (no LLM calls in this module).
  - AG3 (MappingPostProcessorAgent) uses these utilities to:
      1) Finalize transformation text per row (documentation-first).
      2) Validate + normalize rows against Step 1 schemas (no hallucinations).
      3) Generate question_candidates for Step 3 (HITL) from issues + low confidence.

Design constraints:
  - Do not invent entities/columns.
  - When information is missing (join keys, substring positions, CASE branches),
    do NOT guess: emit issues + questions for HITL.
"""

from __future__ import annotations

from typing import Iterable

from agents.mapping_generation.models import (
    IssueSeverity,
    IssueType,
    MappingRow,
    OpenIssue,
    QuestionCandidate,
    QuestionPriority,
    RuleType,
    Step2WorkContext,
    TableCommonFilter,
)
from agents.mapping_ingestion.models import ColumnRef

from utils.mapping_logic_utils import finalize_needs_review, normalize_target_key


def _issue_id(prefix: str, *, table_id: str, column_name: str) -> str:
    return f"{prefix}_{table_id}_{column_name}"


def _target_col_ref(*, table_id: str, column_name: str) -> ColumnRef:
    return ColumnRef(entity_type="TARGET_TABLE", entity_id=table_id, column_name=column_name)


def _ensure_issue(
    *,
    issues: list[OpenIssue],
    row: MappingRow,
    issue_id: str,
    issue_type: IssueType,
    severity: IssueSeverity,
    message: str,
    suggested_question: str | None,
    created_by: str,
) -> str:
    if issue_id not in {i.issue_id for i in issues}:
        issues.append(
            OpenIssue(
                issue_id=issue_id,
                issue_type=issue_type,
                severity=severity,
                target_column={
                    "entity_type": "TARGET_TABLE",
                    "entity_id": row.target_table.entity_id,
                    "column_name": row.target_column_name,
                },
                message=message,
                suggested_question=suggested_question,
                created_by=created_by,  # type: ignore[arg-type]
                evidence_refs=[],
            )
        )
    if issue_id not in row.open_issue_ids:
        row.open_issue_ids.append(issue_id)
    return issue_id


def _iter_target_table_ids(ctx: Step2WorkContext) -> set[str]:
    return {t.table_id for t in ctx.shared_state.target_schema.tables}


def _iter_target_columns(ctx: Step2WorkContext) -> dict[str, set[str]]:
    return {t.table_id: {c.attribute_name for c in t.columns} for t in ctx.shared_state.target_schema.tables}


def _iter_source_columns(ctx: Step2WorkContext) -> dict[str, set[str]]:
    return {f.file_id: {c.physical_name for c in f.columns} for f in ctx.shared_state.source_schema.files}


def _get_target_table(ctx: Step2WorkContext, table_id: str):
    for t in ctx.shared_state.target_schema.tables:
        if t.table_id == table_id:
            return t
    return None


def enrich_rows_with_target_metadata(*, ctx: Step2WorkContext, rows: list[MappingRow]) -> None:
    """
    Copy target-side metadata from Step 1 TargetSchema onto each Step 2 MappingRow.

    Why:
      - The Step 3 review UI needs these fields for display without having to join on TargetSchema.
      - This is metadata-only enrichment; it must not affect mapping logic.

    Fields populated (when available):
      - row.target_database (TargetTable.database; dataset id like DB_AEDWP1)
      - row.target_logical_attribute_name
      - row.target_attribute_business_description
      - row.target_data_type
      - row.target_default
      - row.target_nullability
      - row.target_key (P/F/A)
    """
    tables_by_id = {t.table_id: t for t in ctx.shared_state.target_schema.tables}

    # Pre-index columns by table_id + upper(attribute_name) for robust lookups.
    cols_by_table: dict[str, dict[str, object]] = {}
    for t in ctx.shared_state.target_schema.tables:
        cols_by_table[t.table_id] = {getattr(c, "attribute_name", "").upper(): c for c in getattr(t, "columns", []) or []}

    for row in rows:
        tgt_table_id = row.target_table.entity_id
        table = tables_by_id.get(tgt_table_id)
        if not table:
            continue

        row.target_database = getattr(table, "database", None)

        col = cols_by_table.get(tgt_table_id, {}).get((row.target_column_name or "").upper())
        if not col:
            continue

        row.target_logical_attribute_name = getattr(col, "logical_attribute_name", None)
        row.target_attribute_business_description = getattr(col, "attribute_description", None)
        row.target_data_type = getattr(col, "data_type", None)
        row.target_default = getattr(col, "default_value", None)
        row.target_nullability = getattr(col, "nullability", None)

        flags: list[str] = []
        if bool(getattr(col, "is_primary_key", False)):
            flags.append("P")
        if bool(getattr(col, "is_foreign_key", False)):
            flags.append("F")
        if getattr(col, "alternate_key_groups", None):
            flags.append("A")
        row.target_key = ",".join(flags) if flags else None


def _natural_key_hint_for_sk(ctx: Step2WorkContext, *, target_table_id: str) -> list[str]:
    """
    Best-effort extraction of natural key columns for SK description.

    Sources (in priority order):
      1) Target metadata: TargetTable.alternate_keys (Step 1 parsing).
      2) Instruction overrides: ctx.composite_key_rules_by_entity[target_table_id] (Step 1 instructions parsing).
    """
    table = _get_target_table(ctx, target_table_id)
    if table and getattr(table, "alternate_keys", None):
        for ak in table.alternate_keys or []:
            cols = [c for c in (ak.column_names or []) if c]
            if cols:
                return cols

    for rule in ctx.composite_key_rules_by_entity.get(target_table_id, []) or []:
        if isinstance(rule, dict) and rule.get("column_names"):
            cols = [c for c in (rule.get("column_names") or []) if c]
            if cols:
                return cols
    return []


def finalize_transformation_texts(
    *,
    ctx: Step2WorkContext,
    rows: list[MappingRow],
    issues: list[OpenIssue],
    table_common_filters: list[TableCommonFilter],
) -> None:
    """
    Fill `transformation_rules_text` (and optionally `special_considerations_text`) deterministically.

    Why deterministic:
      - AG3 must be safe without RAG/FYI.
      - Free-text generation is allowed, but we must not invent schema.
    """
    _ = table_common_filters  # common filters are applied implicitly; not repeated per-row in v1.

    for row in rows:
        if (row.transformation_rules_text or "").strip():
            continue

        tgt_table_id = row.target_table.entity_id
        tgt_col = row.target_column_name
        key = normalize_target_key(tgt_table_id, tgt_col)

        src = None
        if row.source_entity and row.source_entity.entity_type == "SOURCE_FILE":
            src = row.source_entity.entity_id

        src_fields = [c for c in (row.source_field_names or []) if c]

        if row.rule_type == RuleType.DIRECT:
            if src and src_fields:
                qualified = ", ".join([f"{src}.{c}" for c in src_fields])
                row.transformation_rules_text = f"Direct move from {qualified}."
            elif src and not src_fields:
                row.transformation_rules_text = f"Direct move from {src} (source column not selected)."
            else:
                row.transformation_rules_text = "Direct move (source entity/column not selected)."
                _ensure_issue(
                    issues=issues,
                    row=row,
                    issue_id=_issue_id("ISSUE_SRC", table_id=tgt_table_id, column_name=tgt_col),
                    issue_type=IssueType.MISSING_SOURCE_FIELD,
                    severity=IssueSeverity.WARN,
                    message="Direct rule requires a source field, but none is selected.",
                    suggested_question="Which source field populates this target column?",
                    created_by="MappingPostProcessorAgent",
                )

        elif row.rule_type == RuleType.LOOKUP:
            lt_ids = [lt.entity_id for lt in (row.lookup_tables or []) if lt and lt.entity_type == "TARGET_TABLE"]
            lt_text = ", ".join(lt_ids) if lt_ids else "<lookup_table_unknown>"
            if row.join_condition and not row.join_condition.is_unknown and (row.join_condition.join_text or "").strip():
                row.transformation_rules_text = f"Lookup {tgt_table_id}.{tgt_col} by joining {src or '<source_file>'} to {lt_text}. {row.join_condition.join_text}."
            else:
                row.transformation_rules_text = f"Lookup {tgt_table_id}.{tgt_col} from {lt_text} (join keys/path unknown)."
                _ensure_issue(
                    issues=issues,
                    row=row,
                    issue_id=_issue_id("ISSUE_JOIN", table_id=tgt_table_id, column_name=tgt_col),
                    issue_type=IssueType.JOIN_UNKNOWN,
                    severity=IssueSeverity.WARN,
                    message="Lookup required but join keys/path are unknown.",
                    suggested_question="Which lookup table(s) and join keys should be used for this column?",
                    created_by="MappingPostProcessorAgent",
                )

        elif row.rule_type == RuleType.SK:
            nk_cols = _natural_key_hint_for_sk(ctx, target_table_id=tgt_table_id)
            if nk_cols:
                nk_text = ", ".join(nk_cols)
                row.transformation_rules_text = (
                    f"SK creation for {tgt_table_id}.{tgt_col}: use natural key ({nk_text}) to check for an existing SK; "
                    "if none exists, generate a new SK (MAX+1)."
                )
            else:
                row.transformation_rules_text = (
                    f"SK creation for {tgt_table_id}.{tgt_col}: natural key (AK/composite key) not defined; "
                    "requires BSA confirmation."
                )
                _ensure_issue(
                    issues=issues,
                    row=row,
                    issue_id=_issue_id("ISSUE_AK", table_id=tgt_table_id, column_name=tgt_col),
                    issue_type=IssueType.MISSING_AK_DEFINITION,
                    severity=IssueSeverity.WARN,
                    message="SK rule requires a natural key (AK/composite key) definition for uniqueness, but none is available.",
                    suggested_question="Confirm the natural key (AK) used for SK creation.",
                    created_by="MappingPostProcessorAgent",
                )

        elif row.rule_type == RuleType.TECHNICAL:
            row.transformation_rules_text = "System generated by the ETL framework (no source mapping)."

        elif row.rule_type in {RuleType.DEFAULT, RuleType.HARDCODE}:
            dr = ctx.default_rules_map.get(key) if isinstance(ctx.default_rules_map, dict) else None
            default_value = None
            condition_text = None
            if isinstance(dr, dict):
                default_value = (dr.get("default_value") or "").strip()
                condition_text = (dr.get("condition_text") or "").strip()
            if row.rule_type == RuleType.HARDCODE:
                if default_value:
                    row.transformation_rules_text = f"Hardcode constant value '{default_value}'."
                else:
                    row.transformation_rules_text = "Hardcode constant value (missing explicit default_value)."
                    _ensure_issue(
                        issues=issues,
                        row=row,
                        issue_id=_issue_id("ISSUE_DEFAULT", table_id=tgt_table_id, column_name=tgt_col),
                        issue_type=IssueType.AMBIGUOUS_MAPPING,
                        severity=IssueSeverity.WARN,
                        message="HARDCODE rule selected but default_value is missing from instructions payload.",
                        suggested_question="What constant value should be hardcoded for this target column?",
                        created_by="MappingPostProcessorAgent",
                    )
            else:
                if default_value and condition_text:
                    row.transformation_rules_text = f"Default to '{default_value}' when {condition_text}."
                elif default_value:
                    row.transformation_rules_text = f"Default to '{default_value}' when source is missing/blank."
                else:
                    row.transformation_rules_text = "Default rule selected (missing explicit default_value)."
                    _ensure_issue(
                        issues=issues,
                        row=row,
                        issue_id=_issue_id("ISSUE_DEFAULT", table_id=tgt_table_id, column_name=tgt_col),
                        issue_type=IssueType.AMBIGUOUS_MAPPING,
                        severity=IssueSeverity.WARN,
                        message="DEFAULT rule selected but default_value is missing from instructions payload.",
                        suggested_question="What default value should be used, and under what condition?",
                        created_by="MappingPostProcessorAgent",
                    )

        elif row.rule_type == RuleType.SUBSTRING:
            if src and src_fields:
                row.transformation_rules_text = f"Derive {tgt_table_id}.{tgt_col} by extracting a substring from {src}.{src_fields[0]} (positions/pattern not specified)."
            else:
                row.transformation_rules_text = "Substring rule selected but source field is not selected."
            _ensure_issue(
                issues=issues,
                row=row,
                issue_id=_issue_id("ISSUE_SUBSTR", table_id=tgt_table_id, column_name=tgt_col),
                issue_type=IssueType.AMBIGUOUS_MAPPING,
                severity=IssueSeverity.WARN,
                message="SUBSTRING rule requires extraction positions/pattern, but this information is not available yet.",
                suggested_question="What substring extraction rule (start/length or pattern) should be applied?",
                created_by="MappingPostProcessorAgent",
            )

        elif row.rule_type in {RuleType.CASE, RuleType.IF_ELSE}:
            branch = (row.row_filter_text or "").strip()
            if src and src_fields:
                if branch:
                    row.transformation_rules_text = f"When ({branch}), set {tgt_table_id}.{tgt_col} from {src}.{src_fields[0]}."
                else:
                    row.transformation_rules_text = f"Conditional rule selected; set {tgt_table_id}.{tgt_col} from {src}.{src_fields[0]} (missing condition)."
            else:
                row.transformation_rules_text = "Conditional rule selected (missing condition and/or source field)."
            _ensure_issue(
                issues=issues,
                row=row,
                issue_id=_issue_id("ISSUE_COND", table_id=tgt_table_id, column_name=tgt_col),
                issue_type=IssueType.AMBIGUOUS_MAPPING,
                severity=IssueSeverity.WARN,
                message="Conditional rule requires explicit branch conditions; Step 1 instructions do not provide them yet.",
                suggested_question="Provide the rule conditions/branches and the correct source fields for each branch.",
                created_by="MappingPostProcessorAgent",
            )

        else:
            row.transformation_rules_text = "Mapping logic not finalized (UNKNOWN rule type)."
            _ensure_issue(
                issues=issues,
                row=row,
                issue_id=_issue_id("ISSUE_RULE", table_id=tgt_table_id, column_name=tgt_col),
                issue_type=IssueType.AMBIGUOUS_MAPPING,
                severity=IssueSeverity.WARN,
                message="Rule type is UNKNOWN; mapping logic cannot be finalized automatically.",
                suggested_question="Confirm the correct rule type and mapping logic for this target column.",
                created_by="MappingPostProcessorAgent",
            )


def post_validate_and_normalize_rows(*, ctx: Step2WorkContext, rows: list[MappingRow], issues: list[OpenIssue]) -> None:
    """
    Strict validation pass.

    This pass ensures the final Step2State is internally consistent and safe:
      - Every referenced entity/column exists in Step 1 schemas.
      - CASE/IF_ELSE rows have rule_instance_id.
      - Join unknown implies needs_review via OpenIssues.
    """
    target_table_ids = _iter_target_table_ids(ctx)
    target_cols_by_table = _iter_target_columns(ctx)
    source_cols_by_file = _iter_source_columns(ctx)

    confidence_threshold = float(getattr(ctx, "confidence_threshold", 0.85)) if hasattr(ctx, "confidence_threshold") else 0.85

    for row in rows:
        table_id = row.target_table.entity_id
        col = row.target_column_name

        # Target validation
        if table_id not in target_table_ids or col not in target_cols_by_table.get(table_id, set()):
            _ensure_issue(
                issues=issues,
                row=row,
                issue_id=_issue_id("ISSUE_SCHEMA", table_id=table_id, column_name=col),
                issue_type=IssueType.SCHEMA_MISMATCH,
                severity=IssueSeverity.ERROR,
                message="Row references a target table/column not present in Step 1 target_schema.",
                suggested_question="Fix target metadata or adjust mapping target reference.",
                created_by="MappingPostProcessorAgent",
            )

        # Source validation (only when present)
        if row.source_entity and row.source_entity.entity_type == "SOURCE_FILE":
            file_id = row.source_entity.entity_id
            allowed_cols = source_cols_by_file.get(file_id, set())
            bad = [c for c in (row.source_field_names or []) if c and c not in allowed_cols]
            if bad:
                _ensure_issue(
                    issues=issues,
                    row=row,
                    issue_id=_issue_id("ISSUE_SCHEMA_SRC", table_id=table_id, column_name=col),
                    issue_type=IssueType.SCHEMA_MISMATCH,
                    severity=IssueSeverity.ERROR,
                    message=f"Row references source column(s) not present in Step 1 source_schema: {bad}",
                    suggested_question="Confirm correct source column names (must exist in source metadata).",
                    created_by="MappingPostProcessorAgent",
                )

        # Multi-rule normalization: CASE/IF_ELSE must have rule_instance_id.
        if row.rule_type in {RuleType.CASE, RuleType.IF_ELSE} and not (row.rule_instance_id or "").strip():
            row.rule_instance_id = "RULE_1"
            _ensure_issue(
                issues=issues,
                row=row,
                issue_id=_issue_id("ISSUE_MULTI", table_id=table_id, column_name=col),
                issue_type=IssueType.AMBIGUOUS_MAPPING,
                severity=IssueSeverity.WARN,
                message="CASE/IF_ELSE row must have rule_instance_id; defaulted to RULE_1.",
                suggested_question="Confirm branch rules (RULE_1/RULE_2/...) and their conditions.",
                created_by="MappingPostProcessorAgent",
            )

        # Join unknown should have an issue id (AG1/AG2 usually provides it, but enforce here).
        if row.join_condition and row.join_condition.is_unknown:
            _ensure_issue(
                issues=issues,
                row=row,
                issue_id=_issue_id("ISSUE_JOIN", table_id=table_id, column_name=col),
                issue_type=IssueType.JOIN_UNKNOWN,
                severity=IssueSeverity.WARN,
                message="Join is marked unknown and requires HITL.",
                suggested_question="Provide join keys/relationship information.",
                created_by="MappingPostProcessorAgent",
            )

        finalize_needs_review(row, confidence_threshold=confidence_threshold)

    # Prune issues not referenced by any row.
    referenced = {iid for r in rows for iid in (r.open_issue_ids or [])}
    issues[:] = [i for i in issues if i.issue_id in referenced]


def _question_priority_for_issue(issue: OpenIssue) -> QuestionPriority:
    if issue.severity == IssueSeverity.ERROR:
        return QuestionPriority.P0
    if issue.issue_type in {IssueType.JOIN_UNKNOWN, IssueType.MISSING_SOURCE_FIELD, IssueType.MISSING_AK_DEFINITION}:
        return QuestionPriority.P0
    if issue.issue_type in {IssueType.SCHEMA_MISMATCH}:
        return QuestionPriority.P0
    if issue.severity == IssueSeverity.INFO:
        return QuestionPriority.P2
    return QuestionPriority.P1


def build_question_candidates(
    *,
    ctx: Step2WorkContext,
    rows: list[MappingRow],
    issues: list[OpenIssue],
    confidence_threshold: float | None,
) -> list[QuestionCandidate]:
    """
    Convert issues + low-confidence rows into Step 2 `question_candidates[]` for Step 3.
    """
    if confidence_threshold is None:
        confidence_threshold = float(getattr(ctx, "confidence_threshold", 0.85)) if hasattr(ctx, "confidence_threshold") else 0.85

    issue_by_id = {i.issue_id: i for i in issues}
    questions: list[QuestionCandidate] = []
    used_ids: set[str] = set()

    # 1) Promote OpenIssues into questions (primary driver for HITL).
    for row in rows:
        for iid in row.open_issue_ids or []:
            issue = issue_by_id.get(iid)
            if not issue:
                continue
            qid = f"Q_{iid}"
            if qid in used_ids:
                continue
            used_ids.add(qid)

            question_text = issue.suggested_question or issue.message
            questions.append(
                QuestionCandidate(
                    question_id=qid,
                    priority=_question_priority_for_issue(issue),
                    target_column=issue.target_column,
                    question_text=question_text,
                    context_summary=issue.message,
                    evidence_refs=issue.evidence_refs or [],
                )
            )

    # 2) Add low-confidence questions even without explicit issues.
    for row in rows:
        if row.open_issue_ids:
            continue
        if row.confidence_score >= float(confidence_threshold):
            continue

        table_id = row.target_table.entity_id
        col = row.target_column_name
        qid = f"Q_LOWCONF_{table_id}_{col}"
        if qid in used_ids:
            continue
        used_ids.add(qid)

        src = row.source_entity.entity_id if row.source_entity and row.source_entity.entity_type == "SOURCE_FILE" else None
        src_fields = ", ".join(row.source_field_names or []) if row.source_field_names else "<unknown>"
        options = []
        if row.candidate_sources_topk:
            options = [f"{c.source_entity.entity_id}.{c.source_column_name}" for c in row.candidate_sources_topk if c]

        question_text = (
            f"Confirm mapping for {table_id}.{col}: rule_type={row.rule_type.value}, "
            f"source={src or '<unknown>'}, source_fields={src_fields}."
        )
        if options:
            question_text += " Which source field is correct?"

        questions.append(
            QuestionCandidate(
                question_id=qid,
                priority=QuestionPriority.P1,
                target_column=_target_col_ref(table_id=table_id, column_name=col),
                question_text=question_text,
                context_summary=row.reasoning_summary,
                evidence_refs=row.evidence_refs or [],
            )
        )

    return questions
