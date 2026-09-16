"""
FYI_CD embedding pipeline utilities.

Responsibilities:
  - Query SOURCE_TABLE (configurable below) from BigQuery
  - Embed ONLY CD_DSC via Vertex AI (pure semantic label search)
  - Store full row context + embedding in BigQuery (ARRAY<FLOAT64> column)
  - Semantic search using ML.DISTANCE returning full row context
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.settings import config

logger = logging.getLogger(__name__)

# ── Config: change this to switch the source table ───────────────────────────
SOURCE_TABLE = "ust-genai-pa-poc-gcp.DATAMAP_COPILOT.FYI_CD"

EMBED_TABLE = "datamap_similarity_search_FYI_CD"
EMBED_TABLE_FULL_ID = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{EMBED_TABLE}"
EMBEDDING_COLUMN = "combined_embedding"

BATCH_SIZE = 1000 # Vertex AI max texts per embed request

_OUTPUT_COLUMNS = [
    "db_nm",
    "tbl_vw_nm",
    "cd_colm_nm",
    "cd_val",
    "dsc_colm_nm",
    "cd_dsc",
]


def _build_cd_dsc_text(cd_dsc: str | None) -> str:
    return str(cd_dsc).strip() if cd_dsc else " "


def ensure_fyi_cd_table() -> None:
    from utils import local_warehouse as bigquery

    client = bigquery.Client(project=config.BQ_PROJECT_ID)
    try:
        client.get_table(EMBED_TABLE_FULL_ID)
        return
    except Exception:
        pass

    schema = [bigquery.SchemaField(c, "STRING", mode="NULLABLE") for c in _OUTPUT_COLUMNS]
    schema.append(bigquery.SchemaField(EMBEDDING_COLUMN, "FLOAT64", mode="REPEATED"))
    client.create_table(bigquery.Table(EMBED_TABLE_FULL_ID, schema=schema))
    logger.info("Created FYI_CD embeddings table: %s", EMBED_TABLE_FULL_ID)


async def run_fyi_cd_pipeline(
    batch_from: int | None = None,
    batch_to: int | None = None,
) -> int:
    """
    Fetch ALL rows from SOURCE_TABLE → embed CD_DSC only → store full row context in BQ.
    Runs up to 8 embed+store batches concurrently.

    Args:
        batch_from: 1-based first batch number to process (inclusive). None = start from 1.
        batch_to:   1-based last batch number to process (inclusive). None = run to end.

    Returns total rows embedded in this run.
    """
    import os
    from utils.vectorstore_vertex_utils import embed_texts_gemini_embedding
    from utils import local_warehouse as bigquery

    ensure_fyi_cd_table()
    bq_client = bigquery.Client(project=config.BQ_PROJECT_ID)

    sql = (
        f"SELECT DB_NM, TBL_VW_NM, CD_COLM_NM, CD_VAL, DSC_COLM_NM, CD_DSC "
        f"FROM `{SOURCE_TABLE}` "
        f"ORDER BY DB_NM, TBL_VW_NM, CD_COLM_NM"
    )
    rows = [dict(r) for r in bq_client.query(sql).result()]
    if not rows:
        logger.warning("FYI_CD pipeline: no rows found.")
        return 0

    _batch_from = batch_from if batch_from is not None else (
        int(os.environ["FYI_CD_BATCH_FROM"]) if "FYI_CD_BATCH_FROM" in os.environ else 1
    )
    _batch_to = batch_to if batch_to is not None else (
        int(os.environ["FYI_CD_BATCH_TO"]) if "FYI_CD_BATCH_TO" in os.environ else None
    )

    batches = [rows[i: i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    total_batches = len(batches)
    _from_idx = _batch_from - 1
    _to_idx = _batch_to if _batch_to is not None else total_batches
    selected_with_nums = [(i + 1, batches[i]) for i in range(_from_idx, _to_idx) if i < total_batches]

    logger.info(
        "FYI_CD: %d rows, %d total batches of %d — running batches %d–%d concurrency=8",
        len(rows), total_batches, BATCH_SIZE, _batch_from, min(_to_idx, total_batches),
    )

    total_stored = 0
    failed_batches = 0
    semaphore = asyncio.Semaphore(8)

    async def _process_batch(batch_num: int, batch: list[dict]) -> int:
        async with semaphore:
            texts = [_build_cd_dsc_text(r.get("CD_DSC")) for r in batch]
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
                    "cd_colm_nm": r.get("CD_COLM_NM"),
                    "cd_val": r.get("CD_VAL"),
                    "dsc_colm_nm": r.get("DSC_COLM_NM"),
                    "cd_dsc": r.get("CD_DSC"),
                    EMBEDDING_COLUMN: vec,
                }
                for r, vec in zip(batch, vectors)
            ]
            job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: bq_client.load_table_from_json(
                    records, EMBED_TABLE_FULL_ID, job_config=job_config
                ).result()
            )
            logger.info("Batch %d/%d: stored %d rows", batch_num, total_batches, len(records))
            return len(records)

    # Track true batch numbers alongside coroutines before gathering
    tasks = [
        (batch_num, _process_batch(batch_num, batch))
        for batch_num, batch in selected_with_nums
    ]

    results = await asyncio.gather(
        *[task for _, task in tasks],
        return_exceptions=True,
    )

    for (batch_num, _), res in zip(tasks, results):
        if isinstance(res, Exception):
            failed_batches += 1
            logger.error("Batch %d failed: %s", batch_num, res)
        else:
            total_stored += res

    if failed_batches:
        logger.warning("FYI_CD pipeline complete with %d failed batch(es). Stored %d rows this run.", failed_batches, total_stored)
    else:
        logger.info("FYI_CD pipeline complete. Stored %d rows this run.", total_stored)
    return total_stored


async def search_fyi_cd(
    query_text: str,
    top_k: int = 10,
    field_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    Embed query_text and retrieve the most similar FYI_CD rows using ML.DISTANCE (cosine).
    Returns full row context: DB_NM, TBL_VW_NM, CD_COLM_NM, CD_VAL, DSC_COLM_NM, CD_DSC + distance.

    Args:
        query_text: BRD concept to embed and search against CD_DSC embeddings.
        top_k:      Number of results to return.
        field_name: Optional DART filter field name (e.g. 'CO_CD_ROLLUP_ID').
                    When provided, restricts search to rows where cd_colm_nm = field_name.
                    When None, searches across all code values (used for smoke tests).
    """
    if getattr(config, "STANDALONE_MODE", False):
        logger.info("[fyi_cd_search] standalone mode — returning no code-value matches")
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

    field_filter = "AND cd_colm_nm = @field_name" if field_name else ""
    sql = f"""
    SELECT
        db_nm,
        tbl_vw_nm,
        cd_colm_nm,
        cd_val,
        dsc_colm_nm,
        cd_dsc,
        ML.DISTANCE({EMBEDDING_COLUMN}, @query_vec, 'COSINE') AS similarity_distance
    FROM `{EMBED_TABLE_FULL_ID}`
    WHERE 1=1
    {field_filter}
    ORDER BY similarity_distance ASC
    LIMIT @top_k
    """

    query_params = [
        bigquery.ArrayQueryParameter("query_vec", "FLOAT64", query_vector),
        bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
    ]
    if field_name:
        query_params.append(bigquery.ScalarQueryParameter("field_name", "STRING", field_name.upper()))

    job_config = bigquery.QueryJobConfig(query_parameters=query_params)

    _LABELS = {
        "db_nm": "Database Name",
        "tbl_vw_nm": "Table/View Name",
        "cd_colm_nm": "Code Column Name",
        "cd_val": "Code Value",
        "dsc_colm_nm": "Description Column Name",
        "cd_dsc": "Code Description",
        "similarity_distance": "Similarity Distance",
    }

    rows = client.query(sql, job_config=job_config).result()
    return [{_LABELS.get(k, k): v for k, v in dict(r).items()} for r in rows]
