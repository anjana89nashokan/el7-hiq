from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from google.genai.types import Part

from config.settings import config
from agents.extract_requirement_layer_agent.tools.brd_utils import (
    _get_client,
    _create_cache,
    _delete_cache,
    _split_markdown_semantic_chunks
)
from agents.extract_requirement_layer_agent.tools.file_layout_utils import (
    _chunk_pdf,
    _to_pdf_bytes,
    _merge_layout_chunks,
)

from agents.extract_requirement_layer_agent.prompts.file_layout_prompts import (
    _LAYOUT_CHUNK_PROMPT,
    _LAYOUT_VALIDATION_PROMPT,  
)
from utils.extract_parser_utils import parse_xlsx_to_json
from utils.gcs_artifact_utils import (
    download_bytes,
    list_blobs,
    upload_json,
)

logger = logging.getLogger(__name__)


def _repair_truncated_json(text: str) -> dict:
    """Best-effort recovery of a truncated JSON object by trimming to the last complete value."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for end in range(len(text) - 1, 0, -1):
        candidate = text[:end].rstrip().rstrip(",") + "}"
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {}


def _merge_validated_layout(current: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """
    Merge a validation update into the running layout dict.

    Rules:
    - Tables present only in `update` are added.
    - Tables present in both: `update` rows replace `current` rows (validator wins).
    - Tables present only in `current` are preserved unchanged (validator did not
      see that page range so must not delete them).
    - "_validation_corrections" is never written into the layout dict.
    """
    merged = dict(current)
    for key, value in update.items():
        if key == "_validation_corrections":
            continue
        merged[key] = value
    return merged


def _ensure_table_values_are_lists(tables: dict) -> dict:
    """
    Guarantee every value in file_layout_tables is a list[dict].
    - Strips the meta-key "file_layout_tables" if it appears as a key (legacy nesting artifact)
    - Keeps only keys whose value is a non-empty list
    """
    # Strip the legacy wrapper key regardless of how many other keys exist
    tables.pop("file_layout_tables", None)
    return {k: v for k, v in tables.items() if isinstance(v, list)}


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def run_file_layout_extraction(session_id: str) -> dict[str, Any]:
    """ 1. Fetch file_layout_* artifact from GCS
        2. For xlsx: convert to markdown then extract via LLM + validate.
           For other formats: chunk markdown and extract via LLM, then validate.
        3. Persist file_layout_tables.json to GCS and return result dict.

        Response shape (always consistent for UI):
          { "Table/Sheet Name": [ {"col": "val", ...}, ... ] }
    """

    # ------------------------------------------------------------------
    # 1. Locate raw artifact in GCS
    # Search both upload prefixes: the /uploads/ path (used by upload-extract
    # endpoint) and the legacy /uploaded_files/ path.
    # ------------------------------------------------------------------
    xlsx_blob = None
    filename = None

    for search_prefix in [
        f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/uploads/",
        f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/uploaded_files/",
    ]:
        blobs = list_blobs(prefix=search_prefix)
        xlsx_blob = next(
            (
                b for b in blobs
                if "file_layout_" in Path(b.name).name
                and Path(b.name).suffix.lower() in {".xlsx", ".xls", ".xlsm"}
            ),
            None,
        )
        if xlsx_blob:
            break

    if xlsx_blob is not None:
        # ------------------------------------------------------------------
        # XLSX: parse directly to JSON — no LLM, no markdown conversion
        # ------------------------------------------------------------------
        filename = Path(xlsx_blob.name).name
        logger.info("Found xlsx file layout | name=%s — using direct JSON parser", filename)
        raw_bytes = download_bytes(object_name=xlsx_blob.name)

        file_layout_tables = _ensure_table_values_are_lists(parse_xlsx_to_json(raw_bytes))

        for sheet, rows in file_layout_tables.items():
            logger.info(
                "Parsed sheet | session=%s sheet=%r rows=%d",
                session_id, sheet, len(rows),
            )
        logger.info(
            "Direct xlsx parse complete | session=%s sheets=%d",
            session_id, len(file_layout_tables),
        )

        layout_object = (
            f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}"
            "/extracted_data/file_layout_tables.json"
        )
        upload_json(object_name=layout_object, payload=file_layout_tables)
        gcs_uri = f"gs://{config.MAPPING_ARTIFACT_BUCKET}/{layout_object}"
        logger.info("Persisted file_layout_tables | uri=%s", gcs_uri)

        return {
            "session_id": session_id,
            "file_layout_filename": filename,
            "total_pages": len(file_layout_tables),
            "tables_extracted": len(file_layout_tables),
            "file_layout_tables": file_layout_tables,
            "gcs_output_uri": gcs_uri,
        }
    else:
        # Fall back to markdown files
        md_blob = None
        for search_prefix in [
            f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/markdown_files/",
        ]:
            md_blobs = list_blobs(prefix=search_prefix)
            md_blob = next(
                (b for b in md_blobs if "file_layout_" in Path(b.name).name and b.name.endswith(".md")),
                None,
            )
            if md_blob:
                break

        if md_blob is None:
            raise FileNotFoundError(
                f"No file_layout artifact found for session_id={session_id}"
            )

        filename = Path(md_blob.name).name
        markdown = download_bytes(object_name=md_blob.name).decode("utf-8")

        logger.info(
            "Loaded file layout markdown | name=%s chars=%d",
            filename, len(markdown)
        )

    # ------------------------------------------------------------------
    # 2. Chunk markdown (semantic) — PDF/DOCX path only
    # ------------------------------------------------------------------
    chunks = _split_markdown_semantic_chunks(markdown, approx_pages=8)
    chunk_labels = [f"chunk-{i+1}" for i in range(len(chunks))]

    logger.info(
        "Chunked markdown | session=%s chunks=%d",
        session_id, len(chunks),
    )

    client = _get_client()

    # ------------------------------------------------------------------
    # Static system-hint cache — shared by both extraction and validation
    # ------------------------------------------------------------------
    system_hint = (
        "You are a precise document extraction assistant for file layout specifications. "
        "Extract every table exactly as it appears, keyed by its header."
    )
    system_cache_name = _create_cache(
        client,
        content=system_hint,
        display_name=f"layout-prompt-{session_id}",
    )

    # ------------------------------------------------------------------
    # PHASE 1: Extraction — chunk by chunk
    # ------------------------------------------------------------------
    chunk_results: list[dict[str, Any]] = []
    handoff = "(start of document)"

    try:
        for idx, chunk_text in enumerate(chunks):
            page_range = chunk_labels[idx]

            prompt = _LAYOUT_CHUNK_PROMPT.format(
                page_range=page_range,
                handoff=handoff
            )

            call_config: dict[str, Any] = {
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "max_output_tokens": 65536,
            }
            if system_cache_name:
                call_config["cached_content"] = system_cache_name

            last_exc: Exception | None = None
            for attempt in range(3):
                try:
                    resp = client.models.generate_content(
                        model=config.AGENT_MODEL,
                        contents=[chunk_text, prompt],
                        config=call_config,
                    )
                    text = (resp.text or "{}").strip()
                    chunk_json = _repair_truncated_json(text)
                    chunk_results.append(chunk_json)
                    last_key = list(chunk_json.keys())[-1] if chunk_json else ""
                    handoff = f"Last table: {last_key!r}" if last_key else "(empty chunk)"
                    logger.info(
                        "Layout extraction chunk %d/%d pages=%s tables=%d",
                        idx + 1, len(chunks), page_range, len(chunk_json),
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "Layout chunk %d attempt %d failed: %s", idx, attempt + 1, exc
                    )
                    time.sleep(1.5 * (attempt + 1))
            else:
                logger.error("Layout chunk %d permanently failed: %s", idx, last_exc)
                chunk_results.append({})

    finally:
        # System hint cache is no longer needed after extraction
        if system_cache_name:
            _delete_cache(client, system_cache_name)

    # Merge all extraction chunks into one flat dict
    raw_layout_tables = _merge_layout_chunks(chunk_results)
    logger.info(
        "Merged raw file layout tables | session=%s table_count=%d",
        session_id, len(raw_layout_tables),
    )

    # ------------------------------------------------------------------
    # PHASE 2: LLM-as-judge validation — re-present each PDF chunk and
    # correct the accumulated extraction in place.
    #
    # Strategy:
    #   • Pre-cache every PDF chunk before the validation loop starts so
    #     each LLM call references a cache rather than inlining raw bytes.
    #   • For chunks too small to cache, fall back to inline Part.
    #   • Always clean up caches in a finally block.
    # ------------------------------------------------------------------
    logger.info(
        "Starting LLM-as-judge validation | session=%s chunks=%d",
        session_id, len(chunks),
    )

    # Pre-cache all PDF chunks as Parts (bytes → base64 text for the cache API)
    # We store the raw bytes alongside the cache name so we can fall back to
    # an inline Part when caching was not possible.
    chunk_cache_names: list[str | None] = []
    for idx, chunk_text in enumerate(chunks):
        cache_name = _create_cache(
            client,
            content=chunk_text,
            display_name=f"layout-val-chunk-{session_id}-{idx}",
        )
        chunk_cache_names.append(cache_name)
        if cache_name:
            logger.info(
                "Cached validation chunk %d/%d | cache=%s",
                idx + 1, len(chunks), cache_name,
            )
        else:
            logger.info(
                "Validation chunk %d/%d too small to cache — will use inline",
                idx + 1, len(chunks),
            )

    all_corrections: list[str] = []
    running_layout = dict(raw_layout_tables)

    try:
        for idx, (chunk_text, cache_name) in enumerate(zip(chunks, chunk_cache_names)):
            
            page_range = chunk_labels[idx]

            val_prompt = _LAYOUT_VALIDATION_PROMPT.format(
                page_range=page_range,
                extracted=json.dumps(running_layout, indent=2),
            )

            val_config: dict[str, Any] = {
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "max_output_tokens": 65536,
            }

            if cache_name:
                # PDF pages are in the cache; prompt is the only inline content
                val_config["cached_content"] = cache_name
                contents = [val_prompt]
            else:
                # Fall back: send PDF bytes inline alongside the prompt
                contents = [chunk_text, val_prompt]

            last_exc = None
            for attempt in range(3):
                try:
                    resp = client.models.generate_content(
                        model=config.AGENT_MODEL,
                        contents=contents,
                        config=val_config,
                    )
                    text = (resp.text or "{}").strip()
                    val_result = _repair_truncated_json(text)

                    corrections = val_result.pop("_validation_corrections", [])
                    all_corrections.extend(corrections)

                    running_layout = _merge_validated_layout(running_layout, val_result)

                    logger.info(
                        "Validation chunk %d/%d pages=%s cache=%s corrections=%d",
                        idx + 1, len(chunks), page_range,
                        cache_name or "inline", len(corrections),
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "Validation chunk %d attempt %d failed: %s", idx, attempt + 1, exc
                    )
                    time.sleep(1.5 * (attempt + 1))
            else:
                logger.error(
                    "Validation chunk %d permanently failed: %s — keeping prior state",
                    idx, last_exc,
                )
                # Keep running_layout unchanged; do not corrupt accumulated state

    finally:
        # Always clean up pre-cached validation chunks
        for name in chunk_cache_names:
            if name:
                _delete_cache(client, name)

    file_layout_tables = _ensure_table_values_are_lists(running_layout)
    corrections_made = bool(all_corrections)

    logger.info(
        "LLM-as-judge validation complete | session=%s table_count=%d corrections=%d",
        session_id, len(file_layout_tables), len(all_corrections),
    )

    # ------------------------------------------------------------------
    # Persist validated layout JSON to GCS
    # ------------------------------------------------------------------
    layout_object = (
        f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}"
        "/extracted_data/file_layout_tables.json"
    )
    upload_json(object_name=layout_object, payload=file_layout_tables)
    
    logger.info("Corrections made %s",corrections_made)
    gcs_uri = f"gs://{config.MAPPING_ARTIFACT_BUCKET}/{layout_object}"
    logger.info("Persisted file_layout_tables | uri=%s", gcs_uri)

    return {
        "session_id": session_id,
        "file_layout_filename": filename,
        "total_pages": len(chunks),
        "tables_extracted": len(file_layout_tables),
        "file_layout_tables": file_layout_tables,
        "gcs_output_uri": gcs_uri,
    }