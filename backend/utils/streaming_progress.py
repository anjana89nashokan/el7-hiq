# utils/streaming_progress.py
"""
Streaming Progress Tracker for all DataMap Copilot features.
Provides unified progress tracking across profiling, relationship analysis,
data dictionary generation, metadata templates, and anomaly detection.
"""

import logging
from typing import Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class FeatureType(Enum):
    """Supported feature types for progress tracking"""
    PROFILING = "profiling"
    RELATIONSHIP_ANALYSIS = "relationship_analysis"
    DATA_DICTIONARY = "data_dictionary"
    METADATA_TEMPLATE = "metadata_template"
    ANOMALY_DETECTION = "anomaly_detection"
    SIMILARITY = "similarity"


# Feature-specific phase mappings with progress weights
FEATURE_PHASE_CONFIGS = {
    FeatureType.PROFILING: {
        "phases": [
            ("statistical_analysis", 25),      # Phase A: BigQuery parallel analysis
            ("batch_creation", 5),             # Phase B: Token budgeting
            ("llm_enhancement", 60),           # Phase C: Batched LLM calls
            ("aggregation", 10),               # Phase D: Result merging
        ],
        "supports_batch_progress": True,
        "supports_table_progress": True,
    },
    FeatureType.RELATIONSHIP_ANALYSIS: {
        "phases": [
            ("data_loading", 20),
            ("statistical_correlation", 30),
            ("llm_semantic_analysis", 40),
            ("report_generation", 10),
        ],
        "supports_batch_progress": False,
        "supports_table_progress": True,
    },
    FeatureType.DATA_DICTIONARY: {
        "phases": [
            ("profiling_data_loading", 15),
            ("llm_description_generation", 70),
            ("formatting_and_validation", 15),
        ],
        "supports_batch_progress": False,
        "supports_table_progress": True,
    },
    FeatureType.METADATA_TEMPLATE: {
        "phases": [
            ("template_analysis", 20),
            ("mapping_suggestions", 30),
            ("metadata_generation", 40),
            ("excel_creation", 10),
        ],
        "supports_batch_progress": False,
        "supports_table_progress": False,
    },
    FeatureType.ANOMALY_DETECTION: {
        "phases": [
            ("data_profiling", 30),
            ("pattern_detection", 40),
            ("anomaly_scoring", 20),
            ("report_generation", 10),
        ],
        "supports_batch_progress": False,
        "supports_table_progress": True,
    },
    FeatureType.SIMILARITY: {
        "phases": [
            ("metadata_fetching", 30),        # Phase 1: Fetch table metadata (batched)
            ("semantic_matching", 20),        # Phase 1: LLM semantic analysis
            ("overlap_validation", 40),       # Phase 2: Data overlap calculation
            ("insights_generation", 10),      # Phase 3: LLM insights
        ],
        "supports_batch_progress": True,     # Supports metadata batching
        "supports_table_progress": True,     # Track matches processed
    },
}


class StreamingProgressTracker:
    """
    Unified progress tracker for all DataMap Copilot features.
    Generates SSE-compatible progress events.
    """

    def __init__(self, feature_type: FeatureType, total_items: int = 0):
        """
        Initialize progress tracker.

        Args:
            feature_type: Type of feature being tracked
            total_items: Total number of items (tables, files, etc.)
        """
        self.feature_type = feature_type
        self.total_items = total_items
        self.current_phase_index = 0
        self.processed_items = 0

        self.config = FEATURE_PHASE_CONFIGS.get(feature_type, {})
        self.phases = self.config.get("phases", [])

        logger.info(f"StreamingProgressTracker initialized for {feature_type.value} with {total_items} items")

    def get_init_event(self, message: str = None) -> Dict[str, Any]:
        """
        Generate initial status event.

        Args:
            message: Optional custom message

        Returns:
            SSE-compatible event dict
        """
        default_message = f"Starting {self.feature_type.value.replace('_', ' ')}..."
        if self.total_items > 0:
            default_message += f" ({self.total_items} items)"

        return {
            "event": "status",
            "data": {
                "phase": "init",
                "feature": self.feature_type.value,
                "message": message or default_message,
                "progress": 0,
                "total_items": self.total_items
            }
        }

    def get_phase_progress_event(
        self,
        phase_index: int,
        sub_progress: float = 0.0,
        message: str = None,
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Generate phase progress event.

        Args:
            phase_index: Index of current phase (0-based)
            sub_progress: Progress within current phase (0-100)
            message: Optional custom message
            metadata: Additional metadata to include

        Returns:
            SSE-compatible event dict
        """
        if phase_index >= len(self.phases):
            return self.get_complete_event()

        phase_name, phase_weight = self.phases[phase_index]

        # Calculate cumulative progress from previous phases
        cumulative_progress = sum(weight for _, weight in self.phases[:phase_index])

        # Add current phase progress
        current_phase_progress = (sub_progress / 100.0) * phase_weight
        total_progress = min(cumulative_progress + current_phase_progress, 99.9)

        default_message = f"{phase_name.replace('_', ' ').title()} in progress..."

        event_data = {
            "phase": phase_name,
            "feature": self.feature_type.value,
            "message": message or default_message,
            "progress": round(total_progress, 1),
            "phase_progress": round(sub_progress, 1)
        }

        if metadata:
            event_data.update(metadata)

        return {
            "event": "progress",
            "data": event_data
        }

    def get_item_progress_event(
        self,
        phase_index: int,
        items_processed: int,
        item_name: str = None,
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Generate item-level progress event (for tables, files, etc.).

        Args:
            phase_index: Index of current phase
            items_processed: Number of items processed so far
            item_name: Name of current item being processed
            metadata: Additional metadata

        Returns:
            SSE-compatible event dict
        """
        self.processed_items = items_processed

        # Calculate sub-progress within phase
        sub_progress = (items_processed / self.total_items * 100) if self.total_items > 0 else 0

        message = f"Processing {items_processed}/{self.total_items}"
        if item_name:
            message += f" - {item_name}"

        item_metadata = {
            "items_processed": items_processed,
            "total_items": self.total_items,
        }
        if item_name:
            item_metadata["current_item"] = item_name

        if metadata:
            item_metadata.update(metadata)

        return self.get_phase_progress_event(
            phase_index=phase_index,
            sub_progress=sub_progress,
            message=message,
            metadata=item_metadata
        )

    def get_batch_progress_event(
        self,
        batch_num: int,
        total_batches: int,
        batch_info: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Generate batch-level progress event (for batched LLM processing).

        Args:
            batch_num: Current batch number (0-based)
            total_batches: Total number of batches
            batch_info: Additional batch information

        Returns:
            SSE-compatible event dict
        """
        # Batching typically happens in LLM enhancement phase (index 2 for profiling)
        phase_index = 2

        sub_progress = ((batch_num + 1) / total_batches * 100) if total_batches > 0 else 0

        message = f"Processing batch {batch_num + 1}/{total_batches}"

        batch_metadata = {
            "batch_num": batch_num + 1,
            "total_batches": total_batches
        }

        if batch_info:
            batch_metadata.update(batch_info)

        return self.get_phase_progress_event(
            phase_index=phase_index,
            sub_progress=sub_progress,
            message=message,
            metadata=batch_metadata
        )

    def get_complete_event(self, result: Any = None, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Generate completion event.

        Args:
            result: Final result data
            metadata: Additional metadata

        Returns:
            SSE-compatible event dict
        """
        event_data = {
            "phase": "complete",
            "feature": self.feature_type.value,
            "message": f"{self.feature_type.value.replace('_', ' ').title()} complete!",
            "progress": 100
        }

        if result is not None:
            event_data["result"] = result

        if metadata:
            event_data.update(metadata)

        return {
            "event": "complete",
            "data": event_data
        }

    def get_error_event(self, error_message: str, error_details: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Generate error event.

        Args:
            error_message: Error message
            error_details: Additional error details

        Returns:
            SSE-compatible event dict
        """
        event_data = {
            "phase": "error",
            "feature": self.feature_type.value,
            "message": f"Error: {error_message}",
            "progress": self.get_current_progress()
        }

        if error_details:
            event_data["error_details"] = error_details

        return {
            "event": "error",
            "data": event_data
        }

    def get_current_progress(self) -> float:
        """Get current progress percentage."""
        if not self.phases:
            return 0.0

        cumulative = sum(weight for _, weight in self.phases[:self.current_phase_index])
        return round(cumulative, 1)


def detect_feature_type(message: str) -> FeatureType:
    """
    Detect feature type from message content.

    Args:
        message: User message text

    Returns:
        Detected FeatureType
    """
    message_lower = message.lower()

    # Pattern matching for feature detection (order matters - most specific first)
    if "[metadata template]" in message_lower or "metadata template" in message_lower or "metadata_template.xlsx" in message_lower:
        return FeatureType.METADATA_TEMPLATE

    elif "[data dictionary]" in message_lower or "create a data dictionary" in message_lower:
        return FeatureType.DATA_DICTIONARY

    elif "relationship" in message_lower and "analys" in message_lower:
        return FeatureType.RELATIONSHIP_ANALYSIS

    elif "anomaly" in message_lower or ("data quality" in message_lower and "analys" in message_lower):
        return FeatureType.ANOMALY_DETECTION

    else:
        # Default to profiling (most common case)
        return FeatureType.PROFILING
