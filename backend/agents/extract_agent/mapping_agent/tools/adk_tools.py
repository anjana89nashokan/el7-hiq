"""
Mapping Layer — ADK tool functions.

Implements the IndiMap "Reused Shortcut" and mapping generation/classification.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext

from agents.extract_agent.pipeline_models import MappingEntry
from config.settings import config

logger = logging.getLogger(__name__)

# Thresholds (configurable via settings)
EXACT_THRESHOLD = float(getattr(config, "EXTRACT_MATCH_EXACT_THRESHOLD", 0.90))
PARTIAL_THRESHOLD = float(getattr(config, "EXTRACT_MATCH_PARTIAL_THRESHOLD", 0.60))
INDIMAP_REUSE_THRESHOLD = float(
    getattr(config, "EXTRACT_INDIMAP_REUSE_THRESHOLD", 0.90)
)


def _classify_match(confidence: float) -> str:
    if confidence >= EXACT_THRESHOLD:
        return "exact"
    elif confidence >= PARTIAL_THRESHOLD:
        return "partial"
    else:
        return "no_match"


# ─── Tool 1: IndiMap Reuse Check ────────────────────────────────────────────

def check_indimap_reuse(
    tool_context: ToolContext,
    target_fields: List[str],
) -> str:
    """
    Check IndiMap (MEM2) for existing approved mappings for each target field.

    This is the "Reused Shortcut" — if a field has a high-confidence historical
    mapping from a previously approved extract, reuse it directly without
    going through full mapping generation.
    """
    reused_mappings: list[dict] = []
    reuse_count = 0

    try:
        from utils.indemap_db_utils import fetch_mapping_rules_by_column

        for field in target_fields:
            try:
                rules = fetch_mapping_rules_by_column(
                    target_column_name=field,
                    top_n=3,
                )
                if rules:
                    best = rules[0]
                    source_col = best.get("SOURCE_COLUMN_NAME", "")
                    source_table = best.get("SOURCE_TABLE_NAME", "")
                    confidence = 0.90

                    if confidence >= INDIMAP_REUSE_THRESHOLD and source_col:
                        reused_mappings.append(
                            MappingEntry(
                                target_field=field,
                                source_field=source_col,
                                source_table=source_table,
                                match_type="exact",
                                confidence=confidence,
                                reused_from_indimap=True,
                                indimap_reference_id=str(
                                    best.get("MAPPING_RULE_SK", "")
                                ),
                                needs_review=False,
                                mapping_evidence=f"Reused from IndiMap: {source_table}.{source_col}",
                            ).model_dump()
                        )
                        reuse_count += 1
            except Exception as field_err:
                logger.warning("IndiMap check failed for '%s': %s", field, field_err)

    except Exception as e:
        logger.warning("IndiMap reuse check unavailable: %s", e)

    tool_context.state["indimap_reused_mappings"] = reused_mappings
    tool_context.state["indimap_reused_fields"] = [
        m["target_field"] for m in reused_mappings
    ]

    msg = (
        f"IndiMap reuse check complete. "
        f"{reuse_count}/{len(target_fields)} fields reused from historical mappings."
    )
    logger.info(msg)
    return msg


# ─── Tool 2: Generate Field Mappings ────────────────────────────────────────

def generate_field_mappings(
    tool_context: ToolContext,
    mappings: List[Dict[str, Any]],
) -> str:
    """
    Generate mappings for fields NOT reused from IndiMap.
    """
    generated = []
    reused_fields = set(tool_context.state.get("indimap_reused_fields", []))

    for m in mappings:
        field = m.get("target_field", "")
        if field in reused_fields:
            continue

        confidence = float(m.get("confidence", 0.0))
        match_type = _classify_match(confidence)

        entry = MappingEntry(
            target_field=field,
            source_field=m.get("source_field"),
            source_table=m.get("source_table"),
            source_database=m.get("source_database"),
            match_type=match_type,
            confidence=confidence,
            transformation_rule=m.get("transformation_rule"),
            reused_from_indimap=False,
            needs_review=(match_type != "exact"),
            mapping_evidence=m.get("mapping_evidence", ""),
        )
        generated.append(entry.model_dump())

    tool_context.state["generated_mappings"] = generated

    msg = f"Generated {len(generated)} new mappings (excluding {len(reused_fields)} IndiMap reused)."
    logger.info(msg)
    return msg


# ─── Tool 3: Finalize Mapping ────────────────────────────────────────────────

def finalize_mapping(
    tool_context: ToolContext,
) -> str:
    """
    Finalize all mappings: merge IndiMap reused + newly generated.
    """
    reused = tool_context.state.get("indimap_reused_mappings", [])
    generated = tool_context.state.get("generated_mappings", [])

    all_mappings = reused + generated

    exact_count    = sum(1 for m in all_mappings if m.get("match_type") == "exact")
    partial_count  = sum(1 for m in all_mappings if m.get("match_type") == "partial")
    no_match_count = sum(1 for m in all_mappings if m.get("match_type") == "no_match")
    review_count   = sum(1 for m in all_mappings if m.get("needs_review"))
    reuse_count    = sum(1 for m in all_mappings if m.get("reused_from_indimap"))

    confidences = [m.get("confidence", 0.0) for m in all_mappings]
    avg_confidence = sum(confidences) / max(len(confidences), 1)

    unmapped = [
        m["target_field"] for m in all_mappings if m.get("match_type") == "no_match"
    ]

    tool_context.state["final_mappings"]    = all_mappings
    tool_context.state["unmapped_fields"]   = unmapped
    tool_context.state["mapping_summary"]   = {
        "total_target_fields": len(all_mappings),
        "exact_matches":       exact_count,
        "partial_matches":     partial_count,
        "no_matches":          no_match_count,
        "reused_from_indimap": reuse_count,
        "needs_review_count":  review_count,
        "average_confidence":  round(avg_confidence, 3),
    }

    msg = (
        f"Mapping finalized: {len(all_mappings)} total — "
        f"{exact_count} exact, {partial_count} partial, {no_match_count} unmatched. "
        f"{reuse_count} reused from IndiMap. "
        f"Avg confidence: {avg_confidence:.2f}. "
        f"{review_count} flagged for review."
    )
    logger.info(msg)
    return msg
