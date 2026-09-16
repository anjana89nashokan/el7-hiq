"""Runtime LLM settings — lets a user set their own API key from the UI.

The key is applied immediately (no restart): we update the live ``config`` object
and ``os.environ`` so the next genai/ADK client picks it up, and we persist it to
``datamap_backend/.env`` so it survives a restart.
"""
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import config

router = APIRouter()

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"  # datamap_backend/.env


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def _persist_env(updates: dict[str, str]) -> None:
    """Update or insert KEY=VALUE lines in datamap_backend/.env (best-effort)."""
    try:
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines() if _ENV_PATH.exists() else []
        remaining = dict(updates)
        out: list[str] = []
        for line in lines:
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
            if m and m.group(1) in remaining:
                out.append(f"{m.group(1)}={remaining.pop(m.group(1))}")
            else:
                out.append(line)
        for key, value in remaining.items():
            out.append(f"{key}={value}")
        _ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception:
        # The runtime override still applies even if writing the file fails.
        pass


def _validate_gemini(key: str) -> tuple[bool, str]:
    try:
        from google import genai

        client = genai.Client(api_key=key)
        next(iter(client.models.list()), None)  # one lightweight request
        return True, "Key validated."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not validate the key: {str(exc)[:200]}"


def _validate_openai(key: str) -> tuple[bool, str]:
    try:
        import httpx

        response = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20.0,
        )
        if response.status_code == 200:
            return True, "Key validated."
        detail = response.text[:200] if response.text else response.reason_phrase
        return False, f"Could not validate the key: {detail}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not validate the key: {str(exc)[:200]}"


def _state() -> dict:
    return {
        "provider": config.LLM_PROVIDER,
        "gemini": {"configured": bool(config.GOOGLE_API_KEY), "masked": _mask(config.GOOGLE_API_KEY)},
        "groq": {
            "configured": bool(config.GROQ_API_KEY),
            "masked": _mask(config.GROQ_API_KEY),
            "model": config.GROQ_MODEL,
        },
        "openai": {
            "configured": bool(config.OPENAI_API_KEY),
            "masked": _mask(config.OPENAI_API_KEY),
            "model": config.OPENAI_MODEL,
        },
    }


class LlmSettings(BaseModel):
    provider: str | None = None  # "gemini" | "groq" | "openai"
    google_api_key: str | None = None
    groq_api_key: str | None = None
    groq_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None


@router.get("/llm")
def get_llm_settings():
    """Current provider and which keys are configured (masked — never returns the raw key)."""
    return _state()


@router.post("/llm")
def update_llm_settings(body: LlmSettings):
    """Set the API key/provider at runtime, persist to .env, and apply immediately."""
    updates: dict[str, str] = {}
    validated: bool | None = None
    message = "Settings saved."

    if body.google_api_key is not None:
        key = body.google_api_key.strip()
        if key:
            validated, message = _validate_gemini(key)
            if not validated:
                raise HTTPException(status_code=400, detail=message)
        config.GOOGLE_API_KEY = key
        if key:
            os.environ["GOOGLE_API_KEY"] = key
            os.environ["GEMINI_API_KEY"] = key
        updates["GOOGLE_API_KEY"] = key

    if body.groq_api_key is not None:
        key = body.groq_api_key.strip()
        config.GROQ_API_KEY = key
        if key:
            os.environ["GROQ_API_KEY"] = key
        updates["GROQ_API_KEY"] = key

    if body.groq_model:
        config.GROQ_MODEL = body.groq_model.strip()
        os.environ["GROQ_MODEL"] = config.GROQ_MODEL
        updates["GROQ_MODEL"] = config.GROQ_MODEL

    if body.openai_api_key is not None:
        key = body.openai_api_key.strip()
        if key:
            validated, message = _validate_openai(key)
            if not validated:
                raise HTTPException(status_code=400, detail=message)
        config.OPENAI_API_KEY = key
        if key:
            os.environ["OPENAI_API_KEY"] = key
        updates["OPENAI_API_KEY"] = key

    if body.openai_model:
        config.OPENAI_MODEL = body.openai_model.strip() or "gpt-4o-mini"
        os.environ["OPENAI_MODEL"] = config.OPENAI_MODEL
        updates["OPENAI_MODEL"] = config.OPENAI_MODEL

    # Resolve provider: explicit choice, else infer from whichever key was provided.
    provider = (body.provider or "").strip().lower()
    if not provider:
        if body.google_api_key:
            provider = "gemini"
        elif body.groq_api_key:
            provider = "groq"
        elif body.openai_api_key:
            provider = "openai"
    if provider:
        if provider not in ("gemini", "groq", "openai"):
            raise HTTPException(
                status_code=400,
                detail="provider must be 'gemini', 'groq', or 'openai'",
            )
        config.LLM_PROVIDER = provider
        os.environ["LLM_PROVIDER"] = provider
        updates["LLM_PROVIDER"] = provider

    # The active provider must have a key.
    if config.LLM_PROVIDER == "gemini" and not config.GOOGLE_API_KEY:
        raise HTTPException(status_code=400, detail="Gemini is selected but no Google API key is set.")
    if config.LLM_PROVIDER == "groq" and not config.GROQ_API_KEY:
        raise HTTPException(status_code=400, detail="Groq is selected but no Groq API key is set.")
    if config.LLM_PROVIDER == "openai" and not config.OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="ChatGPT is selected but no OpenAI API key is set.")

    _persist_env(updates)
    return {**_state(), "saved": True, "validated": validated, "message": message}
