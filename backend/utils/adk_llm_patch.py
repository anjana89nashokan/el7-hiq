"""Force ADK + genai routing for STTM (OpenAI / Groq / Gemini BYOK).

Launchpad loads DataMap before STTM; DataMap may install an ADK patch that only
knows Groq/Gemini. STTM profiling must honor OpenAI keys from Settings, so we
re-apply the patch on STTM startup (always, not gated on _ust_model_patched).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def apply_sttm_llm_routing_patch() -> None:
    from config.settings import config

    # Ensure OpenAI tool-call history repair is active before ADK runs.
    from utils import litellm_json  # noqa: F401

    _patch_llm_agent(config)
    _patch_genai_client(config)
    logger.info(
        "STTM ADK LLM routing patch applied (provider=%s, openai=%s, gemini=%s)",
        config.LLM_PROVIDER,
        "set" if (config.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")) else "unset",
        "set" if (config.GOOGLE_API_KEY or os.getenv("GOOGLE_API_KEY")) else "unset",
    )


def _active_provider(config) -> str:
    return (
        os.getenv("LLM_PROVIDER") or config.LLM_PROVIDER or "gemini"
    ).strip().lower()


def _openai_key(config) -> str:
    return (os.getenv("OPENAI_API_KEY") or config.OPENAI_API_KEY or "").strip()


def _openai_model(config) -> str:
    model = (
        os.getenv("OPENAI_MODEL") or getattr(config, "OPENAI_MODEL", "") or "gpt-4o-mini"
    ).strip() or "gpt-4o-mini"
    return model if str(model).startswith("openai/") else f"openai/{model}"


def _patch_llm_agent(config) -> None:
    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.models.base_llm import BaseLlm

    def _canonical_model(self):
        if isinstance(self.model, BaseLlm):
            return self.model

        provider = _active_provider(config)
        if provider == "openai" and _openai_key(config) and self.model:
            from utils.litellm_json import FenceStrippingLiteLlm as LiteLlm

            kw = {}
            if config.LLM_FALLBACKS:
                kw["fallbacks"] = list(config.LLM_FALLBACKS)
            return LiteLlm(model=_openai_model(config), **kw)

        if provider == "groq" and config.GROQ_API_KEY and self.model:
            from utils.litellm_json import FenceStrippingLiteLlm as LiteLlm

            kw = {}
            if config.LLM_FALLBACKS:
                kw["fallbacks"] = list(config.LLM_FALLBACKS)
            if config.GROQ_MAX_TOKENS:
                kw["max_tokens"] = config.GROQ_MAX_TOKENS
            return LiteLlm(model=f"groq/{config.GROQ_MODEL}", **kw)

        if self.model:
            from google.adk.models.registry import LLMRegistry

            return LLMRegistry.new_llm(self.model)

        ancestor = self.parent_agent
        while ancestor is not None:
            if isinstance(ancestor, LlmAgent):
                return ancestor.canonical_model
            ancestor = ancestor.parent_agent
        raise ValueError(f"No model found for {self.name}.")

    LlmAgent.canonical_model = property(_canonical_model)


def _patch_genai_client(config) -> None:
    from google import genai as genai_mod

    if getattr(genai_mod, "_ust_sttm_real_client", None) is None:
        genai_mod._ust_sttm_real_client = genai_mod.Client

    real_client = genai_mod._ust_sttm_real_client

    def _client_factory(*args, **kwargs):
        provider = _active_provider(config)
        if provider in {"openai", "groq"} and (
            (provider == "openai" and _openai_key(config))
            or (provider == "groq" and config.GROQ_API_KEY)
        ):
            from utils.genai_groq_compat import GroqGenaiCompatClient

            return GroqGenaiCompatClient(*args, **kwargs)

        gemini_key = (config.GOOGLE_API_KEY or os.getenv("GOOGLE_API_KEY") or "").strip()
        if gemini_key:
            kwargs.pop("vertexai", None)
            kwargs.pop("project", None)
            kwargs.pop("location", None)
            kwargs.setdefault("api_key", gemini_key)
            return real_client(*args, **kwargs)

        return real_client(*args, **kwargs)

    genai_mod.Client = _client_factory
