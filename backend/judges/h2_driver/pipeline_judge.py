from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from judges.base_judge import BaseJudge
from judges.h2_driver.prompts import (
    DART_FIELD_RULES,
    PIPELINE_OVERALL_SUMMARY_PROMPT,
    STEP1_MAPPING_JUDGE_PROMPT,
    STEP2_LOGIC_JUDGE_PROMPT,
    STEP3_VALIDATION_JUDGE_PROMPT,
)
from judges.h2_driver.schemas import (
    BrdContext,
    DriverPipelineJudgeInput,
    DriverPipelineJudgeOutput,
    StepJudgment,
)

try:
    import structlog
    logger = structlog.get_logger()
except Exception:
    import logging
    logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transformation patterns — mirrors tools.py _TRANSFORMATION_PATTERNS
# ---------------------------------------------------------------------------
_TRANSFORMATION_PATTERNS = [
    "CASE WHEN", "COALESCE", "ISNULL", "CONVERT(", "CAST(",
    "SUBSTR(", "LEFT(", "RIGHT(", "UPPER(", "LOWER(", "TRIM(",
    "DECODE(", "NVL(", "IIF(", "FORMAT(", "HAVING",
]

# Known critical field confusions (from agent instructions)
# Maps filter_category → correct DART field. Used to detect wrong field assignments.
_CORRECT_DART_FIELDS: dict[str, str] = {
    "company":              "IBC_FOC_LVL_CD",
    "business_type":        "CO_CD_ROLLUP_ID",
    "exclusion":            "CO_CD_ROLLUP_ID",
    "lob":                  "MED_LOB_ROLLUP",
    "coverage":             "CVG_CTG_CD",
    "financial_arrangement": "GRP_FARG_CD",
    "product":              "PROD_CD",
    "extended_product":     "PROD_OPT_CD",
    "state":                "CO_ST_CD",
    "group_id":             "GRP_OPR_BUS_UNIT_CD",
    "customer_id":          "CLIENT_ID",
    "sensitivity":          "PROT_CTG_CD",
}

# Fields that should NEVER be swapped (most critical confusions)
_CRITICAL_FIELD_SWAPS: list[tuple[str, str, str]] = [
    # (category, wrong_field, correct_field)
    ("lob",      "CVG_CTG_CD",   "MED_LOB_ROLLUP"),
    ("coverage", "MED_LOB_ROLLUP", "CVG_CTG_CD"),
]

# Date literal patterns that must NOT appear (must use :run_date)
_HARDCODED_DATE_RE = re.compile(
    r"'?\d{4}-\d{2}-\d{2}'?|'\d{2}/\d{2}/\d{4}'|TO_DATE\s*\(",
    re.IGNORECASE,
)

# filter_id format must be Fxxx (3-digit zero-padded)
_FILTER_ID_RE = re.compile(r"^F\d{3}$")


def _safe_str(val: Any) -> str:
    return str(val).strip() if val is not None else ""


def _has_transformation(text: str) -> list[str]:
    upper = text.upper()
    return [p for p in _TRANSFORMATION_PATTERNS if p.upper() in upper]


def _verdict_score(verdict: str) -> float:
    return {"PASS": 1.0, "WARN": 0.7, "BLOCK": 0.2}.get(verdict.upper(), 0.5)


# ---------------------------------------------------------------------------
# Step 1 deterministic checks
# ---------------------------------------------------------------------------

def _check_step1(driver_mapping: dict, brd_context: BrdContext) -> dict[str, Any]:
    filter_candidates: list[dict] = driver_mapping.get("filter_candidates") or []
    unmapped_concepts: list[str] = driver_mapping.get("unmapped_concepts") or []
    ibc_aha_context: str = _safe_str(driver_mapping.get("ibc_aha_context"))

    findings: list[str] = []

    # BRD coverage: every active filter key must appear in candidates or unmapped
    covered_categories = {(c.get("filter_category") or "").lower() for c in filter_candidates}
    covered_concepts = " ".join(
        (c.get("brd_concept") or "") + " " + (c.get("brd_source") or "")
        for c in filter_candidates
    ).lower()
    unmapped_blob = " ".join(str(u) for u in unmapped_concepts).lower()

    brd_key_map = {
        "company": ["company"],
        "line_of_business": ["lob"],
        "coverage_plan": ["coverage"],
        "financial_arrangement": ["financial_arrangement"],
        "excluded_companies": ["exclusion", "business_type"],
        "excluded_lob": ["lob"],
        "customer_id": ["customer_id"],
        "opt_out_groups": ["customer_id"],
        "state": ["state"],
        "sensitive_data_exclusion": ["sensitivity"],
    }

    uncovered_brd_keys: list[str] = []
    for brd_key in brd_context.active_filter_keys:
        expected_cats = brd_key_map.get(brd_key, [brd_key.replace("_", " ")])
        found = (
            any(cat in covered_categories for cat in expected_cats)
            or brd_key.lower() in covered_concepts
            or brd_key.lower() in unmapped_blob
        )
        if not found:
            uncovered_brd_keys.append(brd_key)
            findings.append(
                f"BRD key '{brd_key}' has no FilterCandidate and is not in unmapped_concepts — silently dropped."
            )

    # date_parameters sub-key coverage
    date_params = brd_context.filters_and_parameters.get("date_parameters") or {}
    date_covered = any(
        c.get("filter_category") == "date_range" or c.get("filter_category") == "enrollment"
        for c in filter_candidates
    )
    if date_params and not date_covered:
        findings.append(
            f"BRD has date_parameters {list(date_params.keys())} but no date_range FilterCandidate was produced."
        )

    # Critical field swap checks
    swap_errors: list[str] = []
    for c in filter_candidates:
        cat = (c.get("filter_category") or "").lower()
        dart = (c.get("dart_field") or "").upper()
        for swap_cat, wrong_field, correct_field in _CRITICAL_FIELD_SWAPS:
            if cat == swap_cat and dart == wrong_field.upper():
                swap_errors.append(
                    f"CRITICAL: category='{cat}' mapped to '{dart}' — must be '{correct_field}'. "
                    "LOB↔Coverage field swap detected."
                )

    # Hardcoded dates in sql_clause
    date_literal_errors: list[str] = []
    for c in filter_candidates:
        clause = _safe_str(c.get("sql_clause"))
        if clause and _HARDCODED_DATE_RE.search(clause):
            date_literal_errors.append(
                f"FilterCandidate '{c.get('filter_category')}' has hardcoded date literal in sql_clause: '{clause[:80]}'"
            )

    # open_item without bsa_question
    missing_bsa_q: list[str] = []
    for c in filter_candidates:
        if c.get("open_item") and not (c.get("bsa_question") or "").strip():
            missing_bsa_q.append(c.get("filter_category") or "?")

    # ibc_aha_context validity
    aha_in_scope = any(
        kw in (brd_context.in_scope or "").lower()
        for kw in ["aha", "tpa", "ahanj", "ahapa", "ahaw", "ia ", "iabl"]
    )
    ibc_in_scope = any(
        kw in (brd_context.in_scope or "").lower()
        for kw in ["ibc", "independence"]
    )
    expected_context = "both" if (aha_in_scope and ibc_in_scope) else ("AHA" if aha_in_scope else "IBC")
    context_mismatch = ibc_aha_context.lower() not in (
        expected_context.lower(),
        "ibc" if expected_context == "IBC" else "",
        "aha" if expected_context == "AHA" else "",
        "both",
    )

    findings.extend(swap_errors)
    findings.extend(date_literal_errors)
    if missing_bsa_q:
        findings.append(f"open_item=True without bsa_question for categories: {missing_bsa_q}")
    if context_mismatch:
        findings.append(
            f"ibc_aha_context='{ibc_aha_context}' but in_scope suggests '{expected_context}'."
        )

    active_keys_total = len(brd_context.active_filter_keys)
    brd_coverage_rate = (
        round(1.0 - (len(uncovered_brd_keys) / active_keys_total), 4)
        if active_keys_total > 0
        else 1.0
    )

    return {
        "filter_candidate_count": len(filter_candidates),
        "unmapped_concept_count": len(unmapped_concepts),
        "ibc_aha_context": ibc_aha_context,
        "expected_ibc_aha_context": expected_context,
        "uncovered_brd_keys": uncovered_brd_keys,
        "critical_field_swaps": swap_errors,
        "hardcoded_date_errors": date_literal_errors,
        "missing_bsa_question_categories": missing_bsa_q,
        "context_mismatch": context_mismatch,
        # Derived KPIs
        "brd_coverage_rate": brd_coverage_rate,
        "active_brd_filter_count": active_keys_total,
        "uncovered_brd_key_count": len(uncovered_brd_keys),
        "critical_field_swap_count": len(swap_errors),
        "hardcoded_date_error_count": len(date_literal_errors),
        "open_items_missing_question_count": len(missing_bsa_q),
        "ibc_aha_context_match": not context_mismatch,
        "findings": findings,
        "is_blocking": bool(swap_errors or date_literal_errors or uncovered_brd_keys),
    }


# ---------------------------------------------------------------------------
# Step 2 deterministic checks
# ---------------------------------------------------------------------------

def _check_step2(driver_logic: dict, driver_mapping: dict) -> dict[str, Any]:
    common_filters: list[dict] = driver_logic.get("common_filters") or []
    sql_where: str = _safe_str(driver_logic.get("sql_where_clause"))
    open_item_count: int = int(driver_logic.get("open_item_count") or 0)
    global_filter_count: int = int(driver_logic.get("global_filter_count") or len(common_filters))

    findings: list[str] = []

    # Transformation check — combined WHERE clause
    where_transforms = _has_transformation(sql_where)
    if where_transforms:
        findings.append(
            f"sql_where_clause contains transformation patterns: {where_transforms}. "
            "Driver predicates must be pure filter logic only."
        )

    # Transformation check — individual sql_clauses
    filter_transforms: list[str] = []
    for f in common_filters:
        clause = _safe_str(f.get("sql_clause"))
        hits = _has_transformation(clause)
        if hits:
            filter_transforms.append(
                f"Filter {f.get('filter_id', '?')} ({f.get('dart_field', '?')}): {hits}"
            )
            findings.append(f"Transformation in filter {f.get('filter_id')}: {hits} in clause: {clause[:80]}")

    # Hardcoded dates
    date_errors: list[str] = []
    for f in common_filters:
        clause = _safe_str(f.get("sql_clause"))
        if f.get("filter_category") in ("date_range", "enrollment") and _HARDCODED_DATE_RE.search(clause):
            date_errors.append(f"Filter {f.get('filter_id')}: hardcoded date literal in sql_clause")
            findings.append(f"Filter {f.get('filter_id')} has hardcoded date literal: '{clause[:80]}'")

    # filter_id format: must be F001, F002...
    bad_ids: list[str] = []
    for f in common_filters:
        fid = _safe_str(f.get("filter_id"))
        if fid and not _FILTER_ID_RE.match(fid):
            bad_ids.append(fid)
    if bad_ids:
        findings.append(f"filter_id not zero-padded to 3 digits: {bad_ids[:5]}")

    # IN/NOT IN direction: include→IN, exclude→NOT IN
    direction_errors: list[str] = []
    for f in common_filters:
        ftype = (f.get("filter_type") or "").lower()
        clause = _safe_str(f.get("sql_clause")).upper()
        if ftype == "include" and "NOT IN" in clause and "-- OPEN ITEM" not in clause:
            direction_errors.append(
                f"Filter {f.get('filter_id')} ({f.get('dart_field')}): include filter uses NOT IN."
            )
        elif ftype == "exclude" and "NOT IN" not in clause and "IN (" in clause and "-- OPEN ITEM" not in clause:
            direction_errors.append(
                f"Filter {f.get('filter_id')} ({f.get('dart_field')}): exclude filter uses IN instead of NOT IN."
            )
    findings.extend(direction_errors)

    # brd_traceability must be a list
    bad_trace: list[str] = []
    for f in common_filters:
        trace = f.get("brd_traceability")
        if trace is not None and not isinstance(trace, list):
            bad_trace.append(f.get("filter_id", "?"))
    if bad_trace:
        findings.append(f"brd_traceability is not a list for filters: {bad_trace[:5]}")

    # open_item=True must carry bsa_question
    missing_bsa_q: list[str] = []
    for f in common_filters:
        if f.get("open_item") and not (f.get("bsa_question") or "").strip():
            missing_bsa_q.append(f.get("filter_id", "?"))
    if missing_bsa_q:
        findings.append(f"open_item=True without bsa_question on filters: {missing_bsa_q[:5]}")

    # Candidate coverage: every FilterCandidate should have a CommonFilter
    candidate_count = len(driver_mapping.get("filter_candidates") or [])
    filter_count = len(common_filters)
    dropped_count = max(0, candidate_count - filter_count)
    if dropped_count > 0:
        findings.append(
            f"{dropped_count} FilterCandidate(s) appear to have no corresponding CommonFilter "
            f"({candidate_count} candidates → {filter_count} filters)."
        )

    # Open item ratio
    open_ratio = open_item_count / max(global_filter_count, 1)

    all_transforms = bool(where_transforms or filter_transforms)
    filter_generation_rate = (
        round(filter_count / candidate_count, 4) if candidate_count > 0 else 1.0
    )

    return {
        "global_filter_count": global_filter_count,
        "open_item_count": open_item_count,
        "open_item_ratio": round(open_ratio, 4),
        "transformation_violations": filter_transforms[:10],
        "where_clause_transforms": where_transforms,
        "hardcoded_date_errors": date_errors,
        "bad_filter_ids": bad_ids[:10],
        "direction_errors": direction_errors[:10],
        "bad_traceability": bad_trace[:10],
        "missing_bsa_question": missing_bsa_q[:10],
        "dropped_candidates": dropped_count,
        # Derived KPIs
        "candidate_count": candidate_count,
        "filter_count": filter_count,
        "filter_generation_rate": filter_generation_rate,
        "transformation_violation_count": len(filter_transforms) + (1 if where_transforms else 0),
        "direction_error_count": len(direction_errors),
        "hardcoded_date_count": len(date_errors),
        "dropped_candidate_count": dropped_count,
        "bad_filter_id_count": len(bad_ids),
        "missing_traceability_count": len(bad_trace),
        "missing_bsa_question_count": len(missing_bsa_q),
        "findings": findings,
        "is_blocking": bool(all_transforms or date_errors or direction_errors or dropped_count > 0),
    }


# ---------------------------------------------------------------------------
# Step 3 deterministic checks
# ---------------------------------------------------------------------------

def _check_step3(driver_validation: dict, driver_logic: dict) -> dict[str, Any]:
    can_proceed = driver_validation.get("can_proceed")
    total_high = int(driver_validation.get("total_high") or 0)
    total_medium = int(driver_validation.get("total_medium") or 0)
    issues: list[dict] = driver_validation.get("issues") or []
    standards_compliant = driver_validation.get("standards_compliant")
    no_transformation_logic = driver_validation.get("no_transformation_logic")
    all_brd_traced = driver_validation.get("all_brd_requirements_traced")

    findings: list[str] = []

    # Rule: can_proceed must equal (total_high == 0)
    expected_can_proceed = total_high == 0
    proceed_inconsistent = can_proceed != expected_can_proceed
    if proceed_inconsistent:
        findings.append(
            f"can_proceed={can_proceed} but total_high={total_high}. "
            f"Expected can_proceed={expected_can_proceed} (rule: can_proceed = total_high == 0)."
        )

    # Cross-check: scan driver_logic SQL for transformations the validator should have caught
    common_filters: list[dict] = driver_logic.get("common_filters") or []
    sql_where = _safe_str(driver_logic.get("sql_where_clause"))
    actual_transforms = _has_transformation(sql_where)
    for f in common_filters:
        actual_transforms.extend(_has_transformation(_safe_str(f.get("sql_clause"))))
    actual_transforms = list(set(actual_transforms))

    reported_transform_issues = [
        i for i in issues if i.get("issue_type") == "transformation_logic"
    ]
    missed_transforms: list[str] = []
    if actual_transforms and not reported_transform_issues:
        missed_transforms = actual_transforms
        findings.append(
            f"Transformation patterns found in driver_logic ({actual_transforms}) "
            "but validator reported no transformation_logic issues."
        )

    # Cross-check: detect field conflicts the validator should have caught
    field_types: dict[str, str] = {}
    actual_conflicts: list[str] = []
    for f in common_filters:
        dart_field = f.get("dart_field", "")
        ftype = (f.get("filter_type") or "").lower()
        if not dart_field or ftype == "date_range":
            continue
        if dart_field in field_types and field_types[dart_field] != ftype:
            actual_conflicts.append(dart_field)
        else:
            field_types[dart_field] = ftype

    reported_conflict_issues = [i for i in issues if i.get("issue_type") == "conflict"]
    missed_conflicts: list[str] = []
    if actual_conflicts and not reported_conflict_issues:
        missed_conflicts = actual_conflicts
        findings.append(
            f"Field conflict detected in driver_logic for fields {actual_conflicts} "
            "but validator reported no conflict issues."
        )

    # Cross-check: missing brd_traceability
    filters_missing_trace = [
        f.get("filter_id", "?") for f in common_filters
        if not [t for t in (f.get("brd_traceability") or []) if t and t.strip()]
    ]
    reported_trace_issues = [i for i in issues if i.get("issue_type") == "missing_brd_trace"]
    missed_trace: list[str] = []
    if filters_missing_trace and not reported_trace_issues:
        missed_trace = filters_missing_trace
        findings.append(
            f"Filters {filters_missing_trace[:5]} have no brd_traceability entries "
            "but validator reported no missing_brd_trace issues."
        )

    # standards_compliant should always be True (field validation at mapping time)
    if standards_compliant is False:
        findings.append(
            "standards_compliant=False — this flag should be True; field name validation "
            "is delegated to search_standards_tool at mapping time."
        )

    # no_transformation_logic cross-check
    if actual_transforms and no_transformation_logic is True:
        findings.append(
            f"no_transformation_logic=True but transformation patterns detected: {actual_transforms}"
        )
    elif not actual_transforms and no_transformation_logic is False:
        findings.append(
            "no_transformation_logic=False but no transformation patterns were detected in driver_logic."
        )

    return {
        "can_proceed": can_proceed,
        "total_high": total_high,
        "total_medium": total_medium,
        "issue_count": len(issues),
        "proceed_inconsistent": proceed_inconsistent,
        "missed_transforms": missed_transforms,
        "missed_conflicts": missed_conflicts,
        "missed_trace_filters": missed_trace[:5],
        # Derived KPIs
        "missed_transform_count": len(missed_transforms),
        "missed_conflict_count": len(missed_conflicts),
        "missed_trace_count": len(missed_trace),
        "validator_accurate": not (proceed_inconsistent or missed_transforms or missed_conflicts),
        "findings": findings,
        "is_blocking": bool(proceed_inconsistent or missed_transforms or missed_conflicts),
    }


# ---------------------------------------------------------------------------
# DriverPipelineJudge
# ---------------------------------------------------------------------------

class DriverPipelineJudge(BaseJudge):
    """
    Unified judge for the 3-step driver generation pipeline.
    Combines domain-specific deterministic checks with LLM assessment.
    """

    async def evaluate(self, judge_input: DriverPipelineJudgeInput) -> DriverPipelineJudgeOutput:
        started_at = time.perf_counter()
        session_id = judge_input.session_id

        try:
            logger.info("driver_pipeline_judge_start", session_id=session_id)
        except Exception:
            pass

        step_judgments: list[StepJudgment] = []

        s1 = await self._judge_step1(judge_input)
        step_judgments.append(s1)

        s2 = await self._judge_step2(judge_input)
        step_judgments.append(s2)

        s3 = await self._judge_step3(judge_input)
        step_judgments.append(s3)

        overall_verdict, overall_score = self._aggregate_verdicts(step_judgments)
        can_proceed = overall_verdict != "BLOCK"

        all_findings: list[str] = []
        for sj in step_judgments:
            all_findings.extend(sj.findings[:3])

        bsa_summary, bsa_note = await self._build_bsa_summary(
            step_judgments, overall_verdict, overall_score, can_proceed, all_findings
        )

        all_recommendations: list[str] = []
        for sj in step_judgments:
            all_recommendations.extend(sj.recommendations[:3])

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        try:
            logger.info(
                "driver_pipeline_judge_done",
                session_id=session_id,
                verdict=overall_verdict,
                score=overall_score,
                elapsed_ms=elapsed_ms,
            )
        except Exception:
            pass

        quality_scorecard = self._build_quality_scorecard(
            step_judgments, overall_verdict, overall_score, can_proceed
        )
        rule_scores = self._build_rule_scores(step_judgments)

        return DriverPipelineJudgeOutput(
            session_id=session_id,
            overall_verdict=overall_verdict,
            overall_score=overall_score,
            overall_summary=bsa_summary,
            can_proceed=can_proceed,
            step_judgments=step_judgments,
            recommendations=all_recommendations,
            bsa_review_summary=f"{bsa_summary} {bsa_note}".strip(),
            judged_at=datetime.now(timezone.utc).isoformat(),
            quality_scorecard=quality_scorecard,
            rule_scores=rule_scores,
        )

    # ------------------------------------------------------------------
    # Scorecard / Rule scores
    # ------------------------------------------------------------------

    @staticmethod
    def _build_quality_scorecard(
        step_judgments: list[StepJudgment],
        overall_verdict: str,
        overall_score: float,
        can_proceed: bool,
    ) -> dict[str, Any]:
        by_step = {sj.step: sj for sj in step_judgments}
        s1 = by_step.get("business_mapping")
        s2 = by_step.get("logic_builder")
        s3 = by_step.get("driver_validation")

        s1_det = s1.details if s1 else {}
        s2_det = s2.details if s2 else {}
        s3_det = s3.details if s3 else {}

        blocking_steps = [sj.step for sj in step_judgments if sj.verdict.upper() == "BLOCK"]
        warning_steps = [sj.step for sj in step_judgments if sj.verdict.upper() == "WARN"]

        return {
            "overall_verdict": overall_verdict,
            "overall_score": overall_score,
            "can_proceed": can_proceed,
            "step_scores": {
                "business_mapping":  s1.score if s1 else None,
                "logic_builder":     s2.score if s2 else None,
                "driver_validation": s3.score if s3 else None,
            },
            "step_verdicts": {
                "business_mapping":  s1.verdict if s1 else None,
                "logic_builder":     s2.verdict if s2 else None,
                "driver_validation": s3.verdict if s3 else None,
            },
            "kpis": {
                # Step 1 — business mapping
                "brd_coverage_rate":             s1_det.get("brd_coverage_rate"),
                "active_brd_filter_count":       s1_det.get("active_brd_filter_count"),
                "uncovered_brd_key_count":       s1_det.get("uncovered_brd_key_count"),
                "filter_candidate_count":        s1_det.get("filter_candidate_count"),
                "unmapped_concept_count":        s1_det.get("unmapped_concept_count"),
                "critical_field_swap_count":     s1_det.get("critical_field_swap_count"),
                "ibc_aha_context_match":         s1_det.get("ibc_aha_context_match"),
                # Step 2 — logic builder
                "filter_count":                  s2_det.get("filter_count"),
                "filter_generation_rate":        s2_det.get("filter_generation_rate"),
                "open_item_count":               s2_det.get("open_item_count"),
                "open_item_ratio":               s2_det.get("open_item_ratio"),
                "transformation_violation_count": s2_det.get("transformation_violation_count"),
                "direction_error_count":         s2_det.get("direction_error_count"),
                "hardcoded_date_count":          (s1_det.get("hardcoded_date_error_count") or 0)
                                                  + (s2_det.get("hardcoded_date_count") or 0),
                "dropped_candidate_count":       s2_det.get("dropped_candidate_count"),
                "bad_filter_id_count":           s2_det.get("bad_filter_id_count"),
                "missing_traceability_count":    s2_det.get("missing_traceability_count"),
                "missing_bsa_question_count":    (s1_det.get("open_items_missing_question_count") or 0)
                                                  + (s2_det.get("missing_bsa_question_count") or 0),
                # Step 3 — driver validation
                "validator_total_high":          s3_det.get("total_high"),
                "validator_total_medium":        s3_det.get("total_medium"),
                "validator_proceed_inconsistent": s3_det.get("proceed_inconsistent"),
                "missed_transform_count":        s3_det.get("missed_transform_count"),
                "missed_conflict_count":         s3_det.get("missed_conflict_count"),
                "missed_trace_count":            s3_det.get("missed_trace_count"),
                "validator_accurate":            s3_det.get("validator_accurate"),
            },
            "blocking_steps": blocking_steps,
            "warning_steps":  warning_steps,
            "blocking_count": len(blocking_steps),
            "warning_count":  len(warning_steps),
        }

    @staticmethod
    def _build_rule_scores(step_judgments: list[StepJudgment]) -> list[dict[str, Any]]:
        """
        Synthesize H1-style RuleScore entries from the deterministic check details.
        Each rule maps to one judgable concern; verdict = PASS|WARN|FAIL.
        """
        by_step = {sj.step: sj for sj in step_judgments}
        s1_det = by_step.get("business_mapping").details  if by_step.get("business_mapping")  else {}
        s2_det = by_step.get("logic_builder").details      if by_step.get("logic_builder")      else {}
        s3_det = by_step.get("driver_validation").details  if by_step.get("driver_validation")  else {}

        def _v(fail_cond: bool, warn_cond: bool = False) -> str:
            if fail_cond:
                return "FAIL"
            if warn_cond:
                return "WARN"
            return "PASS"

        rules: list[dict[str, Any]] = []

        # R1 — BRD coverage
        coverage = s1_det.get("brd_coverage_rate", 1.0) or 0.0
        rules.append({
            "rule_id": "R1_BRD_COVERAGE",
            "rule_name": "BRD filter key coverage",
            "step": "business_mapping",
            "verdict": _v(coverage < 0.7, coverage < 0.95),
            "score": round(coverage, 4),
            "weight": 0.20,
            "evidence": (
                f"{s1_det.get('uncovered_brd_key_count', 0)} of "
                f"{s1_det.get('active_brd_filter_count', 0)} BRD filter keys uncovered."
            ),
            "blocking": coverage < 0.7,
            "recommendations": [],
        })

        # R2 — Field mapping correctness (LOB/Coverage swap)
        swap_count = s1_det.get("critical_field_swap_count", 0) or 0
        rules.append({
            "rule_id": "R2_FIELD_MAPPING_CORRECTNESS",
            "rule_name": "DART field correctness (LOB/Coverage)",
            "step": "business_mapping",
            "verdict": _v(swap_count > 0),
            "score": 0.0 if swap_count > 0 else 1.0,
            "weight": 0.15,
            "evidence": f"{swap_count} critical field swap(s) detected.",
            "blocking": swap_count > 0,
            "recommendations": (
                ["Map LOB→MED_LOB_ROLLUP and Coverage→CVG_CTG_CD."] if swap_count > 0 else []
            ),
        })

        # R3 — Date safety
        hardcoded_total = (
            (s1_det.get("hardcoded_date_error_count") or 0)
            + (s2_det.get("hardcoded_date_count") or 0)
        )
        rules.append({
            "rule_id": "R3_DATE_SAFETY",
            "rule_name": "No hardcoded date literals (must use :run_date)",
            "step": "business_mapping+logic_builder",
            "verdict": _v(hardcoded_total > 0),
            "score": 0.0 if hardcoded_total > 0 else 1.0,
            "weight": 0.10,
            "evidence": f"{hardcoded_total} hardcoded date literal(s) across steps 1-2.",
            "blocking": hardcoded_total > 0,
            "recommendations": (
                ["Replace hardcoded date literals with :run_date."] if hardcoded_total > 0 else []
            ),
        })

        # R4 — No transformations in driver SQL
        transform_count = s2_det.get("transformation_violation_count", 0) or 0
        rules.append({
            "rule_id": "R4_NO_TRANSFORMATIONS",
            "rule_name": "Driver SQL contains no transformation expressions",
            "step": "logic_builder",
            "verdict": _v(transform_count > 0),
            "score": 0.0 if transform_count > 0 else 1.0,
            "weight": 0.15,
            "evidence": f"{transform_count} transformation pattern(s) found in driver SQL.",
            "blocking": transform_count > 0,
            "recommendations": (
                ["Move CASE WHEN / functions out of driver predicates."] if transform_count > 0 else []
            ),
        })

        # R5 — Direction correctness (include→IN, exclude→NOT IN)
        direction_count = s2_det.get("direction_error_count", 0) or 0
        rules.append({
            "rule_id": "R5_DIRECTION_CORRECTNESS",
            "rule_name": "include uses IN, exclude uses NOT IN",
            "step": "logic_builder",
            "verdict": _v(direction_count > 0),
            "score": 0.0 if direction_count > 0 else 1.0,
            "weight": 0.10,
            "evidence": f"{direction_count} IN/NOT IN direction error(s).",
            "blocking": direction_count > 0,
            "recommendations": [],
        })

        # R6 — Candidate coverage (no silent drops)
        dropped_count = s2_det.get("dropped_candidate_count", 0) or 0
        rules.append({
            "rule_id": "R6_CANDIDATE_COVERAGE",
            "rule_name": "Every FilterCandidate produces a CommonFilter",
            "step": "logic_builder",
            "verdict": _v(dropped_count > 0),
            "score": (
                round(s2_det.get("filter_generation_rate") or 1.0, 4)
                if dropped_count == 0 else 0.0
            ),
            "weight": 0.10,
            "evidence": f"{dropped_count} candidate(s) dropped between steps 1 and 2.",
            "blocking": dropped_count > 0,
            "recommendations": [],
        })

        # R7 — Validator accuracy (Step 3 quality)
        proceed_bad = bool(s3_det.get("proceed_inconsistent"))
        missed = (
            (s3_det.get("missed_transform_count") or 0)
            + (s3_det.get("missed_conflict_count") or 0)
        )
        rules.append({
            "rule_id": "R7_VALIDATOR_ACCURACY",
            "rule_name": "Validator can_proceed is consistent and catches all issues",
            "step": "driver_validation",
            "verdict": _v(proceed_bad or missed > 0),
            "score": 0.0 if (proceed_bad or missed > 0) else 1.0,
            "weight": 0.10,
            "evidence": (
                f"proceed_inconsistent={proceed_bad}; "
                f"{missed} transformation/conflict issue(s) missed by validator."
            ),
            "blocking": proceed_bad or missed > 0,
            "recommendations": (
                ["Re-run driver_validator_agent."] if (proceed_bad or missed > 0) else []
            ),
        })

        # R8 — Traceability completeness (warn-level)
        trace_missing = (
            (s2_det.get("missing_traceability_count") or 0)
            + (s3_det.get("missed_trace_count") or 0)
        )
        rules.append({
            "rule_id": "R8_TRACEABILITY",
            "rule_name": "Every filter has BRD traceability",
            "step": "logic_builder+driver_validation",
            "verdict": _v(False, trace_missing > 0),
            "score": 1.0 if trace_missing == 0 else max(0.0, 1.0 - 0.05 * trace_missing),
            "weight": 0.05,
            "evidence": f"{trace_missing} filter(s) missing BRD traceability.",
            "blocking": False,
            "recommendations": [],
        })

        # R9 — Open item discipline (every open_item carries a bsa_question)
        oi_missing = (
            (s1_det.get("open_items_missing_question_count") or 0)
            + (s2_det.get("missing_bsa_question_count") or 0)
        )
        rules.append({
            "rule_id": "R9_OPEN_ITEM_QUALITY",
            "rule_name": "Every open_item carries a bsa_question",
            "step": "business_mapping+logic_builder",
            "verdict": _v(False, oi_missing > 0),
            "score": 1.0 if oi_missing == 0 else max(0.0, 1.0 - 0.1 * oi_missing),
            "weight": 0.05,
            "evidence": f"{oi_missing} open_item(s) without a bsa_question.",
            "blocking": False,
            "recommendations": [],
        })

        return rules

    # ------------------------------------------------------------------
    # Step 1 — Business Mapping
    # ------------------------------------------------------------------

    async def _judge_step1(self, judge_input: DriverPipelineJudgeInput) -> StepJudgment:
        det = _check_step1(judge_input.driver_mapping, judge_input.brd_context)

        # Fast-path BLOCK for critical field swaps or hardcoded dates
        if det["critical_field_swaps"] or det["hardcoded_date_errors"]:
            return StepJudgment(
                step="business_mapping",
                verdict="BLOCK",
                score=0.0,
                summary="Critical mapping errors detected — field swap or hardcoded dates require immediate correction.",
                findings=det["findings"][:10],
                recommendations=[
                    "Correct the LOB↔Coverage field swap (LOB→MED_LOB_ROLLUP, Coverage→CVG_CTG_CD)."
                    if det["critical_field_swaps"] else
                    "Replace hardcoded date literals with :run_date in all sql_clause fields."
                ],
                details=det,
            )

        brd = judge_input.brd_context
        filter_candidates = judge_input.driver_mapping.get("filter_candidates") or []

        llm_response = await self.llm_call(
            STEP1_MAPPING_JUDGE_PROMPT.format(
                dart_field_rules=DART_FIELD_RULES,
                filters_and_parameters_json=json.dumps(
                    brd.filters_and_parameters, indent=2, default=str
                ),
                in_scope=brd.in_scope or "(not provided)",
                out_of_scope=brd.out_of_scope or "(not provided)",
                active_filter_keys=json.dumps(brd.active_filter_keys),
                filter_candidates_json=json.dumps(filter_candidates, indent=2, default=str),
                unmapped_concepts=json.dumps(
                    judge_input.driver_mapping.get("unmapped_concepts") or []
                ),
                ibc_aha_context=judge_input.driver_mapping.get("ibc_aha_context", ""),
                deterministic_findings="\n".join(det["findings"]) or "None",
            )
        )

        verdict = self._extract_verdict(llm_response)
        score = self._extract_score(llm_response, verdict)

        # Escalate based on deterministic findings
        if det["uncovered_brd_keys"]:
            verdict = "BLOCK" if len(det["uncovered_brd_keys"]) > 1 else max_verdict(verdict, "WARN")
            score = min(score, 0.5 if len(det["uncovered_brd_keys"]) > 1 else 0.75)
        if det["context_mismatch"]:
            verdict = max_verdict(verdict, "WARN")
            score = min(score, 0.75)

        findings = list(llm_response.get("findings") or [])
        findings = _merge_findings(det["findings"], findings)

        return StepJudgment(
            step="business_mapping",
            verdict=verdict,
            score=round(max(0.0, min(1.0, score)), 4),
            summary=_safe_str(llm_response.get("summary")),
            findings=findings[:10],
            recommendations=_coerce_str_list(llm_response.get("recommendations"))[:8],
            details=det,
        )

    # ------------------------------------------------------------------
    # Step 2 — Logic Builder
    # ------------------------------------------------------------------

    async def _judge_step2(self, judge_input: DriverPipelineJudgeInput) -> StepJudgment:
        det = _check_step2(judge_input.driver_logic, judge_input.driver_mapping)

        # Fast-path BLOCK for transformations — mirrors validator_rules check 1
        if det["transformation_violations"] or det["where_clause_transforms"]:
            violations = det["transformation_violations"] + (
                [f"sql_where_clause: {det['where_clause_transforms']}"]
                if det["where_clause_transforms"] else []
            )
            return StepJudgment(
                step="logic_builder",
                verdict="BLOCK",
                score=0.0,
                summary="Transformation expressions detected in driver SQL — these must not appear in filter predicates.",
                findings=[f"Transformation violation: {v}" for v in violations[:5]],
                recommendations=[
                    "Move CASE WHEN / function logic to the Transformation Rules layer.",
                    "Driver WHERE clause must use raw column references and IN/NOT IN only.",
                ],
                details=det,
            )

        brd = judge_input.brd_context
        filter_candidates = judge_input.driver_mapping.get("filter_candidates") or []
        common_filters = judge_input.driver_logic.get("common_filters") or []

        llm_response = await self.llm_call(
            STEP2_LOGIC_JUDGE_PROMPT.format(
                dart_field_rules=DART_FIELD_RULES,
                in_scope=brd.in_scope or "(not provided)",
                out_of_scope=brd.out_of_scope or "(not provided)",
                filter_candidates_json=json.dumps(filter_candidates, indent=2, default=str),
                common_filters_json=json.dumps(common_filters, indent=2, default=str),
                sql_where_clause=judge_input.driver_logic.get("sql_where_clause") or "",
                global_filter_count=judge_input.driver_logic.get("global_filter_count", len(common_filters)),
                open_item_count=judge_input.driver_logic.get("open_item_count", 0),
                ibc_aha_context=judge_input.driver_logic.get("ibc_aha_context", ""),
                deterministic_findings="\n".join(det["findings"]) or "None",
            )
        )

        verdict = self._extract_verdict(llm_response)
        score = self._extract_score(llm_response, verdict)

        # Escalate from deterministic findings
        if det["direction_errors"]:
            verdict = "BLOCK"
            score = min(score, 0.2)
        if det["dropped_candidates"] > 0:
            verdict = "BLOCK"
            score = min(score, 0.1)
        if det["hardcoded_date_errors"]:
            verdict = "BLOCK"
            score = min(score, 0.0)
        if det["open_item_ratio"] > 0.5 and verdict == "PASS":
            verdict = "WARN"
            score = min(score, 0.75)

        findings = _merge_findings(det["findings"], list(llm_response.get("findings") or []))

        return StepJudgment(
            step="logic_builder",
            verdict=verdict,
            score=round(max(0.0, min(1.0, score)), 4),
            summary=_safe_str(llm_response.get("summary")),
            findings=findings[:10],
            recommendations=_coerce_str_list(llm_response.get("recommendations"))[:8],
            details=det,
        )

    # ------------------------------------------------------------------
    # Step 3 — Driver Validation
    # ------------------------------------------------------------------

    async def _judge_step3(self, judge_input: DriverPipelineJudgeInput) -> StepJudgment:
        det = _check_step3(judge_input.driver_validation, judge_input.driver_logic)

        # Fast-path BLOCK for can_proceed inconsistency or missed critical issues
        if det["proceed_inconsistent"] or det["missed_transforms"] or det["missed_conflicts"]:
            return StepJudgment(
                step="driver_validation",
                verdict="BLOCK",
                score=0.0,
                summary="Validator produced incorrect results — can_proceed inconsistency or missed critical issues.",
                findings=det["findings"][:10],
                recommendations=[
                    "Re-run the driver_validator_agent. "
                    "Ensure can_proceed=True only when total_high==0.",
                ],
                details=det,
            )

        brd = judge_input.brd_context
        common_filters = judge_input.driver_logic.get("common_filters") or []

        llm_response = await self.llm_call(
            STEP3_VALIDATION_JUDGE_PROMPT.format(
                requirements=brd.requirements or "(not provided)",
                in_scope=brd.in_scope or "(not provided)",
                out_of_scope=brd.out_of_scope or "(not provided)",
                common_filters_json=json.dumps(common_filters[:20], indent=2, default=str),
                sql_where_clause=judge_input.driver_logic.get("sql_where_clause") or "",
                driver_validation_json=json.dumps(judge_input.driver_validation, indent=2, default=str),
                deterministic_findings="\n".join(det["findings"]) or "None",
            )
        )

        verdict = self._extract_verdict(llm_response)
        score = self._extract_score(llm_response, verdict)

        if det["missed_trace_filters"] and verdict == "PASS":
            verdict = "WARN"
            score = min(score, 0.75)

        findings = _merge_findings(det["findings"], list(llm_response.get("findings") or []))

        return StepJudgment(
            step="driver_validation",
            verdict=verdict,
            score=round(max(0.0, min(1.0, score)), 4),
            summary=_safe_str(llm_response.get("summary")),
            findings=findings[:10],
            recommendations=_coerce_str_list(llm_response.get("recommendations"))[:8],
            details=det,
        )

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate_verdicts(step_judgments: list[StepJudgment]) -> tuple[str, float]:
        if not step_judgments:
            return "BLOCK", 0.0
        verdicts = [sj.verdict.upper() for sj in step_judgments]
        avg_score = round(sum(sj.score for sj in step_judgments) / len(step_judgments), 4)
        if "BLOCK" in verdicts:
            return "BLOCK", avg_score
        if "WARN" in verdicts:
            return "WARN", avg_score
        return "PASS", avg_score

    async def _build_bsa_summary(
        self,
        step_judgments: list[StepJudgment],
        overall_verdict: str,
        overall_score: float,
        can_proceed: bool,
        all_findings: list[str],
    ) -> tuple[str, str]:
        by_step = {sj.step: sj for sj in step_judgments}
        s1 = by_step.get("business_mapping")
        s2 = by_step.get("logic_builder")
        s3 = by_step.get("driver_validation")
        try:
            response = await self.llm_call(
                PIPELINE_OVERALL_SUMMARY_PROMPT.format(
                    step1_verdict=s1.verdict if s1 else "N/A",
                    step1_score=s1.score if s1 else 0.0,
                    step2_verdict=s2.verdict if s2 else "N/A",
                    step2_score=s2.score if s2 else 0.0,
                    step3_verdict=s3.verdict if s3 else "N/A",
                    step3_score=s3.score if s3 else 0.0,
                    overall_verdict=overall_verdict,
                    overall_score=overall_score,
                    can_proceed=can_proceed,
                    all_findings="\n".join(f"- {f}" for f in all_findings[:9]) or "None",
                )
            )
            return _safe_str(response.get("summary")), _safe_str(response.get("bsa_note"))
        except Exception:
            return (
                f"Driver pipeline scored {overall_score:.2f} overall ({overall_verdict}).",
                "Review step-level findings for details.",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_verdict(llm_response: dict) -> str:
        raw = _safe_str(llm_response.get("verdict")).upper()
        return raw if raw in ("PASS", "WARN", "BLOCK") else "WARN"

    @staticmethod
    def _extract_score(llm_response: dict, verdict: str) -> float:
        try:
            return round(max(0.0, min(1.0, float(llm_response.get("score", 0)))), 4)
        except (TypeError, ValueError):
            return _verdict_score(verdict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def max_verdict(a: str, b: str) -> str:
    order = {"PASS": 0, "WARN": 1, "BLOCK": 2}
    return a if order.get(a.upper(), 0) >= order.get(b.upper(), 0) else b


def _coerce_str_item(f: object) -> str:
    if isinstance(f, str):
        return f
    if isinstance(f, dict):
        for key in (
            "finding",
            "recommendation",
            "suggestion",
            "action",
            "message",
            "text",
            "description",
            "detail",
            "issue",
        ):
            v = f.get(key)
            if isinstance(v, str) and v.strip():
                return v
        try:
            return json.dumps(f, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(f)
    return str(f)


def _coerce_str_list(raw) -> list[str]:
    return [s for s in (_coerce_str_item(x) for x in (raw or [])) if s]


def _merge_findings(deterministic: list[str], llm: list) -> list[str]:
    """Combine deterministic findings first (they're ground truth), then LLM additions.

    LLM responses occasionally return findings as dicts instead of strings;
    coerce to string before deduping so the set() lookup doesn't blow up.
    """
    seen = set(deterministic)
    merged = list(deterministic)
    for raw in llm:
        f = _coerce_str_item(raw)
        if f and f not in seen:
            merged.append(f)
            seen.add(f)
    return merged
