"""Local ADK runtime helpers (standalone, no GCP / Vertex Reasoning Engine).

Provides a single SQLite-backed ADK session service used everywhere in place of
``VertexAiSessionService``. Call ``get_session_service()`` instead of constructing
a ``VertexAiSessionService(project=..., location=..., agent_engine_id=...)``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from google.adk.sessions import DatabaseSessionService

from config.settings import config
from utils.init_session import LOCAL_APP_NAME

logger = logging.getLogger(__name__)

_SESSION_SERVICE: DatabaseSessionService | None = None


def get_session_service() -> DatabaseSessionService:
    """Return a process-wide SQLite-backed ADK session service."""
    global _SESSION_SERVICE
    if _SESSION_SERVICE is None:
        db_url = config.ADK_SESSION_DB_URL
        if db_url.startswith("sqlite:///"):
            db_path = db_url.replace("sqlite:///", "", 1)
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        logger.info("Initializing local ADK DatabaseSessionService at %s", db_url)
        _SESSION_SERVICE = DatabaseSessionService(db_url=db_url)
    return _SESSION_SERVICE


def resolve_app_name(app_name: str | None = None) -> str:
    """Resolve a local app name (replaces a Vertex reasoning-engine resource id)."""
    return (app_name or "").strip() or LOCAL_APP_NAME


def VertexAiSessionService(*args, **kwargs):  # noqa: N802 - drop-in name
    """Drop-in replacement for ADK's ``VertexAiSessionService``.

    Ignores Vertex constructor args (project/location/agent_engine_id) and returns
    the shared local SQLite-backed session service. Lets the ~13 call sites that do
    ``VertexAiSessionService(...)`` work unchanged after repointing the import.
    """
    return get_session_service()
