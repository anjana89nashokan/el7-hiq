"""Shared LLM rate-limit helpers for ADK/Vertex agent loops."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from config.settings import config

logger = logging.getLogger(__name__)


@dataclass
class _UsageWindow:
    window_start: float = field(default_factory=time.time)
    window_tokens: int = 0
    window_requests: int = 0
    total_tokens: int = 0


_USAGE_BY_KEY: dict[str, _UsageWindow] = defaultdict(_UsageWindow)
_USAGE_LOCK = asyncio.Lock()
_REQUEST_TIMESTAMPS_BY_KEY: dict[str, list[float]] = defaultdict(list)
_REQUEST_LOCK = asyncio.Lock()


def _event_token_count(event: Any) -> int:
    usage = getattr(event, "usage_metadata", None)
    if not usage:
        return 0
    return int(getattr(usage, "total_token_count", 0) or 0)


async def wait_before_llm_request(message: Any) -> None:
    """
    Backward-compatible no-op.

    The previous implementation called Vertex count_tokens before every agent
    request. In extract mapping/driver loops that creates an extra API call per
    field/step and can make long synchronous requests more likely to disconnect.
    Keep the function so existing imports do not break while callers move to
    event-usage based throttling.
    """
    return None


async def wait_for_llm_request_slot(
    session_id: str,
    rpm_limit: int | None = None,
    window_seconds: int = 60,
    safety_margin: float = 0.9,
) -> None:
    """
    Lightweight pre-call RPM pacing without Vertex count_tokens calls.

    This protects first-call 429s, where no ADK usage event exists yet. It only
    tracks request timestamps in memory and intentionally avoids extra network
    calls before the real agent request.
    """
    rpm = int(rpm_limit or getattr(config, "LLM_RPM_LIMIT", 50))
    effective_limit = max(1, int(rpm * safety_margin))

    while True:
        wait_seconds = 0.0
        async with _REQUEST_LOCK:
            now = time.time()
            cutoff = now - window_seconds
            timestamps = [
                ts for ts in _REQUEST_TIMESTAMPS_BY_KEY[session_id] if ts > cutoff
            ]
            _REQUEST_TIMESTAMPS_BY_KEY[session_id] = timestamps

            if len(timestamps) < effective_limit:
                timestamps.append(now)
                return

            oldest = min(timestamps)
            wait_seconds = max(0.0, window_seconds - (now - oldest) + 0.25)

        logger.warning(
            "[LLM_RATE_LIMIT] request slot wait %.2fs session_key=%s rpm=%d/%d",
            wait_seconds,
            session_id,
            len(_REQUEST_TIMESTAMPS_BY_KEY[session_id]),
            effective_limit,
        )
        await asyncio.sleep(wait_seconds)


async def record_llm_usage_and_get_wait(
    event: Any,
    session_id: str,
    buffer_tokens: int = 100,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
    window_seconds: int = 20,
    max_wait_seconds: int = 300,
) -> float:
    """
    Record actual ADK usage metadata and return recommended wait seconds.

    This is intentionally session-keyed and in-memory. It is a runtime guard, not
    business state. Use distinct prefixes when separate flows should not share a
    bucket, e.g. "extract_mapping:<session_id>".

    This function does not sleep. Callers should apply the returned delay between
    field/agent runs, not while consuming ADK events.
    """
    rpm = int(rpm_limit or getattr(config, "LLM_RPM_LIMIT", 50))
    tpm = int(tpm_limit or getattr(config, "LLM_TPM_LIMIT", 250_000))
    
    event_tokens = _event_token_count(event)
    # Only count as a new request if the event has usage metadata (turn complete)
    is_new_turn = event_tokens > 0
    wait_seconds = 0.0

    async with _USAGE_LOCK:
        now = time.time()
        usage = _USAGE_BY_KEY[session_id]
        elapsed = now - usage.window_start

        if elapsed >= window_seconds:
            usage.window_start = now
            usage.window_tokens = 0
            usage.window_requests = 0
            elapsed = 0

        projected_tokens = usage.window_tokens + event_tokens + buffer_tokens
        projected_requests = usage.window_requests + (1 if is_new_turn else 0)

        if projected_tokens >= tpm:
            wait_seconds = max(wait_seconds, window_seconds - elapsed)
        if projected_requests >= rpm:
            wait_seconds = max(wait_seconds, window_seconds - elapsed)

        wait_seconds = min(max(wait_seconds, 0.0), float(max_wait_seconds))

        if wait_seconds <= 0:
            usage.total_tokens += event_tokens
            usage.window_tokens += event_tokens
            if is_new_turn:
                usage.window_requests += 1
            return 0.0

        usage.total_tokens += event_tokens
        usage.window_tokens += event_tokens
        if is_new_turn:
            usage.window_requests += 1

    logger.warning(
        "[LLM_RATE_LIMIT] recommended wait %.2fs session_key=%s projected_tokens=%d/%d projected_requests=%d/%d",
        wait_seconds,
        session_id,
        projected_tokens,
        tpm,
        projected_requests,
        rpm,
    )
    return wait_seconds


async def manage_llm_rate_limits(
    event: Any,
    session_id: str,
    buffer_tokens: int = 100,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
    window_seconds: int = 20,
    max_wait_seconds: int = 300,
) -> None:
    """
    Compatibility wrapper for older callers.

    New extract mapping/driver code should use record_llm_usage_and_get_wait()
    and sleep between fields/steps.
    """
    wait_seconds = await record_llm_usage_and_get_wait(
        event=event,
        session_id=session_id,
        buffer_tokens=buffer_tokens,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        window_seconds=window_seconds,
        max_wait_seconds=max_wait_seconds,
    )
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)


def is_resource_exhausted_error(exc: Exception) -> bool:
    """Return True for likely Vertex/Gemini quota exhaustion errors."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429 or str(code) == "429":
        return True

    text = " ".join(
        part
        for part in (
            str(exc),
            str(getattr(exc, "message", "")),
            str(getattr(exc, "details", "")),
        )
        if part
    ).lower()
    return (
        "429" in text
        or "resource exhausted" in text
        or "quota" in text
        or "rate limit" in text
        or "too many requests" in text
    )


def is_transient_llm_transport_error(exc: Exception) -> bool:
    """Return True for retryable upstream/proxy transport failures."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if str(code) in {"503", "504"}:
        return True

    text = " ".join(
        part
        for part in (
            str(exc),
            str(getattr(exc, "message", "")),
            str(getattr(exc, "details", "")),
        )
        if part
    ).lower()
    return any(
        marker in text
        for marker in (
            "connection aborted",
            "remote end closed",
            "remotedisconnected",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
            "service unavailable",
            "deadline exceeded",
            "read timed out",
            "read timeout",
            "connect timeout",
            "timeout",
            "503",
            "504",
        )
    )


def calculate_retry_delay(attempt: int) -> float:
    """Exponential backoff delay for retry attempt index 0, 1, 2, ..."""
    # Reduce base delay to 1.0 to fight timeouts
    base = float(getattr(config, "LLM_RETRY_BASE_DELAY", 1.0))
    max_delay = float(getattr(config, "LLM_RETRY_MAX_DELAY", 30.0))
    return min(max_delay, base * (2 ** max(0, attempt)))


def calculate_mapping_retry_delay(attempt: int) -> float:
    """Balanced backoff for Vertex 429s during mapping field agent runs."""
    base = float(getattr(config, "MAPPING_LLM_RETRY_BASE_DELAY", 8.0))
    max_delay = float(getattr(config, "MAPPING_LLM_RETRY_MAX_DELAY", 30.0))
    return min(max_delay, base * (2 ** max(0, attempt)))
