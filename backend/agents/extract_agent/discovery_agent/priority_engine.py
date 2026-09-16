"""
Discovery Layer — Priority Engine.

Enforces the strict discovery source priority order:
  Priority 1: IndiMap (MEM2) — historical approved mappings
  Priority 2: ADW Standards (MEM3) — enterprise standard documents
  Priority 3: FYI / Data Dictionary (MEM3) — field definitions
  Priority 4: Join Repository — ERwin graph / join catalog

For each target field, sources are queried IN ORDER.
If a high-confidence match is found at priority N,
lower-priority sources are SKIPPED to minimize latency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from agents.extract_agent.pipeline_models import CandidateSource, DiscoveryResult

logger = logging.getLogger(__name__)


@dataclass
class PriorityTier:
    """Configuration for a single discovery tier."""

    source_name: str
    rank: int
    confidence_threshold: float


class DiscoveryPriorityEngine:
    """
    Deterministic priority engine for warehouse source discovery.

    Queries sources in strict order and short-circuits when a high-confidence
    match is found, avoiding unnecessary lower-priority lookups.
    """

    PRIORITY_ORDER = [
        PriorityTier("indimap", rank=1, confidence_threshold=0.85),
        PriorityTier("adw_standards", rank=2, confidence_threshold=0.80),
        PriorityTier("fyi", rank=3, confidence_threshold=0.75),
        PriorityTier("join_repository", rank=4, confidence_threshold=0.70),
    ]

    def __init__(self) -> None:
        self._source_handlers: dict[str, callable] = {}

    def register_source(self, source_name: str, handler: callable) -> None:
        """Register an async handler for a discovery source."""
        self._source_handlers[source_name] = handler

    async def discover_sources(
        self,
        target_field: str,
        context: dict,
    ) -> DiscoveryResult:
        """
        Discover candidate sources for a single target field.

        Queries sources in priority order. If any source returns a candidate
        above its confidence threshold, lower-priority sources are skipped.

        Args:
            target_field: The target field name to find sources for.
            context: Additional context (domain, source hints, etc.)

        Returns:
            DiscoveryResult with sorted candidates and selection reasoning.
        """
        all_candidates: list[CandidateSource] = []
        reasoning_parts: list[str] = []

        for tier in self.PRIORITY_ORDER:
            handler = self._source_handlers.get(tier.source_name)
            if handler is None:
                reasoning_parts.append(
                    f"[{tier.source_name}] Skipped — no handler registered."
                )
                continue

            try:
                candidates = await handler(target_field, context)
                for c in candidates:
                    all_candidates.append(c)

                high_conf = [c for c in candidates if c.confidence >= tier.confidence_threshold]

                if high_conf:
                    reasoning_parts.append(
                        f"[{tier.source_name}] Found {len(high_conf)} high-confidence match(es) "
                        f"(threshold={tier.confidence_threshold}). Short-circuiting."
                    )
                    # Short-circuit: skip lower-priority sources
                    break
                else:
                    reasoning_parts.append(
                        f"[{tier.source_name}] Found {len(candidates)} candidate(s), "
                        f"none above threshold {tier.confidence_threshold}. Continuing."
                    )

            except Exception as e:
                reasoning_parts.append(
                    f"[{tier.source_name}] Error: {e}. Continuing to next source."
                )
                logger.warning(
                    "Discovery source '%s' failed for field '%s': %s",
                    tier.source_name,
                    target_field,
                    e,
                )

        # Sort candidates by (priority_rank ASC, confidence DESC)
        all_candidates.sort(key=lambda c: (c.priority_rank, -c.confidence))

        selected = all_candidates[0] if all_candidates else None
        reasoning = " | ".join(reasoning_parts)

        logger.info(
            "Discovery for '%s': %d candidates, selected=%s (confidence=%.2f)",
            target_field,
            len(all_candidates),
            selected.source_name if selected else "NONE",
            selected.confidence if selected else 0.0,
        )

        return DiscoveryResult(
            target_field=target_field,
            candidates=all_candidates,
            selected_source=selected,
            selection_reasoning=reasoning,
        )

    async def discover_all(
        self,
        target_fields: list[str],
        context: dict,
    ) -> list[DiscoveryResult]:
        """Discover sources for multiple target fields."""
        results = []
        for field in target_fields:
            result = await self.discover_sources(field, context)
            results.append(result)
        return results


# ─── Default source handler implementations ─────────────────────────────────


async def _indimap_handler(target_field: str, context: dict) -> list[CandidateSource]:
    """Query IndiMap (MEM2) for historical mappings."""
    candidates = []
    try:
        from utils.indemap_db_utils import fetch_mapping_rules_by_column

        rules = fetch_mapping_rules_by_column(
            target_column_name=target_field,
            top_n=5,
        )
        for rule in rules:
            source_col = rule.get("SOURCE_COLUMN_NAME", "")
            source_table = rule.get("SOURCE_TABLE_NAME", "")
            if source_col:
                candidates.append(
                    CandidateSource(
                        source_name=source_col,
                        source_type="column",
                        table_name=source_table,
                        discovery_source="indimap",
                        priority_rank=1,
                        confidence=0.90,
                        match_evidence=f"IndiMap historical rule: {source_table}.{source_col}",
                    )
                )
    except Exception as e:
        logger.warning("IndiMap lookup failed for '%s': %s", target_field, e)
    return candidates


async def _adw_standards_handler(target_field: str, context: dict) -> list[CandidateSource]:
    """Query ADW Standards via Vector Search (MEM3)."""
    candidates = []
    try:
        from utils.vectorstore_vertex_utils import embed_texts_gemini_embedding, find_neighbors

        query = f"ADW standard source for field: {target_field}"
        embeddings = await embed_texts_gemini_embedding(texts=[query])
        if embeddings and embeddings[0]:
            neighbors = await find_neighbors(
                feature_vector=embeddings[0],
                neighbor_count=3,
            )
            for n in neighbors:
                distance = n.get("distance", 1.0) or 1.0
                conf = max(0.0, min(1.0, 1.0 - distance))
                candidates.append(
                    CandidateSource(
                        source_name=n.get("datapoint_id", ""),
                        source_type="column",
                        discovery_source="adw_standards",
                        priority_rank=2,
                        confidence=conf,
                        match_evidence=f"Vector search distance={distance:.3f}",
                    )
                )
    except Exception as e:
        logger.warning("ADW Standards lookup failed for '%s': %s", target_field, e)
    return candidates


async def _fyi_handler(target_field: str, context: dict) -> list[CandidateSource]:
    """Query FYI / Data Dictionary via Discovery Engine (MEM3)."""
    candidates = []
    try:
        from utils.vertex_ai_search_utils import answer_query_data_dictionary_json
        from config.settings import config

        result = answer_query_data_dictionary_json(
            query=f"source column for target field: {target_field}",
            project_id=config.PROJECT_ID,
            location=config.DATASTORE_LOCATION,
            engine_id=config.VERTEX_AI_APP_ID,
        )
        for row in result.get("rows", []):
            candidates.append(
                CandidateSource(
                    source_name=row.get("Attribute Name", ""),
                    source_type="column",
                    table_name=row.get("File Name"),
                    discovery_source="fyi",
                    priority_rank=3,
                    confidence=0.75,
                    match_evidence=f"FYI: {row.get('Attribute Description', '')}",
                )
            )
    except Exception as e:
        logger.warning("FYI lookup failed for '%s': %s", target_field, e)
    return candidates


async def _join_repository_handler(target_field: str, context: dict) -> list[CandidateSource]:
    """Query the Join Repository / ERwin graph."""
    candidates = []
    try:
        source_tables = context.get("source_tables_hint", [])
        if not source_tables:
            return candidates

        from utils.join_filter_utils import find_join_paths

        # Look for join paths from hinted tables that include the target field
        for table in source_tables[:3]:  # limit to avoid excessive lookups
            try:
                paths = find_join_paths(
                    source_table=table,
                    target_column=target_field,
                    max_hops=3,
                )
                for path in (paths or []):
                    candidates.append(
                        CandidateSource(
                            source_name=path.get("target_column", target_field),
                            source_type="column",
                            table_name=path.get("target_table", table),
                            discovery_source="join_repository",
                            priority_rank=4,
                            confidence=0.70,
                            match_evidence=f"Join path from {table}: {path}",
                        )
                    )
            except Exception:
                pass
    except Exception as e:
        logger.warning("Join repository lookup failed for '%s': %s", target_field, e)
    return candidates


def create_default_engine() -> DiscoveryPriorityEngine:
    """Create a DiscoveryPriorityEngine with all default source handlers registered."""
    engine = DiscoveryPriorityEngine()
    engine.register_source("indimap", _indimap_handler)
    engine.register_source("adw_standards", _adw_standards_handler)
    engine.register_source("fyi", _fyi_handler)
    engine.register_source("join_repository", _join_repository_handler)
    return engine
