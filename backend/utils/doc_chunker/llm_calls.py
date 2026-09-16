"""
LLM call wrappers for the two per-chunk calls.

Call 1 — call_extraction()     : PDF bytes + typed extraction prompt → ExtractionResult
Call 2 — call_domain_scoring() : text-only domain prompt             → DomainScoringResult
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from google import genai
from google.genai.types import Part

from config.settings import config
from utils.rate_limiter import RateLimiter

from .models import DomainScoringResult, ExtractionResult

logger = logging.getLogger(__name__)

DEFAULT_EXTRACTION_MAX_TOKENS = 32_768
DEFAULT_DOMAIN_MAX_TOKENS = 2_048
DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 1.0

_rate_limiter = RateLimiter()


def _get_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )


def _safe_json_load(raw: str) -> Any:
    """Robust JSON parser: strips markdown fences and trailing commas."""
    if not raw or not raw.strip():
        raise ValueError("Empty model response")
    txt = raw.strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.I)
    txt = re.sub(r"\s*```$", "", txt, flags=re.I)
    txt = re.sub(r",\s*}", "}", txt)
    txt = re.sub(r",\s*\]", "]", txt)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        start, end = txt.find("{"), txt.rfind("}")
        if start != -1 and end > start:
            candidate = re.sub(r",\s*}", "}", txt[start: end + 1])
            candidate = re.sub(r",\s*\]", "]", candidate)
            return json.loads(candidate)
        logger.error("Failed to parse JSON. Raw output (first 500 chars):\n%s", raw[:500])
        raise


# ---------------------------------------------------------------------------
# Call 1 — typed extraction (PDF bytes + prompt)
# ---------------------------------------------------------------------------

def call_extraction(
    chunk_bytes: bytes,
    prompt: str,
    chunk_index: int,
    page_range: str,
    max_tokens: int = DEFAULT_EXTRACTION_MAX_TOKENS,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> ExtractionResult:
    client = _get_client()
    pdf_part = Part.from_bytes(data=chunk_bytes, mime_type="application/pdf")

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _rate_limiter.wait_for_availability(max_tokens // 4)
            logger.info("Call-1 extraction | chunk=%d pages=%s attempt=%d", chunk_index, page_range, attempt + 1)

            resp = client.models.generate_content(
                model=config.AGENT_MODEL,
                contents=[pdf_part, prompt],
                config={
                    "temperature": 0.0,
                    "max_output_tokens": max_tokens,
                    "response_mime_type": "application/json",
                },
            )
            data = _safe_json_load(resp.text or "")
            data["chunk_index"] = chunk_index
            data["page_range"] = page_range

            # open_section: null from model arrives as None — that's fine for Optional field
            result = ExtractionResult.model_validate(data)
            logger.info(
                "Call-1 done | chunk=%d reqs=%d in_scope=%d out_scope=%d layout=%d tables=%d open=%s",
                chunk_index,
                len(result.requirements),
                len(result.in_scope),
                len(result.out_of_scope),
                len(result.file_layout),
                len(result.generic_tables),
                result.open_section.section_type if result.open_section else "none",
            )
            return result

        except Exception as exc:
            last_exc = exc
            logger.warning("Call-1 failed | chunk=%d attempt=%d error=%s", chunk_index, attempt + 1, exc)
            time.sleep(retry_delay * (attempt + 1))

    raise RuntimeError(
        f"Call-1 extraction failed for chunk {chunk_index} after {retries + 1} attempts"
    ) from last_exc


# ---------------------------------------------------------------------------
# Call 2 — domain scoring (text-only, no PDF bytes)
# ---------------------------------------------------------------------------

def call_domain_scoring(
    prompt: str,
    chunk_index: int,
    max_tokens: int = DEFAULT_DOMAIN_MAX_TOKENS,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> DomainScoringResult:
    client = _get_client()

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _rate_limiter.wait_for_availability(max_tokens // 4)
            logger.info("Call-2 domain scoring | chunk=%d attempt=%d", chunk_index, attempt + 1)

            resp = client.models.generate_content(
                model=config.AGENT_MODEL,
                contents=[prompt],
                config={
                    "temperature": 0.0,
                    "max_output_tokens": max_tokens,
                    "response_mime_type": "application/json",
                },
            )
            data = _safe_json_load(resp.text or "")
            data["chunk_index"] = chunk_index

            result = DomainScoringResult.model_validate(data)
            logger.info("Call-2 done | chunk=%d top_domain=%s", chunk_index, result.top_domain)
            return result

        except Exception as exc:
            last_exc = exc
            logger.warning("Call-2 failed | chunk=%d attempt=%d error=%s", chunk_index, attempt + 1, exc)
            time.sleep(retry_delay * (attempt + 1))

    raise RuntimeError(
        f"Call-2 domain scoring failed for chunk {chunk_index} after {retries + 1} attempts"
    ) from last_exc
