"""
IndeMap embedding pipeline utilities.

Responsibilities:
  - Query ihg-dart-edw-dev2.DB_SRCD2 IndeMap table
  - Convert rows to semantic text documents
  - Generate embeddings via Vertex AI (reuses vectorstore_vertex_utils)
  - Store embeddings in BigQuery (ARRAY<FLOAT64> column)
  - Semantic search using ML.DISTANCE
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.settings import config

logger = logging.getLogger(__name__)

EMBED_TABLE_FULL_ID = (
    f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.indemap_embeddings"
)

_SOURCE_COLUMNS = [
    "IM_ENTITY_SRC_COLM_SK",
    "SRC_COLM_NM",
    "LAST_UPD_TS",
    "TGT_COLM_LGC_NM",
    "TGT_COLM_DSC",
    "TGT_COLM_NM",
    "INTF_CD",
    "IM_TGT_ENTITY_COMN_FLTR_TXT",
    "IM_ENTITY_APP_TRANS_RULE_TP_CD",
    "IM_SRC_ENTITY_TXT",
    "IM_SRC_COLM_TXT",
    "IM_MAP_APP_TGT_TRANS_JOIN_TXT",
    "IM_MAP_APP_TGT_TRANS_RULE_TXT",
    "IM_MAP_APP_TGT_TRANS_RULE_SEQ_NO",
    "IM_MAP_APP_TGT_TRANS_SPCL_TXT",
    "IM_MAP_APP_TGT_TRANS_FLTR_TXT",
    "IM_ENTITY_COLM_CDC_IND",
    "IM_MAP_APP_TGT_ENTITY_ATTR_DOC_VAL",
]


# ── Row → semantic text ───────────────────────────────────────────────────────

def _row_to_target_text(row: dict[str, Any]) -> str:
    """Text used for similarity search — target identity + documentation fields."""
    def _v(k: str) -> str:
        v = row.get(k)
        return str(v).strip() if v is not None else ""

    return "\n".join(filter(None, [
        f"Target Column Name: {_v('TGT_COLM_NM')}" if _v('TGT_COLM_NM') else "",
        f"Target Column Logical Name: {_v('TGT_COLM_LGC_NM')}" if _v('TGT_COLM_LGC_NM') else "",
        f"Target Column Description: {_v('TGT_COLM_DSC')}" if _v('TGT_COLM_DSC') else "",
        f"Attribute Documentation: {_v('IM_MAP_APP_TGT_ENTITY_ATTR_DOC_VAL')}" if _v('IM_MAP_APP_TGT_ENTITY_ATTR_DOC_VAL') else "",
    ]))


_EMBED_BATCH_SIZE = 200   # rows per embed + BQ insert batch
_BQ_INSERT_CHUNK  = 500   # max rows per insert_rows_json call


# ── BQ helpers ────────────────────────────────────────────────────────────────

def ensure_embeddings_table(recreate_if_stale: bool = True) -> None:
    """Create the embeddings table. Drops and recreates if the schema is stale."""
    from utils import local_warehouse as bigquery

    client = bigquery.Client(project=config.BQ_PROJECT_ID)
    try:
        tbl = client.get_table(EMBED_TABLE_FULL_ID)
        existing = {f.name for f in tbl.schema}
        # Stale if it has the old 'embedding' column or is missing new columns
        is_stale = "embedding" in existing or "target_embedding" not in existing
        if not is_stale:
            return
        if recreate_if_stale:
            logger.info("Stale schema detected — dropping and recreating %s", EMBED_TABLE_FULL_ID)
            client.delete_table(EMBED_TABLE_FULL_ID)
        else:
            return
    except Exception:
        pass  # table doesn't exist yet

    schema = [bigquery.SchemaField(c, "STRING", mode="NULLABLE") for c in _SOURCE_COLUMNS]
    schema.append(bigquery.SchemaField("target_embedding", "FLOAT64", mode="REPEATED"))
    client.create_table(bigquery.Table(EMBED_TABLE_FULL_ID, schema=schema))
    logger.info("Created embeddings table: %s", EMBED_TABLE_FULL_ID)


def fetch_indemap_rows() -> list[dict[str, Any]]:
    """Fetch ALL rows from the IndeMap source table (no limit)."""
    from utils import local_warehouse as bigquery

    cols = ", ".join(_SOURCE_COLUMNS)
    sql = (
        f"SELECT {cols} "
        f"FROM `{config.INDEMAP_SOURCE_PROJECT}.{config.INDEMAP_SOURCE_DATASET}.{config.INDEMAP_SOURCE_TABLE}`"
    )
    client = bigquery.Client(project=config.BQ_PROJECT_ID)
    return [dict(r) for r in client.query(sql).result()]


def _store_batch(client: Any, records: list[dict[str, Any]]) -> None:
    """Insert a batch of records into BQ, chunked to stay under the 10 MB limit."""
    def _serialize(v: Any) -> Any:
        import datetime
        if isinstance(v, (datetime.datetime, datetime.date)):
            return v.isoformat()
        return v

    serialized = [
        {k: _serialize(v) for k, v in rec.items() if k != "target_embedding"} | {"target_embedding": rec["target_embedding"]}
        for rec in records
    ]
    for i in range(0, len(serialized), _BQ_INSERT_CHUNK):
        chunk = serialized[i : i + _BQ_INSERT_CHUNK]
        errors = client.insert_rows_json(EMBED_TABLE_FULL_ID, chunk)
        if errors:
            raise RuntimeError(f"BQ insert errors: {errors}")


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_embedding_pipeline() -> int:
    """
    Full pipeline: fetch ALL rows → embed in batches → store in BQ.
    Each batch is independently retried on embed failure.
    Returns total number of rows successfully embedded.
    """
    from utils.vectorstore_vertex_utils import embed_texts_gemini_embedding
    from utils import local_warehouse as bigquery

    ensure_embeddings_table()

    rows = fetch_indemap_rows()
    if not rows:
        logger.warning("No rows fetched from IndeMap source table.")
        return 0

    logger.info("Fetched %d rows — embedding in batches of %d", len(rows), _EMBED_BATCH_SIZE)
    bq_client = bigquery.Client(project=config.BQ_PROJECT_ID)
    total_stored = 0
    failed_batches = 0

    for batch_start in range(0, len(rows), _EMBED_BATCH_SIZE):
        batch = rows[batch_start : batch_start + _EMBED_BATCH_SIZE]
        batch_num = batch_start // _EMBED_BATCH_SIZE + 1
        try:
            texts = [_row_to_target_text(r) for r in batch]
            vectors = await embed_texts_gemini_embedding(
                texts=texts,
                model=config.EVIDENCE_EMBEDDING_MODEL,
                output_dimensions=int(config.EVIDENCE_EMBEDDING_DIMENSIONS),
                max_concurrency=int(config.EVIDENCE_EMBED_MAX_CONCURRENCY),
            )
            records = [
                {**{c: row.get(c) for c in _SOURCE_COLUMNS}, "target_embedding": vec}
                for row, vec in zip(batch, vectors)
            ]
            _store_batch(bq_client, records)
            total_stored += len(batch)
            logger.info("Batch %d: stored %d rows (total %d)", batch_num, len(batch), total_stored)
        except Exception as exc:
            failed_batches += 1
            logger.error("Batch %d failed (rows %d-%d): %s", batch_num, batch_start, batch_start + len(batch), exc)

    if failed_batches:
        logger.warning("Pipeline complete with %d failed batch(es). Stored %d rows.", failed_batches, total_stored)
    else:
        logger.info("Pipeline complete. Stored %d rows.", total_stored)

    return total_stored


# ── Semantic search ───────────────────────────────────────────────────────────

async def search_similar_mappings(
    query_text: str,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """
    Embed the pre-built query_text and retrieve the most similar IndeMap rows
    using ML.DISTANCE (cosine).

    Returns list of dicts with all source columns + distance score.
    """
    if getattr(config, "STANDALONE_MODE", False):
        # No Vertex embeddings / IndeMap vector table locally — degrade to empty
        # so the mapping waterfall proceeds and flags fields as open items.
        logger.info("[indemap_search] standalone mode — returning no historical mappings")
        return []
    from utils.vectorstore_vertex_utils import embed_texts_gemini_embedding
    from utils import local_warehouse as bigquery

    vectors = await embed_texts_gemini_embedding(
        texts=[query_text],
        model=config.EVIDENCE_EMBEDDING_MODEL,
        output_dimensions=int(config.EVIDENCE_EMBEDDING_DIMENSIONS),
        max_concurrency=1,
    )
    query_vector = vectors[0]

    client = bigquery.Client(project=config.BQ_PROJECT_ID)

    # Only select columns that actually exist in the table to handle schema drift
    existing_fields = {f.name for f in client.get_table(EMBED_TABLE_FULL_ID).schema}
    cols = ", ".join(c for c in _SOURCE_COLUMNS if c in existing_fields)
    distance_col = "target_embedding" if "target_embedding" in existing_fields else "embedding"

    sql = f"""
    SELECT
      {cols},
      ML.DISTANCE({distance_col}, @query_vec, 'COSINE') AS similarity_distance
    FROM `{EMBED_TABLE_FULL_ID}`
    ORDER BY similarity_distance ASC
    LIMIT @top_k
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("query_vec", "FLOAT64", query_vector),
            bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
        ]
    )

    _LABELS = {
        "IM_ENTITY_SRC_COLM_SK": "Source Column SK",
        "SRC_COLM_NM": "Source Column Name",
        "LAST_UPD_TS": "Last Updated",
        "TGT_COLM_LGC_NM": "Target Column Logical Name",
        "TGT_COLM_DSC": "Target Column Description",
        "TGT_COLM_NM": "Target Column Name",
        "INTF_CD": "Interface Code",
        "IM_TGT_ENTITY_COMN_FLTR_TXT": "Common Filter",
        "IM_ENTITY_APP_TRANS_RULE_TP_CD": "Rule Type",
        "IM_SRC_ENTITY_TXT": "Source Entity",
        "IM_SRC_COLM_TXT": "Source Column",
        "IM_MAP_APP_TGT_TRANS_JOIN_TXT": "Join",
        "IM_MAP_APP_TGT_TRANS_RULE_TXT": "Transformation Rule",
        "IM_MAP_APP_TGT_TRANS_RULE_SEQ_NO": "Rule Sequence",
        "IM_MAP_APP_TGT_TRANS_SPCL_TXT": "Special Consideration",
        "IM_MAP_APP_TGT_TRANS_FLTR_TXT": "Filter",
        "IM_ENTITY_COLM_CDC_IND": "CDC Indicator",
        "IM_MAP_APP_TGT_ENTITY_ATTR_DOC_VAL": "Attribute Documentation",
        "similarity_distance": "Similarity Distance",
    }

    rows = client.query(sql, job_config=job_config).result()
    return [
        {_LABELS.get(k, k): v for k, v in dict(r).items()}
        for r in rows
    ]
