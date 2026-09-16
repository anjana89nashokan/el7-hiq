"""
FYI_TBL_COLS embedding pipeline utilities.

Responsibilities:
  - Query ust-genai-pa-poc-gcp.DATAMAP_COPILOT.FYI_TBL_COLS (BigQuery)
  - Embed COLM_NM + ATTR_NM + ATTR_DSC via Vertex AI
  - Store embeddings in BigQuery (ARRAY<FLOAT64> column)
  - Semantic search using ML.DISTANCE
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.settings import config

logger = logging.getLogger(__name__)

FYI_TBL_COLS_SOURCE = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{config.FYI_TABLE_ID}"
FYI_TBL_COLS_EMBED_TABLE = "datamap_similarity_search_FYI_TBL_COLS"
FYI_TBL_COLS_EMBED_TABLE_FULL_ID = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{FYI_TBL_COLS_EMBED_TABLE}"
FYI_TBL_COLS_EMBEDDING_COLUMN = "combined_embedding"

BATCH_SIZE = 250  # Vertex AI max texts per embed request

_FYI_TBL_COLS_OUTPUT_COLUMNS = [
    "db_nm",
    "tbl_vw_nm",
    "enty_dsc",
    "colm_nm",
    "attr_nm",
    "attr_dsc",
]


def _build_combined_text(column_name: str | None, attr_name: str | None, description: str | None) -> str:
    parts = [
        str(column_name).strip() if column_name else "",
        str(attr_name).strip() if attr_name else "",
        str(description).strip() if description else "",
    ]
    combined = ". ".join(p for p in parts if p)
    return combined if combined else " "


def ensure_fyi_tbl_cols_table() -> None:
    from utils import local_warehouse as bigquery

    client = bigquery.Client(project=config.BQ_PROJECT_ID)
    try:
        client.get_table(FYI_TBL_COLS_EMBED_TABLE_FULL_ID)
        return
    except Exception:
        pass

    schema = [bigquery.SchemaField(c, "STRING", mode="NULLABLE") for c in _FYI_TBL_COLS_OUTPUT_COLUMNS]
    schema.append(bigquery.SchemaField(FYI_TBL_COLS_EMBEDDING_COLUMN, "FLOAT64", mode="REPEATED"))
    client.create_table(bigquery.Table(FYI_TBL_COLS_EMBED_TABLE_FULL_ID, schema=schema))
    logger.info("Created FYI_TBL_COLS embeddings table: %s", FYI_TBL_COLS_EMBED_TABLE_FULL_ID)


def _get_fyi_embedded_row_count(client: Any) -> int:
    """Return the number of rows already stored in the FYI embeddings table."""
    try:
        result = client.query(f"SELECT COUNT(*) AS cnt FROM `{FYI_TBL_COLS_EMBED_TABLE_FULL_ID}`").result()
        return next(iter(result))["cnt"]
    except Exception:
        return 0


async def run_fyi_tbl_cols_pipeline(
    batch_from: int | None = None,
    batch_to: int | None = None,
) -> int:
    """
    Fetch ALL rows from FYI_TBL_COLS BQ table → embed COLM_NM + ATTR_NM + ATTR_DSC → store in BQ.
    Resumes from the last successfully stored row if a previous run was interrupted.
    Runs up to 8 embed+store batches concurrently.

    Args:
        batch_from: 1-based first batch number to process (inclusive). None = start from 1.
        batch_to:   1-based last batch number to process (inclusive). None = run to end.

    Returns total rows embedded in this run.
    """
    from utils.vectorstore_vertex_utils import embed_texts_gemini_embedding
    from utils import local_warehouse as bigquery

    ensure_fyi_tbl_cols_table()
    bq_client = bigquery.Client(project=config.BQ_PROJECT_ID)

    import os
    sql = (
        f"SELECT DB_NM, TBL_VW_NM, ENTY_DSC, COLM_NM, ATTR_NM, ATTR_DSC "
        f"FROM `{FYI_TBL_COLS_SOURCE}` "
        f"ORDER BY DB_NM, TBL_VW_NM, COLM_NM"
    )
    rows = [dict(r) for r in bq_client.query(sql).result()]
    if not rows:
        logger.warning("FYI_TBL_COLS pipeline: no rows found.")
        return 0

    # resolve batch range: explicit args > env vars > defaults
    _batch_from = batch_from if batch_from is not None else (
        int(os.environ["FYI_BATCH_FROM"]) if "FYI_BATCH_FROM" in os.environ else 1
    )
    _batch_to = batch_to if batch_to is not None else (
        int(os.environ["FYI_BATCH_TO"]) if "FYI_BATCH_TO" in os.environ else None
    )

    batches = [rows[i: i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    total_batches = len(batches)
    _from_idx = _batch_from - 1                                          # 0-based inclusive
    _to_idx   = _batch_to if _batch_to is not None else total_batches    # 0-based exclusive
    selected_with_nums = [(i + 1, batches[i]) for i in range(_from_idx, _to_idx) if i < total_batches]

    logger.info(
        "FYI_TBL_COLS: %d rows, %d total batches of %d — running batches %d–%d concurrency=8",
        len(rows), total_batches, BATCH_SIZE, _batch_from, min(_to_idx, total_batches),
    )
    total_stored = 0
    failed_batches = 0
    semaphore = asyncio.Semaphore(8)

    async def _process_batch(batch_num: int, batch: list[dict]) -> int:
        async with semaphore:
            texts = [_build_combined_text(r.get("COLM_NM"), r.get("ATTR_NM"), r.get("ATTR_DSC")) for r in batch]
            vectors = await embed_texts_gemini_embedding(
                texts=texts,
                model=config.EVIDENCE_EMBEDDING_MODEL,
                output_dimensions=int(config.EVIDENCE_EMBEDDING_DIMENSIONS),
                max_concurrency=int(config.EVIDENCE_EMBED_MAX_CONCURRENCY),
            )
            records = [
                {
                    "db_nm": r.get("DB_NM"),
                    "tbl_vw_nm": r.get("TBL_VW_NM"),
                    "enty_dsc": r.get("ENTY_DSC"),
                    "colm_nm": r.get("COLM_NM"),
                    "attr_nm": r.get("ATTR_NM"),
                    "attr_dsc": r.get("ATTR_DSC"),
                    FYI_TBL_COLS_EMBEDDING_COLUMN: vec,
                }
                for r, vec in zip(batch, vectors)
            ]
            job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: bq_client.load_table_from_json(
                    records, FYI_TBL_COLS_EMBED_TABLE_FULL_ID, job_config=job_config
                ).result()
            )
            logger.info("Batch %d/%d: stored %d rows", batch_num, len(batches), len(records))
            return len(records)

    results = await asyncio.gather(
        *[_process_batch(i, b) for i, b in selected_with_nums],
        return_exceptions=True,
    )

    for i, res in enumerate(results, 1):
        if isinstance(res, Exception):
            failed_batches += 1
            logger.error("Batch %d failed: %s", i, res)
        else:
            total_stored += res

    if failed_batches:
        logger.warning("FYI pipeline complete with %d failed batch(es). Stored %d rows this run.", failed_batches, total_stored)
    else:
        logger.info("FYI_TBL_COLS pipeline complete. Stored %d rows this run.", total_stored)
    return total_stored


async def search_fyi_tbl_cols(
    query_text: str,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """
    Embed query_text and retrieve the most similar FYI_TBL_COLS rows using ML.DISTANCE (cosine).
    Returns DB name, table name, entity description, attribute name, and similarity distance.
    """
    if getattr(config, "STANDALONE_MODE", False):
        logger.info("[fyi_tbl_search] standalone mode — returning no table-column matches")
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
    sql = f"""
    SELECT
        db_nm,
        tbl_vw_nm,
        enty_dsc,
        ML.DISTANCE({FYI_TBL_COLS_EMBEDDING_COLUMN}, @query_vec, 'COSINE') AS similarity_distance
    FROM `{FYI_TBL_COLS_EMBED_TABLE_FULL_ID}`
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
        "db_nm": "Database Name",
        "tbl_vw_nm": "Table Name",
        "enty_dsc": "Table Entity Description",
        "similarity_distance": "Similarity Distance",
    }

    rows = client.query(sql, job_config=job_config).result()
    return [{_LABELS.get(k, k): v for k, v in dict(r).items()} for r in rows]
