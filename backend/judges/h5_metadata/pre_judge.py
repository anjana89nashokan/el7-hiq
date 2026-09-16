from __future__ import annotations

import copy
import json
import time

try:
    import structlog
except Exception:  # pragma: no cover
    import logging

    class _StructlogShim:
        @staticmethod
        def get_logger():
            return logging.getLogger(__name__)

    structlog = _StructlogShim()

try:  # pragma: no cover
    from google.adk.agents import Agent as AdkAgent
except Exception:  # pragma: no cover
    from google.adk.agents import LlmAgent as AdkAgent  # type: ignore

from google.adk.tools import FunctionTool

from config.settings import config
from judges.base_judge import AuditEventType, BaseJudge, audit_trail
from judges.h5_metadata.naming_checker import (
    analyze_cast_safety,
    check_all_attribute_names,
    check_duplicate_attribute_names,
    check_file_name,
    check_position_sequence,
)
from judges.h5_metadata.rules import (
    rule_r1_naming_conformance,
    rule_r2_type_safety,
    rule_r3_template_schema_validity,
    rule_r4_completeness,
    rule_r5_round_trip_consistency,
    rule_r6_agent_score_calibration,
)
from judges.h5_metadata.schema_validator import validate_indimap_template
from judges.h5_metadata.schemas import JudgeInputH5, JudgeOutputH5
from models.judge import JudgeEvaluation, JudgeVerdict, RuleScore, RuleVerdict

logger = structlog.get_logger()


_OVERALL_WEIGHTS = {
    "R1_NAMING_CONFORMANCE": 0.20,
    "R2_TYPE_SAFETY": 0.25,
    "R3_TEMPLATE_SCHEMA": 0.20,
    "R4_COMPLETENESS": 0.25,
    "R5_ROUND_TRIP": 0.10,
}


def _safe_score(rule_scores: list[RuleScore], rule_id: str) -> float:
    for rule in rule_scores:
        if rule.rule_id == rule_id:
            return rule.score
    return 0.0


class PreJudgeH5(BaseJudge):
    """Pre-checkpoint judge for H5 — Metadata Generation Layer."""

    async def evaluate(self, judge_input: JudgeInputH5) -> JudgeOutputH5:
        started_at = time.perf_counter()
        audit_trail.record(
            AuditEventType.AGENT_INVOKED,
            judge_input.session_id,
            agent="PreJudgeH5",
            revision_number=judge_input.revision_number,
        )

        deterministic_analysis = self._build_deterministic_analysis(judge_input)
        rule_scores: list[RuleScore] = []
        recommendation = "Forward the annotated metadata to the BSA for H5 review."

        # R3 first — schema validity
        r3 = await rule_r3_template_schema_validity(
            judge_input, self.llm_call, deterministic_analysis
        )
        rule_scores.append(r3)

        json_parse_blocked = (
            r3.verdict == RuleVerdict.FAIL
            and r3.score == 0.0
            and r3.evidence.startswith("INVALID JSON")
        )

        if not json_parse_blocked:
            r4 = await rule_r4_completeness(
                judge_input, self.llm_call, deterministic_analysis
            )
            rule_scores.append(r4)

            r1 = await rule_r1_naming_conformance(
                judge_input, self.llm_call, deterministic_analysis
            )
            rule_scores.append(r1)

            r2 = await rule_r2_type_safety(
                judge_input, self.llm_call, deterministic_analysis
            )
            rule_scores.append(r2)

            r5 = await rule_r5_round_trip_consistency(
                judge_input, self.llm_call, deterministic_analysis
            )
            rule_scores.append(r5)

            r6 = await rule_r6_agent_score_calibration(
                judge_input,
                self.llm_call,
                judge_naming_score=_safe_score(rule_scores, "R1_NAMING_CONFORMANCE"),
                judge_type_score=_safe_score(rule_scores, "R2_TYPE_SAFETY"),
                judge_completeness_score=_safe_score(rule_scores, "R4_COMPLETENESS"),
            )
            rule_scores.append(r6)

        overall_score = self._compute_weighted_score(rule_scores)
        blocking_rules = [
            r.rule_id for r in rule_scores if r.blocking and r.verdict == RuleVerdict.FAIL
        ]
        warnings = [r.rule_id for r in rule_scores if r.verdict == RuleVerdict.WARN]

        if json_parse_blocked:
            verdict = JudgeVerdict.BLOCK
            recommendation = (
                "Return to the MetadataBuilder — the IndiMap template is invalid JSON and cannot be evaluated."
            )
        elif blocking_rules:
            verdict = JudgeVerdict.BLOCK
            recommendation = (
                "Return to the MetadataBuilder — blocking judge findings must be resolved before BSA review."
            )
        elif warnings:
            verdict = JudgeVerdict.WARN
            recommendation = (
                "Forward to BSA with quality scorecard and judge annotations highlighting concerns."
            )
        else:
            verdict = JudgeVerdict.PASS

        # Auto-corrected output (only when naming is the only concern)
        auto_corrected_output = self._build_auto_corrected_output(
            judge_input, rule_scores, verdict
        )

        # Reopen H4 detection
        reopen_h4_required = any(
            "REOPEN_H4_REQUIRED" in (rec or "")
            for r in rule_scores
            for rec in r.recommendations
        )

        if reopen_h4_required:
            verdict = JudgeVerdict.BLOCK
            recommendation = (
                "ESCALATE to senior BSA: a NO MATCH field was promoted at H5 — H4 must be re-reviewed."
            )

        if auto_corrected_output and verdict == JudgeVerdict.BLOCK:
            # Auto-correction can soften a naming-only block to WARN
            naming_only_block = blocking_rules == ["R1_NAMING_CONFORMANCE"]
            if naming_only_block:
                verdict = JudgeVerdict.WARN
                recommendation = (
                    "Auto-corrections available — BSA may approve the auto-corrected output directly."
                )

        quality_scorecard = self._build_quality_scorecard(
            rule_scores, overall_score, blocking_rules, judge_input
        )

        annotated_metadata = self._build_annotated_metadata(
            judge_input.metadata_output.model_dump(mode="json"),
            rule_scores,
            quality_scorecard,
        )

        bsa_review_summary = self._build_bsa_review_summary(
            verdict=verdict,
            rule_scores=rule_scores,
            overall_score=overall_score,
            reopen_h4_required=reopen_h4_required,
            auto_corrected_available=bool(auto_corrected_output),
        )

        evaluation = JudgeEvaluation(
            session_id=judge_input.session_id,
            phase="metadata",
            checkpoint="H5",
            judge_mode="pre",
            verdict=verdict,
            overall_score=overall_score,
            rule_scores=rule_scores,
            blocking_rules=blocking_rules,
            warnings=warnings,
            summary=self._build_evaluation_summary(verdict, rule_scores, overall_score),
            recommendation=recommendation,
            judge_model=self.model_name,
            evaluation_latency_ms=int((time.perf_counter() - started_at) * 1000),
        )

        audit_trail.record(
            AuditEventType.AGENT_COMPLETED,
            judge_input.session_id,
            agent="PreJudgeH5",
            verdict=evaluation.verdict.value,
            overall_score=evaluation.overall_score,
        )

        return JudgeOutputH5(
            evaluation=evaluation,
            revision_directive=None,
            annotated_metadata=annotated_metadata,
            bsa_review_summary=bsa_review_summary,
            quality_scorecard=quality_scorecard,
            auto_corrected_output=auto_corrected_output or {},
        )

    def _build_deterministic_analysis(self, judge_input: JudgeInputH5) -> dict:
        attributes_dump = [a.model_dump() for a in judge_input.metadata_output.attributes]
        try:
            parsed_template = json.loads(judge_input.metadata_output.indimap_template_json or "{}")
        except json.JSONDecodeError:
            parsed_template = None

        schema_result = (
            validate_indimap_template(parsed_template)
            if isinstance(parsed_template, dict)
            else None
        )

        casts_analysis = []
        for cast in judge_input.metadata_output.type_casts_applied or []:
            issue = analyze_cast_safety(
                source_type=str(cast.get("source_type") or ""),
                target_type=str(cast.get("target_type") or ""),
                source_precision=cast.get("source_precision"),
                target_precision=cast.get("target_precision"),
                source_scale=cast.get("source_scale"),
                target_scale=cast.get("target_scale"),
                cast_expression=cast.get("cast_expression"),
                attribute_name=str(cast.get("attribute_name") or ""),
            )
            casts_analysis.append(
                {
                    "cast": cast,
                    "issue": issue.__dict__ if issue else None,
                }
            )

        return {
            "file_name_violations": [v.__dict__ for v in check_file_name(
                judge_input.metadata_output.file_metadata.file_name
            )],
            "attribute_violations": [v.__dict__ for v in check_all_attribute_names(attributes_dump)],
            "duplicate_attribute_names": check_duplicate_attribute_names(attributes_dump),
            "position_issues": check_position_sequence(attributes_dump),
            "schema_validation": (
                {
                    "is_valid": schema_result.is_valid,
                    "block_count": schema_result.block_count,
                    "warn_count": schema_result.warn_count,
                    "errors": [e.__dict__ for e in schema_result.errors],
                    "warnings": [w.__dict__ for w in schema_result.warnings],
                    "attributes_validated": schema_result.attributes_validated,
                    "file_fields_validated": schema_result.file_fields_validated,
                }
                if schema_result is not None
                else {"is_valid": False, "errors": ["template not parseable"]}
            ),
            "cast_analysis": casts_analysis,
        }

    def _compute_weighted_score(self, rule_scores: list[RuleScore]) -> float:
        if not rule_scores:
            return 0.0
        total_weight = 0.0
        weighted_sum = 0.0
        for rule in rule_scores:
            weight = _OVERALL_WEIGHTS.get(rule.rule_id, 0.0)
            total_weight += weight
            weighted_sum += rule.score * weight
        if total_weight <= 0:
            return round(sum(r.score for r in rule_scores) / max(len(rule_scores), 1), 4)
        return round(weighted_sum / total_weight, 4)

    def _build_quality_scorecard(
        self,
        rule_scores: list[RuleScore],
        overall_score: float,
        blocking_rules: list[str],
        judge_input: JudgeInputH5,
    ) -> dict:
        scorecard = {
            "naming_conformance_score": _safe_score(rule_scores, "R1_NAMING_CONFORMANCE"),
            "type_conformance_score": _safe_score(rule_scores, "R2_TYPE_SAFETY"),
            "completeness_score": _safe_score(rule_scores, "R4_COMPLETENESS"),
            "template_validity_score": _safe_score(rule_scores, "R3_TEMPLATE_SCHEMA"),
            "round_trip_score": _safe_score(rule_scores, "R5_ROUND_TRIP"),
            "overall_score": overall_score,
            "blocking_issues": blocking_rules,
        }

        # Auto-correctable / manual review counts
        auto_correctable_count = 0
        manual_review_count = 0
        for rule in rule_scores:
            if rule.rule_id == "R1_NAMING_CONFORMANCE":
                # Use the agent's recorded numbers as a starting point
                auto_correctable_count += len(judge_input.metadata_output.naming_auto_corrections or [])
                manual_review_count += len(judge_input.metadata_output.naming_manual_flags or [])
            if rule.verdict in {RuleVerdict.FAIL, RuleVerdict.WARN}:
                manual_review_count += len(rule.recommendations)
        scorecard["auto_correctable_count"] = auto_correctable_count
        scorecard["manual_review_count"] = manual_review_count

        # Score verification flags from R6
        r6 = next((r for r in rule_scores if r.rule_id == "R6_SCORE_CALIBRATION"), None)
        scorecard["score_verified"] = {
            "naming": r6 is not None and r6.verdict != RuleVerdict.FAIL,
            "type": r6 is not None and r6.verdict != RuleVerdict.FAIL,
            "completeness": r6 is not None and r6.verdict != RuleVerdict.FAIL,
        }
        return scorecard

    def _build_auto_corrected_output(
        self,
        judge_input: JudgeInputH5,
        rule_scores: list[RuleScore],
        current_verdict: JudgeVerdict,
    ) -> dict | None:
        # Only consider auto-corrections when R1 has issues but other critical rules are OK
        attributes_dump = [a.model_dump() for a in judge_input.metadata_output.attributes]
        violations = check_all_attribute_names(attributes_dump)
        violations += check_file_name(judge_input.metadata_output.file_metadata.file_name)
        if not violations:
            return None
        if not all(v.auto_correctable for v in violations):
            return None

        critical_rules_blocking = [
            r.rule_id
            for r in rule_scores
            if r.rule_id in {"R2_TYPE_SAFETY", "R3_TEMPLATE_SCHEMA", "R4_COMPLETENESS", "R5_ROUND_TRIP"}
            and r.verdict == RuleVerdict.FAIL
        ]
        if critical_rules_blocking:
            return None

        corrected = copy.deepcopy(judge_input.metadata_output.model_dump(mode="json"))
        # Apply file-name correction
        file_corrections = [
            v for v in violations if v.field_path.startswith("file_metadata.")
        ]
        for violation in file_corrections:
            if violation.suggested_correction:
                corrected.setdefault("file_metadata", {})["file_name"] = violation.suggested_correction
                break

        # Apply attribute corrections
        path_to_correction = {
            v.field_path: v.suggested_correction
            for v in violations
            if v.field_path.startswith("attributes[") and v.suggested_correction
        }
        for attribute in corrected.get("attributes", []):
            position = attribute.get("position")
            path = f"attributes[{position}].name"
            if path in path_to_correction:
                attribute["name"] = path_to_correction[path]

        return {
            "metadata_output": corrected,
            "applied_corrections": [
                {
                    "field_path": v.field_path,
                    "original": v.field_name,
                    "corrected": v.suggested_correction,
                    "violation_type": v.violation_type.value,
                }
                for v in violations
                if v.suggested_correction
            ],
        }

    def _build_annotated_metadata(
        self,
        metadata_output: dict,
        rule_scores: list[RuleScore],
        quality_scorecard: dict,
    ) -> dict:
        annotated = copy.deepcopy(metadata_output)
        rule_by_id = {r.rule_id: r for r in rule_scores}

        annotated["__judge_file__"] = {
            rule_id: {
                "verdict": rule_by_id[rule_id].verdict.value,
                "score": rule_by_id[rule_id].score,
                "evidence": rule_by_id[rule_id].evidence,
            }
            for rule_id in ("R3_TEMPLATE_SCHEMA", "R4_COMPLETENESS")
            if rule_id in rule_by_id
        }

        for attribute in annotated.get("attributes", []):
            judge_block: dict = {}
            for rule_id in (
                "R1_NAMING_CONFORMANCE",
                "R2_TYPE_SAFETY",
                "R4_COMPLETENESS",
                "R5_ROUND_TRIP",
            ):
                rule = rule_by_id.get(rule_id)
                if rule is None:
                    continue
                judge_block[rule_id] = {
                    "verdict": rule.verdict.value,
                    "score": rule.score,
                }
            attribute["__judge__"] = judge_block

        annotated["__judge_scorecard__"] = quality_scorecard
        return annotated

    @staticmethod
    def _build_evaluation_summary(
        verdict: JudgeVerdict, rule_scores: list[RuleScore], overall_score: float
    ) -> str:
        failed = [r.rule_name for r in rule_scores if r.verdict == RuleVerdict.FAIL]
        warned = [r.rule_name for r in rule_scores if r.verdict == RuleVerdict.WARN]
        if verdict == JudgeVerdict.BLOCK:
            return (
                f"Pre-judge H5 blocked the metadata at score {overall_score:.2f}. "
                f"Blocking concerns in {', '.join(failed) if failed else 'evaluated rules'}."
            )
        if warned:
            return (
                f"Pre-judge H5 returned WARN at score {overall_score:.2f}. "
                f"Most checks passed; follow-up needed on {', '.join(warned)}."
            )
        return (
            f"Pre-judge H5 passed at score {overall_score:.2f}. "
            "Metadata is ready for BSA review."
        )

    def _build_bsa_review_summary(
        self,
        verdict: JudgeVerdict,
        rule_scores: list[RuleScore],
        overall_score: float,
        reopen_h4_required: bool,
        auto_corrected_available: bool,
    ) -> str:
        sentences: list[str] = []
        if reopen_h4_required:
            sentences.append(
                "ESCALATION: a field that was NO MATCH at H4 has been mapped at H5 — H4 must be re-reviewed before this extract can proceed."
            )

        sentences.append(
            f"The metadata scored {overall_score:.0%} overall with a {verdict.value.upper()} verdict from the judge."
        )

        # Concrete dimension callouts
        r1 = next((r for r in rule_scores if r.rule_id == "R1_NAMING_CONFORMANCE"), None)
        r2 = next((r for r in rule_scores if r.rule_id == "R2_TYPE_SAFETY"), None)
        r3 = next((r for r in rule_scores if r.rule_id == "R3_TEMPLATE_SCHEMA"), None)
        r4 = next((r for r in rule_scores if r.rule_id == "R4_COMPLETENESS"), None)

        callouts: list[str] = []
        if r1 and r1.verdict != RuleVerdict.PASS:
            naming_pct = int(r1.score * 100)
            callouts.append(f"{naming_pct}% of field names conform to enterprise naming standards")
        if r2 and r2.verdict == RuleVerdict.FAIL:
            callouts.append("a data type or unsafe cast needs correction")
        if r3 and r3.verdict == RuleVerdict.FAIL:
            callouts.append("the IndiMap template has structural errors and cannot be registered")
        if r4 and r4.verdict == RuleVerdict.FAIL:
            callouts.append("one or more fields from the original layout are missing or unmapped")
        if callouts:
            sentences.append("Findings: " + "; ".join(callouts) + ".")

        if auto_corrected_available:
            sentences.append(
                "Auto-corrections are available for all naming issues — you may apply them with one click and approve the corrected version."
            )

        if verdict == JudgeVerdict.BLOCK:
            sentences.append(
                "The metadata has been returned to the agent and will not reach your review queue until corrected."
            )
        else:
            sentences.append(
                "Approving will upload the Final Extract Specification to IndiMap and hand off to data engineering for implementation."
            )

        return " ".join(sentences[:6])


async def run_pre_judge_h5(
    session_id: str,
    metadata_output_json: str,
    h4_mapping_json: str,
    layout_fields_json: str,
    revision_number: int = 0,
) -> dict:
    judge = PreJudgeH5()
    judge_input = JudgeInputH5(
        session_id=session_id,
        metadata_output=json.loads(metadata_output_json),
        h4_mapping_spec=json.loads(h4_mapping_json),
        original_layout_fields=json.loads(layout_fields_json),
        revision_number=revision_number,
    )
    result = await judge.evaluate(judge_input)
    return result.model_dump(mode="json")


run_pre_judge_h5_tool = FunctionTool(func=run_pre_judge_h5)

pre_judge_h5_agent = AdkAgent(
    name="PreJudgeH5",
    model=getattr(config, "GEMINI_MODEL", None) or getattr(config, "AGENT_MODEL", "gemini-2.5-pro"),
    description=(
        "Pre-checkpoint judge for the Metadata Generation Layer (H5). "
        "Evaluates metadata for naming conformance, type safety, schema validity, "
        "completeness, round-trip fidelity, and score accuracy. Final quality gate "
        "before the Extract Specification is assembled."
    ),
    instruction=(
        "You are the Pre-Judge for H5 — Metadata Generation Layer. "
        "When invoked, call the run_pre_judge_h5 tool with the provided parameters "
        "and return the tool output exactly."
    ),
    tools=[run_pre_judge_h5_tool],
)
