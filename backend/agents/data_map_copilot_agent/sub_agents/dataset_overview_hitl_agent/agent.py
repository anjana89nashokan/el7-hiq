"""
Dataset overview (Data Profiling chat) HITL agent.

- Classifies QUESTION vs UPDATE for /profiling-chat endpoints
- Applies markdown + structured edits via apply_dataset_overview_modification()
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from google import genai
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types
from pydantic import BaseModel, Field

from config.settings import config

logger = logging.getLogger(__name__)

PROFILING_CHAT_TEXT_RESPONSE_KEY = "_profiling_chat_text_response"
PROFILING_HITL_CONTEXT_SUMMARY_KEY = "_profiling_hitl_context_summary"
_TEXT_RESPONSE_PREVIEW_CHARS = 6000

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_STOP_TERMS = {
    "add", "change", "column", "correct", "data", "field", "fix", "from",
    "modify", "profiling", "report", "response", "set", "table", "the",
    "this", "type", "update", "value", "with",
}
_TEXT_MARKERS = (
    "text_response", "text response", "text summary", "markdown", "overview",
    "narrative", "prose", "wording", "write-up", "write up", "paragraph", "section",
)


class DatasetOverviewHITLResponse(BaseModel):
    message: str = Field(
        description=(
            "For QUESTION: natural language answer. "
            "For UPDATE: must be exactly the string 'UPDATE'."
        )
    )


def extract_text_response(canonical_response: dict[str, Any]) -> str:
    if not isinstance(canonical_response, dict):
        return ""
    text = canonical_response.get("text_response")
    if isinstance(text, str) and text.strip():
        return text
    for key in ("markdown", "summary", "text"):
        candidate = canonical_response.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return ""


def normalize_profiling_chat_response(payload: Any) -> dict[str, Any]:
    """Ensure text_response + tool_response.result[] for chunk editing."""
    if not isinstance(payload, dict):
        return {}
    normalized = json.loads(json.dumps(payload, default=str))
    text = extract_text_response(normalized)
    if text:
        normalized["text_response"] = text
    tool = normalized.get("tool_response")
    if isinstance(tool, dict):
        if "result" not in tool and isinstance(tool.get("all_tables"), list):
            tool["result"] = tool["all_tables"]
    return normalized


def build_profiling_hitl_context_summary(canonical_response: dict[str, Any]) -> str:
    """Context for the coordinator agent (includes markdown preview + structured snippet)."""
    parts: list[str] = []
    text = extract_text_response(canonical_response)
    if text:
        preview = text if len(text) <= _TEXT_RESPONSE_PREVIEW_CHARS else (
            text[:_TEXT_RESPONSE_PREVIEW_CHARS] + "\n... [truncated]"
        )
        parts.append(f"text_response (markdown report):\n{preview}")
    try:
        tool_resp = canonical_response.get("tool_response", canonical_response)
        if isinstance(tool_resp, dict):
            result = tool_resp.get("result", tool_resp.get("all_tables", tool_resp))
            parts.append(
                f"tool_response structured data: {json.dumps(result, default=str)[:4000]}"
            )
    except Exception as exc:
        logger.warning("[DATASET_OVERVIEW_HITL] summary tool section failed: %s", exc)
    return "\n\n".join(parts) if parts else json.dumps(canonical_response, default=str)[:4000]


def build_profiling_chat_state_delta(canonical_response: dict[str, Any]) -> dict[str, str]:
    normalized = normalize_profiling_chat_response(canonical_response)
    return {
        PROFILING_HITL_CONTEXT_SUMMARY_KEY: build_profiling_hitl_context_summary(normalized),
        PROFILING_CHAT_TEXT_RESPONSE_KEY: extract_text_response(normalized),
    }


def _targets_text_response(user_message: str) -> bool:
    lowered = (user_message or "").lower()
    if any(marker in lowered for marker in _TEXT_MARKERS):
        return True
    if "summary" in lowered and not any(
        t in lowered for t in ("table_summary", "table summary", "row count", "total_rows")
    ):
        return True
    return False


def _genai_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return text
    raw = ""
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                raw += part_text
    return raw


def apply_dataset_overview_text_response_edit(
    *,
    user_query: str,
    text_response: str,
    client: genai.Client | None = None,
) -> tuple[str, bool]:
    """Apply user instruction to the markdown text_response string."""
    current = (text_response or "").strip()
    if not current:
        logger.warning("[DATASET_OVERVIEW_HITL][TEXT_EDIT] empty text_response, skipping")
        return text_response or "", False

    client = client or _genai_client()
    prompt = f"""You edit the markdown field text_response of a data profiling report.

USER INSTRUCTION (apply to the markdown below):
{user_query}

CURRENT text_response MARKDOWN:
{current}

Rules:
- Apply ONLY what the user asked
- Return the FULL updated markdown in "value"
- Set "changed" to true if and only if the markdown was modified
- If the instruction does not apply to this markdown, set changed=false and return the original markdown unchanged in "value"

Return JSON only: {{"changed": true|false, "value": "<full markdown string>"}}
"""

    try:
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
    except (TypeError, AttributeError):
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.1),
        )

    parsed = json.loads(_response_text(response) or "{}")
    if not isinstance(parsed, dict) or "value" not in parsed:
        raise ValueError("text_response editor returned invalid JSON")

    edited = parsed.get("value")
    if not isinstance(edited, str):
        edited = str(edited) if edited is not None else current

    changed = edited != current or bool(parsed.get("changed"))
    if changed and edited == current:
        changed = False
    logger.info(
        "[DATASET_OVERVIEW_HITL][TEXT_EDIT] changed=%s | before_len=%d | after_len=%d",
        changed,
        len(current),
        len(edited),
    )
    return edited, changed


def should_run_text_response_edit(user_query: str, text_response: str) -> bool:
    if not (text_response or "").strip():
        return False
    lowered = (user_query or "").lower()
    if _targets_text_response(user_query):
        return True
    return any(
        token in lowered
        for token in ("change", "update", "modify", "rename", "set", "fix", "correct", "add", "remove")
    )


def before_callback(callback_context: CallbackContext):
    state = callback_context.state.to_dict()
    logger.info(
        "[DATASET_OVERVIEW_HITL] BEFORE callback - state keys: %s",
        list(state.keys()),
    )
    if state.get(PROFILING_HITL_CONTEXT_SUMMARY_KEY):
        return None
    if state.get(PROFILING_CHAT_TEXT_RESPONSE_KEY):
        text_response = state[PROFILING_CHAT_TEXT_RESPONSE_KEY]
        preview = (
            text_response
            if len(text_response) <= _TEXT_RESPONSE_PREVIEW_CHARS
            else text_response[:_TEXT_RESPONSE_PREVIEW_CHARS] + "\n... [truncated]"
        )
        callback_context.state[PROFILING_HITL_CONTEXT_SUMMARY_KEY] = (
            f"text_response (markdown report):\n{preview}"
        )
        return None
    logger.warning(
        "[DATASET_OVERVIEW_HITL] Missing %s / %s; router should inject before run",
        PROFILING_HITL_CONTEXT_SUMMARY_KEY,
        PROFILING_CHAT_TEXT_RESPONSE_KEY,
    )
    return None


def after_callback(callback_context: CallbackContext):
    logger.info(
        "[DATASET_OVERVIEW_HITL] AFTER callback - state keys: %s",
        list(callback_context.state.to_dict().keys()),
    )
    return None


dataset_overview_hitl_agent = LlmAgent(
    name="dataset_overview_hitl_agent",
    model=config.AGENT_MODEL,
    instruction=f"""
You are a Data Profiling (dataset overview) Human-in-the-Loop (HITL) coordinator.

You classify intent only; you do NOT edit JSON or markdown yourself.
After you return UPDATE, the API edits the report and refreshes session state.

REPORT CONTEXT — the ONLY source of truth. Answer strictly from the data below;
never invent column names, row/column counts, data types, or values. If something
is not present in this context, say you don't have that information.

Compact context (markdown preview + structured snippet):
{{{PROFILING_HITL_CONTEXT_SUMMARY_KEY}?}}

Full markdown report (text_response):
{{{PROFILING_CHAT_TEXT_RESPONSE_KEY}?}}

Never use relationship_analysis_tool_response, data_anomaly_analysis_tool_response,
or conversation history from prior turns when answering QUESTIONs.

--------------------------------------------------
INTENT
--------------------------------------------------
1) QUESTION — user wants to understand the report
   → answer in `message` using ONLY the REPORT CONTEXT above
2) UPDATE — user wants to change the report (text_response, columns, recommendations, types, etc.)
   → return EXACTLY: UPDATE

Examples of UPDATE:
  • "Change the data type of member_id to STRING"
  • "Update the text summary to mention nulls"
  • "Fix the wording in the overview"

--------------------------------------------------
OUTPUT
--------------------------------------------------
QUESTION: natural language in `message` (not "UPDATE")
UPDATE: message must be exactly UPDATE with no other text
""",
    before_agent_callback=before_callback,
    after_agent_callback=after_callback,
    output_schema=DatasetOverviewHITLResponse,
    output_key="final_dataset_overview_HITL_response",
)

profiling_hitl_agent = dataset_overview_hitl_agent
