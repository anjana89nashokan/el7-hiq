"""
Data Dictionary Modification Tool - Interactive editing via chat

Allows users to modify generated data dictionaries through natural language requests.
Works with both vendor DD and profiling-generated DD.

Part of Plan 2 - Interactive Data Dictionary Enhancement
Zero impact on existing generation flow
"""

import json
import logging
from typing import Dict, Any, List
from google import genai
from google.genai import types as genai_types
from google.adk.tools import ToolContext
from config.settings import config

# Reuse existing formatting functions
from utils.datadict_batched import generate_datadict_markdown, generate_datadict_summary

logger = logging.getLogger(__name__)


def modify_data_dictionary_tool(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Modify existing data dictionary based on user's natural language request.

    **Features:**
    - Reads current DD from session state
    - Interprets modification request (change description, update business name, etc.)
    - Applies changes to specific fields
    - Returns updated markdown + JSON
    - Saves updated version back to session state

    **Args:**
        tool_context: ADK tool context (provides access to session state and user message)

    **Returns:**
        Dict with text_response and tool_response (updated data dictionary)
    """

    logger.info("[datadict_modify] Tool called - reading current data dictionary from session...")

    try:
        # Read current data dictionary from session state
        session = tool_context.session
        logger.info(f"[datadict_modify] Session state keys: {list(session.state.keys())}")

        current_dd = session.state.get("final_data_dict_response", {})
        logger.info(f"[datadict_modify] current_dd exists: {bool(current_dd)}")
        if current_dd:
            logger.info(f"[datadict_modify] current_dd keys: {list(current_dd.keys()) if isinstance(current_dd, dict) else 'not a dict'}")

        if not current_dd:
            logger.error("[datadict_modify] No data dictionary found in session state")
            return {
                "text_response": "Error: No data dictionary found. Please generate one first.",
                "tool_response": {"error": "final_data_dict_response not found in session state"}
            }

        # Extract current fields from tool_response
        current_fields = current_dd.get("tool_response", {}).get("result", [])

        if not current_fields:
            logger.error("[datadict_modify] No fields found in data dictionary")
            return {
                "text_response": "Error: Data dictionary is empty.",
                "tool_response": {"error": "No fields in data dictionary"}
            }

        total_fields = len(current_fields)
        logger.info(f"[datadict_modify] Found {total_fields} fields in current DD")

        # Log first few field names for debugging
        if current_fields:
            sample_fields = [f.get('field_name', 'N/A') for f in current_fields[:5]]
            logger.info(f"[datadict_modify] Sample field names: {sample_fields}")

        # Get user's modification request from recent messages
        # The user message is in the session history
        user_request = _extract_user_request(tool_context)
        logger.info(f"[datadict_modify] User request: {user_request[:200]}...")

        # ============================================
        # CALL LLM TO INTERPRET AND APPLY MODIFICATIONS
        # ============================================
        logger.info("[datadict_modify] Calling LLM to interpret modification request...")

        client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )

        # Build modification prompt with improved field matching
        modification_prompt = f"""You are a data dictionary editor. Apply the user's requested changes to the data dictionary.

**Current Data Dictionary:**
{json.dumps(current_fields, indent=2)}

**User's Modification Request:**
{user_request}

**Instructions:**
1. **Parse the user's request** to understand:
   - Which field(s) to modify (use FUZZY MATCHING - e.g., "livingo id" matches "livongo_id")
   - What attribute(s) to change (description, business_name, primary_key, foreign_key, nullable, etc.)
   - What the new value should be

2. **Field Name Matching Rules:**
   - Match by field_name OR business_name
   - Ignore case, spaces, underscores (e.g., "livingo id" = "livongo_id" = "LIVONGO_ID")
   - If multiple matches, apply to all
   - If no match found, report error in modifications_applied

3. **Attribute Mapping:**
   - "primary key" / "pk" → primary_key attribute
   - "foreign key" / "fk" → foreign_key attribute
   - "description" → field_description attribute
   - "business name" / "name" → business_name attribute
   - "nullable" / "required" → nullable attribute
   - "set as", "mark as", "set to" → means set the attribute to "Yes" or the specified value

4. **Value Interpretation:**
   - "yes", "true", "mark yes", "set yes" → "Yes"
   - "no", "false", "mark no", "set no" → "No"
   - For descriptions: use the exact text provided

5. **Apply Changes:**
   - Modify ONLY the specified fields and attributes
   - Return the COMPLETE updated data dictionary (all {total_fields} fields)
   - Preserve ALL other fields and attributes exactly as-is

**Examples:**
- "livingo id - set as primary key and mark yes" → Find field matching "livingo_id" or "livongo_id", set primary_key = "Yes"
- "Change description of event_date to 'Event timestamp'" → Find "event_date", set field_description = "Event timestamp"
- "Mark birth_date as nullable" → Find "birth_date", set nullable = "Yes"

**Output Format:**
Return JSON with this structure:
{{
  "modifications_applied": [
    {{
      "field_name": "actual_field_name_from_dd",
      "changes": "Set primary_key to 'Yes'"
    }}
  ],
  "updated_fields": [
    {{
      "file_name": "...",
      "field_name": "...",
      "business_name": "...",
      "data_type": "...",
      "length": 0,
      "precision": 0,
      "format": "-",
      "nullable": "Yes",
      "default_value": "-",
      "primary_key": "No",
      "foreign_key": "No",
      "field_description": "..."
    }}
  ]
}}

**CRITICAL:** You MUST apply the modifications if a matching field is found. Do NOT return "No modifications were requested" unless the field truly doesn't exist.

Generate the updated data dictionary now:"""

        # ============================================
        # RETRY LOGIC - Handle transient network errors
        # ============================================
        max_retries = getattr(config, 'LLM_MAX_RETRIES', 3)
        retry_base_delay = getattr(config, 'LLM_RETRY_BASE_DELAY', 2.0)
        retry_max_delay = getattr(config, 'LLM_RETRY_MAX_DELAY', 30.0)

        response = None
        last_error = None

        for attempt in range(max_retries):
            try:
                logger.info(f"[datadict_modify] LLM call attempt {attempt + 1}/{max_retries}")

                response = client.models.generate_content(
                    model=config.AGENT_MODEL,
                    contents=modification_prompt,
                    config=genai_types.GenerateContentConfig(
                        temperature=0.1,  # Lower temp for more deterministic output (from llm_helper)
                        max_output_tokens=50000,  # Support large data dictionaries (from llm_helper)
                        response_mime_type="application/json"
                    )
                )

                # If successful, break out of retry loop
                logger.info(f"[datadict_modify] LLM call succeeded on attempt {attempt + 1}")
                break

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Check if it's a retryable error (connection issues, timeouts, rate limits)
                is_retryable = any(err in error_str for err in [
                    'connection', 'timeout', 'aborted', 'reset',
                    'remote end closed', 'remotedisconnected',
                    '503', '429', 'resource exhausted', 'unavailable',
                    'deadline exceeded', 'proxy'
                ])

                if is_retryable and attempt < max_retries - 1:
                    # Calculate exponential backoff delay
                    wait_time = min(retry_base_delay * (2 ** attempt), retry_max_delay)
                    logger.warning(f"[datadict_modify] Retryable error on attempt {attempt + 1}/{max_retries}: {str(e)}")
                    logger.info(f"[datadict_modify] Retrying in {wait_time:.1f} seconds...")

                    import time
                    time.sleep(wait_time)
                else:
                    # Non-retryable error or max retries reached
                    if attempt == max_retries - 1:
                        logger.error(f"[datadict_modify] Failed after {max_retries} attempts. Last error: {str(e)}")
                    else:
                        logger.error(f"[datadict_modify] Non-retryable error: {str(e)}")
                    raise

        # Safety check - should never happen due to raise above
        if response is None:
            raise Exception(f"LLM call failed after {max_retries} attempts. Last error: {last_error}")

        # Parse LLM response
        result = json.loads(response.text)

        # Handle two possible formats:
        # 1. {"updated_fields": [...], "modifications_applied": [...]}
        # 2. [...]  (just the array directly)
        if isinstance(result, list):
            logger.info("[datadict_modify] LLM returned array directly (no wrapper)")
            updated_fields = result
            modifications = []  # Can't extract modifications from this format
        elif isinstance(result, dict):
            logger.info("[datadict_modify] LLM returned dict with metadata")
            updated_fields = result.get("updated_fields", [])
            modifications = result.get("modifications_applied", [])
        else:
            logger.error(f"[datadict_modify] Unexpected result type: {type(result)}")
            return {
                "text_response": "Error: LLM returned unexpected format",
                "tool_response": {"error": f"Unexpected result type: {type(result)}"}
            }

        logger.info(f"[datadict_modify] Received {len(updated_fields)} updated fields")
        if modifications:
            logger.info(f"[datadict_modify] LLM applied {len(modifications)} modifications")
            for mod in modifications:
                logger.info(f"  - {mod.get('field_name')}: {mod.get('changes')}")

            # Check if LLM said "no modifications"
            if len(modifications) == 1:
                first_mod = modifications[0]
                changes_text = str(first_mod.get('changes', '')).lower()
                if any(phrase in changes_text for phrase in ['no modifications', 'not found', 'unchanged', 'no changes']):
                    logger.warning(f"[datadict_modify] LLM reported no modifications applied: {first_mod.get('changes')}")
                    logger.warning(f"[datadict_modify] User request was: {user_request}")
                    logger.warning(f"[datadict_modify] This might be a field name mismatch issue")
        else:
            logger.info("[datadict_modify] No explicit modification list (direct array format)")

        if len(updated_fields) != total_fields:
            logger.warning(f"[datadict_modify] Field count mismatch: expected {total_fields}, got {len(updated_fields)}")

        # ============================================
        # GENERATE UPDATED OUTPUT
        # ============================================
        logger.info("[datadict_modify] Generating updated markdown and summary...")

        # Generate markdown (reuse existing function)
        markdown = generate_datadict_markdown(updated_fields)

        # Generate summary (reuse existing function)
        summary = generate_datadict_summary(
            updated_fields,
            total_batches=1,  # Not relevant for modifications
            total_time_seconds=0  # Not relevant for modifications
        )

        # Build modification notice
        mod_notice = "✅ **Data Dictionary Updated**\n\n"
        if modifications:
            mod_notice += "**Changes Applied:**\n"
            for mod in modifications:
                mod_notice += f"- {mod.get('field_name')}: {mod.get('changes')}\n"
            mod_notice += f"\n---\n\n"
        else:
            mod_notice += "**Your requested modifications have been applied.**\n\n---\n\n"

        final_text = mod_notice + summary + "\n\n" + markdown

        # Create updated response object (matching original format)
        updated_dd_response = {
            "text_response": final_text,
            "tool_response": {
                "result": updated_fields,
                "source": current_dd.get("tool_response", {}).get("source", "modified"),
                "total_fields": len(updated_fields),
                "modifications_applied": modifications
            }
        }

        # Update session state with modified DD
        session.state["final_data_dict_response"] = updated_dd_response
        logger.info("[datadict_modify] Updated session state with modified data dictionary")

        return updated_dd_response

    except Exception as e:
        logger.error(f"[datadict_modify] Error: {e}")
        import traceback
        traceback.print_exc()

        return {
            "text_response": f"Error modifying data dictionary: {str(e)}",
            "tool_response": {
                "error": str(e),
                "error_details": traceback.format_exc()
            }
        }


def _extract_user_request(tool_context: ToolContext) -> str:
    """
    Extract the user's modification request from tool context.

    The ADK framework passes the conversation history through tool_context.
    We extract the most recent user message.

    Args:
        tool_context: ADK tool context

    Returns:
        User's request text
    """
    try:
        session = tool_context.session

        # Debug: Log available attributes
        logger.info(f"[datadict_modify] tool_context attributes: {dir(tool_context)}")
        logger.info(f"[datadict_modify] session attributes: {dir(session)}")

        # Try to get from tool_args if agent passes it
        if hasattr(tool_context, 'tool_args') and 'user_request' in tool_context.tool_args:
            logger.info("[datadict_modify] Found user request in tool_args")
            return tool_context.tool_args['user_request']

        # Try accessing from session.events (ADK stores events, not history)
        if hasattr(session, 'events') and session.events:
            logger.info(f"[datadict_modify] Checking session.events ({len(session.events)} events)")
            # Events are chronological, so iterate backwards
            for event in reversed(session.events):
                if hasattr(event, 'author') and event.author == 'user':
                    if hasattr(event, 'content') and event.content:
                        if hasattr(event.content, 'parts') and event.content.parts:
                            for part in event.content.parts:
                                if hasattr(part, 'text') and part.text:
                                    logger.info(f"[datadict_modify] Found user message in session.events: {part.text[:100]}")
                                    return part.text

        # Try accessing conversation history from tool_context
        if hasattr(tool_context, 'messages') and tool_context.messages:
            logger.info(f"[datadict_modify] Checking tool_context.messages ({len(tool_context.messages)} messages)")
            # Get last user message
            for msg in reversed(tool_context.messages):
                if hasattr(msg, 'role') and msg.role == 'user':
                    if hasattr(msg, 'parts') and msg.parts:
                        for part in msg.parts:
                            if hasattr(part, 'text') and part.text:
                                logger.info(f"[datadict_modify] Found user message in messages: {part.text[:100]}")
                                return part.text

        # Fallback: Check session history (old approach)
        if hasattr(session, 'history') and session.history:
            logger.info(f"[datadict_modify] Checking session.history ({len(session.history)} items)")
            for msg in reversed(session.history):
                if hasattr(msg, 'role') and msg.role == 'user':
                    if hasattr(msg, 'parts') and msg.parts:
                        for part in msg.parts:
                            if hasattr(part, 'text') and part.text:
                                logger.info(f"[datadict_modify] Found user message in history: {part.text[:100]}")
                                return part.text

        # Last resort: generic message
        logger.warning("[datadict_modify] Could not extract specific user request from context")
        logger.warning("[datadict_modify] Tried: tool_args, session.events, tool_context.messages, session.history")
        return "Apply the requested modifications to the data dictionary"

    except Exception as e:
        logger.error(f"[datadict_modify] Error extracting user request: {e}")
        import traceback
        traceback.print_exc()
        return "Modify the data dictionary as requested"
