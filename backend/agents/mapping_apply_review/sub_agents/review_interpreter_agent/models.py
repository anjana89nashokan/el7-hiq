"""
Step 4 - ReviewInterpreterAgent internal schemas.

Why these are here (not in utils):
  - They are LLM-facing request/response models for structured output.
  - They must remain tightly coupled to prompts and the LLM contract.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from agents.mapping_apply_review.models import InterpretationPlan


class RowAnswerInput(BaseModel):
    """
    One answer text linked to a row via Step 3 question -> row_ids mapping.
    """

    question_id: str = Field(..., description="Step 3 question_id.")
    priority: str = Field(..., description="Question priority (P0/P1/P2).")
    answer_text: str = Field(..., description="Raw answer text entered by the BSA.")


class RowInterpreterInput(BaseModel):
    """
    Per-row context given to the LLM.

    Notes:
      - Keep it small: only the row snapshot + BSA inputs.
      - Target identifiers are included for context, but MUST NOT be modified.
    """

    row_id: str = Field(..., description="Step 2 MappingRow.row_id (stable).")
    target_table_id: str = Field(..., description="Target table id (immutable).")
    target_column_name: str = Field(..., description="Target column name (immutable).")

    current_rule_type: str = Field(..., description="Current rule_type after applying Step 3 patch.")
    current_source_entity_id: Optional[str] = Field(default=None, description="Current source entity id (if any).")
    current_source_fields: List[str] = Field(default_factory=list, description="Current source fields list.")

    bsa_patch_draft: Optional[dict] = Field(
        default=None,
        description="Raw Step 3 row_patch draft (structured). This is what the BSA edited in the table.",
    )
    bsa_feedback_text: Optional[str] = Field(
        default=None,
        description="Free-text feedback from the BSA (reasoning_summary).",
    )

    linked_answers: List[RowAnswerInput] = Field(
        default_factory=list,
        description="Answers linked to this row through Step 3 questions (priority annotated).",
    )


class ReviewInterpreterBatchRequest(BaseModel):
    items: List[RowInterpreterInput] = Field(default_factory=list, description="Rows to interpret in one batch call.")


class ReviewInterpreterBatchOutput(BaseModel):
    """
    Structured output: interpretation plans per row.
    """

    plans: List[InterpretationPlan] = Field(default_factory=list, description="Interpretation plans per row.")
