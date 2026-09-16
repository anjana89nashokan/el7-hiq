from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Optional, Tuple

import asyncio

from google import genai
from google.genai import types

from config.settings import config


logger = logging.getLogger(__name__)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# gemini-2.5-pro supports up to 65,536 output tokens.  We default to 32k which
# comfortably fits per-item judgments for 100+ items, and can be tuned via env.
_DEFAULT_MAX_OUTPUT_TOKENS = int(os.getenv("QUALITY_JUDGE_MAX_OUTPUT_TOKENS", "32768"))
_RETRY_MAX_OUTPUT_TOKENS = int(os.getenv("QUALITY_JUDGE_MAX_OUTPUT_TOKENS_RETRY", "65536"))


@lru_cache(maxsize=1)
def _get_client():
    # genai.Client is patched in config.settings to use the Gemini Developer API
    # key (or Groq via LiteLlm) — no Vertex / Application Default Credentials needed.
    return genai.Client()


def _strip_json_fences(text: str) -> str:
    match = _JSON_FENCE_RE.search(text or "")
    if match:
        return match.group(1).strip()
    return (text or "").strip()


# --------------------------------------------------------------------------- #
# Response-shape helpers
# --------------------------------------------------------------------------- #


def _extract_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return text
    try:
        return response.candidates[0].content.parts[0].text  # type: ignore[attr-defined]
    except Exception:
        return ""


def _finish_reason(response: Any) -> str:
    """Return finish reason as a string (e.g. 'STOP', 'MAX_TOKENS') or ''."""
    try:
        reason = response.candidates[0].finish_reason  # type: ignore[attr-defined]
    except Exception:
        return ""
    return getattr(reason, "name", None) or str(reason or "")


# --------------------------------------------------------------------------- #
# Truncated-JSON salvage
#
# When Gemini hits max_output_tokens mid-response, the JSON it returned is
# valid for some prefix and then chops off mid-string (typically inside a
# `rationale` or `evidence_quote` of a `per_item_judgments[N]` entry).
# We attempt to:
#   1. Trim back to the last fully-closed array element.
#   2. Close any open arrays / objects we walked through.
#   3. Re-parse.
# If salvage fails, we re-raise the original JSONDecodeError so the caller
# gets a real signal.
# --------------------------------------------------------------------------- #


def _try_salvage_truncated_json(raw: str) -> Optional[dict[str, Any]]:
    text = _strip_json_fences(raw)
    if not text or not text.lstrip().startswith("{"):
        return None

    # Walk the string respecting strings/escapes, recording every position where
    # the bracket stack returns to a state matching the start of a known array
    # element.  We do not try to be clever — we just find the last position
    # where the prefix `text[:i+1]` is balanced under one extra open bracket
    # (i.e. we can close it and produce valid JSON).
    stack: list[str] = []
    in_string = False
    escape = False
    last_safe: Optional[Tuple[int, list[str]]] = None

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                return None  # malformed even at the prefix level
            stack.pop()
            # After this close, the prefix `text[:i+1]` is balanced for the
            # current depth — a safe place to truncate.
            last_safe = (i + 1, list(stack))
        elif ch == "," and stack:
            # A top-level comma inside an array/object: also a safe truncation
            # point (we drop the comma and close the open containers below).
            last_safe = (i, list(stack))

    if last_safe is None:
        return None

    cutoff, open_stack = last_safe
    closer = "".join("}" if c == "{" else "]" for c in reversed(open_stack))
    candidate = text[:cutoff] + closer

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, dict):
        return parsed
    return None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class GeminiJudgeClient:
    """
    Thin async Vertex Gemini client for judge prompts.

    Built fresh — does not import anything from server/judges.
    """

    def __init__(
        self,
        model_name: str | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self.model_name = (
            model_name
            or os.getenv("QUALITY_JUDGE_MODEL")
            or getattr(config, "AGENT_MODEL", "gemini-2.5-pro")
        )
        self.max_output_tokens = max_output_tokens or _DEFAULT_MAX_OUTPUT_TOKENS

    async def judge_json(self, *, system: str, user: str) -> dict[str, Any]:
        """
        Send `system` + `user` to Gemini and return the parsed JSON object.

        On JSON parse failure:
          1. Log the finish_reason and a short snippet around the failure point.
          2. If the response was truncated (MAX_TOKENS), retry once with a
             larger output cap.
          3. If parsing still fails, attempt to salvage by closing any open
             arrays/objects at the last safe boundary.
          4. If salvage fails, raise the JSONDecodeError.
        """
        prompt = f"{system.strip()}\n\n---\n\n{user.strip()}"

        raw, reason = await self._generate(prompt, self.max_output_tokens)

        try:
            return json.loads(_strip_json_fences(raw))
        except json.JSONDecodeError as exc:
            _log_parse_failure(raw, exc, reason, attempt="initial")

        retry_cap = max(_RETRY_MAX_OUTPUT_TOKENS, self.max_output_tokens * 2)
        retry_prompt = (
            f"{prompt}\n\n"
            "Your previous response was not valid JSON (it may have been truncated). "
            "Reply with ONE JSON object only — no prose, no markdown fences, no "
            "trailing commentary.  Keep `rationale` and `evidence_quote` SHORT "
            "(one sentence max) so the whole object fits in the response budget."
        )
        raw2, reason2 = await self._generate(retry_prompt, retry_cap)

        try:
            return json.loads(_strip_json_fences(raw2))
        except json.JSONDecodeError as exc:
            _log_parse_failure(raw2, exc, reason2, attempt="retry")

        salvaged = _try_salvage_truncated_json(raw2) or _try_salvage_truncated_json(raw)
        if salvaged is not None:
            logger.warning(
                "Judge JSON was truncated; salvaged %d per_item_judgments by closing open brackets.",
                len((salvaged.get("per_item_judgments") or [])),
            )
            return salvaged

        json.loads(_strip_json_fences(raw2))  # re-raise the original decode error
        raise RuntimeError("unreachable")  # for type checkers

    async def _generate(self, prompt: str, max_output_tokens: int) -> tuple[str, str]:
        client = _get_client()
        cfg = types.GenerateContentConfig(
            temperature=0.0,
            top_p=1.0,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
        )
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model_name,
                contents=prompt,
                config=cfg,
            )
        except Exception:
            logger.exception(
                "Gemini call failed (model=%s max_output_tokens=%d)",
                self.model_name, max_output_tokens,
            )
            raise
        return _extract_text(response), _finish_reason(response)


def _log_parse_failure(
    raw: str,
    exc: json.JSONDecodeError,
    finish_reason: str,
    *,
    attempt: str,
) -> None:
    snippet_start = max(0, exc.pos - 80)
    snippet_end = min(len(raw), exc.pos + 80)
    logger.warning(
        "Judge JSON parse failed on %s attempt: finish_reason=%s len=%d err=%s pos=%d snippet=%r",
        attempt, finish_reason, len(raw), exc.msg, exc.pos,
        raw[snippet_start:snippet_end],
    )
