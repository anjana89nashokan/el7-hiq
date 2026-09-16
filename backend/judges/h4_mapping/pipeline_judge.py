"""
MappingPipelineJudge — LLM judge for the mapping generation agent.

Evaluates the mapping_result.json produced by the mapping stage against
seven rules (R1-R7). Each rule produces a deterministic check result and
contributes to the final per-rule scorecard.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from judges.base_judge import BaseJudge
from judges.h4_mapping.prompts import (
    MAPPING_JUDGE_PROMPT,
    MAPPING_JUDGE_RULES,
    MAPPING_OVERALL_SUMMARY_PROMPT,
)
from judges.h4_mapping.schemas import (
    MappingPipelineJudgeInput,
    MappingPipelineJudgeOutput,
    MappingStepJudgment,
)

try:
    import structlog
    logger = structlog.get_logger()
except Exception:
    import logging
    logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Match-level → expected score band (R2)
_MATCH_SCORE_BANDS: dict[str, tuple[float, float]] = {
    "L1": (0.70, 1.00),
    "L2": (0.50, 0.85),
    "L3": (0.30, 0.70),
}

# Transformation patterns that must NOT appear in the driver common_filter (R7)
_TRANSFORMATION_PATTERNS_IN_DRIVER: list[str] = [
    "CASE WHEN", "COALESCE", "ISNULL", "CONVERT(", "CAST(",
    "SUBSTR(", "LEFT(", "RIGHT(", "UPPER(", "LOWER(", "TRIM(",
    "DECODE(", "NVL(", "IIF(", "FORMAT(", "HAVING",
]

# WHERE / filter keywords that must NOT appear in transformation_rule (R7)
_FILTER_KEYWORDS_IN_TRANSFORM: list[str] = [
    " WHERE ", "WHERE ", " FROM ", " GROUP BY ", " HAVING ",
]

_DATE_FORMAT_RE = re.compile(
    r"\bYYYY[- ]?MM[- ]?DD\b|\bMM[- ]?DD[- ]?YYYY\b|\bDD[- ]?MM[- ]?YYYY\b",
    re.IGNORECASE,
)


def _safe_str(val: Any) -> str:
    return str(val).strip() if val is not None else ""


def _verdict_score(verdict: str) -> float:
    return {"PASS": 1.0, "WARN": 0.7, "BLOCK": 0.2}.get(verdict.upper(), 0.5)


def _max_verdict(a: str, b: str) -> str:
    order = {"PASS": 0, "WARN": 1, "BLOCK": 2}
    return a if order.get(a.upper(), 0) >= order.get(b.upper(), 0) else b


# ---------------------------------------------------------------------------
# R1 — Field Coverage
# ---------------------------------------------------------------------------

def _check_field_coverage(
    mapping_rows: list[dict], layout_attributes: list[dict]
) -> dict[str, Any]:
    layout_names = []
    for a in layout_attributes:
        name = (
            a.get("Attribute Name")
            or a.get("attribute_name")
            or a.get("Field Name")
            or a.get("field_name")
            or a.get("name")
        )
        if name:
            layout_names.append(_safe_str(name).upper())
    layout_set = set(layout_names)

    mapping_targets: list[str] = [
        _safe_str(r.get("target_attribute")).upper()
        for r in mapping_rows
        if r.get("target_attribute")
    ]

    missing = sorted(layout_set - set(mapping_targets))
    extra = [n for n in mapping_targets if n not in layout_set]

    name_counts: dict[str, int] = {}
    for n in mapping_targets:
        name_counts[n] = name_counts.get(n, 0) + 1
    duplicates = [n for n, c in name_counts.items() if c > 1]

    coverage_rate = (
        round(1.0 - len(missing) / len(layout_set), 4) if layout_set else 1.0
    )

    findings: list[str] = []
    if missing:
        findings.append(f"R1: {len(missing)} layout field(s) missing a mapping row: {missing[:5]}")
    if duplicates:
        findings.append(f"R1: {len(duplicates)} target_attribute(s) appear in multiple rows: {duplicates[:5]}")
    if extra:
        findings.append(f"R1: {len(extra)} mapping row(s) target a field not in the layout: {extra[:5]}")

    return {
        "layout_field_count": len(layout_set),
        "mapping_row_count": len(mapping_rows),
        "missing_fields": missing[:10],
        "duplicate_targets": duplicates[:10],
        "extra_targets": extra[:10],
        "missing_field_count": len(missing),
        "duplicate_target_count": len(duplicates),
        "extra_target_count": len(extra),
        "field_coverage_rate": coverage_rate,
        "findings": findings,
        "is_blocking": bool(missing or duplicates),
    }


# ---------------------------------------------------------------------------
# R2 — Match Type Accuracy
# ---------------------------------------------------------------------------

def _check_match_accuracy(mapping_rows: list[dict]) -> dict[str, Any]:
    band_violations: list[str] = []
    null_match_no_open_item: list[str] = []
    counts = {"L1": 0, "L2": 0, "L3": 0, "null": 0, "other": 0}

    for r in mapping_rows:
        target = _safe_str(r.get("target_attribute"))
        level_raw = _safe_str(r.get("match_level")).upper()
        score = r.get("match_score")
        try:
            score_f = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_f = None

        if level_raw in _MATCH_SCORE_BANDS:
            counts[level_raw] += 1
            if score_f is None:
                band_violations.append(
                    f"R2: '{target}' match_level={level_raw} but match_score is missing."
                )
            else:
                lo, hi = _MATCH_SCORE_BANDS[level_raw]
                if not (lo <= score_f <= hi):
                    band_violations.append(
                        f"R2: '{target}' match_level={level_raw} score={score_f:.2f} "
                        f"outside band [{lo}, {hi}]."
                    )
        elif level_raw in ("", "NULL", "NONE", "NO_MATCH"):
            counts["null"] += 1
            if not r.get("open_item"):
                null_match_no_open_item.append(target or "?")
        else:
            counts["other"] += 1

    if null_match_no_open_item:
        band_violations.append(
            f"R2: {len(null_match_no_open_item)} null/no_match row(s) lack open_item=True: "
            f"{null_match_no_open_item[:5]}"
        )

    total_classified = counts["L1"] + counts["L2"] + counts["L3"]
    accuracy_rate = (
        round(1.0 - (len(band_violations) - len(null_match_no_open_item)) / max(total_classified, 1), 4)
        if total_classified > 0 else 1.0
    )
    accuracy_rate = max(0.0, min(1.0, accuracy_rate))

    findings = list(band_violations[:10])

    return {
        "l1_count": counts["L1"],
        "l2_count": counts["L2"],
        "l3_count": counts["L3"],
        "null_count": counts["null"],
        "other_count": counts["other"],
        "band_violations": band_violations[:10],
        "band_violation_count": len(band_violations),
        "null_match_no_open_item_count": len(null_match_no_open_item),
        "match_accuracy_rate": accuracy_rate,
        "findings": findings,
        "is_blocking": False,
    }


# ---------------------------------------------------------------------------
# R3 — Transformation Correctness
# ---------------------------------------------------------------------------

def _check_transformation_correctness(mapping_rows: list[dict]) -> dict[str, Any]:
    syntax_errors: list[str] = []
    transformation_count = 0

    for r in mapping_rows:
        rule = _safe_str(r.get("transformation_rule"))
        if not rule or rule.lower() in ("populate blank", "default", "n/a"):
            continue
        transformation_count += 1
        target = _safe_str(r.get("target_attribute"))

        # Parens balance
        if rule.count("(") != rule.count(")"):
            syntax_errors.append(
                f"R3: '{target}' unbalanced parentheses in transformation_rule."
            )

        # CASE / END balance
        upper = rule.upper()
        case_count = len(re.findall(r"\bCASE\b", upper))
        end_count = len(re.findall(r"\bEND\b", upper))
        if case_count != end_count:
            syntax_errors.append(
                f"R3: '{target}' CASE/END mismatch ({case_count} CASE vs {end_count} END)."
            )

        # Single quote balance (must be even)
        if rule.count("'") % 2 != 0:
            syntax_errors.append(
                f"R3: '{target}' unbalanced single quotes in transformation_rule."
            )

        # Trailing comma before closing paren
        if re.search(r",\s*\)", rule):
            syntax_errors.append(
                f"R3: '{target}' trailing comma before closing paren."
            )

        # SUBSTR — must have 3 comma-separated args inside parens
        for m in re.finditer(r"SUBSTR\s*\(([^)]*)\)", rule, re.IGNORECASE):
            arg_count = len([a for a in m.group(1).split(",") if a.strip()])
            if arg_count != 3:
                syntax_errors.append(
                    f"R3: '{target}' SUBSTR has {arg_count} args (expected 3)."
                )

        # CAST — must contain ' AS '
        for m in re.finditer(r"CAST\s*\(([^)]*)\)", rule, re.IGNORECASE):
            inner = m.group(1)
            if " AS " not in inner.upper():
                syntax_errors.append(
                    f"R3: '{target}' CAST(...) missing AS clause."
                )

        # Date format pattern check — if FORMAT(/DATE_FORMAT( appears, ensure pattern is recognized
        if re.search(r"DATE_FORMAT\s*\(|FORMAT\s*\(", rule, re.IGNORECASE):
            if not _DATE_FORMAT_RE.search(rule):
                syntax_errors.append(
                    f"R3: '{target}' FORMAT/DATE_FORMAT used but no recognized date pattern (YYYYMMDD/etc)."
                )

    correctness_rate = (
        round(1.0 - len(syntax_errors) / max(transformation_count, 1), 4)
        if transformation_count > 0 else 1.0
    )
    correctness_rate = max(0.0, min(1.0, correctness_rate))

    return {
        "transformation_count": transformation_count,
        "syntax_error_count": len(syntax_errors),
        "syntax_errors": syntax_errors[:10],
        "transformation_correctness_rate": correctness_rate,
        "findings": list(syntax_errors[:10]),
        "is_blocking": False,
    }


# ---------------------------------------------------------------------------
# R4 — Join Minimization
# ---------------------------------------------------------------------------

def _check_join_minimization(mapping_rows: list[dict]) -> dict[str, Any]:
    distinct_tables: set[str] = set()
    mapped_row_count = 0

    for r in mapping_rows:
        src = _safe_str(r.get("source_entity"))
        if src:
            distinct_tables.add(src.upper())
            mapped_row_count += 1

    table_count = len(distinct_tables)
    fan_out_ratio = (
        round(table_count / mapped_row_count, 4) if mapped_row_count > 0 else 0.0
    )
    minimization_rate = max(0.0, 1.0 - fan_out_ratio)
    fan_out_excessive = fan_out_ratio > 0.5

    findings: list[str] = []
    if fan_out_excessive:
        findings.append(
            f"R4: fan-out ratio {fan_out_ratio:.2f} ({table_count} tables for "
            f"{mapped_row_count} mapped rows) — possible lack of join optimisation."
        )

    return {
        "distinct_source_table_count": table_count,
        "mapped_row_count": mapped_row_count,
        "fan_out_ratio": fan_out_ratio,
        "join_minimization_rate": round(minimization_rate, 4),
        "fan_out_excessive": fan_out_excessive,
        "distinct_source_tables": sorted(distinct_tables)[:15],
        "findings": findings,
        "is_blocking": False,
    }


# ---------------------------------------------------------------------------
# R5 — NO MATCH Handling
# ---------------------------------------------------------------------------

def _check_no_match_handling(mapping_rows: list[dict]) -> dict[str, Any]:
    no_match_count = 0
    open_item_count = 0
    silent_no_match: list[str] = []

    for r in mapping_rows:
        level = _safe_str(r.get("match_level")).upper()
        is_no_match = level in ("", "NULL", "NONE", "NO_MATCH")
        is_open = bool(r.get("open_item"))
        reason = _safe_str(r.get("open_item_reason"))
        target = _safe_str(r.get("target_attribute")) or "?"

        if is_open:
            open_item_count += 1
        if is_no_match:
            no_match_count += 1
        if (is_no_match or is_open) and not reason:
            silent_no_match.append(target)

    findings: list[str] = []
    if silent_no_match:
        findings.append(
            f"R5: {len(silent_no_match)} NO MATCH/open_item row(s) without open_item_reason: "
            f"{silent_no_match[:5]}"
        )

    no_match_handling_rate = (
        round(
            1.0 - len(silent_no_match) / max(no_match_count + open_item_count, 1),
            4,
        )
        if (no_match_count + open_item_count) > 0
        else 1.0
    )

    return {
        "no_match_count": no_match_count,
        "open_item_count": open_item_count,
        "silent_no_match_count": len(silent_no_match),
        "silent_no_match": silent_no_match[:10],
        "no_match_handling_rate": max(0.0, min(1.0, no_match_handling_rate)),
        "findings": findings,
        "is_blocking": bool(silent_no_match),
    }


# ---------------------------------------------------------------------------
# R6 — IndiMap Reuse Declared
# ---------------------------------------------------------------------------

def _check_indimap_reuse(mapping_rows: list[dict]) -> dict[str, Any]:
    l1_count = 0
    undeclared_l1: list[str] = []

    for r in mapping_rows:
        if _safe_str(r.get("match_level")).upper() != "L1":
            continue
        l1_count += 1
        target = _safe_str(r.get("target_attribute")) or "?"
        src_entity = _safe_str(r.get("source_entity"))
        src_attr = _safe_str(r.get("source_attribute"))
        if not (src_entity and src_attr):
            undeclared_l1.append(target)

    findings: list[str] = []
    if undeclared_l1:
        findings.append(
            f"R6: {len(undeclared_l1)} L1 reuse row(s) missing source_entity/source_attribute: "
            f"{undeclared_l1[:5]}"
        )

    declaration_rate = (
        round(1.0 - len(undeclared_l1) / l1_count, 4) if l1_count > 0 else 1.0
    )

    return {
        "l1_count": l1_count,
        "undeclared_l1_count": len(undeclared_l1),
        "undeclared_l1": undeclared_l1[:10],
        "indimap_declaration_rate": max(0.0, min(1.0, declaration_rate)),
        "findings": findings,
        "is_blocking": False,
    }


# ---------------------------------------------------------------------------
# R7 — Transformation vs Driver Separation
# ---------------------------------------------------------------------------

def _check_separation(
    mapping_rows: list[dict], common_filter: str
) -> dict[str, Any]:
    transform_with_filter: list[str] = []
    driver_with_transform: list[str] = []

    # Transform must not contain WHERE / FROM / GROUP BY / HAVING
    for r in mapping_rows:
        rule = _safe_str(r.get("transformation_rule"))
        if not rule:
            continue
        upper = " " + rule.upper() + " "
        target = _safe_str(r.get("target_attribute")) or "?"
        for kw in _FILTER_KEYWORDS_IN_TRANSFORM:
            if kw in upper:
                transform_with_filter.append(
                    f"'{target}' transformation_rule contains '{kw.strip()}'"
                )
                break

    # Driver SQL must not contain transformation patterns
    cf_upper = common_filter.upper() if common_filter else ""
    for pat in _TRANSFORMATION_PATTERNS_IN_DRIVER:
        if pat.upper() in cf_upper:
            driver_with_transform.append(
                f"common_filter contains transformation pattern '{pat}'"
            )

    findings: list[str] = []
    if transform_with_filter:
        findings.append(
            f"R7: {len(transform_with_filter)} transformation_rule(s) contain filter keywords: "
            f"{transform_with_filter[:5]}"
        )
    if driver_with_transform:
        findings.append(
            f"R7: driver common_filter contains transformation pattern(s): "
            f"{driver_with_transform[:5]}"
        )

    return {
        "transform_with_filter_count": len(transform_with_filter),
        "driver_with_transform_count": len(driver_with_transform),
        "transform_with_filter": transform_with_filter[:10],
        "driver_with_transform": driver_with_transform[:10],
        "findings": findings,
        "is_blocking": bool(transform_with_filter or driver_with_transform),
    }


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def _aggregate_checks(
    mapping_rows: list[dict],
    layout_attributes: list[dict],
    common_filter: str,
) -> dict[str, Any]:
    return {
        "r1_field_coverage":         _check_field_coverage(mapping_rows, layout_attributes),
        "r2_match_accuracy":         _check_match_accuracy(mapping_rows),
        "r3_transformation_syntax":  _check_transformation_correctness(mapping_rows),
        "r4_join_minimization":      _check_join_minimization(mapping_rows),
        "r5_no_match_handling":      _check_no_match_handling(mapping_rows),
        "r6_indimap_reuse":          _check_indimap_reuse(mapping_rows),
        "r7_separation":             _check_separation(mapping_rows, common_filter),
    }


# ---------------------------------------------------------------------------
# MappingPipelineJudge
# ---------------------------------------------------------------------------

class MappingPipelineJudge(BaseJudge):
    """Judge for the mapping generation agent output."""

    async def evaluate(
        self, judge_input: MappingPipelineJudgeInput
    ) -> MappingPipelineJudgeOutput:
        started_at = time.perf_counter()
        session_id = judge_input.session_id

        try:
            logger.info("mapping_pipeline_judge_start", session_id=session_id)
        except Exception:
            pass

        mapping_result = judge_input.mapping_result or {}
        transformation_rules = mapping_result.get("transformation_rules") or {}
        rows: list[dict] = transformation_rules.get("rows") or []
        common_filter = (
            judge_input.common_filter
            or transformation_rules.get("common_filter")
            or ""
        )

        # Layout source-of-truth: prefer caller-provided layout_columns,
        # fall back to metadata_attributes.
        layout_attributes: list[dict] = (
            judge_input.layout_columns or judge_input.metadata_attributes or []
        )

        det_results = _aggregate_checks(rows, layout_attributes, common_filter)

        all_findings: list[str] = []
        for r in det_results.values():
            all_findings.extend(r.get("findings", []))

        is_blocking = any(r.get("is_blocking") for r in det_results.values())

        # Fast-path BLOCK if any blocking deterministic check fires
        if is_blocking:
            step_judgment = MappingStepJudgment(
                step="mapping",
                verdict="BLOCK",
                score=0.0,
                summary=(
                    "Mapping output has blocking issues — uncovered or duplicated layout fields, "
                    "silent NO MATCH rows, or driver/transformation separation violations."
                ),
                findings=all_findings[:12],
                recommendations=[
                    "Re-run mapping_row_agent for any uncovered layout fields.",
                    "Document open_item_reason for every NO MATCH / open item.",
                    "Move WHERE/filter logic out of transformation_rule into the driver layer.",
                ],
                details={"deterministic_checks": det_results},
            )
        else:
            step_judgment = await self._llm_judge(judge_input, det_results, all_findings, rows)

        overall_verdict = step_judgment.verdict
        overall_score = step_judgment.score
        can_proceed = overall_verdict != "BLOCK"

        bsa_summary, bsa_note = await self._build_bsa_summary(
            step_judgment, overall_verdict, overall_score, can_proceed, all_findings
        )

        quality_scorecard = self._build_quality_scorecard(
            det_results, step_judgment, overall_verdict, overall_score, can_proceed, rows
        )
        rule_scores = self._build_rule_scores(det_results)

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        try:
            logger.info(
                "mapping_pipeline_judge_done",
                session_id=session_id,
                verdict=overall_verdict,
                score=overall_score,
                elapsed_ms=elapsed_ms,
            )
        except Exception:
            pass

        return MappingPipelineJudgeOutput(
            session_id=session_id,
            overall_verdict=overall_verdict,
            overall_score=overall_score,
            overall_summary=bsa_summary,
            can_proceed=can_proceed,
            step_judgments=[step_judgment],
            recommendations=step_judgment.recommendations[:8],
            bsa_review_summary=f"{bsa_summary} {bsa_note}".strip(),
            judged_at=datetime.now(timezone.utc).isoformat(),
            quality_scorecard=quality_scorecard,
            rule_scores=rule_scores,
        )

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    async def _llm_judge(
        self,
        judge_input: MappingPipelineJudgeInput,
        det_results: dict[str, Any],
        all_findings: list[str],
        rows: list[dict],
    ) -> MappingStepJudgment:
        brd = judge_input.brd_context
        mapping_result = judge_input.mapping_result or {}
        tr = mapping_result.get("transformation_rules") or {}

        llm_response = await self.llm_call(
            MAPPING_JUDGE_PROMPT.format(
                rules=MAPPING_JUDGE_RULES,
                in_scope=brd.in_scope or "(not provided)",
                out_of_scope=brd.out_of_scope or "(not provided)",
                requirements=brd.requirements or "(not provided)",
                common_rules_json=json.dumps(brd.common_rules, indent=2, default=str),
                file_attributes_json=json.dumps(brd.file_attributes_mapping, indent=2, default=str),
                common_filter=judge_input.common_filter or tr.get("common_filter") or "(none)",
                driver_predicates_json=json.dumps(
                    judge_input.driver_predicates[:20], indent=2, default=str
                ),
                layout_columns_json=json.dumps(
                    (judge_input.layout_columns or judge_input.metadata_attributes)[:30],
                    indent=2,
                    default=str,
                ),
                layout_count=len(judge_input.layout_columns or judge_input.metadata_attributes),
                mapping_common_rules_json=json.dumps(
                    mapping_result.get("common_rules") or [], indent=2, default=str
                ),
                common_rules_count=len(mapping_result.get("common_rules") or []),
                target_entity=tr.get("target_entity") or "(none)",
                driver_table_required=tr.get("driver_table_required"),
                history_data_pull=tr.get("history_data_pull"),
                row_count=len(rows),
                rows_json=json.dumps(rows[:30], indent=2, default=str),
                deterministic_findings="\n".join(all_findings) or "None",
            )
        )

        verdict = self._extract_verdict(llm_response)
        score = self._extract_score(llm_response, verdict)

        # Escalate based on deterministic findings
        if det_results["r2_match_accuracy"]["band_violation_count"] > 5:
            verdict = _max_verdict(verdict, "WARN")
            score = min(score, 0.80)
        if det_results["r3_transformation_syntax"]["syntax_error_count"] > 0:
            verdict = _max_verdict(verdict, "WARN")
            score = min(score, 0.75)
        if det_results["r4_join_minimization"]["fan_out_excessive"]:
            verdict = _max_verdict(verdict, "WARN")
            score = min(score, 0.85)
        if det_results["r6_indimap_reuse"]["undeclared_l1_count"] > 0:
            verdict = _max_verdict(verdict, "WARN")
            score = min(score, 0.85)

        det_findings = []
        for r in det_results.values():
            det_findings.extend(r.get("findings", []))
        merged = _merge_findings(det_findings, list(llm_response.get("findings") or []))

        return MappingStepJudgment(
            step="mapping",
            verdict=verdict,
            score=round(max(0.0, min(1.0, score)), 4),
            summary=_safe_str(llm_response.get("summary")),
            findings=merged[:12],
            recommendations=list(llm_response.get("recommendations") or [])[:8],
            details={"deterministic_checks": det_results},
        )

    # ------------------------------------------------------------------
    # BSA summary
    # ------------------------------------------------------------------

    async def _build_bsa_summary(
        self,
        step_judgment: MappingStepJudgment,
        overall_verdict: str,
        overall_score: float,
        can_proceed: bool,
        all_findings: list[str],
    ) -> tuple[str, str]:
        try:
            response = await self.llm_call(
                MAPPING_OVERALL_SUMMARY_PROMPT.format(
                    step_verdict=step_judgment.verdict,
                    step_score=step_judgment.score,
                    overall_verdict=overall_verdict,
                    overall_score=overall_score,
                    can_proceed=can_proceed,
                    all_findings="\n".join(f"- {f}" for f in all_findings[:9]) or "None",
                )
            )
            return _safe_str(response.get("summary")), _safe_str(response.get("bsa_note"))
        except Exception:
            return (
                f"Mapping scored {overall_score:.2f} ({overall_verdict}).",
                "Review mapping findings for details.",
            )

    # ------------------------------------------------------------------
    # Scorecard / Rule scores
    # ------------------------------------------------------------------

    @staticmethod
    def _build_quality_scorecard(
        det_results: dict[str, Any],
        step_judgment: MappingStepJudgment,
        overall_verdict: str,
        overall_score: float,
        can_proceed: bool,
        rows: list[dict],
    ) -> dict[str, Any]:
        r1 = det_results["r1_field_coverage"]
        r2 = det_results["r2_match_accuracy"]
        r3 = det_results["r3_transformation_syntax"]
        r4 = det_results["r4_join_minimization"]
        r5 = det_results["r5_no_match_handling"]
        r6 = det_results["r6_indimap_reuse"]
        r7 = det_results["r7_separation"]

        total_classified = r2["l1_count"] + r2["l2_count"] + r2["l3_count"]

        return {
            "overall_verdict": overall_verdict,
            "overall_score": overall_score,
            "can_proceed": can_proceed,
            "step_scores":   {"mapping": step_judgment.score},
            "step_verdicts": {"mapping": step_judgment.verdict},
            "kpis": {
                # R1
                "field_coverage_rate":          r1["field_coverage_rate"],
                "layout_field_count":           r1["layout_field_count"],
                "mapping_row_count":            r1["mapping_row_count"],
                "missing_field_count":          r1["missing_field_count"],
                "duplicate_target_count":       r1["duplicate_target_count"],
                # R2
                "l1_match_count":               r2["l1_count"],
                "l2_match_count":               r2["l2_count"],
                "l3_match_count":               r2["l3_count"],
                "no_match_count":               r2["null_count"],
                "l1_match_ratio": (
                    round(r2["l1_count"] / total_classified, 4) if total_classified > 0 else 0.0
                ),
                "l2_match_ratio": (
                    round(r2["l2_count"] / total_classified, 4) if total_classified > 0 else 0.0
                ),
                "l3_match_ratio": (
                    round(r2["l3_count"] / total_classified, 4) if total_classified > 0 else 0.0
                ),
                "match_score_band_violations":  r2["band_violation_count"],
                "match_accuracy_rate":          r2["match_accuracy_rate"],
                # R3
                "transformation_count":             r3["transformation_count"],
                "transformation_syntax_errors":     r3["syntax_error_count"],
                "transformation_correctness_rate":  r3["transformation_correctness_rate"],
                # R4
                "distinct_source_table_count":  r4["distinct_source_table_count"],
                "fan_out_ratio":                r4["fan_out_ratio"],
                "join_minimization_rate":       r4["join_minimization_rate"],
                # R5
                "open_item_count":              r5["open_item_count"],
                "open_item_ratio": (
                    round(r5["open_item_count"] / len(rows), 4) if rows else 0.0
                ),
                "silent_no_match_count":        r5["silent_no_match_count"],
                "no_match_handling_rate":       r5["no_match_handling_rate"],
                # R6
                "indimap_undeclared_l1_count":  r6["undeclared_l1_count"],
                "indimap_declaration_rate":     r6["indimap_declaration_rate"],
                # R7
                "transform_with_filter_count":  r7["transform_with_filter_count"],
                "driver_with_transform_count":  r7["driver_with_transform_count"],
                "driver_separation_violations": (
                    r7["transform_with_filter_count"] + r7["driver_with_transform_count"]
                ),
            },
            "blocking_steps": ["mapping"] if step_judgment.verdict == "BLOCK" else [],
            "warning_steps":  ["mapping"] if step_judgment.verdict == "WARN" else [],
            "blocking_count": 1 if step_judgment.verdict == "BLOCK" else 0,
            "warning_count":  1 if step_judgment.verdict == "WARN" else 0,
        }

    @staticmethod
    def _build_rule_scores(det_results: dict[str, Any]) -> list[dict[str, Any]]:
        """Synthesize H1-style RuleScore entries for R1-R7."""

        def _v(fail_cond: bool, warn_cond: bool = False) -> str:
            if fail_cond:
                return "FAIL"
            if warn_cond:
                return "WARN"
            return "PASS"

        r1 = det_results["r1_field_coverage"]
        r2 = det_results["r2_match_accuracy"]
        r3 = det_results["r3_transformation_syntax"]
        r4 = det_results["r4_join_minimization"]
        r5 = det_results["r5_no_match_handling"]
        r6 = det_results["r6_indimap_reuse"]
        r7 = det_results["r7_separation"]

        return [
            {
                "rule_id": "R1_FIELD_COVERAGE",
                "rule_name": "Every layout field has exactly one mapping row",
                "verdict": _v(r1["missing_field_count"] > 0 or r1["duplicate_target_count"] > 0),
                "score": r1["field_coverage_rate"],
                "weight": 0.25,
                "evidence": (
                    f"{r1['mapping_row_count']} mapping row(s) for {r1['layout_field_count']} "
                    f"layout field(s); {r1['missing_field_count']} missing, "
                    f"{r1['duplicate_target_count']} duplicate, "
                    f"{r1['extra_target_count']} extra."
                ),
                "blocking": r1["missing_field_count"] > 0 or r1["duplicate_target_count"] > 0,
                "recommendations": (
                    ["Map every layout field exactly once — re-run mapping_row_agent for missing fields."]
                    if r1["missing_field_count"] > 0 or r1["duplicate_target_count"] > 0 else []
                ),
            },
            {
                "rule_id": "R2_MATCH_TYPE_ACCURACY",
                "rule_name": "match_level vs match_score consistency (L1 0.70-1.00, L2 0.50-0.85, L3 0.30-0.70)",
                "verdict": _v(False, r2["band_violation_count"] > 0),
                "score": r2["match_accuracy_rate"],
                "weight": 0.10,
                "evidence": (
                    f"{r2['band_violation_count']} band violation(s); "
                    f"L1={r2['l1_count']} L2={r2['l2_count']} L3={r2['l3_count']} null={r2['null_count']}."
                ),
                "blocking": False,
                "recommendations": [],
            },
            {
                "rule_id": "R3_TRANSFORMATION_CORRECTNESS",
                "rule_name": "transformation_rule is syntactically valid",
                "verdict": _v(False, r3["syntax_error_count"] > 0),
                "score": r3["transformation_correctness_rate"],
                "weight": 0.15,
                "evidence": (
                    f"{r3['syntax_error_count']} syntax issue(s) across "
                    f"{r3['transformation_count']} transformations."
                ),
                "blocking": False,
                "recommendations": [],
            },
            {
                "rule_id": "R4_JOIN_MINIMIZATION",
                "rule_name": "Distinct source tables minimised vs row count",
                "verdict": _v(False, r4["fan_out_excessive"]),
                "score": r4["join_minimization_rate"],
                "weight": 0.10,
                "evidence": (
                    f"{r4['distinct_source_table_count']} distinct source table(s) for "
                    f"{r4['mapped_row_count']} mapped row(s); fan_out_ratio="
                    f"{r4['fan_out_ratio']:.2f}."
                ),
                "blocking": False,
                "recommendations": [],
            },
            {
                "rule_id": "R5_NO_MATCH_HANDLING",
                "rule_name": "Every NO MATCH / open_item has a documented investigation path",
                "verdict": _v(r5["silent_no_match_count"] > 0),
                "score": r5["no_match_handling_rate"],
                "weight": 0.15,
                "evidence": (
                    f"{r5['silent_no_match_count']} silent NO MATCH/open_item row(s) "
                    f"out of {r5['no_match_count'] + r5['open_item_count']} total."
                ),
                "blocking": r5["silent_no_match_count"] > 0,
                "recommendations": (
                    ["Populate open_item_reason for every NO MATCH and open_item=True row."]
                    if r5["silent_no_match_count"] > 0 else []
                ),
            },
            {
                "rule_id": "R6_INDIMAP_REUSE_DECLARED",
                "rule_name": "L1 matches declare source_entity + source_attribute",
                "verdict": _v(False, r6["undeclared_l1_count"] > 0),
                "score": r6["indimap_declaration_rate"],
                "weight": 0.10,
                "evidence": (
                    f"{r6['undeclared_l1_count']} of {r6['l1_count']} L1 row(s) "
                    "missing source_entity/source_attribute."
                ),
                "blocking": False,
                "recommendations": [],
            },
            {
                "rule_id": "R7_TRANSFORMATION_DRIVER_SEPARATION",
                "rule_name": "transformations stay out of driver SQL; filter logic stays out of transformations",
                "verdict": _v(
                    r7["transform_with_filter_count"] > 0 or r7["driver_with_transform_count"] > 0
                ),
                "score": (
                    1.0 if (
                        r7["transform_with_filter_count"] == 0
                        and r7["driver_with_transform_count"] == 0
                    ) else 0.0
                ),
                "weight": 0.15,
                "evidence": (
                    f"{r7['transform_with_filter_count']} transformation(s) contain filter keywords; "
                    f"{r7['driver_with_transform_count']} driver-side transformation pattern(s)."
                ),
                "blocking": (
                    r7["transform_with_filter_count"] > 0 or r7["driver_with_transform_count"] > 0
                ),
                "recommendations": (
                    [
                        "Move WHERE/filter logic out of transformation_rule into the driver layer.",
                        "Move CASE WHEN / function logic out of the driver SQL.",
                    ]
                    if (
                        r7["transform_with_filter_count"] > 0
                        or r7["driver_with_transform_count"] > 0
                    )
                    else []
                ),
            },
        ]

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

def _merge_findings(deterministic: list[str], llm: list[str]) -> list[str]:
    """Deterministic findings are ground truth — LLM additions appended after."""
    seen = set(deterministic)
    merged = list(deterministic)
    for f in llm:
        if f not in seen:
            merged.append(f)
            seen.add(f)
    return merged
