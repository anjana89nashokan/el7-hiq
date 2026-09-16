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
            class _ShimLogger:
                def __init__(self):
                    self._logger = logging.getLogger(__name__)

                def _format(self, msg, kwargs):
                    if not kwargs:
                        return msg
                    kw_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
                    return f"{msg} {kw_str}"

                def info(self, msg, *args, **kwargs):
                    self._logger.info(self._format(msg, kwargs), *args)

                def exception(self, msg, *args, **kwargs):
                    self._logger.exception(self._format(msg, kwargs), *args)

                def error(self, msg, *args, **kwargs):
                    self._logger.error(self._format(msg, kwargs), *args)

                def warning(self, msg, *args, **kwargs):
                    self._logger.warning(self._format(msg, kwargs), *args)

                def debug(self, msg, *args, **kwargs):
                    self._logger.debug(self._format(msg, kwargs), *args)

            return _ShimLogger()

    structlog = _StructlogShim()

try:  # pragma: no cover - compatibility across ADK versions
    from google.adk.agents import Agent as AdkAgent
except Exception:  # pragma: no cover
    from google.adk.agents import LlmAgent as AdkAgent  # type: ignore

from google.adk.tools import FunctionTool

from config.settings import config
from judges.base_judge import AuditEventType, BaseJudge, audit_trail
from judges.h1_requirement.rules import (
    rule_r1_completeness,
    rule_r2_no_hallucination,
    rule_r3_ambiguity_coverage,
    rule_r4_scope_boundary,
    rule_r5_transcript_consistency,
    rule_r6_domain_classification,
)
from judges.h1_requirement.schemas import JudgeInputH1, JudgeOutputH1
from models.judge import JudgeEvaluation, JudgeVerdict, RuleScore, RuleVerdict

logger = structlog.get_logger()

ALL_RULES = [
    rule_r1_completeness,
    rule_r2_no_hallucination,
    rule_r3_ambiguity_coverage,
    rule_r4_scope_boundary,
    rule_r5_transcript_consistency,
    rule_r6_domain_classification,
]


class PreJudgeH1(BaseJudge):
    async def evaluate(self, judge_input: JudgeInputH1) -> JudgeOutputH1:
        started_at = time.perf_counter()
        audit_trail.record(
            AuditEventType.AGENT_INVOKED,
            judge_input.session_id,
            agent="PreJudgeH1",
            revision_number=judge_input.revision_number,
        )

        rule_scores: list[RuleScore] = []
        recommendation = "Forward the annotated Requirements Model to the BSA for H1 review."

        r1 = await rule_r1_completeness(judge_input, self.llm_call)
        rule_scores.append(r1)

        if r1.score < 0.30:
            overall_score = round(r1.score, 4)
            verdict = JudgeVerdict.BLOCK
            recommendation = (
                "Return to the RequirementInterpreter immediately — the output is too incomplete to evaluate further."
            )
        else:
            r2 = await rule_r2_no_hallucination(judge_input, self.llm_call)
            rule_scores.append(r2)
            if r2.blocking and r2.verdict == RuleVerdict.FAIL:
                overall_score, verdict = self.aggregate_scores(rule_scores)
                recommendation = (
                    "Return to the RequirementInterpreter immediately — fabricated values must be removed before BSA review."
                )
            else:
                for rule in ALL_RULES[2:]:
                    rule_scores.append(await rule(judge_input, self.llm_call))
                overall_score, verdict = self.aggregate_scores(rule_scores)
                if verdict == JudgeVerdict.BLOCK:
                    recommendation = (
                        "Return to the RequirementInterpreter and resolve the blocking judge findings before BSA review."
                    )
                elif verdict == JudgeVerdict.WARN:
                    recommendation = (
                        "Forward to the BSA with judge annotations and highlighted concerns."
                    )
        if len(rule_scores) == 1:
            overall_score = round(rule_scores[0].score, 4)

        warnings = [rule.rule_id for rule in rule_scores if rule.verdict == RuleVerdict.WARN]
        blocking_rules = [
            rule.rule_id
            for rule in rule_scores
            if rule.blocking and rule.verdict == RuleVerdict.FAIL
        ]
        summary = self._build_evaluation_summary(verdict, rule_scores, overall_score)

        evaluation = JudgeEvaluation(
            session_id=judge_input.session_id,
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
        annotated_artifact = self._build_annotated_artifact(
            judge_input.requirement_model.model_dump(mode="json"), rule_scores
        )
        bsa_review_summary = self._build_bsa_review_summary(
            verdict=verdict,
            rule_scores=rule_scores,
            overall_score=overall_score,
        )
        output = JudgeOutputH1(
            evaluation=evaluation,
            revision_directive=None,
            annotated_artifact=annotated_artifact,
            bsa_review_summary=bsa_review_summary,
        )
        audit_trail.record(
            AuditEventType.AGENT_COMPLETED,
            judge_input.session_id,
            agent="PreJudgeH1",
            verdict=evaluation.verdict.value,
            overall_score=evaluation.overall_score,
        )
        return output

    @staticmethod
    def _build_evaluation_summary(
        verdict: JudgeVerdict, rule_scores: list[RuleScore], overall_score: float
    ) -> str:
        failed = [rule.rule_name for rule in rule_scores if rule.verdict == RuleVerdict.FAIL]
        warned = [rule.rule_name for rule in rule_scores if rule.verdict == RuleVerdict.WARN]
        if verdict == JudgeVerdict.BLOCK:
            return (
                f"Pre-judge H1 blocked the artifact at score {overall_score:.2f}. "
                f"Blocking concerns were found in {', '.join(failed) if failed else 'the evaluated rules'}."
            )
        if warned:
            return (
                f"Pre-judge H1 returned WARN at score {overall_score:.2f}. "
                f"Most checks passed, with follow-up needed on {', '.join(warned)}."
            )
        return (
            f"Pre-judge H1 passed at score {overall_score:.2f}. "
            "The requirements artifact is ready for BSA review."
        )

    def _build_annotated_artifact(
        self, requirement_model: dict, rule_scores: list[RuleScore]
    ) -> dict:
        annotated = copy.deepcopy(requirement_model)
        for rule in rule_scores:
            annotated[f"__judge_{rule.rule_id}__"] = {
                "verdict": rule.verdict.value,
                "score": rule.score,
                "evidence": rule.evidence,
                "citations": rule.citations,
                "blocking": rule.blocking,
                "recommendations": rule.recommendations,
            }
        return annotated

    def _build_bsa_review_summary(
        self, verdict: JudgeVerdict, rule_scores: list[RuleScore], overall_score: float
    ) -> str:
        passed = [rule.rule_name for rule in rule_scores if rule.verdict == RuleVerdict.PASS]
        warned = [rule.rule_name for rule in rule_scores if rule.verdict == RuleVerdict.WARN]
        failed = [rule.rule_name for rule in rule_scores if rule.verdict == RuleVerdict.FAIL]

        sentences = [
            f"The Requirements Model scored {overall_score:.2f} overall and the pre-judge returned a {verdict.value.upper()} verdict.",
            (
                f"The cleanest areas were {', '.join(passed)}."
                if passed
                else "Several core checks ran, but none were fully clean."
            ),
            (
                f"Please pay attention to {', '.join(warned)} during review."
                if warned
                else "No non-blocking concerns were raised by the judge."
            ),
        ]
        if failed:
            sentences.append(
                f"The highest-risk findings were in {', '.join(failed)} and are called out in the judge annotations."
            )
        sentences.append(
            "The annotated artifact is ready for review."
            if verdict != JudgeVerdict.BLOCK
            else "The artifact should return to the RequirementInterpreter before standard BSA review."
        )
        return " ".join(sentences[:5])


async def run_pre_judge_h1(
    session_id: str,
    requirement_model_json: str,
    brd_text: str,
    layout_raw_json: str,
    transcript_texts_json: str,
    layout_text: str | None = None,
    revision_number: int = 0,
) -> dict:
    from judges.h1_requirement.schemas import JudgeInputH1, RequirementModelInput

    judge = PreJudgeH1()
    judge_input = JudgeInputH1(
        session_id=session_id,
        requirement_model=RequirementModelInput(**json.loads(requirement_model_json)),
        brd_text=brd_text,
        layout_text=layout_text,
        layout_raw=json.loads(layout_raw_json),
        transcript_texts=json.loads(transcript_texts_json),
        revision_number=revision_number,
    )
    result = await judge.evaluate(judge_input)
    return result.model_dump(mode="json")


run_pre_judge_tool = FunctionTool(func=run_pre_judge_h1)

pre_judge_h1_agent = AdkAgent(
    name="PreJudgeH1",
    model=getattr(config, "GEMINI_MODEL", None) or getattr(config, "AGENT_MODEL", "gemini-2.5-pro"),
    description=(
        "Pre-checkpoint judge for the Requirements Layer (H1). "
        "Evaluates the RequirementModel against six rules before it reaches the BSA."
    ),
    instruction=(
        "You are the Pre-Judge for H1 — Requirements Layer. "
        "When invoked, call the run_pre_judge_h1 tool with the provided parameters "
        "and return the tool output exactly."
    ),
    tools=[run_pre_judge_tool],
)
