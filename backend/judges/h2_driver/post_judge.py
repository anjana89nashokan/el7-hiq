from __future__ import annotations

import json
import re
import time
from typing import Any

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
from judges.h2_driver.prompts import POST_JUDGE_FEEDBACK_PARSE_PROMPT
from judges.h2_driver.schemas import JudgeInputH2, JudgeOutputH2
from judges.h2_driver.sql_analyzer import build_sql_analysis_report, parse_predicates
from models.judge import (
    JudgeEvaluation,
    JudgeVerdict,
    RevisionDirective,
    RuleScore,
    RuleVerdict,
)

logger = structlog.get_logger()


_PRIORITY_ORDER_FIX_TYPES = [
    "move_to_transform",
    "change_field",
    "remove_predicate",
    "change_operator",
    "change_values",
    "add_predicate",
    "flag_for_clarification",
]


class PostJudgeH2(BaseJudge):
    """Post-rejection judge for H2 — produces SQL-level RevisionDirective."""

    async def evaluate(self, judge_input: JudgeInputH2) -> JudgeOutputH2:
        started_at = time.perf_counter()
        audit_trail.record(
            AuditEventType.AGENT_INVOKED,
            judge_input.session_id,
            agent="PostJudgeH2",
            revision_number=judge_input.revision_number,
        )

        complaints = await self._parse_bsa_feedback(
            judge_input.bsa_rejection_feedback or "",
            judge_input.driver_criteria.where_clause or "",
        )

        structured_fixes: list[dict] = []
        failed_rules: list[str] = []
        context_clarifications: list[str] = []

        for complaint in complaints:
            complaint_text = str(complaint.get("complaint") or "").strip()
            affected = str(complaint.get("affected_predicate") or "").strip() or None
            fix_type = self._normalize_fix_type(complaint.get("fix_type"), complaint_text)
            rule_id = self._map_complaint_to_rule(complaint, complaint_text)
            failed_rules.append(rule_id)

            corrected_sql = await self._generate_corrected_sql(
                affected, fix_type, complaint_text, judge_input
            )

            instruction = self._build_instruction(
                rule_id, fix_type, complaint_text, affected, corrected_sql
            )

            fix_entry = {
                "rule_id": rule_id,
                "fix_type": fix_type,
                "original_sql": affected,
                "corrected_sql": corrected_sql,
                "instruction": instruction,
                "severity": complaint.get("severity") or "major",
            }
            structured_fixes.append(fix_entry)

            if fix_type == "flag_for_clarification" or self._looks_like_clarification(complaint_text):
                context_clarifications.append(complaint_text)

        # Sort fixes by priority
        structured_fixes.sort(
            key=lambda fix: _PRIORITY_ORDER_FIX_TYPES.index(fix["fix_type"])
            if fix["fix_type"] in _PRIORITY_ORDER_FIX_TYPES
            else 99
        )

        unique_failed_rules = sorted(set(failed_rules))

        revision_directive = RevisionDirective(
            session_id=judge_input.session_id,
            source="bsa_rejection",
            failed_rules=unique_failed_rules,
            bsa_feedback_raw=judge_input.bsa_rejection_feedback,
            structured_fixes=structured_fixes,
            priority_order=[fix["fix_type"] for fix in structured_fixes],
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
                blocking=rule_id
                in {
                    "R2_NO_TRANSFORMATION_LEAKAGE",
                    "R3_STANDARD_FIELD_COMPLIANCE",
                    "R5_OPERATOR_DIRECTION",
                    "R7_FYI_USAGE",
                },
                recommendations=[
                    fix["instruction"] for fix in structured_fixes if fix["rule_id"] == rule_id
                ],
            )
            for rule_id in unique_failed_rules
        ]

        evaluation = JudgeEvaluation(
            session_id=judge_input.session_id,
            phase="driver",
            checkpoint="H2",
            judge_mode="post",
            verdict=JudgeVerdict.BLOCK,
            overall_score=0.0,
            rule_scores=rule_scores,
            blocking_rules=unique_failed_rules,
            warnings=[],
            summary=(
                "The BSA rejected the H2 driver and the post-judge translated the rejection into a structured SQL repair plan."
            ),
            recommendation="Return to the DriverGenerator with the RevisionDirective and rerun H2 pre-judge after revisions.",
            judge_model=self.model_name,
            evaluation_latency_ms=int((time.perf_counter() - started_at) * 1000),
        )

        audit_trail.record(
            AuditEventType.FEEDBACK_APPLIED,
            judge_input.session_id,
            agent="PostJudgeH2",
            failed_rules=unique_failed_rules,
        )
        audit_trail.record(
            AuditEventType.AGENT_COMPLETED,
            judge_input.session_id,
            agent="PostJudgeH2",
            verdict=evaluation.verdict.value,
            overall_score=evaluation.overall_score,
        )

        return JudgeOutputH2(
            evaluation=evaluation,
            revision_directive=revision_directive,
            annotated_driver={},
            bsa_review_summary=(
                "The judge converted the rejection feedback into prioritized SQL-level repair steps for the DriverGenerator. "
                "Transformation removals run first, followed by field corrections, predicate removals, "
                "operator changes, value-set updates, additions, and finally any items needing BSA clarification."
            ),
            sql_analysis_report=build_sql_analysis_report(
                judge_input.driver_criteria.where_clause or ""
            ),
        )

    async def _parse_bsa_feedback(self, feedback_text: str, where_clause: str) -> list[dict]:
        if not feedback_text.strip():
            return []
        response = await self.llm_call(
            POST_JUDGE_FEEDBACK_PARSE_PROMPT.format(
                feedback_text=feedback_text, where_clause=where_clause
            )
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
                "affected_predicate": None,
                "fix_type": None,
                "severity": "major",
                "rule_hint": "unknown",
            }
            for part in fallback_parts
        ]

    @staticmethod
    def _map_complaint_to_rule(complaint: dict, complaint_text: str) -> str:
        text = " ".join(
            [
                complaint_text,
                str(complaint.get("affected_predicate") or ""),
                str(complaint.get("rule_hint") or ""),
            ]
        ).lower()
        if any(k in text for k in ["fabricat", "invented", "made up", "not in brd", "no source"]):
            return "R1_BRD_TRACEABILITY"
        if any(k in text for k in ["transform", "substr", "concat", "case", "function", "format"]):
            return "R2_NO_TRANSFORMATION_LEAKAGE"
        if any(k in text for k in ["field name", "non-standard", "wrong field", "not standard", "standard field"]):
            return "R3_STANDARD_FIELD_COMPLIANCE"
        if any(k in text for k in ["contradiction", "always false", "impossible", "both"]):
            return "R4_LOGICAL_CONSISTENCY"
        if any(k in text for k in ["wrong direction", "include", "exclude", "inverted", "not in", " in "]):
            return "R5_OPERATOR_DIRECTION"
        if any(k in text for k in ["missing", "dimension", "coverage", "no filter", "forgot"]):
            return "R6_POPULATION_COVERAGE"
        if any(k in text for k in ["fyi", "wrong values", "invalid code", "not a valid"]):
            return "R7_FYI_USAGE"
        return "R6_POPULATION_COVERAGE"

    @staticmethod
    def _normalize_fix_type(raw_fix_type: Any, complaint_text: str) -> str:
        if isinstance(raw_fix_type, str):
            value = raw_fix_type.strip().lower()
            mapping = {
                "add": "add_predicate",
                "remove": "remove_predicate",
                "change_operator": "change_operator",
                "change_values": "change_values",
                "change_field": "change_field",
                "reorder": "change_operator",
                "split": "change_values",
            }
            if value in mapping:
                return mapping[value]
            if value in _PRIORITY_ORDER_FIX_TYPES:
                return value

        lowered = complaint_text.lower()
        if any(k in lowered for k in ["transform", "substr", "case", "concat"]):
            return "move_to_transform"
        if any(k in lowered for k in ["wrong field", "non-standard", "rename"]):
            return "change_field"
        if any(k in lowered for k in ["remove", "drop", "delete"]):
            return "remove_predicate"
        if any(k in lowered for k in ["invert", "in instead of not in", "not in instead of in", "wrong direction"]):
            return "change_operator"
        if any(k in lowered for k in ["values", "missing values", "extra values"]):
            return "change_values"
        if any(k in lowered for k in ["missing", "add", "include also"]):
            return "add_predicate"
        if any(k in lowered for k in ["unclear", "clarify", "ambig"]):
            return "flag_for_clarification"
        return "add_predicate"

    async def _generate_corrected_sql(
        self,
        original_predicate: str | None,
        fix_type: str,
        complaint_text: str,
        judge_input: JudgeInputH2,
    ) -> str | None:
        if not original_predicate:
            return None
        if fix_type == "remove_predicate":
            return None
        if fix_type == "flag_for_clarification":
            return None
        if fix_type == "change_operator":
            corrected = original_predicate
            substitutions = [
                (r"\bNOT\s+IN\b", "IN"),
                (r"(?<!NOT )\bIN\b", "NOT IN"),
                (r"!=", "="),
                (r"<>", "="),
                (r"\bIS\s+NOT\s+NULL\b", "IS NULL"),
                (r"\bIS\s+NULL\b", "IS NOT NULL"),
            ]
            applied = False
            for pattern, replacement in substitutions:
                if re.search(pattern, original_predicate, flags=re.IGNORECASE):
                    corrected = re.sub(
                        pattern, replacement, original_predicate, count=1, flags=re.IGNORECASE
                    )
                    applied = True
                    break
            return corrected if applied else None
        if fix_type == "change_field":
            standards = judge_input.standards_dictionary or {}
            for business_term, standard in standards.items():
                if business_term.lower() in complaint_text.lower():
                    parsed = parse_predicates(original_predicate)
                    if parsed and parsed[0].field_name:
                        return original_predicate.replace(parsed[0].field_name, standard, 1)
            return None
        if fix_type == "move_to_transform":
            parsed = parse_predicates(original_predicate)
            if parsed and parsed[0].field_name:
                stripped = (
                    f"{parsed[0].field_name} {parsed[0].operator or '='} "
                    f"<RAW_VALUE_TBD_BY_BSA>  -- TRANSFORMATION MOVED: {original_predicate}"
                )
                return stripped
            return f"-- TRANSFORMATION MOVED: {original_predicate}"
        # add_predicate / change_values are not deterministically computable here
        return None

    @staticmethod
    def _build_instruction(
        rule_id: str,
        fix_type: str,
        complaint_text: str,
        original_predicate: str | None,
        corrected_sql: str | None,
    ) -> str:
        base = f"[{rule_id} / {fix_type}] "
        target = f"on '{original_predicate}'" if original_predicate else "on the affected predicate"
        body = f"{complaint_text}".strip()
        if corrected_sql:
            return f"{base}{body} {target}. Apply: {corrected_sql}"
        if fix_type == "flag_for_clarification":
            return f"{base}{body} {target}. Flag as BSA Clarification before regenerating."
        if fix_type == "remove_predicate":
            return f"{base}Remove {target}: {body}"
        return f"{base}{body} {target}. Provide the exact corrected SQL in the next revision."

    @staticmethod
    def _looks_like_clarification(text: str) -> bool:
        lowered = text.lower()
        return any(k in lowered for k in ["should be", "must be", "clarif", "specifically", "meaning"])


async def run_post_judge_h2(
    session_id: str,
    driver_criteria_json: str,
    requirement_model_json: str,
    brd_text: str,
    standards_dictionary_json: str,
    bsa_rejection_feedback: str,
    prior_evaluation_json: str,
    revision_number: int,
) -> dict:
    judge = PostJudgeH2()
    judge_input = JudgeInputH2(
        session_id=session_id,
        driver_criteria=json.loads(driver_criteria_json),
        h1_requirement_model=json.loads(requirement_model_json),
        brd_text=brd_text,
        standards_dictionary=json.loads(standards_dictionary_json),
        bsa_rejection_feedback=bsa_rejection_feedback,
        previous_evaluation=json.loads(prior_evaluation_json) if prior_evaluation_json else None,
        revision_number=revision_number,
    )
    result = await judge.evaluate(judge_input)
    return result.model_dump(mode="json")


run_post_judge_h2_tool = FunctionTool(func=run_post_judge_h2)

post_judge_h2_agent = AdkAgent(
    name="PostJudgeH2",
    model=getattr(config, "GEMINI_MODEL", None) or getattr(config, "AGENT_MODEL", "gemini-2.5-pro"),
    description=(
        "Post-rejection judge for the Driver Generation Layer (H2). "
        "Analyzes BSA rejection feedback and produces a RevisionDirective "
        "with SQL-level corrected predicates for the DriverGenerator."
    ),
    instruction=(
        "You are the Post-Judge for H2 — Driver Generation Layer. "
        "When invoked, call the run_post_judge_h2 tool and return the tool output exactly."
    ),
    tools=[run_post_judge_h2_tool],
)
