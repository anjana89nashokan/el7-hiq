"""
AG3 (MappingPostProcessorAgent) internal schemas.

Why a separate `models.py` here:
  - Keeps AG3-specific helper payloads close to the agent implementation.
  - Avoids polluting the global Step 2 schema in `agents/mapping_generation/models.py`.

What these are used for:
  - Today (v1): AG3 is deterministic and does NOT require LLM calls.
  - Future (optional): If we decide to use an LLM to "polish" wording for
    `transformation_rules_text` / `special_considerations_text`, these models become
    the structured output contract for that LLM call (so the output remains safe).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TransformationTextOutput(BaseModel):
    """
    Structured output for an optional future LLM call in AG3.

    Used by:
      - AG3 (MappingPostProcessorAgent)

    Why this exists:
      - Ensures any LLM-generated text is returned in a fixed JSON shape.
      - Lets us keep strict post-validation in Python even if we later add an LLM.
    """

    transformation_rules_text: str = Field(
        ...,
        description=(
            "Documentation-first mapping logic (NOT SQL). Must only reference entities/columns "
            "provided in the input payload. No new tables/columns."
        ),
    )
    special_considerations_text: str | None = Field(
        None,
        description="Optional additional notes (only when actionable).",
    )

