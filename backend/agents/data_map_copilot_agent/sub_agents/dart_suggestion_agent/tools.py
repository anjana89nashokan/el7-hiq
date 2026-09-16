"""
DART Suggestion Agent Tools

Pipeline executed by get_dart_suggestions (single tool):
  1. Vector search (REAL) → up to 10 DART candidates per source column
     Uses BQ VECTOR_SEARCH against datamap_similarity_search_fyi table
  2. MDR filter → keep only tables with RCMND_STS_CD='R' in mdr.dbo.DB_TBL_VW
  3. Return top N MDR-approved candidates per source column
     (if none pass filter → no_results=True with reason)
"""

import concurrent.futures
import logging
import time
from typing import List, Dict, Any

from vertexai.language_models import TextEmbeddingModel
from utils import local_warehouse as bigquery
from google.adk.tools import ToolContext
from pydantic import BaseModel, Field

from config.settings import config
from utils.bg_query_utils import get_bigquery_client
from utils.indemap_db_utils import fetch_mdr_recommended_tables

logger = logging.getLogger(__name__)

VECTOR_SEARCH_MAX = 10
SUGGESTION_TOP_N = config.DART_SUGGESTION_TOP_N
EMBEDDING_COLUMN = "combined_embedding"
DART_TABLE_FQN = f"{config.DART_PROJECT_ID}.{config.DART_DATASET_ID}.{config.DART_VECTOR_TABLE}"

# =============================================================================
# Embedding model — module-level singleton
# =============================================================================

_embedding_model: TextEmbeddingModel | None = None


def _get_embedding_model() -> TextEmbeddingModel:
    global _embedding_model

    if _embedding_model is None:
        logger.info("[dart_suggestion] Initializing text embedding model")
        _embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-005")

    return _embedding_model


# =============================================================================
# Parallel embedding generator
# =============================================================================

def generate_embeddings_parallel(text_list, chunk_size=200, max_workers=5):
    """
    Parallel embedding generator.
    - Handles empty/None values
    - Preserves original list order
    - Parallelises API calls across chunks
    """
    # Step 1: Clean input safely
    cleaned_texts = []
    empty_indexes = []

    for i, text in enumerate(text_list):
        if text is None:
            cleaned_texts.append(None)
            empty_indexes.append(i)
        else:
            text = str(text).strip()
            if text == "":
                cleaned_texts.append(None)
                empty_indexes.append(i)
            else:
                cleaned_texts.append(text)

    valid_texts = [t for t in cleaned_texts if t is not None]

    if not valid_texts:
        return [None] * len(text_list)

    # Step 2: Create chunks
    chunks = [
        valid_texts[i:i + chunk_size]
        for i in range(0, len(valid_texts), chunk_size)
    ]

    # Step 3: Define worker
    def embed_chunk(chunk):
        response = _get_embedding_model().get_embeddings(chunk)
        return [r.values for r in response]

    # Step 4: Parallel execution
    all_embeddings = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(embed_chunk, chunks))

    for result in results:
        all_embeddings.extend(result)

    # Step 5: Rebuild original order
    final_embeddings = []
    valid_index = 0

    for i in range(len(cleaned_texts)):
        if cleaned_texts[i] is None:
            final_embeddings.append(None)
        else:
            final_embeddings.append(all_embeddings[valid_index])
            valid_index += 1

    return final_embeddings


# =============================================================================
# Input schema — Pydantic model so ADK generates a proper JSON schema for Gemini
# =============================================================================

class SourceColumnInput(BaseModel):
    source_table: str = Field("", description="Source table name")
    column_name: str = Field(..., description="Source column name to find DART matches for")
    column_description: str = Field("", description="Business description of the source column")


# =============================================================================
# Vector search — REAL (BQ VECTOR_SEARCH against dart embedding table)
# =============================================================================

def _vector_search_real(source_columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Run BQ VECTOR_SEARCH for each source column using column_name + column_description
    as the query embedding. Returns up to VECTOR_SEARCH_MAX candidates per column.
    """
    logger.info("[vector_search] Initialising BQ client")
    client = get_bigquery_client()
    logger.info("[vector_search] BQ client ready — target table: %s", DART_TABLE_FQN)

    results = []
    total = len(source_columns)

    for idx, src_col in enumerate(source_columns, start=1):
        column_name = src_col.get("column_name", "")
        column_desc = src_col.get("column_description", "")
        query_text = f"{column_name}. {column_desc}"

        logger.info(
            "[vector_search] [%d/%d] START '%s' | query_text: '%s'",
            idx, total, column_name, query_text,
        )

        # ── Step A: Generate embedding ────────────────────────────────
        try:
            logger.info("[vector_search] [%d/%d] '%s' — generating embedding", idx, total, column_name)
            query_embedding = generate_embeddings_parallel([query_text])[0]
        except Exception as e:
            logger.error(
                "[vector_search] [%d/%d] '%s' — embedding generation failed: %s (%s)",
                idx, total, column_name, e, type(e).__name__,
            )
            results.append({
                "source_info": {
                    "column_name": column_name,
                    "column_description": column_desc,
                    "source_table": src_col.get("source_table", ""),
                },
                "dart_tables": [],
            })
            continue

        if query_embedding is None:
            logger.warning(
                "[vector_search] [%d/%d] '%s' — embedding returned None (empty/invalid input text)",
                idx, total, column_name,
            )
            results.append({
                "source_info": {
                    "column_name": column_name,
                    "column_description": column_desc,
                    "source_table": src_col.get("source_table", ""),
                },
                "dart_tables": [],
            })
            continue

        logger.info(
            "[vector_search] [%d/%d] '%s' — embedding generated, dimension: %d",
            idx, total, column_name, len(query_embedding),
        )

        # ── Step B: Build and run BQ VECTOR_SEARCH query ─────────────
        query = f"""
        SELECT
          base.database_name,
          base.table_name,
          base.table_business_name,
          base.table_business_description,
          base.column_name,
          base.column_business_name,
          base.column_business_description,
          distance
        FROM VECTOR_SEARCH(
          TABLE `{DART_TABLE_FQN}`,
          '{EMBEDDING_COLUMN}',
          (SELECT @query_embedding AS {EMBEDDING_COLUMN}),
          top_k => {VECTOR_SEARCH_MAX}
        )
        ORDER BY distance ASC
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter(
                    "query_embedding",
                    "FLOAT64",
                    query_embedding,
                )
            ]
        )

        try:
            logger.info(
                "[vector_search] [%d/%d] '%s' — submitting BQ VECTOR_SEARCH (top_k=%d, embedding_col=%s)",
                idx, total, column_name, VECTOR_SEARCH_MAX, EMBEDDING_COLUMN,
            )
            query_job = client.query(query, job_config=job_config)
            logger.info(
                "[vector_search] [%d/%d] '%s' — BQ job submitted: %s",
                idx, total, column_name, query_job.job_id,
            )

            df = query_job.to_dataframe()
            logger.info(
                "[vector_search] [%d/%d] '%s' — BQ job complete: %d row(s) returned",
                idx, total, column_name, len(df),
            )

        except Exception as e:
            logger.error(
                "[vector_search] [%d/%d] '%s' — BQ query failed: %s (%s)",
                idx, total, column_name, e, type(e).__name__,
            )
            results.append({
                "source_info": {
                    "column_name": column_name,
                    "column_description": column_desc,
                    "source_table": src_col.get("source_table", ""),
                },
                "dart_tables": [],
            })
            continue

        # ── Step C: Parse results ─────────────────────────────────────
        if df.empty:
            logger.warning(
                "[vector_search] [%d/%d] '%s' — BQ returned 0 rows (table may be empty or embedding mismatch)",
                idx, total, column_name,
            )

        candidates = []
        for _, row in df.iterrows():
            dist = float(row["distance"])
            candidates.append({
                "table_name": row["table_name"],
                "table_description": row["table_business_description"],
                "column": row["column_name"],
                "column_description": row["column_business_description"],
                "distance": dist,
                "similarity_score": round(1 - dist, 4),
            })
            logger.debug(
                "[vector_search] [%d/%d] '%s' — candidate: %s.%s (distance=%.4f)",
                idx, total, column_name, row["table_name"], row["column_name"], dist,
            )

        if candidates:
            logger.info(
                "[vector_search] [%d/%d] '%s' — %d candidate(s) | distance range: %.4f – %.4f",
                idx, total, column_name, len(candidates),
                candidates[0]["distance"], candidates[-1]["distance"],
            )

        results.append({
            "source_info": {
                "column_name": column_name,
                "column_description": column_desc,
                "source_table": src_col.get("source_table", ""),
            },
            "dart_tables": candidates,
        })

        logger.info("[vector_search] [%d/%d] '%s' — DONE", idx, total, column_name)

    return results


# =============================================================================
# Pipeline tool: get_dart_suggestions
# =============================================================================

def get_dart_suggestions(
    source_columns: List[SourceColumnInput],
    tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """
    Full pipeline to suggest DART tables/columns for source columns.

    Steps:
      1. Vector search (real BQ) using column_name + column_description
         → up to 10 DART candidate table+column pairs per source column
      2. MDR filter: query mdr.dbo.DB_TBL_VW to keep only RCMND_STS_CD='R' tables
         (parameterized IN clause — only checks tables from step 1, not the full MDR list)
      3. Return top N MDR-approved candidates per source column.
         If no candidates pass the filter, no_results=True with a reason.

    Args:
        source_columns: List of SourceColumnInput with column_name, column_description,
            and optionally source_table.
        tool_context: ADK tool context (injected by the framework).

    Returns:
        Dict with 'source_column_results' list and 'total_processed' count.
    """
    pipeline_start = time.time()

    # Normalize — ADK may pass SourceColumnInput instances or plain dicts
    normalized: List[Dict[str, Any]] = []
    for sc in source_columns:
        if isinstance(sc, dict):
            normalized.append(sc)
        elif hasattr(sc, "model_dump"):
            normalized.append(sc.model_dump())
        else:
            normalized.append({"column_name": str(sc), "column_description": "", "source_table": ""})

    col_names = [sc.get("column_name", "") for sc in normalized]
    logger.info(
        "[get_dart_suggestions] START — %d source column(s): %s",
        len(normalized), col_names,
    )

    # Step 1: Vector search
    vector_results = _vector_search_real(normalized)

    source_column_results = []

    for idx, vr in enumerate(vector_results, start=1):
        source_info = vr["source_info"]
        candidates = vr["dart_tables"]
        col_label = source_info["column_name"]

        logger.info(
            "[get_dart_suggestions] [%d/%d] '%s': %d vector candidate(s)",
            idx, len(vector_results), col_label, len(candidates),
        )

        if not candidates:
            logger.warning(
                "[get_dart_suggestions] '%s': no vector candidates found", col_label
            )
            source_column_results.append({
                "source_table": source_info["source_table"],
                "source_column": col_label,
                "source_column_description": source_info["column_description"],
                "dart_suggestions": [],
                "no_results": True,
                "no_results_reason": "No candidate DART tables returned by vector search.",
            })
            continue

        # Step 2: MDR filter — check which candidate tables are recommended
        candidate_table_names = list({
            c.get("table_name", "").strip()
            for c in candidates
            if c.get("table_name", "").strip()
        })

        logger.info(
            "[get_dart_suggestions] '%s': checking %d unique table(s) against MDR filter: %s",
            col_label, len(candidate_table_names), candidate_table_names,
        )

        mdr_recommended: Dict[str, str] = fetch_mdr_recommended_tables(candidate_table_names)

        if not mdr_recommended:
            logger.warning(
                "[get_dart_suggestions] '%s': none of the %d candidate table(s) passed MDR filter",
                col_label, len(candidate_table_names),
            )
            source_column_results.append({
                "source_table": source_info["source_table"],
                "source_column": col_label,
                "source_column_description": source_info["column_description"],
                "dart_suggestions": [],
                "no_results": True,
                "no_results_reason": (
                    f"Vector search returned {len(candidate_table_names)} candidate table(s) "
                    f"({', '.join(candidate_table_names)}), but none are MDR-recommended "
                    f"(RCMND_STS_CD='R')."
                ),
            })
            continue

        # Step 3: Build filtered suggestions from MDR-approved candidates only
        # Deduplicate by (table_name, column_name) — BQ embedding table may have duplicate rows
        seen_pairs: set = set()
        filtered = []
        for candidate in candidates:
            tbl = candidate.get("table_name", "").strip()
            col = candidate.get("column", "").strip()

            if tbl not in mdr_recommended:
                logger.debug(
                    "[get_dart_suggestions] Dropping '%s.%s' — not MDR-recommended",
                    tbl, col,
                )
                continue

            pair = (tbl, col)
            if pair in seen_pairs:
                logger.debug(
                    "[get_dart_suggestions] Dropping duplicate '%s.%s'", tbl, col,
                )
                continue
            seen_pairs.add(pair)

            filtered.append({
                "table_name": tbl,
                "table_description": candidate.get("table_description", ""),
                "column_name": col,
                "column_description": candidate.get("column_description", ""),
                "rcmnd_sts_dsc": mdr_recommended[tbl],
                "match_source": "vector_search",
            })

        top_candidates = filtered[:SUGGESTION_TOP_N]

        logger.info(
            "[get_dart_suggestions] [%d/%d] '%s': %d MDR-approved suggestion(s) (from %d vector candidates)",
            idx, len(vector_results), col_label, len(top_candidates), len(candidates),
        )

        source_column_results.append({
            "source_table": source_info["source_table"],
            "source_column": col_label,
            "source_column_description": source_info["column_description"],
            "dart_suggestions": top_candidates,
            "no_results": len(top_candidates) == 0,
            "no_results_reason": "" if top_candidates else "No MDR-recommended candidates after filtering.",
        })

    elapsed = time.time() - pipeline_start
    logger.info(
        "[get_dart_suggestions] DONE — %d column(s) processed in %.2fs",
        len(normalized), elapsed,
    )

    return {
        "source_column_results": source_column_results,
        "total_processed": len(source_column_results),
    }
