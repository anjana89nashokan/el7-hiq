import json
import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from config.settings import config

logger = logging.getLogger(__name__)


class SimilarityHITLResponse(BaseModel):
    message: str = Field(
        description=(
            "For QUESTION: natural language answer. "
            "For UPDATE: must be exactly the string 'UPDATE'."
        )
    )


def before_callback(callback_context: CallbackContext):
    state = callback_context.state.to_dict()
    logger.info("[SIMILARITY_HITL] BEFORE callback - state keys: %s", list(state.keys()))

    payload = state.get("final_similarity_response")
    if isinstance(payload, dict):
        try:
            callback_context.state["_similarity_hitl_context_summary"] = (
                json.dumps(payload, default=str)[:4000]
            )
        except Exception as exc:
            logger.warning("[SIMILARITY_HITL] Failed to summarize final_similarity_response: %s", exc)

    return None


def after_callback(callback_context: CallbackContext):
    logger.info("[SIMILARITY_HITL] AFTER callback - state keys: %s", list(callback_context.state.to_dict().keys()))
    return None


def apply_similarity_hitl_modification(*, user_query: str, tool_response: dict) -> dict:
    """
    Uses a single LLM call to apply a MINIMAL user-requested modification
    to an existing similarity tool_response.
    Returns the FULL modified tool_response.
    """
    logger.info("[SIMILARITY_MODIFIER] Starting modification")
    logger.info("[SIMILARITY_MODIFIER] User query: %s", user_query)

    prompt = f"""
You are a STRICT JSON editor.

You are given:
1) USER INSTRUCTION
2) SIMILARITY TOOL RESPONSE (JSON)

Your task:
- Modify the JSON ONLY if the user explicitly asks for a change
- Apply the SMALLEST POSSIBLE change
- Return the FULL JSON after modification

STRICT RULES (NON-NEGOTIABLE):
- Do NOT add new keys
- Do NOT remove existing keys
- Do NOT reorder arrays
- Do NOT reword descriptions unless explicitly asked
- Do NOT infer or fix data
- If the instruction does NOT require a change, return the JSON EXACTLY AS-IS

OUTPUT REQUIREMENTS:
- Output ONLY valid JSON
- No markdown
- No explanations
- No extra text

USER INSTRUCTION:
{user_query}

SIMILARITY TOOL RESPONSE (JSON):
{json.dumps(tool_response, indent=2)}
"""

    client = genai.Client(
        vertexai=True,
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )

    try:
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
    except (TypeError, AttributeError) as e:
        logger.warning("[SIMILARITY_MODIFIER] response_mime_type not supported, fallback: %s", e)
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.1),
        )

    raw_text = ""
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                raw_text += part.text

    try:
        modified = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.error("[SIMILARITY_MODIFIER] Invalid JSON returned by LLM")
        raise RuntimeError("LLM did not return valid JSON") from e

    logger.info("[SIMILARITY_MODIFIER] Modification complete")
    return modified


def _tool_responses_equal(left: dict, right: dict) -> bool:
    try:
        return json.dumps(left, sort_keys=True, default=str) == json.dumps(
            right, sort_keys=True, default=str
        )
    except (TypeError, ValueError):
        return left == right


def regenerate_similarity_text_response(
    tool_response: dict,
    *,
    existing_text: str = "",
) -> str:
    """
    Build markdown summary from an updated similarity tool_response.
    Keeps output aligned with smart_similarity_agent executor format.
    """
    if not isinstance(tool_response, dict) or not tool_response:
        return existing_text or ""

    matches = tool_response.get("potential_matches") or []
    stats = tool_response.get("summary_statistics") or {}

    if not matches and not stats:
        return existing_text or ""

    high = stats.get("high_confidence_matches")
    medium = stats.get("medium_confidence_matches")
    low = stats.get("low_confidence_matches")

    if high is None:
        high = sum(1 for m in matches if m.get("confidence") == "HIGH")
    if medium is None:
        medium = sum(1 for m in matches if m.get("confidence") == "MEDIUM")
    if low is None:
        low = sum(1 for m in matches if m.get("confidence") == "LOW")

    total = stats.get("total_matches_found", len(matches))

    lines = [
        "# Similarity Analysis Results",
        "",
        "## Summary",
        f"- Total Matches: {total}",
        f"- High Confidence: {high}",
        f"- Medium Confidence: {medium}",
        f"- Low Confidence: {low}",
        "",
        "## High-Confidence Matches",
    ]

    high_matches = [m for m in matches if m.get("confidence") == "HIGH"]
    if not high_matches:
        high_matches = matches[:10]

    for index, match in enumerate(high_matches[:10], start=1):
        rank = match.get("rank", index)
        source_col = match.get("source_column_name", "—")
        dart_field = match.get("dart_field_name") or match.get("dart_column_name", "—")
        dart_table = match.get("dart_table_name", "—")
        filename = match.get("filename", "")
        header_sim = match.get("header_name_similarity", match.get("semantic_score"))
        overlap = match.get("data_overlap_similarity", match.get("data_overlap_percent"))
        combined = match.get("combined_score")
        confidence = match.get("confidence", "—")

        lines.extend(
            [
                "",
                f"### {rank}. `{source_col}` → `{dart_field}`",
                f"- **DART Table:** `{dart_table}`",
                f"- **DART Column:** `{dart_field}`",
            ]
        )
        if filename:
            lines.append(f"- **Source Table:** `{filename}`")
        lines.append(f"- **Source Column:** `{source_col}`")
        lines.append("")
        lines.append("**Reasoning:**")
        if header_sim is not None:
            lines.append(
                f"- **Header Similarity ({header_sim}%):** Column names similarity score."
            )
        if overlap is not None:
            lines.append(f"- **Data Overlap ({overlap}%):** Value overlap between columns.")
        if combined is not None:
            lines.append(f"- **Combined Score ({combined}%):** Overall match score.")
        lines.append(f"- **Confidence:** {confidence}")

    return "\n".join(lines)


def apply_similarity_hitl_text_modification(*, user_query: str, text_response: str) -> str:
    """
    Applies a minimal user-requested edit to the markdown text_response only.
    """
    if not (text_response or "").strip():
        return text_response

    prompt = f"""
You are a STRICT markdown editor for similarity analysis reports.

You are given:
1) USER INSTRUCTION
2) EXISTING MARKDOWN REPORT

Your task:
- Modify the markdown ONLY if the user explicitly asks for a narrative/reasoning/text change
- Apply the SMALLEST POSSIBLE change
- Preserve overall structure (headings, lists, match sections)
- If the instruction targets structured match data (confidence, scores, ranks), return the markdown EXACTLY AS-IS

OUTPUT REQUIREMENTS:
- Output ONLY the full markdown document
- No JSON wrappers
- No extra commentary

USER INSTRUCTION:
{user_query}

EXISTING MARKDOWN:
{text_response}
"""

    client = genai.Client(
        vertexai=True,
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )

    try:
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.1),
        )
    except Exception as exc:
        logger.warning("[SIMILARITY_TEXT_MODIFIER] LLM call failed: %s", exc)
        return text_response

    raw_text = ""
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                raw_text += part.text

    modified = raw_text.strip()
    return modified or text_response


def apply_similarity_hitl_full_update(
    *,
    user_query: str,
    similarity_response: dict,
) -> dict[str, Any]:
    """
    Apply HITL edits to both tool_response and text_response.

    - Structured match edits -> tool_response via JSON editor, then regenerate markdown
    - Text-only edits -> markdown editor when tool JSON unchanged
    """
    tool_response = (
        similarity_response.get("tool_response", {})
        if isinstance(similarity_response, dict)
        else {}
    )
    text_response = (
        similarity_response.get("text_response", "")
        if isinstance(similarity_response, dict)
        else ""
    )

    if not isinstance(tool_response, dict):
        tool_response = {}

    modified_tool_response = apply_similarity_hitl_modification(
        user_query=user_query,
        tool_response=tool_response,
    )

    if not _tool_responses_equal(tool_response, modified_tool_response):
        text_response = regenerate_similarity_text_response(
            modified_tool_response,
            existing_text=text_response,
        )
        logger.info(
            "[SIMILARITY_HITL] tool_response changed; regenerated text_response (%d chars)",
            len(text_response),
        )
    else:
        modified_text = apply_similarity_hitl_text_modification(
            user_query=user_query,
            text_response=text_response,
        )
        if modified_text.strip() != (text_response or "").strip():
            text_response = modified_text
            logger.info("[SIMILARITY_HITL] text_response updated via markdown editor")

    return {
        "text_response": text_response,
        "tool_response": modified_tool_response,
    }


similarity_hitl_agent = LlmAgent(
    name="similarity_hitl_agent",
    model=config.AGENT_MODEL,
    instruction="""
You are a Similarity Human-in-the-Loop (HITL) coordinator.

You do NOT modify similarity outputs.
You do NOT re-run similarity analysis.
The full similarity output is already stored in Vertex AI session state.
Use the compact read-only summary at `_similarity_hitl_context_summary` for
QUESTION answers. For UPDATE requests, return UPDATE and let the endpoint
apply the edit.

--------------------------------------------------
INPUTS
--------------------------------------------------
- A user instruction
- Relevant similarity state: final_similarity_response

--------------------------------------------------
INTENT CLASSIFICATION (MANDATORY)
--------------------------------------------------
Classify the user intent into ONE of the following:

1) QUESTION (READ-ONLY)
   - The user is ONLY asking to understand existing similarity output
   - Examples:
       • "What is the confidence level for this match?"
       • "Which column has the highest overlap?"
       • "What does the combined score mean?"

2) UPDATE (EDIT REQUEST)
   - The user is asking to MODIFY existing output using rename, update, change, modify
   - Examples:
       • "Change the confidence of this match to HIGH"
       • "Update the match reasoning for column X"
       • "Rename source_column_name to member_id"

--------------------------------------------------
OUTPUT RULES (CRITICAL)
--------------------------------------------------

### CASE 1: QUESTION
- Answer clearly using the provided similarity output
- Return the answer in `message`
- `message` MUST be natural language
- `message` MUST NOT be "UPDATE"

### CASE 2: UPDATE
- DO NOT answer
- DO NOT explain
- Return EXACTLY:

  UPDATE

(no quotes, no whitespace, no punctuation)

--------------------------------------------------
STRICT GUARDRAILS
--------------------------------------------------
- NEVER modify JSON
- NEVER infer new matches
- NEVER touch state
- NEVER return explanations for UPDATE
- NEVER mix QUESTION and UPDATE behavior

If the request is ambiguous, ask for clarification in `message`.
""",
    before_agent_callback=before_callback,
    after_agent_callback=after_callback,
    output_schema=SimilarityHITLResponse,
    output_key="final_similarity_HITL_response",
)
