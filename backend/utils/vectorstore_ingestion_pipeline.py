"""
EvidenceHub ingestion pipeline (transcripts/playbooks).

Responsibilities:
  - Read uploaded files
  - Extract text deterministically
  - Chunk text (char-based)
  - Dedupe based on (evidence_type, source_ref, chunk_hash)
  - Embed chunks (Vertex embeddings)
  - Upsert vectors to Vertex Vector Search
  - Persist metadata + chunk text to BigQuery (catalog)

This module intentionally has no UI concerns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import UploadFile

from config.settings import config
from utils.evidence_text_extraction_utils import chunk_text, extract_text_from_bytes, sha256_text
from utils.vectorstore_bigquery_utils import (
    ensure_vectorstore_metadata_table_exists,
    fetch_existing_chunk_hashes,
    insert_metadata_rows,
    utc_now,
)
from utils.vectorstore_vertex_utils import embed_texts_gemini_embedding, epoch_seconds, upsert_datapoints_to_index

import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    files_received: int
    chunks_total: int
    chunks_deduped: int
    chunks_ingested: int


def _default_authority_for_evidence_type(evidence_type: str) -> str:
    # Simple defaults: playbooks are more curated than raw transcripts.
    if evidence_type == "PLAYBOOK":
        return "MED"
    return "LOW"


async def ingest_evidence_files(
    *,
    files: list[UploadFile],
    evidence_type: str,
    interface_code: Optional[str] = None,
    authority_level: Optional[str] = None,
    version: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> IngestResult:
    """
    Ingest uploaded evidence files into Vector Search + BigQuery.

    evidence_type: TRANSCRIPT | PLAYBOOK (for now; more types later)
    """
    if not files:
        return IngestResult(files_received=0, chunks_total=0, chunks_deduped=0, chunks_ingested=0)

    ensure_vectorstore_metadata_table_exists()

    evidence_type = (evidence_type or "").strip().upper()
    if evidence_type not in {"TRANSCRIPT", "PLAYBOOK"}:
        raise ValueError("evidence_type must be TRANSCRIPT or PLAYBOOK for file ingestion.")

    authority_level = (authority_level or _default_authority_for_evidence_type(evidence_type)).strip().upper()
    if authority_level not in {"LOW", "MED", "HIGH"}:
        raise ValueError("authority_level must be LOW|MED|HIGH")

    created_at = created_at or utc_now()
    ingested_at = utc_now()

    chunks_total = 0
    chunks_deduped = 0
    chunks_ingested = 0

    for f in files:
        filename = f.filename or "uploaded"
        data = await f.read()

        text = extract_text_from_bytes(filename=filename, data=data)
        if not text.strip():
            # Skip empty extractions (caller can decide to re-upload or use a different parser later).
            continue

        chunks = chunk_text(
            text=text,
            chunk_size_chars=int(config.EVIDENCE_INGEST_CHUNK_SIZE_CHARS),
            overlap_chars=int(config.EVIDENCE_INGEST_CHUNK_OVERLAP_CHARS),
        )
        chunks_total += len(chunks)

        # Dedupe per file (source_ref = filename).
        hashes = [sha256_text(c) for c in chunks]
        existing = fetch_existing_chunk_hashes(evidence_type=evidence_type, source_ref=filename, chunk_hashes=hashes)

        # Prepare upserts and BQ rows.
        to_embed: list[str] = []
        embed_meta: list[dict[str, Any]] = []
        doc_id = str(uuid.uuid4())
        for idx, (chunk, h) in enumerate(zip(chunks, hashes, strict=False)):
            if h in existing:
                chunks_deduped += 1
                continue
            dp_id = str(uuid.uuid4())
            to_embed.append(chunk)
            embed_meta.append(
                {
                    "datapoint_id": dp_id,
                    "doc_id": doc_id,
                    "chunk_index": idx,
                    "chunk_hash": h,
                    "chunk_text": chunk,
                    "evidence_type": evidence_type,
                    "authority_level": authority_level,
                    "source_ref": filename,
                    "interface_code": interface_code,
                    "target_table_id": None,
                    "target_column_name": None,
                    "rule_type": None,
                    "created_at": created_at,
                    "ingested_at": ingested_at,
                    "version": version,
                    "is_active": True,
                    "vector_index_id": config.VECTOR_SEARCH_INDEX_ID or None,
                    "vector_deployed_index_id": config.VECTOR_SEARCH_DEPLOYED_INDEX_ID or None,
                    "embedding_model": config.EVIDENCE_EMBEDDING_MODEL,
                    "embedding_dimensions": int(config.EVIDENCE_EMBEDDING_DIMENSIONS),
                }
            )

        if not to_embed:
            continue

        # Embed chunks (async, concurrency-limited).
        vectors = await embed_texts_gemini_embedding(
            texts=to_embed,
            model=config.EVIDENCE_EMBEDDING_MODEL,
            output_dimensions=int(config.EVIDENCE_EMBEDDING_DIMENSIONS),
            max_concurrency=int(config.EVIDENCE_EMBED_MAX_CONCURRENCY),
        )

        # Build datapoints for Vector Search upsert.
        datapoints: list[dict[str, Any]] = []
        for meta, vec in zip(embed_meta, vectors, strict=False):
            created_epoch = epoch_seconds(created_at)
            dp = {
                # Vertex REST uses lowerCamel field names.
                "datapointId": meta["datapoint_id"],
                "featureVector": vec,
                "restricts": [
                    {"namespace": "evidence_type", "allowList": [meta["evidence_type"]]},
                    {"namespace": "authority_level", "allowList": [meta["authority_level"]]},
                    {"namespace": "source_ref", "allowList": [meta["source_ref"]]},
                    {"namespace": "is_active", "allowList": ["true"]},
                ],
                "numericRestricts": [
                    {"namespace": "created_at_epoch", "valueInt": int(created_epoch)},
                ],
            }
            if meta.get("interface_code"):
                dp["restricts"].append({"namespace": "interface_code", "allowList": [meta["interface_code"]]})
            datapoints.append(dp)

        await upsert_datapoints_to_index(index_id=str(config.VECTOR_SEARCH_INDEX_ID), datapoints=datapoints)
        logger.info(
            "[evidence_ingest] upserted datapoints: file=%s count=%s evidence_type=%s dims=%s",
            filename,
            len(datapoints),
            evidence_type,
            int(config.EVIDENCE_EMBEDDING_DIMENSIONS),
        )

        # Persist metadata + chunk text to BigQuery.
        bq_rows: list[dict[str, Any]] = []
        for meta in embed_meta:
            bq_rows.append(
                {
                    **meta,
                    # Convert datetimes to ISO for insert_rows_json compatibility.
                    "created_at": meta["created_at"].isoformat(),
                    "ingested_at": meta["ingested_at"].isoformat(),
                }
            )
        insert_metadata_rows(bq_rows)

        chunks_ingested += len(embed_meta)

    return IngestResult(
        files_received=len(files),
        chunks_total=chunks_total,
        chunks_deduped=chunks_deduped,
        chunks_ingested=chunks_ingested,
    )
