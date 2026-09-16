"""
Discovery Layer — ADK tool functions.

Wraps the DiscoveryPriorityEngine as ADK tools for the LLM agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from google.adk.tools import ToolContext

from agents.extract_agent.pipeline_models import CandidateSource, DiscoveryResult

logger = logging.getLogger(__name__)


def run_discovery_engine(
    tool_context: ToolContext,
    target_fields: List[str],
) -> str:
    """
    Run the Warehouse Discovery Engine for a list of target fields.

    Queries sources in strict priority order:
      1. IndiMap (MEM2) — historical mappings
      2. ADW Standards (MEM3) — enterprise standards
      3. FYI / Data Dictionary (MEM3) — field definitions
      4. Join Repository — ERwin graph

    The engine short-circuits when high-confidence matches are found.
    Results are saved to session state.
    """
    import asyncio
    from agents.extract_agent.discovery_agent.priority_engine import (
        create_default_engine,
    )

    engine = create_default_engine()

    # Build context from session state
    context = {
        "source_tables_hint": tool_context.state.get("source_tables_hint", []),
        "domain": tool_context.state.get("primary_domain", "unknown"),
        "extract_drivers": tool_context.state.get("extract_drivers", []),
    }

    # Run discovery (async)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                results = pool.submit(
                    asyncio.run,
                    engine.discover_all(target_fields, context),
                ).result()
        else:
            results = asyncio.run(engine.discover_all(target_fields, context))
    except Exception as e:
        logger.error("Discovery engine failed: %s", e)
        # Return empty results for all fields
        results = [
            DiscoveryResult(
                target_field=f,
                candidates=[],
                selected_source=None,
                selection_reasoning=f"Discovery engine error: {e}",
            )
            for f in target_fields
        ]

    # Save raw results to session state
    tool_context.state["discovery_results_raw"] = [r.model_dump() for r in results]

    # Build summary
    total = len(results)
    with_match = sum(1 for r in results if r.selected_source is not None)
    high_conf = sum(
        1 for r in results if r.selected_source and r.selected_source.confidence >= 0.85
    )
    no_match = total - with_match

    summary = (
        f"Discovery complete for {total} fields: "
        f"{with_match} matched ({high_conf} high-confidence), "
        f"{no_match} unmatched."
    )

    logger.info(summary)
    return summary


def save_discovery_results(
    tool_context: ToolContext,
    discovery_results: List[Dict[str, Any]],
) -> str:
    """Save finalized discovery results to session state."""
    tool_context.state["discovery_results"] = discovery_results

    total = len(discovery_results)
    needs_review = sum(
        1
        for r in discovery_results
        if not r.get("selected_source")
        or r.get("selected_source", {}).get("confidence", 0) < 0.70
    )

    logger.info(
        "Discovery results saved: %d total, %d needing review", total, needs_review
    )
    return (
        f"Discovery results saved. {total} fields, {needs_review} flagged for review."
    )


def finalize_discovery_results(
    tool_context: ToolContext,
    reviewed_results: List[Dict[str, Any]],
    fields_needing_review: List[str],
    summary: str,
) -> str:
    """Finalize and persist reviewed discovery results."""
    tool_context.state["discovery_results"] = reviewed_results
    tool_context.state["discovery_fields_needing_review"] = fields_needing_review
    tool_context.state["discovery_summary"] = summary

    can_proceed = len(fields_needing_review) == 0

    logger.info(
        "Discovery finalized: %d results, %d needing review, can_proceed=%s",
        len(reviewed_results),
        len(fields_needing_review),
        can_proceed,
    )
    return (
        f"Discovery finalized. {len(reviewed_results)} fields processed. "
        f"{len(fields_needing_review)} flagged for BSA review. "
        f"{'Pipeline can proceed.' if can_proceed else 'BSA review required before proceeding.'}"
    )
