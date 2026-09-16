from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any
from urllib import response

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from utils.adk_runtime import VertexAiSessionService
from google.genai.errors import ClientError, ServerError

from api.dependencies.auth import CurrentUser, resolve_current_user
from api.models import (
    AppSessionCreateRequest,
    AppSessionRenameRequest,
    ExtractResumeStateRequest,
    MappingResumeStateRequest,
    MappingReviewDraftRequest,
    ProfilingResumeStateRequest,
    ProfilingRunStartRequest,
    SessionCreateRequest,
    SessionModule,
)
from config.settings import config
from db.engine import app_db_session, is_app_db_enabled
from db.repositories import AppSessionRepository
from utils.run_artifact_loader import (
    load_latest_step4_state,
    load_step2_state,
    load_step3_review_package,
)
from utils.gcs_artifact_utils import make_json_compatible
from utils.init_session import InitSession
from utils.profiling_artifact_store import (
    load_resume_json_artifact,
    save_resume_json_artifact,
)

ADK_API_URL = os.getenv("ADK_API_URL", "http://localhost:8000")

router = APIRouter()
logger = logging.getLogger(__name__)


def _resolve_active_app_name(fallback_app_name: str | None = None) -> str:
    resource_name = str(fallback_app_name or "").strip()
    if resource_name:
        return resource_name

    init_session = InitSession().initialize_session()
    if hasattr(init_session, "name") and getattr(init_session, "name"):
        return str(getattr(init_session, "name"))
    if hasattr(init_session, "resource_name") and getattr(
        init_session, "resource_name"
    ):
        return str(getattr(init_session, "resource_name"))
    if hasattr(init_session, "to_dict"):
        payload = init_session.to_dict()
        if isinstance(payload, dict) and payload.get("name"):
            return str(payload["name"])

    raise RuntimeError("Unable to resolve active reasoning engine name.")


async def _create_vertex_runtime(*, user_id: str) -> dict[str, str]:
    # Standalone: local SQLite-backed ADK session service (no Vertex).
    from utils.adk_runtime import get_session_service

    session_service = get_session_service()
    app_name = _resolve_active_app_name()
    session_response = await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        state={"is_stream": False},
    )
    return {
        "vertex_session_id": str(session_response.id),
        "vertex_app_name": app_name,
        "vertex_user_id": user_id,
    }


async def _try_create_vertex_runtime(*, user_id: str) -> dict[str, str] | None:
    """Best-effort ADK runtime bootstrap; returns None when optional init fails."""
    try:
        return await _create_vertex_runtime(user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Optional ADK runtime creation skipped for user %s: %s",
            user_id,
            exc,
            exc_info=True,
        )
        return None


def _serialize_app_session(session_obj: Any, *, hl7: dict | None = None) -> dict[str, Any]:
    payload = {
        "id": session_obj.id,
        "title": session_obj.title,
        "status": session_obj.status,
        "user_id": session_obj.user_key,
        "user_email": session_obj.user_email,
        "current_profiling_run_id": session_obj.current_profiling_run_id,
        "current_mapping_run_id": session_obj.current_mapping_run_id,
        "current_extract_run_id": getattr(session_obj, "current_extract_run_id", None),
        "current_hl7_session_id": getattr(session_obj, "current_hl7_session_id", None),
        "active_vertex_session_id": session_obj.active_vertex_session_id,
        "active_vertex_app_name": session_obj.active_vertex_app_name,
        "last_opened_at": session_obj.last_opened_at.isoformat()
        if session_obj.last_opened_at
        else None,
        "created_at": session_obj.created_at.isoformat()
        if session_obj.created_at
        else None,
        "updated_at": session_obj.updated_at.isoformat()
        if session_obj.updated_at
        else None,
    }
    if hl7 is not None:
        payload["hl7"] = hl7
    return payload


def _has_profiling_resume(run: Any) -> bool:
    if not run:
        return False
    state = run.resume_state_json or {}
    return bool(isinstance(state, dict) and state.get("uploadResponse"))


def _profiling_mode_from_state(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return "normal"
    mode = (
        str(state.get("profilingMode") or state.get("profiling_mode") or "")
        .strip()
        .lower()
    )
    return "streaming" if mode == "streaming" else "normal"


def _profiling_mode(run: Any) -> str:
    if not run:
        return "normal"
    return _profiling_mode_from_state(make_json_compatible(run.resume_state_json or {}))


def _has_mapping_resume(run: Any) -> bool:
    if not run:
        return False
    state = run.resume_state_json or {}
    if not isinstance(state, dict):
        return False
    current_step = state.get("currentStep")
    return bool(
        state.get("step4Data")
        or state.get("mappingData")
        or run.step4_uri
        or run.step2_uri
        or run.step3_review_package_uri
        or (isinstance(current_step, (int, float)) and current_step >= 2)
    )


def _hydrate_mapping_resume_state(run: Any) -> dict[str, Any]:
    state = make_json_compatible(run.resume_state_json or {})
    run_id = str(run.mapping_run_id or "").strip()

    if not run_id:
        return state

    if not state.get("mappingData") and run.step2_uri:
        try:
            step2_state, _ = load_step2_state(run_id)
            payload = step2_state.model_dump()
            state["mappingData"] = payload
            if not state.get("baselineMappingData"):
                state["baselineMappingData"] = payload
            current_step = state.get("currentStep")
            if not isinstance(current_step, (int, float)) or current_step < 2:
                state["currentStep"] = 2
        except Exception:
            logger.exception(
                "Failed to hydrate Step 2 mapping state for run_id=%s", run_id
            )

    if not state.get("step3Questions") and run.step3_review_package_uri:
        try:
            review_package, _ = load_step3_review_package(run_id)
            state["step3Questions"] = [
                q.model_dump() for q in (review_package.review_questions or [])
            ]
            current_step = state.get("currentStep")
            if not isinstance(current_step, (int, float)) or current_step < 2:
                state["currentStep"] = 2
        except Exception:
            logger.exception(
                "Failed to hydrate Step 3 review package for run_id=%s", run_id
            )

    if not state.get("step4Data") and run.step4_uri:
        try:
            step4_state, step4_uri = load_latest_step4_state(run_id)
            state["step4Data"] = make_json_compatible(step4_state)
            state["step4StatePath"] = step4_uri
            state["currentStep"] = 4
        except Exception:
            logger.exception("Failed to hydrate Step 4 state for run_id=%s", run_id)

    return state


def _normalize_data_dictionary_json(value: Any) -> list[Any] | None:
    normalized = make_json_compatible(value)
    if not normalized:
        return None
    if isinstance(normalized, list):
        if not normalized:
            return None
        if isinstance(normalized[0], dict):
            return [normalized]
        return normalized
    if isinstance(normalized, dict):
        result = normalized.get("result")
        if isinstance(result, list) and result:
            return [make_json_compatible(result)]
        return [normalized]
    return None


def _externalize_profiling_resume_state(
    session_id: str, resume_state: dict[str, Any]
) -> dict[str, Any]:
    state = make_json_compatible(resume_state or {})
    state["profilingMode"] = _profiling_mode_from_state(state)
    data_dictionary_state = state.get("dataDictionaryState")
    result_data = (
        data_dictionary_state.get("resultData")
        if isinstance(data_dictionary_state, dict)
        else None
    )
    normalized_dd_json = _normalize_data_dictionary_json(
        state.get("dataDictionaryJson")
    )
    if not normalized_dd_json:
        normalized_dd_json = _normalize_data_dictionary_json(result_data)

    artifact_payload = {
        "dataDictionaryJson": normalized_dd_json,
        "dataDictionaryResponse": state.get("dataDictionaryResponse"),
        "dataDictionaryState": data_dictionary_state,
        "modifiedDataDictionaryResponse": state.get("modifiedDataDictionaryResponse"),
    }

    has_artifact_data = any(
        artifact_payload.get(key) not in (None, "", [], {})
        for key in (
            "dataDictionaryJson",
            "dataDictionaryResponse",
            "dataDictionaryState",
            "modifiedDataDictionaryResponse",
        )
    )
    if not has_artifact_data:
        return state

    artifact_uri = save_resume_json_artifact(
        session_id=session_id,
        artifact_name="data-dictionary",
        payload=artifact_payload,
    )
    state["dataDictionaryArtifactUri"] = artifact_uri
    state["dataDictionaryJson"] = normalized_dd_json
    if isinstance(data_dictionary_state, dict):
        updated_state = dict(data_dictionary_state)
        if normalized_dd_json and not updated_state.get("json"):
            updated_state["json"] = normalized_dd_json
        state["dataDictionaryState"] = updated_state

    if state.get("profilingMode") == "streaming":
        streaming_payload = {
            "initialMessageData": state.get("initialMessageData"),
            "relationshipResponse": state.get("relationshipResponse"),
            "similarityResponse": state.get("similarityResponse"),
            "anomalyData": state.get("anomalyData"),
            "prevMetadataResponse": state.get("prevMetadataResponse"),
            "modifiedRelationshipResponse": state.get("modifiedRelationshipResponse"),
            "modifiedAnomalyResponse": state.get("modifiedAnomalyResponse"),
            "modifiedMetadataResponse": state.get("modifiedMetadataResponse"),
            "steps": state.get("steps"),
        }
        has_streaming_artifact = any(
            streaming_payload.get(key) not in (None, "", [], {})
            for key in streaming_payload
        )
        if has_streaming_artifact:
            artifact_uri = save_resume_json_artifact(
                session_id=session_id,
                artifact_name="streaming-profiling",
                payload=streaming_payload,
            )
            state["streamingProfilingArtifactUri"] = artifact_uri
            for key in streaming_payload:
                state.pop(key, None)
    return state


def _hydrate_profiling_resume_state(
    resume_state: dict[str, Any] | None,
) -> dict[str, Any]:
    state = make_json_compatible(resume_state or {})
    state["profilingMode"] = _profiling_mode_from_state(state)
    artifact_uri = str(state.get("dataDictionaryArtifactUri") or "").strip()

    if artifact_uri:
        try:
            artifact_payload = load_resume_json_artifact(artifact_uri)
            if isinstance(artifact_payload, dict):
                for key in (
                    "dataDictionaryJson",
                    "dataDictionaryResponse",
                    "dataDictionaryState",
                    "modifiedDataDictionaryResponse",
                ):
                    if artifact_payload.get(key) is not None:
                        state[key] = artifact_payload[key]
        except FileNotFoundError:
            logger.warning(
                "Profiling data dictionary artifact missing for URI %s", artifact_uri
            )
        except Exception:
            logger.exception(
                "Failed to hydrate profiling data dictionary artifact for URI %s",
                artifact_uri,
            )

    data_dictionary_state = state.get("dataDictionaryState")
    normalized_dd_json = _normalize_data_dictionary_json(
        state.get("dataDictionaryJson")
    )
    if not normalized_dd_json and isinstance(data_dictionary_state, dict):
        normalized_dd_json = _normalize_data_dictionary_json(
            data_dictionary_state.get("resultData")
        )
    if normalized_dd_json:
        state["dataDictionaryJson"] = normalized_dd_json
        if isinstance(data_dictionary_state, dict) and not data_dictionary_state.get(
            "json"
        ):
            updated_state = dict(data_dictionary_state)
            updated_state["json"] = normalized_dd_json
            state["dataDictionaryState"] = updated_state

    streaming_artifact_uri = str(
        state.get("streamingProfilingArtifactUri") or ""
    ).strip()
    if streaming_artifact_uri:
        try:
            artifact_payload = load_resume_json_artifact(streaming_artifact_uri)
            if isinstance(artifact_payload, dict):
                for key in (
                    "initialMessageData",
                    "relationshipResponse",
                    "similarityResponse",
                    "anomalyData",
                    "prevMetadataResponse",
                    "modifiedRelationshipResponse",
                    "modifiedAnomalyResponse",
                    "modifiedMetadataResponse",
                    "steps",
                ):
                    if artifact_payload.get(key) is not None:
                        state[key] = artifact_payload[key]
        except FileNotFoundError:
            logger.warning(
                "Streaming profiling artifact missing for URI %s",
                streaming_artifact_uri,
            )
        except Exception:
            logger.exception(
                "Failed to hydrate streaming profiling artifact for URI %s",
                streaming_artifact_uri,
            )

    return state


def _serialize_profiling_run(run: Any) -> dict[str, Any] | None:
    if not run:
        return None
    hydrated_state = _hydrate_profiling_resume_state(run.resume_state_json)
    return {
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "resume_state": hydrated_state,
        "profiling_mode": _profiling_mode_from_state(hydrated_state),
        "profiling_context_uri": run.profiling_context_uri,
        "active_vertex_session_id": run.active_vertex_session_id,
        "active_vertex_app_name": run.active_vertex_app_name,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
    }


def _serialize_profiling_run_summary(run: Any) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "profiling_mode": _profiling_mode(run),
        "profiling_context_uri": run.profiling_context_uri,
        "active_vertex_session_id": run.active_vertex_session_id,
        "active_vertex_app_name": run.active_vertex_app_name,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "has_resume": _has_profiling_resume(run),
    }


def _has_extract_resume(run: Any) -> bool:
    if not run:
        return False
    state = run.resume_state_json or {}
    if not isinstance(state, dict):
        return False
    # Resumable once the upload/parse step produced a requirement layer.
    return bool(state.get("uploadSessionId") or state.get("brdInfo") or run.upload_session_id)


def _serialize_extract_run(run: Any) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "resume_state": make_json_compatible(run.resume_state_json or {}),
        "upload_session_id": run.upload_session_id,
        "brd_gcs_uri": run.brd_gcs_uri,
        "layout_gcs_uri": run.layout_gcs_uri,
        "metadata_gcs_uri": run.metadata_gcs_uri,
        "driver_gcs_uri": run.driver_gcs_uri,
        "active_vertex_session_id": run.active_vertex_session_id,
        "active_vertex_app_name": run.active_vertex_app_name,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
    }


def _serialize_extract_run_summary(run: Any) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "has_resume": _has_extract_resume(run),
    }


def _serialize_mapping_run(run: Any) -> dict[str, Any] | None:
    if not run:
        return None
    draft = run.review_draft
    hydrated_resume_state = _hydrate_mapping_resume_state(run)
    return {
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "mapping_run_id": run.mapping_run_id,
        "resume_state": hydrated_resume_state,
        "artifacts": {
            "step1_uri": run.step1_uri,
            "step2_uri": run.step2_uri,
            "step3_review_package_uri": run.step3_review_package_uri,
            "step3_capture_uri": run.step3_capture_uri,
            "step4_uri": run.step4_uri,
        },
        "review_draft": {
            "answers": draft.answers_json or {},
            "feedbacks": draft.feedbacks_json or {},
            "changed_rows": draft.changed_rows_json or [],
            "active_tab": draft.active_tab,
            "selected_row_id": draft.selected_row_id,
            "last_saved_at": draft.last_saved_at.isoformat() if draft else None,
        }
        if draft
        else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
    }


def _serialize_mapping_run_summary(run: Any) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "mapping_run_id": run.mapping_run_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "has_resume": _has_mapping_resume(run),
    }


def _require_app_db() -> None:
    if not is_app_db_enabled():
        raise HTTPException(
            status_code=503, detail="App session database is not configured."
        )


@router.get("/intialize_sessions")
async def intialize_sessions():
    """Legacy endpoint kept for backward compatibility."""
    try:
        init_session = InitSession().initialize_session()
        return init_session.to_dict()
    except ServerError as e:
        raise HTTPException(
            status_code=e.code, detail=f"Error sending message: {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=response.status_code if "response" in locals() else 500,
            detail=f"Failed to create session: {e}",
        ) from e


@router.post("/")
async def create_session(request: SessionCreateRequest):
    """Legacy Vertex-session endpoint kept for compatibility with existing flows."""
    try:
        session_service = VertexAiSessionService(
            config.GOOGLE_CLOUD_PROJECT, config.GOOGLE_CLOUD_LOCATION
        )
        app_name = _resolve_active_app_name(request.app_name)
        try:
            session_response = await session_service.create_session(
                app_name=app_name,
                user_id=request.user_id,
                state=request.initial_state,
            )
        except ClientError as exc:
            detail = str(exc)
            if "ReasoningEngine does not exist" not in detail:
                raise
            refreshed_app_name = _resolve_active_app_name()
            session_response = await session_service.create_session(
                app_name=refreshed_app_name,
                user_id=request.user_id,
                state=request.initial_state,
            )

        return session_response
    except ServerError as e:
        raise HTTPException(
            status_code=e.code, detail=f"Error sending message: {e}"
        ) from e
    except ClientError as e:
        raise HTTPException(
            status_code=e.code, detail=f"Failed to create session: {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=response.status_code if "response" in locals() else 500,
            detail=f"Failed to create session: {e}",
        ) from e


@router.get("/")
async def get_sessions(user_id: str | None = None, app_name: str | None = None):
    """Legacy Vertex-session listing endpoint kept for compatibility."""
    if not user_id or not app_name:
        return {"sessions": []}
    try:
        session_service = VertexAiSessionService(
            config.GOOGLE_CLOUD_PROJECT, config.GOOGLE_CLOUD_LOCATION
        )
        sessions_response = await session_service.list_sessions(
            user_id=user_id, app_name=app_name
        )
        return sessions_response
    except ServerError as e:
        raise HTTPException(
            status_code=e.code, detail=f"Error sending message: {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve sessions: {e}"
        ) from e


@router.delete("/")
async def delete_sessions(app_name: str):
    """Legacy Vertex-session delete endpoint kept for compatibility."""
    try:
        delete_response = InitSession().delete_session(resource_name=app_name)
        return {"detail": f"Sessions deleted successfully {delete_response}"}
    except ServerError as e:
        raise HTTPException(
            status_code=e.code, detail=f"Error sending message: {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve sessions: {e}"
        ) from e


@router.get("/vertex/{user_id}/{session_id}")
async def retrieve_session(user_id: str, session_id: str, app_name: str):
    """Legacy Vertex-session retrieval endpoint kept for compatibility."""
    try:
        session_service = VertexAiSessionService(
            config.GOOGLE_CLOUD_PROJECT, config.GOOGLE_CLOUD_LOCATION
        )
        session_response = await session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        return session_response
    except ServerError as e:
        raise HTTPException(
            status_code=e.code, detail=f"Error sending message: {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=response.status_code if "response" in locals() else 500,
            detail=f"Failed to retrieve session: {e}",
        ) from e


@router.get("/app/list")
async def list_app_sessions(
    module: SessionModule | None = Query(default=None),
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        sessions = repo.list_sessions(user_key=current_user.user_key)
        # Optional module filter: ?module=sess (profiling/sourcing) | extract.
        # Default returns all of the user's sessions (profiling + extract) so the
        # shared Sessions page and Sidebar can surface both workflows.
        prefix = None
        if module == SessionModule.extract:
            prefix = "extract_"
        elif module == SessionModule.sourcing:
            prefix = "sess_"
        items = [
            item for item in sessions
            if prefix is None or item.id.startswith(prefix)
        ]
        return {
            "sessions": [
                _serialize_app_session(item, hl7=repo.hl7_summary_for_session(item))
                for item in items
            ]
        }


@router.post("/app")
async def create_app_session(
    payload: AppSessionCreateRequest,
    module: SessionModule = Query(...),
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    # ADK runtime is created lazily on POST /app/{id}/profiling/start — not here.
    # Creating it during session shell setup breaks HL7 upload on Cloud Run when
    # the ADK SQLite store is unavailable.
    runtime: dict[str, str] | None = None
    try:
        with app_db_session() as db:
            repo = AppSessionRepository(db)
            session_obj = repo.create_session(
                user_key=current_user.user_key,
                user_email=current_user.user_email,
                title=payload.title,
                runtime=runtime,
                id_prefix="extract" if module == SessionModule.extract else "sess",
            )
            return {
                "session": _serialize_app_session(session_obj),
                "runtime": runtime,
            }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to create app session for user %s", current_user.user_key)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create app session: {exc}",
        ) from exc


@router.get("/app/{session_id}")
async def get_app_session_detail(
    session_id: str,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        repo.touch_opened(session=session_obj)
        profiling_run = repo.get_current_profiling_run(session=session_obj)
        mapping_run = repo.get_current_mapping_run(session=session_obj)
        extract_run = repo.get_current_extract_run(session=session_obj)
        return {
            "session": _serialize_app_session(session_obj),
            "profiling_run": _serialize_profiling_run(profiling_run),
            "mapping_run": _serialize_mapping_run(mapping_run),
            "extract_run": _serialize_extract_run(extract_run),
            "runtime": {
                "vertex_session_id": session_obj.active_vertex_session_id,
                "vertex_app_name": session_obj.active_vertex_app_name,
                "vertex_user_id": session_obj.active_vertex_user_id
                or session_obj.user_key,
            },
        }


@router.get("/app/{session_id}/summary")
async def get_app_session_summary(
    session_id: str,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        profiling_run = repo.get_current_profiling_run(session=session_obj)
        mapping_run = repo.get_current_mapping_run(session=session_obj)
        extract_run = repo.get_current_extract_run(session=session_obj)
        return {
            "session": _serialize_app_session(session_obj),
            "profiling_run": _serialize_profiling_run_summary(profiling_run),
            "mapping_run": _serialize_mapping_run_summary(mapping_run),
            "extract_run": _serialize_extract_run_summary(extract_run),
            "flags": {
                "has_profiling_resume": _has_profiling_resume(profiling_run),
                "has_mapping_resume": _has_mapping_resume(mapping_run),
                "has_extract_resume": _has_extract_resume(extract_run),
            },
        }


@router.patch("/app/{session_id}")
async def rename_app_session(
    session_id: str,
    payload: AppSessionRenameRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        renamed = repo.rename_session(session=session_obj, title=payload.title)
        return {"session": _serialize_app_session(renamed)}


@router.delete("/app/{session_id}")
async def delete_app_session(
    session_id: str,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        repo.soft_delete_session(session=session_obj)
        return {"deleted": True, "session_id": session_id}


@router.post("/app/{session_id}/runtime")
async def ensure_app_session_runtime(
    session_id: str,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    """Create the ADK session for this app session if it does not already have one.

    Unlike /profiling/start, this does not open a new profiling run — it is safe
    to call from chat or other flows that only need a session_id.
    """
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        existing_id = session_obj.active_vertex_session_id
        existing_app = session_obj.active_vertex_app_name
        run = repo.get_current_profiling_run(session=session_obj)
        if not existing_id and run and run.active_vertex_session_id:
            runtime = {
                "vertex_session_id": run.active_vertex_session_id,
                "vertex_app_name": run.active_vertex_app_name or "",
                "vertex_user_id": current_user.user_key,
            }
            repo.attach_runtime(session=session_obj, runtime=runtime)
            return {"runtime": runtime, "session": _serialize_app_session(session_obj)}
        if existing_id and existing_app:
            return {
                "runtime": {
                    "vertex_session_id": existing_id,
                    "vertex_app_name": existing_app,
                    "vertex_user_id": session_obj.active_vertex_user_id
                    or current_user.user_key,
                },
                "session": _serialize_app_session(session_obj),
            }

    runtime = await _create_vertex_runtime(user_id=current_user.user_key)
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        repo.attach_runtime(session=session_obj, runtime=runtime)
        return {
            "runtime": runtime,
            "session": _serialize_app_session(session_obj),
        }


@router.post("/app/{session_id}/profiling/start")
async def start_profiling_run(
    session_id: str,
    _payload: ProfilingRunStartRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    runtime = await _create_vertex_runtime(user_id=current_user.user_key)
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        run = repo.create_profiling_run(
            session=session_obj,
            profiling_context_uri=None,
            vertex_session_id=runtime["vertex_session_id"],
            vertex_app_name=runtime["vertex_app_name"],
        )
        return {
            "session": _serialize_app_session(session_obj),
            "profiling_run": _serialize_profiling_run(run),
            "runtime": runtime,
        }


@router.patch("/app/{session_id}/profiling")
async def save_profiling_resume_state(
    session_id: str,
    payload: ProfilingResumeStateRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        run = repo.get_current_profiling_run(session=session_obj)
        if not run:
            raise HTTPException(
                status_code=404,
                detail="No active profiling run found for this session.",
            )
        resume_state = dict(payload.resume_state or {})
        existing_state = make_json_compatible(run.resume_state_json or {})
        if "profilingMode" not in resume_state and existing_state.get("profilingMode"):
            resume_state["profilingMode"] = existing_state["profilingMode"]
        repo.update_profiling_run(
            run=run,
            status=payload.status,
            current_step=payload.current_step,
            resume_state_json=_externalize_profiling_resume_state(
                session_id, resume_state
            ),
            profiling_context_uri=payload.profiling_context_uri,
            completed=(payload.status == "COMPLETED"),
        )
        return {"profiling_run": _serialize_profiling_run(run)}


@router.patch("/app/{session_id}/extract")
async def save_extract_resume_state(
    session_id: str,
    payload: ExtractResumeStateRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        # Auto-create the extract run on first save (the extract workflow has no
        # explicit "start" step — the run begins when the user uploads documents).
        run = repo.get_current_extract_run(session=session_obj)
        if not run:
            run = repo.create_extract_run(
                session=session_obj,
                vertex_session_id=session_obj.active_vertex_session_id,
                vertex_app_name=session_obj.active_vertex_app_name,
            )
        repo.update_extract_run(
            run=run,
            status=payload.status,
            current_step=payload.current_step,
            resume_state_json=dict(payload.resume_state or {}),
            upload_session_id=payload.upload_session_id,
            brd_gcs_uri=payload.brd_gcs_uri,
            layout_gcs_uri=payload.layout_gcs_uri,
            metadata_gcs_uri=payload.metadata_gcs_uri,
            driver_gcs_uri=payload.driver_gcs_uri,
            completed=(payload.status == "COMPLETED"),
        )
        return {"extract_run": _serialize_extract_run(run)}


@router.patch("/app/{session_id}/mapping")
async def save_mapping_resume_state(
    session_id: str,
    payload: MappingResumeStateRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        run = repo.get_current_mapping_run(session=session_obj)
        if not run:
            raise HTTPException(
                status_code=404, detail="No active mapping run found for this session."
            )
        repo.update_mapping_run(
            run=run,
            status=payload.status,
            current_step=payload.current_step,
            resume_state_json=payload.resume_state,
            completed=(payload.status == "COMPLETED"),
        )
        return {"mapping_run": _serialize_mapping_run(run)}


@router.put("/app/{session_id}/mapping-review-draft")
async def save_mapping_review_draft(
    session_id: str,
    payload: MappingReviewDraftRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    _require_app_db()
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(
            session_id=session_id, user_key=current_user.user_key
        )
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        run = repo.get_current_mapping_run(session=session_obj)
        if not run:
            raise HTTPException(
                status_code=404, detail="No active mapping run found for this session."
            )
        repo.save_mapping_review_draft(
            mapping_run=run,
            answers_json=payload.answers,
            feedbacks_json=payload.feedbacks,
            changed_rows_json=payload.changed_rows,
            active_tab=payload.active_tab,
            selected_row_id=payload.selected_row_id,
        )
        repo.update_mapping_run(run=run, current_step="review", status="REVIEW")
        return {"mapping_run": _serialize_mapping_run(run)}


@router.get("/dashboarddetails")
async def get_dashboard_details(
    current_user: CurrentUser = Depends(resolve_current_user),
    db_ctx=Depends(app_db_session),
):
    _require_app_db()
    try:
        user_key = current_user.user_key
        with db_ctx as db:
            repo = AppSessionRepository(db)
            stats = repo.get_dashboard_stats(user_key=user_key)
            return stats

    except Exception as e:
        logger.exception(
            "Error fetching dashboard details for user: %s", current_user.user_key
        )
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
