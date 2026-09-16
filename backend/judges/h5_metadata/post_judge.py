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
from judges.h5_metadata.naming_checker import (
    check_attribute_name,
    check_data_type_validity,
)
from judges.h5_metadata.prompts import POST_JUDGE_FEEDBACK_PARSE_H5_PROMPT
from judges.h5_metadata.schemas import JudgeInputH5, JudgeOutputH5
from models.judge import (
    JudgeEvaluation,
    JudgeVerdict,
    RevisionDirective,
    RuleScore,
    RuleVerdict,
)

logger = structlog.get_logger()


_PRIORITY_ORDER_FIX_TYPES = [
    "regenerate_template",
    "reopen_h4",
    "revert_source",
    "revert_transformation",
    "rename_attribute",
    "retype_attribute",
    "fix_file_metadata_field",
    "correct_match_type",
    "add_transformation",
    "remove_transformation",
    "flag_for_clarification",
]


class PostJudgeH5(BaseJudge):
    """Post-rejection judge for H5 — Metadata Generation Layer."""

    async def evaluate(self, judge_input: JudgeInputH5) -> JudgeOutputH5:
        started_at = time.perf_counter()
        audit_trail.record(
            AuditEventType.AGENT_INVOKED,
            judge_input.session_id,
            agent="PostJudgeH5",
            revision_number=judge_input.revision_number,
        )

        complaints = await self._parse_bsa_feedback(judge_input)

        h4_fields_by_name = {
            str(f.get("field_name") or "").upper(): f
            for f in judge_input.h4_mapping_spec.fields
        }
        attributes_by_name = {
            a.name.upper(): a for a in judge_input.metadata_output.attributes
        }

        structured_fixes: list[dict] = []
        failed_rules: list[str] = []
        reopen_h4 = False

        for complaint in complaints:
            rule_id = self._map_complaint_to_rule(complaint)
            failed_rules.append(rule_id)

            location_type = str(complaint.get("location_type") or "").lower() or "attribute"
            attribute_name = str(complaint.get("attribute_name") or "").strip() or None
            attribute_position = complaint.get("attribute_position")
            metadata_field_path = str(complaint.get("metadata_field_path") or "").strip() or None
            current_value = complaint.get("current_value")
            suggested_value = complaint.get("suggested_value")
            severity = complaint.get("severity") or "major"
            complaint_text = str(complaint.get("complaint") or "").strip()

            fix_type = self._normalize_fix_type(
                complaint.get("fix_type"), complaint_text, rule_id, complaint
            )

            if fix_type == "reopen_h4":
                reopen_h4 = True

            corrected_value = self._resolve_corrected_value(
                fix_type=fix_type,
                attribute_name=attribute_name,
                metadata_field_path=metadata_field_path,
                suggested_value=suggested_value,
                attributes_by_name=attributes_by_name,
                h4_fields_by_name=h4_fields_by_name,
                judge_input=judge_input,
            )

            instruction = self._build_instruction(
                rule_id=rule_id,
                fix_type=fix_type,
                complaint_text=complaint_text,
                attribute_name=attribute_name,
                attribute_position=attribute_position,
                metadata_field_path=metadata_field_path,
                corrected_value=corrected_value,
            )

            structured_fixes.append(
                {
                    "rule_id": rule_id,
                    "fix_type": fix_type,
                    "location_type": location_type,
                    "attribute_name": attribute_name,
                    "attribute_position": attribute_position,
                    "metadata_field_path": metadata_field_path,
                    "current_value": current_value,
                    "corrected_value": corrected_value,
                    "instruction": instruction,
                    "severity": severity,
                }
            )

        # Order fixes by priority
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
                {"reopen_h4_required": True} if reopen_h4 else {}
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
                    "R3_TEMPLATE_SCHEMA",
                    "R5_ROUND_TRIP",
                    "R4_COMPLETENESS",
                },
                recommendations=[
                    fix["instruction"] for fix in structured_fixes if fix["rule_id"] == rule_id
                ],
            )
            for rule_id in unique_failed_rules
        ]

        evaluation = JudgeEvaluation(
            session_id=judge_input.session_id,
            phase="metadata",
            checkpoint="H5",
            judge_mode="post",
            verdict=JudgeVerdict.BLOCK,
            overall_score=0.0,
            rule_scores=rule_scores,
            blocking_rules=unique_failed_rules,
            warnings=[],
            summary=(
                "BSA rejected the H5 metadata. Post-judge translated the rejection into a structured field-level repair plan."
                + (" Includes a reopen_h4 directive — H4 must be re-reviewed." if reopen_h4 else "")
            ),
            recommendation=(
                "ESCALATE to senior BSA — H4 must be re-reviewed before MetadataBuilder can proceed."
                if reopen_h4
                else "Return to MetadataBuilder with the RevisionDirective and rerun H5 pre-judge after revisions."
            ),
            judge_model=self.model_name,
            evaluation_latency_ms=int((time.perf_counter() - started_at) * 1000),
        )

        audit_trail.record(
            AuditEventType.FEEDBACK_APPLIED,
            judge_input.session_id,
            agent="PostJudgeH5",
            failed_rules=unique_failed_rules,
            reopen_h4_required=reopen_h4,
        )
        audit_trail.record(
            AuditEventType.AGENT_COMPLETED,
            judge_input.session_id,
            agent="PostJudgeH5",
            verdict=evaluation.verdict.value,
            overall_score=evaluation.overall_score,
        )

        bsa_review_summary = (
            "Senior BSA escalation: a NO MATCH field was promoted at H5. H4 must be re-reviewed before this extract can proceed."
            if reopen_h4
            else (
                "The judge converted the rejection feedback into prioritized field-level repair steps. "
                "Template regeneration runs first, followed by source/transform reverts, naming/type fixes, "
                "and finally any items needing BSA clarification."
            )
        )

        return JudgeOutputH5(
            evaluation=evaluation,
            revision_directive=revision_directive,
            annotated_metadata={},
            bsa_review_summary=bsa_review_summary,
            quality_scorecard={
                "blocking_issues": unique_failed_rules,
                "reopen_h4_required": reopen_h4,
            },
            auto_corrected_output={},
        )

    async def _parse_bsa_feedback(self, judge_input: JudgeInputH5) -> list[dict]:
        feedback_text = (judge_input.bsa_rejection_feedback or "").strip()
        if not feedback_text:
            return []

        attribute_summary = [
            {"position": a.position, "name": a.name, "data_type": a.data_type}
            for a in judge_input.metadata_output.attributes
        ]
        file_metadata_summary = judge_input.metadata_output.file_metadata.model_dump()

        response = await self.llm_call(
            POST_JUDGE_FEEDBACK_PARSE_H5_PROMPT.format(
                feedback_text=feedback_text,
                attribute_summary_json=json.dumps(attribute_summary),
                file_metadata_summary_json=json.dumps(file_metadata_summary),
            )
        )
        complaints = response.get("complaints")
        if isinstance(complaints, list) and complaints:
            return complaints

        fallback = [
            part.strip(" -\n\t")
            for part in re.split(r"[\n;]+", feedback_text)
            if part.strip(" -\n\t")
        ]
        return [
            {
                "complaint": part,
                "location_type": "attribute",
                "attribute_name": None,
                "attribute_position": None,
                "metadata_field_path": None,
                "fix_type": None,
                "current_value": None,
                "suggested_value": None,
                "severity": "major",
                "rule_hint": "unknown",
            }
            for part in fallback
        ]

    @staticmethod
    def _map_complaint_to_rule(complaint: dict) -> str:
        text = " ".join(
            [
                str(complaint.get("complaint") or ""),
                str(complaint.get("metadata_field_path") or ""),
                str(complaint.get("rule_hint") or ""),
            ]
        ).lower()
        if any(k in text for k in ["name", "naming", "rename", "abbreviation", "convention", "suffix", "case"]):
            return "R1_NAMING_CONFORMANCE"
        if any(k in text for k in ["type", "data type", "cast", "overflow", "truncat", "precision", "decimal"]):
            return "R2_TYPE_SAFETY"
        if any(k in text for k in ["template", "schema", "invalid", "format", "json", "missing field"]):
            return "R3_TEMPLATE_SCHEMA"
        if any(k in text for k in ["missing", "field", "layout", "dropped", "incomplete", "no source"]):
            return "R4_COMPLETENESS"
        if any(k in text for k in ["changed", "different", "h4", "approved", "revert", "original", "source"]):
            return "R5_ROUND_TRIP"
        if any(k in text for k in ["score", "wrong", "inflated", "percent", "threshold"]):
            return "R6_SCORE_CALIBRATION"
        return "R4_COMPLETENESS"

    @staticmethod
    def _normalize_fix_type(
        raw_fix_type: Any,
        complaint_text: str,
        rule_id: str,
        complaint: dict,
    ) -> str:
        lowered = complaint_text.lower()
        if isinstance(raw_fix_type, str):
            value = raw_fix_type.strip().lower()
            if value in _PRIORITY_ORDER_FIX_TYPES:
                return value
            mapping = {
                "rename": "rename_attribute",
                "retype": "retype_attribute",
                "revert_to_h4": "revert_source",
                "change_value": "fix_file_metadata_field",
                "add_field": "add_transformation",
                "remove_field": "remove_transformation",
                "flag_for_clarification": "flag_for_clarification",
            }
            if value in mapping:
                return mapping[value]

        if rule_id == "R3_TEMPLATE_SCHEMA":
            return "regenerate_template"
        if rule_id == "R5_ROUND_TRIP":
            if "no_match" in lowered or "no match" in lowered or "promoted" in lowered:
                return "reopen_h4"
            if "transform" in lowered:
                return "revert_transformation"
            return "revert_source"
        if rule_id == "R1_NAMING_CONFORMANCE":
            return "rename_attribute"
        if rule_id == "R2_TYPE_SAFETY":
            return "retype_attribute"
        if rule_id == "R4_COMPLETENESS":
            if "missing" in lowered:
                return "add_transformation"
            return "fix_file_metadata_field"
        return "flag_for_clarification"

    def _resolve_corrected_value(
        self,
        fix_type: str,
        attribute_name: str | None,
        metadata_field_path: str | None,
        suggested_value: Any,
        attributes_by_name: dict,
        h4_fields_by_name: dict,
        judge_input: JudgeInputH5,
    ) -> str | None:
        if fix_type in {"regenerate_template", "reopen_h4", "flag_for_clarification"}:
            return None

        if fix_type == "revert_source" and attribute_name:
            h4_field = h4_fields_by_name.get(attribute_name.upper())
            if not h4_field:
                return None
            source_table = h4_field.get("source_table")
            source_column = h4_field.get("source_column")
            if source_table and source_column:
                return f"{source_table}.{source_column}"
            return None

        if fix_type == "revert_transformation" and attribute_name:
            h4_field = h4_fields_by_name.get(attribute_name.upper())
            if h4_field and h4_field.get("transformation"):
                return str(h4_field["transformation"])
            return None

        if fix_type == "rename_attribute" and attribute_name:
            attribute = attributes_by_name.get(attribute_name.upper())
            if attribute is None:
                return suggested_value if isinstance(suggested_value, str) else None
            violations = check_attribute_name(
                attribute.name,
                f"attributes[{attribute.position}].name",
                attribute.semantic_type,
            )
            for violation in violations:
                if violation.auto_correctable and violation.suggested_correction:
                    return violation.suggested_correction
            return suggested_value if isinstance(suggested_value, str) else None

        if fix_type == "retype_attribute":
            if isinstance(suggested_value, str) and check_data_type_validity(suggested_value):
                return suggested_value
            return None

        if fix_type == "fix_file_metadata_field":
            if isinstance(suggested_value, str):
                return suggested_value
            return None

        if fix_type == "correct_match_type" and attribute_name:
            h4_field = h4_fields_by_name.get(attribute_name.upper())
            if h4_field:
                return str(h4_field.get("match_type") or "")
            return None

        if isinstance(suggested_value, str):
            return suggested_value
        return None

    @staticmethod
    def _build_instruction(
        rule_id: str,
        fix_type: str,
        complaint_text: str,
        attribute_name: str | None,
        attribute_position: int | None,
        metadata_field_path: str | None,
        corrected_value: str | None,
    ) -> str:
        prefix = f"[{rule_id} / {fix_type}]"
        target_parts: list[str] = []
        if attribute_name and attribute_position is not None:
            target_parts.append(f"attribute '{attribute_name}' at position {attribute_position}")
        elif attribute_name:
            target_parts.append(f"attribute '{attribute_name}'")
        if metadata_field_path:
            target_parts.append(f"path '{metadata_field_path}'")
        target = ", ".join(target_parts) or "the affected field"

        body = complaint_text or "Apply the indicated fix."
        if corrected_value:
            return f"{prefix} {body} on {target}. Apply value: {corrected_value}"
        if fix_type == "reopen_h4":
            return f"{prefix} {body} on {target}. ESCALATE — H4 must be re-reviewed."
        if fix_type == "regenerate_template":
            return f"{prefix} {body}. Re-export the IndiMap template — do not patch JSON manually."
        if fix_type == "flag_for_clarification":
            return f"{prefix} {body} on {target}. BSA must specify the correct value."
        return f"{prefix} {body} on {target}. Provide the corrected value in the next revision."


async def run_post_judge_h5(
    session_id: str,
    metadata_output_json: str,
    h4_mapping_json: str,
    layout_fields_json: str,
    bsa_rejection_feedback: str,
    prior_evaluation_json: str,
    revision_number: int,
) -> dict:
    judge = PostJudgeH5()
    judge_input = JudgeInputH5(
        session_id=session_id,
        metadata_output=json.loads(metadata_output_json),
        h4_mapping_spec=json.loads(h4_mapping_json),
        original_layout_fields=json.loads(layout_fields_json),
        bsa_rejection_feedback=bsa_rejection_feedback,
        previous_evaluation=json.loads(prior_evaluation_json) if prior_evaluation_json else None,
        revision_number=revision_number,
    )
    result = await judge.evaluate(judge_input)
    return result.model_dump(mode="json")


run_post_judge_h5_tool = FunctionTool(func=run_post_judge_h5)

post_judge_h5_agent = AdkAgent(
    name="PostJudgeH5",
    model=getattr(config, "GEMINI_MODEL", None) or getattr(config, "AGENT_MODEL", "gemini-2.5-pro"),
    description=(
        "Post-rejection judge for the Metadata Generation Layer (H5). "
        "Maps BSA feedback to attribute-level corrections and produces a "
        "RevisionDirective. Escalates to H4 re-review when corrections require "
        "mapping decisions."
    ),
    instruction=(
        "You are the Post-Judge for H5 — Metadata Generation Layer. "
        "When invoked, call the run_post_judge_h5 tool and return the tool output exactly."
    ),
    tools=[run_post_judge_h5_tool],
)
