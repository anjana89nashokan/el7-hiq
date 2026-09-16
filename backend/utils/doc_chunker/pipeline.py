"""
Core extraction pipeline.

Per-chunk loop (sequential — context must flow forward):
  1. Build Call-1 prompt  (injects previous handoff summary + open-section state)
  2. Call-1 → ExtractionResult  (typed buckets: requirements, scope, file_layout, tables)
  3. Update ChunkContext from Call-1 output
  4. Build Call-2 prompt  (compact text summary only — no PDF bytes)
  5. Call-2 → DomainScoringResult
  6. Accumulate domain scores

Post-loop:
  - Merge each typed bucket across chunks (continuation-aware)
  - Average domain scores → final_domain

Entry point: run_extraction_pipeline()
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Dict, List, Optional

from .llm_calls import call_domain_scoring, call_extraction
from .models import (
    ChunkContext,
    DomainScoringResult,
    ExtractionResult,
    FileLayoutRecord,
    GenericTable,
    OpenSectionState,
    PipelineResult,
    Requirement,
    ScopeItem,
)
from .prompts import build_extraction_summary, domain_scoring_prompt, extraction_prompt
from .splitter import prepare_document, split_pdf_to_chunks

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-type merging helpers
# ---------------------------------------------------------------------------

def _merge_requirements(chunks: List[ExtractionResult]) -> List[Requirement]:
    """Concatenate requirements across chunks; deduplicate by description."""
    seen: set[str] = set()
    merged: List[Requirement] = []
    for ext in chunks:
        for req in ext.requirements:
            key = req.description.strip().lower()
            if key not in seen:
                seen.add(key)
                merged.append(req)
    return merged


def _merge_scope(chunks: List[ExtractionResult], attr: str) -> List[ScopeItem]:
    """Concatenate in_scope or out_of_scope items; deduplicate by description."""
    seen: set[str] = set()
    merged: List[ScopeItem] = []
    for ext in chunks:
        for item in getattr(ext, attr):
            key = item.description.strip().lower()
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def _merge_file_layout(chunks: List[ExtractionResult]) -> List[FileLayoutRecord]:
    """
    Concatenate file layout records across chunks.
    Continuation is handled by the model (it skips already-extracted rows),
    so we just concatenate and deduplicate on (section, field_name, position_start).
    """
    seen: set[tuple] = set()
    merged: List[FileLayoutRecord] = []
    for ext in chunks:
        for rec in ext.file_layout:
            key = (
                (rec.section or "").lower(),
                rec.field_name.strip().lower(),
                rec.position_start or "",
            )
            if key not in seen:
                seen.add(key)
                merged.append(rec)
    return merged


def _merge_generic_tables(chunks: List[ExtractionResult]) -> List[GenericTable]:
    """
    Stitch generic tables across chunks using is_continuation / is_complete flags.
    Same strategy as the original table stitcher.
    """
    stitched: List[GenericTable] = []
    open_tbl: Optional[GenericTable] = None

    for ext in chunks:
        for i, tbl in enumerate(ext.generic_tables):
            if i == 0 and tbl.is_continuation and open_tbl is not None:
                new_rows = tbl.rows
                # Drop duplicate last row if model repeated it
                if open_tbl.rows and new_rows and open_tbl.rows[-1] == new_rows[0]:
                    new_rows = new_rows[1:]
                open_tbl.rows.extend(new_rows)
                if tbl.is_complete:
                    open_tbl.is_complete = True
                    stitched.append(open_tbl)
                    open_tbl = None
                continue

            if open_tbl is not None:
                open_tbl.is_complete = True
                stitched.append(open_tbl)
                open_tbl = None

            if tbl.is_complete:
                stitched.append(tbl)
            else:
                open_tbl = tbl

    if open_tbl is not None:
        open_tbl.is_complete = True
        stitched.append(open_tbl)

    for tbl in stitched:
        if tbl.headers:
            tbl.rows = [r for r in tbl.rows if r != tbl.headers]
        tbl.rows = [r for r in tbl.rows if any(c not in (None, "", "nan", "None") for c in r)]

    return stitched


# ---------------------------------------------------------------------------
# Domain score aggregation
# ---------------------------------------------------------------------------

def _aggregate_domain_scores(results: List[DomainScoringResult]) -> Dict[str, float]:
    if not results:
        return {}
    totals: Dict[str, float] = {}
    for dr in results:
        for label, score in dr.scores.items():
            totals[label] = totals.get(label, 0.0) + score
    n = len(results)
    return {label: round(total / n, 3) for label, total in totals.items()}


def _pick_final_domain(scores: Dict[str, float]) -> str:
    return max(scores, key=lambda k: scores[k]) if scores else "other"


# ---------------------------------------------------------------------------
# Context update after Call-1
# ---------------------------------------------------------------------------

def _update_context(context: ChunkContext, extraction: ExtractionResult) -> ChunkContext:
    return ChunkContext(
        previous_handoff_summary=extraction.handoff_summary,
        open_section=extraction.open_section,  # None if all sections closed
        accumulated_domain_scores=context.accumulated_domain_scores,
        chunks_processed=context.chunks_processed + 1,
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_extraction_pipeline(
    file_path: str,
    chunk_size: int = 5,
    extraction_max_tokens: int = 32_768,
    domain_max_tokens: int = 2_048,
    retries: int = 2,
    retry_delay: float = 1.0,
) -> PipelineResult:
    """
    Single-pass extraction pipeline with typed section buckets and rolling context.

    Args:
        file_path:              Path to a .pdf or .docx file.
        chunk_size:             Pages per chunk (default 5).
        extraction_max_tokens:  Max output tokens for Call-1 (extraction).
        domain_max_tokens:      Max output tokens for Call-2 (domain scoring).
        retries:                Retry attempts per LLM call on failure.
        retry_delay:            Base delay (seconds) between retries.

    Returns:
        PipelineResult with typed merged outputs and final domain.
    """
    if sys.platform == "win32":
        import pythoncom
        pythoncom.CoInitialize()

    working_pdf, was_converted = prepare_document(file_path)

    try:
        chunks, total_pages = split_pdf_to_chunks(working_pdf, chunk_size=chunk_size)
        logger.info(
            "Pipeline start | file=%s chunks=%d total_pages=%d",
            os.path.basename(file_path), len(chunks), total_pages,
        )

        context = ChunkContext()
        chunk_extractions: List[ExtractionResult] = []
        chunk_domain_scores: List[DomainScoringResult] = []
        failed_chunks: Dict[int, str] = {}

        for idx, chunk_bytes in enumerate(chunks):
            start_page = idx * chunk_size + 1
            end_page = min((idx + 1) * chunk_size, total_pages)
            page_range = f"{start_page}-{end_page}"

            # ── Call 1: typed extraction ────────────────────────────────────
            ext_prompt = extraction_prompt(idx, page_range, context)
            try:
                extraction = call_extraction(
                    chunk_bytes=chunk_bytes,
                    prompt=ext_prompt,
                    chunk_index=idx,
                    page_range=page_range,
                    max_tokens=extraction_max_tokens,
                    retries=retries,
                    retry_delay=retry_delay,
                )
                chunk_extractions.append(extraction)
            except Exception as exc:
                logger.error("Extraction failed permanently for chunk %d: %s", idx, exc)
                failed_chunks[idx] = str(exc)
                context = ChunkContext(
                    previous_handoff_summary="Previous chunk failed — no context available.",
                    accumulated_domain_scores=context.accumulated_domain_scores,
                    chunks_processed=context.chunks_processed + 1,
                )
                continue

            # ── Update rolling context ──────────────────────────────────────
            context = _update_context(context, extraction)

            # ── Call 2: domain scoring (text-only, cheap) ───────────────────
            open_type = extraction.open_section.section_type if extraction.open_section else None
            summary = build_extraction_summary(
                chunk_index=idx,
                page_range=page_range,
                n_requirements=len(extraction.requirements),
                n_in_scope=len(extraction.in_scope),
                n_out_of_scope=len(extraction.out_of_scope),
                n_file_layout=len(extraction.file_layout),
                n_generic_tables=len(extraction.generic_tables),
                handoff=extraction.handoff_summary,
                open_section_type=open_type,
            )
            dom_prompt = domain_scoring_prompt(idx, page_range, summary)
            try:
                domain_result = call_domain_scoring(
                    prompt=dom_prompt,
                    chunk_index=idx,
                    max_tokens=domain_max_tokens,
                    retries=retries,
                    retry_delay=retry_delay,
                )
                chunk_domain_scores.append(domain_result)
                for label, score in domain_result.scores.items():
                    context.accumulated_domain_scores[label] = (
                        context.accumulated_domain_scores.get(label, 0.0) + score
                    )
            except Exception as exc:
                # Domain scoring is non-fatal — extraction result is still kept
                logger.warning("Domain scoring failed for chunk %d (non-fatal): %s", idx, exc)

        # ── Post-processing: merge all typed buckets ────────────────────────
        requirements = _merge_requirements(chunk_extractions)
        in_scope = _merge_scope(chunk_extractions, "in_scope")
        out_of_scope = _merge_scope(chunk_extractions, "out_of_scope")
        file_layout = _merge_file_layout(chunk_extractions)
        generic_tables = _merge_generic_tables(chunk_extractions)

        final_scores = _aggregate_domain_scores(chunk_domain_scores)
        final_domain = _pick_final_domain(final_scores)

        logger.info(
            "Pipeline complete | reqs=%d in_scope=%d out_scope=%d layout=%d tables=%d "
            "final_domain=%s failed=%d",
            len(requirements), len(in_scope), len(out_of_scope),
            len(file_layout), len(generic_tables),
            final_domain, len(failed_chunks),
        )

        return PipelineResult(
            document_path=file_path,
            total_chunks=len(chunks),
            total_pages=total_pages,
            chunk_extractions=chunk_extractions,
            chunk_domain_scores=chunk_domain_scores,
            requirements=requirements,
            in_scope=in_scope,
            out_of_scope=out_of_scope,
            file_layout=file_layout,
            generic_tables=generic_tables,
            final_domain=final_domain,
            final_domain_scores=final_scores,
            failed_chunks=failed_chunks,
        )

    finally:
        if was_converted and os.path.exists(working_pdf):
            os.remove(working_pdf)
            logger.info("Removed temporary PDF: %s", working_pdf)
        if sys.platform == "win32":
            import pythoncom
            pythoncom.CoUninitialize()
