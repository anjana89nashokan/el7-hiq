"""
IndeMap semantic search — ADK tool functions.

Accepts a target attribute name + description, queries the BQ embeddings
table for similar historical mappings, and returns source column/table
information formatted for LLM reasoning.

Both functions follow the same ADK tool contract as adk_tools.py:
  - Accept ToolContext as first argument
  - Persist results to tool_context.state
  - Return a plain string summary the LLM can reason over
"""

import logging
from typing import Any, Optional

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)


def _build_query_text(
    target_attribute: str,
    logical_attribute_name: str | None,
    logical_attribute_description: str | None,
) -> str:
    """
    Build the semantic query text matching the embedding space:
    TGT_COLM_NM + TGT_COLM_LGC_NM + TGT_COLM_DSC + IM_MAP_APP_TGT_ENTITY_ATTR_DOC_VAL.
    logical_attribute_description maps to both TGT_COLM_DSC and Attribute Documentation
    since at query time we don't have the attr doc separately.
    """
    parts = []
    if target_attribute:
        parts.append(f"Target Column Name: {target_attribute.strip()}")
    if logical_attribute_name and logical_attribute_name.strip():
        parts.append(f"Target Column Logical Name: {logical_attribute_name.strip()}")
    if logical_attribute_description and logical_attribute_description.strip():
        parts.append(f"Target Column Description: {logical_attribute_description.strip()}")
        parts.append(f"Attribute Documentation: {logical_attribute_description.strip()}")
    return "\n".join(parts) if parts else f"Target Column Name: {target_attribute}"


def _run_async(coro: Any) -> Any:
    """
    Safely run a coroutine from a sync context.
    Uses the running loop's thread executor when already inside an async runner
    (e.g. ADK), otherwise falls back to asyncio.run().
    """
    import asyncio
    import concurrent.futures

    try:
        loop = asyncio.get_running_loop()
        # We are inside an async context — run in a thread to avoid blocking
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


def search_indemap_mappings(
    tool_context: ToolContext,
    target_attribute: str,
    logical_attribute_name: Optional[str] = None,
    logical_attribute_description: Optional[str] = None,
    top_k: int = 10,
) -> str:
    """
    Search IndeMap historical mappings semantically for a target attribute.

    Query text is built from the best available signal:
      - Both logical_attribute_name + logical_attribute_description → use both
      - Only logical_attribute_name → use name only
      - Neither → fall back to target_attribute

    Results are stored in tool_context.state["indemap_search_results"][target_attribute]
    for downstream tools (generate_field_mappings, finalize_mapping) to consume.

    Args:
        target_attribute:            Target column name (e.g. "MBR_ID").
        logical_attribute_name:      Human-readable logical name.
        logical_attribute_description: Business description of the attribute.
        top_k:                       Number of similar mappings to return (default 10).

    Returns:
        Formatted string summary of top matches including source table, source
        column, rule type, and transformation rule.
    """
    from utils.indemap_embedding_utils import search_similar_mappings

    query_text = _build_query_text(
        target_attribute=target_attribute,
        logical_attribute_name=logical_attribute_name,
        logical_attribute_description=logical_attribute_description,
    )
    logger.debug("[indemap_search] target=%r query_text=%r", target_attribute, query_text)

    try:
        results: list[dict[str, Any]] = _run_async(
            search_similar_mappings(query_text=query_text, top_k=top_k)
        )
    except Exception as exc:
        logger.warning("[indemap_search] search failed for '%s': %s", target_attribute, exc)
        results = []

    # Persist to agent state keyed by target attribute
    existing: dict = tool_context.state.get("indemap_search_results", {})
    existing[target_attribute] = results
    tool_context.state["indemap_search_results"] = existing

    if not results:
        msg = (
            f"IndeMap search for '{target_attribute}' returned no results. "
            "Proceed with standards search or discovery context."
        )
        logger.info(msg)
        return msg

    lines = [
        f"IndeMap historical mappings for target attribute '{target_attribute}' ({len(results)} result(s)):",
        "Use the source table, source column, transformation rule, join, and filter information below "
        "to determine the correct mapping for this target attribute.",
        "",
    ]

    for i, r in enumerate(results, 1):
        def _g(label: str) -> str:
            return str(r.get(label) or "").strip() or "—"

        dist = r.get("Similarity Distance")
        dist_str = f"{dist:.4f}" if dist is not None else "—"

        lines.append(f"--- Mapping [{i}] (similarity_distance={dist_str}) ---")
        lines.append(f"  Target Column Name       : {_g('Target Column Name')}")
        lines.append(f"  Target Logical Name      : {_g('Target Column Logical Name')}")
        lines.append(f"  Target Description       : {_g('Target Column Description')}")
        lines.append(f"  Attribute Documentation  : {_g('Attribute Documentation')}")
        lines.append(f"  Interface Code           : {_g('Interface Code')}")
        lines.append(f"  Source Table (Entity)    : {_g('Source Entity')}")
        lines.append(f"  Source Column            : {_g('Source Column')}")
        lines.append(f"  Source Column Name       : {_g('Source Column Name')}")
        lines.append(f"  Source Column SK         : {_g('Source Column SK')}")
        lines.append(f"  Rule Type                : {_g('Rule Type')}")
        lines.append(f"  Rule Sequence            : {_g('Rule Sequence')}")
        lines.append(f"  Transformation Rule      : {_g('Transformation Rule')}")
        lines.append(f"  Join Logic               : {_g('Join')}")
        lines.append(f"  Filter                   : {_g('Filter')}")
        lines.append(f"  Common Filter            : {_g('Common Filter')}")
        lines.append(f"  Special Consideration    : {_g('Special Consideration')}")
        lines.append(f"  CDC Indicator            : {_g('CDC Indicator')}")
        lines.append(f"  Last Updated             : {_g('Last Updated')}")
        lines.append("")

    summary = "\n".join(lines)
    logger.info(
        "[indemap_search] target=%r results=%d", target_attribute, len(results)
    )
    return summary


def run_indemap_embedding_pipeline(
    tool_context: ToolContext,
) -> str:
    """
    Trigger the full IndeMap embedding pipeline:
    fetch ALL rows → embed in batches → store in BigQuery.

    Use this tool only when explicitly asked to refresh the IndeMap embeddings.
    It is NOT part of the normal per-field mapping workflow.

    Returns:
        String confirming how many rows were embedded.
    """
    from utils.indemap_embedding_utils import run_embedding_pipeline

    try:
        count: int = _run_async(run_embedding_pipeline())
    except Exception as exc:
        logger.error("[indemap_pipeline] embedding pipeline failed: %s", exc)
        tool_context.state["indemap_pipeline_status"] = "failed"
        return f"IndeMap embedding pipeline failed: {exc}"

    tool_context.state["indemap_pipeline_status"] = "success"
    tool_context.state["indemap_pipeline_rows_embedded"] = count

    msg = f"IndeMap embedding pipeline complete. {count} rows embedded successfully."
    logger.info(msg)
    return msg
