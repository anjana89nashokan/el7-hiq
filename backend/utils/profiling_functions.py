# tools/profiling_function.py


import logging
import time
from typing import Dict, Any, List
from decimal import Decimal
from datetime import datetime, date, time as dt_time
from google.adk.tools import ToolContext
from config.settings import config
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.bg_query_utils import get_bigquery_client
from utils.profiling_functions_batched import build_profiling_error_result
from utils.semantic_analyzer import suggest_composite_keys_with_llm,_merge_pk_candidates
from utils.scoring_utils import calculate_quality_score
from utils.composite_key_validator import (
    validate_composite_keys_in_bigquery,
    filter_composite_keys_by_context
)
import re, json
try:
    from utils import local_warehouse as bigquery
    GCP_AVAILABLE = True
except ImportError:
    GCP_AVAILABLE = False


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


logger.warning(
    "[PROFILING TOOL EXECUTED] MODE=NORMAL TRIGERREDDD | file=profiling_functions.py"
)


# ------------------------
# Utility functions
# ------------------------

NAN_STRINGS = {"nan", "NaN", "NAN", "null", "NULL", "None", "none", "NA", "N/A", "n/a", ""}

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

    Args:
        table_references (list[str]): List of BigQuery table references (e.g., project.dataset.table)
        tool_context (ToolContext): ADK tool context

    Returns:
        list[dict]: Data profiling results with quality metrics and recommendations
    """
    logging.info("=== INITIAL PROFILING STARTED ===")
    print(f"Table references: {table_references}")

    results: List[Dict[str, Any]] = []

    # Run profiling jobs in parallel threads
    with ThreadPoolExecutor(max_workers=min(8, len(table_references))) as executor:
        future_to_ref = {executor.submit(start_profiling, ref): ref for ref in table_references}

        for future in as_completed(future_to_ref):
            
            ref = future_to_ref[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append(build_profiling_error_result(ref, str(e)))

    print(f"Intelligent Profiling Tool Response - {results}")
    return results


# ------------------------
# BigQuery optimized analysis
# ------------------------

# ------------------------
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
    total_rows = row["total_rows"]

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

        detected_temporal_type = None

        if col_type in ["STRING", "TEXT"]:
            detected_temporal_type = _infer_datetime_type(samples)

        final_data_type = detected_temporal_type if detected_temporal_type else col_type

        analysis = {
            "data_type": final_data_type,
            "total_count": total_count,
            "null_count": null_count,
            "null_percentage": (null_count / total_count * 100) if total_count else 0,
            "unique_count": unique_count,
            "uniqueness_percentage": (unique_count / total_count * 100) if total_count else 0,
            "distinct_values_sample": samples,
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

    logger.info(f"Statistical analysis complete for {len(column_analysis)} columns")
    
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
        logger.info(f"Collected {len(sample_rows)} sample rows")
    except Exception as e:
        logger.warning(f"Failed to fetch sample data: {e}")
        sample_rows = []
    
    # Prepare column metadata for LLM
    #Added nan string filter for column_metadata sample_values sent to LLM
    # Was: "sample_values": col_analysis["distinct_values_sample"][:5]  — included nan strings
    # Now: nan strings are filtered out so LLM doesn't treat them as real business values
 
    column_metadata = {}
    for col_name, col_analysis in column_analysis.items():
        column_metadata[col_name] = {
            "data_type": col_analysis["data_type"],
            "uniqueness": col_analysis["uniqueness_percentage"],
            "null_percentage": col_analysis["null_percentage"],
            # "sample_values": col_analysis["distinct_values_sample"][:5]
            "sample_values": [
                v for v in col_analysis["distinct_values_sample"][:5]
                if str(v) not in NAN_STRINGS ]
        }
    
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
        logger.info(f"LLM analysis complete - Context: {llm_suggestions['table_context']['detected_level']}")
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
        logger.info(f"Validated {sum(len(v) for v in validated_combos.values() if isinstance(v, list))} combinations")
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
        logger.info(f"Context filtering complete")
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
                 # Same fix as CHANGE 6 applied to enhanced function
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
        logger.info(f"Default value analysis complete")
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
        "data_quality_score": calculate_quality_score(column_analysis,default_value_analysis),
        
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
        
        logger.info("Enhanced analysis successfully added to results")
        
    except Exception as e:
        # If enhanced analysis fails, include error info but don't break profiling
        logger.warning(f"Enhanced analysis failed: {e}")
        results["enhanced_analysis"] = {
            "available": False,
            "error": str(e),
            "fallback_mode": True,
            "message": "Enhanced analysis unavailable, using standard profiling only"
        }
    
    logger.info(f"Enhanced profiling complete for {table_reference}")
    
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



def _generate_recommendations(
    column_analysis: Dict, default_value_analysis: Dict = None
) -> list:
    """Generate actionable recommendations"""
    recommendations = []

    for column_name, analysis in column_analysis.items():
        if "error" in analysis:
            continue

        null_pct = analysis.get("null_percentage", 0)  # true nulls only
        blank_pct = analysis.get("blank_percentage", 0)  # empty strings only

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
        f" Table Context: Detected as '{detected_level}' data with {confidence*100:.0f}% confidence. "
        f"{table_context.get('reasoning', '')}"
    )
    
    # Primary key recommendations
    if pk_candidates:
        top_pk = pk_candidates[0]
        if top_pk["confidence"] == "HIGH":
            recommendations.append(
                f" Primary Key: '{top_pk['column']}' is the recommended primary key "
                f"({top_pk['uniqueness_percentage']:.1f}% unique, {top_pk['null_percentage']:.1f}% nulls)"
            )
        elif top_pk["confidence"] == "MEDIUM":
            recommendations.append(
                f" Primary Key: '{top_pk['column']}' is a candidate but has some issues "
                f"({top_pk['uniqueness_percentage']:.1f}% unique, {top_pk['null_percentage']:.1f}% nulls). "
                f"Consider data quality improvements."
            )
    
    # Composite key recommendations
    two_col = composite_recommendations.get("two_column_recommendations", [])
    three_col = composite_recommendations.get("three_column_recommendations", [])
    
    if two_col:
        top_combo = two_col[0]
        recommendations.append(
            f" Composite Key Option (2 columns): [{', '.join(top_combo['columns'])}] "
            f"({top_combo['uniqueness_percentage']:.1f}% unique) - {top_combo['business_meaning']}"
        )
    
    if three_col:
        top_combo = three_col[0]
        recommendations.append(
            f" Composite Key Option (3 columns): [{', '.join(top_combo['columns'])}] "
            f"({top_combo['uniqueness_percentage']:.1f}% unique) - {top_combo['business_meaning']}"
        )
    
    # Data quality issues
    for column_name, analysis in column_analysis.items():
        null_pct = analysis.get("null_percentage", 0)
        if null_pct > 30:
            recommendations.append(
                f" Data Quality: Column '{column_name}' has high null percentage ({null_pct:.1f}%) "
                f"- investigate data collection"
            )
        if blank_pct > 30:
            recommendations.append(
                f" Data Quality: Column '{column_name}' has high blank percentage ({blank_pct:.1f}%) "
                f"- data collected but empty"
            )
    
    # Default value warnings
    if default_value_analysis:
        for col, info in default_value_analysis.items():
            default_pct = info.get("default_pct", 0)
            if default_pct > 80:
                recommendations.append(
                    f" Default Value: Column '{col}' has {default_pct:.1f}% of values as '{info.get('default_value')}' "
                    f"- check for data quality issues"
                )
    
    return recommendations
