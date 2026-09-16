"""
agents/hitl/profiling_chat_hitl_agent.py
-----------------------------------------
Human-in-the-loop chat agent for the Data Profiling response produced by /send.

Targets `final_profiling_response` in Vertex AI session state.

Intent classification:
  QUESTION  → answer from existing profiling data, return natural language
  UPDATE    → return exactly "UPDATE" so the endpoint applies the modification
"""

import json
import logging
from pydantic import BaseModel, Field
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from config.settings import config

logger = logging.getLogger(__name__)


# --------------------------------------------------
# OUTPUT SCHEMA  (mirrors ProfilingHITLResponse)
# --------------------------------------------------
class ProfilingChatHITLResponse(BaseModel):
    message: str = Field(
        description=(
            "For QUESTION: natural language answer based on the profiling data. "
            "For UPDATE: must be exactly the string 'UPDATE' with no other text."
        )
    )


# --------------------------------------------------
# CALLBACKS  (mirrors datadict_hitl_agent pattern)
# --------------------------------------------------
def before_callback(callback_context: CallbackContext):
    logger.info(
        "[PROFILING_CHAT_HITL] BEFORE callback — state keys: %s",
        list(callback_context.state.to_dict().keys()),
    )

    state = callback_context.state.to_dict()
    profiling_response = state.get("final_profiling_response")

    if not profiling_response:
        logger.warning(
            "[PROFILING_CHAT_HITL] final_profiling_response not found in session state"
        )
        return None

    # Inject a compact summary into state so the agent can reference it
    # without the full payload bloating the context window.
    try:
        tool_resp = profiling_response.get("tool_response", {})
        results = tool_resp.get("result", [])
        summary_lines = []
        for r in results:
            table = r.get("table_reference", "unknown")
            score = (r.get("data_quality_score") or {}).get("overall_score", "N/A")
            cols = list((r.get("column_analysis") or {}).keys())
            recs = r.get("recommendations", [])
            summary_lines.append(
                f"Table: {table} | DQ Score: {score} | "
                f"Columns: {', '.join(cols[:10])}{'...' if len(cols) > 10 else ''} | "
                f"Recommendations ({len(recs)}): {'; '.join(recs[:3])}{'...' if len(recs) > 3 else ''}"
            )
        summary = "\n".join(summary_lines)
        callback_context.state["_profiling_chat_context_summary"] = summary
        logger.info(
            "[PROFILING_CHAT_HITL] Injected context summary (%d chars)",
            len(summary),
        )
    except Exception as exc:
        logger.warning("[PROFILING_CHAT_HITL] Context summary injection failed: %s", exc)

    return None


def after_callback(callback_context: CallbackContext):
    logger.info(
        "[PROFILING_CHAT_HITL] AFTER callback — state keys: %s",
        list(callback_context.state.to_dict().keys()),
    )
    return None


# --------------------------------------------------
# AGENT
# --------------------------------------------------
profiling_chat_hitl_agent = LlmAgent(
    name="profiling_chat_hitl_agent",
    model=config.AGENT_MODEL,
    instruction="""
You are a Data Profiling Chat Human-in-the-Loop (HITL) agent.

You operate on the profiling report produced for a BigQuery table.
The report is stored in session state under the key: final_profiling_response

It contains:
  - text_response   : markdown summary of the profiling
  - tool_response   : structured JSON with result[], including:
      • table_reference, data_quality_score, recommendations
      • column_analysis (per-column stats: uniqueness, nulls, data type, etc.)
      • default_value_analysis
      • enhanced_analysis (composite key suggestions, PK recommendations)
      • table_summary (total_rows, total_columns)

A compact summary is also available in session state under: _profiling_chat_context_summary

--------------------------------------------------
INTENT CLASSIFICATION (MANDATORY)
--------------------------------------------------
Classify the user message into EXACTLY ONE of:

1) QUESTION (READ-ONLY)
   User wants to understand, explore, or ask about the profiling data.
   Examples:
     • "What is the data quality score?"
     • "Which columns have high null percentage?"
     • "What are the recommended composite keys?"
     • "Why is insurance_member_id not a primary key?"
     • "Summarise the recommendations"
     • "What does the enhanced analysis say?"

2) UPDATE (EDIT REQUEST)
   User wants to CHANGE, MODIFY, RENAME, or UPDATE something in the response.
   Trigger words: change, update, modify, rename, set, fix, correct, remove, add
   Examples:
     • "Change the recommendation for gender column"
     • "Update the text summary to mention the null issue"
     • "Rename ibc_id to ibc_member_id in the report"
     • "Fix the PK recommendation"
     • "Remove the foreign key flag from event_date"
     • "Change the data type of member_id to STRING"
     • "Update the data type of event_date column to DATE"
     • "Set the data type of age to INTEGER"

--------------------------------------------------
OUTPUT RULES (CRITICAL)
--------------------------------------------------

### CASE 1: QUESTION
- Answer clearly and concisely using ONLY data from final_profiling_response
- Reference specific column names, scores, and values from the data
- Return your answer in the `message` field
- `message` MUST be natural language
- `message` MUST NOT be the string "UPDATE"

### CASE 2: UPDATE
- DO NOT answer the question
- DO NOT explain what you will do
- DO NOT include any markdown or extra text
- Return EXACTLY this string in `message`:

  UPDATE

(no quotes, no punctuation, no whitespace around it)

--------------------------------------------------
STRICT GUARDRAILS
--------------------------------------------------
- NEVER modify JSON or session state yourself
- NEVER invent column stats, scores, or values not in the profiling data
- NEVER mix QUESTION and UPDATE behavior
- If the request is ambiguous, ask for clarification in `message`
""",
    before_agent_callback=before_callback,
    after_agent_callback=after_callback,
    output_schema=ProfilingChatHITLResponse,
    output_key="final_profiling_chat_HITL_response",
)
