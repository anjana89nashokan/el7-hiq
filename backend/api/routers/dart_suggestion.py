"""
DART Suggestion Router

Provides the POST /dart-suggestion endpoint for Phase 2 auto-suggest DART matches.
Follows the same agent-calling pattern as /similarity-check in messages.py.
"""

import logging
import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from google.genai import types
from google.adk import Runner
from utils.adk_runtime import VertexAiSessionService
from google.adk.events import Event, EventActions
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini

from config.settings import config
from api.models import MessageRequest
from agents.data_map_copilot_agent.sub_agents.dart_suggestion_agent.agent import dart_suggestion_agent

logger = logging.getLogger(__name__)

router = APIRouter()

# Summarizer for session compaction (same pattern as messages.py)
_summarization_llm = Gemini(model="gemini-2.5-flash")
_summarizer = LlmEventSummarizer(llm=_summarization_llm)


def _build_dart_suggestion_app(app_name: str) -> App:
    """
    Build an ADK App for the dart_suggestion_agent.
    Falls back to model_construct when the app_name is a Vertex resource name.
    """
    try:
        app = App(
            name=app_name,
            root_agent=dart_suggestion_agent,
            events_compaction_config=EventsCompactionConfig(
                compaction_interval=3,
                overlap_size=1,
                summarizer=_summarizer,
            ),
        )
        return app
    except ValidationError as exc:
        logger.warning(
            "App name '%s' failed validation (%s); constructing without validation.",
            app_name,
            exc,
        )
        return App.model_construct(name=app_name, root_agent=dart_suggestion_agent)


@router.post("/dart-suggestion")
async def dart_suggestion(request: MessageRequest):
    """
    Auto-suggest matching DART tables/columns for source columns.

    Phase 2 endpoint: Takes source columns (from data dictionary step) and returns
    DART table/column suggestions using vector search + IndeMap historical mappings
    + Type 2 SCD detection.

    Args:
        request: MessageRequest with:
            - newMessage: JSON array of source columns
              [{"source_table": "...", "column_name": "...", "column_description": "..."}, ...]
            - dart_database_name: Optional dataset ID override for DART tables
            - sessionId, userId, appName: Standard session fields
    """
    req_id = str(uuid.uuid4())[:8]
    start_time = time.time()

    try:
        req = request.dict()
        logger.info("[dart-suggestion] [%s] Request received", req_id)

        session_id = req.get("sessionId")
        if not session_id:
            raise HTTPException(status_code=400, detail="sessionId is required")

        app_name = req["appName"]
        user_id = req["userId"]

        logger.info("[dart-suggestion] [%s] user=%s session=%s", req_id, user_id, session_id)

        # Set up session service
        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
        )

        # Retrieve existing session — may fail if session expired or Vertex AI unreachable
        try:
            session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception as exc:
            logger.exception("[dart-suggestion] [%s] Failed to retrieve session: %s", req_id, exc)
            return [{
                "text_response": f"Failed to retrieve session '{session_id}': {str(exc)}",
                "suggestions": [],
                "should_update": False,
            }]

        # Build app and runner
        app = _build_dart_suggestion_app(app_name)
        runner = Runner(app=app, session_service=session_service)
        logger.info("[dart-suggestion] [%s] Runner initialized", req_id)

        # Inject dart_dataset_id to session state if provided
        state_delta = {}
        dart_database_name = req.get("dart_database_name")
        if dart_database_name:
            state_delta["dart_dataset_id"] = dart_database_name
            logger.info("[dart-suggestion] [%s] dart_dataset_id override: %s", req_id, dart_database_name)

        if state_delta:
            state_inject_event = Event(
                author="system",
                invocation_id=f"dart-suggestion-state-{uuid.uuid4()}",
                actions=EventActions(state_delta=state_delta),
            )
            await session_service.append_event(session=session, event=state_inject_event)
            logger.info("[dart-suggestion] [%s] State delta injected: %s", req_id, list(state_delta.keys()))

        user_text = req["newMessage"]["parts"][0]["text"]
        msg = types.Content(
            role="user",
            parts=[types.Part(text=user_text + "\n\nRegenerate the full answer from scratch.")],
        )

        response_parts = []
        event_count = 0
        MAX_EVENTS = 1000

        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=msg,
        ):
            event_count += 1
            logger.debug("[dart-suggestion] [%s] Event #%d", req_id, event_count)

            # Safety cap
            if event_count > MAX_EVENTS:
                logger.error("[dart-suggestion] [%s] MAX EVENT LIMIT (%d) EXCEEDED", req_id, MAX_EVENTS)
                return [{
                    "text_response": "Internal error: maximum processing limit reached.",
                    "tool_response": {},
                    "status": 0,
                    "should_update": False,
                }]

            # Check for dart_suggestion_response in state_delta (agent output)
            if (
                hasattr(event, "actions")
                and event.actions
                and hasattr(event.actions, "state_delta")
                and event.actions.state_delta
            ):
                if "dart_suggestion_response" in event.actions.state_delta:
                    logger.info("[dart-suggestion] [%s] Response captured via state_delta at event #%d", req_id, event_count)
                    response_parts.append(event.actions.state_delta["dart_suggestion_response"])
                    break

            # Check for text content in event (fallback)
            if (
                hasattr(event, "content")
                and event.content
                and hasattr(event.content, "parts")
                and event.content.parts
                and len(event.content.parts) > 0
                and getattr(event.content.parts[0], "text", None)
            ):
                text = event.content.parts[0].text
                if isinstance(text, str):
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict) and "suggestions" in parsed:
                            logger.info("[dart-suggestion] [%s] Response captured via text content at event #%d", req_id, event_count)
                            response_parts.append(parsed)
                            break
                    except json.JSONDecodeError:
                        continue

            # Handle malformed function call errors
            if (
                hasattr(event, "error_code")
                and event.error_code
                and event.error_code == "MALFORMED_FUNCTION_CALL"
            ):
                logger.warning("[dart-suggestion] [%s] MALFORMED_FUNCTION_CALL at event #%d", req_id, event_count)
                logger.warning("[dart-suggestion] [%s] Event detail: %s", req_id, event)

        elapsed = time.time() - start_time

        if not response_parts:
            logger.warning("[dart-suggestion] [%s] No response captured after %d event(s) (%.2fs)", req_id, event_count, elapsed)
            return [{
                "text_response": "DART suggestion analysis did not complete. No results found.",
                "suggestions": [],
                "should_update": False,
            }]

        logger.info("[dart-suggestion] [%s] Done — %d result(s), %d event(s), %.2fs", req_id, len(response_parts), event_count, elapsed)
        return response_parts

    except Exception as e:
        elapsed = time.time() - start_time
        logger.exception("[dart-suggestion] [%s] Unhandled error after %.2fs: %s (%s)", req_id, elapsed, e, type(e).__name__)
        return [{
            "text_response": f"DART suggestion failed: {str(e)}",
            "suggestions": [],
            "should_update": False,
        }]
