from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from config.settings import config
from utils.gcs_artifact_utils import (
    download_json_uri,
    download_text,
    gcs_uri,
    list_blobs,
    materialize_gcs_uri_to_temp_file,
    upload_bytes,
    upload_json,
    upload_text,
)


def _prefix() -> str:
    value = str(getattr(config, "PROFILING_ARTIFACT_PREFIX", "profiling-artifacts") or "profiling-artifacts")
    value = value.strip().strip("/")
    return value or "profiling-artifacts"


def _session_base(session_id: str) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        raise RuntimeError("session_id is required for profiling artifact storage.")
    return f"{_prefix()}/{sid}"


def session_base(session_id: str) -> str:
    return _session_base(session_id)


def _context_object_name(session_id: str) -> str:
    return f"{_session_base(session_id)}/session/context.json"


def _resume_json_object_name(session_id: str, artifact_name: str) -> str:
    safe_name = _sanitize_filename(artifact_name).rsplit(".", 1)[0] or "artifact"
    return f"{_session_base(session_id)}/resume/{safe_name}.json"


def profiling_context_uri(session_id: str) -> str:
    return gcs_uri(_context_object_name(session_id))


def load_profiling_session_context(session_id: str) -> dict[str, Any]:
    uri = profiling_context_uri(session_id)
    try:
        return download_json_uri(uri)
    except FileNotFoundError:
        return {}


def save_profiling_session_context(session_id: str, payload: dict[str, Any]) -> str:
    return upload_json(object_name=_context_object_name(session_id), payload=payload)


def update_profiling_session_context(session_id: str, updates: dict[str, Any]) -> tuple[dict[str, Any], str]:
    context = load_profiling_session_context(session_id)
    context.update(updates)
    uri = save_profiling_session_context(session_id, context)
    return context, uri


def _sanitize_filename(filename: str) -> str:
    base = os.path.basename(str(filename or "").strip())
    return (base or "artifact.bin").replace("\\", "_").replace("/", "_")


def save_raw_file(*, session_id: str, filename: str, content: bytes, subfolder: str = "files") -> str:
    safe_name = _sanitize_filename(filename)
    object_name = f"{_session_base(session_id)}/raw_files/{subfolder}/{safe_name}"
    suffix = Path(safe_name).suffix.lower()
    content_type_map = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
        ".pdf": "application/pdf",
        ".txt": "text/plain; charset=utf-8",
        ".zip": "application/zip",
    }
    content_type = content_type_map.get(suffix, "application/octet-stream")
    return upload_bytes(object_name=object_name, content=content, content_type=content_type)


def save_document_bytes(*, session_id: str, document_kind: str, filename: str, content: bytes) -> str:
    safe_name = _sanitize_filename(filename)
    object_name = f"{_session_base(session_id)}/documents/{document_kind}/{safe_name}"
    content_type = "application/octet-stream"
    suffix = Path(safe_name).suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif suffix == ".csv":
        content_type = "text/csv"
    elif suffix == ".pdf":
        content_type = "application/pdf"
    elif suffix == ".txt":
        content_type = "text/plain; charset=utf-8"
    return upload_bytes(object_name=object_name, content=content, content_type=content_type)


def save_generated_file(*, session_id: str, local_path: str | Path, generated_kind: str, filename: str | None = None) -> str:
    path = Path(local_path)
    object_name = f"{_session_base(session_id)}/derived/{generated_kind}/{_sanitize_filename(filename or path.name)}"
    content_type = "application/octet-stream"
    if path.suffix.lower() in {".xlsx", ".xls"}:
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif path.suffix.lower() == ".md":
        content_type = "text/markdown; charset=utf-8"
    with path.open("rb") as handle:
        return upload_bytes(object_name=object_name, content=handle.read(), content_type=content_type)


def save_resume_json_artifact(*, session_id: str, artifact_name: str, payload: Any) -> str:
    return upload_json(object_name=_resume_json_object_name(session_id, artifact_name), payload=payload)


def load_resume_json_artifact(uri: str) -> dict[str, Any]:
    return download_json_uri(uri)


def save_profiling_report_artifacts(*, session_id: str, report_id: str, html_content: str, json_content: str) -> tuple[str, str]:
    report_key = str(report_id or "").strip()
    if not report_key:
        raise RuntimeError("report_id is required for profiling report artifacts.")
    base = f"{_session_base(session_id)}/reports/{report_key}"
    html_uri = upload_text(object_name=f"{base}.html", content=html_content, content_type="text/html; charset=utf-8")
    json_uri = upload_text(object_name=f"{base}.json", content=json_content, content_type="application/json")
    return html_uri, json_uri


def load_profiling_report_html(session_id: str, report_id: str) -> str:
    return download_text(object_name=f"{_session_base(session_id)}/reports/{report_id}.html")


def load_profiling_report_json(session_id: str, report_id: str) -> dict[str, Any]:
    base = _session_base(session_id)
    rid = str(report_id or "").strip()
    # 1) exact match when a report id is supplied
    if rid:
        try:
            return json.loads(download_text(object_name=f"{base}/reports/{rid}.json"))
        except FileNotFoundError:
            pass
    # 2) resilient fallback: use the session's available report JSON (handles an
    #    empty/stale report_id that would otherwise resolve to "reports/.json").
    from utils.gcs_artifact_utils import list_blobs

    candidates = sorted(
        b.name for b in list_blobs(prefix=f"{base}/reports/") if b.name.endswith(".json")
    )
    if not candidates:
        raise FileNotFoundError(
            f"No profiling report JSON found for session {session_id} (report_id={report_id!r})."
        )
    return json.loads(download_text(object_name=candidates[-1]))


def profiling_report_proxy_path(session_id: str, report_id: str) -> str:
    return f"/files/profiling-reports/{session_id}/{report_id}"


def materialize_profiling_artifact(uri_or_path: str) -> Path:
    raw = str(uri_or_path or "").strip()
    if not raw:
        raise RuntimeError("Artifact path is required.")
    if raw.startswith("gs://"):
        return materialize_gcs_uri_to_temp_file(raw)
    return Path(raw)


def list_session_artifacts(session_id: str) -> list[str]:
    prefix = f"{_session_base(session_id)}/"
    return [gcs_uri(blob.name) for blob in list_blobs(prefix=prefix)]


PROFILING_CHAT_RESPONSE_ARTIFACT = "send-profiling-chat-response"
PROFILING_CHAT_RESPONSE_URI_KEY = "profiling_chat_response_uri"


def persist_profiling_chat_response(session_id: str, payload: Any) -> str:
    """Store the canonical /send Data Profiling payload outside Vertex session state."""
    if not session_id or payload in (None, "", [], {}):
        raise RuntimeError("session_id and profiling chat payload are required.")
    from utils.gcs_artifact_utils import make_json_compatible

    artifact_uri = save_resume_json_artifact(
        session_id=session_id,
        artifact_name=PROFILING_CHAT_RESPONSE_ARTIFACT,
        payload=make_json_compatible(payload),
    )
    update_profiling_session_context(session_id, {PROFILING_CHAT_RESPONSE_URI_KEY: artifact_uri})
    return artifact_uri


def load_profiling_chat_response(session_id: str) -> dict[str, Any] | None:
    """Load canonical Data Profiling response saved during /send."""
    context = load_profiling_session_context(session_id)
    artifact_uri = str(context.get(PROFILING_CHAT_RESPONSE_URI_KEY) or "").strip()
    if not artifact_uri:
        return None
    try:
        loaded = load_resume_json_artifact(artifact_uri)
    except FileNotFoundError:
        return None
    return loaded if isinstance(loaded, dict) else None
