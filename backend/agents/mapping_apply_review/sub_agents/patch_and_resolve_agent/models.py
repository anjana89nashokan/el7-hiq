"""
PatchAndResolveAgent (Step 4) - internal LLM request/response schemas.

Why these are here:
  - LLM-facing structured output contracts belong with the subagent + prompts.
  - Deterministic apply/validation logic remains in utils (no LLM).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from agents.mapping_apply_review.models import IssuePlan


class IssueContextInput(BaseModel):
    """
    Minimal issue-centric context sent to the LLM.
    """

    issue_id: str = Field(..., description="Step2 OpenIssue.issue_id.")
    issue_type: str = Field(..., description="Step2 OpenIssue.issue_type.")
    severity: str = Field(..., description="Step2 OpenIssue.severity.")
    target_table_id: str = Field(..., description="Target table id (immutable).")
    target_column_name: str = Field(..., description="Target column name (immutable).")
    issue_message: str = Field(..., description="Human-readable issue message from Step 2.")

    affected_row_ids: List[str] = Field(default_factory=list, description="MappingRow.row_id values impacted by this issue.")

    # Row snapshots (small): current state after applying row-level intent plan.
    row_snapshots: List[dict] = Field(
        default_factory=list,
        description="Minimal per-row snapshot objects (rule_type/source_entity/source_field_names/join_condition/etc.).",
    )

    # Evidence inputs
    feedback_texts: List[str] = Field(default_factory=list, description="Relevant BSA feedback texts (free text).")
    answers: List[dict] = Field(
        default_factory=list,
        description="Relevant answers: {question_id, priority, answer_text}.",
    )


class IssuePlanBatchRequest(BaseModel):
    items: List[IssueContextInput] = Field(default_factory=list, description="Issues to resolve in one batch call.")


class IssuePlanBatchOutput(BaseModel):
    """
    Output: structured issue plans.
    """

    issue_plans: List[IssuePlan] = Field(default_factory=list, description="Issue resolution plans.")

