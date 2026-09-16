# utils/token_estimator.py
"""
Token estimation utilities for LLM context management.
Provides token counting for Gemini 2.5 Pro to prevent context window overflow.
"""

import json
import logging
from typing import Dict, Any, List, Union

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Gemini tokenization heuristic: ~3.5 characters per token (English text)
# More conservative than GPT-4 (~4 chars/token) due to Gemini's tokenizer
CHARS_PER_TOKEN = 3.5


def estimate_tokens_from_text(text: str) -> int:
    """
    Estimate token count from plain text string.

    Args:
        text: Input text string

    Returns:
        Estimated token count

    Note:
        Uses conservative heuristic (3.5 chars/token).
        Actual Gemini tokenization may vary ±20%.
    """
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN)


def estimate_tokens_from_json(data: Union[Dict, List]) -> int:
    """
    Estimate token count from JSON-serializable data.

    Args:
        data: Dictionary or list that can be JSON-serialized

    Returns:
        Estimated token count

    Note:
        Serializes to JSON string first, then estimates tokens.
        Accounts for JSON formatting overhead (brackets, quotes, commas).
    """
    if not data:
        return 0

    try:
        json_str = json.dumps(data, default=str)
        return estimate_tokens_from_text(json_str)
    except (TypeError, ValueError) as e:
        logger.warning(f"Failed to serialize data to JSON for token estimation: {e}")
        # Fallback: rough estimate based on string representation
        return estimate_tokens_from_text(str(data))


def estimate_tokens_for_column_metadata(
    column_metadata: Dict[str, Any],
    include_samples: bool = True,
    max_samples: int = 10
) -> int:
    """
    Estimate tokens for column metadata structure.

    Args:
        column_metadata: {
            "column_name": {
                "data_type": "STRING",
                "uniqueness": 95.5,
                "null_percentage": 2.1,
                "sample_values": ["val1", "val2", ...]
            },
            ...
        }
        include_samples: Whether to include sample values in estimation
        max_samples: Maximum samples to include per column

    Returns:
        Estimated token count
    """
    if not column_metadata:
        return 0

    # Build streamlined metadata for token estimation
    streamlined = {}
    for col_name, col_data in column_metadata.items():
        col_summary = {
            "name": col_name,
            "type": col_data.get("data_type", "UNKNOWN"),
            "uniqueness": f"{col_data.get('uniqueness', 0):.1f}%",
            "null_pct": f"{col_data.get('null_percentage', 0):.1f}%"
        }

        if include_samples:
            samples = col_data.get("sample_values", [])
            col_summary["samples"] = samples[:max_samples] if samples else []

        streamlined[col_name] = col_summary

    return estimate_tokens_from_json(streamlined)


def estimate_tokens_for_sample_rows(
    sample_rows: List[Dict],
    max_rows: int = 10
) -> int:
    """
    Estimate tokens for sample data rows.

    Args:
        sample_rows: List of row dictionaries [{col: val, ...}, ...]
        max_rows: Maximum rows to include in estimation

    Returns:
        Estimated token count
    """
    if not sample_rows:
        return 0

    limited_rows = sample_rows[:max_rows]
    return estimate_tokens_from_json(limited_rows)


def estimate_tokens_for_table_data(
    table_reference: str,
    column_metadata: Dict[str, Any],
    sample_rows: List[Dict],
    include_samples: bool = True,
    max_sample_rows: int = 10,
    max_samples_per_column: int = 3
) -> Dict[str, int]:
    """
    Estimate total token count for a single table's data.

    Args:
        table_reference: BigQuery table reference string
        column_metadata: Column statistics and metadata
        sample_rows: Sample data rows
        include_samples: Whether to include sample values
        max_sample_rows: Max sample rows to include
        max_samples_per_column: Max sample values per column in metadata

    Returns:
        {
            "table_reference_tokens": int,
            "metadata_tokens": int,
            "sample_rows_tokens": int,
            "total_tokens": int
        }
    """
    ref_tokens = estimate_tokens_from_text(table_reference)

    metadata_tokens = estimate_tokens_for_column_metadata(
        column_metadata,
        include_samples=include_samples,
        max_samples=max_samples_per_column
    )

    sample_tokens = estimate_tokens_for_sample_rows(
        sample_rows,
        max_rows=max_sample_rows
    )

    total_tokens = ref_tokens + metadata_tokens + sample_tokens

    return {
        "table_reference_tokens": ref_tokens,
        "metadata_tokens": metadata_tokens,
        "sample_rows_tokens": sample_tokens,
        "total_tokens": total_tokens
    }


def estimate_prompt_overhead_tokens(
    num_tables: int,
    prompt_template_chars: int = 2000
) -> int:
    """
    Estimate token overhead for prompt template and instructions.

    Args:
        num_tables: Number of tables in batch
        prompt_template_chars: Estimated character count of prompt template

    Returns:
        Estimated token count for prompt overhead

    Note:
        Includes:
        - System instructions
        - JSON schema examples
        - Business context
        - Formatting guidelines
    """
    # Base prompt template
    base_tokens = estimate_tokens_from_text("x" * prompt_template_chars)

    # Per-table overhead (instructions, separators)
    per_table_overhead = 50  # ~50 tokens per table for formatting

    total_overhead = base_tokens + (num_tables * per_table_overhead)

    logger.debug(
        f"Prompt overhead estimate: {total_overhead} tokens "
        f"(base: {base_tokens}, per-table: {per_table_overhead} × {num_tables})"
    )

    return total_overhead


def check_token_budget(
    estimated_tokens: int,
    max_tokens: int,
    safety_margin: int = 0
) -> Dict[str, Any]:
    """
    Check if estimated tokens fit within budget.

    Args:
        estimated_tokens: Estimated token count
        max_tokens: Maximum allowed tokens
        safety_margin: Additional safety margin to subtract from max_tokens

    Returns:
        {
            "fits_budget": bool,
            "estimated_tokens": int,
            "available_tokens": int,
            "utilization_pct": float,
            "overflow_tokens": int (if fits_budget=False)
        }
    """
    available_tokens = max_tokens - safety_margin
    fits_budget = estimated_tokens <= available_tokens
    utilization_pct = (estimated_tokens / available_tokens * 100) if available_tokens > 0 else 0

    result = {
        "fits_budget": fits_budget,
        "estimated_tokens": estimated_tokens,
        "available_tokens": available_tokens,
        "utilization_pct": round(utilization_pct, 2),
    }

    if not fits_budget:
        result["overflow_tokens"] = estimated_tokens - available_tokens
        logger.warning(
            f"Token budget exceeded: {estimated_tokens}/{available_tokens} tokens "
            f"({utilization_pct:.1f}% utilization) - OVERFLOW by {result['overflow_tokens']} tokens"
        )
    else:
        logger.debug(
            f"Token budget check: {estimated_tokens}/{available_tokens} tokens "
            f"({utilization_pct:.1f}% utilization) - ✓ FITS"
        )

    return result


# Logging helper
def log_token_estimate(
    table_reference: str,
    token_breakdown: Dict[str, int],
    level: str = "INFO"
) -> None:
    """
    Log token estimation breakdown for debugging.

    Args:
        table_reference: Table identifier
        token_breakdown: Result from estimate_tokens_for_table_data()
        level: Logging level (INFO, DEBUG, WARNING)
    """
    log_func = getattr(logger, level.lower(), logger.info)

    log_func(
        f"Token estimate for {table_reference}: "
        f"Total={token_breakdown['total_tokens']}, "
        f"Metadata={token_breakdown['metadata_tokens']}, "
        f"Samples={token_breakdown['sample_rows_tokens']}"
    )
