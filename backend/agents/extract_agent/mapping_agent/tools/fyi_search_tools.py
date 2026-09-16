"""
FYI_TBL_COLS semantic search — ADK tool functions.

Accepts a target attribute name + description, queries the BQ FYI_TBL_COLS
embeddings table for similar source tables/columns, and returns results
formatted for LLM reasoning.

Follows the same ADK tool contract as indemap_search_tools.py:
  - Accept ToolContext as first argument
  - Persist results to tool_context.state
  - Return a plain string summary the LLM can reason over
"""

import logging
from typing import Any, Optional

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)


def _build_fyi_query_text(
    target_attribute: str,
    logical_attribute_name: str | None,
    logical_attribute_description: str | None,
) -> str:
    """Build semantic query text to match the FYI embedding space (column_name + attr_name + description)."""
    parts = []
    if target_attribute:
        parts.append(target_attribute.strip())
    if logical_attribute_name and logical_attribute_name.strip():
        parts.append(logical_attribute_name.strip())
    if logical_attribute_description and logical_attribute_description.strip():
        parts.append(logical_attribute_description.strip())
    return ". ".join(parts) if parts else target_attribute


def _run_async(coro: Any) -> Any:
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


def search_fyi_table_columns(
    tool_context: ToolContext,
    target_attribute: str,
    logical_attribute_name: Optional[str] = None,
    logical_attribute_description: Optional[str] = None,
    top_k: int = 10,
) -> str:
    """
    Search FYI_TBL_COLS (BigQuery) for source tables matching a target attribute.

    Embeds COLM_NM + ATTR_NM + ATTR_DSC and returns DB name, table name,
    entity description, and attribute name for the top matches.

    Results are stored in tool_context.state["fyi_tbl_cols_results"][target_attribute].

    Args:
        target_attribute:            Target column name.
        logical_attribute_name:      Human-readable logical name.
        logical_attribute_description: Business description of the attribute.
        top_k:                       Number of results to return (default 10).

    Returns:
        Formatted string summary of top matching source tables.
    """
    from utils.fyi_embedding_utils import search_fyi_tbl_cols

    query_text = _build_fyi_query_text(
        target_attribute=target_attribute,
        logical_attribute_name=logical_attribute_name,
        logical_attribute_description=logical_attribute_description,
    )
    logger.debug("[fyi_tbl_cols_search] target=%r query_text=%r", target_attribute, query_text)

    try:
        results: list[dict] = _run_async(search_fyi_tbl_cols(query_text=query_text, top_k=top_k))
    except Exception as exc:
        logger.warning("[fyi_tbl_cols_search] search failed for '%s': %s", target_attribute, exc)
        results = []

    existing: dict = tool_context.state.get("fyi_tbl_cols_results", {})
    existing[target_attribute] = results
    tool_context.state["fyi_tbl_cols_results"] = existing

    if not results:
        return (
            f"FYI_TBL_COLS search for '{target_attribute}' returned no results. "
            "Proceed with IndeMap or standards search."
        )

    lines = [
        f"FYI_TBL_COLS source tables for target attribute '{target_attribute}' ({len(results)} result(s)):",
        "",
    ]
    for i, r in enumerate(results, 1):
        def _g(label: str) -> str:
            return str(r.get(label) or "").strip() or "—"

        dist = r.get("Similarity Distance")
        dist_str = f"{dist:.4f}" if dist is not None else "—"
        lines.append(f"--- Match [{i}] (similarity_distance={dist_str}) ---")
        lines.append(f"  Database Name         : {_g('Database Name')}")
        lines.append(f"  Table Name            : {_g('Table Name')}")
        lines.append(f"  Table Entity Desc     : {_g('Table Entity Description')}")
        lines.append("")

    summary = "\n".join(lines)
    logger.info("[fyi_tbl_cols_search] target=%r results=%d", target_attribute, len(results))
    return summary


def run_fyi_tbl_cols_pipeline(
    tool_context: ToolContext,
    batch_from: Optional[int] = None,
    batch_to: Optional[int] = None,
) -> str:
    """
    Trigger the FYI_TBL_COLS embedding pipeline.

    Args:
        batch_from: 1-based first batch to run (inclusive). None = from start.
        batch_to:   1-based last batch to run (inclusive). None = to end.

    Use only when explicitly asked to refresh FYI_TBL_COLS embeddings.
    """
    from utils.fyi_embedding_utils import run_fyi_tbl_cols_pipeline as _run_pipeline

    try:
        count: int = _run_async(_run_pipeline(batch_from=batch_from, batch_to=batch_to))
    except Exception as exc:
        logger.error("[fyi_tbl_cols_pipeline] failed: %s", exc)
        tool_context.state["fyi_tbl_cols_pipeline_status"] = "failed"
        return f"FYI_TBL_COLS embedding pipeline failed: {exc}"

    tool_context.state["fyi_tbl_cols_pipeline_status"] = "success"
    tool_context.state["fyi_tbl_cols_pipeline_rows_embedded"] = count
    msg = f"FYI_TBL_COLS embedding pipeline complete. {count} rows embedded successfully."
    logger.info(msg)
    return msg
