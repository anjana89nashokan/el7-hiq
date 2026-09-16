from __future__ import annotations

import io
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from google import genai
from google.genai import types as genai_types
from google.genai.types import Part
from pypdf import PdfReader, PdfWriter

from config.settings import config
from utils.doc_chunker.splitter import convert_docx_to_pdf
from utils.gcs_artifact_utils import list_blobs, download_bytes, upload_text, upload_json

from agents.extract_requirement_layer_agent.schema.brd_transcript_agent_response_schema import (
    _build_empty_requirement_schema,
    response_schema
)

logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# GCS artifact classification helpers
# ---------------------------------------------------------------------------

_PREFIX_MAP = {
    "brd_": "brd",
    "file_layout_": "file_layout",
    "transcript_": "transcript",
    "bsa_notes": "bsa_notes",
    "interface_code": "interface_code",
    "pipeline_result": "pipeline_result",
}

def _classify_blob_name(name: str) -> Optional[str]:
    """Return the artifact type key for a GCS object name, or None if unknown."""
    basename = name.split("/")[-1]
    for prefix, kind in _PREFIX_MAP.items():
        if basename.startswith(prefix):
            return kind
    return None

# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

def _get_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )

def _safe_json_load(text: str) -> dict:
    import json
    try:
        return json.loads(text)
    except:
        import re
        text = re.sub(r"```json|```", "", text).strip()
        try:
            return json.loads(text)
        except:
            return {}


def _pdf_bytes_to_chunks(pdf_bytes: bytes, chunk_size: int = 5) -> tuple[list[bytes], int]:
    """Split raw PDF bytes into page chunks without writing to disk."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    total = len(reader.pages)
    chunks: list[bytes] = []
    for start in range(0, total, chunk_size):
        writer = PdfWriter()
        for i in range(start, min(start + chunk_size, total)):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())
    return chunks, total


def _convert_to_markdown(
    pdf_bytes: bytes,
    prompt_template: str,
    chunk_size: int = config.MARKDOWN_CHUNK_PAGES,
    max_tokens: int = 30000,
    retries: int = 3,
    retry_delay: float = 1.5,
    min_chunk_chars: int = 10,
    min_total_chars: int = 10,
) -> str:

    if sys.platform == "win32":
        import pythoncom
        pythoncom.CoInitialize()

    try:
        client = _get_client()

        chunks, total_pages = _pdf_bytes_to_chunks(pdf_bytes, chunk_size)

        if total_pages == 0:
            raise ValueError("PDF has 0 pages — extraction cannot proceed")

        logger.info("PDF split into %d chunks | total_pages=%d", len(chunks), total_pages)

        markdown_parts: list[str] = []
        handoff = "(start of document)"

        failed_chunks = []

        for idx, chunk_bytes in enumerate(chunks):
            start_page = idx * chunk_size + 1
            end_page = min((idx + 1) * chunk_size, total_pages)
            page_range = f"{start_page}-{end_page}"

            prompt = prompt_template.format(page_range=page_range, handoff=handoff)
            pdf_part = Part.from_bytes(data=chunk_bytes, mime_type="application/pdf")

            success = False
            last_exc = None

            for attempt in range(retries):
                try:
                    resp = client.models.generate_content(
                        model=config.AGENT_MODEL,
                        contents=[pdf_part, prompt],
                        config={
                            "temperature": 0.0,
                            "max_output_tokens": max_tokens,
                        },
                    )

                    chunk_md = (resp.text or "").strip()

                    # detect empty output; threshold scales with page count
                    _min = max(min_chunk_chars, 10 * (end_page - start_page + 1))
                    if not chunk_md or len(chunk_md) < _min:
                        raise ValueError("Chunk returned empty or too small")

                    markdown_parts.append(chunk_md)

                    handoff = chunk_md[-500:]  # stronger continuity

                    logger.info(
                        "Chunk SUCCESS | %d/%d pages=%s chars=%d",
                        idx + 1,
                        len(chunks),
                        page_range,
                        len(chunk_md),
                    )

                    success = True
                    break

                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "Chunk FAILED | %d attempt=%d pages=%s error=%s",
                        idx + 1,
                        attempt + 1,
                        page_range,
                        exc,
                    )
                    time.sleep(retry_delay * (attempt + 1))

            if not success:
                logger.error("Chunk PERMANENT FAILURE | pages=%s", page_range)
                failed_chunks.append((idx, chunk_bytes, page_range))

        #  HARD FAIL if any chunk failed
        if failed_chunks:
            raise RuntimeError(f"{len(failed_chunks)} chunks failed — extraction incomplete")

        full_markdown = "\n\n".join(markdown_parts)

        # FINAL VALIDATION
        if len(full_markdown) < min_total_chars:
            raise ValueError("Final markdown too small — extraction likely incomplete")

        logger.info("FULL MARKDOWN GENERATED | total_chars=%d", len(full_markdown))

        return full_markdown

    finally:
        if sys.platform == "win32":
            import pythoncom
            pythoncom.CoUninitialize()

# ---------------------------------------------------------------------------
# Ensure PDF bytes — convert DOCX bytes → PDF bytes if needed
# ---------------------------------------------------------------------------

def _ensure_pdf_bytes(raw_bytes: bytes, filename: str) -> bytes:
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return raw_bytes

    if ext != ".docx":
        raise ValueError(f"Cannot convert {ext!r} to PDF")

    tmp_dir = Path(config.DATA_DIR) / "tmp_brd_conversion"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    docx_path = tmp_dir / f"_conv_{os.urandom(4).hex()}.docx"
    pdf_path = docx_path.with_name(docx_path.stem + "_converted.pdf")

    try:
        docx_path.write_bytes(raw_bytes)

        # Use the improved conversion function with fallback methods
        try:
            convert_docx_to_pdf(str(docx_path))
            
            if not pdf_path.exists():
                raise FileNotFoundError(f"Converted PDF not found: {pdf_path}")

            logger.info("DOCX → PDF SUCCESS | file=%s", filename)
            return pdf_path.read_bytes()
            
        except Exception as e:
            logger.error("DOCX → PDF FAILED | file=%s error=%s", filename, e)
            raise

    finally:
        # Cleanup temporary files
        for p in tmp_dir.glob(f"{docx_path.stem}*"):
            try:
                p.unlink()
            except Exception as cleanup_error:
                logger.warning("Cleanup failed for %s: %s", p, cleanup_error)
# ---------------------------------------------------------------------------
# Stateful markdown extraction
# ---------------------------------------------------------------------------

EXTRACTION_CHUNK_PAGES = getattr(config, "EXTRACTION_CHUNK_PAGES", 10)

def _normalize_schema(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    return {
        "scope": payload.get("scope", {}),
        "bsa_input": payload.get("bsa_input", ""),
        "requirements": payload.get("requirements", ""),
        "filters_and_parameters": payload.get("filters_and_parameters", {}),
        "file_attributes_mapping": payload.get("file_attributes_mapping", {}),
        "file_specs": payload.get("file_specs", {}),
        "common_rules": payload.get("common_rules", {}),
    }


def _split_markdown_semantic_chunks(markdown: str, approx_pages: int = EXTRACTION_CHUNK_PAGES) -> list[str]:
    """
    Split markdown into extraction-safe semantic chunks.
    Uses heading-aware splitting first, then size fallback.
    """
    if not markdown.strip():
        return []

    sections = re.split(r"(?m)^# ", markdown)
    rebuilt = []
    for idx, sec in enumerate(sections):
        sec = sec.strip()
        if not sec:
            continue
        rebuilt.append("# " + sec if idx > 0 else sec)

    # fallback chunking by char size
    max_chars = approx_pages * 4000
    chunks: list[str] = []
    current = ""

    for sec in rebuilt:
        if len(current) + len(sec) > max_chars and current:
            chunks.append(current)
            current = sec
        else:
            current += "\n\n" + sec

    if current.strip():
        chunks.append(current)

    return chunks


def _merge_nested_state(
    base: dict[str, Any],
    incoming: dict[str, Any],
    append_bsa_input: bool = False,
) -> dict[str, Any]:
    for key, value in incoming.items():
        if key not in base:
            base[key] = value
            continue
        if key == "bsa_input":
            if value:
                if append_bsa_input:
                    base[key] = (base.get(key, "") + "\n\n" + value).strip()
                else:
                    base[key] = value
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_nested_state(base[key], value, append_bsa_input=append_bsa_input)
        else:
            if value not in (None, "", [], {}):
                base[key] = value
    return base


def _create_cache(
    client: genai.Client,
    content: str,
    display_name: str,
) -> str | None:
    """
    Create a Vertex AI cached content entry for a large static text.
    Returns the cache name, or None if caching is disabled / content too small.
    """
    if not config.BRD_CONTEXT_CACHE_ENABLED:
        return None
    try:
        cached = client.caches.create(
            model=config.AGENT_MODEL,
            config=genai_types.CreateCachedContentConfig(
                contents=[genai_types.Content(role="user", parts=[genai_types.Part(text=content)])],
                display_name=display_name,
                ttl=f"{config.BRD_CONTEXT_CACHE_TTL_SECONDS}s",
            ),
        )
        logger.info("Created prompt cache | name=%s display=%s", cached.name, display_name)
        return cached.name
    except Exception as exc:
        logger.warning("Prompt cache creation failed (%s) — falling back to inline context", exc)
        return None


def _delete_cache(client: genai.Client, cache_name: str) -> None:
    try:
        client.caches.delete(name=cache_name)
        logger.info("Deleted prompt cache | name=%s", cache_name)
    except Exception as exc:
        logger.warning("Could not delete prompt cache %s: %s", cache_name, exc)

def _final_cleanup(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": state.get("scope", {}),
        "bsa_input": state.get("bsa_input", "").strip(),
        "requirements": state.get("requirements", "").strip(),
        "filters_and_parameters": state.get("filters_and_parameters", {}),
        "file_attributes_mapping": state.get("file_attributes_mapping", {}),
        "file_specs": state.get("file_specs", {}),
        "common_rules": state.get("common_rules", {}),
    }

def _run_stateful_extraction(
    combined_markdown: str,
    session_id: str,
    bsa_input: str | None = None,
) -> dict[str, Any]:
    """
    Stateful chunk-wise markdown extraction.
    Caches BSA notes once (if large enough) so every chunk call reuses it
    instead of re-sending the full text each time.
    """
    from agents.extract_requirement_layer_agent.prompts.brd_pormpts import _extract_chunk_structured

    client = _get_client()
    chunks = _split_markdown_semantic_chunks(combined_markdown)
    running_state = _build_empty_requirement_schema()

    logger.info("Starting stateful extraction | session=%s chunks=%d", session_id, len(chunks))

    # Cache BSA notes once — reused across all chunk calls
    bsa_cache_name: str | None = None
    cached_bsa_input = bsa_input  # fallback: inline
    if bsa_input:
        bsa_cache_name = _create_cache(
            client,
            content=bsa_input,
            display_name=f"bsa-notes-{session_id}",
        )
        if bsa_cache_name:
            cached_bsa_input = None  # will be injected via cache reference
        else:
            logger.info("Cache unavailable — bsa_input will be passed inline to all chunks | session=%s contents=%s", session_id, bsa_input)

    try:
        for idx, chunk in enumerate(chunks):
            logger.info("Processing extraction chunk %d/%d", idx + 1, len(chunks))
            chunk_result = _extract_chunk_structured(
                client=client,
                chunk_markdown=chunk,
                running_state=running_state,
                chunk_index=idx,
                bsa_input=cached_bsa_input,
                cache_name=bsa_cache_name,
            )
            running_state = _merge_nested_state(running_state, chunk_result, append_bsa_input=True)
    finally:
        if bsa_cache_name:
            _delete_cache(client, bsa_cache_name)

    return _final_cleanup(running_state)


def _resolve_field_path(data: dict[str, Any], dot_path: str) -> Any:
    """Walk a dot-separated path into a nested dict and return the value, or None."""
    keys = dot_path.split(".")
    node = data
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node

def _apply_only_allowed_fields(base: dict, updates: dict, allowed_paths: list[str]):
    """
    Apply updates ONLY for allowed field paths.
    Everything else is ignored.
    """
    for path in allowed_paths:
        value = _resolve_field_path(updates, path)
        if value is not None:
            _set_field_path(base, path, value)


def _set_field_path(data: dict, dot_path: str, value: Any):
    keys = dot_path.split(".")
    node = data
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value

