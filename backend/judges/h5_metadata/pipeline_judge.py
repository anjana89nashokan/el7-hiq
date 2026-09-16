"""
MetadataPipelineJudge — LLM judge for the metadata extraction agent.

Evaluates:
  metadata_extractor_agent → extracted_metadata

Cross-checks the extracted filespecs and attribute list against the BRD
requirement context and the authoritative layout column list.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from judges.base_judge import BaseJudge
from judges.h5_metadata.prompts import (
    METADATA_OVERALL_SUMMARY_PROMPT,
    STEP2_EXTRACTION_JUDGE_PROMPT,
)
from judges.h5_metadata.schemas import (
    MetadataPipelineJudgeInput,
    MetadataPipelineJudgeOutput,
    MetadataStepJudgment,
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

_REQUIRED_ATTRIBUTE_KEYS: list[str] = [
    "Attribute Name",
    "Logical Attribute Name",
    "Attribute Description",
    "Data Type",
    "Length",
    "Precision",
    "Format",
    "Nullability",
    "Default Value",
    "Primary Key",
    "Foreign Key",
    "Alternate Key1",
]

_VALID_NULLABILITY: frozenset[str] = frozenset({"NOT NULL", "NULLABLE", "NULL", ""})


def _safe_str(val: Any) -> str:
    return str(val).strip() if val is not None else ""


def _verdict_score(verdict: str) -> float:
    return {"PASS": 1.0, "WARN": 0.7, "BLOCK": 0.2}.get(verdict.upper(), 0.5)


# ---------------------------------------------------------------------------
# Deterministic checks — extraction
# ---------------------------------------------------------------------------

def _resolve_extractor_payload(extracted_metadata: dict) -> tuple[dict, dict]:
    """
    Accept either of the two shapes the extractor agent may produce:

    1. Raw tool input shape:
        { "filespecs": {...}, "file1": {...} }

    2. Session-state / response wrapper shape (what extract_metadata_template_values
       writes to state and what the API typically returns):
        { "extracted_filespecs": {...}, "extracted_file1": {...} }

    Returns (filespecs_dict, file1_dict) — empty dicts if neither shape is present.
    """
    filespecs = (
        extracted_metadata.get("filespecs")
        or extracted_metadata.get("extracted_filespecs")
        or {}
    )
    file1 = (
        extracted_metadata.get("file1")
        or extracted_metadata.get("extracted_file1")
        or {}
    )
    return (filespecs if isinstance(filespecs, dict) else {},
            file1 if isinstance(file1, dict) else {})


def _check_extraction(extracted_metadata: dict, layout_columns: list[dict]) -> dict[str, Any]:
    """Cross-check metadata_extractor_agent output against extraction rules."""
    filespecs, file1 = _resolve_extractor_payload(extracted_metadata)
    attributes: list[dict] = file1.get("attributes") or []

    findings: list[str] = []

    # filespecs populated
    filespecs_empty = not filespecs
    if filespecs_empty:
        findings.append("filespecs dict is empty — no file-level metadata was extracted.")

    # file1 header fields
    required_header = [
        "entity_type",
        "entity_physical_name",
        "entity_business_name",
        "entity_description",
    ]
    missing_header = [f for f in required_header if not file1.get(f)]
    if missing_header:
        findings.append(f"file1 header fields missing or empty: {missing_header}")

    # attributes list non-empty
    if not attributes:
        findings.append("file1.attributes list is empty — no column-level metadata was extracted.")

    # All 12 required keys per attribute
    missing_keys_findings: list[str] = []
    null_attr_names: list[int] = []

    for idx, attr in enumerate(attributes, start=1):
        missing = [k for k in _REQUIRED_ATTRIBUTE_KEYS if k not in attr]
        if missing:
            missing_keys_findings.append(
                f"attribute[{idx}] ('{attr.get('Attribute Name', '?')}') missing keys: {missing}"
            )
        if not attr.get("Attribute Name"):
            null_attr_names.append(idx)

    if missing_keys_findings:
        findings.extend(missing_keys_findings[:5])
    if null_attr_names:
        findings.append(f"Attribute Name is null/empty at positions: {null_attr_names[:5]}")

    # Nullability valid values
    bad_nullability: list[str] = []
    for attr in attributes:
        nullability = _safe_str(attr.get("Nullability")).upper()
        if nullability and nullability not in _VALID_NULLABILITY:
            bad_nullability.append(
                f"'{attr.get('Attribute Name')}' Nullability='{attr.get('Nullability')}'"
            )
    if bad_nullability:
        findings.append(
            f"Invalid Nullability values (must be NOT NULL or NULLABLE): {bad_nullability[:5]}"
        )

    # Layout column coverage
    layout_names = [
        _safe_str(
            c.get("field_name") or c.get("Field Name") or c.get("Field") or c.get("name")
        ).upper()
        for c in layout_columns
    ]
    layout_names = [n for n in layout_names if n]

    attr_names_upper = {
        _safe_str(a.get("Attribute Name")).upper()
        for a in attributes
        if a.get("Attribute Name")
    }

    dropped_columns: list[str] = []
    if layout_names and attr_names_upper:
        dropped_columns = [n for n in layout_names if n not in attr_names_upper]
        if dropped_columns:
            findings.append(
                f"{len(dropped_columns)} layout column(s) have no corresponding attribute: "
                f"{dropped_columns[:5]}"
            )

    layout_count = len(layout_names)
    attr_count = len(attributes)
    count_mismatch = layout_count > 0 and attr_count < layout_count

    is_blocking = bool(
        filespecs_empty
        or not attributes
        or null_attr_names
        or missing_keys_findings
        or dropped_columns
    )

    layout_coverage_rate = (
        round(1.0 - (len(dropped_columns) / layout_count), 4)
        if layout_count > 0
        else 1.0
    )
    attribute_completeness_rate = (
        round(1.0 - (len(missing_keys_findings) / attr_count), 4)
        if attr_count > 0
        else 0.0
    )

    return {
        "filespecs_key_count": len(filespecs),
        "filespecs_populated": not filespecs_empty,
        "attribute_count": attr_count,
        "layout_column_count": layout_count,
        "dropped_columns": dropped_columns[:10],
        "dropped_column_count": len(dropped_columns),
        "count_mismatch": count_mismatch,
        "missing_header_fields": missing_header,
        "missing_header_field_count": len(missing_header),
        "missing_keys_finding_count": len(missing_keys_findings),
        "null_attribute_name_positions": null_attr_names[:10],
        "null_attribute_name_count": len(null_attr_names),
        "bad_nullability": bad_nullability[:5],
        "bad_nullability_count": len(bad_nullability),
        # Derived KPIs
        "layout_coverage_rate": layout_coverage_rate,
        "attribute_completeness_rate": attribute_completeness_rate,
        "findings": findings,
        "is_blocking": is_blocking,
    }


# ---------------------------------------------------------------------------
# MetadataPipelineJudge
# ---------------------------------------------------------------------------

class MetadataPipelineJudge(BaseJudge):
    """Judge for the metadata extraction agent output."""

    async def evaluate(
        self, judge_input: MetadataPipelineJudgeInput
    ) -> MetadataPipelineJudgeOutput:
        started_at = time.perf_counter()
        session_id = judge_input.session_id

        try:
            logger.info("metadata_pipeline_judge_start", session_id=session_id)
        except Exception:
            pass

        extraction_judgment = await self._judge_extraction(judge_input)
        step_judgments = [extraction_judgment]

        overall_verdict = extraction_judgment.verdict
        overall_score = extraction_judgment.score
        can_proceed = overall_verdict != "BLOCK"

        all_findings = extraction_judgment.findings[:9]

        bsa_summary, bsa_note = await self._build_bsa_summary(
            extraction_judgment, overall_verdict, overall_score, can_proceed, all_findings
        )

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        try:
            logger.info(
                "metadata_pipeline_judge_done",
                session_id=session_id,
                verdict=overall_verdict,
                score=overall_score,
                elapsed_ms=elapsed_ms,
            )
        except Exception:
            pass

        quality_scorecard = self._build_quality_scorecard(
            extraction_judgment, overall_verdict, overall_score, can_proceed
        )
        rule_scores = self._build_rule_scores(extraction_judgment)

        return MetadataPipelineJudgeOutput(
            session_id=session_id,
            overall_verdict=overall_verdict,
            overall_score=overall_score,
            overall_summary=bsa_summary,
            can_proceed=can_proceed,
            step_judgments=step_judgments,
            recommendations=extraction_judgment.recommendations[:6],
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
        extraction_judgment: MetadataStepJudgment,
        overall_verdict: str,
        overall_score: float,
        can_proceed: bool,
    ) -> dict[str, Any]:
        det = extraction_judgment.details or {}
        return {
            "overall_verdict": overall_verdict,
            "overall_score": overall_score,
            "can_proceed": can_proceed,
            "step_scores": {"extraction": extraction_judgment.score},
            "step_verdicts": {"extraction": extraction_judgment.verdict},
            "kpis": {
                "layout_coverage_rate":         det.get("layout_coverage_rate"),
                "layout_column_count":          det.get("layout_column_count"),
                "attribute_count":              det.get("attribute_count"),
                "dropped_column_count":         det.get("dropped_column_count"),
                "attribute_completeness_rate":  det.get("attribute_completeness_rate"),
                "missing_required_key_count":   det.get("missing_keys_finding_count"),
                "null_attribute_name_count":    det.get("null_attribute_name_count"),
                "filespecs_key_count":          det.get("filespecs_key_count"),
                "filespecs_populated":          det.get("filespecs_populated"),
                "missing_header_field_count":   det.get("missing_header_field_count"),
                "bad_nullability_count":        det.get("bad_nullability_count"),
                "count_mismatch":               det.get("count_mismatch"),
            },
            "blocking_steps": ["extraction"] if extraction_judgment.verdict == "BLOCK" else [],
            "warning_steps":  ["extraction"] if extraction_judgment.verdict == "WARN"  else [],
            "blocking_count": 1 if extraction_judgment.verdict == "BLOCK" else 0,
            "warning_count":  1 if extraction_judgment.verdict == "WARN"  else 0,
        }

    @staticmethod
    def _build_rule_scores(extraction_judgment: MetadataStepJudgment) -> list[dict[str, Any]]:
        """Synthesize H1-style RuleScore entries from the deterministic check details."""
        det = extraction_judgment.details or {}

        def _v(fail_cond: bool, warn_cond: bool = False) -> str:
            if fail_cond:
                return "FAIL"
            if warn_cond:
                return "WARN"
            return "PASS"

        rules: list[dict[str, Any]] = []

        # R1 — Layout coverage
        coverage = det.get("layout_coverage_rate", 1.0) or 0.0
        dropped = det.get("dropped_column_count", 0) or 0
        rules.append({
            "rule_id": "R1_LAYOUT_COVERAGE",
            "rule_name": "Every layout column has a corresponding attribute",
            "step": "extraction",
            "verdict": _v(dropped > 0),
            "score": round(coverage, 4),
            "weight": 0.30,
            "evidence": (
                f"{dropped} of {det.get('layout_column_count', 0)} layout column(s) "
                "missing from extracted attributes."
            ),
            "blocking": dropped > 0,
            "recommendations": (
                ["Re-run metadata_extractor_agent — every layout column must produce an attribute."]
                if dropped > 0 else []
            ),
        })

        # R2 — Attribute completeness (12 required keys)
        missing_keys = det.get("missing_keys_finding_count", 0) or 0
        completeness = det.get("attribute_completeness_rate", 1.0) or 0.0
        rules.append({
            "rule_id": "R2_ATTRIBUTE_COMPLETENESS",
            "rule_name": "All 12 required keys present per attribute",
            "step": "extraction",
            "verdict": _v(missing_keys > 0),
            "score": round(completeness, 4),
            "weight": 0.20,
            "evidence": f"{missing_keys} attribute(s) missing one or more required keys.",
            "blocking": missing_keys > 0,
            "recommendations": [],
        })

        # R3 — No null Attribute Names
        null_names = det.get("null_attribute_name_count", 0) or 0
        rules.append({
            "rule_id": "R3_NO_NULL_ATTRIBUTE_NAMES",
            "rule_name": "Attribute Name is never null/empty",
            "step": "extraction",
            "verdict": _v(null_names > 0),
            "score": 0.0 if null_names > 0 else 1.0,
            "weight": 0.15,
            "evidence": f"{null_names} attribute(s) with null Attribute Name.",
            "blocking": null_names > 0,
            "recommendations": [],
        })

        # R4 — Filespecs populated
        filespecs_populated = det.get("filespecs_populated", True)
        filespecs_count = det.get("filespecs_key_count", 0) or 0
        rules.append({
            "rule_id": "R4_FILESPECS_POPULATED",
            "rule_name": "filespecs dict contains file-level metadata",
            "step": "extraction",
            "verdict": _v(not filespecs_populated),
            "score": 0.0 if not filespecs_populated else 1.0,
            "weight": 0.10,
            "evidence": f"filespecs has {filespecs_count} key(s).",
            "blocking": not filespecs_populated,
            "recommendations": [],
        })

        # R5 — Header fields present
        missing_header = det.get("missing_header_field_count", 0) or 0
        rules.append({
            "rule_id": "R5_HEADER_FIELDS",
            "rule_name": "file1 header fields populated (entity_type, physical/business name, description)",
            "step": "extraction",
            "verdict": _v(False, missing_header > 0),
            "score": 1.0 if missing_header == 0 else max(0.0, 1.0 - 0.25 * missing_header),
            "weight": 0.10,
            "evidence": f"{missing_header} header field(s) missing.",
            "blocking": False,
            "recommendations": [],
        })

        # R6 — Nullability format
        bad_null = det.get("bad_nullability_count", 0) or 0
        rules.append({
            "rule_id": "R6_NULLABILITY_FORMAT",
            "rule_name": "Nullability is 'NOT NULL' or 'NULLABLE'",
            "step": "extraction",
            "verdict": _v(False, bad_null > 0),
            "score": 1.0 if bad_null == 0 else max(0.0, 1.0 - 0.1 * bad_null),
            "weight": 0.10,
            "evidence": f"{bad_null} attribute(s) with non-standard Nullability value.",
            "blocking": False,
            "recommendations": [],
        })

        # R7 — Count consistency
        mismatch = bool(det.get("count_mismatch"))
        rules.append({
            "rule_id": "R7_COUNT_CONSISTENCY",
            "rule_name": "Attribute count >= layout column count",
            "step": "extraction",
            "verdict": _v(False, mismatch),
            "score": 0.5 if mismatch else 1.0,
            "weight": 0.05,
            "evidence": (
                f"attribute_count={det.get('attribute_count', 0)}, "
                f"layout_column_count={det.get('layout_column_count', 0)}."
            ),
            "blocking": False,
            "recommendations": [],
        })

        return rules

    # ------------------------------------------------------------------
    # Extraction judgment
    # ------------------------------------------------------------------

    async def _judge_extraction(
        self, judge_input: MetadataPipelineJudgeInput
    ) -> MetadataStepJudgment:
        det = _check_extraction(judge_input.extracted_metadata, judge_input.layout_columns)

        # Fast-path BLOCK: empty attributes, dropped columns, or null Attribute Names
        if det["is_blocking"]:
            return MetadataStepJudgment(
                step="extraction",
                verdict="BLOCK",
                score=0.0,
                summary=(
                    "Extraction output is incomplete — missing attributes, empty filespecs, "
                    "or layout columns not represented."
                ),
                findings=det["findings"][:10],
                recommendations=[
                    "Re-run metadata_extractor_agent. Every layout column must produce an attribute entry.",
                    "Ensure filespecs contains all required file-level metadata fields.",
                ],
                details=det,
            )

        brd = judge_input.brd_context
        filespecs, file1 = _resolve_extractor_payload(judge_input.extracted_metadata)
        attributes = file1.get("attributes") or []
        file1_header = {k: v for k, v in file1.items() if k != "attributes"}

        llm_response = await self.llm_call(
            STEP2_EXTRACTION_JUDGE_PROMPT.format(
                in_scope=brd.in_scope or "(not provided)",
                out_of_scope=brd.out_of_scope or "(not provided)",
                requirements=brd.requirements or "(not provided)",
                layout_columns_json=json.dumps(
                    judge_input.layout_columns[:30], indent=2, default=str
                ),
                filespecs_json=json.dumps(filespecs, indent=2, default=str),
                file1_header_json=json.dumps(file1_header, indent=2, default=str),
                attributes_json=json.dumps(attributes[:30], indent=2, default=str),
                attribute_count=len(attributes),
                layout_count=det["layout_column_count"],
                deterministic_findings="\n".join(det["findings"]) or "None",
            )
        )

        verdict = self._extract_verdict(llm_response)
        score = self._extract_score(llm_response, verdict)

        if det["count_mismatch"]:
            verdict = _max_verdict(verdict, "WARN")
            score = min(score, 0.75)
        if det["missing_header_fields"]:
            verdict = _max_verdict(verdict, "WARN")
            score = min(score, 0.80)
        if det["bad_nullability"]:
            verdict = _max_verdict(verdict, "WARN")
            score = min(score, 0.80)

        findings = _merge_findings(det["findings"], list(llm_response.get("findings") or []))

        return MetadataStepJudgment(
            step="extraction",
            verdict=verdict,
            score=round(max(0.0, min(1.0, score)), 4),
            summary=_safe_str(llm_response.get("summary")),
            findings=findings[:10],
            recommendations=list(llm_response.get("recommendations") or [])[:8],
            details=det,
        )

    # ------------------------------------------------------------------
    # BSA summary
    # ------------------------------------------------------------------

    async def _build_bsa_summary(
        self,
        extraction_judgment: MetadataStepJudgment,
        overall_verdict: str,
        overall_score: float,
        can_proceed: bool,
        all_findings: list[str],
    ) -> tuple[str, str]:
        try:
            response = await self.llm_call(
                METADATA_OVERALL_SUMMARY_PROMPT.format(
                    extraction_verdict=extraction_judgment.verdict,
                    extraction_score=extraction_judgment.score,
                    overall_verdict=overall_verdict,
                    overall_score=overall_score,
                    can_proceed=can_proceed,
                    all_findings="\n".join(f"- {f}" for f in all_findings) or "None",
                )
            )
            return _safe_str(response.get("summary")), _safe_str(response.get("bsa_note"))
        except Exception:
            return (
                f"Metadata extraction scored {overall_score:.2f} ({overall_verdict}).",
                "Review extraction findings for details.",
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

def _max_verdict(a: str, b: str) -> str:
    order = {"PASS": 0, "WARN": 1, "BLOCK": 2}
    return a if order.get(a.upper(), 0) >= order.get(b.upper(), 0) else b


def _merge_findings(deterministic: list[str], llm: list[str]) -> list[str]:
    """Deterministic findings are ground truth — LLM additions appended after."""
    seen = set(deterministic)
    merged = list(deterministic)
    for f in llm:
        if f not in seen:
            merged.append(f)
            seen.add(f)
    return merged
