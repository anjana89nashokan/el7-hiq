"""LiteLlm subclass that unwraps markdown-fenced JSON from model output.

ADK agents that declare ``output_schema=...`` parse the model's text as strict
JSON. Gemini honors structured output natively, but non-Gemini models (Groq/
OpenAI-style via litellm) frequently wrap the JSON in a ```json … ``` fence,
which breaks ADK's parse. This subclass strips a single surrounding code fence
from each response so those agents work across providers — no agent-code changes.
"""

from __future__ import annotations

import json
import os
import re
from typing import AsyncGenerator

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.models.llm_request import LlmRequest


def _msg_role(message) -> str | None:
    if isinstance(message, dict):
        return message.get("role")
    return getattr(message, "role", None)


def _msg_tool_calls(message):
    if isinstance(message, dict):
        return message.get("tool_calls") or []
    return getattr(message, "tool_calls", None) or []


def _tool_call_id(tool_call) -> str | None:
    if isinstance(tool_call, dict):
        return tool_call.get("id")
    return getattr(tool_call, "id", None)


def _tool_message_id(message) -> str | None:
    if isinstance(message, dict):
        return message.get("tool_call_id")
    return getattr(message, "tool_call_id", None)


def repair_openai_tool_call_sequence(messages):
    """OpenAI requires each assistant tool_call to have a matching tool message."""
    if not messages:
        return messages

    repaired = []
    for i, message in enumerate(messages):
        repaired.append(message)
        tool_calls = _msg_tool_calls(message)
        if _msg_role(message) != "assistant" or not tool_calls:
            continue

        expected_ids = [tid for tid in (_tool_call_id(tc) for tc in tool_calls) if tid]
        if not expected_ids:
            continue

        responded: set[str] = set()
        j = i + 1
        while j < len(messages) and _msg_role(messages[j]) == "tool":
            tid = _tool_message_id(messages[j])
            if tid:
                responded.add(tid)
            j += 1

        for tid in expected_ids:
            if tid not in responded:
                repaired.append(
                    {
                        "role": "tool",
                        "tool_call_id": tid,
                        "content": json.dumps({"status": "ok"}),
                    }
                )
    return repaired


def apply_openai_tool_call_repair_patch() -> None:
    from google.adk.models import lite_llm as adk_lite_llm

    if getattr(adk_lite_llm, "_ust_tool_repair_patched", False):
        return

    original_get_completion_inputs = adk_lite_llm._get_completion_inputs

    def _get_completion_inputs_with_repair(llm_request):
        messages, tools, response_format, generation_params = original_get_completion_inputs(
            llm_request
        )
        model = str(getattr(llm_request, "model", "") or "")
        provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
        if model.startswith("openai/") or provider == "openai":
            messages = repair_openai_tool_call_sequence(messages)
        return messages, tools, response_format, generation_params

    adk_lite_llm._get_completion_inputs = _get_completion_inputs_with_repair
    adk_lite_llm._ust_tool_repair_patched = True


def apply_openai_tool_call_args_patch() -> None:
    """Avoid hard failures when OpenAI truncates large tool-call JSON payloads."""
    from google.adk.models import lite_llm as adk_lite_llm

    if getattr(adk_lite_llm, "_ust_tool_args_patched", False):
        return

    original_message_to_response = adk_lite_llm._message_to_generate_content_response

    def _message_to_generate_content_response(message, *, is_partial=False, model_version=None):
        if not message.get("tool_calls", None):
            return original_message_to_response(
                message, is_partial=is_partial, model_version=model_version
            )

        safe_message = dict(message)
        safe_tool_calls = []
        for tool_call in message.get("tool_calls") or []:
            if getattr(tool_call, "type", None) != "function":
                safe_tool_calls.append(tool_call)
                continue
            raw_args = getattr(getattr(tool_call, "function", None), "arguments", None) or "{}"
            try:
                json.loads(raw_args)
                safe_tool_calls.append(tool_call)
            except json.JSONDecodeError:
                safe_tool_calls.append(
                    type(tool_call)(
                        id=getattr(tool_call, "id", None),
                        type=getattr(tool_call, "type", "function"),
                        function=type(tool_call.function)(
                            name=getattr(tool_call.function, "name", ""),
                            arguments="{}",
                        ),
                    )
                )
        safe_message["tool_calls"] = safe_tool_calls
        return original_message_to_response(
            safe_message, is_partial=is_partial, model_version=model_version
        )

    adk_lite_llm._message_to_generate_content_response = _message_to_generate_content_response
    adk_lite_llm._ust_tool_args_patched = True


apply_openai_tool_call_repair_patch()
apply_openai_tool_call_args_patch()

def _strip_fence(text: str) -> str:
    """Best-effort: return the bare JSON from a model response that may be wrapped
    in a ```json fence, a leading 'json' language tag, or surrounding prose."""
    if not isinstance(text, str):
        return text
    t = text.strip()
    # drop a leading code fence ``` optionally followed by a language tag
    t = re.sub(r"^```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n?", "", t)
    # drop a trailing code fence
    t = re.sub(r"\r?\n?```[ \t]*$", "", t)
    t = t.strip()
    # drop a leading bare language tag line (e.g. 'json' / 'JSON')
    t = re.sub(r"^(?:json|JSON)\b[ \t]*\r?\n", "", t).strip()
    # final fallback: slice to the outermost JSON object/array
    if t and t[0] not in "{[":
        starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
        if starts:
            start = min(starts)
            end = max(t.rfind("}"), t.rfind("]"))
            if end > start:
                t = t[start : end + 1]
    return t.strip()


class FenceStrippingLiteLlm(LiteLlm):
    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        async for resp in super().generate_content_async(llm_request, stream=stream):
            try:
                content = getattr(resp, "content", None)
                if content and getattr(content, "parts", None):
                    for part in content.parts:
                        if getattr(part, "text", None):
                            part.text = _strip_fence(part.text)
            except Exception:  # noqa: BLE001 - never break the stream over cleanup
                pass
            yield resp
