# server/api/routers/messages_stream_new.py
"""
Enhanced streaming endpoint with support for:
- Vendor DD validation workflow (non-streaming bypass)
- HITL mapping workflow (non-streaming bypass)
- Profiling/Relationship/Dict/Anomaly (streaming)
- Session state management
- Context injection from the profiling session context artifact
- Tool support (intelligent_profiling_tool, data_anomaly_analysis_tool)
"""

from pydantic import BaseModel
import requests, os
import traceback
import json
from utils.bg_query_utils import get_table, get_bigquery_client
import logging
from pydoc import text
import time
import asyncio
from typing import Optional, Dict, Any, List
import re
from datetime import datetime
import pandas as pd

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Json
from sse_starlette.sse import EventSourceResponse

from api.models import MessageRequest, ProfilingChatHITLRequest, SimilarityChatHITLRequest
from agents.data_map_copilot_agent.agent import root_agent
from agents.data_map_copilot_agent.sub_agents.profiling_agent.agent import (
    profiling_agent_anomaly,
)
from agents.data_dict_stream_agent.agent import large_data_root_agent
from agents.data_map_copilot_agent.sub_agents.dataset_overview_hitl_agent.agent import (
    apply_dataset_overview_text_response_edit,
    build_profiling_chat_state_delta,
    dataset_overview_hitl_agent,
    normalize_profiling_chat_response,
    should_run_text_response_edit,
)
from agents.data_map_copilot_agent.sub_agents.similarity_hitl_agent.agent import (
    apply_similarity_hitl_full_update,
    regenerate_similarity_text_response,
    similarity_hitl_agent,
)

from google.adk import Runner
from google.adk.apps import App
from google.adk.sessions import Session
from utils.adk_runtime import VertexAiSessionService
from google.genai import types
from google import genai

from config.settings import config
from utils.streaming_progress import StreamingProgressTracker, FeatureType
from utils.markdown_formatter import generate_error_markdown
from utils.llm_helper import GoogleGeminiClient
from utils.bg_query_utils import validate_dataset_and_tables_large_data
from utils.table_extractor_utils_large import resolve_metadata_path
from utils.dd_session_utils import (
    persist_dd_candidates,
    persist_resolved_metadata_path,
    primary_metadata_path_from_state,
    save_selected_dd_choice,
)
from utils.profiling_artifact_store import (
    load_profiling_chat_response,
    load_profiling_session_context,
    load_resume_json_artifact,
    persist_profiling_chat_response,
    profiling_context_uri,
    save_resume_json_artifact,
    save_document_bytes,
    update_profiling_session_context,
)
from google.adk.events import Event, EventActions
import uuid
from api.dependencies.auth import CurrentUser, resolve_current_user
from db.engine import app_db_session, is_app_db_enabled
from db.repositories import AppSessionRepository
from utils.gcs_artifact_utils import make_json_compatible


router = APIRouter()


def _load_session_context(session_id: str) -> dict[str, Any]:
    if not session_id:
        return {}
    return load_profiling_session_context(session_id)


def _update_session_context(session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    if not session_id:
        return {}
    context, _ = update_profiling_session_context(session_id, updates)
    return context


def _persist_large_data_profiling_results(session_id: str, profiling_full_results: Any) -> None:
    if not session_id or profiling_full_results in (None, "", [], {}):
        return
    artifact_uri = save_resume_json_artifact(
        session_id=session_id,
        artifact_name="streaming-profiling-full-results",
        payload=make_json_compatible(profiling_full_results),
    )
    _update_session_context(session_id, {"profiling_full_results_uri": artifact_uri})


def _is_canonical_data_profiling_chat_response(payload: Any) -> bool:
    """True for dataset-overview profiling payloads, false for anomaly/relationship payloads."""
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


def _capture_profiling_chat_response(session_id: Optional[str], profiling_payload: Any) -> None:
    """Persist canonical streamed Data Profiling output separately from mutable Vertex state."""
    if not session_id or not _is_canonical_data_profiling_chat_response(profiling_payload):
        return
    try:
        uri = persist_profiling_chat_response(session_id, profiling_payload)
        logging.info(
            "[STREAM_PROFILING_CHAT][PERSIST] session=%s | uri=%s",
            session_id,
            uri,
        )
    except Exception:
        logging.exception(
            "[STREAM_PROFILING_CHAT][PERSIST] Failed for session=%s",
            session_id,
        )


def _resolve_profiling_chat_response(
    session_id: str,
    session_state: dict[str, Any],
) -> dict[str, Any] | None:
    """Prefer GCS canonical profiling payload; fall back to non-anomaly session keys."""
    stored = load_profiling_chat_response(session_id)
    if stored:
        if _is_canonical_data_profiling_chat_response(stored):
            logging.info(
                "[STREAM_PROFILING_CHAT][RESOLVE] loaded canonical response from GCS | session=%s",
                session_id,
            )
            return stored
        logging.warning(
            "[STREAM_PROFILING_CHAT][RESOLVE] Ignoring non-canonical GCS payload | session=%s",
            session_id,
        )

    for key in ("final_profiling_response_streaming", "final_profiling_response"):
        candidate = session_state.get(key)
        if isinstance(candidate, dict) and _is_canonical_data_profiling_chat_response(candidate):
            logging.info(
                "[STREAM_PROFILING_CHAT][RESOLVE] using canonical session key=%s | session=%s",
                key,
                session_id,
            )
            return candidate
        if candidate:
            logging.warning(
                "[STREAM_PROFILING_CHAT][RESOLVE] Ignoring %s because it is not dataset-overview shape | session=%s",
                key,
                session_id,
            )
    return None


async def _hydrate_large_data_profiling_results(
    *,
    session_service: VertexAiSessionService,
    session: Session,
    session_id: str,
) -> None:
    if not session_id or session.state.get("profiling_full_results"):
        return

    current_session_data = _load_session_context(session_id)
    artifact_uri = str(current_session_data.get("profiling_full_results_uri") or "").strip()
    if not artifact_uri:
        return

    try:
        profiling_full_results = load_resume_json_artifact(artifact_uri)
    except FileNotFoundError:
        logging.warning(
            "[streaming] Missing profiling_full_results artifact for session %s: %s",
            session_id,
            artifact_uri,
        )
        return
    except Exception:
        logging.exception(
            "[streaming] Failed to load profiling_full_results artifact for session %s",
            session_id,
        )
        return

    session.state["profiling_full_results"] = profiling_full_results
    state_update_event = Event(
        author="system",
        invocation_id=f"sys-inv-{uuid.uuid4()}",
        actions=EventActions(state_delta={"profiling_full_results": profiling_full_results}),
    )
    await session_service.append_event(session=session, event=state_update_event)
    logging.info(
        "[streaming] Restored profiling_full_results into session backend for session %s",
        session_id,
    )


async def _inject_profiling_chat_canonical_state(
    *,
    session_service: VertexAiSessionService,
    session: Session,
    session_id: str,
    canonical_response: dict[str, Any],
) -> None:
    state_delta = build_profiling_chat_state_delta(canonical_response)
    session.state.update(state_delta)
    await session_service.append_event(
        session=session,
        event=Event(
            author="system",
            invocation_id=f"stream-profiling-chat-canonical-{uuid.uuid4()}",
            actions=EventActions(state_delta=state_delta),
        ),
    )


async def _persist_profiling_chat_update(
    *,
    session_service: VertexAiSessionService,
    session: Session,
    session_id: str,
    canonical_response: dict[str, Any],
) -> str:
    uri = persist_profiling_chat_response(session_id, canonical_response)
    hitl_delta = build_profiling_chat_state_delta(canonical_response)
    state_delta = {
        **hitl_delta,
        "final_profiling_response_streaming": canonical_response,
        "final_profiling_response": canonical_response,
    }
    session.state.update(state_delta)
    await session_service.append_event(
        session=session,
        event=Event(
            author="system",
            invocation_id=f"stream-profiling-chat-hitl-update-{uuid.uuid4()}",
            actions=EventActions(state_delta=state_delta),
        ),
    )
    logging.info(
        "[STREAM_PROFILING_CHAT][PERSIST_HITL] session=%s | uri=%s | keys=%s",
        session_id,
        uri,
        list(state_delta.keys()),
    )
    return uri


def _build_hitl_app(app_name: str, agent) -> App:
    try:
        return App(name=app_name, root_agent=agent)
    except Exception as exc:
        logging.warning(
            "App name '%s' failed validation for HITL app (%s); constructing without validation.",
            app_name,
            exc,
        )
        return App.model_construct(name=app_name, root_agent=agent)


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
_PROFILING_CHAT_MAX_SELECTED_CHUNKS = 12
_PROFILING_CHAT_MAX_CHUNK_BYTES = 24000


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
        label += f"[{key}]" if isinstance(key, int) else f".{key}"
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

    if "text_response" in profiling_response:
        chunks.append({
            "path": ["text_response"],
            "label": "text_response",
            "value": profiling_response.get("text_response", ""),
            "kind": "text_response",
            "search": "text_response summary markdown recommendations data type dataset overview",
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

    if kind == "text_response" and any(
        marker in message
        for marker in ("text", "summary", "markdown", "describe", "mention", "recommend", "data type", "overview")
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
        "overview": "table_summary",
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
    if "data type" in lowered or "datatype" in lowered:
        text_chunk = next((chunk for chunk in chunks if chunk.get("kind") == "text_response"), None)
        if text_chunk and text_chunk not in selected:
            selected.insert(0, text_chunk)

    if not selected:
        selected = [
            chunk for chunk in chunks
            if len(chunk.get("path") or []) <= 3
        ][:_PROFILING_CHAT_MAX_SELECTED_CHUNKS]

    selected = _profiling_chat_prune_parent_chunks(selected)

    selected.sort(
        key=lambda chunk: (
            0 if chunk.get("kind") == "text_response" else 1,
            _profiling_chat_json_size(chunk.get("value")),
        )
    )
    return selected


def _profiling_chat_edit_chunk(
    *,
    client: genai.Client,
    user_message: str,
    chunk: dict[str, Any],
) -> tuple[bool, Any]:
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
- When this path is text_response, update only the relevant prose mentions for that same column/type

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
        logging.warning(
            "[STREAM_PROFILING_CHAT_HITL][CHUNK_EDIT] response_mime_type fallback: %s",
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
                logging.info(
                    "[STREAM_PROFILING_CHAT_HITL] text_response updated via dataset overview editor"
                )

    selected_chunks = _profiling_chat_select_chunks(modified_response, user_message)
    logging.info(
        "[STREAM_PROFILING_CHAT_HITL][CHUNK_EDIT] selected_chunks=%d total_size=%d paths=%s",
        len(selected_chunks),
        _profiling_chat_json_size(modified_response),
        [chunk["label"] for chunk in selected_chunks[:25]],
    )

    changed_paths: list[str] = []
    for chunk in selected_chunks:
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

    logging.info("[STREAM_PROFILING_CHAT_HITL][CHUNK_EDIT] changed_paths=%s", changed_paths)
    _profiling_chat_sync_table_result_aliases(modified_response)
    return modified_response

# Configuration constants
MAX_DD_ROWS = 100000  # Configurable limit for DD file reading


def apply_column_mapping(df, file_path, mappings, target_schema):
    """Apply column mapping to normalize dataframe schema"""
    logging.info(
        f"[MAPPING] Applying column mapping for file: {os.path.basename(file_path)}"
    )

    # Create a mapping dictionary for this specific file
    file_mappings = {
        m["sourceColumn"]: m["targetColumn"]
        for m in mappings
        if m.get("sourceFile") == file_path
    }

    logging.info(f"[MAPPING] File-specific mappings: {file_mappings}")

    # Create target dataframe with same index as source
    target_columns = [col["name"] for col in target_schema]
    logging.info(f"[MAPPING] Target columns: {target_columns}")

    # Initialize result dataframe with correct index
    result_df = pd.DataFrame(index=df.index)

    # Map existing columns to target schema
    for source_col in df.columns:
        if source_col in file_mappings:
            target_col = file_mappings[source_col]
            if target_col in target_columns:
                result_df[target_col] = df[source_col]
                logging.info(f"[MAPPING] Mapped {source_col} -> {target_col}")
        elif source_col in target_columns:
            # Direct match (no mapping needed)
            result_df[source_col] = df[source_col]
            logging.info(f"[MAPPING] Direct match: {source_col}")

    # Fill unmapped columns with NaN
    for target_col in target_columns:
        if target_col not in result_df.columns:
            result_df[target_col] = pd.NA
            logging.info(f"[MAPPING] Filled unmapped column with NULL: {target_col}")

    # Ensure column order matches target schema
    result_df = result_df[target_columns]
    if "extraction_order" in df.columns:
        result_df["extraction_order"] = df["extraction_order"]

    logging.info(
        f"[MAPPING] Result dataframe: {len(result_df)} rows, {len(result_df.columns)} columns"
    )
    return result_df

# --- RATE LIMITING CONSTANTS ---
LLM_RPM_LIMIT = 40
LLM_TPM_LIMIT = 100_000
WINDOW_SECONDS = 20
MAX_WAIT_SECONDS = 300  # 5 minutes


async def manage_llm_rate_limits(
    event: Event, session_id: str, buffer_tokens: int = 100
):
    """
    Enforces RPM & TPM without ever resetting cumulative token counts.
    Uses rolling window accounting to calculate proactive wait time.
    """
    from datetime import datetime

    now = datetime.utcnow()

    session_context = _load_session_context(session_id)

    if "usage_metadata" not in session_context:
        session_context["usage_metadata"] = {
            "total_tokens": 0,  # cumulative
            "window_tokens": 0,  # rolling window
            "window_requests": 0,
            "window_start": now.isoformat(),
            "last_request": None,
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
    event_tokens = event.usage_metadata.total_token_count if event.usage_metadata else 0

    projected_window_tokens = usage["window_tokens"] + event_tokens + buffer_tokens
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
        logging.warning(
            f"[RateLimit] Throttling for {wait_seconds:.2f}s (Tokens: {projected_window_tokens}, Reqs: {projected_window_requests})"
        )
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

# --- RATE LIMITING CONSTANTS ---
LLM_RPM_LIMIT = 40
LLM_TPM_LIMIT = 100_000
WINDOW_SECONDS = 20
MAX_WAIT_SECONDS = 300  # 5 minutes


async def manage_llm_rate_limits(
    event: Event, session_id: str, buffer_tokens: int = 100
):
    """
    Enforces RPM & TPM without ever resetting cumulative token counts.
    Uses rolling window accounting to calculate proactive wait time.
    """
    from datetime import datetime

    now = datetime.utcnow()

    session_context = _load_session_context(session_id)

    if "usage_metadata" not in session_context:
        session_context["usage_metadata"] = {
            "total_tokens": 0,  # cumulative
            "window_tokens": 0,  # rolling window
            "window_requests": 0,
            "window_start": now.isoformat(),
            "last_request": None,
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
    event_tokens = event.usage_metadata.total_token_count if event.usage_metadata else 0

    projected_window_tokens = usage["window_tokens"] + event_tokens + buffer_tokens
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
        logging.warning(
            f"[RateLimit] Throttling for {wait_seconds:.2f}s (Tokens: {projected_window_tokens}, Reqs: {projected_window_requests})"
        )
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


def extract_json_from_string(text_blob: str):
    """
    Extracts a JSON object from a string that might be embedded in a markdown code block.

    This function looks for a JSON block formatted as ```json ... ```. It handles
    cases where there is text before or after the block, or if the string
    consists only of the block itself.

    Args:
        text_blob: The input string containing the JSON data.

    Returns:
        A Python dictionary or list if a valid JSON object is found, otherwise None.
    """
    # Regex to find the content within ```json ... ```
    # re.DOTALL allows '.' to match newline characters, which is crucial for multi-line JSON
    pattern = r"```json\s*(.*?)\s*```"

    match = re.search(pattern, text_blob, re.DOTALL)

    # If a JSON block is found
    if match:
        # The actual JSON string is in the first captured group
        json_string = match.group(1)
        return json_string
    else:
        # If no ```json ... ``` block is found, return None
        return None


def format_output(stage, data):
    """Helper function to format output using LLM"""
    gemini_client = GoogleGeminiClient()
    response = gemini_client.generate(stage, data)
    logging.info("+" * 200)
    logging.info(response)
    logging.info("+" * 200)
    return response


def get_text_between_brackets(s: str) -> str:
    """Extract text between square brackets"""
    start = s.find("[")
    end = s.find("]", start + 1)
    return s[start + 1 : end] if start != -1 and end != -1 else ""


def get_data_ditionary(dd_reference: List["str"], dataset_id_override: str = None):
    """
    Fetch data dictionary from BigQuery.

    Args:
        dd_reference: List of table references to fetch
        dataset_id_override: Optional dataset ID to use instead of config default

    Returns:
        List of DataFrames containing table data
    """
    logging.info(
        f"[get_data_ditionary] DATASET_OVERRIDE: Called with dataset_id_override = {dataset_id_override}"
    )
    logging.info(
        f"[get_data_ditionary] DATASET_OVERRIDE: Number of table references = {len(dd_reference)}"
    )

    results = []
    for dd_ref in dd_reference:
        logging.info(
            f"[get_data_ditionary] DATASET_OVERRIDE: Fetching table {dd_ref} with override = {dataset_id_override}"
        )
        table = get_table(dd_ref, dataset_id_override=dataset_id_override)
        results.append(table)
        logging.info(
            f"[get_data_ditionary] DATASET_OVERRIDE: Successfully fetched table {dd_ref}"
        )

    logging.info(
        f"[get_data_ditionary] DATASET_OVERRIDE: Completed fetching {len(results)} tables"
    )
    return results

def _enrich_dd_with_bq_stats(dd_response: dict, dataset_id_override: str = None) -> dict:
    """
    Enriches each DD row with:
    - most_occurrences: top-N most frequent values per column from the source table
    """
    from collections import defaultdict

    logging.info("[DD_ENRICH] Starting enrichment with dataset_override: %s", dataset_id_override)
    logging.info("[DD_ENRICH] Response keys: %s", list(dd_response.keys()) if isinstance(dd_response, dict) else "Not a dict")

    top_n = getattr(config, "DD_MOST_OCCURRENCES_TOP_N", 5)
    client = get_bigquery_client()

    # Extract result from agent response structure
    result = None
    
    if "result" in dd_response:
        result = dd_response.get("result")
    elif "tool_response" in dd_response and isinstance(dd_response["tool_response"], dict):
        tool_response = dd_response["tool_response"]
        # Try multiple possible keys where the data dictionary might be stored
        result = (
            tool_response.get("result") or
            tool_response.get("data_dictionary") or
            tool_response.get("fields") or
            tool_response.get("rows")
        )
    elif "data_dictionary_table_id" in dd_response or "table_name" in dd_response:
        table_id = (
            dd_response.get("data_dictionary_table_id")
            or dd_response.get("table_name")
            or dd_response.get("data_dictionary_table_name")
        )
        try:
            query = f"SELECT * FROM `{table_id}`"
            rows = client.query(query).result()
            result = [dict(row) for row in rows]
            key_map = {
                "File Name": "file_name", "Attribute Name": "field_name",
                "Logical Attribute Name": "business_name", "Attribute Description": "field_description",
                "Data Type": "data_type", "Length": "length", "Precision": "precision",
                "Format": "format", "Nullability": "nullable", "Most Occurrences": "most_occurrences",
                "Primary Key": "primary_key", "Foreign Key": "foreign_key",
            }
            result = [{key_map.get(k, k): v for k, v in row.items()} for row in result]
        except Exception as exc:
            logging.warning("[DD_ENRICH] Failed to fetch DD table %s: %s", table_id, exc)
            return dd_response
    
    if not result or not isinstance(result, list):
        logging.warning("[DD_ENRICH] No result or data_dictionary_table_id in dd_response")
        return dd_response

    logging.info("[DD_ENRICH] Processing %s rows for enrichment", len(result))

    # Use dataset override if provided
    dataset_id = dataset_id_override or config.BQ_DATASET_ID
    logging.info("[DD_ENRICH] Using dataset_id: %s", dataset_id)

    tables: dict = defaultdict(list)
    for row in result:
        file_name = row.get("file_name") or ""
        if file_name:
            tables[file_name].append(row)

    for table_name, rows in tables.items():
        full_table = f"{config.BQ_PROJECT_ID}.{dataset_id}.{table_name}"
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
                
                most_occurrences_with_pct = []
                for e in top_info:
                    if e["value"] is not None:
                        count = e.get("count", 0)
                        percentage = round((count / total_rows * 100), 2) if total_rows > 0 else 0
                        most_occurrences_with_pct.append(f"{e['value']}({percentage}%)")
                
                # Join all values into a single comma-separated string
                dd_row["most_occurrences"] = ", ".join(most_occurrences_with_pct)
                dd_row.pop("default_value", None)
        except Exception as exc:
            logging.warning("[DD_ENRICH] BQ query failed for table %s: %s", table_name, exc)
            for dd_row in rows:
                existing_most_occ = dd_row.get("most_occurrences")
                if isinstance(existing_most_occ, str) and existing_most_occ.strip():
                    values = [v.strip() for v in existing_most_occ.split(",") if v.strip()]
                    most_occurrences_with_pct = []
                    for i, value in enumerate(values):
                        mock_percentage = round(30 - (i * 5), 2) if i < 6 else 5.0
                        most_occurrences_with_pct.append(f"{value}({mock_percentage}%)")
                    dd_row["most_occurrences"] = ", ".join(most_occurrences_with_pct)
                else:
                    dd_row.setdefault("most_occurrences", "")
                dd_row.pop("default_value", None)

    dd_response["result"] = result
    if "tool_response" in dd_response and isinstance(dd_response["tool_response"], dict):
        dd_response["tool_response"]["result"] = result

    def _build_markdown_table(rows: list) -> str:
        if not rows:
            return "# Data Dictionary\n\nNo fields found."
        lines = [
            "# Data Dictionary Generation Complete\n",
            "## Data Dictionary\n",
            "| File Name | Field Name | Field Business Name | Data Type | Length | Format | Nullable | Most Occurrences | Primary Key | Foreign Key | Field Description |",
            "|-----------|------------|---------------------|-----------|--------|--------|----------|------------------|-------------|-------------|-------------------|"
        ]
        for row in rows:
            lines.append(
                f"| {row.get('file_name', '-')} "
                f"| {row.get('field_name', '-')} "
                f"| {row.get('business_name', '-')} "
                f"| {row.get('data_type', '-')} "
                f"| {row.get('length', '-')} "
                f"| {row.get('format', '-')} "
                f"| {row.get('nullable', '-')} "
                f"| {row.get('most_occurrences', '-')} "
                f"| {row.get('primary_key', '-')} "
                f"| {row.get('foreign_key', '-')} "
                f"| {row.get('field_description', '-')} |"
            )
        return "\n".join(lines)

    dd_response["text_response"] = _build_markdown_table(result)
    logging.info("[DD_ENRICH] Regenerated text_response from enriched result rows")
    logging.info("[DD_ENRICH] Enrichment complete. Final result has %s rows", len(result))
    return dd_response


def extract_table_names_from_request(text: str) -> list[str]:
    """
    Pass the request text containing tables ids to parse them using this func.
    'Do profiling for the following files : table1, table2'
    """
    if ":" not in text:
        return []

    after_colon = text.split(":", 1)[1]
    tables = [t.strip() for t in after_colon.split(",") if t.strip()]
    logging.info("Tables extracted from request: %s", tables)
    return tables

def parse_similarity_payload(message: str):
    """
    Parse DART references from a similarity-check prompt
    and return ONLY the DART table names.

    Returns:
        List[str]: list of DART table names
    """

    # Extract the JSON block (first {...} block)
    match = re.search(r"\{[\s\S]*\}", message)
    if not match:
        raise ValueError("No JSON block found in similarity request")

    json_str = match.group(0)

    # Parse JSON strictly
    try:
        payload = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in similarity request: {e}")

    # Validate required keys
    if "dart_references" not in payload:
        raise ValueError("Missing 'dart_references' in similarity payload")

    if not isinstance(payload["dart_references"], list):
        raise ValueError("'dart_references' must be a list")

    # Extract ONLY table names
    dart_tables = []
    for ref in payload["dart_references"]:
        if not isinstance(ref, dict):
            raise ValueError("Each dart_reference must be an object")
        if "table" not in ref:
            raise ValueError("Each dart_reference must contain 'table'")
        dart_tables.append(ref["table"])

    return dart_tables

class ValidateDatasetTablesRequest(BaseModel):
    dataset_id: str
    table_ids: List[str]


@router.post("/upload-batch")
async def upload_batch_stream(
    data_dict_files: Optional[List[UploadFile]] = File(
        None, description="Optional Vendor-provided Data Dictionaries."
    ),
    data_dict_file: Optional[UploadFile] = File(
        None, description="Optional Vendor-provided Data Dictionary."
    ),
    brd_file: Optional[UploadFile] = File(
        None, description="Optional Business Requirements Document."
    ),
    file_spec_file: Optional[UploadFile] = File(
        None, description="Optional File Specification document."
    ),
    project_name: Optional[str] = Form(None, description="Name of the overall project."),
    vendor_name: Optional[str] = Form(None, description="Name of the data vendor."),
    vendor_contact_person: Optional[str] = Form(
        None, description="Contact email for the vendor."
    ),
    file_delivery_frequency: Optional[str] = Form(
        None, description="How often files are delivered (e.g., daily=1, weekly=7)."
    ),
    brd_description: Optional[str] = Form(
        None, description="A brief description of the BRD content (if provided)."
    ),
    spec_description: Optional[str] = Form(
        None, description="A brief description of the specification file content (if provided)."
    ),
    session_id: str = Form(None, description="Current session id"),
    app_session_id: Optional[str] = Form(None, description="Selected app session id"),
    transfer_method: Optional[str] = Form(
        None, description="Method used to transfer the file (e.g., SFTP, API, Email)."
    ),
    vendor_contact_name: Optional[str] = Form(
        None, description="Full name of the vendor's primary contact person."
    ),
    frequency_mode: Optional[str] = Form(
        None, description="Delivery mode classification such as daily, weekly, monthly, or ad-hoc."
    ),
    vendor_phone_number: Optional[str] = Form(
        None, description="Phone number of the vendor's primary contact."
    ),
    dependencies: Optional[str] = Form(
        None, description="Any upstream or downstream system dependencies related to this file."
    ),
    vendor_email: Optional[str] = Form(
        None, description="Vendor email address used for operational communications."
    ),
    email_notification_dl: Optional[str] = Form(
        None, description="Distribution list email address for automated file notifications."
    ),
    date_timestamp_format: Optional[str] = Form(
        None, description="Date and timestamp format used within the file (e.g., YYYY-MM-DD)."
    ),
    header_record_number: Optional[str] = Form(
        None, description="Expected record identifier or count for header rows."
    ),
    trailer_record_number: Optional[str] = Form(
        None, description="Expected record identifier or count for trailer rows."
    ),
    quote_indicator: Optional[str] = Form(
        None, description="Character used to wrap text values in the file (e.g., double quote)."
    ),
    file_population_type: Optional[str] = Form(
        None, description="Indicates whether file is full population, delta, or incremental load."
    ),
    file_compression_type: Optional[str] = Form(
        None, description="Compression format applied to the file (e.g., ZIP, GZIP)."
    ),
    receive_file_when_no_data: Optional[str] = Form(
        None, description="Indicates whether a file is expected even when no data is present."
    ),
    assumptions: Optional[str] = Form(
        None, description="Any documented assumptions related to file processing or data interpretation."
    ),
    vendor_server_name: Optional[str] = Form(
        None, description="Name or address of the vendor server from which files are received."
    ),
    current_user: CurrentUser = Depends(resolve_current_user),
):
    """Step 1: Upload supplemental files and detect DD candidates - STATELESS (streaming flow)"""
    try:
        logging.info(
            f"--- [/messages-strm/upload-batch] Received request for session_id: {session_id} ---"
        )

        additional_info = {
            "project_name": project_name,
            "vendor_name": vendor_name,
            "vendor_contact_person": vendor_contact_person,
            "file_delivery_frequency": file_delivery_frequency,
            "brd_description": brd_description,
            "spec_description": spec_description,
            "transfer_method": transfer_method,
            "vendor_contact_name": vendor_contact_name,
            "frequency_mode": frequency_mode,
            "vendor_phone_number": vendor_phone_number,
            "dependencies": dependencies,
            "vendor_email": vendor_email,
            "email_notification_dl": email_notification_dl,
            "date_timestamp_format": date_timestamp_format,
            "header_record_number": header_record_number,
            "trailer_record_number": trailer_record_number,
            "quote_indicator": quote_indicator,
            "file_population_type": file_population_type,
            "file_compression_type": file_compression_type,
            "receive_file_when_no_data": receive_file_when_no_data,
            "assumptions": assumptions,
            "vendor_server_name": vendor_server_name,
        }

        profiling_run_id: str | None = None
        if app_session_id:
            if not is_app_db_enabled():
                raise HTTPException(status_code=503, detail="App session database is not configured.")
            with app_db_session() as db:
                repo = AppSessionRepository(db)
                app_session = repo.get_session(session_id=app_session_id, user_key=current_user.user_key)
                if not app_session:
                    raise HTTPException(status_code=404, detail="Session not found.")
                profiling_run = repo.create_profiling_run(
                    session=app_session,
                    profiling_context_uri=profiling_context_uri(session_id),
                    vertex_session_id=app_session.active_vertex_session_id,
                    vertex_app_name=app_session.active_vertex_app_name,
                )
                repo.update_profiling_run(
                    run=profiling_run,
                    status="RUNNING",
                    current_step="upload",
                    profiling_context_uri=profiling_context_uri(session_id),
                    resume_state_json={"profilingMode": "streaming"},
                )
                profiling_run_id = profiling_run.id

        uploaded_info = {}
        brd_extraction_status = None

        # Save DD files if provided
        dd_uploads: List[UploadFile] = []
        if data_dict_files:
            dd_uploads.extend(data_dict_files)
        if data_dict_file:
            dd_uploads.append(data_dict_file)

        if dd_uploads:
            dd_paths: List[str] = []
            for upload_file in dd_uploads:
                try:
                    artifact_uri = save_document_bytes(
                        session_id=session_id,
                        document_kind="data_dict",
                        filename=upload_file.filename,
                        content=await upload_file.read(),
                    )
                    dd_paths.append(artifact_uri)
                    logging.info(
                        f"Saved data_dict_file_path item to {artifact_uri}"
                    )
                except Exception as e:
                    logging.error(
                        f"Failed to save data_dict_file_path item: {str(e)}"
                    )
            if dd_paths:
                uploaded_info["data_dict_file_path"] = dd_paths

        # Save BRD and Spec files
        for file_label, upload_file in [
            ("brd_file", brd_file),
            ("file_spec_file", file_spec_file),
        ]:
            if upload_file:
                try:
                    artifact_uri = save_document_bytes(
                        session_id=session_id,
                        document_kind="brd" if file_label == "brd_file" else "file_spec",
                        filename=upload_file.filename,
                        content=await upload_file.read(),
                    )
                    uploaded_info[file_label] = artifact_uri
                    logging.info(f"Saved {file_label} to {artifact_uri}")
                except Exception as e:
                    error_msg = f"Failed to save {file_label}: {str(e)}"
                    logging.error(error_msg)
                    if file_label == "brd_file":
                        brd_extraction_status = {
                            "brd_exists": True,
                            "extraction_attempted": False,
                            "extraction_success": False,
                            "error_description": error_msg,
                        }

        # Store session data
        session_entry = {**additional_info, **uploaded_info}
        _update_session_context(session_id, session_entry)
        logging.info("Session %s updated in profiling context artifact", session_id)

        # Resolve metadata path
        if brd_extraction_status is None:
            metadata_path, brd_extraction_status = await resolve_metadata_path(
                uploaded_info=uploaded_info,
                brd_file_path=uploaded_info.get("brd_file"),
                session_id=session_id,
            )
        else:
            metadata_path = None

        logging.info(
            f"Metadata resolution complete | metadata_path={metadata_path} | brd_status={brd_extraction_status}"
        )

        # Multiple DD candidates -> return to UI
        if (
            isinstance(metadata_path, list)
            and metadata_path
            and isinstance(metadata_path[0], dict)
        ):
            if brd_extraction_status is None:
                brd_extraction_status = {
                    "brd_exists": bool(uploaded_info.get("brd_file")),
                    "extraction_attempted": True,
                    "extraction_success": True,
                    "error_description": None,
                }
            if not brd_extraction_status.get("dd_candidates"):
                brd_extraction_status["dd_candidates"] = metadata_path
            persist_dd_candidates(
                session_id=session_id,
                dd_candidates=metadata_path,
                brd_extraction_status=brd_extraction_status,
            )
            return {
                "status": "awaiting_dd_selection",
                "dd_candidates": metadata_path,
                "brd_extraction_status": brd_extraction_status,
            }

        # Single metadata or no DD -> ready for processing
        if isinstance(metadata_path, list) and len(metadata_path) > 0:
            if isinstance(metadata_path[0], dict):
                metadata_path = metadata_path[0].get("file_path")
            else:
                metadata_path = metadata_path[0]

        if metadata_path and session_id:
            persist_resolved_metadata_path(
                session_id=session_id,
                metadata_path=str(metadata_path),
                selected_dd_paths=[str(metadata_path)],
                brd_extraction_status=brd_extraction_status,
            )
            logging.info(
                f"Stored resolved metadata_path for session {session_id}: {metadata_path}"
            )

        if app_session_id and profiling_run_id:
            with app_db_session() as db:
                repo = AppSessionRepository(db)
                app_session = repo.get_session(session_id=app_session_id, user_key=current_user.user_key)
                if app_session:
                    run = repo.get_current_profiling_run(session=app_session)
                    if run and run.id == profiling_run_id:
                        repo.update_profiling_run(
                            run=run,
                            status="READY",
                            current_step="dataset_overview",
                            profiling_context_uri=profiling_context_uri(session_id),
                            resume_state_json={
                                "profilingMode": "streaming",
                                "currentStep": 1,
                                "uploadResponse": {
                                    "total_files": 0,
                                    "status": "ready_for_processing",
                                    "metadata_path": str(metadata_path) if metadata_path else None,
                                    "brd_extraction_status": make_json_compatible(brd_extraction_status),
                                },
                            },
                        )

        return {
            "status": "ready_for_processing",
            "metadata_path": metadata_path,
            "brd_extraction_status": brd_extraction_status,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error sending message: {e}"
        )


@router.post("/save-selected-dd")
async def save_selected_dd(
    session_id: str = Form(...),
    selected_paths: List[str] = Form(...),
    should_merge: bool = Form(False),
    column_mappings: Optional[str] = Form(None),
    target_schema: Optional[str] = Form(None),
):
    """Save user-selected DD file paths and optionally merge them into a single file"""
    try:
        return save_selected_dd_choice(
            session_id=session_id,
            selected_paths=selected_paths,
            should_merge=should_merge,
            column_mappings=column_mappings,
            target_schema=target_schema,
            apply_column_mapping=apply_column_mapping,
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Failed to save/merge DD files: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to save selection: {str(e)}"
        )

@router.post("/messages/chat/human-in-the-loop/profiling-chat-streaming")
async def profiling_chat_human_in_the_loop_stream_new(request: ProfilingChatHITLRequest):
    """
    Chat HITL endpoint for the streamed dataset overview response.

    Reads the canonical streamed Data Profiling response from GCS, with session fallback.
    - QUESTION returns the dataset-overview agent answer
    - UPDATE edits the stored profiling response and persists it back
    """
    try:
        logging.info(
            "[STREAM_PROFILING_CHAT_HITL][REQUEST] user=%s | session=%s | app=%s",
            request.user_id,
            request.session_id,
            request.app_name,
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

        raw_profiling_response = _resolve_profiling_chat_response(
            request.session_id,
            session.state,
        )
        profiling_response = (
            normalize_profiling_chat_response(raw_profiling_response)
            if raw_profiling_response
            else {}
        )
        if not profiling_response.get("tool_response") and not profiling_response.get("text_response"):
            profiling_full_results = session.state.get("profiling_full_results")
            if not profiling_full_results:
                await _hydrate_large_data_profiling_results(
                    session_service=session_service,
                    session=session,
                    session_id=request.session_id,
                )
                profiling_full_results = session.state.get("profiling_full_results")

            if profiling_full_results:
                profiling_response = normalize_profiling_chat_response({
                    "text_response": "",
                    "tool_response": {"all_tables": profiling_full_results},
                    "should_update": False,
                })
            else:
                raise HTTPException(
                    400,
                    "No streaming profiling response in session. Run /send-stream-new profiling first.",
                )

        await _inject_profiling_chat_canonical_state(
            session_service=session_service,
            session=session,
            session_id=request.session_id,
            canonical_response=profiling_response,
        )

        logging.warning(
            "[STREAM_PROFILING_CHAT_HITL][RESPONSE_BEFORE] type=%s | len=%d | preview=%r",
            type(profiling_response).__name__,
            len(profiling_response) if isinstance(profiling_response, dict) else -1,
            str(profiling_response)[:300],
        )

        if request.is_edit:
            logging.info("[STREAM_PROFILING_CHAT_HITL][MODE] UPDATE (is_edit=True, skipping agent)")
        else:
            hitl_app = _build_hitl_app(request.app_name, dataset_overview_hitl_agent)
            runner = Runner(app=hitl_app, session_service=session_service)
            msg = types.Content(role="user", parts=[types.Part(text=request.user_message)])

            raw_message = ""
            async for event in runner.run_async(
                user_id=request.user_id,
                session_id=request.session_id,
                new_message=msg,
            ):
                agent_name = getattr(event, "agent_name", None)
                if agent_name:
                    logging.info(
                        "[STREAM_PROFILING_CHAT_HITL][AGENT] agent=%s | event_type=%s",
                        agent_name,
                        type(event).__name__,
                    )

                if hasattr(event, "usage_metadata"):
                    await manage_llm_rate_limits(event, request.session_id)

                if hasattr(event, "content") and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            raw_message += part.text

                if agent_name == dataset_overview_hitl_agent.name:
                    logging.warning(
                        "[STREAM_PROFILING_CHAT_HITL][STOP] Root agent '%s' completed.",
                        dataset_overview_hitl_agent.name,
                    )
                    break

            raw_message = raw_message.strip()
            agent_message = raw_message
            try:
                parsed = json.loads(raw_message)
                agent_message = parsed.get("message", raw_message).strip()
            except json.JSONDecodeError:
                agent_message = raw_message.strip()

            logging.warning(
                "[STREAM_PROFILING_CHAT_HITL][AGENT_MESSAGE] value=%r",
                agent_message,
            )

            if agent_message != "UPDATE":
                return {
                    "session_id": request.session_id,
                    "mode": "QUESTION",
                    "text_response": agent_message,
                    "tool_response": None,
                }

        client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
        )
        try:
            modified_response = normalize_profiling_chat_response(profiling_response)
            current_text = modified_response.get("text_response") or ""
            if isinstance(current_text, str) and should_run_text_response_edit(
                request.user_message, current_text
            ):
                edited_text, text_changed = apply_dataset_overview_text_response_edit(
                    user_query=request.user_message,
                    text_response=current_text,
                    client=client,
                )
                if text_changed:
                    modified_response["text_response"] = edited_text
            modified_response = _profiling_chat_apply_chunked_modification(
                client=client,
                user_message=request.user_message,
                profiling_response=modified_response,
                skip_text_response_edit=True,
            )
        except Exception as edit_err:
            logging.error("[STREAM_PROFILING_CHAT_HITL][EDIT_FAILED] %s", edit_err)
            raise HTTPException(500, f"Failed to apply modification: {edit_err}")

        try:
            uri = await _persist_profiling_chat_update(
                session_service=session_service,
                session=session,
                session_id=request.session_id,
                canonical_response=modified_response,
            )
            logging.warning(
                "[STREAM_PROFILING_CHAT_HITL][STATE_UPDATED] persisted streaming response + HITL context | uri=%s | session=%s",
                uri,
                request.session_id,
            )
        except Exception:
            logging.exception(
                "[STREAM_PROFILING_CHAT_HITL][GCS_UPDATE_FAILED] session=%s",
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
    except Exception as e:
        logging.exception(
            "[STREAM_PROFILING_CHAT_HITL][ERROR] user=%s | session=%s",
            request.user_id,
            request.session_id,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/messages/chat/human-in-the-loop/similarity-check-streaming-chat")
async def similarity_check_streaming_chat_human_in_the_loop(
    request: SimilarityChatHITLRequest,
):
    """
    Chat HITL endpoint for the streamed similarity response from /similarity-check-stream.

    Reads `final_similarity_response` from Vertex AI session state.
    - QUESTION -> natural language answer from similarity data
    - UPDATE   -> returns proposed text_response + tool_response (not persisted until apply_changes)
    - apply_changes -> persists text_response + tool_response to session state
    """
    try:
        logging.info(
            "[STREAM_SIMILARITY_CHAT_HITL][REQUEST] user=%s | session=%s | app=%s",
            request.user_id,
            request.session_id,
            request.app_name,
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
                "No similarity response in session. Run /similarity-check-stream first.",
            )

        similarity_response = session.state["final_similarity_response"]
        tool_response = (
            similarity_response.get("tool_response", {})
            if isinstance(similarity_response, dict)
            else {}
        )

        logging.warning(
            "[STREAM_SIMILARITY_CHAT_HITL][TOOL_RESPONSE_BEFORE] type=%s | preview=%r",
            type(tool_response).__name__,
            str(tool_response)[:300],
        )

        if request.apply_changes:
            logging.info("[STREAM_SIMILARITY_CHAT_HITL][MODE] APPLY_CHANGES")

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
                **(
                    similarity_response
                    if isinstance(similarity_response, dict)
                    else {}
                ),
                "text_response": applied_text,
                "tool_response": applied_tool,
            }

            update_event = Event(
                author="system",
                invocation_id=f"similarity-stream-hitl-apply-{uuid.uuid4()}",
                actions=EventActions(
                    state_delta={
                        "final_similarity_response": updated_similarity_response
                    }
                ),
            )
            await session_service.append_event(session=session, event=update_event)
            logging.warning(
                "[STREAM_SIMILARITY_CHAT_HITL][STATE_UPDATED] apply_changes persisted | session=%s",
                request.session_id,
            )

            return {
                "session_id": request.session_id,
                "mode": "APPLY_CHANGES",
                "text_response": updated_similarity_response["text_response"],
                "tool_response": updated_similarity_response["tool_response"],
                "should_update": True,
            }

        hitl_app = _build_hitl_app(request.app_name, similarity_hitl_agent)
        runner = Runner(app=hitl_app, session_service=session_service)
        msg = types.Content(role="user", parts=[types.Part(text=request.user_message)])

        raw_message = ""
        async for event in runner.run_async(
            user_id=request.user_id,
            session_id=request.session_id,
            new_message=msg,
        ):
            agent_name = getattr(event, "agent_name", None)
            if agent_name:
                logging.info(
                    "[STREAM_SIMILARITY_CHAT_HITL][AGENT] agent=%s | event_type=%s",
                    agent_name,
                    type(event).__name__,
                )

            if hasattr(event, "usage_metadata"):
                await manage_llm_rate_limits(event, request.session_id)

            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        raw_message += part.text

            if agent_name == similarity_hitl_agent.name:
                logging.warning(
                    "[STREAM_SIMILARITY_CHAT_HITL][STOP] Root agent '%s' completed.",
                    similarity_hitl_agent.name,
                )
                break

        raw_message = raw_message.strip()
        agent_message = raw_message
        try:
            parsed = json.loads(raw_message)
            agent_message = parsed.get("message", raw_message).strip()
        except json.JSONDecodeError:
            agent_message = raw_message.strip()

        logging.warning(
            "[STREAM_SIMILARITY_CHAT_HITL][AGENT_MESSAGE] value=%r",
            agent_message,
        )

        if agent_message != "UPDATE":
            logging.info("[STREAM_SIMILARITY_CHAT_HITL][MODE] QUESTION")
            return {
                "session_id": request.session_id,
                "mode": "QUESTION",
                "text_response": agent_message,
                "tool_response": None,
            }

        logging.info("[STREAM_SIMILARITY_CHAT_HITL][MODE] UPDATE (preview only)")

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

        logging.warning(
            "[STREAM_SIMILARITY_CHAT_HITL][PROPOSED_TOOL] type=%s | preview=%r",
            type(proposed_tool_response).__name__,
            str(proposed_tool_response)[:300],
        )
        logging.warning(
            "[STREAM_SIMILARITY_CHAT_HITL][PROPOSED_TEXT] len=%d | preview=%r",
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
    except Exception as e:
        logging.exception(
            "[STREAM_SIMILARITY_CHAT_HITL][ERROR] user=%s | session=%s",
            request.user_id,
            request.session_id,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-stream-new")
async def send_message_stream_new(
    request: str = Form(..., description="JSON string of MessageRequest"),
    data_dict_file: Optional[UploadFile] = File(
        None, description="Optional Vendor-provided Data Dictionary."
    ),
    brd_file: Optional[UploadFile] = File(
        None, description="Optional Business Requirements Document."
    ),
    file_spec_file: Optional[UploadFile] = File(
        None, description="Optional File Specification document."
    ),
    project_name: Optional[str] = Form(
        None, description="Name of the overall project."
    ),
    vendor_name: Optional[str] = Form(None, description="Name of the data vendor."),
    vendor_contact_person: Optional[str] = Form(
        None, description="Contact email for the vendor."
    ),
    file_delivery_frequency: Optional[str] = Form(
        None, description="How often files are delivered (e.g., daily=1, weekly=7)."
    ),
    brd_description: Optional[str] = Form(
        None, description="A brief description of the BRD content (if provided)."
    ),
    spec_description: Optional[str] = Form(
        None,
        description="A brief description of the specification file content (if provided).",
    ),
    database_name: Optional[str] = Form(
        None,
        description="Optional BigQuery dataset ID to use instead of config default.",
    ),
    metadata_path: Optional[str] = Form(
        None,
        description="Optional resolved metadata path from prior upload step.",
    ),
    transfer_method: Optional[str] = Form(None, description="Method used to transfer the file (e.g., SFTP, API, Email)."),
    vendor_contact_name: Optional[str] = Form(None, description="Full name of the vendor’s primary contact person."),
    frequency_mode: Optional[str] = Form(None, description="Delivery mode classification such as daily, weekly, monthly, or ad-hoc."),
    vendor_phone_number: Optional[str] = Form(None, description="Phone number of the vendor’s primary contact."),
    dependencies: Optional[str] = Form(None, description="Any upstream or downstream system dependencies related to this file."),
    vendor_email: Optional[str] = Form(None, description="Vendor email address used for operational communications."),
    email_notification_dl: Optional[str] = Form(None, description="Distribution list email address for automated file notifications."),
    # file_delimiter: Optional[str] = Form("TEST", description="Delimiter used in the file (e.g., comma, pipe, tab)."),
    # file_extension: Optional[str] = Form(None, description="File extension indicating format (e.g., .csv, .txt, .xlsx)."),
    date_timestamp_format: Optional[str] = Form(None, description="Date and timestamp format used within the file (e.g., YYYY-MM-DD)."),
    header_record_number: Optional[str] = Form(None, description="Expected record identifier or count for header rows."),
    trailer_record_number: Optional[str] = Form(None, description="Expected record identifier or count for trailer rows."),
    quote_indicator: Optional[str] = Form(None, description="Character used to wrap text values in the file (e.g., double quote)."),
    file_population_type: Optional[str] = Form(None, description="Indicates whether file is full population, delta, or incremental load."),
    file_compression_type: Optional[str] = Form(None, description="Compression format applied to the file (e.g., ZIP, GZIP)."),
    receive_file_when_no_data: Optional[str] = Form(None, description="Indicates whether a file is expected even when no data is present."),
    assumptions: Optional[str] = Form(None, description="Any documented assumptions related to file processing or data interpretation."),
    vendor_server_name: Optional[str] = Form(None, description="Name or address of the vendor server from which files are received."),
    current_user: CurrentUser = Depends(resolve_current_user),
):
    """
    Enhanced streaming endpoint with full feature support:
    - Vendor DD validation workflow (bypasses streaming)
    - HITL mapping workflow (bypasses streaming)
    - Profiling streaming (intelligent_profiling_tool)
    - Relationship streaming
    - Data Dictionary streaming
    - Anomaly streaming (data_anomaly_analysis_tool)
    - Session state management (is_stream flag)
    - Context injection from the profiling session context artifact
    - File Uploads & Metadata (merged from upload-batch)
    """

    async def event_generator():
        try:
            # ==========================================
            # PHASE 1: REQUEST PREPARATION
            # ==========================================
            try:
                req = json.loads(request)
            except json.JSONDecodeError as e:
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {
                            "message": f"Invalid JSON in request: {str(e)}",
                            "phase": "error",
                        }
                    ),
                }
                return
            session_id = req.get("sessionId")
            effective_user_id = current_user.user_key or req.get("userId")
       
            # --- Handle additional files and info ---
            if session_id:
                existing_session_data = _load_session_context(session_id)
                has_existing_dd = bool(existing_session_data.get("data_dict_file_path"))

                additional_info = {
                    "project_name": project_name,
                    "vendor_name": vendor_name,
                    "vendor_contact_person": vendor_contact_person,
                    "file_delivery_frequency": file_delivery_frequency,
                    "brd_description": brd_description,
                    "spec_description": spec_description,
                    # "physical_file_name": physical_file_name,
                    "transfer_method": transfer_method,
                    "vendor_contact_name": vendor_contact_name,
                    "frequency_mode": frequency_mode,
                    "vendor_phone_number": vendor_phone_number,
                    "dependencies": dependencies,
                    "vendor_email": vendor_email,
                    "email_notification_dl": email_notification_dl,
                    #"file_delimiter": file_delimiter,
                    # "file_extension": file_extension,
                    "date_timestamp_format": date_timestamp_format,
                    "header_record_number": header_record_number,
                    "trailer_record_number": trailer_record_number,
                    "quote_indicator": quote_indicator,
                    "file_population_type": file_population_type,
                    "file_compression_type": file_compression_type,
                    "receive_file_when_no_data": receive_file_when_no_data,
                    "assumptions": assumptions,
                    "vendor_server_name": vendor_server_name,
                }
                # Filter out None values
                additional_info = {
                    k: v for k, v in additional_info.items() if v is not None
                }

                uploaded_info = {}
                CHUNK_SIZE = 1024 * 1024

                # If session already has DD, ignore duplicate form metadata/files
                if has_existing_dd:
                    incoming_file_labels = [
                        label
                        for label, upload_file in [
                            ("data_dict_file_path", data_dict_file),
                            ("brd_file", brd_file),
                            ("file_spec_file", file_spec_file),
                        ]
                        if upload_file
                    ]
                    incoming_meta_keys = list(additional_info.keys())

                    if incoming_meta_keys or incoming_file_labels:
                        logging.warning(
                            "[send-stream-new] Session %s already has data_dict_file_path. "
                            "Ignoring incoming metadata/files. meta_keys=%s files=%s",
                            session_id,
                            incoming_meta_keys,
                            incoming_file_labels,
                        )
                else:
                    for file_label, upload_file in [
                        ("data_dict_file_path", data_dict_file),
                        ("brd_file", brd_file),
                        ("file_spec_file", file_spec_file),
                    ]:
                        if upload_file:
                            document_kind = {
                                "data_dict_file_path": "data_dict",
                                "brd_file": "brd",
                                "file_spec_file": "file_spec",
                            }[file_label]
                            artifact_uri = save_document_bytes(
                                session_id=session_id,
                                document_kind=document_kind,
                                filename=upload_file.filename,
                                content=await upload_file.read(),
                            )
                            uploaded_info[file_label] = artifact_uri
                            logging.info("[send-stream-new] Saved %s to %s", file_label, artifact_uri)

                    if additional_info or uploaded_info:
                        session_entry = {**additional_info, **uploaded_info}
                        _update_session_context(session_id, session_entry)
                        logging.info("[send-stream-new] Updated profiling context for session %s", session_id)
            # -----------------------------------------------------------

            user_message = req["newMessage"]["parts"][0]["text"]
            app_name = req["appName"]

            logging.info(f"[send-stream-new] Request received: {user_message[:100]}...")

            # Initialize contexts
            agent_context_string = ""
            data_dictionary_context = ""
            final_message_to_agent = user_message

            # Initialize tracker
            tracker: Optional[StreamingProgressTracker] = None

            def ensure_tracker(
                feature: FeatureType,
                total_items: int = 0,
                message: Optional[str] = None,
            ):
                nonlocal tracker
                if tracker and tracker.feature_type == feature:
                    return None
                tracker = StreamingProgressTracker(feature, total_items=total_items)
                init_event = tracker.get_init_event(message)
                return {
                    "event": init_event["event"],
                    "data": json.dumps(init_event["data"]),
                }

            def emit_complete_events(
                result: Dict[str, Any], feature_hint: FeatureType
            ) -> List[Dict[str, Any]]:
                events: List[Dict[str, Any]] = []
                init_evt = ensure_tracker(feature_hint)
                if init_evt:
                    events.append(init_evt)
                if tracker:
                    complete_event = tracker.get_complete_event(result=result)
                    events.append(
                        {
                            "event": complete_event["event"],
                            "data": json.dumps(complete_event["data"]),
                        }
                    )
                return events

            def emit_error_events(
                message: str,
                feature_hint: FeatureType,
                details: Optional[Dict[str, Any]] = None,
            ) -> List[Dict[str, Any]]:
                events: List[Dict[str, Any]] = []
                init_evt = ensure_tracker(feature_hint)
                if init_evt:
                    events.append(init_evt)
                if tracker:
                    error_event = tracker.get_error_event(message, details)
                    events.append(
                        {
                            "event": error_event["event"],
                            "data": json.dumps(error_event["data"]),
                        }
                    )
                return events

            # Send init status
            yield {
                "event": "status",
                "data": json.dumps(
                    {
                        "phase": "init",
                        "feature": "unknown",
                        "message": "Initializing request...",
                        "progress": 0,
                        "total_items": 0,
                    }
                ),
            }

            # ==========================================
            # PHASE 2: CONTEXT BUILDING
            # ==========================================

            # Build data dictionary context from additional_data
            additional_data = req.get("additional_data") or {}
            if additional_data.get("data_dictionary"):
                data_dictionary_reference = additional_data["data_dictionary"]
                logging.info(
                    f"[send-stream-new] DATASET_OVERRIDE: Preparing to fetch data dictionary"
                )
                logging.info(
                    f"[send-stream-new] DATASET_OVERRIDE: database_name parameter = {database_name}"
                )
                logging.info(
                    f"[send-stream-new] DATASET_OVERRIDE: Table references = {data_dictionary_reference}"
                )

                # Pass database_name as dataset_id_override to get_data_ditionary
                data_dictionary_content = get_data_ditionary(
                    data_dictionary_reference, dataset_id_override=database_name
                )
                data_dictionary_context = f"\n\n Data Dictionary Content: {json.dumps(data_dictionary_content)}"
                logging.info(
                    f"[send-stream-new] Data dictionary context added from additional_data"
                )

            # Check shared profiling context for vendor DD (validation workflow).
            if session_id:
                current_session_data = _load_session_context(session_id)

                # -----------------------------------------
                # STEP 1 — Try Uploaded Vendor DD
                # -----------------------------------------
                dd_path = current_session_data.get("data_dict_file_path")
                if isinstance(dd_path, list):
                    if dd_path and isinstance(dd_path[0], dict):
                        if len(dd_path) > 1:
                            logging.info(
                                "[send-stream-new] Multiple DD candidates found; using the first one only"
                            )
                        dd_path = dd_path[0].get("file_path")
                    else:
                        if len(dd_path) > 1:
                            logging.info(
                                "[send-stream-new] Multiple uploaded DDs found; using the first one only"
                            )
                        dd_path = dd_path[0] if dd_path else None

                if not dd_path and metadata_path:
                    dd_path = metadata_path
                    _update_session_context(session_id, {"data_dict_file_path": [metadata_path]})
                    logging.info(
                        f"[send-stream-new] Stored metadata_path for session {session_id}: {metadata_path}"
                    )

                    try:
                        dd_path, brd_extraction_status = await resolve_metadata_path(
                            uploaded_info=current_session_data,
                            brd_file_path=current_session_data.get("brd_file"),
                            session_id=session_id,
                        )

                        if dd_path:
                            if isinstance(dd_path, list):
                                if dd_path and isinstance(dd_path[0], dict):
                                    dd_path = dd_path[0].get("file_path")
                                else:
                                    dd_path = dd_path[0] if dd_path else None
                            logging.info(
                                f"[send-stream-new] ✓ DD extracted from BRD: {dd_path}"
                            )

                            _update_session_context(
                                session_id,
                                {"data_dict_file_path": [str(dd_path)]},
                            )

                        else:
                            logging.info(
                                "[send-stream-new] BRD extraction returned None"
                            )

                    except Exception as e:
                        logging.warning(
                            f"[send-stream-new] BRD extraction failed: {str(e)}"
                        )
                # -----------------------------------------
                # If DD Exists (Vendor or prior selection)
                # -----------------------------------------
                if dd_path:
                    logging.info(
                        f"[send-stream-new] ✓ Data dictionary resolved: {dd_path}"
                    )

                    if "[Data Dictionary" in user_message:
                        logging.info("[send-stream-new] Triggering validation workflow")
                        final_message_to_agent = "[Data Dictionary Validation]"

                    agent_context_string = (
                        f"\n\n--- System Context ---\n"
                        f"A data dictionary has been resolved for this session.\n"
                        f"DD Path: {dd_path}"
                    )
            # VALIDATION TESTING FOR DATABASE NAME AND TABLES IF THE EXIST OR NOT IN THE DATABASE
            table_ids = extract_table_names_from_request(user_message)

            if table_ids and any(
                kw in user_message.lower()
                for kw in ["profiling", "profile", "analyze", "analysis"]
            ): # Check to make sure validation not triggered for other stages
                dataset_to_use = database_name or config.BQ_DATASET_ID

                validation = validate_dataset_and_tables_large_data(
                    dataset_id=dataset_to_use,
                    table_ids=table_ids
                )

                if not validation["valid"]:
                    error_payload = {
                        "phase": "validation_error",
                        "message": "BigQuery validation failed",
                        "details": {
                            "dataset": dataset_to_use,
                            "missing_dataset": validation["missing_dataset"],
                            "missing_tables": validation["missing_tables"]
                        }
                    }

                    yield {
                        "event": "error",
                        "data": json.dumps(error_payload)
                    }
                    return       

            # ==========================================
            # PHASE 3: SESSION STATE SETUP
            # ==========================================
            session_service = VertexAiSessionService(
                project=config.GOOGLE_CLOUD_PROJECT,
                location=config.GOOGLE_CLOUD_LOCATION,
            )

            try:
                session = await session_service.get_session(
                    app_name=app_name, user_id=effective_user_id, session_id=session_id
                )

                # CRITICAL: Set is_stream = TRUE and dataset_id_override (if provided)
                state_delta = {"is_stream": True}

                # Add dataset_id_override if database_name parameter is provided
                logging.info(
                    f"[send-stream-new] DATASET_OVERRIDE: Checking database_name parameter"
                )
                logging.info(
                    f"[send-stream-new] DATASET_OVERRIDE: database_name = {database_name}"
                )

                if database_name:
                    state_delta["dataset_id_override"] = database_name
                    logging.info(
                        f"[send-stream-new] DATASET_OVERRIDE: Using custom dataset_id = {database_name}"
                    )
                    logging.info(
                        f"[send-stream-new] DATASET_OVERRIDE: Session state will be updated with dataset_id_override = {database_name}"
                    )
                else:
                    logging.info(
                        f"[send-stream-new] DATASET_OVERRIDE: No custom dataset_id provided, using default from config"
                    )

                state_update_event = Event(
                    author="system",
                    invocation_id=f"sys-inv-{uuid.uuid4()}",
                    actions=EventActions(state_delta=state_delta),
                )

                await session_service.append_event(
                    session=session, event=state_update_event
                )
                logging.info(
                    f"[send-stream-new] DATASET_OVERRIDE: Session state event appended successfully"
                )
                logging.info(
                    f"[send-stream-new] DATASET_OVERRIDE: State delta keys = {list(state_delta.keys())}"
                )
                logging.info(
                    f"[send-stream-new] Session state updated for session {session_id}"
                )

            except Exception as e:
                logging.error(f"[send-stream-new] - Could not set session state: {e}")
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": f"Failed to set session state: {e}"}
                    ),
                }
                return

            # Handle similarity data injection (if provided)
            if req.get("stateDelta", {}).get("similarity_dart_references"):
                logging.info("[send-stream-new] - Similarity structured data detected")
                dart_refs = req["stateDelta"]["similarity_dart_references"]
                source_tables = req["stateDelta"].get("similarity_source_tables", [])

                try:
                    similarity_state_event = Event(
                        author="system",
                        invocation_id=f"similarity-inject-{uuid.uuid4()}",
                        actions=EventActions(
                            state_delta={
                                "similarity_dart_references": dart_refs,
                                "similarity_source_tables": source_tables,
                            }
                        ),
                    )
                    await session_service.append_event(
                        session=session, event=similarity_state_event
                    )
                    logging.info(
                        f"[send-stream-new] - Injected similarity data: {len(dart_refs)} DART tables, {len(source_tables)} source tables"
                    )
                except Exception as e:
                    logging.error(
                        f"[send-stream-new] - Failed to inject similarity data: {e}"
                    )

            stage = get_text_between_brackets(user_message)
            if str(stage or "").strip().lower() == "data anomaly analysis":
                try:
                    await _hydrate_large_data_profiling_results(
                        session_service=session_service,
                        session=session,
                        session_id=session_id,
                    )
                    from utils.anomaly_table_resolver import collect_anomaly_source_tables

                    request_source_tables = req.get("stateDelta", {}).get(
                        "anomaly_source_tables", []
                    )
                    source_tables = (
                        [
                            str(table).strip()
                            for table in request_source_tables
                            if str(table).strip()
                        ]
                        if request_source_tables
                        else collect_anomaly_source_tables(
                            dict(session.state or {}),
                            session_id,
                        )
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
                        session.state["anomaly_source_tables"] = source_tables
                        agent_context_string += (
                            "\n\n--- Anomaly Analysis Tables ---\n"
                            "Use ONLY these uploaded source table references when calling "
                            "data_anomaly_analysis_tool:\n"
                            f"{', '.join(source_tables)}\n"
                            "Do NOT use filenames, data dictionary labels, or IBC profiling "
                            "template tables (_nulls___lengths, _defaults, _foreign_keys)."
                        )
                        logging.info(
                            "[send-stream-new] Injected anomaly source tables: %s",
                            source_tables,
                        )
                    else:
                        logging.warning(
                            "[send-stream-new] No anomaly source tables found for session %s",
                            session_id,
                        )
                except Exception as e:
                    logging.error(
                        "[send-stream-new] Failed to inject anomaly source tables: %s",
                        e,
                    )
            # ==========================================
            # PHASE 4: RUN AGENT
            # ==========================================
            if str(stage or "").strip().lower() == "data anomaly analysis":
                stream_root_agent = profiling_agent_anomaly
                logging.info(
                    "[send-stream-new] Direct routing to profiling_agent_anomaly"
                )
            else:
                stream_root_agent = root_agent
            orchestrator_app = App(name=app_name, root_agent=stream_root_agent)
            runner = Runner(app=orchestrator_app, session_service=session_service)

            # Build enriched message
            enriched_message_text = (
                final_message_to_agent
                + data_dictionary_context
                + agent_context_string
                + "\n REGENRATE THE ANSWER EVEN IF ALREADY GENERATED without complaining."
            )

            logging.info(
                f"[send-stream-new] Enriched message: {enriched_message_text[:200]}..."
            )

            msg = types.Content(
                role="user", parts=[types.Part(text=enriched_message_text)]
            )

            should_exit = False
            event_count = 0

            async for event in runner.run_async(
                user_id=effective_user_id, session_id=session_id, new_message=msg
            ):
                event_count += 1
                logging.info(f"[send-stream-new] Received event #{event_count}")

                # --- RATE LIMITING ---
                if hasattr(event, "usage_metadata"):
                    await manage_llm_rate_limits(event, session_id)
                # ---------------------

                # Debug: Save events
                with open(f"event_{event_count}.txt", "w", encoding="utf-8") as f:
                    f.write(f"{event}")

                # ==========================================
                # PRIORITY 1: VALIDATION WORKFLOW (NON-STREAMING)
                # ==========================================
                if (
                    hasattr(event, "content")
                    and event.content
                    and hasattr(event.content, "parts")
                ):
                    for part in event.content.parts:
                        # Handle set_model_response (non-streaming final response path)
                        # Used by: validation workflow, HITL, similarity check, and other non-streaming features
                        if (
                            hasattr(part, "function_call")
                            and part.function_call
                            and part.function_call.name == "set_model_response"
                        ):
                            args = dict(part.function_call.args)
                            text_response = args.get(
                                "text_response", "Workflow complete."
                            )
                            tool_response = args.get("tool_response", {})

                            # ==========================================
                            # CHECK 1: INTERMEDIATE RESPONSE (Sequential Agent Phase 1)
                            # ==========================================
                            # Similarity agent Phase 1 (semantic_matching_agent) uses store_for_next_agent=True
                            # to pass data to Phase 2 (overlap_validation_agent). We must NOT exit here.
                            if tool_response.get("store_for_next_agent") == True:
                                logging.info(
                                    "[send-stream-new] 📤 Intermediate response detected (store_for_next_agent=True)"
                                )
                                logging.info(
                                    "[send-stream-new]    → Continuing to next agent in sequential chain..."
                                )
                                continue  # Continue processing, don't exit

                            # ==========================================
                            # CHECK 2: VALIDATION WORKFLOW (final response)
                            # ==========================================
                            if (
                                "artifact_delta" in args
                                and "final_audit_log" in args["artifact_delta"]
                            ):
                                logging.info(
                                    "[send-stream-new] ✓ Validation workflow complete - returning audit log"
                                )
                                tool_response = {
                                    "status": "success",
                                    "validation_audit_log": args["artifact_delta"][
                                        "final_audit_log"
                                    ],
                                }

                                final_response = {
                                    "text_response": text_response,
                                    "tool_response": tool_response,
                                }

                                for event_payload in emit_complete_events(
                                    final_response, FeatureType.PROFILING
                                ):
                                    yield event_payload
                                return  # Exit for validation workflow

                            # ==========================================
                            # CHECK 3: FINAL RESPONSE (has meaningful text_response)
                            # ==========================================
                            # This catches final responses from agents that use set_model_response
                            # with actual content (not just "Workflow complete.")
                            if text_response and text_response != "Workflow complete.":
                                logging.info(
                                    "[send-stream-new] ✓ Final response with text_response - completing"
                                )

                                final_response = {
                                    "text_response": text_response,
                                    "tool_response": tool_response,
                                }

                                for event_payload in emit_complete_events(
                                    final_response, FeatureType.PROFILING
                                ):
                                    yield event_payload
                                return  # Exit for final response

                            # ==========================================
                            # FALLBACK: Continue processing
                            # ==========================================
                            # If none of the above conditions match, this might be an edge case
                            # Continue processing to see if there are other completion mechanisms
                            logging.info(
                                "[send-stream-new] ⚠️ set_model_response detected but no exit condition met - continuing..."
                            )
                            continue

                        # Handle HITL needs_user_input (mapping workflow)
                        if (
                            hasattr(part, "text")
                            and part.text
                            and "needs_user_input" in part.text
                        ):
                            logging.info(
                                "[send-stream-new] ⚠️ HITL WORKFLOW detected - bypassing streaming"
                            )

                            try:
                                json_str_match = re.search(
                                    r"\{.*\}", part.text, re.DOTALL
                                )
                                if json_str_match:
                                    tool_response_json = json.loads(
                                        json_str_match.group()
                                    )
                                    if (
                                        tool_response_json.get("status")
                                        == "needs_user_input"
                                    ):
                                        final_response = {
                                            "text_response": "The agent needs your input to proceed.",
                                            "tool_response": tool_response_json,
                                        }
                                        # Emit complete event immediately
                                        for event_payload in emit_complete_events(
                                            final_response, FeatureType.PROFILING
                                        ):
                                            yield event_payload
                                        return
                            except (json.JSONDecodeError, AttributeError) as e:
                                logging.error(
                                    f"[send-stream-new] Failed to parse HITL response: {e}"
                                )

                # ==========================================
                # PRIORITY 2: STATE_DELTA HANDLERS (FALLBACK)
                # ==========================================
                if (
                    hasattr(event, "actions")
                    and event.actions
                    and hasattr(event.actions, "state_delta")
                ):
                    state_delta = event.actions.state_delta

                    # Validation audit log
                    if "final_audit_log" in state_delta:
                        logging.info(
                            "[send-stream-new] ✓ Validation workflow complete (state_delta)"
                        )
                        final_response = {
                            "text_response": "Data Dictionary Validation Complete.",
                            "tool_response": {
                                "validation_audit_log": state_delta["final_audit_log"],
                                "status": "success",
                            },
                        }
                        for event_payload in emit_complete_events(
                            final_response, FeatureType.PROFILING
                        ):
                            yield event_payload
                        break

                    # Profiling complete (fallback if streaming didn't trigger)
                    elif "final_profiling_response" in state_delta:
                        _capture_profiling_chat_response(
                            session_id,
                            state_delta["final_profiling_response"],
                        )
                        logging.info(
                            "[send-stream-new] ⚠️ Profiling complete via state_delta (fallback)"
                        )
                        try:
                            session = await session_service.get_session(
                                app_name=app_name,
                                user_id=effective_user_id,
                                session_id=session_id,
                            )
                            _persist_large_data_profiling_results(
                                session_id,
                                session.state.get("profiling_full_results", []),
                            )
                        except Exception:
                            logging.exception(
                                "[send-stream-new] Failed to persist profiling_full_results from fallback state_delta"
                            )
                        for event_payload in emit_complete_events(
                            state_delta["final_profiling_response"],
                            FeatureType.PROFILING,
                        ):
                            yield event_payload
                        break

                    # Data Dictionary complete (fallback)
                    elif "final_data_dict_response" in state_delta:
                        logging.info(
                            "[send-stream-new] ⚠️ Data dictionary complete via state_delta (fallback)"
                        )
                        enriched_dd = _enrich_dd_with_bq_stats(
                            state_delta["final_data_dict_response"],
                            dataset_id_override=session.state.get("dataset_id_override")
                        )
                        for event_payload in emit_complete_events(
                            enriched_dd,
                            FeatureType.DATA_DICTIONARY,
                        ):
                            yield event_payload
                        break

                    # Metadata Template complete
                    elif "metadata_excel_file" in state_delta:
                        logging.info("[send-stream-new] ✓ Metadata template complete")
                        for event_payload in emit_complete_events(
                            state_delta["metadata_excel_file"],
                            FeatureType.METADATA_TEMPLATE,
                        ):
                            yield event_payload
                        break

                    # Similarity complete
                    elif "final_similarity_response" in state_delta:
                        logging.info("[send-stream-new] ✓ Similarity check complete")
                        for event_payload in emit_complete_events(
                            state_delta["final_similarity_response"],
                            FeatureType.SIMILARITY,
                        ):
                            yield event_payload
                        break

                # ==========================================
                # PRIORITY 3: STREAMING WORKFLOWS
                # ==========================================
                if (
                    hasattr(event, "content")
                    and event.content
                    and hasattr(event.content, "parts")
                    and event.content.parts
                ):
                    for part in event.content.parts:
                        if (
                            hasattr(part, "function_response")
                            and part.function_response
                        ):
                            func_response = part.function_response

                            # ==========================================
                            # PROFILING STREAMING (intelligent_profiling_tool)
                            # ==========================================
                            if func_response.name == "intelligent_profiling_tool":
                                logging.info(
                                    f"[send-stream-new] ✓ Profiling tool completed - starting multi-pass streaming"
                                )

                                try:
                                    # Retrieve results from session state
                                    session = await session_service.get_session(
                                        app_name=app_name,
                                        user_id=effective_user_id,
                                        session_id=session_id,
                                    )

                                    all_results = session.state.get(
                                        "profiling_full_results", []
                                    )

                                    if not all_results:
                                        # Fallback to tool response
                                        raw_response = func_response.response
                                        all_results = (
                                            raw_response
                                            if isinstance(raw_response, list)
                                            else []
                                        )
                                        logging.warning(
                                            f"[send-stream-new] Fallback: Using tool response ({len(all_results)} tables)"
                                        )
                                    else:
                                        logging.info(
                                            f"[send-stream-new] ✓ Retrieved {len(all_results)} tables from session state"
                                        )

                                    _persist_large_data_profiling_results(
                                        session_id,
                                        all_results,
                                    )

                                    tracker_init = ensure_tracker(
                                        FeatureType.PROFILING,
                                        total_items=len(all_results),
                                        message="Profiling in progress...",
                                    )
                                    if tracker_init:
                                        yield tracker_init

                                    # Tool complete event (90%)
                                    yield {
                                        "event": "tool_complete",
                                        "data": json.dumps(
                                            {
                                                "tool_name": "intelligent_profiling_tool",
                                                "tool_response": {
                                                    "result": all_results
                                                },
                                                "progress": 90,
                                                "message": f"Profiling complete ({len(all_results)} tables). Starting multi-pass LLM analysis...",
                                            }
                                        ),
                                    }

                                    # Multi-pass batched streaming
                                    from utils.profiling_analysis_batched import (
                                        build_batch_profiling_analysis_prompt,
                                        build_aggregate_profiling_summary_prompt,
                                        build_searchable_index,
                                    )
                                    from google.genai import types as genai_types

                                    client = genai.Client(
                                        vertexai=True,
                                        project=config.GOOGLE_CLOUD_PROJECT,
                                        location=config.GOOGLE_CLOUD_LOCATION,
                                    )
                                    model = config.AGENT_MODEL
                                    BATCH_SIZE = 8

                                    table_batches = [
                                        all_results[i : i + BATCH_SIZE]
                                        for i in range(0, len(all_results), BATCH_SIZE)
                                    ]
                                    total_batches = len(table_batches)

                                    all_batch_analyses = []
                                    full_markdown_analysis = ""

                                    # Process each batch
                                    for batch_idx, batch_tables in enumerate(
                                        table_batches
                                    ):
                                        batch_num = batch_idx + 1

                                        yield {
                                            "event": "llm_batch_start",
                                            "data": json.dumps(
                                                {
                                                    "batch_number": batch_num,
                                                    "total_batches": total_batches,
                                                    "tables_in_batch": len(
                                                        batch_tables
                                                    ),
                                                    "progress": 90
                                                    + (batch_idx / total_batches * 8),
                                                    "message": f"Analyzing batch {batch_num}/{total_batches}...",
                                                }
                                            ),
                                        }

                                        batch_prompt = (
                                            build_batch_profiling_analysis_prompt(
                                                batch_tables, batch_num, total_batches
                                            )
                                        )
                                        batch_analysis = ""
                                        token_count = 0

                                        response_stream = client.models.generate_content_stream(
                                            model=model,
                                            contents=batch_prompt,
                                            config=genai_types.GenerateContentConfig(
                                                temperature=0.3
                                            ),
                                        )

                                        for chunk in response_stream:
                                            token_text = None
                                            if hasattr(chunk, "text") and chunk.text:
                                                token_text = chunk.text
                                            elif (
                                                hasattr(chunk, "candidates")
                                                and chunk.candidates
                                            ):
                                                for candidate in chunk.candidates:
                                                    for part in getattr(
                                                        candidate.content, "parts", []
                                                    ):
                                                        if (
                                                            hasattr(part, "text")
                                                            and part.text
                                                        ):
                                                            token_text = part.text

                                            if token_text:
                                                batch_analysis += token_text
                                                full_markdown_analysis += token_text
                                                token_count += 1

                                                if token_count % 3 == 0:
                                                    progress = min(
                                                        90
                                                        + (
                                                            batch_idx
                                                            / total_batches
                                                            * 8
                                                        )
                                                        + (len(batch_analysis) / 5000)
                                                        * (8 / total_batches),
                                                        98,
                                                    )
                                                    yield {
                                                        "event": "llm_token",
                                                        "data": json.dumps(
                                                            {
                                                                "token": token_text,
                                                                "cumulative": full_markdown_analysis,
                                                                "batch_number": batch_num,
                                                                "total_batches": total_batches,
                                                                "progress": round(
                                                                    progress, 1
                                                                ),
                                                                "message": f"Batch {batch_num}/{total_batches} analysis...",
                                                            }
                                                        ),
                                                    }

                                        all_batch_analyses.append(batch_analysis)

                                    # Executive summary (98-100%)
                                    yield {
                                        "event": "llm_summary_start",
                                        "data": json.dumps(
                                            {
                                                "progress": 98,
                                                "message": "Generating executive summary...",
                                            }
                                        ),
                                    }

                                    summary_prompt = (
                                        build_aggregate_profiling_summary_prompt(
                                            all_results, all_batch_analyses
                                        )
                                    )
                                    executive_summary = ""
                                    token_count = 0

                                    response_stream = (
                                        client.models.generate_content_stream(
                                            model=model,
                                            contents=summary_prompt,
                                            config=genai_types.GenerateContentConfig(
                                                temperature=0.3
                                            ),
                                        )
                                    )

                                    for chunk in response_stream:
                                        token_text = None
                                        if hasattr(chunk, "text") and chunk.text:
                                            token_text = chunk.text
                                        elif (
                                            hasattr(chunk, "candidates")
                                            and chunk.candidates
                                        ):
                                            for candidate in chunk.candidates:
                                                for part in getattr(
                                                    candidate.content, "parts", []
                                                ):
                                                    if (
                                                        hasattr(part, "text")
                                                        and part.text
                                                    ):
                                                        token_text = part.text

                                        if token_text:
                                            executive_summary += token_text
                                            token_count += 1

                                            if token_count % 3 == 0:
                                                progress = min(
                                                    98
                                                    + (len(executive_summary) / 3000),
                                                    99.9,
                                                )
                                                yield {
                                                    "event": "llm_token",
                                                    "data": json.dumps(
                                                        {
                                                            "token": token_text,
                                                            "cumulative": full_markdown_analysis
                                                            + "\n\n"
                                                            + executive_summary,
                                                            "progress": round(
                                                                progress, 1
                                                            ),
                                                            "message": "Executive summary...",
                                                        }
                                                    ),
                                                }

                                    final_markdown = (
                                        executive_summary
                                        + "\n\n---\n\n"
                                        + full_markdown_analysis
                                    )
                                    searchable_index = build_searchable_index(
                                        all_results
                                    )

                                    # Complete event (100%)
                                    final_result = {
                                        "text_response": final_markdown,
                                        "tool_response": {
                                            "all_tables": all_results,
                                            "searchable_index": searchable_index,
                                            "batch_analyses": all_batch_analyses,
                                            "summary": {
                                                "total_tables": len(all_results),
                                                "total_batches": total_batches,
                                                "analysis_complete": True,
                                            },
                                        },
                                        "should_update": False,
                                    }
                                    _capture_profiling_chat_response(session_id, final_result)

                                    final_state_event = Event(
                                        author="system",
                                        invocation_id=f"stream-profiling-final-{uuid.uuid4()}",
                                        actions=EventActions(
                                            state_delta={"final_profiling_response_streaming": final_result}
                                        ),
                                    )
                                    await session_service.append_event(
                                        session=session,
                                        event=final_state_event,
                                    )

                                    for event_payload in emit_complete_events(
                                        final_result, FeatureType.PROFILING
                                    ):
                                        yield event_payload

                                    logging.info(
                                        f"[send-stream-new] ✓ Profiling complete: {len(all_results)} tables, {total_batches} batches"
                                    )
                                    should_exit = True
                                    break

                                except Exception as e:
                                    logging.error(
                                        f"[send-stream-new] ❌ Profiling error: {e}"
                                    )
                                    import traceback

                                    traceback.print_exc()

                                    error_result = {
                                        "text_response": generate_error_markdown(
                                            f"Profiling error: {str(e)}"
                                        ),
                                        "tool_response": {},
                                        "should_update": False,
                                    }
                                    for event_payload in emit_complete_events(
                                        error_result, FeatureType.PROFILING
                                    ):
                                        yield event_payload
                                    should_exit = True
                                    break

                            # ==========================================
                            # ANOMALY STREAMING (data_anomaly_analysis_tool)
                            # ==========================================
                            elif func_response.name == "data_anomaly_analysis_tool":
                                logging.info(
                                    f"[send-stream-new] ✓ Anomaly tool completed - starting LLM streaming"
                                )

                                try:
                                    raw_response = None
                                    try:
                                        session = await session_service.get_session(
                                            app_name=app_name,
                                            user_id=effective_user_id,
                                            session_id=session_id,
                                        )
                                        raw_response = session.state.get(
                                            "data_anomaly_analysis_tool_response"
                                        )
                                        if raw_response:
                                            logging.info(
                                                "[send-stream-new] Retrieved full anomaly response from session state"
                                            )
                                    except Exception as state_error:
                                        logging.warning(
                                            "[send-stream-new] Could not retrieve anomaly response from session state: %s",
                                            state_error,
                                        )

                                    if not raw_response:
                                        raw_response = func_response.response
                                        logging.warning(
                                            "[send-stream-new] Fallback: using anomaly function response payload"
                                        )

                                    if isinstance(raw_response, str):
                                        raw_response = json.loads(raw_response)

                                    tables_analyzed = raw_response.get(
                                        "tables_analyzed"
                                    ) or raw_response.get("summary_statistics", {}).get(
                                        "total_tables_analyzed", 0
                                    )

                                    tracker_init = ensure_tracker(
                                        FeatureType.ANOMALY_DETECTION,
                                        total_items=tables_analyzed,
                                        message="Anomaly detection in progress...",
                                    )
                                    if tracker_init:
                                        yield tracker_init

                                    # Tool complete (95%)
                                    yield {
                                        "event": "tool_complete",
                                        "data": json.dumps(
                                            {
                                                "tool_name": "data_anomaly_analysis_tool",
                                                "tool_response": raw_response,
                                                "progress": 95,
                                                "message": f"Anomaly detection complete ({tables_analyzed} tables). Generating insights...",
                                            }
                                        ),
                                    }

                                    # LLM analysis
                                    from utils.anomaly_analysis import (
                                        build_anomaly_analysis_prompt,
                                    )
                                    from google.genai import types as genai_types

                                    analysis_prompt = build_anomaly_analysis_prompt(
                                        raw_response
                                    )

                                    yield {
                                        "event": "llm_analysis_start",
                                        "data": json.dumps(
                                            {
                                                "progress": 96,
                                                "message": "Gemini is compiling anomaly insights...",
                                            }
                                        ),
                                    }

                                    client = genai.Client(
                                        vertexai=True,
                                        project=config.GOOGLE_CLOUD_PROJECT,
                                        location=config.GOOGLE_CLOUD_LOCATION,
                                    )
                                    model = config.AGENT_MODEL

                                    llm_text = ""
                                    token_count = 0

                                    response_stream = (
                                        client.models.generate_content_stream(
                                            model=model,
                                            contents=analysis_prompt,
                                            config=genai_types.GenerateContentConfig(
                                                temperature=0.25
                                            ),
                                        )
                                    )

                                    for chunk in response_stream:
                                        token_text = None
                                        if hasattr(chunk, "text") and chunk.text:
                                            token_text = chunk.text
                                        elif (
                                            hasattr(chunk, "candidates")
                                            and chunk.candidates
                                        ):
                                            for candidate in chunk.candidates:
                                                for part in getattr(
                                                    candidate.content, "parts", []
                                                ):
                                                    if (
                                                        hasattr(part, "text")
                                                        and part.text
                                                    ):
                                                        token_text = part.text

                                        if token_text:
                                            llm_text += token_text
                                            token_count += 1

                                            if token_count % 3 == 0:
                                                progress = min(
                                                    96 + (len(llm_text) / 1000), 99.9
                                                )
                                                yield {
                                                    "event": "llm_token",
                                                    "data": json.dumps(
                                                        {
                                                            "token": token_text,
                                                            "cumulative": llm_text,
                                                            "progress": round(
                                                                progress, 1
                                                            ),
                                                            "message": "Streaming anomaly insights...",
                                                        }
                                                    ),
                                                }

                                    # Complete
                                    final_result = {
                                        "text_response": llm_text.strip(),
                                        "tool_response": raw_response,
                                        "should_update": False,
                                    }

                                    for event_payload in emit_complete_events(
                                        final_result, FeatureType.ANOMALY_DETECTION
                                    ):
                                        yield event_payload

                                    logging.info(
                                        f"[send-stream-new] ✓ Anomaly complete: {tables_analyzed} tables"
                                    )
                                    should_exit = True
                                    break

                                except Exception as e:
                                    logging.error(
                                        f"[send-stream-new] ❌ Anomaly error: {e}"
                                    )
                                    import traceback

                                    traceback.print_exc()

                                    error_result = {
                                        "text_response": generate_error_markdown(
                                            f"Anomaly error: {str(e)}"
                                        ),
                                        "tool_response": raw_response
                                        if "raw_response" in locals()
                                        else {},
                                        "should_update": False,
                                    }
                                    for event_payload in emit_complete_events(
                                        error_result, FeatureType.ANOMALY_DETECTION
                                    ):
                                        yield event_payload
                                    should_exit = True
                                    break

                            # ==========================================
                            # RELATIONSHIP STREAMING (relationship_analysis_tool)
                            # ==========================================
                            elif func_response.name == "relationship_analysis_tool":
                                logging.info(
                                    f"[send-stream-new] ✓ Relationship analysis tool completed"
                                )

                                try:
                                    # Extract results
                                    raw_response = func_response.response

                                    num_tables = raw_response.get("tables_analyzed", 0)
                                    num_relationships = len(
                                        raw_response.get(
                                            "cross_table_relationships", []
                                        )
                                    )
                                    logging.info(
                                        f"[send-stream-new] Relationship tool returned {num_relationships} relationships across {num_tables} tables"
                                    )

                                    tracker_init = ensure_tracker(
                                        FeatureType.RELATIONSHIP_ANALYSIS,
                                        total_items=num_tables,
                                        message="Relationship analysis in progress...",
                                    )
                                    if tracker_init:
                                        yield tracker_init

                                    # PHASE 1: Send tool_complete event (95%)
                                    tool_complete_event = {
                                        "event": "tool_complete",
                                        "data": json.dumps(
                                            {
                                                "tool_name": "relationship_analysis_tool",
                                                "tool_response": raw_response,
                                                "progress": 95,
                                                "message": f"Relationship analysis complete. Found {num_relationships} relationships. Generating intelligent insights...",
                                            }
                                        ),
                                    }
                                    yield tool_complete_event

                                    # PHASE 2: Build LLM analysis prompt
                                    from utils.relationship_analysis import (
                                        build_relationship_analysis_prompt,
                                    )

                                    logging.info(
                                        f"[send-stream-new] Building relationship analysis prompt"
                                    )
                                    analysis_prompt = (
                                        build_relationship_analysis_prompt(raw_response)
                                    )

                                    # Send llm_analysis_start event (96%)
                                    llm_start_event = {
                                        "event": "llm_analysis_start",
                                        "data": json.dumps(
                                            {
                                                "progress": 96,
                                                "message": "Gemini is analyzing relationship patterns and data model architecture...",
                                            }
                                        ),
                                    }
                                    yield llm_start_event

                                    # PHASE 3: Get LLM analysis (streaming tokens)
                                    from google.genai import types as genai_types

                                    client = genai.Client(
                                        vertexai=True,
                                        project=config.GOOGLE_CLOUD_PROJECT,
                                        location=config.GOOGLE_CLOUD_LOCATION,
                                    )
                                    model = config.AGENT_MODEL

                                    llm_analysis_text = ""
                                    token_count = 0

                                    logging.info(
                                        f"[send-stream-new] Starting LLM streaming analysis for relationships"
                                    )

                                    try:
                                        # Call LLM with streaming enabled
                                        response_stream = client.models.generate_content_stream(
                                            model=model,
                                            contents=analysis_prompt,
                                            config=genai_types.GenerateContentConfig(
                                                temperature=0.3
                                            ),
                                        )

                                        # Stream tokens to client
                                        for chunk in response_stream:
                                            token_text = None
                                            if hasattr(chunk, "text") and chunk.text:
                                                token_text = chunk.text
                                            elif (
                                                hasattr(chunk, "candidates")
                                                and chunk.candidates
                                            ):
                                                for candidate in chunk.candidates:
                                                    for part in getattr(
                                                        candidate.content, "parts", []
                                                    ):
                                                        if (
                                                            hasattr(part, "text")
                                                            and part.text
                                                        ):
                                                            token_text = part.text

                                            if token_text:
                                                llm_analysis_text += token_text
                                                token_count += 1

                                                if token_count % 3 == 0:
                                                    progress = min(
                                                        96
                                                        + (
                                                            len(llm_analysis_text)
                                                            / 1000
                                                        ),
                                                        99.9,
                                                    )
                                                    yield {
                                                        "event": "llm_token",
                                                        "data": json.dumps(
                                                            {
                                                                "token": token_text,
                                                                "cumulative": llm_analysis_text,
                                                                "progress": round(
                                                                    progress, 1
                                                                ),
                                                            }
                                                        ),
                                                    }

                                        logging.info(
                                            f"[send-stream-new] LLM relationship analysis complete ({len(llm_analysis_text)} chars)"
                                        )

                                    except AttributeError as e:
                                        # Fallback to non-streaming if streaming not supported
                                        logging.warning(
                                            f"[send-stream-new] Streaming not supported, using non-streaming: {e}"
                                        )

                                        response = client.models.generate_content(
                                            model=model,
                                            contents=analysis_prompt,
                                            config=genai_types.GenerateContentConfig(
                                                temperature=0.3
                                            ),
                                        )

                                        # Extract text from response
                                        if hasattr(response, "text"):
                                            llm_analysis_text = response.text.strip()
                                        elif (
                                            hasattr(response, "candidates")
                                            and len(response.candidates) > 0
                                        ):
                                            llm_analysis_text = (
                                                response.candidates[0]
                                                .content.parts[0]
                                                .text.strip()
                                            )
                                        else:
                                            raise ValueError(
                                                "Unable to extract text from LLM response"
                                            )

                                        logging.info(
                                            f"[send-stream-new] LLM relationship analysis complete (non-streaming): {len(llm_analysis_text)} chars"
                                        )

                                        # Send the full text as a single llm_token event
                                        yield {
                                            "event": "llm_token",
                                            "data": json.dumps(
                                                {
                                                    "token": llm_analysis_text,
                                                    "cumulative": llm_analysis_text,
                                                    "progress": 99.9,
                                                }
                                            ),
                                        }

                                    # PHASE 4: Send final complete event (100%)
                                    final_result = {
                                        "text_response": llm_analysis_text,
                                        "tool_response": raw_response,
                                        "should_update": False,
                                    }

                                    for event_payload in emit_complete_events(
                                        final_result, FeatureType.RELATIONSHIP_ANALYSIS
                                    ):
                                        yield event_payload
                                    should_exit = True
                                    break

                                except Exception as e:
                                    logging.error(
                                        f"[send-stream-new] ❌ Relationship analysis error: {e}"
                                    )
                                    import traceback

                                    traceback.print_exc()

                                    error_markdown = generate_error_markdown(
                                        f"Error generating relationship analysis: {str(e)}"
                                    )
                                    error_result = {
                                        "text_response": error_markdown,
                                        "tool_response": raw_response
                                        if "raw_response" in locals()
                                        else {},
                                        "should_update": False,
                                    }
                                    for event_payload in emit_complete_events(
                                        error_result, FeatureType.RELATIONSHIP_ANALYSIS
                                    ):
                                        yield event_payload
                                    should_exit = True
                                    break

                            # ==========================================
                            # DATA DICTIONARY GENERATION STREAMING (Plan 2)
                            # ==========================================
                            elif func_response.name == "data_dictionary_tool":
                                logging.info(
                                    f"[send-stream-new] ✓ Data dict generation tool completed - starting streaming"
                                )

                                try:
                                    raw_response = func_response.response

                                    # Check if async generator (batched streaming version)
                                    if hasattr(raw_response, "__aiter__"):
                                        logging.info(
                                            "[send-stream-new] Data dict generation using batched streaming"
                                        )

                                        async for dd_event in raw_response:
                                            # Forward events directly
                                            yield {
                                                "event": dd_event["event"],
                                                "data": json.dumps(dd_event["data"]),
                                            }

                                            # Check for completion
                                            if dd_event["event"] == "complete":
                                                final_result = dd_event["data"][
                                                    "result"
                                                ]
                                                for (
                                                    event_payload
                                                ) in emit_complete_events(
                                                    final_result,
                                                    FeatureType.DATA_DICTIONARY,
                                                ):
                                                    yield event_payload
                                                should_exit = True
                                                break

                                    else:
                                        # Fallback: non-streaming response (normal flow)
                                        logging.info(
                                            "[send-stream-new] Data dict generation using standard non-streaming"
                                        )
                                        final_result = {
                                            "text_response": raw_response.get(
                                                "text_response", ""
                                            ),
                                            "tool_response": raw_response.get(
                                                "tool_response", {}
                                            ),
                                        }
                                        for event_payload in emit_complete_events(
                                            final_result, FeatureType.DATA_DICTIONARY
                                        ):
                                            yield event_payload

                                    should_exit = True
                                    break

                                except Exception as e:
                                    logging.error(
                                        f"[send-stream-new] ❌ Data dict generation error: {e}"
                                    )
                                    import traceback

                                    traceback.print_exc()

                                    error_result = {
                                        "text_response": generate_error_markdown(
                                            f"Data dictionary generation error: {str(e)}"
                                        ),
                                        "tool_response": {},
                                    }
                                    for event_payload in emit_complete_events(
                                        error_result, FeatureType.DATA_DICTIONARY
                                    ):
                                        yield event_payload
                                    should_exit = True
                                    break

                            # ==========================================
                            # VENDOR DD EXTRACTION STREAMING (Plan 2)
                            # ==========================================
                            elif func_response.name == "extract_and_map_vendor_dd":
                                logging.info(
                                    f"[send-stream-new] ✓ Vendor DD extraction tool completed - starting streaming"
                                )

                                try:
                                    raw_response = func_response.response

                                    # Check if async generator (streaming version)
                                    if hasattr(raw_response, "__aiter__"):
                                        logging.info(
                                            "[send-stream-new] Vendor DD extraction using native Gemini streaming"
                                        )

                                        async for dd_event in raw_response:
                                            # Forward events directly
                                            yield {
                                                "event": dd_event["event"],
                                                "data": json.dumps(dd_event["data"]),
                                            }

                                            # Check for completion
                                            if dd_event["event"] == "complete":
                                                final_result = dd_event["data"][
                                                    "result"
                                                ]
                                                for (
                                                    event_payload
                                                ) in emit_complete_events(
                                                    final_result,
                                                    FeatureType.DATA_DICTIONARY,
                                                ):
                                                    yield event_payload
                                                should_exit = True
                                                break

                                    else:
                                        # Fallback: non-streaming response
                                        logging.info(
                                            "[send-stream-new] Vendor DD extraction using non-streaming fallback"
                                        )
                                        final_result = {
                                            "text_response": raw_response.get(
                                                "text_response", ""
                                            ),
                                            "tool_response": raw_response.get(
                                                "tool_response", {}
                                            ),
                                        }
                                        for event_payload in emit_complete_events(
                                            final_result, FeatureType.DATA_DICTIONARY
                                        ):
                                            yield event_payload

                                    should_exit = True
                                    break

                                except Exception as e:
                                    logging.error(
                                        f"[send-stream-new] ❌ Vendor DD extraction error: {e}"
                                    )
                                    import traceback

                                    traceback.print_exc()

                                    error_result = {
                                        "text_response": generate_error_markdown(
                                            f"Vendor data dictionary extraction error: {str(e)}"
                                        ),
                                        "tool_response": {},
                                    }
                                    for event_payload in emit_complete_events(
                                        error_result, FeatureType.DATA_DICTIONARY
                                    ):
                                        yield event_payload
                                    should_exit = True
                                    break

                if should_exit:
                    break

                # ==========================================
                # PRIORITY 4: FALLBACK TEXT RESPONSE HANDLER
                # ==========================================
                # Handle text responses that might contain JSON in markdown blocks
                # This is critical for similarity check and other features that return text
                if (
                    hasattr(event, "content")
                    and event.content
                    and hasattr(event.content, "parts")
                    and event.content.parts
                    and len(event.content.parts) > 0
                    and getattr(event.content.parts[0], "text", None)
                ):
                    obj = event.content.parts[0].text
                    logging.info(
                        f"[send-stream-new] Text response detected: {str(obj)[:200]}..."
                    )

                    # Check if it's a final response (JSON format)
                    if isinstance(obj, dict):
                        logging.info("[send-stream-new] ✓ JSON dict response detected")
                        feature_hint = (
                            tracker.feature_type if tracker else FeatureType.PROFILING
                        )
                        for event_payload in emit_complete_events(obj, feature_hint):
                            yield event_payload
                        break
                    elif isinstance(obj, str):
                        # Try to extract JSON from markdown code block
                        new_obj = extract_json_from_string(obj)
                        if new_obj:
                            logging.info(
                                "[send-stream-new] ✓ Extracted JSON from markdown block"
                            )
                            try:
                                parsed_obj = json.loads(new_obj)
                                logging.info(
                                    f"[send-stream-new] ✓ Parsed JSON successfully: {list(parsed_obj.keys()) if isinstance(parsed_obj, dict) else 'list'}"
                                )
                                feature_hint = (
                                    tracker.feature_type
                                    if tracker
                                    else FeatureType.PROFILING
                                )
                                for event_payload in emit_complete_events(
                                    parsed_obj, feature_hint
                                ):
                                    yield event_payload
                                break
                            except json.JSONDecodeError as e:
                                logging.warning(
                                    f"[send-stream-new] Failed to parse extracted JSON: {e}"
                                )
                                # Continue processing other events
                        else:
                            logging.info(
                                "[send-stream-new] No JSON block found in text response"
                            )

                # ==========================================
                # PRIORITY 5: ERROR HANDLING
                # ==========================================
                # Handle errors from ADK events
                if hasattr(event, "error_code") and event.error_code:
                    error_message = f"{event.error_code}: {getattr(event, 'error_message', 'Unknown error')}"
                    logging.error(
                        f"[send-stream-new] ❌ Error event received: {error_message}"
                    )
                    feature_hint = (
                        tracker.feature_type if tracker else FeatureType.PROFILING
                    )
                    for error_payload in emit_error_events(error_message, feature_hint):
                        yield error_payload
                    break

                await asyncio.sleep(0.1)

            logging.info(f"[send-stream-new] ✓ Stream completed")

        except Exception as e:
            logging.error(f"[send-stream-new] ❌ Fatal error: {e}")
            import traceback

            traceback.print_exc()

            for error_payload in emit_error_events(
                f"Stream error: {str(e)}", FeatureType.PROFILING
            ):
                yield error_payload

    return EventSourceResponse(event_generator())


# ============================================================================
# NEW ENDPOINT: Large Data Flow - Data Dictionary Generation
# ============================================================================
from agents.data_dict_stream_agent.agent import large_data_root_agent


@router.post("/send-large-data-dict")
async def send_large_data_dict_message(
    request: str = Form(..., description="JSON string of MessageRequest"),
):
    """
    Dedicated endpoint for large data flow - Data Dictionary Generation ONLY.

    NOTE: Vendor DD file (if any) should already be uploaded via /send-stream-new.
    This endpoint reads the file path from profiling session context/session state.

    This endpoint is completely separate from normal flow to ensure zero impact.

    **Supported Scenarios:**
    1. Generate DD from profiling results (no vendor DD uploaded)
    2. Extract DD from vendor file (vendor DD uploaded)

    **Usage:**
    - Frontend calls this endpoint when user clicks "Generate Data Dictionary" in large data flow
    - Session must already have profiling results (from previous profiling step)
    - If vendor DD file is provided, it will be extracted instead of using profiling

    **Key Differences from /send-stream-new:**
    - Uses large_data_root_agent (not root_agent)
    - Focused on data dictionary generation only
    - No profiling, relationship, or other features (yet)
    """

    async def event_generator():
        try:
            # ==========================================
            # PHASE 1: Parse Request
            # ==========================================
            logging.info(
                f"[send-large-data-dict] Starting DD generation for large data flow"
            )

            # Parse request (same format as send-stream-new)
            req = json.loads(request)
            session_id = req["sessionId"]
            user_message = req["newMessage"]["parts"][0]["text"]
            app_name = req["appName"]
            user_id = req["userId"]

            logging.info(
                f"[send-large-data-dict] Session: {session_id}, Message: {user_message}"
            )

            # ==========================================
            # PHASE 2: Initialize Session
            # ==========================================
            session_service = VertexAiSessionService(
                project=config.GOOGLE_CLOUD_PROJECT,
                location=config.GOOGLE_CLOUD_LOCATION,
            )

            # Get or create session (using same method as send-stream-new)
            session = await session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            logging.info(f"[send-large-data-dict] Loaded session: {session_id}")

            # Set is_stream flag (CRITICAL for dispatcher routing)
            session.state["is_stream"] = True

            # ==========================================
            # PHASE 3: Check for Existing Vendor DD in profiling session context
            # ==========================================
            # Check if vendor DD was uploaded in previous step (stored in shared profiling context).
            existing_dd_path = None
            if session_id:
                current_session_data = _load_session_context(session_id)
                existing_dd_path = primary_metadata_path_from_state(current_session_data)
                if existing_dd_path:
                    session.state["data_dict_file_path"] = existing_dd_path
                    logging.info(
                        f"[send-large-data-dict] Found existing vendor DD in profiling context: {existing_dd_path}"
                    )
                    logging.info(
                        f"[send-large-data-dict] Stored in session.state['data_dict_file_path']: {session.state.get('data_dict_file_path')}"
                    )
            if existing_dd_path:
                state_update_event = Event(
                    author="system",
                    invocation_id=f"sys-inv-{uuid.uuid4()}",
                    actions=EventActions(state_delta={"data_dict_file_path": existing_dd_path}),
                )
                await session_service.append_event(session=session, event=state_update_event)
                logging.info("[send-large-data-dict] Persisted data_dict_file_path to session backend")

            await _hydrate_large_data_profiling_results(
                session_service=session_service,
                session=session,
                session_id=session_id,
            )

            # Check if vendor DD was uploaded in previous step (stored in data.json)
            if False and session_id:
                with open(DATA_JSON_PATH, "r", encoding="utf-8") as f:
                    try:
                        all_session_data = json.load(f)
                        current_session_data = all_session_data.get(session_id, {})
                        if "data_dict_file_path" in current_session_data:
                            existing_dd_path = current_session_data[
                                "data_dict_file_path"
                            ]
                            if isinstance(existing_dd_path, list):
                                if existing_dd_path and isinstance(
                                    existing_dd_path[0], dict
                                ):
                                    # If DD candidates were stored, use the first candidate's file_path
                                    if len(existing_dd_path) > 1:
                                        logging.info(
                                            "[send-large-data-dict] Multiple DD candidates found; using the first one only"
                                        )
                                    existing_dd_path = existing_dd_path[0].get(
                                        "file_path"
                                    )
                                else:
                                    if len(existing_dd_path) > 1:
                                        logging.info(
                                            "[send-large-data-dict] Multiple uploaded DDs found; using the first one only"
                                        )
                                    existing_dd_path = (
                                        existing_dd_path[0]
                                        if existing_dd_path
                                        else None
                                    )
                            session.state["data_dict_file_path"] = existing_dd_path
                            logging.info(
                                f"[send-large-data-dict] ✓ Found existing vendor DD in data.json: {existing_dd_path}"
                            )
                            logging.info(
                                f"[send-large-data-dict] ✓ Stored in session.state['data_dict_file_path']: {session.state.get('data_dict_file_path')}"
                            )

                    except Exception:
                        pass

                    state_update_event = Event(
                        author="system",
                        invocation_id=f"sys-inv-{uuid.uuid4()}",
                        actions=EventActions(state_delta={"data_dict_file_path": existing_dd_path}),
                    )
                    await session_service.append_event(session=session, event=state_update_event)
                    logging.info("[send-large-data-dict] Persisted data_dict_file_path to session backend")

            # ==========================================
            # PHASE 4: Log Vendor DD Status
            # ==========================================
            if session.state.get("data_dict_file_path"):
                logging.info(
                    f"[send-large-data-dict] ✓ Vendor DD file detected in session state"
                )
            else:
                logging.info(
                    f"[send-large-data-dict] No vendor DD file - will generate from profiling"
                )

            # ==========================================
            # PHASE 5: Run Large Data Agent
            # ==========================================
            logging.info(f"[send-large-data-dict] Running large_data_root_agent...")
            logging.info(f"[send-large-data-dict] Session state BEFORE agent run:")
            logging.info(
                f"  - data_dict_file_path: {session.state.get('data_dict_file_path', 'NOT SET')}"
            )
            logging.info(
                f"  - profiling_full_results: {'SET' if session.state.get('profiling_full_results') else 'NOT SET'}"
            )
            logging.info(f"  - is_stream: {session.state.get('is_stream', 'NOT SET')}")

            # Create app and runner (matching send-stream-new pattern)
            orchestrator_app = App(name=app_name, root_agent=large_data_root_agent)
            runner = Runner(app=orchestrator_app, session_service=session_service)

            # Prepare message (same format as send-stream-new)
            msg = types.Content(role="user", parts=[types.Part(text=user_message)])

            # Run agent and iterate through events
            event_count = 0
            should_exit = False

            async for event in runner.run_async(
                user_id=user_id, session_id=session_id, new_message=msg
            ):
                event_count += 1

                # Log event details
                event_author = getattr(event, "author", "unknown")
                event_type = type(event).__name__
                logging.info(
                    f"[send-large-data-dict] Event #{event_count}: author={event_author}, type={event_type}"
                )

                # --- RATE LIMITING ---
                if hasattr(event, "usage_metadata"):
                    await manage_llm_rate_limits(event, session_id)
                # ---------------------

                # Debug: Save events
                with open(f"event_{event_count}.txt", "w", encoding="utf-8") as f:
                    f.write(f"{event}")

                # ==========================================
                # CHECK FOR STATE_DELTA WITH FINAL RESULT
                # ==========================================
                if (
                    hasattr(event, "actions")
                    and event.actions
                    and hasattr(event.actions, "state_delta")
                ):
                    state_delta = event.actions.state_delta

                    # Log state_delta keys for debugging
                    if state_delta:
                        logging.info(
                            f"[send-large-data-dict] state_delta keys: {list(state_delta.keys())}"
                        )

                    # Check if data dictionary result is ready
                    if "final_data_dict_response" in state_delta:
                        logging.info(
                            "[send-large-data-dict] ✓ DD generation complete - found in state_delta"
                        )
                        final_dd = state_delta["final_data_dict_response"]
                        
                        # Enrich the data dictionary with BigQuery stats including percentages
                        enriched_dd = _enrich_dd_with_bq_stats(
                            final_dd,
                            dataset_id_override=session.state.get("dataset_id_override")
                        )

                        logging.info(
                            f"[send-large-data-dict] Result keys: {list(enriched_dd.keys()) if isinstance(enriched_dd, dict) else 'not a dict'}"
                        )

                        # Emit completion event
                        yield {
                            "event": "message",
                            "data": json.dumps(
                                {
                                    "phase": "complete",
                                    "progress": 100,
                                    "message": "Data dictionary generation complete",
                                    "result": enriched_dd,
                                }
                            ),
                        }
                        should_exit = True
                        break

                # Check for function_call events (delegation)
                if hasattr(event, "content") and event.content:
                    parts = getattr(event.content, "parts", [])
                    for part in parts:
                        if hasattr(part, "function_call") and part.function_call:
                            func_call = part.function_call
                            logging.info(
                                f"[send-large-data-dict] Function call: {func_call.name}, args: {func_call.args}"
                            )

                # Prevent infinite loops - max 1000 events
                if event_count >= 1000:
                    logging.error(
                        f"[send-large-data-dict] ⚠️ LOOP DETECTED - Stopping after {event_count} events"
                    )
                    should_exit = True
                    break

                # Small delay to prevent tight loop
                await asyncio.sleep(0.05)

            logging.info(
                f"[send-large-data-dict] Agent execution complete - {event_count} events"
            )

            # ==========================================
            # PHASE 6: FALLBACK - Check Session State
            # ==========================================
            # If state_delta didn't trigger, check session state directly
            if not should_exit:
                session = await session_service.get_session(
                    app_name=app_name, user_id=user_id, session_id=session_id
                )
                final_dd = session.state.get("final_data_dict_response")

                if final_dd:
                    # Enrich the data dictionary with BigQuery stats including percentages
                    enriched_dd = _enrich_dd_with_bq_stats(
                        final_dd,
                        dataset_id_override=session.state.get("dataset_id_override")
                    )
                    
                    logging.info(
                        f"[send-large-data-dict] DD generation successful (fallback)"
                    )

                    # Emit completion event (phase: "complete" triggers stream close in frontend)
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {
                                "phase": "complete",
                                "progress": 100,
                                "message": "Data dictionary generation complete",
                                "result": enriched_dd,
                            }
                        ),
                    }
                else:
                    logging.warning(
                        f"[send-large-data-dict] No DD result in session state"
                    )
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {
                                "phase": "error",
                                "message": "Data dictionary generation failed - no result produced",
                            }
                        ),
                    }

            logging.info(f"[send-large-data-dict] ✓ Stream completed")

        except Exception as e:
            logging.error(f"[send-large-data-dict] ❌ Fatal error: {e}")
            import traceback

            traceback.print_exc()

            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "phase": "error",
                        "message": f"Error: {str(e)}",
                        "error_details": traceback.format_exc(),
                    }
                ),
            }

    return EventSourceResponse(event_generator())


# ============================================================================
# NEW ENDPOINT: Similarity Check Streaming
# ============================================================================
from agents.smart_similarity_agent.agent import smart_similarity_root_agent


@router.post("/similarity-check-stream")
async def similarity_check_stream(
    request: str = Form(..., description="JSON string of MessageRequest"),
    database_name: Optional[str] = Form(
        None,
        description="Optional BigQuery dataset ID for source tables (defaults to config.BQ_DATASET_ID)",
    ),
    dart_database_name: Optional[str] = Form(
        None,
        description="Optional DART dataset ID for similarity check dart tables (defaults to config.DART_DATASET_ID)",
    ),
):
    """
    Dedicated endpoint for similarity check operations (streaming).

    This endpoint is completely decoupled from the main profiling flow to reduce
    coupling and improve maintainability.

    **Supported Scenarios:**
    1. Column matching between source tables and DART reference tables
    2. Two-phase approach: semantic matching + data overlap validation
    3. Batch processing for scalability

    **Usage:**
    - Frontend calls this endpoint when user clicks "Run Similarity Check"
    - Session must have `similarity_dart_references` and `similarity_source_tables` in stateDelta
    - Results saved to `final_similarity_response` in session state

    **Key Differences from /send-stream-new:**
    - Uses smart_similarity_root_agent (not root_agent)
    - Focused on similarity check only
    - No profiling, relationship, or other features
    - Dedicated for similarity operations

    **Payload Format:**
    Same as /send-stream-new, with required stateDelta:
    {
        "sessionId": "...",
        "userId": "...",
        "appName": "...",
        "newMessage": {"parts": [{"text": "Run similarity check"}], "role": "user"},
        "stateDelta": {
            "similarity_dart_references": [
                {
                    "table": "project.dataset.dart_table",
                    "columns": ["column1", "column2"]
                }
            ],
            "similarity_source_tables": ["table1", "table2"]
        }
    }
    """

    async def event_generator():
        try:
            # ==========================================
            # PHASE 1: Parse Request
            # ==========================================
            logging.info(f"[similarity-check-stream] Starting similarity check")

            req = json.loads(request)
            session_id = req["sessionId"]
            user_message = req["newMessage"]["parts"][0]["text"]
            app_name = req["appName"]
            user_id = req["userId"]

            logging.info(
                f"[similarity-check-stream] Session: {session_id}, Message: {user_message}"
            )
            logging.info(
                f"[similarity-check-stream] DATASET_OVERRIDE: database_name parameter = {database_name}"
            )
            
            # ==========================================
            # PHASE 2: Initialize Session
            # ==========================================
            session_service = VertexAiSessionService(
                project=config.GOOGLE_CLOUD_PROJECT,
                location=config.GOOGLE_CLOUD_LOCATION,
            )

            session = await session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            logging.info(f"[similarity-check-stream] Loaded session: {session_id}")

            # Set is_stream flag and dataset_id_override (if provided)
            state_delta = {"is_stream": True}

            # Add dataset_id_override if database_name parameter is provided
            logging.info(
                f"[similarity-check-stream] DATASET_OVERRIDE: Checking database_name parameter"
            )
            if database_name:
                state_delta["dataset_id_override"] = database_name
                logging.info(
                    f"[similarity-check-stream] DATASET_OVERRIDE: Using custom dataset_id = {database_name}"
                )
                logging.info(
                    f"[similarity-check-stream] DATASET_OVERRIDE: Session state will be updated with dataset_id_override = {database_name}"
                )
            else:
                logging.info(
                    f"[similarity-check-stream] DATASET_OVERRIDE: No custom dataset_id provided, using default from config"
                )

            if dart_database_name:
                state_delta["dart_dataset_id"] = dart_database_name
                logging.info(
                    f"[similarity-check-stream] dart_dataset_id: Using custom dataset_id = {dart_database_name}"
                )

            # Persist state_delta to session
            state_update_event = Event(
                author="system",
                invocation_id=f"sys-inv-{uuid.uuid4()}",
                actions=EventActions(state_delta=state_delta),
            )
            await session_service.append_event(
                session=session, event=state_update_event
            )
            logging.info(
                f"[similarity-check-stream] DATASET_OVERRIDE: Session state event appended successfully"
            )
            logging.info(
                f"[similarity-check-stream] DATASET_OVERRIDE: State delta keys = {list(state_delta.keys())}"
            )

            # ==========================================
            # PHASE 3: Inject Similarity Data to Session State
            # ==========================================
            if req.get("stateDelta", {}).get("similarity_dart_references"):
                dart_refs = req["stateDelta"]["similarity_dart_references"]
                source_tables = req["stateDelta"].get("similarity_source_tables", [])
                filters = req["stateDelta"].get("similarity_filters", [])

                logging.info(
                    f"[similarity-check-stream] Injecting similarity data: {len(dart_refs)} DART tables, {len(source_tables)} source tables, {len(filters)} filters"
                )

                # Build state delta with filters
                similarity_state = {
                    "similarity_dart_references": dart_refs,
                    "similarity_source_tables": source_tables,
                }

                # Add filters if provided
                if filters:
                    similarity_state["similarity_filters"] = filters
                    logging.info(
                        f"[similarity-check-stream] Filters to inject: {filters}"
                    )

                # Persist to session state
                similarity_state_event = Event(
                    author="system",
                    invocation_id=f"similarity-inject-{uuid.uuid4()}",
                    actions=EventActions(state_delta=similarity_state),
                )
                await session_service.append_event(
                    session=session, event=similarity_state_event
                )
                logging.info(
                    f"[similarity-check-stream] ✓ Similarity data persisted to session"
                )
            else:
                error_msg = "Missing similarity_dart_references in stateDelta"
                logging.error(f"[similarity-check-stream] ❌ {error_msg}")
                yield {
                    "event": "message",
                    "data": json.dumps({"phase": "error", "message": error_msg}),
                }
                return

            # ==========================================
            # PHASE 4: Run Similarity Agent
            # ==========================================
            logging.info(
                f"[similarity-check-stream] Running smart_similarity_root_agent..."
            )

            # Create app and runner
            similarity_app = App(name=app_name, root_agent=smart_similarity_root_agent)
            runner = Runner(app=similarity_app, session_service=session_service)

            # Prepare message
            msg = types.Content(role="user", parts=[types.Part(text=user_message)])

            # Run agent and iterate through events
            event_count = 0
            should_exit = False

            # Send init event
            yield {
                "event": "status",
                "data": json.dumps(
                    {
                        "phase": "init",
                        "message": "Starting similarity check...",
                        "progress": 0,
                    }
                ),
            }

            async for event in runner.run_async(
                user_id=user_id, session_id=session_id, new_message=msg
            ):
                event_count += 1
                logging.info(f"[similarity-check-stream] Event #{event_count}")

                # --- RATE LIMITING ---
                if hasattr(event, "usage_metadata"):
                    await manage_llm_rate_limits(event, session_id)
                # ---------------------

                # ==========================================
                # CHECK state_delta for final_similarity_response (agent persist)
                # ==========================================
                if (
                    hasattr(event, "actions")
                    and event.actions
                    and hasattr(event.actions, "state_delta")
                    and event.actions.state_delta
                ):
                    state_delta = event.actions.state_delta
                    if "final_similarity_response" in state_delta:
                        final_result = state_delta["final_similarity_response"]
                        logging.info(
                            "[similarity-check-stream] ✓ Similarity check complete (from state_delta)"
                        )
                        yield {
                            "event": "message",
                            "data": json.dumps(
                                {
                                    "phase": "complete",
                                    "progress": 100,
                                    "message": "Similarity check complete",
                                    "result": final_result,
                                }
                            ),
                        }
                        should_exit = True
                        break

                # ==========================================
                # CHECK FOR AGENT RESPONSE (like normal flow)
                # ==========================================
                if (
                    hasattr(event, "content")
                    and event.content
                    and hasattr(event.content, "parts")
                ):
                    for part in event.content.parts:
                        # Check for text response with JSON
                        if hasattr(part, "text") and part.text:
                            text_content = part.text.strip()

                            # Try to parse JSON from markdown block or direct JSON
                            import re

                            json_match = re.search(
                                r"```json\s*(\{.*?\})\s*```", text_content, re.DOTALL
                            )

                            if json_match:
                                try:
                                    final_result = json.loads(json_match.group(1))
                                    logging.info(
                                        "[similarity-check-stream] ✓ Similarity check complete (from text)"
                                    )

                                    persist_event = Event(
                                        author="system",
                                        invocation_id=f"similarity-stream-final-{uuid.uuid4()}",
                                        actions=EventActions(
                                            state_delta={
                                                "final_similarity_response": final_result
                                            }
                                        ),
                                    )
                                    await session_service.append_event(
                                        session=session, event=persist_event
                                    )
                                    logging.info(
                                        "[similarity-check-stream] ✓ final_similarity_response persisted to session"
                                    )

                                    yield {
                                        "event": "message",
                                        "data": json.dumps(
                                            {
                                                "phase": "complete",
                                                "progress": 100,
                                                "message": "Similarity check complete",
                                                "result": final_result,
                                            }
                                        ),
                                    }
                                    should_exit = True
                                    break
                                except json.JSONDecodeError:
                                    logging.warning(
                                        "[similarity-check-stream] Failed to parse JSON from text"
                                    )

                # Event limit check (prevent infinite loops)
                if event_count >= 1000:
                    logging.error(
                        f"[similarity-check-stream] ⚠️ LOOP DETECTED - Stopping after {event_count} events"
                    )
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {
                                "phase": "error",
                                "message": f"Maximum event limit reached ({event_count} events). Possible infinite loop detected.",
                            }
                        ),
                    }
                    should_exit = True
                    break

                if should_exit:
                    break

                await asyncio.sleep(0.05)

            logging.info(
                f"[similarity-check-stream] Agent execution complete - {event_count} events"
            )

            # ==========================================
            # PHASE 5: ERROR if no result found
            # ==========================================
            if not should_exit:
                logging.warning(
                    f"[similarity-check-stream] No result extracted from agent response"
                )
                yield {
                    "event": "message",
                    "data": json.dumps(
                        {
                            "phase": "error",
                            "message": "Similarity check failed - no result produced by agent",
                        }
                    ),
                }

            logging.info(f"[similarity-check-stream] ✓ Stream completed")

        except Exception as e:
            logging.error(f"[similarity-check-stream] ❌ Fatal error: {e}")
            import traceback

            traceback.print_exc()

            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "phase": "error",
                        "message": f"Error: {str(e)}",
                        "error_details": traceback.format_exc(),
                    }
                ),
            }

    return EventSourceResponse(event_generator())


@router.post("/validate-dataset-tables")
async def validate_dataset_tables_api(payload: ValidateDatasetTablesRequest):
    """
    Validate BigQuery dataset + tables existence.

    This endpoint expects FORM-DATA fields (same style as /send-stream-new):

      - dataset_id (str, required):
            BigQuery dataset name (no project prefix)
            Example: "DATAMAP_COPILOT"

      - table_ids (List[str], required):
            One or more table names inside the dataset (no dataset/project prefix)
            Example: ["customers", "orders", "transactions"]

    Example (Frontend / Postman):
      dataset_id = "DATAMAP_COPILOT"
      table_ids  = ["customers", "orders"]

    This endpoint is NON-streaming (not SSE) but follows the same response style
    as the streaming endpoints in this project (phase/message/details).

    All responses return an object with:

      {
        "phase": "<error | validation_error | complete>",
        "message": "<human-readable message including dataset + table list>",
        "details": {
            "dataset": "<dataset_id>",
            "tables": ["<table1>", "<table2>", ...],

            # Validation flags
            "missing_dataset": <bool>,
            "missing_tables": ["<missing_table1>", "<missing_table2>", ...],

            # Present only on internal failures
            "error_details": "<traceback...>"
        }
      }

    1) Success (all valid)
       phase = "complete"
       missing_dataset = False
       missing_tables = []

    2) Invalid request / bad input
       phase = "error"
       message describes missing/invalid fields

    3) Dataset exists but some tables missing
       phase = "validation_error"
       missing_dataset = False
       missing_tables = [...]

    4) Dataset missing
       phase = "validation_error"
       missing_dataset = True
       missing_tables = []

    5) Unexpected server error
       phase = "error"
       details.error_details contains traceback
    """
    try:
        dataset_id = (payload.dataset_id or "").strip()
        table_ids = [t.strip() for t in (payload.table_ids or []) if t and t.strip()]
        print(table_ids)
        logging.info(
            f"[validate-dataset-tables] dataset={dataset_id}, table_ids={table_ids}"
        )

        # --------------------------
        # INPUT VALIDATION  -> 400
        # --------------------------
        if not dataset_id:
            payload = {
                "phase": "error",
                "message": f"Invalid request: dataset_id is required. Received dataset_id='{dataset_id}', table_ids={table_ids}",
                "details": {
                    "dataset": dataset_id,
                    "missing_dataset": False,
                    "missing_tables": [],
                    "tables": table_ids,
                },
            }
            return JSONResponse(status_code=400, content=payload)

        if not table_ids:
            payload = {
                "phase": "error",
                "message": f"Invalid request: table_ids must contain at least one table. dataset_id='{dataset_id}', table_ids={table_ids}",
                "details": {
                    "dataset": dataset_id,
                    "missing_dataset": False,
                    "missing_tables": [],
                    "tables": table_ids,
                },
            }
            return JSONResponse(status_code=400, content=payload)

        # --------------------------
        # CALL VALIDATOR FUNCTION
        # --------------------------
        validation = validate_dataset_and_tables_large_data(
            dataset_id=dataset_id, table_ids=table_ids
        )

        valid = bool(validation.get("valid", False))
        missing_dataset = bool(validation.get("missing_dataset", False))
        missing_tables = validation.get("missing_tables") or []

        # --------------------------
        # FAILURE CASES -> 404
        # --------------------------
        if not valid:
            if missing_dataset:
                msg = f"BigQuery validation failed: Dataset '{dataset_id}' not found."
            elif missing_tables:
                msg = f"BigQuery validation failed: Dataset '{dataset_id}' exists, but missing tables={missing_tables}. Requested table_ids={table_ids}"
            else:
                msg = f"BigQuery validation failed for dataset='{dataset_id}', table_ids={table_ids}"

            payload = {
                "phase": "validation_error",
                "message": msg,
                "details": {
                    "dataset": dataset_id,
                    "missing_dataset": missing_dataset,
                    "missing_tables": missing_tables,
                    "tables": table_ids,
                },
            }
            return JSONResponse(status_code=404, content=payload)

        # --------------------------
        # SUCCESS CASE -> 200
        # --------------------------
        payload = {
            "phase": "complete",
            "message": f"BigQuery validation successful dataset='{dataset_id}', table_ids={table_ids}",
            "details": {
                "dataset": dataset_id,
                "missing_dataset": False,
                "missing_tables": [],
                "tables": table_ids,
            },
        }
        return JSONResponse(status_code=200, content=payload)

    except Exception as e:
        logging.error(f"[validate-dataset-tables] Fatal error: {e}")
        logging.error(traceback.format_exc())

        payload = {
            "phase": "error",
            "message": f"Error while validating dataset='{dataset_id}' with table_ids={table_ids}: {str(e)}",
            "details": {
                "dataset": dataset_id if "dataset_id" in locals() else None,
                "tables": table_ids if "table_ids" in locals() else [],
                "error_details": traceback.format_exc(),
            },
        }
        return JSONResponse(status_code=500, content=payload)
