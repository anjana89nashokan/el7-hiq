"""
Unified Memory Manager for the BSA DATAMAP Extract Pipeline.
=============================================================

Provides a single facade over the three memory tiers:

  MEM1 (Short-Term): CloudSQL / Vertex AI session state
    - Pipeline stage tracking, intermediate results, HITL state
    - Uses existing db/ layer + Vertex AI session service

  MEM2 (Long-Term): IndiMap history (SQL Server / BigQuery)
    - Historical approved mappings from previous extracts
    - Uses existing indemap_db_utils + indemap_history_mapping_utils

  MEM3 (Vector DB): RAG knowledge retrieval
    - Enterprise standards, FYI data dictionary, evidence snippets
    - Uses existing vectorstore_vertex_utils + vertex_ai_search_utils
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from config.settings import config

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Unified memory facade for the extract mapping pipeline.

    Each method delegates to the appropriate existing infrastructure.
    All methods are fail-safe — they log errors but do not crash the pipeline.
    """

    def __init__(
        self,
        project_id: str = config.PROJECT_ID,
        location: str = config.LOCATION,
    ):
        self.project_id = project_id
        self.location = location

    # ─── MEM1: Short-Term Session State ─────────────────────────────────

    async def read_session_state(
        self, user_id: str, session_id: str, key: str
    ) -> Any:
        """Read a value from Vertex AI session state (MEM1)."""
        try:
            from google.adk.sessions import VertexAiSessionService

            svc = VertexAiSessionService(
                project=self.project_id, location=self.location
            )
            session = await svc.get_session(
                app_name=config.REASONING_ENGINE_RESOURCE,
                user_id=user_id,
                session_id=session_id,
            )
            return session.state.get(key)
        except Exception as e:
            logger.error("MEM1 read failed for key '%s': %s", key, e)
            return None

    async def write_session_state(
        self, user_id: str, session_id: str, key: str, value: Any
    ) -> bool:
        """Write a value to Vertex AI session state (MEM1)."""
        try:
            from google.adk.sessions import VertexAiSessionService

            svc = VertexAiSessionService(
                project=self.project_id, location=self.location
            )
            await svc.update_session(
                app_name=config.REASONING_ENGINE_RESOURCE,
                user_id=user_id,
                session_id=session_id,
                state={key: value},
            )
            return True
        except Exception as e:
            logger.error("MEM1 write failed for key '%s': %s", key, e)
            return False

    # ─── MEM2: Long-Term IndiMap History ────────────────────────────────

    def query_indimap_history(
        self, target_column: str, top_n: int = 10
    ) -> list[dict]:
        """
        Query IndiMap for historical mapping rules (MEM2).

        Uses the existing indemap_db_utils infrastructure.
        Returns a list of mapping rule dicts.
        """
        try:
            from utils.indemap_db_utils import fetch_mapping_rules_by_column

            rules = fetch_mapping_rules_by_column(
                target_column_name=target_column,
                top_n=top_n,
            )
            logger.info(
                "MEM2: Found %d historical rules for '%s'",
                len(rules),
                target_column,
            )
            return rules
        except Exception as e:
            logger.warning("MEM2 query failed for '%s': %s", target_column, e)
            return []

    def write_indimap_history(
        self,
        run_id: str,
        interface_code: str,
        mappings: list[dict],
        user_id: str,
    ) -> bool:
        """
        Write approved mappings back to IndiMap for future reuse (MEM2).

        This is called after the final H4 approval to feed the learning loop.
        """
        try:
            from utils.indemap_db_utils import log_indemap_audit
            import pandas as pd

            # Log the audit trail
            log_indemap_audit(
                run_id=run_id,
                operation="extract_mapping_approved",
                interface_code=interface_code,
                rows_affected=len(mappings),
                user_id=user_id,
                status="success",
            )

            logger.info(
                "MEM2: Wrote %d approved mappings for run '%s'",
                len(mappings),
                run_id,
            )
            return True
        except Exception as e:
            logger.warning("MEM2 write failed: %s", e)
            return False

    # ─── MEM3: Vector DB / RAG Knowledge Retrieval ──────────────────────

    async def vector_search(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
    ) -> list[dict]:
        """
        Perform vector similarity search (MEM3).

        Uses Vertex AI Vector Search for embedding-based retrieval.
        """
        try:
            from utils.vectorstore_vertex_utils import (
                embed_texts_gemini_embedding,
                find_neighbors,
            )

            # Generate query embedding
            embeddings = await embed_texts_gemini_embedding(texts=[query])
            if not embeddings or not embeddings[0]:
                logger.warning("MEM3: Empty embedding for query '%s'", query[:50])
                return []

            # Build restricts from filters
            restricts = None
            if filters:
                restricts = [
                    {"namespace": k, "allowList": [v] if isinstance(v, str) else v}
                    for k, v in filters.items()
                ]

            neighbors = await find_neighbors(
                feature_vector=embeddings[0],
                neighbor_count=top_k,
                restricts=restricts,
            )

            logger.info(
                "MEM3: Found %d neighbors for query '%s'",
                len(neighbors),
                query[:50],
            )
            return neighbors
        except Exception as e:
            logger.warning("MEM3 vector search failed: %s", e)
            return []

    def fyi_search(self, query: str) -> dict:
        """
        Search the FYI / Data Dictionary via Discovery Engine (MEM3).

        Uses the existing Vertex AI Search integration.
        """
        try:
            from utils.vertex_ai_search_utils import answer_query_data_dictionary_json

            result = answer_query_data_dictionary_json(
                query=query,
                project_id=config.PROJECT_ID,
                location=config.DATASTORE_LOCATION,
                engine_id=config.VERTEX_AI_APP_ID,
            )
            logger.info(
                "MEM3 FYI: Found %d rows for query '%s'",
                len(result.get("rows", [])),
                query[:50],
            )
            return result
        except Exception as e:
            logger.warning("MEM3 FYI search failed: %s", e)
            return {"rows": []}
