"""
RowTextRegeneratorAgent (Step 4) - LLM-based text regeneration with caching.

Responsibilities:
  - Rewrite text-only fields (transformation/filter/special considerations) based on current row state.
  - Must not introduce new identifiers outside an allowlist (enforced downstream).
"""

from __future__ import annotations

import asyncio
import json

from google.adk import Runner
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App
from utils.adk_runtime import VertexAiSessionService
from google.genai import types

from config.settings import config

from .models import RowTextRegenBatchOutput, RowTextRegenBatchRequest
from .prompts import get_row_text_regenerator_prompt


def _get_agent_engine_id() -> str | None:
    resource = getattr(config, "REASONING_ENGINE_RESOURCE", None)
    if not resource:
        return None
    resource = resource.strip()
    if not resource:
        return None
    return resource.split("/")[-1]


def _context_cache_config() -> ContextCacheConfig | None:
    if not bool(getattr(config, "STEP4_TEXT_REGEN_CONTEXT_CACHE_ENABLED", True)):
        return None
    return ContextCacheConfig(
        min_tokens=max(0, int(getattr(config, "STEP4_TEXT_REGEN_CONTEXT_CACHE_MIN_TOKENS", 4096))),
        ttl_seconds=max(1, int(getattr(config, "STEP4_TEXT_REGEN_CONTEXT_CACHE_TTL_SECONDS", 1800))),
        cache_intervals=max(1, int(getattr(config, "STEP4_TEXT_REGEN_CONTEXT_CACHE_INTERVALS", 10))),
    )


row_text_regenerator_llm_agent = LlmAgent(
    name="step4_row_text_regenerator_llm_agent",
    model=config.AGENT_MODEL,
    description="Regenerates mapping row text fields (structured output).",
    instruction=get_row_text_regenerator_prompt(),
    output_schema=RowTextRegenBatchOutput,
    output_key="row_text_regen",
)


class _StructuredTool:
    def __init__(self) -> None:
        self._app = App(
            name="step4_row_text_regenerator_app",
            root_agent=row_text_regenerator_llm_agent,
            context_cache_config=_context_cache_config(),
        )
        self._session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
            agent_engine_id=_get_agent_engine_id(),
        )
        self._runner = Runner(app=self._app, session_service=self._session_service)
        self._session_id: str | None = None

    async def _ensure_session_id(self) -> str | None:
        if self._session_id:
            return self._session_id

        call_timeout = max(0, int(getattr(config, "STEP4_TEXT_REGEN_LLM_CALL_TIMEOUT_SEC", 120)))

        async def _create() -> str:
            session = await self._session_service.create_session(app_name=self._app.name, user_id="system", state={})
            return session.id

        try:
            session_id = await asyncio.wait_for(_create(), timeout=call_timeout) if call_timeout else await _create()
        except Exception:
            return None

        self._session_id = session_id
        return session_id

    async def call(self, request: RowTextRegenBatchRequest) -> RowTextRegenBatchOutput | None:
        if not bool(getattr(config, "STEP4_TEXT_REGEN_ENABLED", True)):
            return None

        msg = types.Content(
            role="user",
            parts=[types.Part(text=f"INPUT_JSON:\n{json.dumps(request.model_dump(), indent=2)}")],
        )

        call_timeout = max(0, int(getattr(config, "STEP4_TEXT_REGEN_LLM_CALL_TIMEOUT_SEC", 120)))
        max_retries = max(0, int(getattr(config, "STEP4_TEXT_REGEN_MAX_RETRIES", 1)))

        async def _run_with_session(session_id: str) -> RowTextRegenBatchOutput | None:
            async for event in self._runner.run_async(user_id="system", session_id=session_id, new_message=msg):
                if hasattr(event, "actions") and event.actions and getattr(event.actions, "state_delta", None):
                    delta = event.actions.state_delta
                    if "row_text_regen" in delta:
                        raw = delta["row_text_regen"]
                        if isinstance(raw, RowTextRegenBatchOutput):
                            return raw
                        if isinstance(raw, dict):
                            return RowTextRegenBatchOutput.model_validate(raw)
            return None

        attempt = 0
        while True:
            session_id = await self._ensure_session_id()
            if not session_id:
                return None

            try:
                out = await asyncio.wait_for(_run_with_session(session_id), timeout=call_timeout) if call_timeout else await _run_with_session(session_id)
            except Exception:
                out = None

            if out is not None:
                return out

            if attempt >= max_retries:
                return None

            # Retry with a fresh session id (handles transient session failures).
            self._session_id = None
            attempt += 1


async def run_row_text_regenerator_agent(*, request: RowTextRegenBatchRequest) -> list:
    global _TOOL  # noqa: PLW0603
    try:
        tool = _TOOL
    except NameError:
        _TOOL = _StructuredTool()
        tool = _TOOL

    out = await tool.call(request)
    if not out:
        return []
    return list(out.plans or [])


row_text_regenerator_agent = SequentialAgent(
    name="row_text_regenerator_agent",
    sub_agents=[],
    description="Step 4 sub-agent: regenerate row text fields for readability (LLM structured output).",
)


__all__ = ["row_text_regenerator_agent", "run_row_text_regenerator_agent"]
