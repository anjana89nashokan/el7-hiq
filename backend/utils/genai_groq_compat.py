"""Groq-backed stand-in for ``google.genai.Client``.

When ``config.LLM_PROVIDER == "groq"``, ``genai.Client(...)`` (centrally patched in
config/settings.py) returns ``GroqGenaiCompatClient`` from here instead of a real
Gemini client. It mimics the small slice of the genai client surface the app uses —
``client.models.generate_content`` / ``generate_content_stream`` / ``count_tokens``
and their ``client.aio.*`` async variants — and routes to Groq via ``litellm``.

This lets every ``genai.Client(...)`` call site work with Groq unchanged. Gemini-only
features (context caching via ``client.caches``) degrade gracefully.
"""

from __future__ import annotations

import logging

from config.settings import config as settings_config

logger = logging.getLogger(__name__)


class _Chunk:
    def __init__(self, text: str):
        self.text = text


class _Response:
    def __init__(self, text: str):
        self.text = text
        self.parsed = None
        self.candidates = []


def _part_text(c) -> str:
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    parts = getattr(c, "parts", None)
    if parts:
        return "\n".join((getattr(p, "text", "") or "") for p in parts)
    return getattr(c, "text", "") or ""


def _to_text(contents) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, (list, tuple)):
        return "\n".join(t for t in (_part_text(c) for c in contents) if t)
    return _part_text(contents)


def _to_messages(contents, system_instruction=None):
    msgs = []
    sys_text = _to_text(system_instruction) if system_instruction else ""
    if sys_text:
        msgs.append({"role": "system", "content": sys_text})
    msgs.append({"role": "user", "content": _to_text(contents)})
    return msgs


def _extract_cfg(gen_config):
    temperature = None
    system_instruction = None
    json_mode = False
    max_tokens = None
    if gen_config is not None:
        temperature = getattr(gen_config, "temperature", None)
        system_instruction = getattr(gen_config, "system_instruction", None)
        mime = getattr(gen_config, "response_mime_type", None)
        if mime and "json" in str(mime).lower():
            json_mode = True
        max_tokens = getattr(gen_config, "max_output_tokens", None)
    return temperature, system_instruction, json_mode, max_tokens


def _model_name() -> str:
    import os

    provider = (
        os.getenv("LLM_PROVIDER") or settings_config.LLM_PROVIDER or "gemini"
    ).lower()
    if provider == "openai":
        model = (os.getenv("OPENAI_MODEL") or getattr(settings_config, "OPENAI_MODEL", "") or "gpt-4o-mini").strip() or "gpt-4o-mini"
        return model if str(model).startswith("openai/") else f"openai/{model}"
    return f"groq/{settings_config.GROQ_MODEL}"


def _build_kwargs(gen_config):
    temperature, system_instruction, json_mode, max_tokens = _extract_cfg(gen_config)
    kwargs = {}
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    if max_tokens:
        kwargs["max_tokens"] = int(max_tokens)
    elif getattr(settings_config, "GROQ_MAX_TOKENS", 0):
        # Cap the output reservation when the caller didn't specify one.
        kwargs["max_tokens"] = int(settings_config.GROQ_MAX_TOKENS)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    chain = getattr(settings_config, "LLM_FALLBACKS", None)
    if chain:
        kwargs["fallbacks"] = list(chain)
    return kwargs, system_instruction


class _Models:
    def generate_content(self, model=None, contents=None, config=None, **_):
        from litellm import completion

        kwargs, sys_inst = _build_kwargs(config)
        resp = completion(
            model=_model_name(),
            messages=_to_messages(contents, sys_inst),
            **kwargs,
        )
        return _Response(resp.choices[0].message.content or "")

    def generate_content_stream(self, model=None, contents=None, config=None, **_):
        from litellm import completion

        kwargs, sys_inst = _build_kwargs(config)
        stream = completion(
            model=_model_name(),
            messages=_to_messages(contents, sys_inst),
            stream=True,
            **kwargs,
        )
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content
            except Exception:  # noqa: BLE001
                delta = None
            if delta:
                yield _Chunk(delta)

    def count_tokens(self, model=None, contents=None, **_):
        # Rough estimate (~4 chars/token); Groq does not expose a count endpoint.
        n = max(1, len(_to_text(contents)) // 4)

        class _TC:
            total_tokens = n

        return _TC()


class _AsyncModels:
    async def generate_content(self, model=None, contents=None, config=None, **_):
        from litellm import acompletion

        kwargs, sys_inst = _build_kwargs(config)
        resp = await acompletion(
            model=_model_name(),
            messages=_to_messages(contents, sys_inst),
            **kwargs,
        )
        return _Response(resp.choices[0].message.content or "")

    async def generate_content_stream(self, model=None, contents=None, config=None, **_):
        from litellm import acompletion

        kwargs, sys_inst = _build_kwargs(config)
        stream = await acompletion(
            model=_model_name(),
            messages=_to_messages(contents, sys_inst),
            stream=True,
            **kwargs,
        )

        async def _gen():
            async for chunk in stream:
                try:
                    delta = chunk.choices[0].delta.content
                except Exception:  # noqa: BLE001
                    delta = None
                if delta:
                    yield _Chunk(delta)

        return _gen()


class _Aio:
    def __init__(self):
        self.models = _AsyncModels()


class _CachesStub:
    def create(self, *a, **k):
        raise NotImplementedError("Context caching is not supported with the Groq provider.")

    def get(self, *a, **k):
        return None

    def delete(self, *a, **k):
        return None

    def list(self, *a, **k):
        return []


class GroqGenaiCompatClient:
    """Minimal genai.Client stand-in backed by Groq (via litellm)."""

    def __init__(self, *args, **kwargs):
        self.models = _Models()
        self.aio = _Aio()
        self.caches = _CachesStub()
