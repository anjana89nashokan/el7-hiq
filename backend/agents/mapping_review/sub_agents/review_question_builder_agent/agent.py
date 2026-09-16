"""
ReviewQuestionBuilderAgent (Step 3) - the only Step 3 sub-agent.

Responsibilities:
  - Triage review scope (what needs review) from Step2State rows/issues/candidates
  - Deduplicate and prioritize questions
  - Assemble UI-ready ReviewQuestion objects
  - Optional LLM wordsmithing with caching (helper-only; no new schema)
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

from agents.mapping_generation.models import (
    OpenIssue,
    Step2State,
)
from agents.mapping_review.models import ReviewQuestion
from config.settings import config
from utils.review_question_builder_utils import (
    build_review_questions_deterministic,
    question_from_issue,
    sort_questions,
)

from .models import (
    QuestionWordsmithBatchOutput,
    QuestionWordsmithBatchRequest,
    QuestionWordsmithInput,
)
from .prompts import get_question_wordsmith_prompt


def _get_agent_engine_id() -> str | None:
    resource = getattr(config, "REASONING_ENGINE_RESOURCE", None)
    if not resource:
        return None
    resource = resource.strip()
    if not resource:
        return None
    return resource.split("/")[-1]


def _context_cache_config() -> ContextCacheConfig | None:
    if not bool(getattr(config, "STEP3_CONTEXT_CACHE_ENABLED", True)):
        return None
    return ContextCacheConfig(
        min_tokens=max(0, int(getattr(config, "STEP3_CONTEXT_CACHE_MIN_TOKENS", 2048))),
        ttl_seconds=max(1, int(getattr(config, "STEP3_CONTEXT_CACHE_TTL_SECONDS", 1800))),
        cache_intervals=max(1, int(getattr(config, "STEP3_CONTEXT_CACHE_INTERVALS", 10))),
    ) 


question_wordsmith_agent = LlmAgent(
    name="step3_question_wordsmith_agent",
    model=config.AGENT_MODEL,
    description="Wordsmith Step 3 review questions (structured output).",
    instruction=get_question_wordsmith_prompt(),
    output_schema=QuestionWordsmithBatchOutput,
    output_key="question_wordsmith",
)


class _StructuredTool:
    def __init__(self) -> None:
        self._app = App(
            name="step3_question_wordsmith_app",
            root_agent=question_wordsmith_agent,
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

        call_timeout = max(0, int(getattr(config, "STEP3_LLM_CALL_TIMEOUT_SEC", 60)))

        async def _create() -> str:
            session = await self._session_service.create_session(app_name=self._app.name, user_id="system", state={})
            return session.id

        try:
            session_id = await asyncio.wait_for(_create(), timeout=call_timeout) if call_timeout else await _create()
        except Exception:
            return None

        self._session_id = session_id
        return session_id

    async def call(self, request: QuestionWordsmithBatchRequest) -> QuestionWordsmithBatchOutput | None:
        if not bool(getattr(config, "STEP3_LLM_ENABLED", True)):
            return None

        session_id = await self._ensure_session_id()
        if not session_id:
            return None

        msg = types.Content(
            role="user",
            parts=[types.Part(text=f"INPUT_JSON:\n{json.dumps(request.model_dump(), indent=2)}")],
        )

        call_timeout = max(0, int(getattr(config, "STEP3_LLM_CALL_TIMEOUT_SEC", 60)))

        async def _run_once() -> QuestionWordsmithBatchOutput | None:
            async for event in self._runner.run_async(user_id="system", session_id=session_id, new_message=msg):
                if hasattr(event, "actions") and event.actions and getattr(event.actions, "state_delta", None):
                    delta = event.actions.state_delta
                    if "question_wordsmith" in delta:
                        raw = delta["question_wordsmith"]
                        if isinstance(raw, QuestionWordsmithBatchOutput):
                            return raw
                        if isinstance(raw, dict):
                            return QuestionWordsmithBatchOutput.model_validate(raw)
            return None

        try:
            return await asyncio.wait_for(_run_once(), timeout=call_timeout) if call_timeout else await _run_once()
        except Exception:
            return None


async def _wordsmith_questions(
    questions: list[ReviewQuestion],
    *,
    issue_lookup: dict[str, OpenIssue],
    enabled: bool,
) -> list[ReviewQuestion]:
    if not enabled:
        return questions

    items: list[QuestionWordsmithInput] = []
    for q in questions:
        if not q.target_column:
            continue
        issue_messages = [issue_lookup[i].message for i in q.issue_ids if i in issue_lookup]
        items.append(
            QuestionWordsmithInput(
                question_id=q.question_id,
                priority=q.priority.value,
                kind=q.kind.value,
                target_table_id=q.target_column.entity_id,
                target_column_name=q.target_column.column_name,
                baseline_question_text=q.question_text,
                baseline_context_summary=q.context_summary,
                issue_messages=issue_messages,
                option_labels=[o.label for o in q.options],
            )
        )

    if not items:
        return questions

    tool = _StructuredTool()
    out = await tool.call(QuestionWordsmithBatchRequest(items=items))
    if not out:
        return questions

    by_id = {i.question_id: i for i in out.items}
    rewritten: list[ReviewQuestion] = []
    for q in questions:
        item = by_id.get(q.question_id)
        if not item:
            rewritten.append(q)
            continue
        rewritten.append(
            q.model_copy(
                update={
                    "question_text": item.question_text.strip() if item.question_text else q.question_text,
                    "context_summary": (item.context_summary.strip() if item.context_summary else q.context_summary),
                }
            )
        )
    return rewritten


async def run_review_question_builder_agent(*, step2_state: Step2State) -> list[ReviewQuestion]:
    issue_lookup = {i.issue_id: i for i in step2_state.open_issues}

    questions = build_review_questions_deterministic(step2_state=step2_state)

    # 3) Optional wordsmithing pass (helper-only).
    wordsmith_enabled = bool(getattr(config, "STEP3_LLM_WORDSMITH_ENABLED", True))
    questions = await _wordsmith_questions(questions, issue_lookup=issue_lookup, enabled=wordsmith_enabled)

    # Safety net: when Step 2 has issues, Step 3 must return actionable questions.
    if not questions and step2_state.open_issues:
        fallback_questions: list[ReviewQuestion] = [
            question_from_issue(step2_state=step2_state, issue=issue)
            for issue in step2_state.open_issues
        ]
        deduped: dict[str, ReviewQuestion] = {}
        for q in fallback_questions:
            deduped.setdefault(q.question_id, q)
        questions = sort_questions(list(deduped.values()))

    return questions


# ADK structural agent (wiring only; runtime uses run_review_question_builder_agent).
review_question_builder_agent = SequentialAgent(
    name="review_question_builder_agent",
    sub_agents=[],
    description="Step 3 sub-agent exposed via run_review_question_builder_agent(step2_state).",
)


__all__ = ["review_question_builder_agent", "run_review_question_builder_agent"]

