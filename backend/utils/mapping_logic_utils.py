"""
Step 2 Mapping Logic utilities.

Responsibilities:
    - Rule-type inference (forced priorities)
    - Mapping row construction with immediate issues

LLM/RAG:
    - This module is intentionally deterministic (no LLM calls here).
    - Any LLM/RAG usage belongs in the MappingLogicAgent sub-agent and must:
        - operate only on provided candidates (no hallucinated schema)
        - emit structured output validated by Step 2 models
        - treat evidence as helper-only (never ground truth)
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from agents.mapping_generation.models import (
    IssueSeverity,
    IssueType,
    MappingRow,
    OpenIssue,
    RuleType,
    RuleTypeSource,
    Step2WorkContext,
    map_step1_rule_type,
)


_BRANCH_KEYWORDS = (
    " if ",
    " when ",
    " otherwise",
    " depends on",
    " based on",
    " condition",
    " conditional",
    " versus",
    " vs ",
    " branch",
    " scenario",
    "individual vs",
    "vs organization",
    "either/or",
)

_PLACEHOLDER_TOKENS = {
    "tbd",
    "todo",
    "placeholder",
    "unknown",
    "n/a",
    "na",
    "none",
    "null",
    "to be confirmed",
    "to be defined",
}


def _norm_text(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _has_branch_semantic(text: Optional[str]) -> bool:
    s = _norm_text(text)
    if not s:
        return False
    padded = f" {s} "
    return any(tok in padded for tok in _BRANCH_KEYWORDS)


def _is_high_or_med_evidence(snippet: str) -> bool:
    s = _norm_text(snippet)
    return ("|high]" in s) or ("|med]" in s)


def _is_substantive_logic_text(value: Optional[str]) -> bool:
    s = _norm_text(value)
    if not s:
        return False
    if s in _PLACEHOLDER_TOKENS:
        return False
    if s.startswith("<") and s.endswith(">"):
        return False
    if any(tok in s for tok in ("<unknown", "<tbd", "<todo", "placeholder", "to be confirmed")):
        return False
    return True


def is_case_ifelse_eligible(
    *,
    target_logical_name: Optional[str],
    target_description: Optional[str],
    evidence_snippets: list[str] | None = None,
    instruction_hints: list[str] | None = None,
) -> bool:
    """
    Strict CASE/IF_ELSE eligibility gate.

    Allowed only when explicit branching cues exist from:
      1) target semantics (logical name/description), OR
      2) HIGH/MED evidence snippet explicitly suggesting branching, OR
      3) explicit instruction hints indicating conditional logic.
    """
    if _has_branch_semantic(target_logical_name) or _has_branch_semantic(target_description):
        return True

    for hint in instruction_hints or []:
        if _has_branch_semantic(hint):
            return True

    for snip in evidence_snippets or []:
        if not _is_high_or_med_evidence(snip):
            continue
        if _has_branch_semantic(snip):
            return True

    return False


def validate_multi_rule_concreteness(
    *,
    instances: list[dict],
    candidate_count: int,
    min_instances: int = 2,
) -> list[dict]:
    """
    Keep only concrete CASE/IF_ELSE instances.

    Concrete means:
      - valid non-empty rule_instance_id
      - selected_candidate_index is in range (or null)
      - row_filter_text or transformation_rules_text is substantive
      - duplicate/empty branches are removed
      - final count >= min_instances
    """
    out: list[dict] = []
    seen_rule_ids: set[str] = set()
    seen_logic: set[tuple[str, str, str]] = set()
    min_instances = max(2, int(min_instances))
    candidate_count = max(0, int(candidate_count))

    for inst in instances or []:
        rid = str(inst.get("rule_instance_id") or "").strip()
        if not rid or rid in seen_rule_ids:
            continue

        candidate_idx = inst.get("selected_candidate_index")
        if candidate_idx is not None:
            try:
                idx = int(candidate_idx)
            except Exception:
                continue
            if idx < 0 or idx >= candidate_count:
                continue
            candidate_idx = idx

        row_filter = (inst.get("row_filter_text") or "").strip()
        transform = (inst.get("transformation_rules_text") or "").strip()
        if not (_is_substantive_logic_text(row_filter) or _is_substantive_logic_text(transform)):
            continue

        logic_key = (
            _norm_text(row_filter),
            _norm_text(transform),
            "" if candidate_idx is None else str(candidate_idx),
        )
        if logic_key in seen_logic:
            continue

        seen_rule_ids.add(rid)
        seen_logic.add(logic_key)
        out.append(
            {
                "rule_instance_id": rid,
                "row_filter_text": row_filter or None,
                "selected_candidate_index": candidate_idx,
                "transformation_rules_text": transform or None,
                "rationale": str(inst.get("rationale") or "").strip(),
            }
        )

    if len(out) < min_instances:
        return []
    return out


def normalize_target_key(table_id: str, column_name: str) -> str:
    """Canonical key for maps/sets: TGT:<table>|COL:<column>."""
    return f"TGT:{table_id}|COL:{column_name}"


def map_rule_type(step1_rule) -> RuleType:
    """Backwards-compatible alias."""
    return map_step1_rule_type(step1_rule)


def _table_has_natural_key(target_table) -> bool:
    """
    Determine whether a target table has any reasonable natural key definition.

    We intentionally do NOT treat an AK group consisting only of surrogate keys as a natural key.
    """
    if not getattr(target_table, "alternate_keys", None):
        return False

    surrogate_cols = {c.attribute_name for c in getattr(target_table, "columns", []) if getattr(c, "is_surrogate_key", False)}
    for ak in target_table.alternate_keys or []:
        cols = [c for c in (ak.column_names or []) if c]
        if not cols:
            continue
        if surrogate_cols and set(cols).issubset(surrogate_cols):
            continue
        return True
    return False


def _is_strong_technical_column(target_table, target_column) -> bool:
    """
    Strong technical detection used by Step 2 rule typing.

    We treat a column as TECHNICAL if it is clearly ETL/audit/SCD scaffolding.
    We avoid forcing TECHNICAL for business attributes that merely contain "CUR" unless it looks like a flag/indicator
    and the table also has SCD scaffolding (EFF/EXP or explicit scd_hints).
    """
    name = (getattr(target_column, "attribute_name", "") or "").upper()
    if not name:
        return False

    # Hard signal: Step 1 already flagged it AND the table's SCD hints mark it system-generated.
    scd_hints = getattr(target_table, "scd_hints", None)
    system_cols = set(getattr(scd_hints, "system_generated_columns", []) or [])
    if name in system_cols:
        return True

    # Audit timestamps (very strong)
    if name.endswith(("_TS", "_DTTM", "_TIMESTAMP")):
        return True

    # Common audit/sequence technical names.
    if name in {
        "CREATED_DT",
        "CREATED_DATE",
        "CREATED_DTTM",
        "UPDATED_DT",
        "UPDATED_DATE",
        "UPDATED_DTTM",
        "LAST_UPDATED_DT",
        "LAST_UPDATED_DTTM",
        "LAST_UPDT_DT",
        "LAST_UPDT_DTTM",
        "ACTIVE_SEQ_NBR",
        "SEQ_NBR",
        "SEQUENCE_NBR",
        "SEQUENCE_NUMBER",
    }:
        return True

    # Batch/run identifiers and common ETL scaffold names
    if name in {"BATCH_ID", "RUN_ID", "JOB_ID"} or name.startswith("ETL_"):
        return True

    # SCD scaffolding cues
    if ("EFF" in name and ("DT" in name or "DTTM" in name)) or ("EXP" in name and ("DT" in name or "DTTM" in name)):
        return True

    # Current flag - only if it looks like a flag and the table has SCD scaffolding
    if ("CUR" in name or "CURRENT" in name) and ("FL" in name or "FLAG" in name or "IND" in name):
        if getattr(scd_hints, "eff_dt_column", None) or getattr(scd_hints, "exp_dt_column", None):
            return True
        if any(("EFF" in (c.attribute_name or "").upper() or "EXP" in (c.attribute_name or "").upper()) for c in getattr(target_table, "columns", [])):
            return True

    # Delete/active flags often come from ETL scaffolding in DART.
    if any(tok in name for tok in ("DEL_IND", "DELETE_FL", "ACTV_IND", "ACTIVE_FL", "OMIT_IND", "INACTV_IND", "ROW_DEL_FL")):
        return True

    # If Step 1 flagged it as technical, treat as technical unless it matches the CUR-business ambiguity case.
    if getattr(target_column, "is_technical", False):
        if ("CUR" in name or "CURRENT" in name) and not ("FL" in name or "FLAG" in name or "IND" in name):
            return False
        return True

    return False


def finalize_needs_review(row: MappingRow, confidence_threshold: float = 0.85) -> None:
    """
    Finalize `needs_review` after later AG1 stages have potentially updated confidence.

    Why this exists:
        - The deterministic seed stage starts with conservative confidence for INFERRED rows.
        - Later LLM stages (catalog candidates, tie-breakers, evidence self-check) can raise confidence.
        - Without a final pass, `needs_review` can remain "sticky" even for high-confidence DIRECT rows.

    Policy (v1):
        - Any OpenIssue => needs_review=True.
        - UNKNOWN => needs_review=True.
        - CASE / IF_ELSE => needs_review=True (branch completeness typically requires HITL).
        - OVERRIDE with no issues => needs_review=False.
        - Otherwise (INFERRED): needs_review = confidence_score < confidence_threshold.
    """
    if row.open_issue_ids:
        row.needs_review = True
        return
    if row.rule_type == RuleType.UNKNOWN:
        row.needs_review = True
        return
    if row.rule_type in {RuleType.CASE, RuleType.IF_ELSE}:
        row.needs_review = True
        return
    if row.rule_type_source == RuleTypeSource.OVERRIDE:
        row.needs_review = False
        return

    row.needs_review = bool(row.confidence_score < float(confidence_threshold))


def infer_rule_type_for_column(
    target_table,
    target_column,
    key: str,
    ctx: Step2WorkContext,
) -> Tuple[RuleType, RuleTypeSource, Optional[str], List[OpenIssue]]:
    """
    Apply hard deterministic precedence for a target column.
    Returns (rule_type, source, forced_reason, issues).
    Priority:
        1) override
        2) explicit lookup rule
        3) technical/system (forced policy)
        4) explicit default/hardcode
        5) inferred pending (UNKNOWN; AG1 LLM chooses final inferred rule)
    """
    issues: List[OpenIssue] = []

    target_table_id = getattr(target_table, "table_id", None) or "<unknown_table>"

    # 1) Explicit rule-type override (highest priority)
    if key in ctx.rule_type_overrides_map:
        reason = ctx.rule_type_override_reasons.get(key) or "Instruction override"
        forced = ctx.rule_type_overrides_map[key]

        # Important: even if SK is forced by instruction, we still need a natural key (AK/composite)
        # to define uniqueness. If missing, emit an issue for HITL.
        if forced == RuleType.SK:
            has_nk = _table_has_natural_key(target_table) or bool(ctx.composite_key_rules_by_entity.get(target_table_id))
            if not has_nk:
                issue = OpenIssue(
                    issue_id=f"ISSUE_AK_{target_table_id}_{target_column.attribute_name}",
                    issue_type=IssueType.MISSING_AK_DEFINITION,
                    severity=IssueSeverity.WARN,
                    target_column={
                        "entity_type": "TARGET_TABLE",
                        "entity_id": target_table_id,
                        "column_name": target_column.attribute_name,
                    },
                    message="SK is forced by instruction but no natural key (AK/composite key) definition found for uniqueness.",
                    suggested_question="Confirm natural key (AK) used for SK creation.",
                    evidence_refs=[],
                )
                issues.append(issue)

        return forced, RuleTypeSource.OVERRIDE, reason, issues

    # 1b) Explicit lookup rule
    if key in ctx.lookup_rules_map:
        return RuleType.LOOKUP, RuleTypeSource.OVERRIDE, "Explicit lookup rule", issues

    # 2) Technical/system
    if bool(getattr(ctx, "force_technical_rules", True)) and _is_strong_technical_column(target_table, target_column):
        return RuleType.TECHNICAL, RuleTypeSource.INFERRED, "Technical/system column", issues

    # 3) Explicit default/hardcode (instructions only)
    if key in ctx.default_rules_map:
        dr = ctx.default_rules_map[key] or {}
        condition_text = (dr.get("condition_text") or "").strip() if isinstance(dr, dict) else ""
        if condition_text:
            return RuleType.DEFAULT, RuleTypeSource.OVERRIDE, "Explicit default rule", issues
        return RuleType.HARDCODE, RuleTypeSource.OVERRIDE, "Explicit hardcode rule", issues

    # 5) Inferred pending path: AG1 LLM is the major chooser for inferred rows.
    return RuleType.UNKNOWN, RuleTypeSource.INFERRED, "INFERRED_PENDING_LLM", issues


def run_mapping_logic(ctx: Step2WorkContext) -> Tuple[List[MappingRow], List[OpenIssue]]:
    """
    Core mapping logic loop (placeholder heuristics).
    Produces MappingRows + accumulated OpenIssues.
    """
    rows: List[MappingRow] = []
    issues: List[OpenIssue] = []

    table_by_id = {t.table_id: t for t in ctx.shared_state.target_schema.tables}
    for table_id in ctx.selected_target_ids:
        table = table_by_id.get(table_id)
        if not table:
            continue
        for tgt_col in table.columns:
            key = normalize_target_key(table_id, tgt_col.attribute_name)
            if key in ctx.ignore_fields_keys:
                continue

            rule_type, rule_source, forced_reason, new_issues = infer_rule_type_for_column(
                table, tgt_col, key, ctx
            )
            issues.extend(new_issues)

            source_entity = None
            source_fields: List[str] = []
            row_issue_ids: List[str] = [iss.issue_id for iss in new_issues]

            # Candidate selection is performed by MappingLogicAgent (LLM) using an indexed source catalog.
            # This deterministic pass intentionally does NOT choose source columns to avoid missing
            # valid candidates when names differ greatly (e.g., PROVIDER_NAME vs PRVN).

            # If LOOKUP has no explicit lookup rule / join keys, seed a join-unknown issue for HITL.
            # (Even when LOOKUP is forced by override, we still need the join/table details.)
            if rule_type == RuleType.LOOKUP and key not in ctx.lookup_rules_map:
                issue = OpenIssue(
                    issue_id=f"ISSUE_JOIN_{table_id}_{tgt_col.attribute_name}",
                    issue_type=IssueType.JOIN_UNKNOWN,
                    severity=IssueSeverity.WARN,
                    target_column={
                        "entity_type": "TARGET_TABLE",
                        "entity_id": table_id,
                        "column_name": tgt_col.attribute_name,
                    },
                    message=(
                        "Lookup required but no explicit lookup rule / join keys provided."
                        if rule_source == RuleTypeSource.OVERRIDE
                        else "Lookup inferred but no explicit lookup rule / join keys provided."
                    ),
                    suggested_question="Which lookup table(s) and join keys should be used for this column?",
                    evidence_refs=[],
                )
                issues.append(issue)
                row_issue_ids.append(issue.issue_id)

            # Column-scoped filters from Step 1 live on the row (since common filters are mapping/table only).
            col_filters = ctx.global_filters_by_column.get(key, [])
            row_filter_text = None
            if col_filters:
                row_filter_text = " AND ".join([gf.expression_text for gf in col_filters if gf.expression_text])

            confidence = 0.95 if rule_source == RuleTypeSource.OVERRIDE else 0.6
            if rule_type == RuleType.TECHNICAL:
                confidence = max(confidence, 0.9)
            if rule_type == RuleType.UNKNOWN:
                confidence = 0.35

            needs_review = False
            if rule_type == RuleType.UNKNOWN or row_issue_ids:
                needs_review = True
            if rule_source != RuleTypeSource.OVERRIDE and confidence < 0.65:
                needs_review = True

            row = MappingRow(
                row_id=f"{table_id}.{tgt_col.attribute_name}",
                target_table={"entity_type": "TARGET_TABLE", "entity_id": table_id},
                target_column_name=tgt_col.attribute_name,
                rule_instance_id=None,
                rule_type=rule_type,
                rule_type_source=rule_source,
                forced_reason=forced_reason,
                source_entity=source_entity,
                source_field_names=source_fields,
                lookup_tables=[
                    lr.get("lookup_table")
                    for lr in ([ctx.lookup_rules_map.get(key)] if key in ctx.lookup_rules_map else [])
                    if isinstance(lr, dict) and lr.get("lookup_table")
                ],
                candidate_sources_topk=None,
                join_condition=None,
                row_filter_text=row_filter_text,
                transformation_rules_text=None,
                special_considerations_text=None,
                confidence_score=confidence,
                needs_review=needs_review,
                selected_lookup_hypothesis_id=None,
                reasoning_summary=forced_reason if forced_reason != "INFERRED_PENDING_LLM" else None,
                evidence_refs=[],
                open_issue_ids=row_issue_ids,
            )
            rows.append(row)

    return rows, issues
