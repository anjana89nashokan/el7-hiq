"""
Step 2 - Subagent #2 (JoinAndFilterAgent) internal models.

These models are internal to AG2 LLM calls and are NOT part of Step2State.
They exist only to enforce structured outputs for join path selection.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class JoinPathSelectionOutput(BaseModel):
    """
    Structured output for AG2 join-path selector.

    Contract:
      - `selected_path_id` must be one of the provided path options or null.
      - The model must never invent tables/columns/keys.
      - Deterministic validation applies after this output.
    """

    selected_path_id: str | None = Field(
        default=None,
        description="Chosen path_id from provided options, or null when unresolved.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence for selected path (0..1).",
    )
    needs_review: bool = Field(
        default=False,
        description="True when path remains ambiguous/unsafe and should be reviewed.",
    )
    reasoning_summary: str = Field(
        default="",
        description="Short rationale for the choice.",
    )
    rejection_reason: str | None = Field(
        default=None,
        description="Reason for returning null when no path is selected.",
    )


__all__ = ["JoinPathSelectionOutput"]

