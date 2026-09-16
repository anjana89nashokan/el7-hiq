"""
RowTextRegeneratorAgent (Step 4) - internal LLM request/response schemas.

Why these are here:
  - LLM-facing structured output contracts belong with the subagent + prompts.
  - Deterministic validation/apply remains in utils.
"""

from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, Field

from agents.mapping_apply_review.models import InterpretationPlan


class RowTextRegenInput(BaseModel):
    """
    Minimal per-row snapshot for safe text regeneration.

    Notes:
      - Identifiers are provided explicitly via allowlisted_identifiers.
      - The LLM must not introduce new identifiers outside that allowlist.
    """

    row_id: str = Field(..., description="Step2 MappingRow.row_id (stable).")
    target_table_id: str = Field(..., description="Target table id (immutable; context only).")
    target_column_name: str = Field(..., description="Target column name (immutable; context only).")
    rule_type: str = Field(..., description="Current rule_type for this row.")

    # Target metadata (context only; helps produce clearer text)
    target_data_type: str | None = Field(default=None, description="Target data type (context).")
    target_attribute_business_description: str | None = Field(default=None, description="Target business description (context).")

    # BSA context (for clarity; text must still respect allowlisted identifiers)
    bsa_feedback_text: str | None = Field(default=None, description="Raw BSA feedback for this row (if any).")
    linked_answers: List[dict[str, Any]] = Field(
        default_factory=list,
        description="Linked answers for this row: {question_id, priority, answer_text}.",
    )
    recent_changes: List[dict[str, Any]] = Field(
        default_factory=list,
        description="Recent applied changes for this row (field_name, before, after, source).",
    )

    # Safe identifiers already present/accepted in row state.
    allowlisted_identifiers: List[str] = Field(
        default_factory=list,
        description="Identifiers the LLM is allowed to mention (must be a subset of these).",
    )

    # Current row state (small)
    source_entity_id: str | None = Field(default=None, description="Current source entity id (if any).")
    source_field_names: List[str] = Field(default_factory=list, description="Current source fields (if any).")
    lookup_table_ids: List[str] = Field(default_factory=list, description="Current lookup table ids (if any).")
    join_text: str | None = Field(default=None, description="Current join_text (if any).")
    row_filter_text: str | None = Field(default=None, description="Current row_filter_text (if any).")
    transformation_rules_text: str | None = Field(default=None, description="Current transformation_rules_text (if any).")
    special_considerations_text: str | None = Field(default=None, description="Current special_considerations_text (if any).")


class RowTextRegenBatchRequest(BaseModel):
    items: List[RowTextRegenInput] = Field(default_factory=list, description="Rows to regenerate in one batch call.")


class RowTextRegenBatchOutput(BaseModel):
    """
    Output: structured row-level plans (text fields only, source=NORMALIZATION).
    """

    plans: List[InterpretationPlan] = Field(default_factory=list, description="InterpretationPlan patches for text fields.")
