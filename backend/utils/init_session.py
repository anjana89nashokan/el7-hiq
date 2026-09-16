"""Standalone session bootstrap (no GCP / Vertex Reasoning Engine).

Previously this created/loaded a Vertex AI Reasoning Engine via ``vertexai`` and
``vertexai.agent_engines``. In standalone mode there is no remote engine: ADK runs
locally with a SQLite-backed session store (see ``utils.adk_runtime``). This shim
keeps the old ``InitSession`` API so existing call sites continue to work, but it
no longer touches GCP — ``initialize_session()`` just returns a local handle whose
``resource_name`` is a stable local app name.
"""

import logging

from config.settings import config

logger = logging.getLogger(__name__)

# Stable local "app name" used in place of a Vertex reasoning-engine resource id.
LOCAL_APP_NAME = "datamap_local"


class _LocalEngineHandle:
    """Minimal stand-in for a Vertex agent-engine object."""

    def __init__(self, app_name: str = LOCAL_APP_NAME):
        self.resource_name = app_name
        self.name = app_name


class InitSession:
    def initialize_session(self):
        app_name = (getattr(config, "REASONING_ENGINE_RESOURCE", None) or LOCAL_APP_NAME)
        logger.info("Standalone session init (no GCP). app_name=%s", app_name)
        return _LocalEngineHandle(app_name)

    def delete_session(self, resource_name: str):
        # No remote engine to delete in standalone mode.
        logger.info("Standalone delete_session no-op for %s", resource_name)
        return {"status": "noop", "resource_name": resource_name}
