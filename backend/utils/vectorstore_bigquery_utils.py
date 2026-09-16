"""
BigQuery catalog for Vector Search datapoints.

Why we need this:
  - Vertex Vector Search cannot "list datapoints by metadata" and cannot delete by filter.
  - We store datapoint ids + metadata in BigQuery so we can:
      * dedupe ingestions
      * audit what was ingested
      * soft delete / hard delete by selecting datapoint_ids from BigQuery
      * join neighbor results back to chunk text
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import config


def ensure_vectorstore_metadata_table_exists() -> None:
    """
    Create the BigQuery table if it doesn't exist.

    Table: <BQ_PROJECT_ID>.<EVIDENCE_BQ_DATASET_ID>.<EVIDENCE_BQ_TABLE_ID>
    """
    from utils import local_warehouse as bigquery  # type: ignore

    client = bigquery.Client(project=config.BQ_PROJECT_ID)
    table_id = f"{config.BQ_PROJECT_ID}.{config.EVIDENCE_BQ_DATASET_ID}.{config.EVIDENCE_BQ_TABLE_ID}"

    try:
        client.get_table(table_id)
        return
    except Exception:
        pass

    schema = [
        bigquery.SchemaField("datapoint_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("doc_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("chunk_index", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("chunk_hash", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("chunk_text", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("evidence_type", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("authority_level", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("source_ref", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("interface_code", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("target_table_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("target_column_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("rule_type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("version", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("is_active", "BOOL", mode="REQUIRED"),
        # Traceability to the deployed vector index.
        bigquery.SchemaField("vector_index_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("vector_deployed_index_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("embedding_model", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("embedding_dimensions", "INT64", mode="NULLABLE"),
    ]

    table = bigquery.Table(table_id, schema=schema)
    # Partition by ingestion time for cheaper lifecycle operations.
    table.time_partitioning = bigquery.TimePartitioning(field="ingested_at")
    # Cluster by the fields we commonly filter on.
    table.clustering_fields = ["evidence_type", "source_ref", "interface_code"]

    client.create_table(table)


def fetch_existing_chunk_hashes(
    *,
    evidence_type: str,
    source_ref: str,
    chunk_hashes: list[str],
) -> set[str]:
    """
    Return the subset of chunk_hashes that already exist for (evidence_type, source_ref).
    """
    if not chunk_hashes:
        return set()

    from utils import local_warehouse as bigquery  # type: ignore

    client = bigquery.Client(project=config.BQ_PROJECT_ID)
    table_id = f"{config.BQ_PROJECT_ID}.{config.EVIDENCE_BQ_DATASET_ID}.{config.EVIDENCE_BQ_TABLE_ID}"

    # Parameterized query to avoid SQL injection and size blowups.
    sql = f"""
    SELECT chunk_hash
    FROM `{table_id}`
    WHERE evidence_type = @evidence_type
      AND source_ref = @source_ref
      AND chunk_hash IN UNNEST(@hashes)
      AND is_active = TRUE
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("evidence_type", "STRING", evidence_type),
            bigquery.ScalarQueryParameter("source_ref", "STRING", source_ref),
            bigquery.ArrayQueryParameter("hashes", "STRING", chunk_hashes),
        ]
    )

    rows = client.query(sql, job_config=job_config).result()
    return {str(r["chunk_hash"]) for r in rows}


def insert_metadata_rows(rows: list[dict[str, Any]]) -> None:
    """
    Insert metadata rows into BigQuery.

    Uses insert_rows_json for simplicity (no staging files).
    """
    if not rows:
        return
    from utils import local_warehouse as bigquery  # type: ignore

    client = bigquery.Client(project=config.BQ_PROJECT_ID)
    table_id = f"{config.BQ_PROJECT_ID}.{config.EVIDENCE_BQ_DATASET_ID}.{config.EVIDENCE_BQ_TABLE_ID}"

    errors = client.insert_rows_json(table_id, rows)
    if errors:
        # Fail fast; ingestion must be auditable.
        raise RuntimeError(f"BigQuery insert_rows_json errors: {errors}")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch_recent_evidence_rows_by_target(
    *,
    evidence_type: str,
    target_table_id: str,
    target_column_name: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """
    Fetch most recent evidence rows by exact target (table_id + column_name).

    Used by Step 2 retrieval for "experience" evidence (BigQuery exact match, no semantic search).

    Notes:
      - We intentionally keep this query strict: exact match only.
      - Recency ordering uses created_at (source time), falling back to ingested_at ordering implicitly.
    """
    if not target_table_id or not target_column_name or limit <= 0:
        return []

    from utils import local_warehouse as bigquery  # type: ignore

    client = bigquery.Client(project=config.BQ_PROJECT_ID)
    table_id = f"{config.BQ_PROJECT_ID}.{config.EVIDENCE_BQ_DATASET_ID}.{config.EVIDENCE_BQ_TABLE_ID}"

    sql = f"""
    SELECT
      datapoint_id,
      doc_id,
      chunk_index,
      chunk_hash,
      chunk_text,
      evidence_type,
      authority_level,
      source_ref,
      interface_code,
      target_table_id,
      target_column_name,
      rule_type,
      created_at,
      version,
      is_active
    FROM `{table_id}`
    WHERE evidence_type = @evidence_type
      AND target_table_id = @target_table_id
      AND target_column_name = @target_column_name
      AND is_active = TRUE
    ORDER BY created_at DESC
    LIMIT @limit
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("evidence_type", "STRING", str(evidence_type).upper()),
            bigquery.ScalarQueryParameter("target_table_id", "STRING", target_table_id),
            bigquery.ScalarQueryParameter("target_column_name", "STRING", target_column_name),
            bigquery.ScalarQueryParameter("limit", "INT64", int(limit)),
        ]
    )

    rows = client.query(sql, job_config=job_config).result()
    return [dict(r) for r in rows]


def fetch_evidence_rows_by_datapoint_ids(*, datapoint_ids: list[str]) -> list[dict[str, Any]]:
    """
    Fetch evidence rows for a set of datapoint ids.

    Used to join Vector Search neighbor datapoint ids back to chunk_text + metadata stored in BigQuery.
    """
    ids = [i for i in (datapoint_ids or []) if i]
    if not ids:
        return []

    from utils import local_warehouse as bigquery  # type: ignore

    client = bigquery.Client(project=config.BQ_PROJECT_ID)
    table_id = f"{config.BQ_PROJECT_ID}.{config.EVIDENCE_BQ_DATASET_ID}.{config.EVIDENCE_BQ_TABLE_ID}"

    sql = f"""
    SELECT
      datapoint_id,
      doc_id,
      chunk_index,
      chunk_hash,
      chunk_text,
      evidence_type,
      authority_level,
      source_ref,
      interface_code,
      target_table_id,
      target_column_name,
      rule_type,
      created_at,
      version,
      is_active
    FROM `{table_id}`
    WHERE datapoint_id IN UNNEST(@ids)
      AND is_active = TRUE
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", ids)],
    )

    rows = client.query(sql, job_config=job_config).result()
    return [dict(r) for r in rows]

