"""
AG3 (MappingPostProcessorAgent) prompts.

Current behavior:
  - AG3 is deterministic-first and does not call an LLM.

Why keep a prompts file anyway:
  - Maintains the same folder convention as other Step 2 sub-agents.
  - Gives us a ready place to introduce *optional* LLM "wordsmithing" later without
    scattering prompt text around the codebase.
"""

from __future__ import annotations


def get_transformation_text_prompt() -> str:
    """
    Prompt for an optional future LLM call that writes clean transformation text.

    If enabled later, the agent MUST:
      - Return structured output (see `.models.TransformationTextOutput`)
      - Only reference entities/columns provided in the input JSON payload
      - Never invent tables, columns, lookup tables, join keys, or constants
    """
    return (
        "You generate mapping documentation text for a single mapping row.\n"
        "Output MUST be valid JSON matching the provided output schema.\n"
        "\n"
        "Hard guardrails:\n"
        "- Do NOT invent tables/columns.\n"
        "- Only use identifiers provided in the input JSON.\n"
        "- Do NOT output SQL.\n"
        "- Keep the text short, explicit, and review-friendly.\n"
    )

