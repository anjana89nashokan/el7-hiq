import logging
from pydoc import text
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import asyncio
import io
import json
import os
import re
import uuid

import pandas as pd
from pydantic import BaseModel, ValidationError
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
import requests
# from api.models import MessageRequest,QARequest, SessionCreateRequest
from google.genai import types
from google.genai.errors import ServerError
from google import genai
from agents.data_map_copilot_agent.agent import root_agent
from utils.profiling_analysis import extract_column_analysis
from agents.data_map_copilot_agent.sub_agents.datadict_agent.agent import data_dict_agent
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.agent import metadata_fill_agent
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.tools.bq_tools import (
    _build_filespecs_rows,
    create_metadata_and_filespecs_tables,
    overwrite_chunk_to_bq,
    overwrite_filespecs_in_bq,
)
from agents.qa_agent.agent import qa_root_agent
from google.adk import Runner
from utils.adk_runtime import VertexAiSessionService
from config.settings import config, llm_is_configured
from google.adk.apps import App
from google.adk.sessions import Session
from pathlib import Path
import json
from utils.llm_helper import GoogleGeminiClient
from utils.bg_query_utils import get_table, create_data_dictionary_table, get_bigquery_client
from utils.profiling_functions_batched import ensure_profiling_result_shape
from google.adk.events import Event, EventActions
from api.routers.sessions import retrieve_session, create_session
from utils.rate_limiter import RateLimiter
from utils.profiling_artifact_store import (
    load_profiling_session_context,
    load_profiling_chat_response,
    load_resume_json_artifact,
    persist_profiling_chat_response,
    update_profiling_session_context,
)

from agents.data_map_copilot_agent.sub_agents.datadict_hitl_agent.agent import data_dict_hitl_agent
from agents.data_map_copilot_agent.sub_agents.metadata_fill_hitl_agent.agent import metadata_fill_hitl_agent
from agents.data_map_copilot_agent.sub_agents.profiling_agent.agent import profiling_agent, profiling_agent_anomaly

from agents.data_map_copilot_agent.sub_agents.profiling_hitl_agent.agent import (
    apply_profiling_hitl_modification,
    profiling_hitl_agent,
)
from agents.data_map_copilot_agent.sub_agents.similarity_hitl_agent.agent import (
    apply_similarity_hitl_full_update,
    regenerate_similarity_text_response,
    similarity_hitl_agent,
)
from agents.data_map_copilot_agent.sub_agents.dataset_overview_hitl_agent.agent import (
    apply_dataset_overview_text_response_edit,
    build_profiling_chat_state_delta,
    dataset_overview_hitl_agent,
    normalize_profiling_chat_response,
    should_run_text_response_edit,
)


# from api.models import MessageRequest,QARequest, SessionCreateRequest
from api.models import (
    MessageRequest,
    QARequest,
    SessionCreateRequest,
    AgentType,
    HumanInLoopRequest,
    HumanInLoopResponse,
    HumanInLoopLargeRequest,
    ProfilingChatHITLRequest,
    SimilarityChatHITLRequest,
)
from api.dependencies.auth import CurrentUser, resolve_current_user

LLM_RPM_LIMIT = 50
LLM_TPM_LIMIT = 250_000
WINDOW_SECONDS = 20
MAX_WAIT_SECONDS = 180  # 5 minutes


# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _friendly_llm_failure_message(exc: Exception) -> str:
    message = str(exc)
    lower = message.lower()
    if (
        "api_key_invalid" in lower
        or "api key not valid" in lower
        or ("invalid_argument" in lower and "api key" in lower)
    ):
        return (
            "Your Gemini API key was rejected by Google. Open Launchpad Settings → LLM API, "
            "click Edit on Gemini, paste a fresh key from https://aistudio.google.com/apikey "
            "(must start with AIza), save it, then click Retry here. "
            "If you use key restrictions in Google Cloud, allow server-side use (no browser referrer lock)."
        )
    if "tool_call_ids did not have response" in lower or (
        "tool_calls" in lower and "must be followed by tool messages" in lower
    ):
        return (
            "The profiling agent session had stale tool-call state. Click Retry — "
            "a fresh session will be created automatically."
        )
    if "no api key" in lower or ("missing" in lower and "api key" in lower):
        if llm_is_configured() and (config.LLM_PROVIDER or "").lower() == "openai":
            return (
                "OpenAI is configured but the profiling agent still tried to use Gemini. "
                "Retry once — if this persists, restart the backend and save your OpenAI key again."
            )
        return (
            "No LLM API key is configured for your account. Open Launchpad Settings → LLM API, "
            "enable OpenAI (ChatGPT) or Gemini, save your key, then retry."
        )
    if "contextwindowexceeded" in lower or "maximum context length" in lower:
        return (
            "The profiling session history is too large for the OpenAI model context window. "
            "Click Retry — the backend will compact the conversation automatically and rerun the analysis."
        )
    if "broken pipe" in lower:
        return (
            "Profiling could not read your uploaded data from the local warehouse. "
            "Click Retry on Dataset Overview. If it keeps failing, restart the backend and re-upload the files."
        )
    if "validation error" in lower and "outputformatprofiling" in lower:
        return (
            "Profiling failed while reading table statistics. Click Retry on Dataset Overview. "
            "If the error persists, restart the backend and re-upload your files."
        )
    return f"Something went wrong. Try again. ({message})"


ADK_API_URL = os.getenv("ADK_API_URL", "http://127.0.0.1:8000")

router = APIRouter()


def _load_session_context(session_id: Optional[str]) -> dict[str, Any]:
    if not session_id:
        return {}
    return load_profiling_session_context(session_id)


def _resolve_data_dictionary_table_id(
    session_id: Optional[str],
    session,
    req: dict[str, Any],
) -> Optional[str]:
    """Resolve the data dictionary BQ table for metadata fill from request or session."""
    additional = req.get("additional_data") or {}
    table_id = additional.get("data_dictionary_table_id")
    if table_id:
        return str(table_id).strip() or None

    if session and getattr(session, "state", None):
        state = session.state if isinstance(session.state, dict) else dict(session.state or {})
        final_dd = state.get("final_data_dict_response")
        if isinstance(final_dd, dict):
            table_id = final_dd.get("data_dictionary_table_id")
            if table_id:
                return str(table_id).strip()

    ctx = _load_session_context(session_id)
    for key in ("data_dictionary_table_id", "datadict_table_id"):
        value = ctx.get(key)
        if value:
            return str(value).strip()

    return None


def _fast_fill_metadata_from_datadict(
    data_dict_table_id: str,
    metadata_table_id: str,
) -> dict[str, Any]:
    """
    Deterministic metadata fill: copy Data Dictionary rows into the metadata BQ table.
    Avoids the slow/brittle multi-agent loop when the DD step already completed.
    """
    client = get_bigquery_client()
    query = f"SELECT * FROM `{data_dict_table_id}`"
    rows = [dict(row) for row in client.query(query).result()]
    if not rows:
        return {"rows_written": 0, "message": "No data dictionary rows found to copy."}

    key_map = {
        "File Name": "file_name",
        "Attribute Name": "field_name",
        "Logical Attribute Name": "business_name",
        "Attribute Description": "field_description",
        "Data Type": "data_type",
        "Length": "length",
        "Precision": "precision",
        "Format": "format",
        "Nullability": "nullable",
        "Default Value": "default_value",
        "Most Occurrences": "most_occurrences",
        "Primary Key": "primary_key",
        "Foreign Key": "foreign_key",
    }
    normalized_rows = []
    for row in rows:
        mapped = {key_map.get(k, k): v for k, v in row.items()}
        attr_name = (
            mapped.get("field_name")
            or mapped.get("Attribute_Name")
            or row.get("field_name")
            or row.get("Attribute Name")
            or row.get("Attribute_Name")
            or ""
        )
        file_name = (
            mapped.get("file_name")
            or mapped.get("File_Name")
            or row.get("file_name")
            or row.get("File Name")
            or row.get("File_Name")
            or ""
        )
        if not file_name and attr_name and "_" in str(attr_name):
            file_name = str(attr_name).split("_")[0]
        normalized_rows.append(
            {
                "file_name": file_name,
                "attribute_name": attr_name,
                "logical_attribute_name": mapped.get("business_name") or mapped.get("Logical_Attribute_Name") or "",
                "attribute_description": mapped.get("field_description") or mapped.get("Attribute_Description") or "",
                "data_type": mapped.get("data_type") or mapped.get("Data_Type") or "",
                "length": mapped.get("length") or mapped.get("Length") or "",
                "precision": mapped.get("precision") or mapped.get("Precision") or "",
                "format": mapped.get("format") or mapped.get("Format") or "",
                "nullability": mapped.get("nullable") or mapped.get("Nullability") or "",
                "default_values": mapped.get("default_value") or mapped.get("Default_Value") or "",
                "most_occurrences": mapped.get("most_occurrences") or mapped.get("Most_Occurrences") or "",
                "primary_key": mapped.get("primary_key") or mapped.get("Primary Key") or "0",
                "foreign_key": mapped.get("foreign_key") or mapped.get("Foreign Key") or "0",
                "alternate_key1": mapped.get("alternate_key1") or mapped.get("Alternate Key1") or "",
            }
        )

    result = overwrite_chunk_to_bq(json.dumps(normalized_rows), metadata_table_id)
    rows_written = result.get("rows_appended", len(normalized_rows)) if isinstance(result, dict) else len(normalized_rows)
    return {
        "rows_written": rows_written,
        "message": (
            f"Metadata template populated from the data dictionary "
            f"({rows_written} field rows copied to BigQuery)."
        ),
    }


def _seed_filespecs_from_context(
    filespecs_table_id: str,
    session_id: Optional[str],
    data_dict_table_id: Optional[str] = None,
) -> None:
    """Populate Filespecs BQ table from profiling session context and upload metadata."""
    session_data = load_profiling_session_context(session_id) if session_id else {}

    if data_dict_table_id and not session_data.get("physical_file_name"):
        try:
            client = get_bigquery_client()
            query = f"SELECT * FROM `{data_dict_table_id}` LIMIT 500"
            names = set()
            for row in client.query(query).result():
                row_dict = dict(row)
                name = row_dict.get("File Name") or row_dict.get("file_name")
                if name:
                    names.add(str(name).strip())
            if names:
                ordered_names = sorted(names)
                session_data = {
                    **session_data,
                    "physical_file_name": ", ".join(ordered_names),
                }
                if not session_data.get("file_extension"):
                    extensions = sorted(
                        {Path(name).suffix for name in ordered_names if Path(name).suffix}
                    )
                    if extensions:
                        session_data["file_extension"] = ", ".join(extensions)
        except Exception as exc:
            logging.warning("[metadata_fill] Failed to derive physical file names: %s", exc)

    rows = _build_filespecs_rows(session_data)
    for row in rows:
        if row.get("Value") is None:
            row["Value"] = ""
    overwrite_filespecs_in_bq(json.dumps(rows), filespecs_table_id)


def _metadata_response_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize metadata completion payloads for the UI."""
    return {
        "text_response": payload.get("message")
        or payload.get("text_response")
        or "Metadata template generated successfully.",
        "metadata_table_id": payload.get("metadata_table_id"),
        "filespecs_table_id": payload.get("filespecs_table_id"),
        "tool_response": payload.get("tool_response") or {},
        "should_update": True,
    }


def _update_session_context(session_id: Optional[str], updates: dict[str, Any]) -> dict[str, Any]:
    if not session_id:
        return {}
    context, _ = update_profiling_session_context(session_id, updates)
    return context


def _is_data_profiling_stage(stage: str) -> bool:
    return (stage or "").strip().lower() == "data profiling"


def _capture_profiling_chat_response(
    session_id: Optional[str],
    stage: str,
    profiling_payload: Any,
) -> None:
    """Persist canonical Data Profiling output to GCS (not Vertex state keys)."""
    if not session_id or not profiling_payload:
        return
    if not _is_data_profiling_stage(stage):
        logger.info(
            "[PROFILING_CHAT][PERSIST] Checking payload shape despite non-profiling stage=%s",
            stage,
        )
    if not _is_canonical_data_profiling_chat_response(profiling_payload):
        logger.info(
            "[PROFILING_CHAT][PERSIST] Skipping non-canonical profiling payload; session=%s stage=%s",
            session_id,
            stage,
        )
        return
    try:
        uri = persist_profiling_chat_response(session_id, profiling_payload)
        logger.info(
            "[PROFILING_CHAT][PERSIST] session=%s | stage=%s | uri=%s",
            session_id,
            stage,
            uri,
        )
        # #region agent log
        try:
            with open("debug-9901e3.log", "a", encoding="utf-8") as _dbg:
                _dbg.write(json.dumps({"sessionId": "9901e3", "location": "messages.py:_capture_profiling_chat_response", "message": "persisted profiling chat response", "data": {"session_id": session_id, "stage": stage, "uri": uri}, "timestamp": int(time.time() * 1000), "hypothesisId": "H1"}) + "\n")
        except Exception:
            pass
        # #endregion
    except Exception:
        logger.exception(
            "[PROFILING_CHAT][PERSIST] Failed for session=%s stage=%s",
            session_id,
            stage,
        )


def _is_canonical_data_profiling_chat_response(payload: Any) -> bool:
    """True when payload matches /send Data Profiling shape (not anomaly/relationship)."""
    if not isinstance(payload, dict):
        return False
    tool_resp = payload.get("tool_response")
    if not isinstance(tool_resp, dict):
        return False
    if "table_anomaly_reports" in tool_resp or "summary_statistics" in tool_resp:
        return False
    results = tool_resp.get("result")
    if not isinstance(results, list):
        results = tool_resp.get("all_tables")
    if isinstance(results, list):
        return bool(results) and any(isinstance(item, dict) for item in results)
    return isinstance(results, dict) and bool(results)


def _resolve_profiling_chat_response(
    session_id: str,
    session_state: dict[str, Any],
) -> dict[str, Any] | None:
    """Prefer GCS canonical profiling payload; fall back to non-anomaly session keys."""
    stored = load_profiling_chat_response(session_id)
    if stored:
        # #region agent log
        try:
            with open("debug-9901e3.log", "a", encoding="utf-8") as _dbg:
                _dbg.write(json.dumps({"sessionId": "9901e3", "location": "messages.py:_resolve_profiling_chat_response", "message": "loaded profiling chat from GCS", "data": {"session_id": session_id, "source": "gcs"}, "timestamp": int(time.time() * 1000), "hypothesisId": "H2"}) + "\n")
        except Exception:
            pass
        # #endregion
        if _is_canonical_data_profiling_chat_response(stored):
            return stored
        logger.warning(
            "[PROFILING_CHAT][RESOLVE] GCS artifact is not data-profiling shape; session=%s",
            session_id,
        )
    for key in ("final_profiling_response_streaming", "final_profiling_response"):
        candidate = session_state.get(key)
        if isinstance(candidate, dict) and _is_canonical_data_profiling_chat_response(candidate):
            # #region agent log
            try:
                with open("debug-9901e3.log", "a", encoding="utf-8") as _dbg:
                    _dbg.write(json.dumps({"sessionId": "9901e3", "location": "messages.py:_resolve_profiling_chat_response", "message": f"fallback to {key}", "data": {"session_id": session_id, "source": "vertex_state"}, "timestamp": int(time.time() * 1000), "hypothesisId": "H3"}) + "\n")
            except Exception:
                pass
            # #endregion
            return candidate
        if candidate:
            logger.warning(
                "[PROFILING_CHAT][RESOLVE] Ignoring %s "
                "(likely overwritten by anomaly/relationship run); session=%s",
                key,
                session_id,
            )
    return None


def _resolve_profiling_from_app_resume(
    vertex_session_id: str,
    user_key: str | None,
) -> dict[str, Any] | None:
    """Load dataset-overview payload saved in the app session profiling resume state."""
    if not vertex_session_id or not user_key:
        return None
    try:
        from db.engine import app_db_session
        from db.repositories import AppSessionRepository
        from api.routers.sessions import _hydrate_profiling_resume_state
    except ImportError:
        return None

    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session_by_vertex_session_id(
            vertex_session_id=vertex_session_id,
            user_key=user_key,
        )
        if not session_obj:
            return None
        run = repo.get_current_profiling_run(session=session_obj)
        if not run:
            return None
        resume = _hydrate_profiling_resume_state(run.resume_state_json)
        initial_data = resume.get("initialMessageData")
        candidates: list[Any] = []
        if isinstance(initial_data, list):
            candidates.extend(initial_data)
        elif isinstance(initial_data, dict):
            candidates.append(initial_data)
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if "tool_response" not in item and "text_response" not in item:
                continue
            normalized = normalize_profiling_chat_response(item)
            tool_resp = normalized.get("tool_response")
            text_resp = normalized.get("text_response")
            if isinstance(tool_resp, dict) or text_resp:
                logger.info(
                    "[PROFILING_CHAT][RESOLVE] Loaded profiling payload from app resume; session=%s",
                    vertex_session_id,
                )
                return normalized
    return None


def _resolve_profiling_full_results(session_id: str) -> list[Any]:
    """Load batched profiling table payloads persisted outside Vertex session state."""
    session_data = _load_session_context(session_id)
    artifact_uri = str(session_data.get("profiling_full_results_uri") or "").strip()
    if not artifact_uri:
        return []
    try:
        payload = load_resume_json_artifact(artifact_uri)
        return payload if isinstance(payload, list) else []
    except Exception:
        logger.exception(
            "[PROFILING_DASHBOARD] Failed to load profiling_full_results artifact for session=%s",
            session_id,
        )
        return []


async def _resolve_profiling_dashboard_payload(
    session_id: str,
    user_id: str,
    app_name: str,
) -> dict[str, Any]:
    """Resolve dataset-overview profiling payload for the Detailed Profiling dashboard."""
    session_service = VertexAiSessionService(
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    session_state = dict(session.state or {}) if session else {}

    raw_profiling = _resolve_profiling_chat_response(session_id, session_state)
    if not raw_profiling:
        profiling_full_results = session_state.get("profiling_full_results") or _resolve_profiling_full_results(
            session_id
        )
        if profiling_full_results:
            raw_profiling = {
                "text_response": "",
                "tool_response": {"all_tables": profiling_full_results},
                "should_update": False,
            }

    if not raw_profiling:
        raw_profiling = _resolve_profiling_from_app_resume(session_id, user_id)

    if not raw_profiling:
        raise HTTPException(
            status_code=404,
            detail=(
                "No profiling dashboard data found. Complete Step 1: Dataset Overview first, "
                "then return to Detailed Table Profiling."
            ),
        )

    return normalize_profiling_chat_response(raw_profiling)


async def _inject_profiling_chat_canonical_state(
    *,
    session_service: VertexAiSessionService,
    session,
    session_id: str,
    canonical_response: dict[str, Any],
) -> None:
    """Inject GCS-backed profiling context (markdown + structured) for HITL agent turns."""
    state_delta = build_profiling_chat_state_delta(canonical_response)
    session.state.update(state_delta)
    update_event = Event(
        author="system",
        invocation_id=f"profiling-chat-canonical-{uuid.uuid4()}",
        actions=EventActions(state_delta=state_delta),
    )
    await session_service.append_event(session=session, event=update_event)


async def _persist_profiling_chat_update(
    *,
    session_service: VertexAiSessionService,
    session,
    session_id: str,
    canonical_response: dict[str, Any],
) -> str:
    """Persist HITL edits to GCS, Vertex session, and agent context keys."""
    uri = persist_profiling_chat_response(session_id, canonical_response)
    hitl_delta = build_profiling_chat_state_delta(canonical_response)
    vertex_delta = {
        **hitl_delta,
        "final_profiling_response": canonical_response,
        "final_profiling_response_streaming": canonical_response,
    }
    session.state.update(vertex_delta)
    update_event = Event(
        author="system",
        invocation_id=f"profiling-chat-hitl-update-{uuid.uuid4()}",
        actions=EventActions(state_delta=vertex_delta),
    )
    await session_service.append_event(session=session, event=update_event)
    logger.info(
        "[PROFILING_CHAT][PERSIST_HITL] session=%s | uri=%s | keys=%s",
        session_id,
        uri,
        list(vertex_delta.keys()),
    )
    return uri

# Define message-related models (or import from models.py)

from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.genai import types
from google.adk.agents.run_config import RunConfig

# Define the AI model to be used for summarization:
summarization_llm = Gemini(model="gemini-2.5-flash")

# Create the summarizer with the custom model:
my_summarizer = LlmEventSummarizer(llm=summarization_llm)

# Configure the App with the custom summarizer and compaction settings:






def _build_orchestrator_app(app_name: str, agent) -> App:
    """
    Build an ADK App instance even when the incoming app_name is not a valid identifier.
    Falls back to model_construct so we can keep the raw Vertex resource name
    (e.g. projects/.../reasoningEngines/ID) without breaking ADK validation.
    """
    try:
        app = App(
            name=app_name,
            root_agent=agent,
            events_compaction_config=EventsCompactionConfig(
                compaction_interval=4,
                overlap_size=2,
                summarizer=my_summarizer,
            ),
            context_cache_config=ContextCacheConfig(
                min_tokens=100000,   
                ttl_seconds=900,   
                cache_intervals=10, 
            ),
        )

        return app

    except ValidationError as exc:
        logging.warning(
            "App name '%s' failed validation (%s); constructing App without validation.",
            app_name,
            exc,
        )
        return App.model_construct(name=app_name, root_agent=agent, events_compaction_config=EventsCompactionConfig(
                compaction_interval=4,
                overlap_size=2,
                summarizer=my_summarizer,
            ),)
# Expected Excel column headers as they appear in the downloaded template
EXPECTED_COLUMNS = [
    "FILE NAME",
    "FIELD NAME",
    "FIELD BUSINESS NAME",
    "FIELD DESCRIPTION",
    "DATA TYPE",
    "LENGTH",
    "FORMAT",
    "NULLABLE",
    "DEFAULT VALUE",
    "PRIMARY KEY",
    "FOREIGN KEY"
]
 
# Mapping from Excel column headers -> JSON keys in tool_response.result
COLUMN_KEY_MAP = {
    "FILE NAME": "file_name",
    "FIELD NAME": "field_name",
    "FIELD BUSINESS NAME": "business_name",
    "FIELD DESCRIPTION": "field_description",
    "DATA TYPE": "data_type",
    "LENGTH": "length",
    "FORMAT": "format",
    "NULLABLE": "nullable",
    "DEFAULT VALUE": "default_value",
    "PRIMARY KEY": "primary_key",
    "FOREIGN KEY": "foreign_key"
}
 
 

@router.post("/data-dictionary/reupload")
async def reupload_data_dictionary(
    file: UploadFile = File(...),
    session_id: str = Form(...)   # <<< ADDED
):
    try:
        filename = file.filename.lower()
        is_excel = filename.endswith((".xlsx", ".xls"))
        is_csv = filename.endswith(".csv")

        logging.error(f"[DEBUG] START REUPLOAD filename={filename}, session_id={session_id}")

        if not (is_excel or is_csv):
            return [{
                "text_response": "Invalid file format. Upload .xlsx .xls .csv",
                "tool_response": {
                    "status": "error",
                    "error_type": "invalid_file_type"
                },
                "status": 0,
                "session_id": session_id,  # <<< RETURNED
                "should_update": False
            }]

        file.file.seek(0)
        raw_bytes = file.file.read()

        if is_excel:
            df = pd.read_excel(io.BytesIO(raw_bytes))
        else:
            text = raw_bytes.decode("utf-8", errors="replace")
            import csv as _csv_reader
            reader = _csv_reader.reader(text.splitlines())
            all_rows = [row for row in reader if any(cell.strip() for cell in row)]

            target_col_count = len(EXPECTED_COLUMNS)

            header_raw = all_rows[0] if all_rows else []
            header_clean = [h.strip().replace('"', '') for h in header_raw]
            header = header_clean[:target_col_count]

            if len(header) < target_col_count:
                header = header + EXPECTED_COLUMNS[len(header):]

            df_rows = []
            for row in all_rows[1:]:
                parts = [p.strip() for p in row]

                if len(parts) > target_col_count:
                    parts = parts[:target_col_count]

                if len(parts) < target_col_count:
                    parts = parts + [""] * (target_col_count - len(parts))

                df_rows.append(parts)

            df = pd.DataFrame(df_rows, columns=header)

        df.columns = df.columns.str.strip().str.replace('"', '', regex=False)
        df = df.loc[:, ~df.columns.str.contains("^Unnamed")]
        df = df.dropna(axis=1, how="all")

        # ====================== CANONICAL COLUMN NORMALIZATION ======================
        # Convert uploaded header names into the canonical schema required by Validation Agent.
        canonical_map = {}
        for col in df.columns:
            normalized = col.strip().upper().replace("_", " ")
            canonical_map[col] = normalized

        df.rename(columns=canonical_map, inplace=True)
        # ===========================================================================

        logging.error(f"[DEBUG] Normalized columns: {list(df.columns)}")


        actual = set(df.columns)
        expected = set(EXPECTED_COLUMNS)

        missing = [col for col in EXPECTED_COLUMNS if col not in actual]
        extra = [col for col in actual if col not in EXPECTED_COLUMNS]

        if missing or extra:
            return [{
                "text_response": "Modified template. Re-download and re-upload.",
                "tool_response": {
                    "status": "error",
                    "error_type": "invalid_template_schema",
                    "missing_columns": missing,
                    "extra_columns": extra
                },
                "status": 0,
                "session_id": session_id,  # <<< RETURNED
                "should_update": False
            }]

        df = df[EXPECTED_COLUMNS]

        result_rows = []
        for _, row in df.iterrows():
            d = {}
            for col, key in COLUMN_KEY_MAP.items():
                # Skip DEFAULT VALUE column in the output
                if col == "DEFAULT VALUE":
                    continue
                v = row.get(col)
                d[key] = None if pd.isna(v) else v
            result_rows.append(d)

        # Create display columns excluding DEFAULT VALUE
        display_columns = [col for col in EXPECTED_COLUMNS if col != "DEFAULT VALUE"]
        df_display = df[display_columns]
        
        header = "| " + " | ".join(display_columns) + " |"
        sep = "|---" * len(display_columns) + "|"
        body = ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |"
                for r in df_display.itertuples(index=False)]
        markdown = "\n".join([header, sep] + body)

        return [{
            "text_response": markdown,
            "tool_response": {
                "result": result_rows,
                "session_id": session_id  # <<< RETURNED
            },
            "status": 1,
            "session_id": session_id,  # <<< RETURNED
            "should_update": True
        }]

    except Exception as e:
        logging.error(f"[UNEXPECTED ERROR] {e}", exc_info=True)
        return [{
            "text_response": "Internal server error",
            "tool_response": {
                "status": "error",
                "error_type": "exception"
            },
            "status": 0,
            "session_id": session_id,  # <<< RETURNED ALWAYS
            "should_update": False
        }]





def extract_response_from_malformed_call(error_message: str) -> Optional[Dict[str, Any]]:
    """
    Extract text_response, tool_response, and should_update from a malformed function call error.
    
    Args:
        error_message: The error message string containing the malformed function call
        
    Returns:
        Dictionary with extracted values or None if extraction fails
    """
    try:
        # Pattern to match set_model_response(...) content
        pattern = r'set_model_response\((.*?)\)\)\'.*?interrupted'
        match = re.search(pattern, error_message, re.DOTALL)
        
        if not match:
            print("Could not find set_model_response pattern")
            return None
        
        # Get the parameters string
        params_str = match.group(1)
        
        # Extract text_response (handling multiline markdown with escaped newlines)
        text_pattern = r"text_response=\\'(.*?)\\'(?=,\s*should_update)"
        text_match = re.search(text_pattern, params_str, re.DOTALL)
        text_response = ""
        if text_match:
            text_response = text_match.group(1)
            # Unescape the string
            text_response = text_response.replace('\\n', '\n').replace("\\'", "'")
        
        # Extract should_update (boolean)
        should_update_pattern = r'should_update=(True|False)'
        should_match = re.search(should_update_pattern, params_str)
        should_update = False
        if should_match:
            should_update = should_match.group(1) == 'True'
        
        # Extract tool_response (JSON object)
        tool_pattern = r'tool_response=(\{.*\})\)'
        tool_match = re.search(tool_pattern, params_str, re.DOTALL)
        tool_response = {}
        if tool_match:
            tool_str = tool_match.group(1)
            # Replace single quotes with double quotes for JSON parsing
            # Handle nested structures carefully
            tool_str = tool_str.replace("\\'", "<<<ESCAPED_QUOTE>>>")
            tool_str = tool_str.replace("'", '"')
            tool_str = tool_str.replace("<<<ESCAPED_QUOTE>>>", "'")
            # Handle True/False/None
            tool_str = tool_str.replace(': True', ': true')
            tool_str = tool_str.replace(': False', ': false')
            tool_str = tool_str.replace(': None', ': null')
            
            try:
                tool_response = json.loads(tool_str)
            except json.JSONDecodeError as e:
                print(f"JSON decode error for tool_response: {e}")
                # Fallback: keep as empty dict
                tool_response = {}
        
        result = {
            'text_response': text_response,
            'tool_response': tool_response,
            'should_update': should_update
        }
        
        return result
        
    except Exception as e:
        print(f"Error extracting response: {e}")
        return None




def _find_closing_brace(text: str, start_pos: int) -> int:
    """Helper to find matching closing brace."""
    brace_count = 0
    in_string = False
    escape_next = False
    
    for i in range(start_pos, len(text)):
        char = text[i]
        
        if escape_next:
            escape_next = False
            continue
        
        if char == '\\':
            escape_next = True
            continue
        
        if char == "'":
            in_string = not in_string
        
        if not in_string:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    return i + 1
    
    return start_pos

def _extract_text_response(error_message: str, start_idx: int) -> str:
    """Extract text_response from error message."""
    text_start = error_message.find("text_response=\\'", start_idx)
    if text_start == -1:
        return ""
    
    text_start += len("text_response=\\'")
    text_end = error_message.find("\\', should_update", text_start)
    if text_end == -1:
        return ""
    
    text_response = error_message[text_start:text_end]
    return text_response.replace('\\n', '\n').replace("\\'", "'")

def _extract_should_update(error_message: str, start_idx: int) -> bool:
    """Extract should_update boolean from error message."""
    should_pattern = r'should_update=(True|False)'
    should_match = re.search(should_pattern, error_message[start_idx:])
    return should_match.group(1) == 'True' if should_match else False

def _extract_tool_response(error_message: str, start_idx: int) -> Dict[str, Any]:
    """Extract and parse tool_response JSON from error message."""
    tool_start = error_message.find("tool_response={", start_idx)
    if tool_start == -1:
        return {}
    
    tool_start += len("tool_response=")
    tool_end = _find_closing_brace(error_message, tool_start)
    
    if tool_end <= tool_start:
        return {}
    
    tool_str = error_message[tool_start:tool_end]
    tool_str = tool_str.replace("\\'", "<<<ESCAPED_QUOTE>>>")
    tool_str = tool_str.replace("'", '"')
    tool_str = tool_str.replace("<<<ESCAPED_QUOTE>>>", "'")
    tool_str = tool_str.replace(': True', ': true')
    tool_str = tool_str.replace(': False', ': false')
    tool_str = tool_str.replace(': None', ': null')
    
    try:
        return json.loads(tool_str)
    except json.JSONDecodeError:
        return {}

def extract_response_alternative(error_message: str) -> Optional[Dict[str, Any]]:
    """
    Alternative extraction method that's more robust for complex nested structures.
    Uses a simpler approach by finding key boundaries.
    
    Args:
        error_message: The error message string containing the malformed function call
        
    Returns:
        Dictionary with extracted values or None if extraction fails
    """
    try:
        start_marker = "set_model_response("
        start_idx = error_message.find(start_marker)
        if start_idx == -1:
            return None
        
        start_idx += len(start_marker)
        
        return {
            'text_response': _extract_text_response(error_message, start_idx),
            'tool_response': _extract_tool_response(error_message, start_idx),
            'should_update': _extract_should_update(error_message, start_idx)
        }
        
    except Exception as e:
        print(f"Error in alternative extraction: {e}")
        return None



def extract_json_from_string(text_blob: str) -> Optional[Any]:
    """
    Extracts a JSON object from a string that might be:
    1) Embedded in a markdown ```json ... ``` block
    2) A raw JSON string starting directly with '{' or '['

    Args:
        text_blob: The input string containing the JSON data.

    Returns:
        A Python dict or list if valid JSON is found, otherwise None.
    """
    if not text_blob or not isinstance(text_blob, str):
        return None

    text_blob = text_blob.strip()

    # ---- Case 1: JSON inside ```json ... ``` block ----
    pattern = r"```json\s*(.*?)\s*```"
    match = re.search(pattern, text_blob, re.DOTALL)

    if match:
        json_string = match.group(1).strip()
        try:
            return json.loads(json_string)
        except json.JSONDecodeError:
            return None

    # ---- Case 2: Raw JSON starting with { or [ ----
    if text_blob.startswith("{") or text_blob.startswith("["):
        try:
            return json.loads(text_blob)
        except json.JSONDecodeError:
            return None

    return None
 

def extract_source_data(data, session_id="default_session"):
    gemini_client = GoogleGeminiClient()

    response = gemini_client.extract_data(data, session_id=session_id)
    print("+"*200)
    print(response)
    print("+"*200)

    return response

def format_output(stage, data, session_id="default_session"):

    gemini_client = GoogleGeminiClient()

    response = gemini_client.generate(stage, data, session_id=session_id)
    print("+"*200)
    print(response)
    print("+"*200)

    return response


def _parse_jsonish(value):
    """Parse a JSON or Python-literal string into Python objects."""
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        try:
            import ast

            return ast.literal_eval(s)
        except Exception:
            return None


def _coerce_profiling_result_entry(item):
    if isinstance(item, dict):
        return item
    if isinstance(item, str):
        parsed = _parse_jsonish(item)
        return parsed if isinstance(parsed, dict) else None
    return None


def _normalize_tool_response_results(tool_response):
    """Ensure tool_response.result is a list of dicts (OpenAI may return JSON strings)."""
    if not isinstance(tool_response, dict):
        return tool_response
    raw = tool_response.get("result")
    if raw is None:
        return tool_response
    if isinstance(raw, str):
        parsed = _parse_jsonish(raw)
        if isinstance(parsed, list):
            raw = parsed
        elif isinstance(parsed, dict):
            raw = [parsed]
        else:
            return tool_response
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return tool_response
    normalized = []
    for item in raw:
        coerced = _coerce_profiling_result_entry(item)
        if coerced is not None:
            normalized.append(ensure_profiling_result_shape(coerced))
    out = dict(tool_response)
    out["result"] = normalized
    return out


def _enrich_column_analysis(tool_response, profiling_json_data, session_id):
    tool_response = _normalize_tool_response_results(tool_response)
    if not isinstance(tool_response, dict):
        return tool_response
    results = tool_response.get("result")
    if not isinstance(results, list):
        return tool_response
    for result in results:
        if not isinstance(result, dict):
            continue
        if not result.get("column_analysis") or len(result.get("column_analysis", [])) <= 1:
            result["column_analysis"] = extract_column_analysis(
                profiling_json_data, session_id=session_id
            )
    return tool_response


def _ensure_tool_response_dict(value):
    """Normalize a model-provided `tool_response` into a dict.

    Gemini function-calls sometimes hand back `tool_response` as a JSON (or
    Python-literal) string instead of an object. Downstream code indexes it like
    a dict (e.g. ``tool_response['result']``), which raises
    "string indices must be integers, not 'str'" when it is actually a str.
    Coerce defensively so the profiling/anomaly flows never crash on shape.
    """
    if isinstance(value, dict):
        return _normalize_tool_response_results(value)
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
        parsed = _parse_jsonish(s)
        if isinstance(parsed, dict):
            return _normalize_tool_response_results(parsed)
        if isinstance(parsed, list):
            return _normalize_tool_response_results({"result": parsed})
    return {}


def get_text_between_brackets(s: str) -> str:
    start = s.find('[')
    end = s.find(']', start + 1)
    return s[start + 1:end] if start != -1 and end != -1 else ''

def get_data_ditionary(dd_reference: List['str']):
    results = []
    for dd_ref in dd_reference:
        print("getting table for ", dd_ref)
        table = get_table(dd_ref)
        results.append(table)
    return results


MODEL_MAX_INPUT_TOKENS = 1_048_576  # Gemini hard limit
# Default safety budget for Gemini-scale models.
MODEL_SAFE_INPUT_TOKENS = 900_000
TOKEN_GUARD_TARGET_TOKENS = 250_000
TOKEN_GUARD_TRIGGER_TOKENS = 500_000
TOKEN_GUARD_MAX_CONVERSATION_CHARS = 4_000_000
DATA_DICTIONARY_TOKEN_GUARD_TARGET_TOKENS = 120_000
DATA_DICTIONARY_TOKEN_GUARD_TRIGGER_TOKENS = 220_000


def _token_guard_limits() -> Dict[str, int]:
    """
    Provider-aware token budgets. OpenAI models (128k window) need much tighter
    limits than Gemini; otherwise ADK never compacts and requests fail at the API.
    """
    provider = (getattr(config, "LLM_PROVIDER", "") or os.getenv("LLM_PROVIDER", "") or "").strip().lower()
    if provider in {"openai", "groq"}:
        return {
            "max_input": 128_000,
            "safe_input": 80_000,
            "guard_target": 50_000,
            "guard_trigger": 75_000,
            "run_config_trigger": 70_000,
            "run_config_target": 45_000,
            "dd_run_config_trigger": 65_000,
            "dd_run_config_target": 40_000,
            "max_conversation_chars": 300_000,
        }
    return {
        "max_input": MODEL_MAX_INPUT_TOKENS,
        "safe_input": MODEL_SAFE_INPUT_TOKENS,
        "guard_target": TOKEN_GUARD_TARGET_TOKENS,
        "guard_trigger": TOKEN_GUARD_TRIGGER_TOKENS,
        "run_config_trigger": TOKEN_GUARD_TRIGGER_TOKENS,
        "run_config_target": TOKEN_GUARD_TARGET_TOKENS,
        "dd_run_config_trigger": DATA_DICTIONARY_TOKEN_GUARD_TRIGGER_TOKENS,
        "dd_run_config_target": DATA_DICTIONARY_TOKEN_GUARD_TARGET_TOKENS,
        "max_conversation_chars": TOKEN_GUARD_MAX_CONVERSATION_CHARS,
    }


def _build_token_guard_run_config() -> RunConfig:
    """
    Shared ADK run config used by all agent endpoints to auto-compress context
    before reaching model limits.
    """
    limits = _token_guard_limits()
    return RunConfig(
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=limits["run_config_trigger"],
            sliding_window=types.SlidingWindow(
                target_tokens=limits["run_config_target"]
            )
        )
    )


def _build_data_dictionary_run_config() -> RunConfig:
    """
    Stricter context compaction for /data-dictionary, where prior profiling
    sessions can carry large artifacts from other agents.
    """
    limits = _token_guard_limits()
    return RunConfig(
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=limits["dd_run_config_trigger"],
            sliding_window=types.SlidingWindow(
                target_tokens=limits["dd_run_config_target"]
            )
        )
    )


def _extract_text_from_content(content: types.Content) -> str:
    if not content or not getattr(content, "parts", None):
        return ""
    return "".join(
        getattr(part, "text", "") or ""
        for part in content.parts
        if getattr(part, "text", None) is not None
    )


def _extract_conversation_text_from_session(session: Optional[Session]) -> str:
    """
    Build conversation text from ADK session history/events for token guard checks.
    Includes tool call/response payloads, which dominate OpenAI context usage.
    """
    if not session:
        return ""

    snippets: List[str] = []
    try:
        if hasattr(session, "events") and session.events:
            for event in session.events:
                author = getattr(event, "author", "unknown")
                content = getattr(event, "content", None)
                if content and getattr(content, "parts", None):
                    for part in content.parts:
                        if getattr(part, "text", None):
                            snippets.append(f"{author}: {part.text}")
                        function_call = getattr(part, "function_call", None)
                        if function_call is not None:
                            args = getattr(function_call, "args", None)
                            snippets.append(
                                f"{author} tool_call {getattr(function_call, 'name', '')}: "
                                f"{json.dumps(args, default=str)}"
                            )
                        function_response = getattr(part, "function_response", None)
                        if function_response is not None:
                            response = getattr(function_response, "response", None)
                            snippets.append(
                                f"{author} tool_response {getattr(function_response, 'name', '')}: "
                                f"{json.dumps(response, default=str)}"
                            )

        if not snippets and hasattr(session, "history") and session.history:
            for message in session.history:
                role = getattr(message, "role", "unknown")
                if hasattr(message, "parts") and message.parts:
                    msg_text = "".join(
                        (getattr(part, "text", "") or "")
                        for part in message.parts
                        if getattr(part, "text", None)
                    ).strip()
                    if msg_text:
                        snippets.append(f"{role}: {msg_text}")
    except Exception as exc:
        logger.warning("[TOKEN_GUARD] Failed to extract conversation history: %s", exc)

    conversation_text = "\n".join(snippets)
    max_chars = _token_guard_limits()["max_conversation_chars"]
    if len(conversation_text) > max_chars:
        logger.warning(
            "[TOKEN_GUARD] Conversation too large (%s chars). Summarizing in 4 chunks.",
            len(conversation_text),
        )
        quarter_size = max(1, len(conversation_text) // 4)
        chunk_summaries: List[str] = []
        chunk_target = max(10_000, _token_guard_limits()["guard_target"] // 4)

        for i in range(4):
            start = i * quarter_size
            end = (i + 1) * quarter_size if i < 3 else len(conversation_text)
            chunk = conversation_text[start:end].strip()
            if not chunk:
                continue
            chunk_summary = _summarize_conversation_chunk(
                text=chunk,
                target_tokens=chunk_target,
            )
            chunk_summaries.append(f"[Conversation Chunk {i + 1} Summary]\n{chunk_summary}")

        conversation_text = "\n\n".join(chunk_summaries)
    return conversation_text


OPENAI_BULKY_STATE_KEYS = (
    "retriever_agent_response",
    "generator_agent_response",
    "saver_agent_response",
    "fetch_agent_response",
)


def _estimate_session_token_load(session: Optional[Session], extra_text: str = "") -> int:
    """Estimate tokens for ADK session history + state + optional extra message text."""
    rate_limiter = RateLimiter()
    conversation_text = _extract_conversation_text_from_session(session)
    state_text = ""
    if session is not None and getattr(session, "state", None):
        try:
            state_text = json.dumps(dict(session.state), default=str)
        except Exception:
            state_text = str(getattr(session, "state", ""))
    combined = "\n".join(part for part in (conversation_text, state_text, extra_text) if part)
    estimated = rate_limiter.count_tokens(combined)
    provider = (getattr(config, "LLM_PROVIDER", "") or os.getenv("LLM_PROVIDER", "") or "").strip().lower()
    if provider in {"openai", "groq"}:
        # OpenAI token counting via Gemini/char heuristics under-estimates tool payloads.
        estimated = int(estimated * 1.35) + 5_000
    return estimated


async def _maybe_compact_openai_session_before_run(
    *,
    session_service: VertexAiSessionService,
    session: Optional[Session],
    app_name: str,
    user_id: str,
    session_id: Optional[str],
    user_message: str,
    stage: str,
    endpoint_name: str,
) -> Optional[Session]:
    """
    Reset oversized ADK sessions for OpenAI-sized models.

    Prior profiling + data-dictionary turns leave large tool payloads in session
    events/state. Relationship/anomaly reruns then exceed the 128k window.
    """
    provider = (getattr(config, "LLM_PROVIDER", "") or os.getenv("LLM_PROVIDER", "") or "").strip().lower()
    if provider not in {"openai", "groq"} or not session_id:
        return session

    limits = _token_guard_limits()
    stage_lower = (stage or "").lower()
    user_lower = (user_message or "").lower()
    is_data_dictionary = "data dictionary" in stage_lower or "data dictionary" in user_lower

    state = dict(getattr(session, "state", {}) or {}) if session else {}
    if not is_data_dictionary:
        for key in OPENAI_BULKY_STATE_KEYS:
            state.pop(key, None)

    estimated = _estimate_session_token_load(session, user_message)
    is_follow_up = any(
        token in user_lower
        for token in ("relationship", "anomaly", "data dictionary", "profiling")
    )
    should_compact = estimated > limits["safe_input"] or (
        is_follow_up and estimated > limits["safe_input"] // 2
    )
    if not should_compact:
        return session

    logger.warning(
        "[TOKEN_GUARD] Compacting session before %s | session=%s | estimated_tokens=%s | safe_limit=%s",
        endpoint_name,
        session_id,
        estimated,
        limits["safe_input"],
    )

    conversation_text = _extract_conversation_text_from_session(session)
    summary_source = conversation_text or json.dumps(state, default=str)
    summary_text = _summarize_text_for_token_guard(
        text=summary_source,
        target_tokens=limits["guard_target"],
    )
    recreated = await _recreate_session_with_summary_event(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        summary_text=summary_text,
        fallback_state=state,
    )
    if not recreated:
        return session
    return await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )


def _summarize_conversation_chunk(text: str, target_tokens: int) -> str:
    """
    Summarize a conversation chunk without pre-truncating its content.
    """
    if not text:
        return ""
    try:
        client = genai.Client(
            vertexai=True,
            location=config.GOOGLE_CLOUD_LOCATION,
            project=config.GOOGLE_CLOUD_PROJECT,
        )
        prompt = (
            "Summarize this conversation chunk for downstream agent context.\n"
            f"Target size: <= {target_tokens} tokens.\n"
            "Keep important requirements, IDs, constraints, and decisions.\n"
            "Output plain text only.\n\n"
            f"CONVERSATION CHUNK:\n{text}"
        )
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
            ),
        )
        summarized = (getattr(response, "text", "") or "").strip()
        if summarized:
            return summarized
    except Exception as exc:
        logger.warning("[TOKEN_GUARD] Chunk summarization failed: %s", exc)

    # Fallback to existing summarizer behavior if direct summarization fails.
    return _summarize_text_for_token_guard(text=text, target_tokens=target_tokens)


def _summarize_text_for_token_guard(text: str, target_tokens: int) -> str:
    """
    Summarize oversized prompt text using Google GenAI and preserve key data/instructions.
    Falls back to deterministic truncation if summarization fails.
    """
    if not text:
        return text

    approx_chars_limit = target_tokens * 4
    if len(text) <= approx_chars_limit:
        return text

    # Keep head+tail as source window for summarization to avoid massive single call payload.
    half = max(1, approx_chars_limit // 2)
    source_window = (
        text[:half]
        + "\n\n[... CONTENT TRUNCATED FOR TOKEN GUARD ...]\n\n"
        + text[-half:]
    )

    try:
        client = genai.Client(
            vertexai=True,
            location=config.GOOGLE_CLOUD_LOCATION,
            project=config.GOOGLE_CLOUD_PROJECT,
        )
        prompt = (
            f"You are compressing prompt context for an LLM with a hard input budget.\n"
            f"Compress the following content to approximately <= {target_tokens} tokens.\n"
            "Rules:\n"
            "1. Preserve user intent and explicit instructions.\n"
            "2. Preserve table names, IDs, paths, schema fields, and critical constraints.\n"
            "3. Remove repetition and verbose explanations.\n"
            "4. Output plain text only.\n\n"
            f"CONTENT:\n{source_window}"
        )
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
            ),
        )
        compressed_text = (getattr(response, "text", "") or "").strip()
        if compressed_text:
            return compressed_text
    except Exception as exc:
        logger.warning("Token guard summarization failed, using truncation fallback: %s", exc)

    # Fallback: deterministic compacting
    return source_window


async def _recreate_session_with_summary_event(
    *,
    session_service: VertexAiSessionService,
    app_name: str,
    user_id: str,
    session_id: str,
    summary_text: str,
    fallback_state: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Reset a session to summary-only history while preserving the same session ID.
    """
    try:
        latest_state = fallback_state or {}
        try:
            latest_session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
            if latest_session and getattr(latest_session, "state", None):
                latest_state = latest_session.state
        except Exception as exc:
            logger.warning(
                "[TOKEN_GUARD] Could not hydrate latest session state before reset. "
                "session=%s error=%s",
                session_id,
                exc,
            )

        await session_service.delete_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        logger.warning("[TOKEN_GUARD] Deleted oversized session %s", session_id)

        recreated_session = await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            state=latest_state,
            session_id=session_id,
        )
        logger.warning(
            "[TOKEN_GUARD] Recreated session with same id=%s and state_keys=%s",
            session_id,
            list((latest_state or {}).keys()),
        )

        summary_event = Event(
            author="system",
            invocation_id=f"summary-reset-{uuid.uuid4()}",
            content=types.Content(
                role="model",
                parts=[types.Part(text=f"[Session Summary]\n{summary_text}")],
            ),
            actions=EventActions(skip_summarization=True),
        )
        await session_service.append_event(session=recreated_session, event=summary_event)
        logger.warning("[TOKEN_GUARD] Appended summary event to session %s", session_id)
        return True
    except Exception as exc:
        logger.error(
            "[TOKEN_GUARD] Failed to recreate summary-only session. session=%s error=%s",
            session_id,
            exc,
        )
        return False


async def _guard_message_tokens(
    msg: types.Content,
    session_id: Optional[str],
    endpoint_name: str,
    session: Optional[Session] = None,
    session_service: Optional[VertexAiSessionService] = None,
    app_name: Optional[str] = None,
    user_id: Optional[str] = None,
) -> types.Content:
    """
    Check full conversation + latest message token count and compress/summarize when needed.
    """
    try:
        rate_limiter = RateLimiter()
        limits = _token_guard_limits()
        latest_user_text = _extract_text_from_content(msg)
        if not latest_user_text:
            return msg

        conversation_text = _extract_conversation_text_from_session(session)
        state_text = ""
        if session is not None and getattr(session, "state", None):
            try:
                state_text = json.dumps(dict(session.state), default=str)
            except Exception:
                state_text = str(getattr(session, "state", ""))
        combined_text = (
            f"[Conversation History]\n{conversation_text}\n\n[Session State]\n{state_text}\n\n[Latest User Message]\n{latest_user_text}"
            if conversation_text or state_text
            else latest_user_text
        )

        estimated_tokens = _estimate_session_token_load(session, latest_user_text)
        if estimated_tokens <= limits["safe_input"]:
            return msg

        logger.warning(
            "[TOKEN_GUARD] endpoint=%s session=%s estimated_conversation_tokens=%s exceeds_safe_limit=%s provider=%s",
            endpoint_name,
            session_id,
            estimated_tokens,
            limits["safe_input"],
            getattr(config, "LLM_PROVIDER", ""),
        )
        compressed_text = _summarize_text_for_token_guard(
            text=combined_text,
            target_tokens=limits["guard_target"],
        )
        compressed_tokens = rate_limiter.count_tokens(compressed_text)
        logger.warning(
            "[TOKEN_GUARD] endpoint=%s session=%s compressed_tokens=%s",
            endpoint_name,
            session_id,
            compressed_tokens,
        )

        if compressed_tokens > limits["safe_input"]:
            hard_char_limit = limits["safe_input"] * 3
            compressed_text = compressed_text[:hard_char_limit]
            compressed_tokens = rate_limiter.count_tokens(compressed_text)
            logger.warning(
                "[TOKEN_GUARD] endpoint=%s session=%s hard-truncated_tokens=%s",
                endpoint_name,
                session_id,
                compressed_tokens,
            )

        history_summary_text = _summarize_text_for_token_guard(
            text=conversation_text if conversation_text else combined_text,
            target_tokens=limits["guard_target"],
        )
        if (
            session_id
            and session_service
            and app_name
            and user_id
            and history_summary_text
        ):
            session_recreated = await _recreate_session_with_summary_event(
                session_service=session_service,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                summary_text=history_summary_text,
                fallback_state=getattr(session, "state", {}) if session else {},
            )
            if session_recreated:
                latest_only_tokens = rate_limiter.count_tokens(latest_user_text)
                if latest_only_tokens <= limits["safe_input"]:
                    return msg

                latest_user_text = _summarize_text_for_token_guard(
                    text=latest_user_text,
                    target_tokens=limits["guard_target"],
                )

        guarded_prompt = (
            "[Compressed Conversation Context]\n"
            f"{compressed_text}\n\n"
            "[Current User Request]\n"
            f"{latest_user_text}"
        )
        return types.Content(role="user", parts=[types.Part(text=guarded_prompt)])
    except Exception as exc:
        logger.warning(
            "[TOKEN_GUARD] endpoint=%s session=%s skipped due to error: %s",
            endpoint_name,
            session_id,
            exc,
        )
        return msg





# NOTE: _build_orchestrator_app is defined earlier in this module (with EventsCompactionConfig).
# The duplicate plain version has been removed to avoid overwriting the compaction-enabled one.






async def manage_llm_rate_limits(
    event: Event,
    session_id: str,
    buffer_tokens: int = 100
):
    """
    Enforces RPM & TPM without ever resetting cumulative token counts.
    Uses rolling window accounting to calculate proactive wait time.
    """

    now = datetime.utcnow()

    session_context = _load_session_context(session_id)

    if "usage_metadata" not in session_context:
        session_context["usage_metadata"] = {
            "total_tokens": 0,        # cumulative
            "window_tokens": 0,       # rolling window
            "window_requests": 0,
            "window_start": now.isoformat(),
            "last_request": None
        }

    usage = session_context["usage_metadata"]

    window_start = datetime.fromisoformat(usage["window_start"])
    elapsed = (now - window_start).total_seconds()

    # Slide window (do NOT reset total tokens)
    if elapsed >= WINDOW_SECONDS:
        usage["window_tokens"] = 0
        usage["window_requests"] = 0
        usage["window_start"] = now.isoformat()
        elapsed = 0

    # Estimate upcoming usage
    event_tokens = (
        event.usage_metadata.total_token_count
        if event.usage_metadata
        else 0
    )

    projected_window_tokens = (
        usage["window_tokens"] + event_tokens + buffer_tokens
    )
    projected_window_requests = usage["window_requests"] + 1

    # Calculate proactive wait
    wait_seconds = 0

    if projected_window_tokens >= LLM_TPM_LIMIT:
        wait_seconds = WINDOW_SECONDS - elapsed

    if projected_window_requests >= LLM_RPM_LIMIT:
        wait_seconds = max(wait_seconds, WINDOW_SECONDS - elapsed)

    # ⬇️ HARD CAP WAIT TIME (5 minutes)
    wait_seconds = min(max(wait_seconds, 0), MAX_WAIT_SECONDS)

    # Wait BEFORE limit is hit
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)

        # Slide window after waiting
        now = datetime.utcnow()
        usage["window_tokens"] = 0
        usage["window_requests"] = 0
        usage["window_start"] = now.isoformat()

    # Update counters AFTER call completes
    usage["total_tokens"] += event_tokens
    usage["window_tokens"] += event_tokens
    usage["window_requests"] += 1
    usage["last_request"] = datetime.utcnow().isoformat()

    _update_session_context(session_id, {"usage_metadata": usage})


@router.post("/send")
async def send_message(
    request: MessageRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    try:
        if not llm_is_configured():
            return [{
                "text_response": (
                    "No LLM API key is configured for your account. "
                    "Open Launchpad Settings → LLM API, enable **OpenAI (ChatGPT)** or **Gemini**, "
                    "save your key, then retry. "
                    "If you already saved a key, the backend may have restarted with "
                    "in-memory storage — save the key again."
                ),
                "tool_response": {},
                "should_update": False,
            }]

        req = request.dict()
        session_id = req.get("sessionId")
        user_message = req["newMessage"]['parts'][0]['text']
        effective_user_id = current_user.user_key or req.get("userId")

        agent_context_string = ""
        data_dictionary_context = ""
        final_message_to_agent = user_message
        current_session_data = _load_session_context(session_id)
        profiling_json_data = current_session_data.get("initial_profiling_report", "")

        print("Request received:", req)

        stage = get_text_between_brackets(req["newMessage"]['parts'][0]['text'])
        logger.info(f"[OUTPUT_FORMAT_FIX] Detected stage from message: '{stage}'")
        logger.info(f"[OUTPUT_FORMAT_FIX] Using two-agent approach with static schemas")
        logger.info(f"[OUTPUT_FORMAT_FIX] Orchestrator will route to: "
                   f"{'profiling_agent_anomaly' if stage.lower() == 'data anomaly analysis' else 'profiling_agent'}")

      
        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )

        app_name = req["appName"]
	    
        try:
            session = await session_service.get_session(
                app_name=app_name,
                user_id=effective_user_id,
                session_id=session_id
            )
            
            state_update_event = Event(
                author="system",
                invocation_id=f"sys-inv-{uuid.uuid4()}",
                actions=EventActions(state_delta={"is_stream": False})
            )

            # Append the event to persist the state change
            await session_service.append_event(session=session, event=state_update_event)
            logging.info(f"Appended is_stream=False event to session {session_id}")

        except Exception as e:
            logging.error(f"Could not append event to session {session_id}: {e}")
            return [{'text_response': f'Could not set session state: {e}', 'tool_response':{}, 'should_update':False}]

        session = await _maybe_compact_openai_session_before_run(
            session_service=session_service,
            session=session,
            app_name=app_name,
            user_id=effective_user_id,
            session_id=session_id,
            user_message=user_message,
            stage=stage,
            endpoint_name="/send",
        )

        # Check for data_dictionary from the current request's additional_data
        additional_data = req.get('additional_data') or {}
        if additional_data.get('data_dictionary'):
            data_dictionary_reference = req['additional_data']['data_dictionary']
            data_dictionary_content = get_data_ditionary(data_dictionary_reference)
            data_dictionary_context = f"\n\n Data Dictionary Content: {json.dumps(data_dictionary_content)}"

        # Check the profiling session context for a previously uploaded vendor DD
        current_session_data = _load_session_context(session_id)
        if "data_dict_file_path" in current_session_data:
            dd_path = current_session_data["data_dict_file_path"]
            if isinstance(dd_path, list):
                dd_path = dd_path[0] if dd_path else None

            print(f"{user_message} :: user_message")
            if "[Data Dictionary" in user_message:
                logging.info(f"Vendor DD found for session {session_id}. Overriding prompt to trigger Validation Agent.")
                final_message_to_agent = (
                    "[Data Dictionary Validation]\n"
                    f"Vendor DD Path: {dd_path}"
                )

                logging.info("=== VALIDATION TRIGGERED ===")
                logging.info(f"Vendor DD Path injected into final_message_to_agent: {dd_path}")
                logging.info(f"Full final_message_to_agent:\n{final_message_to_agent}")

            agent_context_string = (
                f"\n\n--- System Context ---\n"
                f"A vendor data dictionary has been provided in this session.\n"
                f"Vendor DD Path: {dd_path}"
            )

        if str(stage or "").strip().lower() == "data anomaly analysis":
            try:
                from utils.anomaly_table_resolver import collect_anomaly_source_tables

                profiling_full_results = session.state.get("profiling_full_results")
                if not profiling_full_results:
                    artifact_uri = str(
                        current_session_data.get("profiling_full_results_uri") or ""
                    ).strip()
                    if artifact_uri:
                        try:
                            profiling_full_results = load_resume_json_artifact(artifact_uri)
                            if profiling_full_results:
                                session.state["profiling_full_results"] = profiling_full_results
                        except Exception:
                            logger.exception(
                                "[send] Failed to hydrate profiling_full_results for anomaly stage"
                            )

                source_tables = collect_anomaly_source_tables(
                    dict(session.state or {}),
                    session_id,
                )
                if source_tables:
                    anomaly_state_event = Event(
                        author="system",
                        invocation_id=f"anomaly-inject-{uuid.uuid4()}",
                        actions=EventActions(
                            state_delta={"anomaly_source_tables": source_tables}
                        ),
                    )
                    await session_service.append_event(
                        session=session, event=anomaly_state_event
                    )
                    agent_context_string += (
                        "\n\n--- Anomaly Analysis Tables ---\n"
                        "Use ONLY these uploaded source table references when calling "
                        "data_anomaly_analysis_tool:\n"
                        f"{', '.join(source_tables)}\n"
                        "Do NOT use data dictionary file names or IBC profiling dashboard "
                        "tables (_nulls___lengths, _defaults, _foreign_keys)."
                    )
                    logging.info(
                        "[send] Injected anomaly source tables: %s",
                        source_tables,
                    )
                else:
                    logging.warning(
                        "[send] No anomaly source tables found for session %s",
                        session_id,
                    )
            except Exception as e:
                logging.error("[send] Failed to inject anomaly source tables: %s", e)

        print("stage", stage)
        if stage == "Data Dictionary":
            send_root_agent = data_dict_agent
        elif str(stage or "").strip().lower() == "data anomaly analysis":
            send_root_agent = profiling_agent_anomaly
        else:
            send_root_agent = root_agent
        orchestrator_app = _build_orchestrator_app(app_name, send_root_agent)

        runner = Runner(app=orchestrator_app, session_service=session_service)
        print("Runner", runner)

        # 4. Assemble the final, enriched message in one clean step
        enriched_message_text = (
            final_message_to_agent
            + data_dictionary_context
            + agent_context_string
            + "\n REGENRATE THE ANSWER EVEN IF ALREADY GENRATED"
        )

        msg = types.Content(role="user", parts=[types.Part(text=enriched_message_text)])
        msg = await _guard_message_tokens(
            msg,
            session_id,
            "/send",
            session=session,
            session_service=session_service,
            app_name=app_name,
            user_id=effective_user_id,
        )


        response_parts = []

        # Add a clean print statement for debugging
        print("\n" + "="*20 + " [FINAL AGENT PROMPT] " + "="*20)
        print("--- Sending the following enriched prompt to the Root Agent: ---")
        print(enriched_message_text)
        print("="*65 + "\n")

        print("="*100)
        print(effective_user_id)
        print(session_id)
        print(msg)
        print("="*100)
        events = ""
        event_count = 0

        final_agent_text_response = None
        final_sent = False
        captured_anomaly_tool_response = None

        def resolve_anomaly_tool_response(current_tool_response):
            logging.info(f"[ANOMALY RESOLVE] stage='{stage}' | captured={captured_anomaly_tool_response is not None} | has_table_reports={'table_anomaly_reports' in (captured_anomaly_tool_response or {})}")
            is_anomaly_stage = str(stage or "").strip().lower() == "data anomaly analysis"
            if not is_anomaly_stage:
                return current_tool_response
            if not isinstance(captured_anomaly_tool_response, dict):
                return current_tool_response
            if "table_anomaly_reports" not in captured_anomaly_tool_response:
                return current_tool_response
            return captured_anomaly_tool_response

        async for event in runner.run_async(
            user_id=effective_user_id,
            session_id=session_id,
            new_message=msg,
            run_config=_build_token_guard_run_config()
        ):

            await manage_llm_rate_limits(event, session_id=session_id if session_id else "default_session", buffer_tokens=300)
 
            

            print("\n" + "="*20 + f" [RECEIVED EVENT #{event_count}] " + "="*20)
            print(f"EVENT TYPE: {type(event)}")
            print("--- FULL EVENT CONTENT: ---")
            print(event)
            print("="*65 + "\n")
            logging.info(f"--- [EVENT LOG] Received event of type: {type(event)} ---")
            logging.info(event)

            event_count += 1
            print(f"Received event #{event_count}")

            # ------------------------------------------------------------------------------------
            #  SAFETY CAP AGAINST INFINITE EVENT LOOPS
            # ------------------------------------------------------------------------------------
            MAX_EVENTS = 1000  # or increase as needed

            if event_count > MAX_EVENTS:
                logging.error("MAX EVENT LIMIT EXCEEDED – POSSIBLE INFINITE LOOP")

                return [{
                    "text_response": "Internal error: maximum processing limit reached.",
                    "tool_response": {},
                    "status": 0,
                    "should_update": False
                }]
            # ------------------------------------------------------------------------------------

      
            with open("events.txt", "a", encoding="utf-8") as f:
                f.write(f"EVENT {event_count}: \n\n {event}\n")

            try:
                _parts = getattr(getattr(event, "content", None), "parts", None) or []
                for _p in _parts:
                    _fr = getattr(_p, "function_response", None)
                    if _fr and getattr(_fr, "name", "") == "data_anomaly_analysis_tool":
                        _raw = getattr(_fr, "response", None)
                        if isinstance(_raw, dict):
                            captured_anomaly_tool_response = _raw
                            logging.info(f"[ANOMALY CAPTURE] Captured FunctionResponse. Keys: {list(_raw.keys())}")
            except Exception:
                pass


            # --- HANDLE MALFORMED FUNCTION CALLS (bad set_model_response usage) ---
            if hasattr(event, "error_code") and event.error_code == "MALFORMED_FUNCTION_CALL":
                print("\n" + "="*20 + " [MALFORMED FUNCTION CALL DETECTED] " + "="*20)
                # logging.error("[MALFORMED] MALFORMED_FUNCTION_CALL detected in /send loop.")
                # logging.error("[MALFORMED] Raw event snapshot (truncated): %s", str(event)[:3000])

                # Use senior’s approach → trusted standard formatter
                output = format_output(stage, event, session_id=session_id if session_id else "default_session")
                # logging.info("[MALFORMED] format_output fallback result extracted.")
                # logging.info(str(output)[:2000])

                # 2. Normalize: output can be a list or a single dict
                core = None
                if isinstance(output, list):
                    core = output[0] if output else {}
                elif isinstance(output, dict):
                    core = output
                else:
                    core = {}

                # 3. If this looks like a proper validation response, just add our flags
                if isinstance(core, dict) and isinstance(core.get("tool_response"), dict) \
                and "validation_audit_log" in core["tool_response"]:
                    # logging.info("[MALFORMED] Detected validation_audit_log in format_output result. Wrapping with flags.")
                    text_response= core.get("text_response", "")
                    tool_response = core.get("tool_response", {})
                    tool_response = _enrich_column_analysis(
                        tool_response, profiling_json_data, session_id
                    )

                    final_resp = {
                        "text_response": text_response ,
                        "tool_response": tool_response,
                        "status": 1 if text_response and tool_response else 0,          # success
                        "is_dd_present": 1,   # vendor DD was present
                        "should_update": True # UI must treat as final Phase-5
                    }

                    # logging.info(json.dumps(final_resp, indent=2))
                    return [final_resp]

                # 4. Fallback: no audit log, just return whatever format_output produced
                # logging.warning("[MALFORMED] format_output result has no validation_audit_log. Returning raw output.")
                if isinstance(output, list):
                    return output
                else:
                    return [output]


            # --- HANDLE CONTENT (FUNCTION CALLS + HITL) ---
            if hasattr(event, "content") and event.content and hasattr(event.content, "parts"):
                print("\n" + "="*20 + " [CONTENT PARTS DETECTED] " + "="*20)
                for part in event.content.parts:

                    # --- HANDLE set_model_response (FINAL PHASE-5 NORMAL PATH) ---
                    if hasattr(part, "function_call") and part.function_call and part.function_call.name == "set_model_response":
                        # logging.info("[SET_MODEL_RESPONSE] Handler entered. This is the final response call.")

                        args = dict(part.function_call.args)
                        # logging.info("[SET_MODEL_RESPONSE] args keys: %s", list(args.keys()))

                        # Extract arguments safely
                        text_response = args.get("text_response") or "Workflow complete."
                        # Coerce tool_response to a dict: Gemini sometimes returns it as a
                        # JSON string, which would crash the dict indexing just below with
                        # "string indices must be integers, not 'str'".
                        tool_response = _ensure_tool_response_dict(args.get("tool_response"))
                        tool_response = resolve_anomaly_tool_response(tool_response)
                        tool_response = _enrich_column_analysis(
                            tool_response, profiling_json_data, session_id
                        )

                        artifact_delta = args.get("artifact_delta")
                        has_artifact_log = (
                            isinstance(artifact_delta, dict)
                            and 'final_audit_log' in artifact_delta
                        )
                        # logging.info("[SET_MODEL_RESPONSE] final_audit_log present: %s", has_artifact_log)

                        if has_artifact_log:
                            # REAL Validation Final Phase-5
                            final_response = {
                                "text_response": text_response,
                                "tool_response": {
                                    "status": "success",
                                    "validation_audit_log": artifact_delta["final_audit_log"]
                                },
                                "status": 1,
                                "is_dd_present": 1,
                                "should_update": True  # Phase-5 should update state
                            }

                            # logging.info("[SET_MODEL_RESPONSE] FINAL RESPONSE (Validation Phase-5)")
                            final_sent = True
                            return [final_response]

                        # NON-VALIDATION PATH — leave behavior same as before (no DD, no audit log)
                        # resolve_anomaly_tool_response is a no-op for all non-anomaly stages
                        final_response = {
                            "text_response": text_response,
                            "tool_response": resolve_anomaly_tool_response(tool_response),
                            "status": 0,
                            "should_update": False
                        }
                        _capture_profiling_chat_response(session_id, stage, final_response)

                        # logging.info("[SET_MODEL_RESPONSE] FINAL RESPONSE (Non-Phase-5)")
                        final_sent = True
                        return [final_response]

                    # Check for the specific 'needs_user_input' status, which indicates a pause.
                    if hasattr(part, "text") and part.text and "needs_user_input" in part.text:
                        try:
                            # The agent's response is a JSON string embedded in the text.
                            json_str_match = re.search(r'\{.*\}', part.text, re.DOTALL)
                            if json_str_match:
                                tool_response_json = json.loads(json_str_match.group())

                                if tool_response_json.get("tool_response").get("status") == "needs_user_input":
                                    logging.info("="*20 + " [SUCCESS] Caught HITL 'needs_user_input' state. " + "="*20)
                                    final_response = {
                                        "text_response": tool_response_json.get("text_response"),
                                        "tool_response": tool_response_json.get("tool_response"),
                                        "status": 0,            # paused state
                                        "should_update": True 
                                    }
                                    # HITL path: keep existing behavior (object, not list)
                                    
                                    if isinstance(final_response.get("tool_response"), dict):
                                        final_response["tool_response"] = _enrich_column_analysis(
                                            final_response["tool_response"],
                                            profiling_json_data,
                                            session_id,
                                        )

                                    return final_response
                        except (json.JSONDecodeError, AttributeError):
                            logging.error("Failed to parse JSON from agent's text response for HITL.")

            # --- HANDLE STATE DELTA (STREAMED FINAL ARTIFACTS) ---
            if (hasattr(event, "actions") and event.actions and hasattr(event.actions, "state_delta")):
                print("\n" + "="*20 + " [STATE DELTA DETECTED] " + "="*20)
                if 'final_audit_log' in event.actions.state_delta:
                    logging.info("="*20 + " [SUCCESS] Validation Agent finished. " + "="*20)
                    logging.info(f"Event: {event}")

                    # Construct the final response object the UI expects
                    final_response = {
                        "text_response": "Data Dictionary Validation Complete. See the audit log for details.",
                        "tool_response": {
                            "validation_audit_log": event.actions.state_delta['final_audit_log'],
                            "status": "success"
                        },
                        "status": 1,
                        "is_dd_present": 1,
                        "should_update": True  # Phase-5 should update state
                    }
                    logging.info("[STATE_DELTA] FINAL validation response → immediate return")
                    final_sent = True
                    return [final_response]

                elif 'final_profiling_response' in event.actions.state_delta:
                    logging.info("="*100)
                    logging.info(f"Event: {event}")
                    logging.info("="*100)

                    _resp = event.actions.state_delta['final_profiling_response']
                    # Override tool_response with captured raw tool data for anomaly stage
                    # (LLM truncates table_anomaly_reports when writing state_delta)
                    if isinstance(_resp, dict) and 'tool_response' in _resp:
                        _resp['tool_response'] = resolve_anomaly_tool_response(_resp['tool_response'])
                    _capture_profiling_chat_response(session_id, stage, _resp)
                    response_parts.append(_resp)
                    break

                elif 'final_data_dict_response' in event.actions.state_delta:
                    logging.info("="*100)
                    logging.info(f"Event: {event}")
                    logging.info("="*100)

                    response_parts.append(event.actions.state_delta['final_data_dict_response'])
                
                    break

                elif 'metadata_excel_file' in event.actions.state_delta:
                    logging.info("="*100)
                    logging.info(f"Event: {event}")
                    logging.info("="*100)

                    response_parts.append(event.actions.state_delta['metadata_excel_file'])
                    break

                elif 'final_metadata_response' in event.actions.state_delta:
                    logging.info("[send] final_metadata_response captured")
                    response_parts.append(
                        event.actions.state_delta['final_metadata_response']
                    )
                    break

                elif 'final_similarity_response' in event.actions.state_delta:
                    logging.info("="*100)
                    logging.info(f"Event: {event}")
                    logging.info("="*100)

                    response_parts.append(event.actions.state_delta['final_similarity_response'])
                    break

            # --- B. HANDLE CONVERSATIONAL OUTPUTS (NON-FINAL) ---
            if (hasattr(event, "content") and event.content and hasattr(event.content, "parts")):
                print("\n" + "="*20 + " [CONVERSATIONAL CONTENT DETECTED] " + "="*20)
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        # Always store the latest text response from the agent.
                        final_agent_text_response = part.text
                        logging.info(f"--- [INFO] Captured agent's conversational response. ---")

        # --- FINAL RESPONSE ASSEMBLY (AFTER THE LOOP) ---

        if response_parts:
            # logger.info("Successfully captured a final state response. Sending to UI.")
            
            print(f"--- [FINAL RESPONSE PARSER] Analyzing state delta response: ---...")
            tool_response = response_parts[0].get("tool_response", {})
            if isinstance(tool_response, dict):
                response_parts[0]["tool_response"] = _enrich_column_analysis(
                    tool_response, profiling_json_data, session_id
                )

            return response_parts

        # Priority 2: If the loop finished and we have a final text response, parse it.
        if final_agent_text_response:
            print(f"--- [FINAL RESPONSE PARSER] Analyzing text: ---...")
            logger.info("Processing final conversational response from agent.")
            print(f"--- [FINAL RESPONSE PARSER] Analyzing text: ---\n{final_agent_text_response[:500]}...")

            # Use a robust regex to find any JSON object within the text
            json_match = re.search(r'\{.*\}', final_agent_text_response, re.DOTALL)

            if json_match:
                try:
                    tool_response_obj = json.loads(json_match.group())
                    print("  -> Successfully parsed an embedded JSON object.")

                    # This is the crucial check for the HITL "pause" state
                    if tool_response_obj.get("tool_response").get("status") == "needs_user_input":
                        logging.info("="*20 + " [SUCCESS] Caught HITL 'needs_user_input' state. " + "="*20)
                        final_response = {
                            "text_response": tool_response_obj.get("text_response"),
                            "tool_response": tool_response_obj.get("tool_response")
                        }


                        if isinstance(final_response.get("tool_response"), dict):
                            final_response["tool_response"] = _enrich_column_analysis(
                                final_response["tool_response"],
                                profiling_json_data,
                                session_id,
                            )

                        return [final_response]

                    # If it's another kind of JSON, wrap it
                    final_response = {
                        "text_response": tool_response_obj.get("text_response"),
                        "tool_response": resolve_anomaly_tool_response(tool_response_obj.get("tool_response")),
                        "should_update": False
                    }

                    if str(stage or "").strip().lower() != "data anomaly analysis":
                        if isinstance(final_response.get("tool_response"), dict):
                            final_response["tool_response"] = _enrich_column_analysis(
                                final_response["tool_response"],
                                profiling_json_data,
                                session_id,
                            )

                    return [final_response]

                except json.JSONDecodeError:
                    # The regex found braces, but it wasn't valid JSON. Treat as plain text.
                    print("  -> Found something that looked like JSON, but failed to parse.")
                    pass

            # If no JSON is found, return the plain text response.
            print("  -> No embedded JSON found. Returning as plain text.")
            final_response = {
                "text_response": final_agent_text_response,
                "tool_response": resolve_anomaly_tool_response({}),
                "should_update": False
            }
            return [final_response]

        # Priority 3: If we got here, the agent finished silently.
        logger.warning("[FALLBACK] Agent workflow finished, but no final response was captured.")
        final_resp = {
            'text_response': 'Something went wrong, the agent did not produce a final answer.',
            'tool_response': {},
            'status': 0,
            'should_update': False
        }
        return [final_resp]

    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    
    except Exception as e:
        logger.error(f"--- [CRITICAL ERROR in /send] An unexpected error occurred: {e} ---", exc_info=True)
        return [{
            'text_response': _friendly_llm_failure_message(e),
            'tool_response': {},
            'should_update': False
        }]






def _enrich_dd_with_bq_stats(dd_response: dict) -> dict:
    """
    Fetches the full data dictionary from BigQuery, then enriches each row with:
    - default_value: the single value if ALL rows in the source table share it, else blank
    - most_occurrences: top-N most frequent values per column from the source table
    Returns the enriched result list under dd_response["result"].
    """
    from collections import defaultdict

    top_n = getattr(config, "DD_MOST_OCCURRENCES_TOP_N", 5)
    client = get_bigquery_client()

    # Step 1: fetch the DD rows from BQ if not already present
    result = dd_response.get("result")
    if not result:
        table_id = (
            dd_response.get("data_dictionary_table_id")
            or dd_response.get("table_name")
            or dd_response.get("data_dictionary_table_name")
        )
        if not table_id:
            logger.warning("[DD_ENRICH] No result or data_dictionary_table_id in dd_response")
            return dd_response
        try:
            query = f"SELECT * FROM `{table_id}`"
            rows = client.query(query).result()
            result = [dict(row) for row in rows]
            # Normalise BQ column names to snake_case keys the UI expects
            key_map = {
                "File Name": "file_name",
                "Attribute Name": "field_name",
                "Logical Attribute Name": "business_name",
                "Attribute Description": "field_description",
                "Data Type": "data_type",
                "Length": "length",
                "Precision": "precision",
                "Format": "format",
                "Nullability": "nullable",
                "Default Value": "default_value",
                "Most Occurrences": "most_occurrences",
                "Primary Key": "primary_key",
                "Foreign Key": "foreign_key",
            }
            result = [{key_map.get(k, k): v for k, v in row.items()} for row in result]
        except Exception as exc:
            logger.warning("[DD_ENRICH] Failed to fetch DD table %s: %s", table_id, exc)
            return dd_response

    if not result:
        dd_response["result"] = []
        return dd_response

    # Step 2: group DD rows by file_name to query each source table once
    # Rows with no file_name are collected separately and returned as-is (no BQ enrichment possible)
    tables: dict = defaultdict(list)
    no_file_name_rows = []
    for row in result:
        file_name = row.get("file_name") or ""
        if file_name:
            tables[file_name].append(row)
        else:
            no_file_name_rows.append(row)

    # Step 3: for each source table run one APPROX_TOP_COUNT query covering all columns
    for table_name, rows in tables.items():
        full_table = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"
        columns = [r["field_name"] for r in rows if r.get("field_name")]
        if not columns:
            continue
        try:
            parts = [f"APPROX_TOP_COUNT(`{col}`, {top_n}) AS `{col}_top`" for col in columns]
            sql = f"SELECT COUNT(*) AS total_rows, {', '.join(parts)} FROM `{full_table}`"
            bq_row = next(iter(client.query(sql).result()))
            total_rows = bq_row["total_rows"]

            for dd_row in rows:
                col = dd_row.get("field_name")
                if not col:
                    continue
                top_info = bq_row.get(f"{col}_top") or []
                dd_row["most_occurrences"] = [
                    str(e["value"]) for e in top_info if e["value"] is not None
                ]
                if top_info and total_rows > 0 and top_info[0]["count"] >= total_rows:
                    dd_row["default_value"] = str(top_info[0]["value"])
                else:
                    dd_row["default_value"] = ""
        except Exception as exc:
            logger.warning("[DD_ENRICH] BQ query failed for table %s: %s", table_name, exc)
            for dd_row in rows:
                dd_row.setdefault("most_occurrences", [])
                dd_row.setdefault("default_value", "")

    dd_response["result"] = result + no_file_name_rows
    return dd_response


@router.post("/data-dictionary")
async def generate_data_dictionary(request: MessageRequest):
    try:
        req = request.dict()
        session_id = req.get("sessionId")
        user_message = req["newMessage"]["parts"][0]["text"]

        run_config = _build_data_dictionary_run_config()

        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )

        # ---------------------------------------------------------------------
        # 1. Resolve Session
        # ---------------------------------------------------------------------
        session = await session_service.get_session(
            app_name=req["appName"],
            user_id=req["userId"],
            session_id=session_id
        )

        await session_service.append_event(
            session=session,
            event=Event(
                author="system",
                invocation_id=f"sys-inv-{uuid.uuid4()}",
                actions=EventActions(state_delta={"is_stream": False})
            )
        )

        session = await _maybe_compact_openai_session_before_run(
            session_service=session_service,
            session=session,
            app_name=req["appName"],
            user_id=req["userId"],
            session_id=session_id,
            user_message=user_message,
            stage="Data Dictionary",
            endpoint_name="/data-dictionary",
        )

        # ---------------------------------------------------------------------
        # 2. Resolve Vendor Data Dictionary (if exists)
        # ---------------------------------------------------------------------
        agent_context = ""
        final_prompt = user_message

        if session_id:
            session_data = _load_session_context(session_id)
            dd_paths = session_data.get("data_dict_file_path")
            if isinstance(dd_paths, list):
                print(f"Vendor DD found for session {session_id} at path: {dd_paths}")
                vendor_dd_path = dd_paths[0] if dd_paths else ""
                agent_context = (
                    "\n\n--- System Context ---\n"
                    "A vendor data dictionary is available for validation.\n"
                    f"Vendor DD Path: {vendor_dd_path}\n"
                )

                # Force validation mode if DD exists
                final_prompt = (
                    "[Data Dictionary Validation]\n"
                    f"Vendor DD Path: {vendor_dd_path}"
                )

        # ---------------------------------------------------------------------
        # 3. Initialize BigQuery Table for Data Dictionary
        # ---------------------------------------------------------------------
        bq_table_id = f"datadict_{uuid.uuid4()}"
        full_bq_table_id = create_data_dictionary_table(bq_table_id)
        
        # ---------------------------------------------------------------------
        # 4. Build Agent & Runner (DD ONLY)
        # ---------------------------------------------------------------------
        orchestrator_app = _build_orchestrator_app(
            req["appName"],
            data_dict_agent
        )

        runner = Runner(
            app=orchestrator_app,
            session_service=session_service
        )

        msg = types.Content(
            role="user",
            parts=[types.Part(
                text=final_prompt + agent_context + f"\n\nBigQuery Table for saving Data Dictionary: {full_bq_table_id}"
            )]
        )
        msg = await _guard_message_tokens(
            msg,
            session_id,
            "/data-dictionary",
            session=session,
            session_service=session_service,
            app_name=req["appName"],
            user_id=req["userId"],
        )

        # ---------------------------------------------------------------------
        # 4. Run Agent (SHORT, DETERMINISTIC LOOP)
        # ---------------------------------------------------------------------
        i = 0
        async for event in runner.run_async(
            user_id=req["userId"],
            session_id=session_id,
            new_message=msg,
            run_config=run_config
        ):
            print("*" * 50)
            print(event)
            
            await manage_llm_rate_limits(event, session_id=session_id if session_id else "default_session", buffer_tokens=300)

            
            with open("dd_events.txt", "a", encoding="utf-8") as f:
                f.write(f"EVENT {i}: \n\n {event}\n")

            i += 1

            print("*" * 50)
            # ---- FINAL DD GENERATION ----
            if (
                hasattr(event, "actions")
                and event.actions
                and hasattr(event.actions, "state_delta")
            ):
                sd = event.actions.state_delta

                if "final_data_dict_response" in sd:
                    dd_resp = sd["final_data_dict_response"]
                    if isinstance(dd_resp, dict):
                        dd_resp = _enrich_dd_with_bq_stats(dict(dd_resp))
                    return [{
                        "text_response": "Data Dictionary Generated Successfully.",
                        "tool_response": dd_resp,
                        "status": 1,
                        "should_update": True
                    }]

                if "final_audit_log" in sd:
                    return [{
                        "text_response": "Data Dictionary Validation Complete.",
                        "tool_response": {
                            "status": "success",
                            "validation_audit_log": sd["final_audit_log"]
                        },
                        "status": 1,
                        "is_dd_present": 1,
                        "should_update": True
                    }]

        # ---------------------------------------------------------------------
        # 5. Fallback
        # ---------------------------------------------------------------------
        return [{
            "text_response": "Data Dictionary agent completed without output.",
            "tool_response": {},
            "status": 0,
            "should_update": False
        }]

    except Exception as e:
        logger.error("[DATA-DICTIONARY] Critical error", exc_info=True)
        return [{
            "text_response": f"Failed to generate data dictionary: {e}",
            "tool_response": {},
            "status": 0,
            "should_update": False
        }]



@router.post("/similarity-check")
async def similarity_check(request: MessageRequest):
    """
    Dedicated endpoint for Smart Similarity Agent (SequentialAgent with 2 phases)
    Phase 1: Semantic matching - identifies potential column matches
    Phase 2: Overlap validation - calculates data overlap and generates final report

    Args:
        request: MessageRequest with similarity check parameters
            - dart_database_name: Optional dataset override for DART reference tables
            - filters: Optional list of filter objects for dynamic WHERE clauses
    """
    try:
        req = request.dict()
        print("Similarity Check Request received:", req)

        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION)

        session_id = req.get("sessionId")
        app_name = req["appName"]

        session_id = req.get("sessionId")
        if not session_id:
            raise HTTPException(status_code=400, detail="sessionId is required")

        session = await session_service.get_session(
            app_name=app_name,
            user_id=req["userId"],
            session_id=session_id
        )

        orchestrator_app = _build_orchestrator_app(app_name, root_agent)

        runner = Runner(app=orchestrator_app, session_service=session_service)
        print("Runner initialized for similarity check")

        # Inject dart_dataset_id and filters to session state if provided
        state_delta = {}

        # Read dart_database_name from request body
        dart_database_name = req.get("dart_database_name")
        if dart_database_name:
            state_delta["dart_dataset_id"] = dart_database_name
            logging.info(f"[similarity-check] dart_dataset_id: Using custom dataset_id = {dart_database_name}")
        else:
            logging.info(f"[similarity-check] dart_dataset_id: No override provided, tools will use config.DART_DATASET_ID or config.BQ_DATASET_ID")

        # Read filters from request body (already a list, no need to parse JSON)
        filters = req.get("filters")
        if filters:
            state_delta["similarity_filters"] = filters
            logging.info(f"[similarity-check] Filters provided: {len(filters)} filter(s)")
            logging.info(f"[similarity-check] Filter details: {filters}")
        else:
            logging.info(f"[similarity-check] No filters provided")

        # Persist state_delta to session if we have any state to inject
        if state_delta:
            state_inject_event = Event(
                author="system",
                invocation_id=f"similarity-state-{uuid.uuid4()}",
                actions=EventActions(state_delta=state_delta)
            )
            await session_service.append_event(session=session, event=state_inject_event)
            logging.info(f"[similarity-check] State delta injected: {list(state_delta.keys())}")

        msg = types.Content(role="user", parts=[types.Part(text=req["newMessage"]['parts'][0]['text'])])
        msg = await _guard_message_tokens(
            msg,
            session_id,
            "/similarity-check",
            session=session,
            session_service=session_service,
            app_name=app_name,
            user_id=req["userId"],
        )

        response_parts = []
        event_count = 0
        final_sent = False  # <-- NEW FLAG

        print("="*100)
        print(f"User ID: {req['userId']}")
        print(f"Session ID: {session_id}")
        print(f"Message: {msg}")
        print("="*100)

        async for event in runner.run_async(
            user_id=req["userId"],
            session_id=session_id,
            new_message=msg,
            run_config=_build_token_guard_run_config()
        ):
            event_count += 1
            print(f"Received event #{event_count}")
            await manage_llm_rate_limits(event, session_id=session_id if session_id else "default_session", buffer_tokens=300)


            # ------------------------------------------------------------------------------------
            #  SAFETY CAP AGAINST INFINITE EVENT LOOPS
            # ------------------------------------------------------------------------------------
            MAX_EVENTS = 1000  # or increase as needed

            if event_count > MAX_EVENTS:
                logging.error("MAX EVENT LIMIT EXCEEDED – POSSIBLE INFINITE LOOP")

                return [{
                    "text_response": "Internal error: maximum processing limit reached.",
                    "tool_response": {},
                    "status": 0,
                    "should_update": False
                }]
            # ------------------------------------------------------------------------------------
    
            # PRIMARY CHECK: Look for final_similarity_response in state_delta (Phase 2 output)
            if (hasattr(event, "actions") and event.actions and hasattr(event.actions, "state_delta")):
                if 'final_similarity_response' in event.actions.state_delta:
                    logging.info("="*100)
                    logging.info(f"✅ FINAL SIMILARITY RESPONSE CAPTURED (Phase 2 Complete)")
                    logging.info(f"Event: {event}")
                    logging.info("="*100)

                    response_parts.append(event.actions.state_delta['final_similarity_response'])
                    break

            # INTERMEDIATE CHECK: Detect Phase 1 output and continue to Phase 2
            if (hasattr(event, "content") and event.content and
                hasattr(event.content, "parts") and event.content.parts and
                len(event.content.parts) > 0 and
                getattr(event.content.parts[0], "text", None)):

                obj = event.content.parts[0].text

                if isinstance(obj, str):
                    try:
                        parsed_obj = json.loads(obj)

                        # Check if this is Phase 1 intermediate output
                        if isinstance(parsed_obj, dict) and "tool_response" in parsed_obj:
                            tool_resp = parsed_obj.get("tool_response", {})
                            if isinstance(tool_resp, dict) and tool_resp.get("store_for_next_agent") == True:
                                logging.info("="*100)
                                logging.info(f"🔄 Phase 1 Complete - Semantic Matching Done")
                                logging.info(f"Potential matches found: {len(tool_resp.get('potential_matches', []))}")
                                logging.info("Continuing to Phase 2 (Overlap Validation)...")
                                logging.info("="*100)
                                # DON'T break - continue to Phase 2
                                continue

                        # If we get here, it's an unexpected format - log but continue
                        logging.warning(f"Unexpected JSON format in event #{event_count}: {str(parsed_obj)[:200]}")

                    except json.JSONDecodeError as e:
                        logging.warning(f"JSON decode error in event #{event_count}: {e}")
                        continue

            # ERROR HANDLING: Malformed function call
            if(hasattr(event, 'error_code') and event.error_code and event.error_code == 'MALFORMED_FUNCTION_CALL'):
                print("*"*100)
                print(f"MALFORMED_FUNCTION_CALL error in event #{event_count}")
                print(event)
                print("*"*100)

                result = extract_response_from_malformed_call(f"{event}")
                if result:
                    response_parts.append(result)
                    break

        # Final response handling
        if not response_parts:
            return [{'text_response': 'Smart Similarity analysis did not complete. No results found.', 'tool_response':{}, 'should_update':False}]

        return response_parts

    except Exception as e:
        logging.error(f"Error in similarity check: {e}")
        import traceback
        traceback.print_exc()
        return [{'text_response': f'Similarity check failed: {str(e)}', 'tool_response':{}, 'should_update':False}]




@router.post("/metadata_fill")
async def send_message(request: MessageRequest):
    stage = "metadata_fill"
    try:
        req = request.dict()

        session_id = req.get("sessionId")
        user_message = req["newMessage"]["parts"][0]["text"]


        try:
            
            session_service = VertexAiSessionService(
                project=config.GOOGLE_CLOUD_PROJECT,
                location=config.GOOGLE_CLOUD_LOCATION)

        
            # Ensure session exists

            # session_id = new_session_id.id
            app_name = req["appName"]
            session = None
            if session_id:
                session = await session_service.get_session(
                    app_name=app_name,
                    user_id=req["userId"],
                    session_id=session_id
                )
            

            orchestrator_app = _build_orchestrator_app(
               app_name,
                metadata_fill_agent,
            )

            runner = Runner(app=orchestrator_app, session_service=session_service)
            print("Runner", runner)

            
            bq_table_id = f"{uuid.uuid4()}"
            metadata_table_id, filespecs_table_id = create_metadata_and_filespecs_tables(bq_table_id,session_id)

            data_dict_table_id = _resolve_data_dictionary_table_id(session_id, session, req)
            if not data_dict_table_id:
                logging.warning(
                    "[metadata_fill] No data dictionary table id found for session %s",
                    session_id,
                )
                return [{
                    "text_response": (
                        "Metadata template generation requires a completed Data Dictionary step. "
                        "Please finish Step 3 (Data Dictionary), then retry."
                    ),
                    "tool_response": {},
                    "should_update": False,
                }]

            try:
                fast_fill = _fast_fill_metadata_from_datadict(
                    data_dict_table_id,
                    metadata_table_id,
                )
                if fast_fill.get("rows_written", 0) > 0:
                    _seed_filespecs_from_context(
                        filespecs_table_id,
                        session_id,
                        data_dict_table_id,
                    )
                    logging.info(
                        "[metadata_fill] Fast fill succeeded: %s rows from %s",
                        fast_fill["rows_written"],
                        data_dict_table_id,
                    )
                    return [_metadata_response_from_payload({
                        "message": fast_fill["message"],
                        "metadata_table_id": metadata_table_id,
                        "filespecs_table_id": filespecs_table_id,
                    })]
            except Exception as fast_fill_err:
                logging.warning(
                    "[metadata_fill] Fast fill failed, falling back to agent: %s",
                    fast_fill_err,
                    exc_info=True,
                )

            msg = types.Content(
                role="user",
                parts=[
                    types.Part(
                        text=(
                            f"{req['newMessage']['parts'][0]['text']} \n"
                            f"METADATA TABLE ID: {metadata_table_id} \n"
                            f"FILE SPECS TABLE ID: {filespecs_table_id} \n"
                            f"data dictionary table id: {data_dict_table_id}"
                        )
                    )
                ],
            )
            msg = await _guard_message_tokens(
                msg,
                session_id,
                "/metadata_fill",
                session=session,
                session_service=session_service,
                app_name=app_name,
                user_id=req["userId"],
            )
        
        except Exception as e:
            print(f"Error in metadata_fill: {e}")
            import traceback
            traceback.print_exc()
            return [{'text_response': f'Metadata Fill failed: {str(e)}', 'tool_response':{}, 'should_update':False}]


        response_parts = []

        print("="*100)
        print(req["userId"])
        print(session_id)
        print(msg)
        print("="*100)
        events = ""
        event_count = 0

        try:
            async for event in runner.run_async(

                user_id=req["userId"],
                session_id=session_id,
                new_message=msg,
                run_config=_build_token_guard_run_config()
            ):
                print("received event")
                
                event_count += 1
                print(f"Received event #{event_count}")
                await manage_llm_rate_limits(event, session_id=session_id if session_id else "default_session", buffer_tokens=300)

                # ------------------------------------------------------------------------------------
                #  SAFETY CAP AGAINST INFINITE EVENT LOOPS
                # ------------------------------------------------------------------------------------
                MAX_EVENTS = 1000  # or increase as needed

                if event_count > MAX_EVENTS:
                    logging.error("MAX EVENT LIMIT EXCEEDED - POSSIBLE INFINITE LOOP")

                    return [{
                        "text_response": "Internal error: maximum processing limit reached.",
                        "tool_response": {},
                        "status": 0,
                        "should_update": False
                    }]
                # ------------------------------------------------------------------------------------

             
                with open("events.txt", "a", encoding="utf-8") as f:
                    f.write(f"EVENT {event_count}: \n\n {event}\n")


                
                # await session_service.append_event(session=session, event=event)
                if (hasattr(event, "actions") and event.actions and hasattr(event.actions, "state_delta")):
                    if 'final_profiling_response' in event.actions.state_delta:
                        logging.info("="*100)
                        logging.info(f"Event: {event}")
                        logging.info("="*100)

                        profiling_payload = event.actions.state_delta['final_profiling_response']
                        _capture_profiling_chat_response(session_id, stage, profiling_payload)
                        response_parts.append(profiling_payload)
                        break

                    elif 'final_data_dict_response' in event.actions.state_delta:
                        logging.info("="*100)
                        logging.info(f"Event: {event}")
                        logging.info("="*100)

                        response_parts.append(event.actions.state_delta['final_data_dict_response'])
                        break
                    elif 'metadata_excel_file' in event.actions.state_delta:
                        logging.info("="*100)
                        logging.info(f"Event: {event}")
                        logging.info("="*100)

                        response_parts.append(event.actions.state_delta['metadata_excel_file'])
                        break
                    elif 'final_metadata_response' in event.actions.state_delta:
                        logging.info("[metadata_fill] final_metadata_response captured")
                        response_parts.append(
                            event.actions.state_delta['final_metadata_response']
                        )
                        break
                    # final_similarity_response
                    elif 'final_similarity_response' in event.actions.state_delta:
                        logging.info("="*100)
                        logging.info(f"Event: {event}")
                        logging.info("="*100)

                        response_parts.append(event.actions.state_delta['final_similarity_response'])
                        break
        
                if (hasattr(event, "content") and event.content and hasattr(event.content, "parts") and event.content.parts and len(event.content.parts) > 0 and getattr(event.content.parts[0], "text", None)):

                

                    obj = event.content.parts[0].text
                    
                    if isinstance(obj, dict):
                        response_parts.append(obj)
                        return response_parts
                    elif isinstance(obj, str):
                        new_obj = extract_json_from_string(obj)

                        print("new_obj", new_obj)

                        if isinstance(new_obj, dict) and 'metadata_table_id' in new_obj and 'filespecs_table_id' in new_obj:
                            response_parts.append(new_obj)
                            return response_parts
                    else:
                        output = format_output(stage, event)
                        response_parts.append(output)



                if(hasattr(event, 'error_code') and event.error_code and event.error_code == 'MALFORMED_FUNCTION_CALL'):
            
            
                    output = format_output(stage, event)

                    response_parts.append(output)

        except Exception as e:
            print(f"Error in metadata_fill: {e}")
            import traceback
            traceback.print_exc()
            return [{'text_response': f'Metadata Fill failed: {str(e)}', 'tool_response':{}, 'should_update':False}]

        
        if not response_parts:
            return [{'text_response': 'Something went wrong, Please Try again', 'tool_response':{}, 'should_update':False}]

        first = response_parts[0]
        if isinstance(first, dict) and (
            first.get("metadata_table_id") or first.get("filespecs_table_id")
        ):
            return [_metadata_response_from_payload(first)]
        return response_parts

    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        # raise HTTPException(status_code=500, detail=f"Error sending message: {e}")
        return [{'text_response': f'Something went wrong, Try again {e}', 'tool_response':{}, 'should_update':False}]
    
 



@router.post("/humnan_in_loop")
async def send_message(request: MessageRequest):
    try:
        req = request.dict()

        if req['additional_data']:
            if req['additional_data']['data_dictionary']:
                data_dictionary_reference = req['additional_data']['data_dictionary']
                data_dictionary_content = get_data_ditionary(data_dictionary_reference)
        
        print("Request received:", req)

        stage = get_text_between_brackets(req["newMessage"]['parts'][0]['text'])

        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION)

       
        # Ensure session exists

        session_id = req.get("sessionId")
        app_name = req["appName"]
        session = None
        if session_id:
            session = await session_service.get_session(
                app_name=app_name,
                user_id=req["userId"],
                session_id=session_id
            )
        
        orchestrator_app = _build_orchestrator_app(app_name, metadata_fill_agent)
    

        runner = Runner(app=orchestrator_app, session_service=session_service)
        print("Runner", runner)

        data_dictionary_context = f"\n\n Data Dictionary Context: {json.dumps(data_dictionary_content)}" if req['additional_data'] and req['additional_data']['data_dictionary'] else ""
        
        msg = types.Content(role="user", parts=[types.Part(text=f"{req['newMessage']['parts'][0]['text']} \n {data_dictionary_context} \n REGENRATE THE ANSWER EVEN IF ALREADY GENRATED")])
        msg = await _guard_message_tokens(
            msg,
            session_id,
            "/humnan_in_loop",
            session=session,
            session_service=session_service,
            app_name=app_name,
            user_id=req["userId"],
        )
        
        response_parts = []

        print("="*100)
        print(req["userId"])
        print(session_id)
        print(msg)
        print("="*100)
        events = ""
        event_count = 0

        # amazonq-ignore-next-line
        async for event in runner.run_async(

            user_id=req["userId"],
            session_id=session_id,
            new_message=msg,
            run_config=_build_token_guard_run_config()
        ):
            print("received event")
            
            event_count += 1
            print(f"Received event #{event_count}")

            # ------------------------------------------------------------------------------------
            #  SAFETY CAP AGAINST INFINITE EVENT LOOPS
            # ------------------------------------------------------------------------------------
            MAX_EVENTS = 1000  # or increase as needed

            if event_count > MAX_EVENTS:
                logging.error("MAX EVENT LIMIT EXCEEDED – POSSIBLE INFINITE LOOP")

                return [{
                    "text_response": "Internal error: maximum processing limit reached.",
                    "tool_response": {},
                    "status": 0,
                    "should_update": False
                }]
            # ------------------------------------------------------------------------------------


            with open("events.txt", "a", encoding="utf-8") as f:
                f.write(f"EVENT {event_count}: \n\n {event}\n")


            
            # await session_service.append_event(session=session, event=event)
            if (hasattr(event, "actions") and event.actions and hasattr(event.actions, "state_delta")):
                if 'final_profiling_response' in event.actions.state_delta:
                    logging.info("="*100)
                    logging.info(f"Event: {event}")
                    logging.info("="*100)

                    profiling_payload = event.actions.state_delta['final_profiling_response']
                    _capture_profiling_chat_response(session_id, stage, profiling_payload)
                    response_parts.append(profiling_payload)
                    break

                elif 'final_data_dict_response' in event.actions.state_delta:
                    logging.info("="*100)
                    logging.info(f"Event: {event}")
                    logging.info("="*100)

                    response_parts.append(event.actions.state_delta['final_data_dict_response'])
                    break
                elif 'metadata_excel_file' in event.actions.state_delta:
                    logging.info("="*100)
                    logging.info(f"Event: {event}")
                    logging.info("="*100)

                    response_parts.append(event.actions.state_delta['metadata_excel_file'])
                    break
                # final_similarity_response
                elif 'final_similarity_response' in event.actions.state_delta:
                    logging.info("="*100)
                    logging.info(f"Event: {event}")
                    logging.info("="*100)

                    response_parts.append(event.actions.state_delta['final_similarity_response'])
                    break
    
            if (hasattr(event, "content") and event.content and hasattr(event.content, "parts") and event.content.parts and len(event.content.parts) > 0 and getattr(event.content.parts[0], "text", None)):

               

                obj = event.content.parts[0].text
                
                if isinstance(obj, dict):
                    response_parts.append(obj)
                    return response_parts
                elif isinstance(obj, str):
                    new_obj = extract_json_from_string(obj)
                    if isinstance(new_obj, dict):
                        # amazonq-ignore-next-line
                        response_parts.append(new_obj)
                        return response_parts
                else:
                     output = format_output(stage, event)
                     response_parts.append(output)



            if(hasattr(event, 'error_code') and event.error_code and event.error_code == 'MALFORMED_FUNCTION_CALL'):
        
        
                output = format_output(stage, event)

                response_parts.append(output)

              

        
        if not response_parts:
            return [{'text_response': 'Something went wrong, Please Try again', 'tool_response':{}, 'should_update':False}]
        return response_parts
    
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        # raise HTTPException(status_code=500, detail=f"Error sending message: {e}")
        return [{'text_response': f'Something went wrong, Try again {e}', 'tool_response':{}, 'should_update':False}]
    


@router.post("/qa")
async def send_message(request: QARequest):
    try:
        req = request.dict()
        # print("Request received:", req)

        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION     )


        # Ensure session exists
        session_id = req.get("sessionId")
        session = None
        if session_id:
            session = await session_service.get_session(
                app_name=req["appName"],
                user_id=req["userId"],
                session_id=session_id
            )

        
        qa_app = _build_orchestrator_app(req["appName"], qa_root_agent)

        runner = Runner(app=qa_app, session_service=session_service)

        msg = types.Content(role="user", parts=[types.Part(text=req["newMessage"])])
        msg = await _guard_message_tokens(
            msg,
            session_id,
            "/qa",
            session=session,
            session_service=session_service,
            app_name=req["appName"],
            user_id=req["userId"],
        )

        response_parts = []

        async for event in runner.run_async(

            user_id=req["userId"],
            session_id=session_id,
            new_message=msg,
            run_config=_build_token_guard_run_config()
        ):
            logging.info("="*100)
            logging.info(f"Event: {event}")
            logging.info("="*100)

            # await session_service.append_event(session=session, event=event)

            response_parts.append(event)




        return response_parts
    
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending message: {e}")
    

 

def fetch_bigquery_table(table_id: str) -> list[dict]:
    client = get_bigquery_client()
    query = f"SELECT * FROM `{table_id}`"
    rows = client.query(query).result()
    return [dict(row) for row in rows]






@router.post("/messages/chat/human-in-the-loop")
async def human_in_the_loop(request: HumanInLoopRequest):
    try:
        logger.info(
            "[HITL][REQUEST] user=%s | session=%s | app_name=%s | agent_type=%s",
            request.user_id,
            request.session_id,
            request.app_name,
            request.agent_type,
        )

        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
        )

        # --------------------------------------------------
        # 🔑 NEW: Force session hydration + execution mode visibility
        # --------------------------------------------------
        session = await  session_service.get_session(
            user_id=request.user_id,
            session_id=request.session_id,
            app_name=request.app_name,
        )

        if session and session.state:
            logger.warning(
                "[HITL][SESSION STATE] session_id=%s | is_stream=%s | state_keys=%s",
                request.session_id,
                session.state.get("is_stream"),
                list(session.state.keys()),
            )
        else:
            logger.warning(
                "[HITL][SESSION STATE] session_id=%s | NO SESSION STATE FOUND",
                request.session_id,
            )

        # --------------------------------------------------

  

        AGENT_TYPE_TO_AGENT = {
            AgentType.DATA_DICTIONARY_UPDATE.value: data_dict_hitl_agent,
            AgentType.METADATA_FILL_UPDATE.value: metadata_fill_hitl_agent,
            AgentType.DATA_PROFILING.value: root_agent,
            AgentType.DATA_ANOMALY_ANALYSIS.value: root_agent,
        }


        # --------------------------------------------------
        # 🔀 Explicit agent routing (bypass orchestrator)
        # --------------------------------------------------

        selected_agent = AGENT_TYPE_TO_AGENT.get(request.agent_type)
        print(f"selected_agent for HITL CHAT EDIT IS {selected_agent}")

        if selected_agent:
            logger.warning(
                "[HITL][DIRECT_AGENT_ROUTE] agent_type=%s | agent=%s",
                request.agent_type,
                selected_agent,
            )
            root = selected_agent
        else:
            logger.warning(
                "[HITL][FALLBACK_ORCHESTRATOR] agent_type=%s",
                request.agent_type,
            )
            root = root_agent  # fallback safety




            

        hitl_app = _build_orchestrator_app(
            request.app_name,
            root,
        )
       
        # hitl_app = App(
        #     request.app_name,
        #     root,
        # )

        runner = Runner(
            app=hitl_app,
            session_service=session_service,
        )

        msg = types.Content(
            role="user",
            parts=[types.Part(text=request.user_message)],
        )
        msg = await _guard_message_tokens(
            msg,
            request.session_id,
            "/messages/chat/human-in-the-loop",
            session=session,
            session_service=session_service,
            app_name=request.app_name,
            user_id=request.user_id,
        )

        print(f"msg : {msg}")

        raw_response = ""
        event_count = 0
        async for event in runner.run_async(
            user_id=request.user_id,
            session_id=request.session_id,
            new_message=msg,
            run_config=_build_token_guard_run_config(),
        ):
            logger.debug("[HITL][EVENT][RAW] %r", event)

            with open("hitl_events.txt", "a", encoding="utf-8") as f:
                f.write(f"EVENT {event_count}: \n\n {event}\n")


            agent_name = getattr(event, "agent_name", None)

            if agent_name:
                logger.info(
                    "[HITL][AGENT] agent=%s | event_type=%s",
                    agent_name,
                    type(event).__name__,
                )

            # --------------------------------------------------
            # 🔴 HARD STOP FOR HITL (ADD THIS BLOCK)
            # --------------------------------------------------
            if agent_name == root.name:
                logger.warning(
                    "[HITL][STOP] Root HITL agent '%s' completed. Halting execution.",
                    root.name,
                )
                break
            # --------------------------------------------------

            if hasattr(event, "tool_name"):
                logger.warning(
                    "[HITL][TOOL CALL] agent=%s | tool=%s | args=%s",
                    agent_name,
                    event.tool_name,
                    getattr(event, "tool_args", None),
                )

            if hasattr(event, "tool_result"):
                logger.warning(
                    "[HITL][TOOL RESULT] agent=%s | tool=%s | result_preview=%s",
                    agent_name,
                    getattr(event, "tool_name", None),
                    str(event.tool_result)[:300],
                )

            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        raw_response += part.text

        # ---------------- JSON parsing (unchanged) ----------------
        import json

        try:
            # parsed = json.loads(raw_response)

            # if "data_dictionary_table_id" in parsed:
            #     table_id = parsed["data_dictionary_table_id"]
            #     dd_rows = fetch_data_dictionary_table(table_id)
            #     text_response = {"message": parsed["message"]}
            #     tool_response = dd_rows
            # else:
            #     text_response = parsed.get("text_response")
            #     tool_response = parsed.get("tool_response")

            parsed = json.loads(raw_response)

            print(f"parsed states:: {parsed.keys()}")

            # if "data_dictionary_table_id" in parsed:

            #     table_id = parsed["data_dictionary_table_id"]
            #     dd_rows = fetch_bigquery_table(table_id)
            #     text_response = parsed["message"]
            #     tool_response = dd_rows


            # elif "metadata_table_id" in parsed:

            #     print(f"parsed table::: {parsed}")
            #     tool_response ={}
            #     metadata_table_id = parsed["metadata_table_id"]
            #     filespecs_table_id = parsed.get("filespecs_table_id")
            #     metadata_table_rows = fetch_bigquery_table(metadata_table_id)
            #     filespecs_table_rows = fetch_bigquery_table(filespecs_table_id)
            #     text_response = parsed["message"]
                
            #     tool_response["file_specs_mapping"]= filespecs_table_rows
            #     tool_response["row_level_metadata"] = metadata_table_rows

            
            # else:
            #     text_response = parsed.get("text_response")
            #     tool_response = parsed.get("tool_response")


            if request.agent_type == AgentType.DATA_DICTIONARY_UPDATE.value:
                table_id = parsed.get("data_dictionary_table_id")
                text_response = parsed.get("message")
                raw_rows = fetch_bigquery_table(table_id) if table_id else None
                # Normalize Most Occurrences: strip JSON brackets/quotes if stored as serialized string
                if raw_rows:
                    for row in raw_rows:
                        for key in ("Most Occurrences", "most_occurrences"):
                            val = row.get(key)
                            if isinstance(val, str) and (val.startswith('[') or val.startswith('"')):
                                row[key] = val.strip('[]').replace('"', '').replace("'", '')
                tool_response = raw_rows

            elif request.agent_type == AgentType.METADATA_FILL_UPDATE.value:
                metadata_table_id = parsed.get("metadata_table_id")
                filespecs_table_id = parsed.get("filespecs_table_id")

                text_response = parsed.get("message")
                metadata_rows = fetch_bigquery_table(metadata_table_id) if metadata_table_id else []
                # Enrich metadata rows with Default_Value and Most_Occurrences from source tables
                if metadata_rows:
                    from api.routers.data import _enrich_metadata_rows, _write_enriched_metadata_to_bq
                    metadata_rows = _enrich_metadata_rows(metadata_rows)
                    if metadata_table_id:
                        _write_enriched_metadata_to_bq(metadata_table_id, metadata_rows)
                tool_response = {
                    "file_specs_mapping": fetch_bigquery_table(filespecs_table_id) if filespecs_table_id else [],
                    "row_level_metadata": metadata_rows,
                }

            else:
                # Relationship Analysis / Data Anomaly / Profiling
                text_response = parsed.get("text_response")
                tool_response = parsed.get("tool_response")





        except json.JSONDecodeError:
            logger.warning("[HITL][NON_JSON_RESPONSE]")
            text_response = raw_response
            tool_response = None

        logger.info(
            "[HITL][COMPLETED] session=%s | response_len=%d",
            request.session_id,
            len(raw_response),
        )

        return {
            "session_id": request.session_id,
            "text_response": text_response,
            "tool_response": tool_response,
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        logger.exception(
            "[HITL][ERROR] user=%s | session=%s",
            request.user_id,
            request.session_id,
        )
        raise HTTPException(status_code=500, detail=str(e))




logger = logging.getLogger(__name__)


def build_profiling_text_response(
    *,
    target: str,
    profiling_output: dict,
) -> str:
    """
    Convert an existing profiling tool response into a human-readable
    markdown text response using the SAME prompt logic as streaming.

    Single LLM call. No streaming. HITL-safe.
    """

    # --------------------------------------------------
    # STEP 1: Validate inputs
    # --------------------------------------------------
    logger.info("[BUILD_PROFILING_TEXT] START")
    logger.warning(
        "[BUILD_PROFILING_TEXT][INPUT] target=%s | tool_response_type=%s | len=%d",
        target,
        type(profiling_output).__name__,
        len(profiling_output) if isinstance(profiling_output, dict) else -1,
    )

    if not profiling_output or not isinstance(profiling_output, dict):
        logger.error("[BUILD_PROFILING_TEXT] Invalid or empty profiling_output")
        return "No profiling data available to generate analysis."

    # --------------------------------------------------
    # STEP 2: Build analysis prompt (REUSED FROM STREAMING)
    # --------------------------------------------------
    if target == "data_anomaly_analysis_tool_response":
        logger.info("[BUILD_PROFILING_TEXT] Using anomaly analysis prompt builder")
        from utils.anomaly_analysis import build_anomaly_analysis_prompt

        analysis_prompt = build_anomaly_analysis_prompt(profiling_output)

    elif target == "relationship_analysis_tool_response":
        logger.info("[BUILD_PROFILING_TEXT] Using relationship analysis prompt builder")
        from utils.relationship_analysis import build_relationship_analysis_prompt

        analysis_prompt = build_relationship_analysis_prompt(profiling_output)

    else:
        logger.error("[BUILD_PROFILING_TEXT] Unsupported target: %s", target)
        raise ValueError(f"Unsupported profiling target: {target}")

    logger.warning(
        "[BUILD_PROFILING_TEXT][PROMPT] len=%d | preview=%s",
        len(analysis_prompt),
        analysis_prompt[:300],
    )

    # --------------------------------------------------
    # STEP 3: Initialize Gemini client
    # --------------------------------------------------
    client = genai.Client(
        vertexai=True,
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )

    model = config.AGENT_MODEL

    logger.info(
        "[BUILD_PROFILING_TEXT] Calling LLM | model=%s | temperature=%s",
        model,
        0.25,
    )

    # --------------------------------------------------
    # STEP 4: Single non-streaming LLM call
    # --------------------------------------------------
    try:
        response = client.models.generate_content(
            model=model,
            contents=analysis_prompt,
            config=types.GenerateContentConfig(
                temperature=0.25,
            ),
        )
    except Exception as e:
        logger.exception("[BUILD_PROFILING_TEXT] LLM call failed")
        return f"Failed to generate profiling analysis: {str(e)}"

    # --------------------------------------------------
    # STEP 5: Extract text safely
    # --------------------------------------------------
    final_text = ""

    if hasattr(response, "text") and response.text:
        final_text = response.text
    elif hasattr(response, "candidates"):
        for candidate in response.candidates:
            for part in getattr(candidate.content, "parts", []):
                if hasattr(part, "text") and part.text:
                    final_text += part.text

    final_text = final_text.strip()

    logger.warning(
        "[BUILD_PROFILING_TEXT][OUTPUT] len=%d | preview=%s",
        len(final_text),
        final_text[:300],
    )

    # --------------------------------------------------
    # STEP 6: Return final markdown text
    # --------------------------------------------------
    if not final_text:
        logger.error("[BUILD_PROFILING_TEXT] Empty text returned from LLM")
        return "Profiling analysis completed, but no insights could be generated."

    logger.info("[BUILD_PROFILING_TEXT] SUCCESS")
    return final_text




logger = logging.getLogger(__name__)


@router.post("/messages/chat/human-in-the-loop/profiling")
async def profiling_human_in_the_loop(request: HumanInLoopLargeRequest):
    try:
        # ==================================================
        # REQUEST LOGGING
        # ==================================================
        logger.info(
            "[PROFILING_HITL][REQUEST] user=%s | session=%s | app=%s",
            request.user_id,
            request.session_id,
            request.app_name,
        )

        logger.debug(
            "[PROFILING_HITL][REQUEST_MESSAGE_RAW] len=%d | preview=%r",
            len(request.user_message),
            request.user_message[:300],
        )

        # ==================================================
        # LOAD SESSION
        # ==================================================
        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
        )

        session = await session_service.get_session(
            user_id=request.user_id,
            session_id=request.session_id,
            app_name=request.app_name,
        )

        if not session:
            logger.error("[PROFILING_HITL][NO_SESSION]")
            raise HTTPException(
                400,
                "Session not found. Check session_id, app_name, and user_id match the profiling run.",
            )

        session_state = session.state if session.state is not None else {}
        if not session_state:
            logger.error("[PROFILING_HITL][NO_SESSION_STATE]")
            raise HTTPException(400, "Session state not found")

        logger.warning(
            "[PROFILING_HITL][STATE_KEYS] count=%d | keys=%s",
            len(session_state.keys()),
            list(session_state.keys()),
        )

        # ==================================================
        # PARSE TARGET + USER INSTRUCTION
        # ==================================================
        user_msg_raw = request.user_message.strip()

        logger.debug(
            "[PROFILING_HITL][USER_MSG_STRIPPED] len=%d | preview=%r",
            len(user_msg_raw),
            user_msg_raw[:300],
        )

        # if user_msg_raw.startswith("DATA ANOMALY ANALYSIS"):
        #     target = "data_anomaly_analysis_tool_response"
        #     user_instruction = user_msg_raw.replace(
        #         "FOR DATA ANOMALY ANALYSIS", "", 1
        #     ).strip()

        # elif user_msg_raw.startswith("RELATIONSHIP ANALYSIS"):
        #     target = "relationship_analysis_tool_response"
        #     user_instruction = user_msg_raw.replace(
        #         "FOR RELATIONSHIP ANALYSIS", "", 1
        #     ).strip()



        user_msg_lower = user_msg_raw.lower()

        if "relationship analysis" in user_msg_lower:
            target = "relationship_analysis_tool_response"
            user_instruction = user_msg_raw
        elif "data anomaly" in user_msg_lower:
            target = "data_anomaly_analysis_tool_response"
            user_instruction = user_msg_raw


        else:
            logger.error(
                "[PROFILING_HITL][INVALID_PREFIX] preview=%r",
                user_msg_raw[:300],
            )
            raise HTTPException(
                400,
                "Message must start with "
                "'FOR DATA ANOMALY ANALYSIS' or 'FOR RELATIONSHIP ANALYSIS'",
            )

        logger.info("[PROFILING_HITL][TARGET_SELECTED] %s", target)

        logger.debug(
            "[PROFILING_HITL][USER_INSTRUCTION] len=%d | preview=%r",
            len(user_instruction),
            user_instruction[:300],
        )

        # ==================================================
        # VALIDATE TARGET EXISTS
        # ==================================================
        if target not in session.state:
            logger.error(
                "[PROFILING_HITL][MISSING_TARGET] %s not found in state",
                target,
            )
            raise HTTPException(400, f"{target} not present in session state")

        tool_response = session.state[target]

        logger.warning(
            "[PROFILING_HITL][TOOL_RESPONSE_BEFORE] "
            "type=%s | len=%d | preview=%r",
            type(tool_response).__name__,
            len(tool_response) if isinstance(tool_response, dict) else -1,
            str(tool_response)[:300],
        )

        # ==================================================
        # RUN PROFILING HITL AGENT (INTENT DECISION)
        # ==================================================
        hitl_app = _build_orchestrator_app(
            request.app_name,
            profiling_hitl_agent,
        )

        runner = Runner(
            app=hitl_app,
            session_service=session_service,
        )

        msg = types.Content(
            role="user",
            parts=[types.Part(text=user_instruction)],
        )
        msg = await _guard_message_tokens(
            msg,
            request.session_id,
            "/messages/chat/human-in-the-loop/profiling",
            session=session,
            session_service=session_service,
            app_name=request.app_name,
            user_id=request.user_id,
        )

        raw_message = ""

        async for event in runner.run_async(
            user_id=request.user_id,
            session_id=request.session_id,
            new_message=msg,
            run_config=_build_token_guard_run_config(),
        ):
            logger.debug(
                "[PROFILING_HITL][AGENT_EVENT] type=%s",
                type(event).__name__,
            )

            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        raw_message += part.text

            if getattr(event, "agent_name", None) == profiling_hitl_agent.name:
                logger.warning(
                    "[PROFILING_HITL][STOP] Root agent '%s' completed. Halting.",
                    profiling_hitl_agent.name,
                )
                break

        raw_message = raw_message.strip()

        logger.warning(
            "[PROFILING_HITL][AGENT_RAW_OUTPUT] len=%d | preview=%r",
            len(raw_message),
            raw_message[:300],
        )

        # ----------------------------------------------
        # Parse agent JSON output
        # ----------------------------------------------
        agent_message = None

        try:
            parsed = json.loads(raw_message)
            agent_message = parsed.get("message", "").strip()
            logger.warning(
                "[PROFILING_HITL][AGENT_PARSED_MESSAGE] value=%r",
                agent_message,
            )
        except json.JSONDecodeError:
            # Fallback: treat raw text as message
            agent_message = raw_message.strip()
            logger.warning(
                "[PROFILING_HITL][AGENT_MESSAGE_FALLBACK] value=%r",
                agent_message,
            )













        # logger.warning(
        #     "[PROFILING_HITL][AGENT_MESSAGE] len=%d | preview=%r",
        #     len(raw_message),
        #     raw_message[:300],
        # )

        # ==================================================
        # INTENT DECISION (EXPLICIT TOKEN)
        # ==================================================
        if agent_message == "UPDATE":
            logger.info("[PROFILING_HITL][MODE] UPDATE")

            logger.debug(
                "[PROFILING_HITL][UPDATE_INPUT] "
                "user_instruction_len=%d | preview=%r",
                len(user_instruction),
                user_instruction[:300],
            )

            logger.debug(
                "[PROFILING_HITL][UPDATE_INPUT] "
                "tool_response_len=%d | preview=%r",
                len(tool_response) if isinstance(tool_response, dict) else -1,
                str(tool_response)[:300],
            )

            # ----------------------------------------------
            # APPLY MODIFICATION
            # ----------------------------------------------
            modified_tool_response = apply_profiling_hitl_modification(
                user_query=user_instruction,
                tool_response=tool_response,
            )

            logger.warning(
                "[PROFILING_HITL][TOOL_RESPONSE_AFTER] "
                "type=%s | len=%d | preview=%r",
                type(modified_tool_response).__name__,
                len(modified_tool_response)
                if isinstance(modified_tool_response, dict)
                else -1,
                str(modified_tool_response)[:300],
            )

            update_event = Event(
                author="system",
                invocation_id=f"profiling-hitl-{uuid.uuid4()}",
                actions=EventActions(state_delta={target: modified_tool_response}),
            )
            await session_service.append_event(session=session, event=update_event)

            logger.warning(
                "[PROFILING_HITL][STATE_UPDATED] key=%s | new_len=%d",
                target,
                len(modified_tool_response)
                if isinstance(modified_tool_response, dict)
                else -1,
            )

            # ----------------------------------------------
            # BUILD TEXT RESPONSE FROM MODIFIED OUTPUT
            # ----------------------------------------------
            text_response = build_profiling_text_response(
                target=target,
                profiling_output=modified_tool_response,
            )

            logger.debug(
                "[PROFILING_HITL][TEXT_RESPONSE_AFTER_UPDATE] "
                "len=%d | preview=%r",
                len(text_response),
                text_response[:300],
            )

            return {
                "session_id": request.session_id,
                "mode": "UPDATE",
                "message": "Profiling output updated successfully",
                "text_response": text_response,
                "tool_response": modified_tool_response
            }

        # ==================================================
        # QUESTION MODE (ANYTHING OTHER THAN 'UPDATE')
        # ==================================================
        logger.info("[PROFILING_HITL][MODE] QUESTION")

        logger.debug(
            "[PROFILING_HITL][QUESTION_RESPONSE] "
            "len=%d | preview=%r",
            len(raw_message),
            raw_message[:300],
        )

        return {
            "session_id": request.session_id,
            "mode": "QUESTION",
            "text_response": agent_message,
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        logger.exception(
            "[PROFILING_HITL][ERROR] session=%s",
            request.session_id,
        )
        raise HTTPException(status_code=500, detail=str(e))



_PROFILING_CHAT_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_PROFILING_CHAT_STOP_TERMS = {
    "add",
    "change",
    "column",
    "correct",
    "data",
    "field",
    "fix",
    "from",
    "modify",
    "profiling",
    "report",
    "response",
    "set",
    "table",
    "the",
    "this",
    "type",
    "update",
    "value",
    "with",
}


def _profiling_chat_json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str))
    except Exception:
        return len(str(value))


def _profiling_chat_search_text(value: Any, max_chars: int = 1200) -> str:
    try:
        return json.dumps(value, default=str)[:max_chars]
    except Exception:
        return str(value)[:max_chars]


def _profiling_chat_terms(user_message: str) -> set[str]:
    return {
        token.lower()
        for token in _PROFILING_CHAT_TOKEN_RE.findall(user_message or "")
        if len(token) >= 3 and token.lower() not in _PROFILING_CHAT_STOP_TERMS
    }


_PROFILING_CHAT_TEXT_RESPONSE_MARKERS = (
    "text_response",
    "text response",
    "text summary",
    "markdown",
    "overview",
    "narrative",
    "prose",
    "wording",
    "write-up",
    "write up",
    "paragraph",
    "section",
)
_PROFILING_CHAT_MAX_SELECTED_CHUNKS = 12
_PROFILING_CHAT_MAX_CHUNK_BYTES = 24000


def _profiling_chat_targets_text_response(user_message: str) -> bool:
    """True when the user is asking to change the markdown text_response field."""
    lowered = (user_message or "").lower()
    if any(marker in lowered for marker in _PROFILING_CHAT_TEXT_RESPONSE_MARKERS):
        return True
    if "summary" in lowered and not any(
        token in lowered
        for token in ("table_summary", "table summary", "row count", "total_rows")
    ):
        return True
    if any(token in lowered for token in ("describe", "mention", "explain")) and any(
        token in lowered for token in ("report", "summary", "text", "markdown")
    ):
        return True
    return False


def _profiling_chat_ensure_text_response_chunk(
    chunks: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    text_chunk = next((c for c in chunks if c.get("kind") == "text_response"), None)
    if text_chunk and text_chunk not in selected:
        return [text_chunk, *selected]
    return selected


def _profiling_chat_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return text

    raw_text = ""
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                raw_text += part_text
    return raw_text


def _profiling_chat_set_path(target: Any, path: list[Any], value: Any) -> None:
    cursor = target
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value


def _profiling_chat_path_label(path: list[Any]) -> str:
    label = str(path[0])
    for key in path[1:]:
        if isinstance(key, int):
            label += f"[{key}]"
        else:
            label += f".{key}"
    return label


def _profiling_chat_table_results(tool_response: dict[str, Any]) -> tuple[str, list[Any]]:
    for key in ("result", "all_tables"):
        value = tool_response.get(key)
        if isinstance(value, list):
            return key, value
    return "", []


def _profiling_chat_generic_kind(path: list[Any]) -> str:
    for key in reversed(path):
        if not isinstance(key, int):
            return str(key)
    return str(path[-1]) if path else "root"


def _profiling_chat_generic_search(path: list[Any], value: Any) -> str:
    label = _profiling_chat_path_label(path)
    key_hint = ""
    if isinstance(value, dict):
        key_hint = " ".join(str(key) for key in value.keys())
    elif isinstance(value, list):
        nested_keys: list[str] = []
        for item in value[:5]:
            if isinstance(item, dict):
                nested_keys.extend(str(key) for key in item.keys())
        key_hint = " ".join(nested_keys)
    return f"{label} {key_hint} {_profiling_chat_search_text(value)}"


def _profiling_chat_add_generic_chunks(
    *,
    chunks: list[dict[str, Any]],
    value: Any,
    path: list[Any],
    depth: int = 0,
) -> None:
    if not path:
        pass
    elif _profiling_chat_json_size(value) <= _PROFILING_CHAT_MAX_CHUNK_BYTES:
        chunks.append({
            "path": path,
            "label": _profiling_chat_path_label(path),
            "value": value,
            "kind": _profiling_chat_generic_kind(path),
            "search": _profiling_chat_generic_search(path, value),
        })

    if depth >= 10:
        return

    if isinstance(value, dict):
        for key, child in value.items():
            _profiling_chat_add_generic_chunks(
                chunks=chunks,
                value=child,
                path=path + [key],
                depth=depth + 1,
            )
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            _profiling_chat_add_generic_chunks(
                chunks=chunks,
                value=child,
                path=path + [idx],
                depth=depth + 1,
            )


def _profiling_chat_dedupe_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for chunk in chunks:
        marker = tuple(chunk.get("path") or [])
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(chunk)
    return deduped


def _profiling_chat_prune_parent_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_paths = [tuple(chunk.get("path") or []) for chunk in chunks]
    pruned: list[dict[str, Any]] = []
    for chunk, path in zip(chunks, selected_paths):
        if chunk.get("kind") == "text_response":
            pruned.append(chunk)
            continue
        has_selected_child = any(
            other != path and len(other) > len(path) and other[: len(path)] == path
            for other in selected_paths
        )
        if not has_selected_child:
            pruned.append(chunk)
    return pruned


def _profiling_chat_column_chunks(
    *,
    result: dict[str, Any],
    base_path: list[Any],
    table_reference: str,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    column_analysis = result.get("column_analysis")

    if isinstance(column_analysis, dict):
        for column_name, column_payload in column_analysis.items():
            path = base_path + ["column_analysis", column_name]
            chunks.append({
                "path": path,
                "label": _profiling_chat_path_label(path),
                "value": column_payload,
                "kind": "column_analysis",
                "search": (
                    f"{table_reference} column_analysis {column_name} "
                    f"{_profiling_chat_search_text(column_payload)}"
                ),
            })
        return chunks

    if isinstance(column_analysis, list):
        for col_idx, column_item in enumerate(column_analysis):
            if not isinstance(column_item, dict):
                continue

            if len(column_item) == 1:
                column_name, column_payload = next(iter(column_item.items()))
                path = base_path + ["column_analysis", col_idx, column_name]
            else:
                column_name = (
                    column_item.get("column_name")
                    or column_item.get("field_name")
                    or column_item.get("name")
                    or f"column_{col_idx}"
                )
                column_payload = column_item
                path = base_path + ["column_analysis", col_idx]

            chunks.append({
                "path": path,
                "label": _profiling_chat_path_label(path),
                "value": column_payload,
                "kind": "column_analysis",
                "search": (
                    f"{table_reference} column_analysis {column_name} "
                    f"{_profiling_chat_search_text(column_payload)}"
                ),
            })

    return chunks


def _profiling_chat_sync_table_result_aliases(profiling_response: dict[str, Any]) -> None:
    tool_response = profiling_response.get("tool_response")
    if not isinstance(tool_response, dict):
        return
    result = tool_response.get("result")
    all_tables = tool_response.get("all_tables")
    if isinstance(result, list) and isinstance(all_tables, list):
        tool_response["all_tables"] = json.loads(json.dumps(result, default=str))


def _profiling_chat_build_chunks(profiling_response: dict[str, Any]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []

    text_value = profiling_response.get("text_response")
    if isinstance(text_value, str):
        chunks.append({
            "path": ["text_response"],
            "label": "text_response",
            "value": text_value,
            "kind": "text_response",
            "search": "text_response summary markdown recommendations data type",
        })

    tool_response = profiling_response.get("tool_response")
    if not isinstance(tool_response, dict):
        _profiling_chat_add_generic_chunks(
            chunks=chunks,
            value=profiling_response,
            path=[],
        )
        return _profiling_chat_dedupe_chunks(chunks)

    _profiling_chat_add_generic_chunks(
        chunks=chunks,
        value=tool_response,
        path=["tool_response"],
    )

    result_key, results = _profiling_chat_table_results(tool_response)
    if not result_key:
        return _profiling_chat_dedupe_chunks(chunks)

    for idx, result in enumerate(results):
        if not isinstance(result, dict):
            continue

        table_reference = str(result.get("table_reference", ""))
        base_path = ["tool_response", result_key, idx]

        for key in (
            "table_reference",
            "data_quality_score",
            "recommendations",
            "default_value_analysis",
            "enhanced_analysis",
            "table_summary",
        ):
            if key in result:
                path = base_path + [key]
                chunks.append({
                    "path": path,
                    "label": _profiling_chat_path_label(path),
                    "value": result.get(key),
                    "kind": key,
                    "search": f"{table_reference} {key} {_profiling_chat_search_text(result.get(key), 800)}",
                })

        chunks.extend(
            _profiling_chat_column_chunks(
                result=result,
                base_path=base_path,
                table_reference=table_reference,
            )
        )

    return _profiling_chat_dedupe_chunks(chunks)


def _profiling_chat_chunk_score(chunk: dict[str, Any], terms: set[str], user_message: str) -> int:
    message = (user_message or "").lower()
    kind = chunk.get("kind", "")
    label = str(chunk.get("label", "")).lower()
    search = str(chunk.get("search", "")).lower()
    score = 0

    if kind == "text_response":
        if _profiling_chat_targets_text_response(user_message):
            score += 12
        elif any(
            marker in message
            for marker in (
                "text",
                "summary",
                "markdown",
                "describe",
                "mention",
                "recommend",
                "data type",
                "overview",
                "report",
                "description",
                "narrative",
                "prose",
            )
        ):
            score += 4

    if kind == "column_analysis":
        column_name = str(chunk.get("path", [""])[-1]).lower()
        if column_name and column_name in message:
            score += 10

    if kind and kind.lower() in message:
        score += 8

    keyword_to_kind = {
        "recommend": "recommendations",
        "recommendation": "recommendations",
        "score": "data_quality_score",
        "quality": "data_quality_score",
        "default": "default_value_analysis",
        "enhanced": "enhanced_analysis",
        "composite": "enhanced_analysis",
        "summary": "table_summary",
        "row": "table_summary",
    }
    for token, target_kind in keyword_to_kind.items():
        if token in message and kind == target_kind:
            score += 3

    searchable_tokens = {
        token.lower()
        for token in _PROFILING_CHAT_TOKEN_RE.findall(f"{label} {search}")
    }
    for term in terms:
        if term in searchable_tokens:
            score += 1
        if term in label:
            score += 3

    return score


def _profiling_chat_select_chunks(
    profiling_response: dict[str, Any],
    user_message: str,
) -> list[dict[str, Any]]:
    chunks = _profiling_chat_build_chunks(profiling_response)
    terms = _profiling_chat_terms(user_message)

    scored = [
        (chunk, _profiling_chat_chunk_score(chunk, terms, user_message))
        for chunk in chunks
    ]
    selected = [
        chunk
        for chunk, score in sorted(
            scored,
            key=lambda item: (
                item[1],
                -_profiling_chat_json_size(item[0].get("value")),
            ),
            reverse=True,
        )
        if score > 1
    ][:_PROFILING_CHAT_MAX_SELECTED_CHUNKS]

    lowered = (user_message or "").lower()
    # Explicit or implied edits to markdown prose must include text_response.
    if _profiling_chat_targets_text_response(user_message):
        selected = _profiling_chat_ensure_text_response_chunk(chunks, selected)

    # Data type / column edits must keep markdown and structured stats aligned.
    if (
        "data type" in lowered
        or "datatype" in lowered
        or any(c.get("kind") == "column_analysis" for c in selected)
    ):
        selected = _profiling_chat_ensure_text_response_chunk(chunks, selected)

    if not selected:
        # Ambiguous edits are still handled without sending the full payload:
        # try the prose plus compact top-level chunks.
        selected = [
            chunk for chunk in chunks
            if len(chunk.get("path") or []) <= 3
        ][:_PROFILING_CHAT_MAX_SELECTED_CHUNKS]

    # Every UPDATE should attempt text_response when markdown is present.
    selected = _profiling_chat_ensure_text_response_chunk(chunks, selected)
    selected = _profiling_chat_prune_parent_chunks(selected)

    selected.sort(
        key=lambda chunk: (
            0 if chunk.get("kind") == "text_response" else 1,
            _profiling_chat_json_size(chunk.get("value")),
        )
    )
    return selected


def _profiling_chat_edit_text_response(
    *,
    client: genai.Client,
    user_message: str,
    current_text: str,
) -> tuple[bool, str]:
    """Edit the top-level markdown text_response string (not a JSON object chunk)."""
    prompt = f"""You are a STRICT markdown editor for a data profiling report summary.

You are given:
1) USER INSTRUCTION — what to change in the report text
2) CURRENT MARKDOWN TEXT — the full text_response field

Apply ONLY the user-requested change to the markdown.
Preserve all other sections, headings, tables, and wording unless the user asked to change them.
Do NOT invent statistics or column values not implied by the instruction.

If the instruction does not require any change to this markdown, return changed=false and repeat the original text exactly.

Return valid JSON only:
{{
  "changed": true or false,
  "value": "<full markdown string after edit, or unchanged original>"
}}

USER INSTRUCTION:
{user_message}

CURRENT MARKDOWN TEXT:
{current_text}
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
    except (TypeError, AttributeError) as mime_err:
        logger.warning(
            "[PROFILING_CHAT_HITL][TEXT_EDIT] response_mime_type not supported, fallback: %s",
            mime_err,
        )
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.1),
        )

    raw_text = _profiling_chat_response_text(response)
    parsed = json.loads(raw_text or "{}")
    if not isinstance(parsed, dict) or "value" not in parsed:
        raise ValueError("text_response editor returned invalid payload")

    edited = parsed.get("value")
    if edited is None:
        edited = current_text
    if not isinstance(edited, str):
        edited = str(edited)

    changed = edited != current_text or bool(parsed.get("changed"))
    if changed and edited == current_text:
        changed = False
    return changed, edited


def _profiling_chat_edit_chunk(
    *,
    client: genai.Client,
    user_message: str,
    chunk: dict[str, Any],
) -> tuple[bool, Any]:
    if chunk.get("kind") == "text_response":
        current = chunk.get("value", "")
        if not isinstance(current, str):
            current = str(current or "")
        return _profiling_chat_edit_text_response(
            client=client,
            user_message=user_message,
            current_text=current,
        )

    prompt = f"""You are a STRICT JSON editor for one chunk of a data profiling response.

You are given:
1) USER INSTRUCTION
2) JSON PATH
3) CURRENT CHUNK VALUE

Only edit this chunk if the requested change belongs at this JSON path.
If this chunk is unrelated, return changed=false and the original value.

STRICT RULES:
- Apply ONLY the explicit user-requested change
- Preserve the chunk's type and existing structure
- Do NOT add keys unless the user explicitly asks to add a field and this JSON path is the smallest relevant object that should contain it
- Do NOT remove keys
- Do NOT reorder arrays
- Do NOT change numeric values unless explicitly asked
- Do NOT invent data

DATA TYPE CHANGE RULE:
- For a column data type edit, update only the matching data_type field wherever that column exists in this chunk

Return valid JSON only with this exact wrapper:
{{
  "changed": true or false,
  "value": <full chunk value after applying the edit, or original chunk value>
}}

USER INSTRUCTION:
{user_message}

JSON PATH:
{chunk["label"]}

CURRENT CHUNK VALUE:
{json.dumps(chunk.get("value"), indent=2, default=str)}
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
    except (TypeError, AttributeError) as mime_err:
        logger.warning(
            "[PROFILING_CHAT_HITL][CHUNK_EDIT] response_mime_type not supported, fallback: %s",
            mime_err,
        )
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.1),
        )

    raw_text = _profiling_chat_response_text(response)
    parsed = json.loads(raw_text or "{}")
    if not isinstance(parsed, dict) or "value" not in parsed:
        raise ValueError(f"Chunk editor returned invalid payload for {chunk['label']}")

    return bool(parsed.get("changed")), parsed.get("value")


def _profiling_chat_apply_chunked_modification(
    *,
    client: genai.Client,
    user_message: str,
    profiling_response: dict[str, Any],
    skip_text_response_edit: bool = False,
) -> dict[str, Any]:
    if not isinstance(profiling_response, dict):
        raise ValueError("Profiling response must be a JSON object")

    modified_response = normalize_profiling_chat_response(profiling_response)
    modified_response = json.loads(json.dumps(modified_response, default=str))

    if not skip_text_response_edit:
        current_text = modified_response.get("text_response") or ""
        if isinstance(current_text, str) and should_run_text_response_edit(user_message, current_text):
            edited_text, text_changed = apply_dataset_overview_text_response_edit(
                user_query=user_message,
                text_response=current_text,
                client=client,
            )
            if text_changed:
                modified_response["text_response"] = edited_text
                logger.info("[PROFILING_CHAT_HITL] text_response updated via dataset overview editor")

    selected_chunks = _profiling_chat_select_chunks(modified_response, user_message)
    logger.info(
        "[PROFILING_CHAT_HITL][CHUNK_EDIT] selected_chunks=%d total_size=%d paths=%s",
        len(selected_chunks),
        _profiling_chat_json_size(modified_response),
        [chunk["label"] for chunk in selected_chunks[:25]],
    )

    changed_paths: list[str] = []
    for chunk in selected_chunks:
        # text_response is edited once up-front via apply_dataset_overview_text_response_edit
        if chunk.get("kind") == "text_response":
            continue
        changed, edited_value = _profiling_chat_edit_chunk(
            client=client,
            user_message=user_message,
            chunk=chunk,
        )
        if changed:
            _profiling_chat_set_path(modified_response, chunk["path"], edited_value)
            changed_paths.append(chunk["label"])

    logger.info(
        "[PROFILING_CHAT_HITL][CHUNK_EDIT] changed_paths=%s",
        changed_paths,
    )
    _profiling_chat_sync_table_result_aliases(modified_response)
    return modified_response


@router.get("/profiling-dashboard-data")
async def get_profiling_dashboard_data(
    sessionId: str = Query(..., description="Vertex ADK session id"),
    userId: str = Query(..., description="Vertex ADK user id"),
    appName: str = Query(..., description="Vertex ADK app name"),
):
    """Return canonical dataset-overview profiling payload for the Detailed Profiling UI."""
    try:
        payload = await _resolve_profiling_dashboard_payload(
            session_id=sessionId,
            user_id=userId,
            app_name=appName,
        )
        tool_response = payload.get("tool_response") if isinstance(payload, dict) else {}
        all_tables = (
            tool_response.get("all_tables")
            if isinstance(tool_response, dict)
            else None
        )
        table_count = len(all_tables) if isinstance(all_tables, list) else 0
        return {
            "status": "success",
            "table_count": table_count,
            "tool_response": payload,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[PROFILING_DASHBOARD] Failed to resolve payload for session=%s", sessionId)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/messages/chat/human-in-the-loop/profiling-chat")
async def profiling_chat_human_in_the_loop(request: ProfilingChatHITLRequest):
    """
    Chat HITL endpoint for the Data Profiling response from /send.

    Reads canonical Data Profiling output from GCS (fallback: legacy session key).
    - QUESTION -> returns natural language answer from the profiling data
    - UPDATE   -> applies the user modification, persists back to session state,
                  returns the full modified payload
    """
    try:
        logger.info(
            "[PROFILING_CHAT_HITL][REQUEST] user=%s | session=%s | app=%s",
            request.user_id, request.session_id, request.app_name,
        )

        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
        )

        # --------------------------------------------------
        # Hydrate Vertex AI session (mirrors human_in_the_loop)
        # --------------------------------------------------
        session = await session_service.get_session(
            user_id=request.user_id,
            session_id=request.session_id,
            app_name=request.app_name,
        )

        if not session:
            raise HTTPException(
                400,
                "Session not found. Check session_id, app_name, and user_id match the profiling run.",
            )

        session_state = session.state if session.state is not None else {}

        if session_state:
            logger.warning(
                "[PROFILING_CHAT_HITL][SESSION STATE] session_id=%s | is_stream=%s | state_keys=%s",
                request.session_id,
                session_state.get("is_stream"),
                list(session_state.keys()),
            )
        else:
            logger.warning(
                "[PROFILING_CHAT_HITL][SESSION STATE] session_id=%s | empty state (will try GCS)",
                request.session_id,
            )

        raw_profiling = _resolve_profiling_chat_response(
            request.session_id,
            session_state,
        )
        if not raw_profiling:
            profiling_full_results = session_state.get("profiling_full_results")
            if profiling_full_results:
                raw_profiling = {
                    "text_response": "",
                    "tool_response": {"all_tables": profiling_full_results},
                    "should_update": False,
                }

        if not raw_profiling:
            raw_profiling = _resolve_profiling_from_app_resume(
                request.session_id,
                request.user_id,
            )

        if not raw_profiling:
            raise HTTPException(
                400,
                "No Data Profiling response found. Upload files and complete the Dataset Overview step on the profiling page first.",
            )

        profiling_response = normalize_profiling_chat_response(raw_profiling)

        try:
            persist_profiling_chat_response(request.session_id, profiling_response)
        except Exception:
            logger.exception(
                "[PROFILING_CHAT_HITL] Failed to backfill profiling chat artifact for session=%s",
                request.session_id,
            )

        await _inject_profiling_chat_canonical_state(
            session_service=session_service,
            session=session,
            session_id=request.session_id,
            canonical_response=profiling_response,
        )

        logger.warning(
            "[PROFILING_CHAT_HITL][TOOL_RESPONSE_BEFORE] type=%s | len=%d | preview=%r",
            type(profiling_response).__name__,
            len(profiling_response) if isinstance(profiling_response, dict) else -1,
            str(profiling_response)[:300],
        )

        # --------------------------------------------------
        # is_edit=True: skip agent classification, go straight to UPDATE
        # --------------------------------------------------
        if request.is_edit:
            logger.info("[PROFILING_CHAT_HITL][MODE] UPDATE (is_edit=True, skipping agent)")
        else:
            # --------------------------------------------------
            # Build app with EventsCompactionConfig + ContextCacheConfig
            # via _build_orchestrator_app so Vertex session memory is active
            # --------------------------------------------------
            hitl_app = _build_orchestrator_app(request.app_name, dataset_overview_hitl_agent)
            runner = Runner(app=hitl_app, session_service=session_service)

            msg = types.Content(
                role="user",
                parts=[types.Part(text=request.user_message)],
            )
            msg = await _guard_message_tokens(
                msg,
                request.session_id,
                "/messages/chat/human-in-the-loop/profiling-chat",
                session=session,
                session_service=session_service,
                app_name=request.app_name,
                user_id=request.user_id,
            )

            logger.info("[PROFILING_CHAT_HITL] msg=%s", msg)

            raw_message = ""
            event_count = 0

            async for event in runner.run_async(
                user_id=request.user_id,
                session_id=request.session_id,
                new_message=msg,
                run_config=_build_token_guard_run_config(),
            ):
                event_count += 1
                agent_name = getattr(event, "agent_name", None)

                if agent_name:
                    logger.info(
                        "[PROFILING_CHAT_HITL][AGENT] agent=%s | event_type=%s",
                        agent_name, type(event).__name__,
                    )

                # Hard-stop once root HITL agent completes (mirrors human_in_the_loop)
                if agent_name == dataset_overview_hitl_agent.name:
                    logger.warning(
                        "[PROFILING_CHAT_HITL][STOP] Root agent '%s' completed. Halting.",
                        dataset_overview_hitl_agent.name,
                    )
                    if hasattr(event, "content") and event.content:
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                raw_message += part.text
                    break

                elif hasattr(event, "content") and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            raw_message += part.text

            raw_message = raw_message.strip()
            logger.warning(
                "[PROFILING_CHAT_HITL][AGENT_RAW_OUTPUT] len=%d | preview=%r",
                len(raw_message), raw_message[:300],
            )

            # Parse agent JSON output (output_schema guarantees {"message": "..."})
            agent_message = raw_message
            try:
                parsed = json.loads(raw_message)
                agent_message = parsed.get("message", raw_message).strip()
            except json.JSONDecodeError:
                agent_message = raw_message.strip()

            logger.warning(
                "[PROFILING_CHAT_HITL][AGENT_MESSAGE] value=%r", agent_message
            )

            # --------------------------------------------------
            # QUESTION path — return answer directly
            # --------------------------------------------------
            if agent_message != "UPDATE":
                logger.info("[PROFILING_CHAT_HITL][MODE] QUESTION")
                return {
                    "session_id": request.session_id,
                    "mode": "QUESTION",
                    "text_response": agent_message,
                    "tool_response": None,
                }

        # --------------------------------------------------
        # UPDATE path — apply modification via strict LLM JSON editor
        # mirrors apply_profiling_hitl_modification pattern
        # --------------------------------------------------
        logger.info("[PROFILING_CHAT_HITL][MODE] UPDATE")

        client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
        )

        try:
            logger.info(
                "[PROFILING_CHAT_HITL] Applying modification | text_response_len=%d",
                len(profiling_response.get("text_response") or ""),
            )
            modified_response = _profiling_chat_apply_chunked_modification(
                client=client,
                user_message=request.user_message,
                profiling_response=profiling_response,
                skip_text_response_edit=False,
            )
        except Exception as edit_err:
            logger.error("[PROFILING_CHAT_HITL][EDIT_FAILED] %s", edit_err)
            raise HTTPException(500, f"Failed to apply modification: {edit_err}")

        logger.warning(
            "[PROFILING_CHAT_HITL][TOOL_RESPONSE_AFTER] type=%s | len=%d | preview=%r",
            type(modified_response).__name__,
            len(modified_response) if isinstance(modified_response, dict) else -1,
            str(modified_response)[:300],
        )

        # --------------------------------------------------
        # Persist updated canonical response (GCS + Vertex + HITL context keys)
        # --------------------------------------------------
        try:
            uri = await _persist_profiling_chat_update(
                session_service=session_service,
                session=session,
                session_id=request.session_id,
                canonical_response=modified_response,
            )
            logger.warning(
                "[PROFILING_CHAT_HITL][PERSISTED] profiling_chat_response_uri=%s | session=%s",
                uri,
                request.session_id,
            )
        except Exception:
            logger.exception(
                "[PROFILING_CHAT_HITL][GCS_UPDATE_FAILED] session=%s",
                request.session_id,
            )
            raise HTTPException(500, "Failed to persist profiling chat update")

        return {
            "session_id": request.session_id,
            "mode": "UPDATE",
            "text_response": modified_response.get("text_response", ""),
            "tool_response": modified_response.get("tool_response", {}),
        }

    except HTTPException:
        raise
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        logger.exception(
            "[PROFILING_CHAT_HITL][ERROR] user=%s | session=%s",
            request.user_id, request.session_id,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/messages/chat/human-in-the-loop/similarity-chat")
async def similarity_check_human_in_the_loop(request: SimilarityChatHITLRequest):
    """
    HITL endpoint for the Smart Similarity response from /similarity-check.

    Reads `final_similarity_response` from Vertex AI session state.
    - QUESTION -> returns natural language answer from the similarity data
    - UPDATE   -> applies the user modification, persists back to session state,
                  returns the full modified payload
    """
    try:
        logger.info(
            "[SIMILARITY_HITL][REQUEST] user=%s | session=%s | app=%s",
            request.user_id, request.session_id, request.app_name,
        )

        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
        )

        session = await session_service.get_session(
            user_id=request.user_id,
            session_id=request.session_id,
            app_name=request.app_name,
        )

        if not session or not session.state:
            raise HTTPException(400, "Session state not found")

        if "final_similarity_response" not in session.state:
            raise HTTPException(
                400,
                "No similarity response in session. Run /similarity-check first.",
            )

        similarity_response = session.state["final_similarity_response"]
        tool_response = (
            similarity_response.get("tool_response", {})
            if isinstance(similarity_response, dict)
            else {}
        )

        logger.warning(
            "[SIMILARITY_HITL][TOOL_RESPONSE_BEFORE] type=%s | preview=%r",
            type(tool_response).__name__,
            str(tool_response)[:300],
        )

        # --------------------------------------------------
        # APPLY CHANGES fast-path — persist directly, no LLM
        # --------------------------------------------------
        if request.apply_changes:
            logger.info("[SIMILARITY_HITL][MODE] APPLY_CHANGES")

            applied_tool = request.tool_response or tool_response
            existing_text = (
                similarity_response.get("text_response", "")
                if isinstance(similarity_response, dict)
                else ""
            )
            applied_text = (request.text_response or "").strip()
            if not applied_text and applied_tool:
                applied_text = regenerate_similarity_text_response(
                    applied_tool,
                    existing_text=existing_text,
                )
            elif not applied_text:
                applied_text = existing_text

            updated_similarity_response = {
                **(similarity_response if isinstance(similarity_response, dict) else {}),
                "text_response": applied_text,
                "tool_response": applied_tool,
            }

            update_event = Event(
                author="system",
                invocation_id=f"similarity-hitl-apply-{uuid.uuid4()}",
                actions=EventActions(
                    state_delta={"final_similarity_response": updated_similarity_response}
                ),
            )
            await session_service.append_event(session=session, event=update_event)
            logger.warning(
                "[SIMILARITY_HITL][STATE_UPDATED] apply_changes persisted | session=%s",
                request.session_id,
            )

            return {
                "session_id": request.session_id,
                "mode": "APPLY_CHANGES",
                "text_response": updated_similarity_response["text_response"],
                "tool_response": updated_similarity_response["tool_response"],
                "should_update": True,
            }

        # --------------------------------------------------
        # Run HITL agent to classify intent (QUESTION vs UPDATE)
        # --------------------------------------------------
        hitl_app = _build_orchestrator_app(request.app_name, similarity_hitl_agent)
        runner = Runner(app=hitl_app, session_service=session_service)

        msg = types.Content(role="user", parts=[types.Part(text=request.user_message)])
        msg = await _guard_message_tokens(
            msg,
            request.session_id,
            "/messages/similarity-check",
            session=session,
            session_service=session_service,
            app_name=request.app_name,
            user_id=request.user_id,
        )

        raw_message = ""
        async for event in runner.run_async(
            user_id=request.user_id,
            session_id=request.session_id,
            new_message=msg,
            run_config=_build_token_guard_run_config(),
        ):
            if getattr(event, "agent_name", None) == similarity_hitl_agent.name:
                if hasattr(event, "content") and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            raw_message += part.text
                break
            elif hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        raw_message += part.text

        raw_message = raw_message.strip()

        agent_message = raw_message
        try:
            parsed = json.loads(raw_message)
            agent_message = parsed.get("message", raw_message).strip()
        except json.JSONDecodeError:
            agent_message = raw_message.strip()

        logger.warning("[SIMILARITY_HITL][AGENT_MESSAGE] value=%r", agent_message)

        # --------------------------------------------------
        # QUESTION path
        # --------------------------------------------------
        if agent_message != "UPDATE":
            logger.info("[SIMILARITY_HITL][MODE] QUESTION")
            return {
                "session_id": request.session_id,
                "mode": "QUESTION",
                "text_response": agent_message,
                "tool_response": None,
            }

        # --------------------------------------------------
        # UPDATE path — compute proposed changes (persist only on apply_changes)
        # --------------------------------------------------
        logger.info("[SIMILARITY_HITL][MODE] UPDATE (preview only)")

        hitl_payload = (
            similarity_response
            if isinstance(similarity_response, dict)
            else {"tool_response": tool_response, "text_response": ""}
        )
        hitl_result = apply_similarity_hitl_full_update(
            user_query=request.user_message,
            similarity_response=hitl_payload,
        )
        proposed_tool_response = hitl_result["tool_response"]
        proposed_text_response = hitl_result["text_response"]

        logger.warning(
            "[SIMILARITY_HITL][PROPOSED_TOOL] type=%s | preview=%r",
            type(proposed_tool_response).__name__,
            str(proposed_tool_response)[:300],
        )
        logger.warning(
            "[SIMILARITY_HITL][TEXT_RESPONSE_AFTER] len=%d | preview=%r",
            len(proposed_text_response),
            proposed_text_response[:300],
        )

        return {
            "session_id": request.session_id,
            "mode": "UPDATE",
            "text_response": proposed_text_response,
            "tool_response": proposed_tool_response,
            "should_update": False,
        }

    except HTTPException:
        raise
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        logger.exception(
            "[SIMILARITY_HITL][ERROR] user=%s | session=%s",
            request.user_id, request.session_id,
        )
        raise HTTPException(status_code=500, detail=str(e))
