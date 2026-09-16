# utils/context_manager.py
"""
LLM Context Manager for Large-Scale Data Profiling

Manages token budgets, batching, and adaptive sampling for 100+ tables
with Gemini 2.5 Pro (1M token input limit, 800K usable with safety margin).
"""

import logging
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from config.settings import config
from utils.token_estimator import (
    estimate_tokens_for_table_data,
    estimate_prompt_overhead_tokens,
    check_token_budget,
    log_token_estimate
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class TableData:
    """
    Container for table profiling data with token-aware sampling.
    """
    table_reference: str
    column_metadata: Dict[str, Any]
    sample_rows: List[Dict]
    total_rows: int = 0
    total_columns: int = 0
    estimated_tokens: int = 0
    is_sampled: bool = False
    sampling_strategy: str = "default"

    def __post_init__(self):
        """Calculate derived properties."""
        self.total_columns = len(self.column_metadata)


@dataclass
class Batch:
    """
    Container for a batch of tables that fit within token budget.
    """
    batch_id: int
    tables: List[TableData] = field(default_factory=list)
    estimated_tokens: int = 0
    prompt_overhead_tokens: int = 0
    total_tokens: int = 0

    def add_table(self, table: TableData, prompt_overhead: int = 0) -> None:
        """Add table to batch and update token counts."""
        self.tables.append(table)
        self.estimated_tokens += table.estimated_tokens
        self.prompt_overhead_tokens = prompt_overhead
        self.total_tokens = self.estimated_tokens + self.prompt_overhead_tokens


class LLMContextManager:
    """
    Manages LLM context window for large-scale data profiling.

    Features:
    - Token budget enforcement (800K usable tokens)
    - Adaptive sampling for null-heavy, wide, and tall tables
    - Intelligent batching (~5 tables per batch)
    - Safety margins to prevent overflow
    """

    def __init__(
        self,
        max_tokens: int = None,
        token_safety_margin: int = None,
        tokens_per_table_budget: int = None,
        max_batch_size: int = None
    ):
        """
        Initialize LLM Context Manager.

        Args:
            max_tokens: Max input tokens (default: 1M for Gemini 2.5 Pro)
            token_safety_margin: Safety margin for response overhead
            tokens_per_table_budget: Max tokens per table
            max_batch_size: Max tables per batch
        """
        self.max_tokens = max_tokens or config.LLM_MAX_INPUT_TOKENS
        self.token_safety_margin = token_safety_margin or config.LLM_TOKEN_SAFETY_MARGIN
        self.usable_tokens = self.max_tokens - self.token_safety_margin
        self.tokens_per_table_budget = tokens_per_table_budget or config.LLM_TOKENS_PER_TABLE_BUDGET
        self.max_batch_size = max_batch_size or config.LLM_MAX_BATCH_SIZE

        logger.info(
            f"LLMContextManager initialized: "
            f"max_tokens={self.max_tokens}, "
            f"usable_tokens={self.usable_tokens}, "
            f"per_table_budget={self.tokens_per_table_budget}, "
            f"max_batch_size={self.max_batch_size}"
        )

    def apply_adaptive_sampling(self, table: TableData) -> TableData:
        """
        Apply adaptive sampling based on table characteristics.

        Strategies:
        1. Schema-only: Remove all sample rows for tables with >100 columns (extreme wide tables)
        2. Null-heavy columns: Reduce samples for columns with >80% nulls
        3. Wide tables: Reduce samples for tables with >50 columns
        4. Tall tables: Reduce samples for tables with >1M rows
        5. Default: 10 sample rows

        Args:
            table: TableData with full samples

        Returns:
            TableData with adaptive sampling applied
        """
        # Determine sampling strategy
        num_columns = len(table.column_metadata)
        total_rows = table.total_rows

        # Check conditions (priority order)
        # NEW: Schema-only mode for extremely wide tables (>100 columns)
        if num_columns > config.ADAPTIVE_SAMPLING_SCHEMA_ONLY_THRESHOLD:
            strategy = "schema_only"
            table.sample_rows = []  # Remove all sample rows
            table.is_sampled = True
            table.sampling_strategy = strategy
            logger.info(
                f"Applied schema-only mode to {table.table_reference}: "
                f"{num_columns} columns (>{config.ADAPTIVE_SAMPLING_SCHEMA_ONLY_THRESHOLD}). "
                f"Sample rows removed to fit token budget."
            )
            # Still process null-heavy columns below
            null_heavy_count = 0
            for col_name, col_data in table.column_metadata.items():
                null_pct = col_data.get("null_percentage", 0) / 100.0
                if null_pct > config.ADAPTIVE_SAMPLING_NULL_HEAVY_THRESHOLD:
                    if "sample_values" in col_data:
                        original_samples = len(col_data["sample_values"])
                        if original_samples > config.ADAPTIVE_SAMPLING_NULL_HEAVY_ROWS:
                            col_data["sample_values"] = col_data["sample_values"][:config.ADAPTIVE_SAMPLING_NULL_HEAVY_ROWS]
                            null_heavy_count += 1
            if null_heavy_count > 0:
                logger.debug(f"Reduced samples for {null_heavy_count} null-heavy columns")
            return table

        elif total_rows > config.ADAPTIVE_SAMPLING_TALL_TABLE_THRESHOLD:
            strategy = "tall_table"
            target_sample_rows = config.ADAPTIVE_SAMPLING_TALL_TABLE_ROWS
        elif num_columns > config.ADAPTIVE_SAMPLING_WIDE_TABLE_THRESHOLD:
            strategy = "wide_table"
            target_sample_rows = config.ADAPTIVE_SAMPLING_WIDE_TABLE_ROWS
        else:
            strategy = "default"
            target_sample_rows = config.ADAPTIVE_SAMPLING_DEFAULT_ROWS

        # Reduce sample rows
        original_sample_count = len(table.sample_rows)
        if original_sample_count > target_sample_rows:
            table.sample_rows = table.sample_rows[:target_sample_rows]
            table.is_sampled = True
            table.sampling_strategy = strategy
            logger.debug(
                f"Applied {strategy} sampling to {table.table_reference}: "
                f"{original_sample_count} → {target_sample_rows} rows"
            )

        # Reduce sample values for null-heavy columns
        null_heavy_count = 0
        for col_name, col_data in table.column_metadata.items():
            null_pct = col_data.get("null_percentage", 0) / 100.0
            if null_pct > config.ADAPTIVE_SAMPLING_NULL_HEAVY_THRESHOLD:
                # Reduce sample values to 3 for null-heavy columns
                if "sample_values" in col_data:
                    original_samples = len(col_data["sample_values"])
                    if original_samples > config.ADAPTIVE_SAMPLING_NULL_HEAVY_ROWS:
                        col_data["sample_values"] = col_data["sample_values"][:config.ADAPTIVE_SAMPLING_NULL_HEAVY_ROWS]
                        null_heavy_count += 1

        if null_heavy_count > 0:
            table.is_sampled = True
            logger.debug(
                f"Reduced samples for {null_heavy_count} null-heavy columns in {table.table_reference}"
            )

        return table

    def estimate_table_tokens(
        self,
        table: TableData,
        include_samples: bool = True
    ) -> int:
        """
        Estimate tokens for a table's data.

        Args:
            table: TableData object
            include_samples: Whether to include sample values

        Returns:
            Estimated token count
        """
        max_sample_rows = len(table.sample_rows)
        max_samples_per_column = 3  # Conservative: 3 samples per column

        token_breakdown = estimate_tokens_for_table_data(
            table_reference=table.table_reference,
            column_metadata=table.column_metadata,
            sample_rows=table.sample_rows,
            include_samples=include_samples,
            max_sample_rows=max_sample_rows,
            max_samples_per_column=max_samples_per_column
        )

        table.estimated_tokens = token_breakdown["total_tokens"]

        log_token_estimate(
            table.table_reference,
            token_breakdown,
            level="DEBUG"
        )

        return table.estimated_tokens

    def create_batches(
        self,
        tables: List[TableData]
    ) -> List[Batch]:
        """
        Split tables into batches that fit within token budget.

        Strategy:
        1. Apply adaptive sampling to each table
        2. Estimate tokens per table
        3. Group tables into batches (max 5 tables or 800K tokens)
        4. Add prompt overhead per batch

        Args:
            tables: List of TableData objects

        Returns:
            List of Batch objects
        """
        if not tables:
            logger.warning("No tables to batch")
            return []

        batch_start_time = time.time()
        logger.info(f"Creating batches for {len(tables)} tables...")

        batches = []
        current_batch = None
        batch_id = 0
        sampling_stats = {
            "schema_only": 0,
            "tall_table": 0,
            "wide_table": 0,
            "default": 0
        }

        for table in tables:
            # Apply adaptive sampling
            table = self.apply_adaptive_sampling(table)

            # Track sampling strategy
            strategy = table.sampling_strategy or "default"
            if strategy in sampling_stats:
                sampling_stats[strategy] += 1

            # Estimate tokens
            table_tokens = self.estimate_table_tokens(table)

            # Check if table exceeds per-table budget
            if table_tokens > self.tokens_per_table_budget:
                logger.warning(
                    f"Table {table.table_reference} exceeds per-table budget: "
                    f"{table_tokens} > {self.tokens_per_table_budget} tokens. "
                    f"Further sampling may be needed."
                )

            # Start new batch if needed
            if current_batch is None:
                current_batch = Batch(batch_id=batch_id)
                batch_id += 1

            # Estimate prompt overhead for batch (increases with table count)
            prompt_overhead = estimate_prompt_overhead_tokens(
                num_tables=len(current_batch.tables) + 1
            )

            # Check if adding table would exceed budget
            projected_total = current_batch.estimated_tokens + table_tokens + prompt_overhead

            # Batch full conditions:
            # 1. Would exceed usable tokens
            # 2. Would exceed max batch size
            batch_full = (
                projected_total > self.usable_tokens or
                len(current_batch.tables) >= self.max_batch_size
            )

            if batch_full and current_batch.tables:
                # Finalize current batch
                batches.append(current_batch)
                table_refs = [t.table_reference.split('.')[-1] for t in current_batch.tables]  # Just table names
                logger.info(
                    f"Batch {current_batch.batch_id} created: "
                    f"{len(current_batch.tables)} tables, "
                    f"{current_batch.total_tokens} tokens "
                    f"({current_batch.total_tokens / self.usable_tokens * 100:.1f}% utilization). "
                    f"Tables: {table_refs}"
                )

                # Start new batch
                current_batch = Batch(batch_id=batch_id)
                batch_id += 1
                prompt_overhead = estimate_prompt_overhead_tokens(num_tables=1)

            # Add table to current batch
            current_batch.add_table(table, prompt_overhead)

        # Add final batch
        if current_batch and current_batch.tables:
            batches.append(current_batch)
            table_refs = [t.table_reference.split('.')[-1] for t in current_batch.tables]
            logger.info(
                f"Batch {current_batch.batch_id} created: "
                f"{len(current_batch.tables)} tables, "
                f"{current_batch.total_tokens} tokens "
                f"({current_batch.total_tokens / self.usable_tokens * 100:.1f}% utilization). "
                f"Tables: {table_refs}"
            )

        batch_duration = time.time() - batch_start_time

        logger.info(
            f"✓ Batching complete in {batch_duration:.2f}s: {len(tables)} tables → {len(batches)} batches. "
            f"Avg {len(tables) / len(batches):.1f} tables/batch."
        )
        logger.info(
            f"Sampling strategy distribution: "
            f"schema_only={sampling_stats['schema_only']}, "
            f"wide_table={sampling_stats['wide_table']}, "
            f"tall_table={sampling_stats['tall_table']}, "
            f"default={sampling_stats['default']}"
        )

        return batches

    def validate_batch_budget(self, batch: Batch) -> Dict[str, Any]:
        """
        Validate that batch fits within token budget.

        Args:
            batch: Batch to validate

        Returns:
            Validation result dict with fits_budget, utilization_pct, etc.
        """
        return check_token_budget(
            estimated_tokens=batch.total_tokens,
            max_tokens=self.usable_tokens,
            safety_margin=0  # Already applied in usable_tokens
        )

    def get_batch_summary(self, batches: List[Batch]) -> Dict[str, Any]:
        """
        Generate summary statistics for all batches.

        Args:
            batches: List of batches

        Returns:
            Summary dict with stats
        """
        if not batches:
            return {
                "total_batches": 0,
                "total_tables": 0,
                "avg_tables_per_batch": 0,
                "avg_tokens_per_batch": 0,
                "max_tokens_batch": 0,
                "total_tokens": 0
            }

        total_tables = sum(len(b.tables) for b in batches)
        total_tokens = sum(b.total_tokens for b in batches)
        max_tokens_batch = max(b.total_tokens for b in batches)

        return {
            "total_batches": len(batches),
            "total_tables": total_tables,
            "avg_tables_per_batch": round(total_tables / len(batches), 2),
            "avg_tokens_per_batch": round(total_tokens / len(batches), 0),
            "max_tokens_batch": max_tokens_batch,
            "total_tokens": total_tokens,
            "max_tokens_utilization_pct": round(max_tokens_batch / self.usable_tokens * 100, 2)
        }
