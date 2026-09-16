"""STTM / DataMap API router, mounted by Launchpad at /sttm/api."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends

from services.auth_dependencies import get_current_user
from services.credential_service import get_provider_api_key, get_provider_raw_credential
from services.database_service import db

# STTM modules use top-level imports (api.*, db.*, utils.*) relative to this tree.
_STTM_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_ROOT = _STTM_ROOT.parent

# Package names that exist both under backend/ and backend/sttm/.
_STTM_PACKAGE_NAMES = frozenset(
    {"api", "agents", "config", "db", "utils", "models", "judges", "quality", "tools"}
)


def _activate_sttm_import_path() -> None:
    """Prefer STTM packages over same-named Launchpad backend modules."""
    sttm_root = str(_STTM_ROOT)
    sys.path = [p for p in sys.path if p != sttm_root]
    sys.path.insert(0, sttm_root)

    backend_root = str(_BACKEND_ROOT)
    for name, mod in list(sys.modules.items()):
        root = name.split(".", 1)[0]
        if root not in _STTM_PACKAGE_NAMES:
            continue
        mod_file = (getattr(mod, "__file__", None) or "").replace("\\", "/")
        if mod_file and backend_root in mod_file and sttm_root not in mod_file:
            del sys.modules[name]


_activate_sttm_import_path()

from utils.adk_llm_patch import apply_sttm_llm_routing_patch  # noqa: E402

apply_sttm_llm_routing_patch()

from api.routers import (  # noqa: E402
    dart_suggestion,
    data,
    doc_extraction,
    evidencehub,
    extract_driver,
    extracts,
    files,
    graphs,
    hl7,
    indemap,
    logs,
    mapping,
    messages,
    messages_stream,
    messages_stream_new,
    quality,
    sessions,
    settings as settings_router,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

logger = logging.getLogger(__name__)


async def _try_provider_key(user_id: str, provider: str) -> str:
    try:
        return await get_provider_api_key(db, user_id, provider, require_enabled=True)
    except Exception:  # noqa: BLE001 - missing/disabled key is not fatal for non-LLM routes
        return ""


async def _bind_user_llm_key(current_user: dict = Depends(get_current_user)) -> None:
    """Bind the signed-in user's Launchpad LLM key (Gemini or OpenAI) for STTM."""
    from config.settings import config as sttm_config

    user_id = str(current_user.get("_id") or "")
    google_key = await _try_provider_key(user_id, "google") if user_id else ""
    openai_key = ""
    openai_model = sttm_config.OPENAI_MODEL

    if user_id:
        try:
            cred = await get_provider_raw_credential(
                db, user_id, "openai", require_enabled=True
            )
            openai_key = (cred.get("api_key") or "").strip()
            if cred.get("model"):
                openai_model = str(cred["model"]).strip() or openai_model
        except Exception:  # noqa: BLE001
            openai_key = await _try_provider_key(user_id, "openai")

    if openai_key:
        _apply_sttm_openai_key(sttm_config, openai_key, openai_model)
        logger.debug(
            "Bound OpenAI key for STTM user %s (last4=%s, model=%s)",
            current_user.get("email") or user_id,
            openai_key[-4:] if len(openai_key) >= 4 else "????",
            openai_model,
        )
        return

    if google_key:
        _apply_sttm_gemini_key(sttm_config, google_key)
        logger.debug(
            "Bound Gemini key for STTM user %s (last4=%s)",
            current_user.get("email") or user_id,
            google_key[-4:] if len(google_key) >= 4 else "????",
        )
        return

    env_openai = (os.getenv("OPENAI_API_KEY", "") or "").strip()
    if env_openai:
        _apply_sttm_openai_key(sttm_config, env_openai, sttm_config.OPENAI_MODEL)
        return

    env_google = (os.getenv("GOOGLE_API_KEY", "") or "").strip()
    if env_google:
        _apply_sttm_gemini_key(sttm_config, env_google)
        return

    env_groq = (os.getenv("GROQ_API_KEY", "") or "").strip()
    if env_groq:
        _apply_sttm_groq_key(sttm_config, env_groq)
        return

    _clear_sttm_llm_keys(sttm_config)
    logger.warning(
        "No enabled LLM API key for STTM user %s — configure Gemini or OpenAI in "
        "Settings → LLM API (in-memory Mongo loses keys on backend restart).",
        current_user.get("email") or user_id,
    )


def _clear_sttm_llm_keys(sttm_config) -> None:
    sttm_config.GOOGLE_API_KEY = ""
    sttm_config.OPENAI_API_KEY = ""
    sttm_config.LLM_PROVIDER = "gemini"
    os.environ.pop("GOOGLE_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["LLM_PROVIDER"] = "gemini"


def _apply_sttm_gemini_key(sttm_config, key: str) -> None:
    sttm_config.GOOGLE_API_KEY = key
    sttm_config.OPENAI_API_KEY = ""
    sttm_config.LLM_PROVIDER = "gemini"
    os.environ["GOOGLE_API_KEY"] = key
    os.environ["GEMINI_API_KEY"] = key
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
    os.environ["LLM_PROVIDER"] = "gemini"
    os.environ.pop("OPENAI_API_KEY", None)


def _apply_sttm_openai_key(sttm_config, key: str, model: str) -> None:
    sttm_config.OPENAI_API_KEY = key
    sttm_config.OPENAI_MODEL = model or "gpt-4o-mini"
    sttm_config.GOOGLE_API_KEY = ""
    sttm_config.LLM_PROVIDER = "openai"
    os.environ["OPENAI_API_KEY"] = key
    os.environ["OPENAI_MODEL"] = sttm_config.OPENAI_MODEL
    os.environ["LLM_PROVIDER"] = "openai"
    os.environ.pop("GOOGLE_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)


def _apply_sttm_groq_key(sttm_config, key: str) -> None:
    sttm_config.GROQ_API_KEY = key
    sttm_config.GOOGLE_API_KEY = ""
    sttm_config.OPENAI_API_KEY = ""
    sttm_config.LLM_PROVIDER = "groq"
    os.environ["GROQ_API_KEY"] = key
    os.environ["LLM_PROVIDER"] = "groq"
    os.environ.pop("GOOGLE_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)


router = APIRouter(dependencies=[Depends(_bind_user_llm_key)])

router.include_router(sessions.router, prefix="/sessions", tags=["sttm-sessions"])
router.include_router(files.router, prefix="/files", tags=["sttm-files"])
router.include_router(messages.router, prefix="/messages", tags=["sttm-messages"])
router.include_router(
    messages_stream.router, prefix="/messages-strm", tags=["sttm-messages-stream"]
)
router.include_router(
    messages_stream_new.router,
    prefix="/messages-strm",
    tags=["sttm-messages-stream-new"],
)
router.include_router(logs.router, prefix="/logs", tags=["sttm-logs"])
router.include_router(mapping.router, prefix="/mapping", tags=["sttm-mapping"])
router.include_router(evidencehub.router, prefix="/evidencehub", tags=["sttm-evidencehub"])
router.include_router(graphs.router, prefix="/graphs", tags=["sttm-graphs"])
router.include_router(data.router, prefix="/data", tags=["sttm-data"])
router.include_router(indemap.router, tags=["sttm-indemap"])
router.include_router(
    dart_suggestion.router, prefix="/dart", tags=["sttm-dart-suggestion"]
)
router.include_router(
    extract_driver.router, prefix="/extract", tags=["sttm-extract-driver"]
)
router.include_router(
    doc_extraction.router, prefix="/doc", tags=["sttm-doc-extraction"]
)
router.include_router(extracts.router, prefix="/extracts", tags=["sttm-extracts"])
router.include_router(quality.router, prefix="/quality", tags=["sttm-quality"])
router.include_router(settings_router.router, prefix="/settings", tags=["sttm-settings"])
router.include_router(hl7.router, prefix="/hl7", tags=["sttm-hl7"])


@router.get("/health", tags=["sttm-system"])
def health():
    return {
        "status": "ok",
        "service": "sttm-datamap",
        "timestamp": datetime.utcnow().isoformat(),
        "modules": {
            "profiling": "ready",
            "mapping": "ready",
            "hl7_fhir": "ready",
        },
    }


def init_sttm_db() -> None:
    """Create STTM SQLite tables on Launchpad startup."""
    log = logging.getLogger(__name__)
    try:
        from config.settings import config
        from db.engine import get_app_engine, get_session_factory, init_db

        Path(config.APP_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        get_app_engine.cache_clear()
        get_session_factory.cache_clear()
        init_db()
        log.info("[sttm] app DB initialized at %s", config.APP_DB_PATH)
    except Exception as exc:  # noqa: BLE001
        log.error("[sttm] app DB init failed: %s", exc, exc_info=True)
