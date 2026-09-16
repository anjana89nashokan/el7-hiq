"""Standalone memory service (no Vertex AI Memory Bank).

Replaces ``VertexAiMemoryBankService`` with ADK's local ``InMemoryMemoryService``.
Keeps the ``InitMemory`` API so existing call sites are unchanged.
"""

import logging

from google.adk.memory import InMemoryMemoryService

logger = logging.getLogger(__name__)

# Process-wide singleton so memory persists across calls within a run.
_MEMORY_SERVICE = None


class InitMemory:
    def initialize_memory(self):
        global _MEMORY_SERVICE
        if _MEMORY_SERVICE is None:
            logger.info("Standalone memory init: using InMemoryMemoryService (no GCP).")
            _MEMORY_SERVICE = InMemoryMemoryService()
        self.memory_service = _MEMORY_SERVICE
        return self.memory_service
