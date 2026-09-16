from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from pydantic import BaseModel, Field
from config.settings import config
from google.adk.tools import FunctionTool

# -----------------------------
# OUTPUT SCHEMA
# -----------------------------

class ProfilingHITLResponse(BaseModel):
    message: str = Field(
        description=(
            "For QUESTION: natural language answer. "
            "For UPDATE: must be exactly the string 'UPDATE'."
        )
    )


# -----------------------------
# CALLBACKS
# -----------------------------
def before_callback(callback_context: CallbackContext):
    print("Executing BEFORE callback for profiling_HITL_agent.")
    print("Input state for profiling_hitl_agent:", callback_context.state.to_dict().keys())
    return None


def after_callback(callback_context: CallbackContext):
    print("Executing AFTER callback for profiling_HITL_agent.")
    print("Output state for profiling_hitl_agent:", callback_context.state.to_dict().keys())
    return None



import json
import logging
from google import genai
from google.genai import types
from config.settings import config

logger = logging.getLogger(__name__)


def apply_profiling_hitl_modification(
    *,
    user_query: str,
    tool_response: dict,
) -> dict:
    """
    Uses a single LLM call to apply a MINIMAL user-requested modification
    to an existing profiling tool response.

    - Returns FULL modified tool response
    - Makes NO other changes
    """

    logger.info("[HITL_MODIFIER] Starting profiling HITL modification")
    logger.warning(f"[HITL_MODIFIER] INITIAL/INPUT LENGTH OF TOOL RESPONSE: {len(tool_response)}")
    logger.info("[HITL_MODIFIER] User query: %s", user_query)

    # --------------------------------------------------
    # Build strict prompt
    # --------------------------------------------------
    prompt = f"""
You are a STRICT JSON editor.

You are given:
1) USER INSTRUCTION
2) PROFILING TOOL RESPONSE (JSON)

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

PROFILING TOOL RESPONSE (JSON):
{json.dumps(tool_response, indent=2)}
"""

    # --------------------------------------------------
    # Initialize Gemini / Vertex client
    # --------------------------------------------------
    client = genai.Client(
        vertexai=True,
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )

    model = config.AGENT_MODEL

    # --------------------------------------------------
    # Call LLM
    # --------------------------------------------------
    try:
        logger.info("[HITL_MODIFIER] Calling LLM with JSON response mode")

        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )

    except (TypeError, AttributeError) as e:
        logger.warning(
            "[HITL_MODIFIER] response_mime_type not supported, fallback mode: %s",
            str(e),
        )

        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
            ),
        )

    # --------------------------------------------------
    # Extract text safely
    # --------------------------------------------------
    raw_text = ""
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                raw_text += part.text

    logger.debug("[HITL_MODIFIER] Raw LLM output length: %d", len(raw_text))

    # --------------------------------------------------
    # Parse JSON
    # --------------------------------------------------
    try:
        modified_tool_response = json.loads(raw_text)
        logger.warning(f"[HITL_MODIFIER] FINAL LENGTH OF TOOL RESPONSE modified_tool_response: {len(tool_response)}")
    except json.JSONDecodeError as e:
        logger.error("[HITL_MODIFIER] Invalid JSON returned by LLM")
        logger.debug("[HITL_MODIFIER] Raw output:\n%s", raw_text)
        raise RuntimeError("LLM did not return valid JSON") from e

    logger.info("[HITL_MODIFIER] Profiling tool response successfully modified")

    return modified_tool_response



# -----------------------------
# PROFILING HITL AGENT
# -----------------------------
profiling_hitl_agent = LlmAgent(
    name="profiling_hitl_agent",
    model=config.AGENT_MODEL,
    instruction="""
You are a Profiling Human-in-the-Loop (HITL) coordinator.

You do NOT modify profiling outputs.
You do NOT re-run profiling or anomaly detection.

--------------------------------------------------
INPUTS
--------------------------------------------------
- A user instruction
- EXACTLY ONE profiling output:
    • relationship_analysis_tool_response
    OR
    • data_anomaly_analysis_tool_response

--------------------------------------------------
INTENT CLASSIFICATION (MANDATORY)
--------------------------------------------------
Classify the user intent into ONE of the following:

1) QUESTION (READ-ONLY)
   - The user is ONLY asking to understand existing profiling output
   - Examples:
       • "What anomalies were detected?"
       • "What are the composite keys?"
       • "Is this relationship one-to-many?"

2) UPDATE (EDIT REQUEST)
   - The user is asking to MODIFY existing output. using rename, update, change, modify
   - Examples:
       • "Rename insurance_member_id to insuranceEE_member_id"
       • "Change severity to HIGH"
       • "Update cardinality description"

--------------------------------------------------
OUTPUT RULES (CRITICAL)
--------------------------------------------------

### CASE 1: QUESTION
- Answer clearly using the provided profiling output
- Return the answer in `message`
- `message` MUST be natural language
- `message` MUST NOT be "UPDATE"

### CASE 2: UPDATE
- DO NOT answer
- DO NOT explain
- DO NOT summarize
- DO NOT include markdown
- Return EXACTLY:
  
  UPDATE

(no quotes, no whitespace, no punctuation)

--------------------------------------------------
STRICT GUARDRAILS (NON-NEGOTIABLE)
--------------------------------------------------
- NEVER modify JSON
- NEVER infer new relationships or anomalies
- NEVER touch state
- NEVER return explanations for UPDATE
- NEVER mix QUESTION and UPDATE behavior

If the request is ambiguous:
- Ask for clarification in `message`

""",
    before_agent_callback=before_callback,
    after_agent_callback=after_callback,
    output_schema=ProfilingHITLResponse,
    output_key="final_profiling_HITL_response",
)
