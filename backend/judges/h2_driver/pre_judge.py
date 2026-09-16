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
from judges.h2_driver.rules import (
    rule_r1_brd_traceability,
    rule_r2_no_transformation_leakage,
    rule_r3_standard_field_compliance,
    rule_r4_logical_consistency,
    rule_r5_operator_direction,
    rule_r6_population_coverage,
    rule_r7_fyi_usage,
)
from judges.h2_driver.schemas import JudgeInputH2, JudgeOutputH2
from judges.h2_driver.sql_analyzer import build_sql_analysis_report
from models.judge import JudgeEvaluation, JudgeVerdict, RuleScore, RuleVerdict

logger = structlog.get_logger()


class PreJudgeH2(BaseJudge):
    """Pre-checkpoint judge for H2 — Driver Generation Layer."""

    async def evaluate(self, judge_input: JudgeInputH2) -> JudgeOutputH2:
        if not judge_input.standards_dictionary:
            raise ValueError(
                "standards_dictionary is required for H2 judge — cannot evaluate R3."
            )

        started_at = time.perf_counter()
        audit_trail.record(
            AuditEventType.AGENT_INVOKED,
            judge_input.session_id,
            agent="PreJudgeH2",
            revision_number=judge_input.revision_number,
        )

        sql_analysis_report = build_sql_analysis_report(
            judge_input.driver_criteria.where_clause or ""
        )

        rule_scores: list[RuleScore] = []
        recommendation = "Forward the annotated DriverCriteria to the BSA for H2 review."

        # R2 first (deterministic, short-circuits on transformation leakage)
        r2 = await rule_r2_no_transformation_leakage(judge_input, self.llm_call)
        rule_scores.append(r2)

        if r2.blocking and r2.verdict == RuleVerdict.FAIL:
            overall_score = 0.0
            verdict = JudgeVerdict.BLOCK
            recommendation = (
                "Return to the DriverGenerator immediately — transformation expressions must be removed before BSA review."
            )
        else:
            # R3 second
            r3 = await rule_r3_standard_field_compliance(judge_input, self.llm_call)
            rule_scores.append(r3)
            unmapped_blocking = r3.blocking and r3.verdict == RuleVerdict.FAIL
            if unmapped_blocking:
                overall_score, verdict = self.aggregate_scores(rule_scores)
                recommendation = (
                    "Return to the DriverGenerator — non-standard or unmapped fields must be resolved before BSA review."
                )
            else:
                # Run remaining rules
                for rule in (
                    rule_r1_brd_traceability,
                    rule_r4_logical_consistency,
                    rule_r5_operator_direction,
                    rule_r6_population_coverage,
                    rule_r7_fyi_usage,
                ):
                    rule_scores.append(await rule(judge_input, self.llm_call))
                overall_score, verdict = self.aggregate_scores(rule_scores)
                if verdict == JudgeVerdict.BLOCK:
                    recommendation = (
                        "Return to the DriverGenerator and resolve blocking judge findings before BSA review."
                    )
                elif verdict == JudgeVerdict.WARN:
                    recommendation = (
                        "Forward to the BSA with judge annotations and highlighted concerns."
                    )

        if len(rule_scores) <= 2:
            overall_score, verdict = self.aggregate_scores(rule_scores)

        warnings = [r.rule_id for r in rule_scores if r.verdict == RuleVerdict.WARN]
        blocking_rules = [
            r.rule_id for r in rule_scores if r.blocking and r.verdict == RuleVerdict.FAIL
        ]
        if "R7_FYI_USAGE" in blocking_rules:
            audit_trail.record(
                AuditEventType.AGENT_INVOKED,
                judge_input.session_id,
                agent="DriverGenerator",
                error="R7_FYI_USAGE_VIOLATION",
            )

        summary = self._build_evaluation_summary(verdict, rule_scores, overall_score)
        evaluation = JudgeEvaluation(
            session_id=judge_input.session_id,
            phase="driver",
            checkpoint="H2",
            judge_mode="pre",
            verdict=verdict,
            overall_score=overall_score,
            rule_scores=rule_scores,
            blocking_rules=blocking_rules,
            warnings=warnings,
            summary=summary,
            recommendation=recommendation,
            judge_model=self.model_name,
            evaluation_latency_ms=int((time.perf_counter() - started_at) * 1000),
        )
        annotated_driver = self._build_annotated_driver(
            judge_input.driver_criteria.model_dump(mode="json"),
            rule_scores,
            sql_analysis_report,
            verdict,
            overall_score,
        )
        bsa_review_summary = self._build_bsa_review_summary(
            verdict=verdict,
            rule_scores=rule_scores,
            overall_score=overall_score,
            standards_dictionary=judge_input.standards_dictionary,
        )

        output = JudgeOutputH2(
            evaluation=evaluation,
            revision_directive=None,
            annotated_driver=annotated_driver,
            bsa_review_summary=bsa_review_summary,
            sql_analysis_report=sql_analysis_report,
        )
        audit_trail.record(
            AuditEventType.AGENT_COMPLETED,
            judge_input.session_id,
            agent="PreJudgeH2",
            verdict=evaluation.verdict.value,
            overall_score=evaluation.overall_score,
        )
        return output

    @staticmethod
    def _build_evaluation_summary(
        verdict: JudgeVerdict, rule_scores: list[RuleScore], overall_score: float
    ) -> str:
        failed = [r.rule_name for r in rule_scores if r.verdict == RuleVerdict.FAIL]
        warned = [r.rule_name for r in rule_scores if r.verdict == RuleVerdict.WARN]
        if verdict == JudgeVerdict.BLOCK:
            return (
                f"Pre-judge H2 blocked the driver at score {overall_score:.2f}. "
                f"Blocking concerns in {', '.join(failed) if failed else 'evaluated rules'}."
            )
        if warned:
            return (
                f"Pre-judge H2 returned WARN at score {overall_score:.2f}. "
                f"Most checks passed; follow-up needed on {', '.join(warned)}."
            )
        return (
            f"Pre-judge H2 passed at score {overall_score:.2f}. "
            "DriverCriteria is ready for BSA review."
        )

    def _build_annotated_driver(
        self,
        driver_criteria: dict,
        rule_scores: list[RuleScore],
        sql_analysis_report: dict,
        verdict: JudgeVerdict,
        overall_score: float,
    ) -> dict:
        annotated = copy.deepcopy(driver_criteria)
        rule_by_id = {r.rule_id: r for r in rule_scores}

        annotated_predicates: list[dict] = []
        for predicate in annotated.get("predicates", []) or []:
            predicate_copy = dict(predicate)
            judge_block: dict[str, dict] = {}
            for rule_id, rule_score in rule_by_id.items():
                judge_block[rule_id] = {
                    "verdict": rule_score.verdict.value,
                    "score": rule_score.score,
                    "evidence": rule_score.evidence,
                }
            predicate_copy["__judge__"] = judge_block
            annotated_predicates.append(predicate_copy)
        if annotated_predicates:
            annotated["predicates"] = annotated_predicates

        annotated["__judge_summary__"] = {
            "overall_verdict": verdict.value,
            "overall_score": overall_score,
            "rule_scores": [r.model_dump(mode="json") for r in rule_scores],
            "blocking_rules": [
                r.rule_id for r in rule_scores if r.blocking and r.verdict == RuleVerdict.FAIL
            ],
        }
        annotated["__sql_analysis_report__"] = sql_analysis_report
        return annotated

    def _build_bsa_review_summary(
        self,
        verdict: JudgeVerdict,
        rule_scores: list[RuleScore],
        overall_score: float,
        standards_dictionary: dict,
    ) -> str:
        # Reverse standards_dictionary for SQL→business term substitution
        reverse_map = {v.upper(): k for k, v in (standards_dictionary or {}).items()}

        def humanize(rule: RuleScore) -> str:
            if rule.rule_id == "R5_OPERATOR_DIRECTION":
                return "population direction (include/exclude)"
            if rule.rule_id == "R2_NO_TRANSFORMATION_LEAKAGE":
                return "data transformations leaking into the population filter"
            if rule.rule_id == "R3_STANDARD_FIELD_COMPLIANCE":
                return "use of approved enterprise field names"
            if rule.rule_id == "R6_POPULATION_COVERAGE":
                return "coverage of all population dimensions"
            if rule.rule_id == "R1_BRD_TRACEABILITY":
                return "traceability of every filter back to the BRD"
            if rule.rule_id == "R4_LOGICAL_CONSISTENCY":
                return "logical consistency of the filters"
            return "FYI value resolution"

        passed = [humanize(r) for r in rule_scores if r.verdict == RuleVerdict.PASS]
        warned = [humanize(r) for r in rule_scores if r.verdict == RuleVerdict.WARN]
        failed = [humanize(r) for r in rule_scores if r.verdict == RuleVerdict.FAIL]

        # If R5 fired, lead with population inversion
        sentences: list[str] = []
        r5 = next((r for r in rule_scores if r.rule_id == "R5_OPERATOR_DIRECTION"), None)
        if r5 and r5.verdict == RuleVerdict.FAIL:
            sentences.append(
                "WARNING: the driver appears to invert the target population — records expected to be "
                "included are being excluded (or vice versa). This is the most consequential type of error."
            )

        sentences.append(
            f"The driver scored {overall_score:.2f} and the judge returned a {verdict.value.upper()} verdict."
        )
        if passed:
            sentences.append(f"Cleanly passing checks: {', '.join(passed)}.")
        if warned:
            sentences.append(f"Please pay attention to {', '.join(warned)} during review.")
        if verdict == JudgeVerdict.BLOCK:
            sentences.append(
                "The driver has been returned to the agent and will not reach your review queue until corrected."
            )
        elif failed:
            sentences.append(f"Highest-risk findings: {', '.join(failed)} — see judge annotations.")
        else:
            sentences.append("No blocking issues — the annotated driver is ready for review.")

        # Substitute any standard field names that leaked into the summary
        text = " ".join(sentences[:6])
        for std, business in reverse_map.items():
            text = text.replace(std, business)
        return text


async def run_pre_judge_h2(
    session_id: str,
    driver_criteria_json: str,
    requirement_model_json: str,
    brd_text: str,
    standards_dictionary_json: str,
    revision_number: int = 0,
) -> dict:
    judge = PreJudgeH2()
    judge_input = JudgeInputH2(
        session_id=session_id,
        driver_criteria=json.loads(driver_criteria_json),
        h1_requirement_model=json.loads(requirement_model_json),
        brd_text=brd_text,
        standards_dictionary=json.loads(standards_dictionary_json),
        revision_number=revision_number,
    )
    result = await judge.evaluate(judge_input)
    return result.model_dump(mode="json")


run_pre_judge_h2_tool = FunctionTool(func=run_pre_judge_h2)

pre_judge_h2_agent = AdkAgent(
    name="PreJudgeH2",
    model=getattr(config, "GEMINI_MODEL", None) or getattr(config, "AGENT_MODEL", "gemini-2.5-pro"),
    description=(
        "Pre-checkpoint judge for the Driver Generation Layer (H2). "
        "Evaluates DriverCriteria SQL logic against seven rules. "
        "BLOCK routes back to DriverGenerator. PASS/WARN forwards to BSA at H2."
    ),
    instruction=(
        "You are the Pre-Judge for H2 — Driver Generation Layer. "
        "When invoked, call the run_pre_judge_h2 tool with the provided parameters "
        "and return the tool output exactly."
    ),
    tools=[run_pre_judge_h2_tool],
)
