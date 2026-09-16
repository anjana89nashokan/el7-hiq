"""
PatchAndResolveAgent (Step 4) - issue-centric resolution with structured LLM output.

Responsibilities:
  - Build issue-centric resolution plans using only:
      * Step 2 open_issues details
      * BSA answers (free text)
      * BSA feedback (free text)
      * current row state (after row-level intent plan applied)
  - Produce structured IssuePlan objects + row-level InterpretationPlans to apply deterministically.

Constraints (hard):
  - No target-side changes.
  - No hallucinated identifiers: any proposed identifier must be backed by verbatim EvidenceSpans.
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

from .models import IssuePlanBatchOutput, IssuePlanBatchRequest
from .prompts import get_patch_and_resolve_prompt


def _get_agent_engine_id() -> str | None:
    resource = getattr(config, "REASONING_ENGINE_RESOURCE", None)
    if not resource:
        return None
    resource = resource.strip()
    if not resource:
        return None
    return resource.split("/")[-1]


def _context_cache_config() -> ContextCacheConfig | None:
    if not bool(getattr(config, "STEP4_CONTEXT_CACHE_ENABLED", True)):
        return None
    return ContextCacheConfig(
        min_tokens=max(0, int(getattr(config, "STEP4_CONTEXT_CACHE_MIN_TOKENS", 4096))),
        ttl_seconds=max(1, int(getattr(config, "STEP4_CONTEXT_CACHE_TTL_SECONDS", 1800))),
        cache_intervals=max(1, int(getattr(config, "STEP4_CONTEXT_CACHE_INTERVALS", 10))),
    )


patch_and_resolve_llm_agent = LlmAgent(
    name="step4_patch_and_resolve_llm_agent",
    model=config.AGENT_MODEL,
    description="Resolves Step 2 issues from BSA answers/feedback (structured output).",
    instruction=get_patch_and_resolve_prompt(),
    output_schema=IssuePlanBatchOutput,
    output_key="issue_plans",
)


class _StructuredTool:
    def __init__(self) -> None:
        self._app = App(
            name="step4_patch_and_resolve_app",
            root_agent=patch_and_resolve_llm_agent,
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

        call_timeout = max(0, int(getattr(config, "STEP4_LLM_CALL_TIMEOUT_SEC", 120)))

        async def _create() -> str:
            session = await self._session_service.create_session(app_name=self._app.name, user_id="system", state={})
            return session.id

        try:
            session_id = await asyncio.wait_for(_create(), timeout=call_timeout) if call_timeout else await _create()
        except Exception:
            return None

        self._session_id = session_id
        return session_id

    async def call(self, request: IssuePlanBatchRequest) -> IssuePlanBatchOutput | None:
        if not bool(getattr(config, "STEP4_LLM_ENABLED", True)):
            return None

        session_id = await self._ensure_session_id()
        if not session_id:
            return None

        msg = types.Content(
            role="user",
            parts=[types.Part(text=f"INPUT_JSON:\n{json.dumps(request.model_dump(), indent=2)}")],
        )

        call_timeout = max(0, int(getattr(config, "STEP4_LLM_CALL_TIMEOUT_SEC", 120)))

        async def _run_once() -> IssuePlanBatchOutput | None:
            async for event in self._runner.run_async(user_id="system", session_id=session_id, new_message=msg):
                if hasattr(event, "actions") and event.actions and getattr(event.actions, "state_delta", None):
                    delta = event.actions.state_delta
                    if "issue_plans" in delta:
                        raw = delta["issue_plans"]
                        if isinstance(raw, IssuePlanBatchOutput):
                            return raw
                        if isinstance(raw, dict):
                            return IssuePlanBatchOutput.model_validate(raw)
            return None

        try:
            return await asyncio.wait_for(_run_once(), timeout=call_timeout) if call_timeout else await _run_once()
        except Exception:
            return None


async def run_patch_and_resolve_agent(*, request: IssuePlanBatchRequest) -> list:
    # Reuse a single tool/session per process to maximize prompt/context caching benefits.
    global _TOOL  # noqa: PLW0603
    try:
        tool = _TOOL
    except NameError:
        _TOOL = _StructuredTool()
        tool = _TOOL

    out = await tool.call(request)
    if not out:
        return []
    return list(out.issue_plans or [])


patch_and_resolve_agent = SequentialAgent(
    name="patch_and_resolve_agent",
    sub_agents=[],
    description="Step 4 sub-agent: build issue resolution plans (LLM structured output).",
)

__all__ = ["patch_and_resolve_agent", "run_patch_and_resolve_agent"]
