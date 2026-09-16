# utils/profiling_functions_batched.py

import logging
import time
from typing import Dict, Any, List
from decimal import Decimal
from datetime import datetime, date, time as dt_time
from google.adk.tools import ToolContext
from config.settings import config
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.bg_query_utils import get_bigquery_client
from utils.semantic_analyzer_batched import suggest_composite_keys_with_llm, suggest_composite_keys_batch, _merge_pk_candidates
from utils.composite_key_validator import (
    validate_composite_keys_in_bigquery,
    filter_composite_keys_by_context
)
from utils.context_manager import LLMContextManager, TableData, Batch
import  json, re
try:
    from utils import local_warehouse as bigquery
    GCP_AVAILABLE = True
except ImportError:
    GCP_AVAILABLE = False


def build_profiling_error_result(
    table_reference: str,
    error_message: str,
) -> Dict[str, Any]:
    """Return a schema-complete profiling result when analysis fails."""
    msg = (error_message or "Profiling failed").strip()
    table_ref = (table_reference or "unknown").strip() or "unknown"
    return {
        "status": "error",
        "table_reference": table_ref,
        "analysis_type": "error",
        "processing_mode": "failed",
        "table_summary": {"total_rows": 0, "total_columns": 0},
        "column_analysis": {},
        "default_value_analysis": {},
        "data_quality_score": {
            "overall_score": 0.0,
            "dimension_scores": {
                "completeness": 0.0,
                "uniqueness": 0.0,
                "distribution": 0.0,
                "validity": 0.0,
            },
            "per_column_scores": {},
        },
        "recommendations": [f"Profiling failed: {msg}"],
        "enhanced_analysis": {
            "available": False,
            "version": "1.0",
            "table_context": {
                "detected_level": "unknown",
                "confidence": 0.0,
                "primary_entity": "",
                "business_context": "",
                "reasoning": msg,
            },
            "primary_key_recommendations": [],
            "composite_key_recommendations": {
                "two_column": [],
                "three_column": [],
                "four_column": [],
            },
            "llm_suggested_combos": {
                "two_column": [],
                "three_column": [],
                "four_column": [],
            },
            "validation_results": {"two_column": []},
            "enhanced_recommendations": [msg],
        },
        "error_message": msg,
    }


def ensure_profiling_result_shape(result: Dict[str, Any]) -> Dict[str, Any]:
    """Upgrade partial/error profiling payloads to the full ToolResultItem shape."""
    if not isinstance(result, dict):
        return build_profiling_error_result("unknown", "Invalid profiling result payload")
    if result.get("status") != "error" and result.get("analysis_type"):
        return result
    return build_profiling_error_result(
        str(result.get("table_reference") or "unknown"),
        str(result.get("error_message") or result.get("status") or "Profiling failed"),
    )

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ------------------------
# Utility functions
# ------------------------

logger.warning(
    "[PROFILING TOOL EXECUTED] MODE=BATCHED | file=profiling_functions_batched.py"
)

# ------------------------
# Missing / NaN Handling
# ------------------------

NAN_STRINGS = {
    "nan", "NaN", "NAN",
    "null", "NULL",
    "None", "none",
    "NA", "N/A", "n/a",
    ""
}

# ------------------------
# Datetime Detection
# ------------------------

DATETIME_PATTERNS = [
    (
        re.compile(
            r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
            r"(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?$"
        ),
        "DATETIME",
    ),
    (
        re.compile(
            r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}[ T]\d{1,2}:\d{2}"
            r"(?::\d{2})?(?:\s?(?:AM|PM))?$",
            re.IGNORECASE,
        ),
        "DATETIME",
    ),
    (
        re.compile(
            r"^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\s+\d{1,2}:\d{2}"
            r"(?::\d{2})?(?:\s?(?:AM|PM))?$",
            re.IGNORECASE,
        ),
        "DATETIME",
    ),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "DATE"),
    (re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$"), "DATE"),
    (
        re.compile(
            r"^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}$",
            re.IGNORECASE,
        ),
        "DATE",
    ),
    (
        re.compile(
            r"^\d{1,2}:\d{2}(?::\d{2})?(?:\s?(?:AM|PM))?$",
            re.IGNORECASE,
        ),
        "TIME",
    ),
]

def _make_serializable(obj):
    """Convert non-serializable objects to serializable format"""
    # Filter out None, NaN, and null values
    if obj is None:
        return None
    if isinstance(obj, float):
        import math
        if math.isnan(obj) or math.isinf(obj):
            return None
    if isinstance(obj, (datetime, date, dt_time)):
        return obj.isoformat()
    elif isinstance(obj, Decimal):
        return float(obj)
    elif hasattr(obj, "isoformat"):  # Any date-like object
        return obj.isoformat()
    return obj


def _clean_results(data):
    """Recursively clean data to make it JSON serializable"""
    if isinstance(data, dict):
        return {key: _clean_results(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [_clean_results(item) for item in data]
    else:
        return _make_serializable(data)

def _infer_datetime_type(values):
    """
    Detect DATE / DATETIME / TIME from sample values.
    """

    if not isinstance(values, list):
        return None

    counts = {
        "DATETIME": 0,
        "DATE": 0,
        "TIME": 0
    }

    total = 0

    for value in values:
        if value is None:
            continue

        raw = str(value).strip().strip('"').strip("'")

        if raw.lower() in NAN_STRINGS:
            continue

        total += 1

        for pattern, detected_type in DATETIME_PATTERNS:
            if pattern.match(raw):
                counts[detected_type] += 1
                break

    if total == 0:
        return None

    for dtype in ["DATETIME", "DATE", "TIME"]:
        if counts[dtype] / total >= 0.8:
            return dtype

    return None
# ------------------------
# Core functions
# ------------------------

def _compress_profiling_results_for_agent(full_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Compress profiling results to reduce token count for ADK agent's context window.

    Token reduction strategies:
    1. Remove sample values from column_analysis (saves ~30%)
    2. Keep only top 3 composite key recommendations (saves ~20%)
    3. Remove validation_results (saves ~15%)
    4. Compress recommendations to summaries (saves ~10%)

    Total reduction: ~60-70% of original tokens

    Args:
        full_results: Full profiling results from intelligent_profiling_tool

    Returns:
        Compressed results that fit in ADK agent's 1M token context window
    """
    compressed = []

    for result in full_results:
        if result.get("status") == "error":
            compressed.append(ensure_profiling_result_shape(result))
            continue

        # Compress column_analysis: Remove sample values
        compressed_columns = {}
        for col_name, col_data in result.get("column_analysis", {}).items():
            compressed_columns[col_name] = {
                "data_type": col_data.get("data_type"),
                "null_percentage": col_data.get("null_percentage"),
                "uniqueness_percentage": col_data.get("uniqueness_percentage"),
                "primary_key_candidate": col_data.get("primary_key_candidate", False),
                "foreign_key_candidate": col_data.get("foreign_key_candidate", False),
                # Remove: distinct_values_sample, min_value, max_value, avg_value, blank_count
            }

        # Compress enhanced_analysis: Keep only essential data
        compressed_enhanced = {}
        enhanced = result.get("enhanced_analysis", {})

        if enhanced.get("available"):
            compressed_enhanced = {
                "available": True,
                "version": enhanced.get("version"),
                "table_context": enhanced.get("table_context"),  # Keep full (small)
                "primary_key_recommendations": enhanced.get("primary_key_recommendations", [])[:3],  # Top 3 only
                "composite_key_recommendations": {
                    "two_column": enhanced.get("composite_key_recommendations", {}).get("two_column", [])[:3],
                    "three_column": enhanced.get("composite_key_recommendations", {}).get("three_column", [])[:2],
                    # Remove: four_column (rarely used)
                },
                # Remove: llm_suggested_combos, validation_results (verbose)
                "enhanced_recommendations": enhanced.get("enhanced_recommendations", [])[:5]  # Top 5 only
            }
        else:
            compressed_enhanced = enhanced  # Keep error info as-is

        # Compress default_value_analysis: Keep only columns with >50% default concentration
        compressed_defaults = {}
        for col_name, default_data in result.get("default_value_analysis", {}).items():
            if default_data.get("default_pct", 0) > 50:
                compressed_defaults[col_name] = {
                    "default_value": default_data.get("default_value"),
                    "default_pct": default_data.get("default_pct")
                    # Remove: total_rows, default_count
                }

        # Build compressed result
        compressed_result = {
            "status": result.get("status"),
            "table_reference": result.get("table_reference"),
            "analysis_type": result.get("analysis_type"),
            "processing_mode": result.get("processing_mode"),
            "table_summary": result.get("table_summary"),  # Keep (small)
            "data_quality_score": result.get("data_quality_score"),
            "column_analysis": compressed_columns,
            "default_value_analysis": compressed_defaults,
            "recommendations": result.get("recommendations", [])[:5],  # Top 5 only
            "enhanced_analysis": compressed_enhanced
        }

        compressed.append(compressed_result)

    logger.info(f"Compressed {len(compressed)} profiling results for ADK agent")
    return compressed


def start_profiling(table_reference: str) -> Dict[str, Any]:
    """Enhanced profiling with LLM-based composite key analysis"""
    logging.info(f"Starting enhanced profiling for table: {table_reference}")

    try:
        client = get_bigquery_client()
        table = client.get_table(table_reference)
        results = _analyze_bigquery_table_enhanced(client, table_reference, table)
        return _clean_results(results)

    except Exception as e:
        logger.error(f"Profiling failed for {table_reference}: {e}")
        return build_profiling_error_result(table_reference, str(e))


def intelligent_profiling_tool(
    table_references: List[str], tool_context: ToolContext
) -> List[Dict[str, Any]]:
    """
    Analyze data quality, null percentages, and column statistics.

    Phase 1: Large-scale batched profiling for 100+ tables.

    Args:
        table_references (list[str]): List of BigQuery table references (e.g., project.dataset.table)
        tool_context (ToolContext): ADK tool context

    Returns:
        list[dict]: Data profiling results with quality metrics and recommendations

    IMPORTANT: Results are compressed to fit ADK agent's context window.
    Full results are stored in tool_context for multi-pass endpoint processing.
    """
    profiling_start = time.time()
    logging.info("=== BATCHED PROFILING STARTED ===")
    logging.info(f"Processing {len(table_references)} tables with batched LLM approach")

    try:
        # Phase A: Parallel BigQuery statistical analysis (no LLM)
        logger.info(f"Phase A: Statistical analysis for {len(table_references)} tables...")
        phase_a_start = time.time()
        bq_results = _parallel_bigquery_analysis(table_references)
        phase_a_duration = time.time() - phase_a_start
        logger.info(
            f"✓ Phase A complete: {len(bq_results)} tables analyzed in {phase_a_duration:.2f}s "
            f"(avg {phase_a_duration/len(table_references):.2f}s per table)"
        )

        # Phase B: Create batches with token budget management
        logger.info("Phase B: Creating batches with token budget...")
        phase_b_start = time.time()
        context_mgr = LLMContextManager()
        table_data_list = _convert_to_table_data(bq_results)
        batches = context_mgr.create_batches(table_data_list)
        phase_b_duration = time.time() - phase_b_start

        summary = context_mgr.get_batch_summary(batches)
        logger.info(
            f"✓ Phase B complete in {phase_b_duration:.2f}s: {summary['total_tables']} tables → "
            f"{summary['total_batches']} batches "
            f"(avg {summary['avg_tables_per_batch']} tables/batch, "
            f"max {summary['max_tokens_utilization_pct']:.1f}% token utilization)"
        )

        # Phase C: Batched LLM enhancement
        logger.info(f"Phase C: LLM enhancement for {len(batches)} batches...")
        phase_c_start = time.time()
        enhanced_results = []
        for i, batch in enumerate(batches):
            logger.info(
                f"Processing batch {i+1}/{len(batches)}: "
                f"{len(batch.tables)} tables, {batch.total_tokens} tokens"
            )
            batch_results = _enhance_batch_with_llm(batch, context_mgr)
            enhanced_results.extend(batch_results)
            logger.info(f"✓ Batch {i+1}/{len(batches)} complete: {len(batch_results)} tables enhanced")
        phase_c_duration = time.time() - phase_c_start
        logger.info(
            f"✓ Phase C complete: {len(enhanced_results)} tables enhanced in {phase_c_duration:.2f}s "
            f"(avg {phase_c_duration/len(batches):.2f}s per batch)"
        )

        # Phase D: Aggregate results
        logger.info("Phase D: Aggregating results...")
        phase_d_start = time.time()
        final_results = _aggregate_results(enhanced_results)
        phase_d_duration = time.time() - phase_d_start
        logger.info(f"✓ Phase D complete: {len(final_results)} final results in {phase_d_duration:.2f}s")

        total_duration = time.time() - profiling_start
        logger.info("=== BATCHED PROFILING COMPLETE ===")
        logger.info(
            f"Total execution time: {total_duration:.2f}s ({total_duration/len(table_references):.2f}s per table avg). "
            f"Phase breakdown: A={phase_a_duration:.1f}s, B={phase_b_duration:.1f}s, "
            f"C={phase_c_duration:.1f}s, D={phase_d_duration:.1f}s"
        )

        # ==========================================
        # ADK-COMPLIANT SOLUTION: Use ToolContext.state
        # ==========================================
        # Store full results in session state (NOT returned to ADK agent)
        # This prevents token limit errors while keeping data accessible to /send-stream endpoint

        tool_context.state['profiling_full_results'] = final_results
        logger.info(f"✓ Stored {len(final_results)} full results in ToolContext.state")

        # Return ONLY compressed results to ADK agent (60-70% token reduction)
        compressed_results = _compress_profiling_results_for_agent(final_results)
        logger.info(
            f"✓ Returning {len(compressed_results)} compressed results to ADK agent "
            f"(~{len(str(compressed_results)) / 1000:.0f}K chars vs ~{len(str(final_results)) / 1000:.0f}K chars for full)"
        )

        return compressed_results

    except Exception as e:
        logger.error(f"Batched profiling failed: {e}")
        # Fallback: return error results for all tables
        error_results = [
            build_profiling_error_result(ref, str(e)) for ref in table_references
        ]

        # Store error results in state as well (for consistency)
        tool_context.state['profiling_full_results'] = error_results
        logger.info(f"✓ Stored {len(error_results)} error results in ToolContext.state")

        # Return only error results to agent
        return error_results


# Enhanced BigQuery analysis
# ------------------------

def _analyze_bigquery_table_enhanced(
    client: bigquery.Client, 
    table_reference: str, 
    table
) -> Dict[str, Any]:
    """
    ENHANCED: Perform optimized BigQuery analysis with LLM-powered composite key detection.
    
    This extends the original _analyze_bigquery_table with:
    1. Sample data collection for LLM analysis
    2. LLM-based context detection
    3. Semantic composite key suggestions
    4. BigQuery validation of suggested keys
    5. Business-aware recommendations
    """
    
    logger.info(f"Starting enhanced analysis for {table_reference}")
    
    # ==========================================
    # PHASE 1: Statistical Analysis (Original)
    # ==========================================
    
    schema = table.schema
    total_rows = table.num_rows
    
    logger.info(f"Table has {total_rows} rows and {len(schema)} columns")
    
    # Build optimized query for column-level stats (ORIGINAL CODE)
    select_parts = ["COUNT(*) AS total_rows"]
    for field in schema:
        col = f"`{field.name}`"
        if field.field_type in ["STRING", "TEXT"]:
            select_parts.extend([
                # Was: COUNTIF({col} IS NULL)
                # Now also counts nan/null/none/na/n/a strings as missing
                f"COUNTIF({col} IS NULL OR LOWER(TRIM({col})) IN ('nan','null','none','na','n/a')) AS {field.name}_nulls",
                f"COUNTIF(TRIM({col}) = '') AS {field.name}_blanks",
                # Excludes nan/null strings from unique count
                f"COUNT(DISTINCT CASE WHEN {col} IS NOT NULL AND LOWER(TRIM({col})) NOT IN ('nan','null','none','na','n/a') THEN {col} END) AS {field.name}_uniques",

                f"AVG(LENGTH({col})) AS {field.name}_avg_length",

                # Excludes nan/null strings from sample values
                f"ARRAY_AGG(DISTINCT CASE WHEN {col} IS NOT NULL AND LOWER(TRIM({col})) NOT IN ('nan','null','none','na','n/a') THEN {col} END IGNORE NULLS LIMIT 10) AS {field.name}_samples",

            ])
        elif field.field_type in ["INTEGER", "INT64", "FLOAT", "NUMERIC", "FLOAT64"]:
            select_parts.extend([
                f"COUNTIF({col} IS NULL) AS {field.name}_nulls",
                f"COUNT(DISTINCT {col}) AS {field.name}_uniques",
                f"MIN({col}) AS {field.name}_min",
                f"MAX({col}) AS {field.name}_max",
                f"AVG({col}) AS {field.name}_avg",
                f"ARRAY_AGG(DISTINCT {col} IGNORE NULLS LIMIT 10) AS {field.name}_samples",
            ])
        else:
            select_parts.extend([
                f"COUNTIF({col} IS NULL) AS {field.name}_nulls",
                f"COUNT(DISTINCT {col}) AS {field.name}_uniques",
                f"ARRAY_AGG(DISTINCT {col} IGNORE NULLS LIMIT 10) AS {field.name}_samples",
            ])

    sql = f"SELECT {', '.join(select_parts)} FROM `{table_reference}`"
    print("sql_statement",sql)
    logger.info("Executing statistical analysis query...")
    row = next(iter(client.query(sql).result()))
    total_rows = row.total_rows


    # Build column analysis (ORIGINAL CODE)
    column_analysis = {}
    for field in schema:
        name = field.name
        col_type = field.field_type

        total_count = total_rows
        null_count = getattr(row, f"{name}_nulls", 0)
        unique_count = getattr(row, f"{name}_uniques", 0)
        from utils.sql_samples import to_sample_list
        samples = to_sample_list(getattr(row, f"{name}_samples", None))
         #Filter any remaining nan/null string artifacts from sample list
        samples = [s for s in samples if str(s) not in NAN_STRINGS]
        if col_type in ["STRING", "TEXT"]:
            blanks = getattr(row, f"{name}_blanks", 0) or 0
        else:
            blanks = 0
        # effective_null_count = null_count + blanks

        # # Filter out None/NaN values from samples
        # filtered_samples = [_make_serializable(s) for s in samples]
        # filtered_samples = [s for s in filtered_samples if s is not None]

        analysis = {
            "data_type": col_type,
            "total_count": total_count,
            "null_count": null_count,
            "null_percentage": (null_count / total_count * 100) if total_count else 0,
            "unique_count": unique_count,
            "uniqueness_percentage": (unique_count / total_count * 100) if total_count else 0,
            "distinct_values_sample": filtered_samples,
        }

        if col_type in ["STRING", "TEXT"]:
            blanks = getattr(row, f"{name}_blanks", 0)
            analysis["blank_count"] = blanks
            analysis["blank_percentage"] = (blanks / total_count * 100) if total_count else 0
            analysis["avg_length"] = getattr(row, f"{name}_avg_length", 0)

        elif col_type in ["INTEGER", "INT64", "FLOAT", "NUMERIC", "FLOAT64"]:
            analysis["min_value"] = getattr(row, f"{name}_min", None)
            analysis["max_value"] = getattr(row, f"{name}_max", None)
            analysis["avg_value"] = getattr(row, f"{name}_avg", None)

        # Original key candidacy (still useful for fallback)
        analysis["primary_key_candidate"] = (
            analysis["uniqueness_percentage"] >= 95 and analysis["null_percentage"] <= 5
        )
        analysis["foreign_key_candidate"] = (
            analysis["uniqueness_percentage"] < 50
            and analysis["null_percentage"] <= 10
            and unique_count > 1
        )

        column_analysis[name] = analysis

    logger.info(f"✓ Statistical analysis complete for {len(column_analysis)} columns")
    
    # ==========================================
    # PHASE 2: Get Sample Data for LLM
    # ==========================================
    
    logger.info("Fetching sample data for LLM analysis...")
    sample_query = f"SELECT * FROM `{table_reference}` LIMIT 20"
    sample_rows = []
    
    try:
        sample_result = client.query(sample_query).result()
        for sample_row in sample_result:
            # Convert Row to dict and make serializable
            row_dict = dict(sample_row)
            serializable_row = {k: _make_serializable(v) for k, v in row_dict.items()}
            # Was: serializable_row used as-is
            # Now: any value that is a nan string is replaced with None before LLM sees it
            serializable_row = {
                k: (None if str(v) in NAN_STRINGS else v)
                for k, v in serializable_row.items()
            }
            sample_rows.append(serializable_row)
        logger.info(f"✓ Collected {len(sample_rows)} sample rows")
    except Exception as e:
        logger.warning(f"Failed to fetch sample data: {e}")
        sample_rows = []
    
    # Prepare column metadata for LLM
    # <<<< Added nan string filter for column_metadata sample_values sent to LLM
    # Was: "sample_values": col_analysis["distinct_values_sample"][:5]  — included nan strings
    # Now: nan strings are filtered out so LLM doesn't treat them as real business values
    column_metadata = {}
    for col_name, col_analysis in column_analysis.items():
        column_metadata[col_name] = {
            "data_type": col_analysis["data_type"],
            "uniqueness": col_analysis["uniqueness_percentage"],
            "null_percentage": col_analysis["null_percentage"],
            "sample_values": [
                v for v in col_analysis["distinct_values_sample"][:5]
                if str(v) not in NAN_STRINGS  ]}
    
    # ==========================================
    # PHASE 3: LLM-Based Composite Key Suggestion
    # ==========================================
    
    logger.info("Requesting LLM analysis for table context and composite keys...")
    max_composite_size = getattr(config, 'MAX_COMPOSITE_KEY_SIZE', 3)
    
    try:
        llm_suggestions = suggest_composite_keys_with_llm(
            table_reference=table_reference,
            column_metadata=column_metadata,
            sample_rows=sample_rows,
            max_composite_size=max_composite_size
        )
        logger.info(f"✓ LLM analysis complete - Context: {llm_suggestions['table_context']['detected_level']}")
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        # Use fallback from semantic_analyzer
        from utils.semantic_analyzer import _fallback_statistical_suggestions
        llm_suggestions = _fallback_statistical_suggestions(column_metadata, max_composite_size)
    
    # ==========================================
    # PHASE 4: BigQuery Validation of Composite Keys
    # ==========================================
    
    logger.info("Validating composite key combinations in BigQuery...")
    
    try:
        validated_combos = validate_composite_keys_in_bigquery(
            client=client,
            table_reference=table_reference,
            composite_key_combos={
                "two_column_combos": llm_suggestions.get("two_column_combos", []),
                "three_column_combos": llm_suggestions.get("three_column_combos", []),
                "four_column_combos": llm_suggestions.get("four_column_combos", [])
            },
            total_rows=total_rows
        )
        logger.info(f"✓ Validated {sum(len(v) for v in validated_combos.values() if isinstance(v, list))} combinations")
    except Exception as e:
        logger.error(f"Composite key validation failed: {e}")
        validated_combos = {
            "two_column_results": [],
            "three_column_results": [],
            "four_column_results": []
        }
    
    # ==========================================
    # PHASE 5: Filter by Context (Business Rules)
    # ==========================================
    
    logger.info("Applying business context filters...")
    min_uniqueness = getattr(config, 'MIN_COMPOSITE_UNIQUENESS', 98.0)
    
    try:
        filtered_combos = filter_composite_keys_by_context(
            validated_results=validated_combos,
            table_context=llm_suggestions["table_context"],
            min_uniqueness=min_uniqueness
        )
        logger.info(f"✓ Context filtering complete")
    except Exception as e:
        logger.error(f"Context filtering failed: {e}")
        filtered_combos = {
            "two_column_recommendations": [],
            "three_column_recommendations": [],
            "four_column_recommendations": []
        }
    
    # ==========================================
    # PHASE 6: Default Value Analysis (Original)
    # ==========================================
    
    default_value_analysis = {}
    try:
        select_parts = ["COUNT(*) AS total_rows"]
        for field in schema:
            col = f"`{field.name}`"
            select_parts.append(
                f"APPROX_TOP_COUNT({col}, 1)[OFFSET(0)] AS {field.name}_top"
            )

        sql = f"SELECT {', '.join(select_parts)} FROM `{table_reference}`"
        row = next(iter(client.query(sql).result()))
        total_rows_check = row["total_rows"]

        for field in schema:
            col = field.name
            top_info = getattr(row, f"{col}_top", None)
            if top_info:
                default_value, default_count = top_info["value"], top_info["count"]
                # Was: default_value used as-is
                # Now: if top value is a nan string, treat as no default value
                if str(default_value) in NAN_STRINGS:
                    default_value, default_count = None, 0
                default_pct = default_count / total_rows_check if total_rows_check else 0
            else:
                default_value, default_count, default_pct = None, 0, 0

            default_value_analysis[col] = {
                "total_rows": total_rows_check,
                "default_value": _make_serializable(default_value),
                "default_count": default_count,
                "default_pct": default_pct * 100.0,
            }
        logger.info(f"✓ Default value analysis complete")
    except Exception as e:
        logger.warning(f"Default value analysis failed: {e}")
        default_value_analysis = {}
    
    # ==========================================
    # PHASE 7: Assemble Enhanced Results
    # ==========================================
    
    logger.info("Assembling final results...")
    
    # Merge primary key candidates (statistical + LLM)
    enhanced_pk_candidates = _merge_pk_candidates(
        column_analysis=column_analysis,
        llm_single_key_candidates=llm_suggestions.get("single_key_candidates", [])
    )
    
    # ==========================================
    # ASSEMBLE RESULTS: Backward-Compatible Structure
    # ==========================================
    
    # Build ORIGINAL output structure (completely unchanged for backward compatibility)
    results = {
        "status": "success",
        "table_reference": table_reference,
        "analysis_type": "comprehensive",
        "processing_mode": "bigquery",
        
        # ORIGINAL: Table summary (unchanged)
        "table_summary": {
            "total_rows": total_rows,
            "total_columns": len(schema)
        },
        
        # ORIGINAL: Column-level statistics (unchanged)
        "column_analysis": column_analysis,
        
        # ORIGINAL: Default value analysis (unchanged)
        "default_value_analysis": default_value_analysis,
        
        # ORIGINAL: Quality score (unchanged)
        "data_quality_score": _calculate_quality_score(column_analysis),
        
        # ORIGINAL: Recommendations in original format (unchanged)
        "recommendations": _generate_recommendations(
            column_analysis=column_analysis,
            default_value_analysis=default_value_analysis
        ),
    }
    
    # ==========================================
    # NEW: Add enhanced analysis as separate nested section
    # This is non-breaking - existing UI can ignore this field
    # ==========================================
    try:
        results["enhanced_analysis"] = {
            "available": True,
            "version": "1.0",
            
            # Table context from LLM
            "table_context": llm_suggestions["table_context"],
            
            # Primary key recommendations (merged statistical + LLM)
            "primary_key_recommendations": enhanced_pk_candidates,
            
            # Composite key recommendations (business-filtered)
            "composite_key_recommendations": {
                "two_column": filtered_combos.get("two_column_recommendations", []),
                "three_column": filtered_combos.get("three_column_recommendations", []),
                "four_column": filtered_combos.get("four_column_recommendations", [])
            },
            
            # Raw LLM suggestions (for debugging/advanced use)
            "llm_suggested_combos": {
                "two_column": llm_suggestions.get("two_column_combos", []),
                "three_column": llm_suggestions.get("three_column_combos", []),
                "four_column": llm_suggestions.get("four_column_combos", [])
            },
            
            # BigQuery validation results (for transparency)
            "validation_results": {
                "two_column": validated_combos.get("two_column_results", []),
                "three_column": validated_combos.get("three_column_results", []),
                "four_column": validated_combos.get("four_column_results", [])
            },
            
            # Enhanced recommendations in markdown format (for BSAs)
            "enhanced_recommendations": _generate_enhanced_recommendations(
                column_analysis=column_analysis,
                default_value_analysis=default_value_analysis,
                pk_candidates=enhanced_pk_candidates,
                composite_recommendations=filtered_combos,
                table_context=llm_suggestions["table_context"]
            )
        }
        
        logger.info("✓ Enhanced analysis successfully added to results")
        
    except Exception as e:
        # If enhanced analysis fails, include error info but don't break profiling
        logger.warning(f"Enhanced analysis failed: {e}")
        results["enhanced_analysis"] = {
            "available": False,
            "error": str(e),
            "fallback_mode": True,
            "message": "Enhanced analysis unavailable, using standard profiling only"
        }
    
    logger.info(f"✓ Enhanced profiling complete for {table_reference}")
    
    return results


# ------------------------
# Helpers
# ------------------------

def _generate_mock_profiling_results(table_reference: str) -> Dict[str, Any]:
    """Generate mock profiling results for testing"""
    return {
        "status": "success",
        "table_reference": table_reference,
        "analysis_type": "mock_comprehensive",
        "table_summary": {"total_rows": 1000, "total_columns": 8},
        "column_analysis": {
            "claim_id": {
                "data_type": "STRING",
                "null_percentage": 0.0,
                "unique_count": 1000,
                "uniqueness_percentage": 100.0,
                "primary_key_candidate": True,
                "distinct_values_sample": ["CLM001", "CLM002", "CLM003"],
            }
        },
        "default_value_analysis": {},
        "data_quality_score": 0.95,
        "recommendations": [
            "claim_id is an excellent primary key candidate (100% unique, no nulls)"
        ],
        "processing_mode": "mock",
    }


def _calculate_quality_score(column_analysis: Dict) -> float:
    """Calculate overall data quality score"""
    scores = []

    for _, analysis in column_analysis.items():
        if "error" in analysis:
            continue
        score = 1.0
        null_pct = analysis.get("null_percentage", 0)

        if null_pct > 50:
            score -= 0.5
        elif null_pct > 20:
            score -= 0.3
        elif null_pct > 5:
            score -= 0.1

        scores.append(max(0.0, score))

    return sum(scores) / len(scores) if scores else 0.0


def _generate_recommendations(
    column_analysis: Dict, default_value_analysis: Dict = None
) -> list:
    """Generate actionable recommendations"""
    recommendations = []

    for column_name, analysis in column_analysis.items():
        if "error" in analysis:
            continue

        null_pct = analysis.get("null_percentage", 0)
        blank_pct      = analysis.get("blank_percentage", 0)      # empty strings only

        uniqueness_pct = analysis.get("uniqueness_percentage", 0)

        if null_pct > 30:
            recommendations.append(
                f"Column '{column_name}': High null percentage ({null_pct:.1f}%) - investigate data collection"
            )
        if blank_pct > 30:
            recommendations.append(
                f"Column '{column_name}': High blank percentage ({blank_pct:.1f}%) "
                f"- data collected but empty, check data entry process"
            )

        if uniqueness_pct == 100 and null_pct == 0:
            recommendations.append(
                f"Column '{column_name}': Excellent primary key candidate (100% unique, no nulls)"
            )
        elif uniqueness_pct > 95 and null_pct < 5:
            recommendations.append(
                f"Column '{column_name}': Good primary key candidate ({uniqueness_pct:.1f}% unique)"
            )
        elif analysis.get("foreign_key_candidate"):
            recommendations.append(
                f"Column '{column_name}': Potential foreign key (low uniqueness suggests references)"
            )

        if default_value_analysis and column_name in default_value_analysis:
            default_info = default_value_analysis[column_name]
            default_pct = default_info.get("default_pct", 0)
            default_value = default_info.get("default_value")

            if default_pct > 50 and default_value is not None:
                recommendations.append(
                    f"Column '{column_name}': Dominant value '{default_value}' appears in {default_pct:.1f}% of records - consider if this skews analysis"
                )
            elif default_pct > 80:
                recommendations.append(
                    f"Column '{column_name}': Very high concentration ({default_pct:.1f}%) of single value - check for data quality issues"
                )

    return recommendations

def _generate_enhanced_recommendations(
    column_analysis: Dict,
    default_value_analysis: Dict,
    pk_candidates: List[Dict],
    composite_recommendations: Dict,
    table_context: Dict
) -> List[str]:
    """Generate enhanced actionable recommendations with LLM context"""
    recommendations = []
    
    # Table context recommendation
    detected_level = table_context.get("detected_level", "unknown")
    confidence = table_context.get("confidence", 0.0)
    
    recommendations.append(
        f"📊 Table Context: Detected as '{detected_level}' data with {confidence*100:.0f}% confidence. "
        f"{table_context.get('reasoning', '')}"
    )
    
    # Primary key recommendations
    if pk_candidates:
        top_pk = pk_candidates[0]
        if top_pk["confidence"] == "HIGH":
            recommendations.append(
                f"🔑 Primary Key: '{top_pk['column']}' is the recommended primary key "
                f"({top_pk['uniqueness_percentage']:.1f}% unique, {top_pk['null_percentage']:.1f}% nulls)"
            )
        elif top_pk["confidence"] == "MEDIUM":
            recommendations.append(
                f"⚠️ Primary Key: '{top_pk['column']}' is a candidate but has some issues "
                f"({top_pk['uniqueness_percentage']:.1f}% unique, {top_pk['null_percentage']:.1f}% nulls). "
                f"Consider data quality improvements."
            )
    
    # Composite key recommendations
    two_col = composite_recommendations.get("two_column_recommendations", [])
    three_col = composite_recommendations.get("three_column_recommendations", [])
    
    if two_col:
        top_combo = two_col[0]
        recommendations.append(
            f"🔗 Composite Key Option (2 columns): [{', '.join(top_combo['columns'])}] "
            f"({top_combo['uniqueness_percentage']:.1f}% unique) - {top_combo['business_meaning']}"
        )
    
    if three_col:
        top_combo = three_col[0]
        recommendations.append(
            f"🔗 Composite Key Option (3 columns): [{', '.join(top_combo['columns'])}] "
            f"({top_combo['uniqueness_percentage']:.1f}% unique) - {top_combo['business_meaning']}"
        )
    
    # Data quality issues
    for column_name, analysis in column_analysis.items():
        null_pct = analysis.get("null_percentage", 0)
        blank_pct = analysis.get("blank_percentage", 0)  # Extract blank_pct from analysis
        if null_pct > 30:
            recommendations.append(
                f"⚠️ Data Quality: Column '{column_name}' has high null percentage ({null_pct:.1f}%) "
                f"- investigate data collection"
            )
        if blank_pct > 30:
            recommendations.append(
                f"⚠️ Data Quality: Column '{column_name}' has high blank percentage ({blank_pct:.1f}%) "
                f"- data collected but empty"
            )
    
    # Default value warnings
    if default_value_analysis:
        for col, info in default_value_analysis.items():
            default_pct = info.get("default_pct", 0)
            if default_pct > 80:
                recommendations.append(
                    f"⚠️ Default Value: Column '{col}' has {default_pct:.1f}% of values as '{info.get('default_value')}' "
                    f"- check for data quality issues"
                )

    return recommendations


# ------------------------
# Phase 1: Batched Profiling Helper Functions
# ------------------------

def _parallel_bigquery_analysis(table_references: List[str]) -> List[Dict[str, Any]]:
    """
    Phase A: Parallel BigQuery statistical analysis (no LLM calls).

    Runs BigQuery stats for all tables in parallel, collecting:
    - Column-level statistics
    - Sample rows
    - Default value analysis

    Args:
        table_references: List of BigQuery table references

    Returns:
        List of dicts with BigQuery statistics for each table
    """
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(config.PROFILING_MAX_WORKERS, len(table_references))) as executor:
        future_to_ref = {
            executor.submit(_statistical_analysis_only, get_bigquery_client(), ref): ref
            for ref in table_references
        }

        for future in as_completed(future_to_ref):
            ref = future_to_ref[future]
            try:
                result = future.result()
                results.append(ensure_profiling_result_shape(result))
            except Exception as e:
                logger.error(f"Statistical analysis failed for {ref}: {e}")
                results.append(build_profiling_error_result(ref, str(e)))

    return results


def _statistical_analysis_only(client, table_reference: str) -> Dict[str, Any]:
    """
    BigQuery-only statistical analysis (no LLM calls).

    Extracts statistical analysis logic from _analyze_bigquery_table_enhanced:
    1. Column-level statistics (lines 315-396)
    2. Sample data collection (lines 402-416)
    3. Default value analysis (lines 501-532)

    Args:
        client: BigQuery client
        table_reference: BigQuery table reference

    Returns:
        Dict with column_analysis, sample_rows, default_value_analysis, table_summary
    """
    table_start = time.time()
    try:
        table = client.get_table(table_reference)
        schema = table.schema
        total_rows = table.num_rows

        logger.debug(f"Analyzing {table_reference}: {total_rows} rows, {len(schema)} columns")

        # Build optimized query for column-level stats
        select_parts = ["COUNT(*) AS total_rows"]
        for field in schema:
            col = f"`{field.name}`"
            if field.field_type in ["STRING", "TEXT"]:
                select_parts.extend([
                    f"COUNTIF({col} IS NULL) AS {field.name}_nulls",
                    f"COUNTIF(TRIM({col}) = '') AS {field.name}_blanks",
                    f"COUNT(DISTINCT {col}) AS {field.name}_uniques",
                    f"AVG(LENGTH({col})) AS {field.name}_avg_length",
                    f"ARRAY_AGG(DISTINCT {col} IGNORE NULLS LIMIT 10) AS {field.name}_samples",
                ])
            elif field.field_type in ["INTEGER", "INT64", "FLOAT", "NUMERIC", "FLOAT64"]:
                select_parts.extend([
                    f"COUNTIF({col} IS NULL) AS {field.name}_nulls",
                    f"COUNT(DISTINCT {col}) AS {field.name}_uniques",
                    f"MIN({col}) AS {field.name}_min",
                    f"MAX({col}) AS {field.name}_max",
                    f"AVG({col}) AS {field.name}_avg",
                    f"ARRAY_AGG(DISTINCT {col} IGNORE NULLS LIMIT 10) AS {field.name}_samples",
                ])
            else:
                select_parts.extend([
                    f"COUNTIF({col} IS NULL) AS {field.name}_nulls",
                    f"COUNT(DISTINCT {col}) AS {field.name}_uniques",
                    f"ARRAY_AGG(DISTINCT {col} IGNORE NULLS LIMIT 10) AS {field.name}_samples",
                ])

        sql = f"SELECT {', '.join(select_parts)} FROM `{table_reference}`"
        row = next(iter(client.query(sql).result()))

        # Build column analysis
        column_analysis = {}
        for field in schema:
            name = field.name
            col_type = field.field_type

            total_count = total_rows
            null_count = getattr(row, f"{name}_nulls", 0)
            unique_count = getattr(row, f"{name}_uniques", 0)
            from utils.sql_samples import to_sample_list
            samples = to_sample_list(getattr(row, f"{name}_samples", None))

            # Filter out None/NaN values from samples
            filtered_samples = [_make_serializable(s) for s in samples]
            filtered_samples = [
                s for s in filtered_samples
                if s is not None and str(s) not in NAN_STRINGS
            ]

            detected_temporal_type = None

            if col_type in ["STRING", "TEXT"]:
                detected_temporal_type = _infer_datetime_type(filtered_samples)

            final_data_type = (
                detected_temporal_type
                if detected_temporal_type
                else col_type
            )

            analysis = {
                "data_type": final_data_type,
                "total_count": total_count,
                "null_count": null_count,
                "null_percentage": (null_count / total_count * 100) if total_count else 0,
                "unique_count": unique_count,
                "uniqueness_percentage": (unique_count / total_count * 100) if total_count else 0,
                "distinct_values_sample": filtered_samples,
            }
            analysis["original_data_type"] = col_type

            if detected_temporal_type:
                analysis["detected_semantic_type"] = detected_temporal_type
            if col_type in ["STRING", "TEXT"]:
                blanks = getattr(row, f"{name}_blanks", 0)
                analysis["blank_count"] = blanks
                analysis["blank_percentage"] = (blanks / total_count * 100) if total_count else 0
                analysis["avg_length"] = getattr(row, f"{name}_avg_length", 0)

            elif col_type in ["INTEGER", "INT64", "FLOAT", "NUMERIC", "FLOAT64"]:
                analysis["min_value"] = getattr(row, f"{name}_min", None)
                analysis["max_value"] = getattr(row, f"{name}_max", None)
                analysis["avg_value"] = getattr(row, f"{name}_avg", None)

            # Key candidacy
            analysis["primary_key_candidate"] = (
                analysis["uniqueness_percentage"] >= 95 and analysis["null_percentage"] <= 5
            )
            analysis["foreign_key_candidate"] = (
                analysis["uniqueness_percentage"] < 50
                and analysis["null_percentage"] <= 10
                and unique_count > 1
            )

            column_analysis[name] = analysis

        # Get sample data
        sample_query = f"SELECT * FROM `{table_reference}` LIMIT 20"
        sample_rows = []

        try:
            sample_result = client.query(sample_query).result()
            for sample_row in sample_result:
                row_dict = dict(sample_row)
                serializable_row = {k: _make_serializable(v) for k, v in row_dict.items()}
                sample_rows.append(serializable_row)
        except Exception as e:
            logger.warning(f"Failed to fetch sample data for {table_reference}: {e}")
            sample_rows = []

        # Default value analysis
        default_value_analysis = {}
        try:
            select_parts = ["COUNT(*) AS total_rows"]
            for field in schema:
                col = f"`{field.name}`"
                select_parts.append(
                    f"APPROX_TOP_COUNT({col}, 1)[OFFSET(0)] AS {field.name}_top"
                )

            sql = f"SELECT {', '.join(select_parts)} FROM `{table_reference}`"
            row = next(iter(client.query(sql).result()))
            total_rows_check = row["total_rows"]

            for field in schema:
                col = field.name
                top_info = getattr(row, f"{col}_top", None)
                if top_info:
                    default_value, default_count = top_info["value"], top_info["count"]
                    default_pct = default_count / total_rows_check if total_rows_check else 0
                else:
                    default_value, default_count, default_pct = None, 0, 0

                default_value_analysis[col] = {
                    "total_rows": total_rows_check,
                    "default_value": _make_serializable(default_value),
                    "default_count": default_count,
                    "default_pct": default_pct * 100.0,
                }
        except Exception as e:
            logger.warning(f"Default value analysis failed for {table_reference}: {e}")
            default_value_analysis = {}

        analysis_duration = time.time() - table_start
        logger.debug(f"✓ Statistical analysis for {table_reference}: {analysis_duration:.2f}s")

        return {
            "status": "success",
            "table_reference": table_reference,
            "column_analysis": column_analysis,
            "sample_rows": sample_rows,
            "default_value_analysis": default_value_analysis,
            "table_summary": {"total_rows": total_rows, "total_columns": len(schema)}
        }

    except Exception as e:
        logger.error(f"Statistical analysis failed for {table_reference}: {e}")
        return build_profiling_error_result(table_reference, str(e))


def _convert_to_table_data(bq_results: List[Dict]) -> List[TableData]:
    """
    Convert BigQuery statistical results to TableData objects.

    Args:
        bq_results: List of dicts from _statistical_analysis_only

    Returns:
        List of TableData objects ready for batching
    """
    table_data_list = []

    for bq_result in bq_results:
        if bq_result.get("status") == "error":
            logger.warning(f"Skipping failed table: {bq_result.get('table_reference')}")
            continue

        # Prepare column metadata for LLM
        column_metadata = {}
        for col_name, col_analysis in bq_result["column_analysis"].items():
            column_metadata[col_name] = {
                "data_type": col_analysis["data_type"],
                "uniqueness": col_analysis["uniqueness_percentage"],
                "null_percentage": col_analysis["null_percentage"],
                "sample_values": col_analysis["distinct_values_sample"][:5]
            }

        table_data = TableData(
            table_reference=bq_result["table_reference"],
            column_metadata=column_metadata,
            sample_rows=bq_result["sample_rows"],
            total_rows=bq_result["table_summary"]["total_rows"],
            total_columns=bq_result["table_summary"]["total_columns"]
        )

        # Store full BigQuery result for later use
        table_data.bq_result = bq_result

        table_data_list.append(table_data)

    return table_data_list


def _enhance_batch_with_llm(batch: Batch, context_mgr: LLMContextManager) -> List[Dict]:
    """
    Phase C: Single LLM call for batch, then validate each table.

    Steps:
    1. Call suggest_composite_keys_batch() → multi-table LLM response
    2. For each table in batch:
       a. Validate composite keys in BigQuery
       b. Apply context filtering
       c. Generate enhanced recommendations
    3. Return list of enhanced results

    Args:
        batch: Batch object with TableData objects
        context_mgr: LLMContextManager instance

    Returns:
        List of enhanced profiling dicts (same format as _analyze_bigquery_table_enhanced)
    """
    client = get_bigquery_client()
    max_composite_size = config.MAX_COMPOSITE_KEY_SIZE
    min_uniqueness = config.MIN_COMPOSITE_UNIQUENESS

    # Single LLM call for all tables in batch
    logger.info(f"Calling LLM for batch of {len(batch.tables)} tables...")
    llm_start = time.time()

    try:
        # Call batch function
        llm_batch_response = suggest_composite_keys_batch(
            tables=batch.tables,
            max_composite_size=max_composite_size
        )
        llm_duration = time.time() - llm_start
        logger.info(
            f"✓ LLM batch call completed in {llm_duration:.2f}s for {len(batch.tables)} tables "
            f"(avg {llm_duration/len(batch.tables):.2f}s per table)"
        )
    except Exception as e:
        logger.error(f"Batch LLM call failed: {e}")
        # Fallback: use single-table LLM calls
        llm_batch_response = {"tables": []}
        for table_data in batch.tables:
            try:
                single_result = suggest_composite_keys_with_llm(
                    table_reference=table_data.table_reference,
                    column_metadata=table_data.column_metadata,
                    sample_rows=table_data.sample_rows,
                    max_composite_size=max_composite_size
                )
                llm_batch_response["tables"].append(single_result)
            except Exception as inner_e:
                logger.error(f"LLM fallback failed for {table_data.table_reference}: {inner_e}")
                llm_batch_response["tables"].append({
                    "table_context": {"detected_level": "unknown", "confidence": 0.0},
                    "single_key_candidates": [],
                    "two_column_combos": [],
                    "three_column_combos": [],
                    "four_column_combos": []
                })

    enhanced_results = []

    for i, table_data in enumerate(batch.tables):
        try:
            # Get LLM suggestions for this table
            if i < len(llm_batch_response.get("tables", [])):
                llm_suggestions = llm_batch_response["tables"][i]
            else:
                logger.warning(f"No LLM suggestions for table {i}: {table_data.table_reference}")
                llm_suggestions = {
                    "table_context": {"detected_level": "unknown", "confidence": 0.0},
                    "single_key_candidates": [],
                    "two_column_combos": [],
                    "three_column_combos": [],
                    "four_column_combos": []
                }

            # Validate composite keys in BigQuery
            bq_result = table_data.bq_result
            total_rows = bq_result["table_summary"]["total_rows"]

            combo_count = (
                len(llm_suggestions.get("two_column_combos", [])) +
                len(llm_suggestions.get("three_column_combos", [])) +
                len(llm_suggestions.get("four_column_combos", []))
            )
            logger.debug(f"Validating {combo_count} composite key combinations for {table_data.table_reference}")

            validation_start = time.time()
            validated_combos = validate_composite_keys_in_bigquery(
                client=client,
                table_reference=table_data.table_reference,
                composite_key_combos={
                    "two_column_combos": llm_suggestions.get("two_column_combos", []),
                    "three_column_combos": llm_suggestions.get("three_column_combos", []),
                    "four_column_combos": llm_suggestions.get("four_column_combos", [])
                },
                total_rows=total_rows
            )
            validation_duration = time.time() - validation_start
            logger.debug(f"✓ Validation complete for {table_data.table_reference}: {validation_duration:.2f}s")

            # Filter by context
            filtered_combos = filter_composite_keys_by_context(
                validated_results=validated_combos,
                table_context=llm_suggestions["table_context"],
                min_uniqueness=min_uniqueness
            )

            # Merge PK candidates
            enhanced_pk_candidates = _merge_pk_candidates(
                column_analysis=bq_result["column_analysis"],
                llm_single_key_candidates=llm_suggestions.get("single_key_candidates", [])
            )

            # Assemble final result (same structure as _analyze_bigquery_table_enhanced)
            result = {
                "status": "success",
                "table_reference": table_data.table_reference,
                "analysis_type": "comprehensive",
                "processing_mode": "bigquery_batched",

                "table_summary": bq_result["table_summary"],
                "column_analysis": bq_result["column_analysis"],
                "default_value_analysis": bq_result["default_value_analysis"],
                "data_quality_score": _calculate_quality_score(bq_result["column_analysis"]),
                "recommendations": _generate_recommendations(
                    column_analysis=bq_result["column_analysis"],
                    default_value_analysis=bq_result["default_value_analysis"]
                ),

                # Enhanced analysis section
                "enhanced_analysis": {
                    "available": True,
                    "version": "1.0",
                    "table_context": llm_suggestions["table_context"],
                    "primary_key_recommendations": enhanced_pk_candidates,
                    "composite_key_recommendations": {
                        "two_column": filtered_combos.get("two_column_recommendations", []),
                        "three_column": filtered_combos.get("three_column_recommendations", []),
                        "four_column": filtered_combos.get("four_column_recommendations", [])
                    },
                    "llm_suggested_combos": {
                        "two_column": llm_suggestions.get("two_column_combos", []),
                        "three_column": llm_suggestions.get("three_column_combos", []),
                        "four_column": llm_suggestions.get("four_column_combos", [])
                    },
                    "validation_results": {
                        "two_column": validated_combos.get("two_column_results", []),
                        "three_column": validated_combos.get("three_column_results", []),
                        "four_column": validated_combos.get("four_column_results", [])
                    },
                    "enhanced_recommendations": _generate_enhanced_recommendations(
                        column_analysis=bq_result["column_analysis"],
                        default_value_analysis=bq_result["default_value_analysis"],
                        pk_candidates=enhanced_pk_candidates,
                        composite_recommendations=filtered_combos,
                        table_context=llm_suggestions["table_context"]
                    )
                }
            }

            enhanced_results.append(result)

        except Exception as e:
            logger.error(f"Enhancement failed for {table_data.table_reference}: {e}")
            # Log full traceback to identify which step failed
            import traceback
            logger.error(f"Traceback for enhancement failure:\n{traceback.format_exc()}")
            # Return basic result without enhancement
            bq_result = table_data.bq_result
            enhanced_results.append({
                "status": "partial",
                "table_reference": table_data.table_reference,
                "analysis_type": "comprehensive",
                "processing_mode": "bigquery_batched",
                "table_summary": bq_result["table_summary"],
                "column_analysis": bq_result["column_analysis"],
                "default_value_analysis": bq_result["default_value_analysis"],
                "data_quality_score": _calculate_quality_score(bq_result["column_analysis"]),
                "recommendations": _generate_recommendations(
                    column_analysis=bq_result["column_analysis"],
                    default_value_analysis=bq_result["default_value_analysis"]
                ),
                "enhanced_analysis": {
                    "available": False,
                    "error": str(e),
                    "message": "Enhancement failed, using basic profiling only"
                }
            })

    return enhanced_results


def _aggregate_results(enhanced_results: List[Dict]) -> List[Dict]:
    """
    Phase D: Aggregate batch results.

    Currently returns results as-is (same format as before).
    Future enhancements could add:
    - Cross-table insights
    - Session-level summary statistics
    - Relationship hints across batches

    Args:
        enhanced_results: List of enhanced profiling results

    Returns:
        Aggregated results (currently pass-through)
    """
    logger.info(f"Aggregated {len(enhanced_results)} table profiling results")

    # Clean results to ensure JSON serializability
    cleaned_results = [_clean_results(result) for result in enhanced_results]

    return cleaned_results


# ------------------------
# Streaming Tool (ADK AsyncGenerator Pattern)
# ------------------------

from typing import AsyncGenerator

async def intelligent_profiling_tool_streaming(
    table_references: List[str],
    tool_context: ToolContext
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    STREAMING VERSION: Analyze data quality with progressive batch yields.

    This is the ADK streaming tool pattern - yields results batch-by-batch
    instead of waiting for all batches to complete.

    Args:
        table_references (list[str]): List of BigQuery table references
        tool_context (ToolContext): ADK tool context

    Yields:
        dict: Batch progress updates with format:
            {
                "event_type": "batch_complete",
                "batch_index": 1,
                "total_batches": 5,
                "batch_results": [...],  # Tables in this batch
                "progress": 0.2  # 20% complete
            }
    """
    profiling_start = time.time()
    logging.info("=== STREAMING BATCHED PROFILING STARTED ===")
    logging.info(f"Processing {len(table_references)} tables with streaming batched approach")

    try:
        # Phase A: Parallel BigQuery statistical analysis (same as non-streaming)
        logger.info(f"Phase A: Statistical analysis for {len(table_references)} tables...")
        phase_a_start = time.time()
        bq_results = _parallel_bigquery_analysis(table_references)
        phase_a_duration = time.time() - phase_a_start
        logger.info(
            f"✓ Phase A complete: {len(bq_results)} tables analyzed in {phase_a_duration:.2f}s "
            f"(avg {phase_a_duration/len(table_references):.2f}s per table)"
        )

        # Phase B: Create batches (same as non-streaming)
        logger.info("Phase B: Creating batches with token budget...")
        phase_b_start = time.time()
        context_mgr = LLMContextManager()
        table_data_list = _convert_to_table_data(bq_results)
        batches = context_mgr.create_batches(table_data_list)
        phase_b_duration = time.time() - phase_b_start

        summary = context_mgr.get_batch_summary(batches)
        logger.info(
            f"✓ Phase B complete in {phase_b_duration:.2f}s: {summary['total_tables']} tables → "
            f"{summary['total_batches']} batches "
            f"(avg {summary['avg_tables_per_batch']} tables/batch, "
            f"max {summary['max_tokens_utilization_pct']:.1f}% token utilization)"
        )

        # Phase C: Batched LLM enhancement - YIELD after each batch
        logger.info(f"Phase C: Streaming LLM enhancement for {len(batches)} batches...")
        phase_c_start = time.time()

        for i, batch in enumerate(batches):
            batch_start = time.time()
            logger.info(
                f"Processing batch {i+1}/{len(batches)}: "
                f"{len(batch.tables)} tables, {batch.total_tokens} tokens"
            )

            # Process this batch
            batch_results = _enhance_batch_with_llm(batch, context_mgr)
            batch_duration = time.time() - batch_start

            logger.info(
                f"✓ Batch {i+1}/{len(batches)} complete: {len(batch_results)} tables enhanced in {batch_duration:.2f}s"
            )

            # ✅ ADK STREAMING: Yield batch immediately
            yield {
                "event_type": "batch_complete",
                "batch_index": i + 1,
                "total_batches": len(batches),
                "batch_results": _clean_results(batch_results),  # Clean for JSON serialization
                "progress": (i + 1) / len(batches),
                "batch_stats": {
                    "tables_in_batch": len(batch_results),
                    "processing_time": batch_duration
                }
            }

        phase_c_duration = time.time() - phase_c_start
        total_duration = time.time() - profiling_start

        logger.info("=== STREAMING BATCHED PROFILING COMPLETE ===")
        logger.info(
            f"Total execution time: {total_duration:.2f}s ({total_duration/len(table_references):.2f}s per table avg). "
            f"Phase breakdown: A={phase_a_duration:.1f}s, B={phase_b_duration:.1f}s, C={phase_c_duration:.1f}s"
        )

    except Exception as e:
        logger.error(f"Streaming batched profiling failed: {e}")
        # Yield error result
        yield {
            "event_type": "error",
            "error_message": str(e),
            "failed_tables": table_references
        }
