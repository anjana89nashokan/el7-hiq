from __future__ import annotations

import json
import re
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

try:  # pragma: no cover
    from google.adk.agents import Agent as AdkAgent
except Exception:  # pragma: no cover
    from google.adk.agents import LlmAgent as AdkAgent  # type: ignore

from google.adk.tools import FunctionTool

from config.settings import config
from judges.base_judge import AuditEventType, BaseJudge, audit_trail
from judges.h1_requirement.prompts import POST_JUDGE_FEEDBACK_PARSE_PROMPT
from judges.h1_requirement.schemas import JudgeInputH1, JudgeOutputH1
from models.judge import JudgeEvaluation, JudgeVerdict, RevisionDirective, RuleScore, RuleVerdict

logger = structlog.get_logger()


class PostJudgeH1(BaseJudge):
    async def evaluate(self, judge_input: JudgeInputH1) -> JudgeOutputH1:
        started_at = time.perf_counter()
        audit_trail.record(
            AuditEventType.AGENT_INVOKED,
            judge_input.session_id,
            agent="PostJudgeH1",
            revision_number=judge_input.revision_number,
        )

        complaints = await self._parse_bsa_feedback(judge_input.bsa_rejection_feedback or "")
        failed_rules = [self._map_complaint_to_rule(complaint) for complaint in complaints]
        priority_map = {
            "R4_SCOPE_BOUNDARY": 0,
            "R2_NO_HALLUCINATION": 1,
            "R1_COMPLETENESS": 2,
            "R3_AMBIGUITY_COVERAGE": 3,
            "R5_TRANSCRIPT_CONSISTENCY": 4,
            "R6_DOMAIN_CLASSIFICATION": 5,
        }
        unique_failed_rules = sorted(set(failed_rules), key=lambda item: priority_map.get(item, 99))

        structured_fixes: list[dict] = []
        context_clarifications: list[str] = []
        for complaint, rule_id in zip(complaints, failed_rules):
            complaint_text = str(complaint.get("complaint") or "").strip()
            field_reference = complaint.get("field_reference") or "requirement_model"
            fix_type = self._infer_fix_type(rule_id, complaint_text)
            structured_fixes.append(
                {
                    "rule_id": rule_id,
                    "fix_type": fix_type,
                    "target_field": field_reference,
                    "instruction": self._build_fix_instruction(rule_id, complaint_text, field_reference),
                    "brd_reference": self._find_brd_reference(field_reference, complaint_text, judge_input.brd_text),
                }
            )
            if self._looks_like_clarification(complaint_text):
                context_clarifications.append(complaint_text)

        revision_directive = RevisionDirective(
            session_id=judge_input.session_id,
            source="bsa_rejection",
            failed_rules=unique_failed_rules,
            bsa_feedback_raw=judge_input.bsa_rejection_feedback,
            structured_fixes=structured_fixes,
            priority_order=unique_failed_rules,
            context_additions=(
                {"bsa_clarifications": context_clarifications}
                if context_clarifications
                else {}
            ),
        )

        rule_scores = [
            RuleScore(
                rule_id=rule_id,
                rule_name=rule_id.replace("_", " ").title(),
                verdict=RuleVerdict.FAIL,
                score=0.0,
                weight=1 / max(len(unique_failed_rules), 1),
                evidence="BSA rejection feedback mapped this issue to the rule.",
                citations=[judge_input.bsa_rejection_feedback or ""],
                blocking=rule_id in {
                    "R2_NO_HALLUCINATION",
                    "R3_AMBIGUITY_COVERAGE",
                    "R4_SCOPE_BOUNDARY",
                    "R5_TRANSCRIPT_CONSISTENCY",
                },
                recommendations=[
                    fix["instruction"] for fix in structured_fixes if fix["rule_id"] == rule_id
                ],
            )
            for rule_id in unique_failed_rules
        ]
        evaluation = JudgeEvaluation(
            session_id=judge_input.session_id,
            judge_mode="post",
            verdict=JudgeVerdict.BLOCK,
            overall_score=0.0,
            rule_scores=rule_scores,
            blocking_rules=unique_failed_rules,
            warnings=[],
            summary=(
                "The BSA rejected the H1 artifact and the post-judge translated the rejection into a structured repair plan."
            ),
            recommendation="Return to the RequirementInterpreter with the RevisionDirective and rerun H1 pre-judge after revisions.",
            judge_model=self.model_name,
            evaluation_latency_ms=int((time.perf_counter() - started_at) * 1000),
        )
        audit_trail.record(
            AuditEventType.FEEDBACK_APPLIED,
            judge_input.session_id,
            agent="PostJudgeH1",
            failed_rules=unique_failed_rules,
        )
        audit_trail.record(
            AuditEventType.AGENT_COMPLETED,
            judge_input.session_id,
            agent="PostJudgeH1",
            verdict=evaluation.verdict.value,
            overall_score=evaluation.overall_score,
        )
        return JudgeOutputH1(
            evaluation=evaluation,
            revision_directive=revision_directive,
            annotated_artifact={},
            bsa_review_summary=(
                "The judge converted the rejection feedback into prioritized repair steps for the RequirementInterpreter. "
                "Scope and inclusion issues are ordered first, followed by completeness, ambiguity, transcript, and domain concerns as applicable. "
                "Use the structured fixes exactly as written before re-running H1."
            ),
        )

    async def _parse_bsa_feedback(self, feedback_text: str) -> list[dict]:
        if not feedback_text.strip():
            return []
        response = await self.llm_call(
            POST_JUDGE_FEEDBACK_PARSE_PROMPT.format(feedback_text=feedback_text)
        )
        complaints = response.get("complaints")
        if isinstance(complaints, list) and complaints:
            return complaints
        fallback_parts = [
            part.strip(" -\n\t")
            for part in re.split(r"[\n;]+", feedback_text)
            if part.strip(" -\n\t")
        ]
        return [
            {
                "complaint": part,
                "field_reference": None,
                "severity": "major",
                "keywords": [],
            }
            for part in fallback_parts
        ]

    def _map_complaint_to_rule(self, complaint: dict) -> str:
        text = " ".join(
            [
                str(complaint.get("complaint") or ""),
                str(complaint.get("field_reference") or ""),
                " ".join(complaint.get("keywords") or []),
            ]
        ).lower()
        if any(keyword in text for keyword in ["fabricat", "hallucin", "invent", "made up"]):
            return "R2_NO_HALLUCINATION"
        if any(keyword in text for keyword in ["scope", "population", "include", "exclude", "wrong records"]):
            return "R4_SCOPE_BOUNDARY"
        if any(keyword in text for keyword in ["ambig", "unclear", "either", "flag"]):
            return "R3_AMBIGUITY_COVERAGE"
        if any(keyword in text for keyword in ["transcript", "call", "meeting", "said", "discussed"]):
            return "R5_TRANSCRIPT_CONSISTENCY"
        if any(keyword in text for keyword in ["domain", "category", "classify", "wrong type"]):
            return "R6_DOMAIN_CLASSIFICATION"
        if any(keyword in text for keyword in ["miss", "skip", "incomplete", "forgot", "empty"]):
            return "R1_COMPLETENESS"
        return "R1_COMPLETENESS"

    @staticmethod
    def _infer_fix_type(rule_id: str, complaint_text: str) -> str:
        lowered = complaint_text.lower()
        if rule_id == "R2_NO_HALLUCINATION":
            return "remove"
        if any(keyword in lowered for keyword in ["add", "missing", "missed", "forgot"]):
            return "add"
        if any(keyword in lowered for keyword in ["cite", "source", "support"]):
            return "cite"
        if any(keyword in lowered for keyword in ["rename", "reword", "clarify", "rephrase"]):
            return "rephrase"
        return "add"

    @staticmethod
    def _build_fix_instruction(rule_id: str, complaint_text: str, field_reference: str) -> str:
        if rule_id == "R4_SCOPE_BOUNDARY":
            return (
                f"Correct the scope handling for {field_reference}: {complaint_text}. "
                "Make the inclusion and exclusion boundaries explicit in scope and explicit_filters."
            )
        if rule_id == "R2_NO_HALLUCINATION":
            return (
                f"Remove unsupported content at {field_reference}: {complaint_text}. "
                "Only retain values that can be traced to the BRD or transcript."
            )
        if rule_id == "R3_AMBIGUITY_COVERAGE":
            return (
                f"Flag the unresolved ambiguity at {field_reference}: {complaint_text}. "
                "Do not silently resolve it in the next revision."
            )
        if rule_id == "R5_TRANSCRIPT_CONSISTENCY":
            return (
                f"Reconcile transcript guidance for {field_reference}: {complaint_text}. "
                "Capture the transcript rule in implicit_rules and log any BRD conflict explicitly."
            )
        if rule_id == "R6_DOMAIN_CLASSIFICATION":
            return (
                f"Revisit the domain classification for {field_reference}: {complaint_text}. "
                "Align the primary_domain, complexity score, and recommended catalogs with the BRD language."
            )
        return (
            f"Address the completeness issue at {field_reference}: {complaint_text}. "
            "Populate the missing requirement detail from the BRD and keep the field traceable."
        )

    @staticmethod
    def _find_brd_reference(field_reference: str, complaint_text: str, brd_text: str) -> str:
        target = field_reference or complaint_text
        for sentence in brd_text.splitlines():
            if target and target.lower() in sentence.lower():
                return sentence.strip()
        for sentence in brd_text.split(". "):
            if any(token and token.lower() in sentence.lower() for token in complaint_text.split()[:3]):
                return sentence.strip()
        return "BRD reference requires manual confirmation."

    @staticmethod
    def _looks_like_clarification(complaint_text: str) -> bool:
        lowered = complaint_text.lower()
        return any(
            keyword in lowered
            for keyword in ["should be", "must be", "use ", "only ", "clarif", "meaning", "specifically"]
        )


async def run_post_judge_h1(
    session_id: str,
    requirement_model_json: str,
    brd_text: str,
    layout_raw_json: str,
    transcript_texts_json: str,
    bsa_rejection_feedback: str,
    prior_evaluation_json: str,
    revision_number: int,
    layout_text: str | None = None,
) -> dict:
    from judges.h1_requirement.schemas import JudgeInputH1, RequirementModelInput

    judge = PostJudgeH1()
    judge_input = JudgeInputH1(
        session_id=session_id,
        requirement_model=RequirementModelInput(**json.loads(requirement_model_json)),
        brd_text=brd_text,
        layout_text=layout_text,
        layout_raw=json.loads(layout_raw_json),
        transcript_texts=json.loads(transcript_texts_json),
        bsa_rejection_feedback=bsa_rejection_feedback,
        previous_evaluation=json.loads(prior_evaluation_json) if prior_evaluation_json else None,
        revision_number=revision_number,
    )
    result = await judge.evaluate(judge_input)
    return result.model_dump(mode="json")


run_post_judge_tool = FunctionTool(func=run_post_judge_h1)

post_judge_h1_agent = AdkAgent(
    name="PostJudgeH1",
    model=getattr(config, "GEMINI_MODEL", None) or getattr(config, "AGENT_MODEL", "gemini-2.5-pro"),
    description=(
        "Post-rejection judge for the Requirements Layer (H1). "
        "Analyzes BSA feedback and produces a RevisionDirective."
    ),
    instruction=(
        "You are the Post-Judge for H1 — Requirements Layer. "
        "When invoked, call the run_post_judge_h1 tool with the provided parameters "
        "and return the tool output exactly."
    ),
    tools=[run_post_judge_tool],
)
