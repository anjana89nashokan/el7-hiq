"""
MappingPostProcessorAgent (Step 2) — Subagent #3.

This agent is the last stage of Step 2 before we persist the Step2State artifact.

Responsibilities (runtime contract):
  1) Finalize `transformation_rules_text` per MappingRow (documentation-first; avoid SQL).
  2) Add `special_considerations_text` only when actionable (edge cases, caveats).
  3) Strict validation + normalization:
       - referenced entities/columns exist in Step 1 schemas (no hallucinations)
       - join unknown => needs_review=True (and drives a question candidate)
       - multi-rule rows well-formed (CASE/IF_ELSE must have rule_instance_id)
  4) Generate `question_candidates[]` for Step 3 (HITL) from:
       - OpenIssues (authoritative “what’s missing”)
       - low confidence rows (even if no explicit issue exists)

Guardrails:
  - Do not invent schema. Only reference entities/columns present in Step 1 SharedState.
  - Deterministic-first: v1 must work without any RAG/FYI. (We can add optional LLM
    wordsmithing later, but the safety/validations remain deterministic.)
"""

from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.mapping_generation.models import (
    MappingRow,
    OpenIssue,
    QuestionCandidate,
    Step2WorkContext,
    TableCommonFilter,
)
from utils.post_processor_utils import (
    build_question_candidates,
    enrich_rows_with_target_metadata,
    finalize_transformation_texts,
    post_validate_and_normalize_rows,
)


async def run_post_processor_agent(
    ctx: Step2WorkContext,
    rows: list[MappingRow],
    issues: list[OpenIssue],
    table_common_filters: list[TableCommonFilter],
) -> tuple[list[MappingRow], list[OpenIssue], list[QuestionCandidate]]:
    """
    Deterministic entrypoint for AG3.

    Returns:
      - rows: updated MappingRows (with transformation text + normalized fields)
      - issues: updated OpenIssues (may add validation issues; prunes unreferenced)
      - question_candidates: derived HITL seeds for Step 3
    """
    finalize_transformation_texts(ctx=ctx, rows=rows, issues=issues, table_common_filters=table_common_filters)
    post_validate_and_normalize_rows(ctx=ctx, rows=rows, issues=issues)
    enrich_rows_with_target_metadata(ctx=ctx, rows=rows)

    confidence_threshold = float(getattr(ctx, "confidence_threshold", 0.85)) if hasattr(ctx, "confidence_threshold") else 0.85
    question_candidates = build_question_candidates(
        ctx=ctx, rows=rows, issues=issues, confidence_threshold=confidence_threshold
    )

    return rows, issues, question_candidates


# ADK structural agent (wiring only; runtime uses the deterministic entrypoint above).
post_processor_agent = SequentialAgent(
    name="mapping_post_processor_agent",
    sub_agents=[],
    description="MappingPostProcessorAgent (Step 2) exposed via run_post_processor_agent(ctx, rows, issues, filters).",
)

__all__ = ["post_processor_agent", "run_post_processor_agent"]
