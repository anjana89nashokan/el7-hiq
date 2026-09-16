"""
BRD extraction utilities.

Pipeline for extract-brd-information:
  1. Download all artifacts from GCS for a given session_id
  2. Convert BRD / transcript DOCX → PDF if needed (reuses splitter.convert_docx_to_pdf)
  3. Convert BRD PDF → markdown iteratively (chunk-by-chunk LLM, preserving all tables/text)
  4. Convert transcript PDF → markdown (same approach, lighter prompt)
  5. Return structured BrdArtifacts with markdown content + metadata
"""
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
from agents.extract_requirement_layer_agent.prompts.brd_pormpts import (
    _BRD_CHUNK_PROMPT,
    _TRANSCRIPT_CHUNK_PROMPT,
    _VALIDATION_PROMPT,
    _CHECKPOINT_PROMPT,
)
from agents.extract_requirement_layer_agent.tools.brd_utils import (
    _classify_blob_name,
    _ensure_pdf_bytes,
    _convert_to_markdown,
    _run_stateful_extraction,
    _safe_json_load,
    _get_client,
    _create_cache,
    _delete_cache,
    _resolve_field_path,
    _apply_only_allowed_fields,
    _set_field_path,
)
from utils.gcs_artifact_utils import list_blobs, download_bytes, upload_text, upload_json
from utils.extract_parser_utils import xlsx_to_markdown

from agents.extract_requirement_layer_agent.schema.brd_transcript_agent_response_schema import response_schema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache management helpers
# ---------------------------------------------------------------------------

def _build_chunk_caches(
    client: Any,
    chunks: list[str],
    session_id: str,
    label: str,
) -> list[str | None]:
    """
    Pre-cache every chunk and return a list of cache names (or None for chunks
    that were too small to cache).  The caller is responsible for deleting them.

    Args:
        client:     Genai client instance.
        chunks:     List of markdown text chunks.
        session_id: Session identifier (used in cache display names).
        label:      Short label to distinguish caches (e.g. "validation", "checkpoint").

    Returns:
        List of cache names / None, parallel to `chunks`.
    """
    cache_names: list[str | None] = []
    for idx, chunk in enumerate(chunks):
        cache_name = _create_cache(
            client,
            content=chunk,
            display_name=f"{label}-chunk-{session_id}-{idx}",
        )
        cache_names.append(cache_name)
        if cache_name:
            logger.info(
                "Cached chunk %d/%d | label=%s session=%s cache=%s",
                idx + 1, len(chunks), label, session_id, cache_name,
            )
        else:
            logger.info(
                "Chunk %d/%d too small to cache — will use inline | label=%s session=%s",
                idx + 1, len(chunks), label, session_id,
            )
    return cache_names


def _delete_chunk_caches(client: Any, cache_names: list[str | None]) -> None:
    """Delete all non-None caches, swallowing errors."""
    for name in cache_names:
        if name:
            _delete_cache(client, name)

def _load_interface_code(session_id: str) -> str | None:
    """Return the interface_code stored at upload time, or None if absent."""
    obj = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/uploads/interface_code.txt"
    try:
        return download_bytes(object_name=obj).decode("utf-8").strip() or None
    except Exception:
        return None


def _pin_interface_code(result: dict, session_id: str) -> dict:
    """Overwrite common_rules.interface_code with the authoritative UI value."""
    code = _load_interface_code(session_id)
    if code:
        result.setdefault("common_rules", {})["interface_code"] = code
    return result


def run_cache_cleanup(session_id: str, labels: list[str] = ("validation", "checkpoint")) -> dict:
    """Call this when a session is fully done to evict all caches."""
    client = _get_client()
    deleted = []
    for label in labels:
        cache_names = _load_cache_registry(session_id, label)
        if cache_names:
            _delete_chunk_caches(client, cache_names)
            deleted.extend([n for n in cache_names if n])
            # Optionally delete the registry object too
    logger.info("Cache cleanup complete | session=%s deleted=%d", session_id, len(deleted))
    return {"deleted_caches": deleted}

# brd_utils additions (or inline here)

CACHE_REGISTRY_OBJECT = "{base_prefix}/cache_registry/{label}_cache_names.json"

def _save_cache_registry(session_id: str, label: str, cache_names: list[str | None]) -> None:
    """Persist cache names to GCS for reuse across endpoints."""
    obj = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/cache_registry/{label}_cache_names.json"
    upload_json(object_name=obj, payload={"cache_names": cache_names, "created_at": time.time()})
    logger.info("Saved cache registry | session=%s label=%s", session_id, label)


def _load_cache_registry(session_id: str, label: str) -> list[str | None] | None:
    """Load previously saved cache names from GCS. Returns None if not found."""
    obj = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/cache_registry/{label}_cache_names.json"
    try:
        raw = download_bytes(object_name=obj)
        data = _safe_json_load(raw.decode("utf-8"))
        return data.get("cache_names")
    except Exception:
        return None


def _get_or_build_chunk_caches(
    client: Any,
    chunks: list[str],
    session_id: str,
    label: str,
) -> list[str | None]:
    """
    Return cached chunk names if they already exist in GCS registry,
    otherwise build fresh caches and persist the registry.
    """
    existing = _load_cache_registry(session_id, label)
    if existing is not None and len(existing) == len(chunks):
        logger.info(
            "Reusing existing caches from registry | session=%s label=%s count=%d",
            session_id, label, len(existing),
        )
        return existing

    logger.info(
        "No valid cache registry found — building fresh | session=%s label=%s",
        session_id, label,
    )
    cache_names = _build_chunk_caches(client, chunks, session_id, label)
    _save_cache_registry(session_id, label, cache_names)
    return cache_names

# ---------------------------------------------------------------------------
# Main pipeline — called by the endpoint
# ---------------------------------------------------------------------------

def run_brd_extraction(session_id: str) -> dict[str, Any]:
    """
    1. List all blobs under bsa-extract-artifacts/{session_id}/
    2. Download and classify each artifact
    3. Convert BRD + transcript to PDF (if DOCX) then to markdown
    4. Return structured result dict
    """
    uploads_prefix = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/uploads/"
    blobs = list_blobs(prefix=uploads_prefix)

    if not blobs:
        raise FileNotFoundError(f"No artifacts found in GCS for session_id={session_id!r} (prefix={uploads_prefix})")

    # Download and classify
    artifacts: dict[str, dict[str, Any]] = {}
    for blob in blobs:
        kind = _classify_blob_name(blob.name)
        if kind is None:
            logger.warning("Skipping unrecognised artifact: %s", blob.name)
            continue
        raw = download_bytes(object_name=blob.name)
        artifacts[kind] = {"name": blob.name.split("/")[-1], "raw": raw}
        logger.info("Downloaded artifact kind=%s name=%s size=%d", kind, blob.name, len(raw))

    if "brd" not in artifacts:
        raise ValueError(f"BRD artifact not found for session_id={session_id!r}")
    if "file_layout" not in artifacts:
        raise ValueError(f"File layout artifact not found for session_id={session_id!r}")

    md_prefix = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/markdown_files"
    result: dict[str, Any] = {"session_id": session_id, "artifacts_found": list(artifacts.keys())}
    markdown_uploads: list[str] = []

    # Build a set of existing markdown object names for this session to skip re-conversion
    existing_md_blobs = list_blobs(prefix=f"{md_prefix}/")
    existing_md_objects: set[str] = {blob.name for blob in existing_md_blobs}

    def _get_or_convert_markdown(md_object: str, convert_fn) -> str:
        """Return cached markdown from GCS if present, otherwise convert and upload."""
        if md_object in existing_md_objects:
            logger.info("Reusing existing markdown (skipping conversion) | object=%s", md_object)
            return download_bytes(object_name=md_object).decode("utf-8", errors="replace")
        content = convert_fn()
        upload_text(object_name=md_object, content=content)
        markdown_uploads.append(md_object)
        logger.info("Uploaded markdown to GCS | object=%s", md_object)
        return content

    # ── BRD → PDF → markdown ────────────────────────────────────────────────
    brd_info = artifacts["brd"]
    result["brd_filename"] = brd_info["name"]
    brd_md_stem = Path(brd_info["name"]).stem
    brd_md_object = f"{md_prefix}/brd_{brd_md_stem}.md"

    def _convert_brd():
        logger.info("Converting BRD to markdown | session=%s", session_id)
        return _convert_to_markdown(_ensure_pdf_bytes(brd_info["raw"], brd_info["name"]), _BRD_CHUNK_PROMPT)

    brd_markdown = _get_or_convert_markdown(brd_md_object, _convert_brd)
    if brd_md_object not in markdown_uploads and brd_md_object in existing_md_objects:
        markdown_uploads.append(brd_md_object)

    # ── File layout → markdown ───────────────────────────────────────────────
    layout_info = artifacts["file_layout"]
    result["file_layout_filename"] = layout_info["name"]
    layout_md_stem = Path(layout_info["name"]).stem
    layout_md_object = f"{md_prefix}/file_layout_{layout_md_stem}.md"

    def _convert_layout():
        if Path(layout_info["name"]).suffix.lower() == ".xlsx":
            logger.info("Converting xlsx file layout to markdown deterministically | session=%s", session_id)
            return xlsx_to_markdown(layout_info["raw"])
        logger.info("Converting file layout to markdown via LLM | session=%s", session_id)
        return _convert_to_markdown(_ensure_pdf_bytes(layout_info["raw"], layout_info["name"]), _BRD_CHUNK_PROMPT)

    layout_markdown = _get_or_convert_markdown(layout_md_object, _convert_layout)
    if layout_md_object not in markdown_uploads and layout_md_object in existing_md_objects:
        markdown_uploads.append(layout_md_object)

    # ── Transcript → PDF → markdown (optional) ──────────────────────────────
    if "transcript" in artifacts:
        transcript_info = artifacts["transcript"]
        result["transcript_filename"] = transcript_info["name"]
        transcript_md_stem = Path(transcript_info["name"]).stem
        transcript_md_object = f"{md_prefix}/transcript_{transcript_md_stem}.md"

        def _convert_transcript():
            logger.info("Converting transcript to markdown | session=%s", session_id)
            return _convert_to_markdown(
                _ensure_pdf_bytes(transcript_info["raw"], transcript_info["name"]),
                _TRANSCRIPT_CHUNK_PROMPT,
                min_chunk_chars=5,
                min_total_chars=5,
            )

        transcript_markdown = _get_or_convert_markdown(transcript_md_object, _convert_transcript)
        if transcript_md_object not in markdown_uploads and transcript_md_object in existing_md_objects:
            markdown_uploads.append(transcript_md_object)
    else:
        result["transcript_markdown"] = None
        result["transcript_filename"] = None

    # ── BSA notes (plain text, no conversion needed) ────────────────────────
    if "bsa_notes" in artifacts:
        result["bsa_notes"] = artifacts["bsa_notes"]["raw"].decode("utf-8", errors="replace")
    else:
        result["bsa_notes"] = None

    interface_code: str | None = None
    if "interface_code" in artifacts:
        interface_code = artifacts["interface_code"]["raw"].decode("utf-8", errors="replace").strip()

    result["markdown_uploads"] = markdown_uploads

    # ── Stateful chunk-wise extraction ──────────────────────────────────────
    combined_markdown = "\n\n".join(filter(None, [
        brd_markdown,
        layout_markdown,
        result.get("transcript_markdown"),
        result.get("bsa_notes"),
    ]))
    extraction_result = _run_stateful_extraction(
        combined_markdown=combined_markdown,
        session_id=session_id,
        bsa_input=result.get("bsa_notes"),
    )
    if interface_code:
        extraction_result.setdefault("common_rules", {})["interface_code"] = interface_code
    result["requirement_layer"] = extraction_result

    # ── Persist final JSON to GCS ────────────────────────────────────────────
    final_object = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/extracted_data/final_requirement_layer.json"
    upload_json(object_name=final_object, payload=extraction_result)
    result["gcs_output_uri"] = f"gs://{config.MAPPING_ARTIFACT_BUCKET}/{final_object}"
    logger.info("Persisted final requirement layer | uri=%s", result["gcs_output_uri"])

    return result


def run_validation(session_id: str) -> dict[str, Any]:
    """
    1. Fetch final_requirement_layer.json from GCS
    2. Fetch cached markdown files from GCS
    3. Try to cache full combined context; if too large, pre-cache every chunk
       up-front before processing, then use each chunk's cache name for inference
    4. Validate/correct all fields via LLM
    5. Persist validated JSON back to GCS
    6. Return full validated JSON in response
    """
    import json
    from agents.extract_requirement_layer_agent.tools.brd_utils import _split_markdown_semantic_chunks, _merge_nested_state

    base_prefix = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}"
    uploads_prefix = f"{base_prefix}/uploads"
    final_object = f"{base_prefix}/extracted_data/final_requirement_layer.json"

    try:
        cached_bytes = download_bytes(object_name=final_object)
    except Exception as exc:
        raise FileNotFoundError(
            f"No extracted data found for session_id={session_id!r}. "
            f"Run /extract-brd-information first. Detail: {exc}"
        )

    extracted: dict[str, Any] = _safe_json_load(cached_bytes.decode("utf-8"))

    md_prefix = f"{base_prefix}/markdown_files/"
    md_blobs = list_blobs(prefix=md_prefix)
    context_parts: list[str] = []
    for blob in md_blobs:
        try:
            md_bytes = download_bytes(object_name=blob.name)
            context_parts.append(md_bytes.decode("utf-8", errors="replace"))
        except Exception:
            logger.warning("Could not fetch markdown blob: %s", blob.name)

    bsa_notes_object = f"{uploads_prefix}/bsa_notes.txt"
    try:
        notes_bytes = download_bytes(object_name=bsa_notes_object)
        context_parts.append(notes_bytes.decode("utf-8", errors="replace"))
    except Exception:
        pass

    combined_context = "\n\n".join(context_parts)
    client = _get_client()

    # Extend schema to allow _validation_corrections so Vertex AI doesn't strip it
    validation_schema = {
        **response_schema,
        "properties": {
            **response_schema["properties"],
            "_validation_corrections": {"type": "array", "items": {"type": "string"}},
        },
    }

    def _call_validation(cache_name: str | None, inline_context: str, current_state: dict) -> dict:
        """
        Call the validation LLM.

        If `cache_name` is provided the cached content is referenced directly and
        `inline_context` is ignored.  Otherwise `inline_context` is embedded in the
        prompt body.
        """
        if cache_name:
            prompt = _VALIDATION_PROMPT.format(
                context="(see cached context)",
                extracted=json.dumps(current_state, indent=2),
            )
            call_config: dict[str, Any] = {
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "max_output_tokens": 8192,
                "response_schema": validation_schema,
                "cached_content": cache_name,
            }
        else:
            prompt = _VALIDATION_PROMPT.format(
                context=inline_context,
                extracted=json.dumps(current_state, indent=2),
            )
            call_config = {
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "max_output_tokens": 8192,
                "response_schema": validation_schema,
            }
        resp = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=[prompt],
            config=call_config,
        )
        result = _safe_json_load((resp.text or "{}").strip())
        # If LLM returned empty/incomplete, preserve current_state so data is never lost
        if not result or not any(result.get(k) for k in ("scope", "bsa_input", "requirements", "filters_and_parameters", "file_specs", "common_rules")):
            logger.warning("Validation LLM returned empty/incomplete result — preserving input state")
            return dict(current_state)
        return result

    all_corrections: list[str] = []

    # ── Strategy 1: try to fit everything into a single context cache ────────
    full_cache_name = _create_cache(
        client,
        content=combined_context,
        display_name=f"validation-context-{session_id}",
    )

    original_bsa_input = extracted.get("bsa_input", "")

    if full_cache_name:
        logger.info("Validation using full cached context | session=%s", session_id)
        try:
            result = _call_validation(full_cache_name, "", extracted)
        finally:
            _delete_cache(client, full_cache_name)
        all_corrections = result.pop("_validation_corrections", [])
        validated = result

    else:
        # ── Strategy 2: pre-cache every chunk, then iterate ──────────────────
        chunks = _split_markdown_semantic_chunks(combined_context)
        logger.info(
            "Validation using pre-cached chunk context | session=%s chunks=%d",
            session_id, len(chunks),
        )

        chunk_cache_names = _get_or_build_chunk_caches(client, chunks, session_id, "validation")

        running_state = dict(extracted)
        for idx, (chunk, cache_name) in enumerate(zip(chunks, chunk_cache_names)):
            chunk_result = _call_validation(cache_name, chunk, running_state)
            chunk_corrections = chunk_result.pop("_validation_corrections", [])
            all_corrections.extend(chunk_corrections)
            running_state = _merge_nested_state(running_state, chunk_result)
            logger.info(
                "Validation chunk %d/%d done | cache=%s corrections=%d",
                idx + 1, len(chunks), cache_name or "inline", len(chunk_corrections),
            )
        # Do NOT delete validation caches — let them expire via TTL.
        # Deleting them here causes 400 INVALID errors on subsequent validation runs
        # because the GCS registry still references the now-deleted cache names.

        validated = running_state

    # Restore bsa_input if validation degraded or cleared it
    if original_bsa_input and len(validated.get("bsa_input", "")) < len(original_bsa_input):
        validated["bsa_input"] = original_bsa_input

    _pin_interface_code(validated, session_id)
    corrections_made = bool(all_corrections)
    validated_object = f"{base_prefix}/extracted_data/validated_requirement_layer.json"
    upload_json(object_name=validated_object, payload=validated)
    gcs_uri = f"gs://{config.MAPPING_ARTIFACT_BUCKET}/{validated_object}"
    logger.info("Persisted validated requirement layer | uri=%s corrections=%s", gcs_uri, all_corrections)

    # Read back from GCS as authoritative source — guarantees response matches what was persisted
    try:
        persisted_bytes = download_bytes(object_name=validated_object)
        final_validated = _safe_json_load(persisted_bytes.decode("utf-8"))
        if not final_validated:
            logger.warning("GCS read-back empty — using in-memory validated dict")
            final_validated = validated
    except Exception as exc:
        logger.warning("GCS read-back failed (%s) — using in-memory validated dict", exc)
        final_validated = validated

    return {
        "validated_requirement_layer": final_validated,
        "corrections_made": corrections_made,
        "corrections": all_corrections,
        "gcs_output_uri": gcs_uri,
    }


def run_brd_checkpoint(
    session_id: str,
    instruction: str,
) -> dict:
    """
    Freeform reject handler:
    - Uses instruction to re-derive fields
    - Pre-caches all context chunks up-front, then processes them sequentially
    - Merges safely into existing JSON
    """
    import json
    import copy
    from agents.extract_requirement_layer_agent.tools.brd_utils import (
        _split_markdown_semantic_chunks,
        _merge_nested_state,
    )

    base_prefix = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}"
    validated_object = f"{base_prefix}/extracted_data/validated_requirement_layer.json"

    # ─────────────────────────────────────────────
    # STEP 1: Load current state
    # ─────────────────────────────────────────────
    current_bytes = download_bytes(object_name=validated_object)
    current: dict = _safe_json_load(current_bytes.decode("utf-8"))

    if not instruction or not instruction.strip():
        md_prefix = f"{base_prefix}/markdown_files/"
        md_blobs = list_blobs(prefix=md_prefix)
        context_parts = []
        for blob in md_blobs:
            try:
                context_parts.append(download_bytes(object_name=blob.name).decode("utf-8", errors="replace"))
            except Exception:
                logger.warning("Could not fetch markdown blob: %s", blob.name)
        try:
            bsa_notes = download_bytes(object_name=f"{base_prefix}/uploads/bsa_notes.txt").decode("utf-8", errors="replace")
        except Exception:
            bsa_notes = None
        combined_markdown = "\n\n".join(context_parts)
        # Full re-extraction triggered by reject with no specific instruction —
        # runs the same stateful extraction pipeline as the initial extract.
        logger.info(
            "Reject with no instruction — running full re-extraction | session=%s",
            session_id,
        )
        extraction_result = _run_stateful_extraction(
            combined_markdown=combined_markdown,
            session_id=session_id,
            bsa_input=bsa_notes,
        )
        _pin_interface_code(extraction_result, session_id)
        upload_json(object_name=validated_object, payload=extraction_result)
        gcs_uri = f"gs://{config.MAPPING_ARTIFACT_BUCKET}/{validated_object}"
        return {"validated_requirement_layer": extraction_result, "gcs_output_uri": gcs_uri}

    # ─────────────────────────────────────────────
    # STEP 2: Load context
    # ─────────────────────────────────────────────
    md_prefix = f"{base_prefix}/markdown_files/"
    md_blobs = list_blobs(prefix=md_prefix)

    context_parts = []
    for blob in md_blobs:
        try:
            context_parts.append(
                download_bytes(object_name=blob.name).decode("utf-8", errors="replace")
            )
        except Exception:
            logger.warning("Could not fetch markdown blob: %s", blob.name)

    try:
        notes = download_bytes(object_name=f"{base_prefix}/uploads/bsa_notes.txt")
        context_parts.append(notes.decode("utf-8", errors="replace"))
    except Exception:
        pass

    combined_context = "\n\n".join(context_parts)

    # ─────────────────────────────────────────────
    # STEP 3: Pre-cache all chunks, then iterate
    # ─────────────────────────────────────────────
    client = _get_client()
    chunks = _split_markdown_semantic_chunks(combined_context)

    logger.info(
        "Freeform reject using pre-cached chunks | session=%s chunks=%d",
        session_id, len(chunks),
    )

    # Cache every chunk up-front before any LLM call.
    # Do NOT delete these caches after use — let them expire via TTL.
    # Deleting them causes 400 INVALID errors on subsequent reject runs
    # because the GCS registry still references the now-deleted cache names.
    chunk_cache_names = _get_or_build_chunk_caches(client, chunks, session_id, "checkpoint")

    running_state = copy.deepcopy(current)
    original_bsa_input = running_state.get("bsa_input", "")

    for idx, (chunk, cache_name) in enumerate(zip(chunks, chunk_cache_names)):
        if cache_name:
            prompt = f"""
You are re-deriving fields in a structured requirement layer JSON following a BSA rejection.

This extraction was previously rejected. You must re-examine the source documents and correct the fields as instructed.

CURRENT JSON:
{json.dumps(running_state, indent=2)}

REJECTION INSTRUCTION:
{instruction}

TASK:
- Source context is provided via cached content.
- Update ONLY fields relevant to the rejection instruction.
- DO NOT modify unrelated fields.
- Preserve structure.
- Return FULL JSON.
"""
            call_config: dict[str, Any] = {
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "max_output_tokens": 60000,
                "response_schema": response_schema,
                "cached_content": cache_name,
            }
        else:
            prompt = f"""
You are re-deriving fields in a structured requirement layer JSON following a BSA rejection.

This extraction was previously rejected. You must re-examine the source documents and correct the fields as instructed.

CURRENT JSON:
{json.dumps(running_state, indent=2)}

REJECTION INSTRUCTION:
{instruction}

SOURCE CONTEXT:
{chunk}

TASK:
- Update ONLY fields relevant to the rejection instruction.
- DO NOT modify unrelated fields.
- Preserve structure.
- Return FULL JSON.
"""
            call_config = {
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "max_output_tokens": 60000,
                "response_schema": response_schema,
            }

        resp = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=[prompt],
            config=call_config,
        )
        chunk_result = _safe_json_load((resp.text or "{}").strip())
        running_state = _merge_nested_state(running_state, chunk_result)
        logger.info(
            "Checkpoint chunk %d/%d processed | cache=%s",
            idx + 1, len(chunks), cache_name or "inline",
        )

    # Restore bsa_input if checkpoint degraded or cleared it
    if original_bsa_input and len(running_state.get("bsa_input", "")) < len(original_bsa_input):
        running_state["bsa_input"] = original_bsa_input

    final_result = running_state

    # ─────────────────────────────────────────────
    # STEP 4: Persist
    # ─────────────────────────────────────────────
    _pin_interface_code(final_result, session_id)
    upload_json(object_name=validated_object, payload=final_result)

    gcs_uri = f"gs://{config.MAPPING_ARTIFACT_BUCKET}/{validated_object}"

    return {
        "validated_requirement_layer": final_result,
        "gcs_output_uri": gcs_uri,
    }
