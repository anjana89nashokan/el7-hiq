# utils/data_anomaly_functions.py
"""
Comprehensive data anomaly detection for BSA data quality analysis
Detects format inconsistencies, pattern deviations, statistical outliers, and data quality issues
"""
import os
import time
import json
import re
from typing import Dict, Any, List, Tuple, Optional
from decimal import Decimal
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.adk.tools import ToolContext
from dotenv import load_dotenv
from config.settings import config

load_dotenv()

try:
    from utils import local_warehouse as bigquery
    from utils.local_warehouse import QueryJobConfig
    GCP_AVAILABLE = True
except ImportError:
    GCP_AVAILABLE = False

# Configuration for anomaly detection
class AnomalyConfig:
    """Configuration for anomaly detection parameters"""
    def __init__(self):
        # Sampling configuration
        self.max_sample_size = 10000
        self.enable_sampling = True
        
        # Anomaly thresholds
        self.outlier_percentile_threshold = 1  # 1st and 99th percentiles
        self.pattern_anomaly_threshold = 5.0   # Flag patterns < 5% frequency
        self.statistical_z_threshold = 3.0     # Z-score threshold for outliers
        
        # Performance settings - reduced for VDI compatibility and connection pool limits
        # BigQuery default connection pool size is 10, so keep max_workers conservative
        self.max_workers = min(4, (os.cpu_count() or 1))  # Reduced from 16 to 4
        self.query_timeout_seconds = 120
        
        # Detection categories
        self.detect_format_inconsistencies = True
        self.detect_statistical_outliers = True
        self.detect_pattern_deviations = True
        self.detect_placeholder_values = True
        self.detect_length_anomalies = True
        self.detect_case_inconsistencies = True

def _normalize_table_refs(table_references: Any) -> List[str]:
    """
    Support both comma-separated strings and iterables.
    Handles strings, lists, tuples, sets, and nested collections.
    """
    if not table_references:
        return []

    if isinstance(table_references, str):
        return [tbl.strip() for tbl in table_references.split(",") if tbl.strip()]

    if isinstance(table_references, (list, tuple, set)):
        tables: List[str] = []
        for item in table_references:
            if item:
                tables.extend(_normalize_table_refs(item) if isinstance(item, (list, tuple, set)) else [str(item).strip()])
        return [tbl for tbl in tables if tbl]

    return [str(table_references).strip()]

def data_anomaly_analysis_tool(table_references: str, anomaly_sensitivity: str = "medium",
                              tool_context: ToolContext = None) -> Dict[str, Any]:
    """
    Comprehensive data anomaly detection across all columns and tables
    
    Args:
        table_references (str): Comma-separated list of table references
        anomaly_sensitivity (str): "low", "medium", or "high" sensitivity
        tool_context (ToolContext): ADK tool context
        
    Returns:
        dict: Comprehensive anomaly analysis with BSA-friendly interpretations
    """
    print(f"=== DATA ANOMALY ANALYSIS STARTED ===")
    print(f"Table references: {table_references}")
    print(f"Sensitivity level: {anomaly_sensitivity}")
    
    try:
        from utils.anomaly_table_resolver import (
            normalize_table_refs,
            resolve_anomaly_table_references,
        )

        raw_tables = normalize_table_refs(table_references)
        tables = resolve_anomaly_table_references(raw_tables, tool_context)
        if not tables:
            return {
                "status": "error",
                "error_message": (
                    "No resolvable source tables for anomaly analysis. "
                    "Complete Step 1 profiling first and use uploaded data tables, "
                    "not IBC profiling template names."
                ),
                "table_references": table_references,
                "requested_tables": raw_tables,
            }

        if raw_tables != tables:
            print(f"Anomaly table references remapped: {raw_tables} -> {tables}")

        # Check if we should use mock mode
        if any("mock" in table.lower() for table in tables) or not GCP_AVAILABLE:
            return _generate_mock_anomaly_results(tables, anomaly_sensitivity)
        
        # Real BigQuery anomaly analysis
        try:
            client = _get_bigquery_client()
            return _perform_comprehensive_anomaly_analysis(client, tables, anomaly_sensitivity)
            
        except Exception as e:
            print(f"BigQuery anomaly analysis failed, using mock: {e}")
            return _generate_mock_anomaly_results(tables, anomaly_sensitivity)
            
    except Exception as e:
        return {
            "status": "error",
            "error_message": str(e),
            "table_references": table_references
        }

def _get_bigquery_client():
    """Initialize BigQuery client"""
    project_id = getattr(config, 'BQ_PROJECT_ID', 'ihg-vertex-ai-poc')
    return bigquery.Client(project=project_id)

def _perform_comprehensive_anomaly_analysis(client, tables: List[str], sensitivity: str) -> Dict[str, Any]:
    """Perform comprehensive anomaly analysis across all tables"""
    
    start_time = time.time()
    
    # Configure sensitivity
    config = _get_sensitivity_config(sensitivity)
    
    results = {
        "status": "success",
        "analysis_timestamp": int(time.time()),
        "sensitivity_level": sensitivity,
        "tables_analyzed": len(tables),
        "table_anomaly_reports": {}, 
        "summary_statistics": {}, 
        "processing_stats": {}, 
        "processing_mode": "bigquery_comprehensive"
    }
    
    print("=== Phase 1: Parallel Table Anomaly Detection ===")
    
    # Parallel anomaly detection across all tables
    table_anomaly_results = {}
    
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_table = {
            executor.submit(_analyze_table_anomalies, client, table, config): table 
            for table in tables
        }
        
        for future in as_completed(future_to_table):
            table = future_to_table[future]
            try:
                anomaly_report = future.result(timeout=config.query_timeout_seconds)
                table_name = _extract_table_name(table)
                table_anomaly_results[table_name] = anomaly_report
                print(f"✓ Anomaly analysis complete for {table_name}")
            except Exception as e:
                print(f"✗ Anomaly analysis failed for {table}: {e}")
                table_anomaly_results[_extract_table_name(table)] = {
                    "status": "error", 
                    "error_message": str(e)
                }
    
    results["table_anomaly_reports"] = table_anomaly_results
    
    print("=== Phase 2: Summary and Recommendations ===")
    
    # Generate summary statistics and recommendations
    summary_stats = _generate_anomaly_summary(table_anomaly_results)
    results["summary_statistics"] = summary_stats
    
    # Processing statistics
    processing_time = time.time() - start_time
    results["processing_stats"] = {
        "total_processing_time": round(processing_time, 2),
        "tables_processed": len([r for r in table_anomaly_results.values() if r.get("status") != "error"]),
        "total_anomalies_detected": summary_stats.get("total_anomalies", 0),
        "anomaly_categories_detected": len(summary_stats.get("anomaly_categories", {}))
    }
    
    # Trim results to stay within LLM token budget before returning
    results = _trim_for_token_budget(results)

    # Clean results for serialization
    results = _make_json_serializable(results)

    print(f"Comprehensive anomaly analysis completed in {processing_time:.2f} seconds")

    return results

def _get_sensitivity_config(sensitivity: str) -> AnomalyConfig:
    """Configure anomaly detection based on sensitivity level"""
    config = AnomalyConfig()
    
    if sensitivity == "high":
        config.pattern_anomaly_threshold = 2.0  # Flag patterns < 2%
        config.statistical_z_threshold = 2.5    # More sensitive outlier detection
        config.outlier_percentile_threshold = 2  # 2nd and 98th percentiles
    elif sensitivity == "low":
        config.pattern_anomaly_threshold = 10.0 # Flag patterns < 10%
        config.statistical_z_threshold = 3.5    # Less sensitive outlier detection
        config.outlier_percentile_threshold = 0.5 # 0.5th and 99.5th percentiles
    # Medium uses default values
    
    return config

def _analyze_table_anomalies(client, table_ref: str, config: AnomalyConfig) -> Dict[str, Any]:
    """Comprehensive anomaly analysis for a single table"""
    
    table_report = {
        "table_reference": table_ref,
        "table_name": _extract_table_name(table_ref),
        "column_anomalies": {}, 
        "table_level_anomalies": [], 
        "anomaly_summary": {}, 
        "total_anomalies_found": 0
    }
    
    try:
        # Get table schema and basic info
        table = client.get_table(table_ref)
        total_rows = table.num_rows
        
        print(f"Analyzing {len(table.schema)} columns in {table_report['table_name']} ({total_rows:,} rows)")
        
        # Determine if sampling is needed
        use_sampling = total_rows > config.max_sample_size and config.enable_sampling
        sample_size = min(config.max_sample_size, total_rows) if use_sampling else total_rows
        sample_clause = f"TABLESAMPLE SYSTEM ({(sample_size / total_rows) * 100:.2f} PERCENT)" if use_sampling else ""

        # Detect large text / blob columns — skip pattern analysis for these
        blob_columns = _detect_blob_columns(client, table_ref, table.schema, sample_clause)

        # Analyze each column for anomalies
        # Reduced max_workers to prevent connection pool exhaustion (was 10, now 3)
        column_futures = {}
        with ThreadPoolExecutor(max_workers=min(3, len(table.schema))) as executor:
            for field in table.schema:
                # Skip RECORD, REPEATED (nested), and BYTES (binary) types
                if field.field_type not in ["RECORD", "REPEATED", "BYTES"]:
                    future = executor.submit(
                        _analyze_column_anomalies,
                        client, table_ref, field.name, field.field_type,
                        total_rows, use_sampling, sample_size, config, blob_columns
                    )
                    column_futures[field.name] = future
            
            # Collect column anomaly results
            for col_name, future in column_futures.items():
                try:
                    column_anomalies = future.result()
                    if column_anomalies:
                        table_report["column_anomalies"][col_name] = column_anomalies
                        table_report["total_anomalies_found"] += len(column_anomalies)
                except Exception as e:
                    print(f"Error analyzing column {col_name}: {e}")
        
        # Table-level anomaly detection
        table_level_anomalies = _detect_table_level_anomalies(client, table_ref, table, config)
        table_report["table_level_anomalies"] = table_level_anomalies
        
        # Generate anomaly summary for this table
        table_report["anomaly_summary"] = _summarize_table_anomalies(table_report)
        
        return table_report
        
    except Exception as e:
        print(f"Error analyzing table {table_ref}: {e}")
        return {"status": "error", "error_message": str(e), "table_reference": table_ref}

def _analyze_column_anomalies(client, table_ref: str, column_name: str, data_type: str,
                            total_rows: int, use_sampling: bool, sample_size: int,
                            config: AnomalyConfig, blob_columns: set = None) -> List[Dict[str, Any]]:
    """Comprehensive anomaly detection for a single column"""

    anomalies = []

    # Build sampling clause
    sample_clause = ""
    if use_sampling:
        sample_rate = (sample_size / total_rows) * 100
        sample_clause = f"TABLESAMPLE SYSTEM ({sample_rate:.2f} PERCENT)"

    # Skip large text / blob columns for all string-based pattern detection
    is_blob = blob_columns and column_name in blob_columns

    try:
        # 1. Enhanced Format/Pattern Inconsistency Detection
        if config.detect_format_inconsistencies and not is_blob:
            format_anomalies = _detect_format_inconsistencies(
                client, table_ref, column_name, data_type, sample_clause, config
            )
            anomalies.extend(format_anomalies)
        
        # 2. Statistical Outlier Detection (numeric columns)
        if config.detect_statistical_outliers and data_type in ["INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC"]:
            outlier_anomalies = _detect_statistical_outliers(
                client, table_ref, column_name, sample_clause, config
            )
            anomalies.extend(outlier_anomalies)
        
        # 3. Pattern Deviation Detection (all columns — skip blobs)
        if config.detect_pattern_deviations and not is_blob:
            pattern_anomalies = _detect_pattern_deviations(
                client, table_ref, column_name, data_type, sample_clause, config
            )
            anomalies.extend(pattern_anomalies)

        # 4. Placeholder and Special Value Detection (run even for blobs — just counts)
        if config.detect_placeholder_values:
            placeholder_anomalies = _detect_placeholder_values(
                client, table_ref, column_name, data_type, sample_clause, config
            )
            anomalies.extend(placeholder_anomalies)

        # 5. Length Anomaly Detection (skip blobs — all values are long by design)
        if config.detect_length_anomalies and data_type in ["STRING", "TEXT"] and not is_blob:
            length_anomalies = _detect_length_anomalies(
                client, table_ref, column_name, sample_clause, config
            )
            anomalies.extend(length_anomalies)

        # 6. Case Consistency Detection (skip blobs)
        if config.detect_case_inconsistencies and data_type in ["STRING", "TEXT"] and not is_blob:
            case_anomalies = _detect_case_inconsistencies(
                client, table_ref, column_name, sample_clause, config
            )
            anomalies.extend(case_anomalies)
        
        return _merge_duplicate_anomaly_rows(anomalies)
        
    except Exception as e:
        print(f"Error in column anomaly detection for {column_name}: {e}")
        return [{
            "anomaly_type": "analysis_error",
            "issue": f"Failed to analyze column: {str(e)}",
            "severity": "high",
            "examples": []
        }]

def _detect_format_inconsistencies(client, table_ref: str, column_name: str, data_type: str,
                                 sample_clause: str, config: AnomalyConfig) -> List[Dict[str, Any]]:
    """Detect format inconsistencies using intelligent pattern recognition"""

    anomalies = []

    if data_type in ["STRING", "TEXT"]:
        query = f"""
            WITH value_patterns AS (
                SELECT
                    `{column_name}` as value,
                    LENGTH(`{column_name}`) as value_length,
                    REGEXP_CONTAINS(`{column_name}`, r'^[0-9]+$') as is_numeric,
                    REGEXP_CONTAINS(`{column_name}`, r'^[A-Za-z]+$') as is_alphabetic,
                    REGEXP_CONTAINS(`{column_name}`, r'^[A-Z]+$') as is_uppercase,
                    REGEXP_CONTAINS(`{column_name}`, r'^[a-z]+$') as is_lowercase,
                    REGEXP_CONTAINS(`{column_name}`, r'-') as has_dash,
                    REGEXP_CONTAINS(`{column_name}`, r'_') as has_underscore,
                    REGEXP_CONTAINS(`{column_name}`, r'^\d{{4}}-\d{{2}}-\d{{2}}$') as is_iso_date,
                    REGEXP_CONTAINS(`{column_name}`, r'^\d{{2}}/\d{{2}}/\d{{4}}$') as is_us_date,
                    REGEXP_CONTAINS(`{column_name}`, r'^\d{{2}}-\d{{2}}-\d{{4}}$') as is_eu_date,
                    REGEXP_CONTAINS(`{column_name}`, r'^[A-Z]{{2,4}}\d+$') as is_code_pattern
                FROM `{table_ref}` {sample_clause}
                WHERE `{column_name}` IS NOT NULL
                  AND TRIM(CAST(`{column_name}` AS STRING)) NOT IN ('', '""', "''")
            ),
            pattern_counts AS (
                SELECT
                    value_length, is_numeric, is_alphabetic, is_uppercase, is_lowercase,
                    has_dash, has_underscore, is_iso_date, is_us_date, is_eu_date, is_code_pattern,
                    COUNT(*) as pattern_count,
                    ARRAY_AGG(DISTINCT SUBSTR(CAST(value AS STRING), 1, 100) LIMIT 5) as example_values
                FROM value_patterns
                GROUP BY value_length, is_numeric, is_alphabetic, is_uppercase, is_lowercase,
                         has_dash, has_underscore, is_iso_date, is_us_date, is_eu_date, is_code_pattern
            ),
            total_records AS (
                SELECT SUM(pattern_count) as total_count FROM pattern_counts
            ),
            dominant_pattern AS (
                SELECT * FROM pattern_counts ORDER BY pattern_count DESC LIMIT 1
            )
            SELECT
                pc.*,
                (pc.pattern_count * 100.0 / tr.total_count) as pattern_percentage,
                tr.total_count,
                dp.value_length as expected_value_length,
                dp.is_numeric as expected_is_numeric,
                dp.is_alphabetic as expected_is_alphabetic,
                dp.is_uppercase as expected_is_uppercase,
                dp.is_lowercase as expected_is_lowercase,
                dp.has_dash as expected_has_dash,
                dp.has_underscore as expected_has_underscore,
                dp.is_iso_date as expected_is_iso_date,
                dp.is_us_date as expected_is_us_date,
                dp.is_eu_date as expected_is_eu_date,
                dp.is_code_pattern as expected_is_code_pattern,
                dp.example_values as dominant_example_values,
                (dp.pattern_count * 100.0 / tr.total_count) as expected_pattern_percentage
            FROM pattern_counts pc, total_records tr, dominant_pattern dp
            WHERE (pc.pattern_count * 100.0 / tr.total_count) < {config.pattern_anomaly_threshold}
            ORDER BY pattern_percentage ASC
            LIMIT 5
        """

        try:
            results = list(client.query(query).result())

            for row in results:
                observed_desc = _build_pattern_description(row)
                expected_desc = _build_pattern_description(row, prefix="expected_")
                affected_pct = round(row.pattern_percentage, 2)
                total_count = int(row.total_count)
                anomaly_examples = _fmt_examples(row.example_values)
                dominant_examples = _fmt_examples(row.dominant_example_values)

                explanation = (
                    f"Most values follow a {expected_desc} pattern "
                    f"({row.expected_pattern_percentage:.1f}% of records, e.g. {_examples_str(dominant_examples)}). "
                    f"{row.pattern_count} records ({affected_pct}%) differ with a {observed_desc} — "
                    f"e.g. {_examples_str(anomaly_examples)}."
                )

                anomalies.append({
                    "anomaly_type": "format_inconsistency",
                    "issue": explanation,
                    "severity": "medium" if row.pattern_percentage > 1.0 else "high",
                    "affected_count": row.pattern_count,
                    "affected_percentage": affected_pct,
                    "total_records_evaluated": total_count,
                    "human_readable_explanation": explanation,
                    "expected_pattern": expected_desc,
                    "observed_pattern": observed_desc,
                    "dominant_examples": dominant_examples,
                    "examples": anomaly_examples,
                    "pattern_percentage": affected_pct,
                    "pattern_details": {
                        "length": row.value_length,
                        "is_numeric": row.is_numeric,
                        "is_alphabetic": row.is_alphabetic,
                        "has_separators": any([row.has_dash, row.has_underscore]),
                        "case_pattern": "uppercase" if row.is_uppercase else "lowercase" if row.is_lowercase else "mixed"
                    }
                })

        except Exception as e:
            print(f"Format inconsistency detection failed for {column_name}: {e}")

    return anomalies

def _detect_statistical_outliers(client, table_ref: str, column_name: str, sample_clause: str, 
                                config: AnomalyConfig) -> List[Dict[str, Any]]:
    """Enhanced statistical outlier detection"""
    
    anomalies = []
    
    # Multi-method outlier detection
    query = f"""
    WITH stats AS (
        SELECT
            COUNT(*) as total_count,
            AVG(`{column_name}`) as mean_val,
            STDDEV(`{column_name}`) as stddev_val,
            MIN(`{column_name}`) as min_val,
            MAX(`{column_name}`) as max_val,
            APPROX_QUANTILES(`{column_name}`, 100)[OFFSET({config.outlier_percentile_threshold})] as p_low,
            APPROX_QUANTILES(`{column_name}`, 100)[OFFSET({100 - config.outlier_percentile_threshold})] as p_high,
            APPROX_QUANTILES(`{column_name}`, 4)[OFFSET(1)] as q1,
            APPROX_QUANTILES(`{column_name}`, 4)[OFFSET(3)] as q3
        FROM `{table_ref}` {sample_clause}
        WHERE `{column_name}` IS NOT NULL
    ),
    outlier_analysis AS (
        SELECT
            `{column_name}` as value,
            stats.total_count,
            stats.mean_val,
            stats.stddev_val,
            stats.p_low,
            stats.p_high,
            stats.q1,
            stats.q3,
            ABS(`{column_name}` - stats.mean_val) / NULLIF(stats.stddev_val, 0) as z_score,
            stats.q3 - stats.q1 as iqr,
            CASE
                WHEN `{column_name}` < stats.p_low THEN 'low_percentile'
                WHEN `{column_name}` > stats.p_high THEN 'high_percentile'
                ELSE 'normal'
            END as percentile_category
        FROM `{table_ref}` {sample_clause}, stats
        WHERE `{column_name}` IS NOT NULL
    ),
    detected_outliers AS (
        SELECT
            value,
            total_count,
            z_score,
            percentile_category,
            CASE
                WHEN z_score > {config.statistical_z_threshold} THEN 'z_score_outlier'
                WHEN value < q1 - 1.5 * iqr OR value > q3 + 1.5 * iqr THEN 'iqr_outlier'
                WHEN percentile_category != 'normal' THEN 'percentile_outlier'
                ELSE 'normal'
            END as outlier_type
        FROM outlier_analysis
        WHERE z_score > {config.statistical_z_threshold}
           OR value < q1 - 1.5 * iqr
           OR value > q3 + 1.5 * iqr
           OR percentile_category != 'normal'
    )
    SELECT
        outlier_type,
        COUNT(*) as outlier_count,
        MAX(total_count) as total_count,
        ARRAY_AGG(DISTINCT value LIMIT 10) as example_values,
        AVG(z_score) as avg_z_score
    FROM detected_outliers
    WHERE outlier_type != 'normal'
    GROUP BY outlier_type
    """

    try:
        results = list(client.query(query).result())

        for row in results:
            severity = "high" if row.avg_z_score > 4.0 else "medium"
            total_count = int(row.total_count or 0)
            affected_pct = _safe_percentage(row.outlier_count, total_count)
            examples = _fmt_examples(row.example_values)
            explanation = (
                f"Most values in this column are within the expected numeric range. "
                f"{row.outlier_count} records ({affected_pct}%) are statistical outliers "
                f"(detected by {row.outlier_type.replace('_', ' ')}). "
                f"Example outlier values: {_examples_str(examples)}."
            )

            anomalies.append({
                "anomaly_type": "statistical_outlier",
                "issue": explanation,
                "severity": severity,
                "affected_count": row.outlier_count,
                "affected_percentage": affected_pct,
                "total_records_evaluated": total_count,
                "human_readable_explanation": explanation,
                "outlier_count": row.outlier_count,
                "avg_z_score": round(row.avg_z_score, 2) if row.avg_z_score else None,
                "examples": examples,
                "detection_method": row.outlier_type
            })
            
    except Exception as e:
        print(f"Statistical outlier detection failed for {column_name}: {e}")
    
    return anomalies

def _detect_pattern_deviations(client, table_ref: str, column_name: str, data_type: str, sample_clause: str, config: AnomalyConfig) -> List[Dict[str, Any]]:
    """Generic pattern deviation detection for any data type"""

    anomalies = []

    query = f"""
        WITH pattern_analysis AS (
            SELECT
                `{column_name}` as original_value,
                REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        REGEXP_REPLACE(UPPER(CAST(`{column_name}` AS STRING)),
                                    r'[0-9]', 'N'),
                        r'[A-Z]', 'A'),
                    r'[^AN\-_\.\s]', 'X'
                ) as pattern_signature,
                LENGTH(CAST(`{column_name}` AS STRING)) as value_length
            FROM `{table_ref}` {sample_clause}
            WHERE `{column_name}` IS NOT NULL
              AND TRIM(CAST(`{column_name}` AS STRING)) NOT IN ('', '""', "''")
        ),
        pattern_counts AS (
            SELECT
                pattern_signature,
                value_length,
                COUNT(*) as pattern_count,
                ARRAY_AGG(DISTINCT SUBSTR(CAST(original_value AS STRING), 1, 100) LIMIT 5) as example_values
            FROM pattern_analysis
            GROUP BY pattern_signature, value_length
        ),
        total_records AS (
            SELECT SUM(pattern_count) as total_count FROM pattern_counts
        ),
        dominant_pattern AS (
            SELECT * FROM pattern_counts ORDER BY pattern_count DESC LIMIT 1
        )
        SELECT
            pc.*,
            (pc.pattern_count * 100.0 / tr.total_count) as pattern_percentage,
            tr.total_count,
            dp.pattern_signature as expected_pattern_signature,
            dp.value_length as expected_value_length,
            dp.example_values as dominant_example_values,
            (dp.pattern_count * 100.0 / tr.total_count) as expected_pattern_percentage
        FROM pattern_counts pc, total_records tr, dominant_pattern dp
        WHERE (pc.pattern_count * 100.0 / tr.total_count) < {config.pattern_anomaly_threshold}
        ORDER BY pattern_percentage ASC
        LIMIT 5
    """

    try:
        results = list(client.query(query).result())

        for row in results:
            observed_desc = _describe_pattern_signature(row.pattern_signature, row.value_length)
            expected_desc = _describe_pattern_signature(row.expected_pattern_signature, row.expected_value_length)
            affected_pct = round(row.pattern_percentage, 2)
            total_count = int(row.total_count)
            anomaly_examples = _fmt_examples(row.example_values)
            dominant_examples = _fmt_examples(row.dominant_example_values)

            explanation = (
                f"Most values follow a {expected_desc} pattern "
                f"({row.expected_pattern_percentage:.1f}% of records, e.g. {_examples_str(dominant_examples)}). "
                f"{row.pattern_count} records ({affected_pct}%) differ — "
                f"e.g. {_examples_str(anomaly_examples)}."
            )

            anomalies.append({
                "anomaly_type": "pattern_deviation",
                "issue": explanation,
                "severity": "medium" if row.pattern_percentage > 2.0 else "high",
                "affected_count": row.pattern_count,
                "affected_percentage": affected_pct,
                "total_records_evaluated": total_count,
                "human_readable_explanation": explanation,
                "expected_pattern": expected_desc,
                "observed_pattern": observed_desc,
                "dominant_examples": dominant_examples,
                "pattern_signature": row.pattern_signature,
                "pattern_percentage": affected_pct,
                "value_length": row.value_length,
                "examples": anomaly_examples,
                "interpretation": explanation
            })

    except Exception as e:
        print(f"Pattern deviation detection failed for {column_name}: {e}")

    return anomalies

def _detect_placeholder_values(client, table_ref: str, column_name: str, data_type: str, 
                             sample_clause: str, config: AnomalyConfig) -> List[Dict[str, Any]]:
    """Enhanced placeholder and suspicious value detection"""
    
    anomalies = []
    
    # Build appropriate placeholder conditions based on data type
    if data_type in ["STRING", "TEXT"]:
        placeholder_conditions = f"""
            `{column_name}` IN ('N/A', 'NA', 'NULL', 'null', 'UNKNOWN', 'unknown', 
                               'TBD', 'TBA', 'PENDING', 'MISSING', 'BLANK', '', 
                               '~', '#N/A', '#NULL!', '#VALUE!', '#REF!', 
                               'XXXXXXXXXX')
            OR TRIM(`{column_name}`) = ''
            OR REGEXP_CONTAINS(`{column_name}`, r'^[X]+$')
            OR REGEXP_CONTAINS(`{column_name}`, r'^[0]+$')
            OR REGEXP_CONTAINS(`{column_name}`, r'^[9]+$')
        """
    elif data_type in ["INT64", "INTEGER", "FLOAT64", "NUMERIC", "BIGNUMERIC"]:
        placeholder_conditions = f"`{column_name}` IN (-1, 0, 999, 9999, 99999, 999999999)"
    elif data_type == "BOOLEAN":
        # Skip boolean columns for placeholder detection
        return anomalies
    else:
        # For other data types, just check for common string placeholders if castable
        placeholder_conditions = f"CAST(`{column_name}` AS STRING) IN ('NULL', 'N/A', '', '0', '-1')"
    
    query = f"""
    WITH placeholder_analysis AS (
        SELECT 
            `{column_name}` as placeholder_value, 
            COUNT(*) as occurrence_count
        FROM `{table_ref}` {sample_clause}
        WHERE {placeholder_conditions}
        GROUP BY `{column_name}`
    ),
    total_records AS (
        SELECT COUNT(*) as total_count
        FROM `{table_ref}` {sample_clause}
        WHERE `{column_name}` IS NOT NULL
    )
    SELECT 
        pa.placeholder_value, 
        pa.occurrence_count, 
        (pa.occurrence_count * 100.0 / tr.total_count) as percentage, 
        tr.total_count
    FROM placeholder_analysis pa, total_records tr
    WHERE (pa.occurrence_count * 100.0 / tr.total_count) > 1.0
    ORDER BY percentage DESC
    """
    
    try:
        results = list(client.query(query).result())
        
        if results:
            total_placeholders = sum(row.occurrence_count for row in results)
            total_count = int(results[0].total_count) if results else 0
            affected_pct = _safe_percentage(total_placeholders, total_count)
            placeholder_examples = [str(row.placeholder_value) for row in results[:3]]
            explanation = (
                f"Most values in this column contain meaningful data. "
                f"{total_placeholders} records ({affected_pct}%) contain placeholder or suspicious values "
                f"such as {', '.join(repr(ex) for ex in placeholder_examples)}. "
                f"These appear to be data entry placeholders rather than valid business values."
            )

            anomalies.append({
                "anomaly_type": "placeholder_values",
                "issue": explanation,
                "severity": "high" if any(row.percentage > 10 for row in results) else "medium",
                "affected_count": total_placeholders,
                "affected_percentage": affected_pct,
                "total_records_evaluated": total_count,
                "human_readable_explanation": explanation,
                "total_placeholder_count": total_placeholders,
                "placeholder_percentage": affected_pct,
                "examples": placeholder_examples,
                "details": [
                    {
                        "value": str(row.placeholder_value),
                        "count": row.occurrence_count,
                        "percentage": round(row.percentage, 2)
                    } for row in results
                ]
            })
            
    except Exception as e:
        print(f"Placeholder detection failed for {column_name}: {e}")
    
    return anomalies

def _detect_length_anomalies(client, table_ref: str, column_name: str, sample_clause: str,
                           config: AnomalyConfig) -> List[Dict[str, Any]]:
    """Detect unusual length patterns in string columns"""

    anomalies = []

    query = f"""
        WITH length_counts AS (
            SELECT
                LENGTH(`{column_name}`) as value_length,
                COUNT(*) as length_count,
                ARRAY_AGG(DISTINCT SUBSTR(CAST(`{column_name}` AS STRING), 1, 100) LIMIT 5) as examples
            FROM `{table_ref}` {sample_clause}
            WHERE `{column_name}` IS NOT NULL
              AND TRIM(CAST(`{column_name}` AS STRING)) NOT IN ('', '""', "''")
            GROUP BY LENGTH(`{column_name}`)
        ),
        length_stats AS (
            SELECT
                AVG(value_length) as avg_length,
                STDDEV(value_length) as stddev_length
            FROM length_counts
        ),
        total_records AS (
            SELECT SUM(length_count) as total_count FROM length_counts
        ),
        dominant_length AS (
            SELECT * FROM length_counts ORDER BY length_count DESC LIMIT 1
        )
        SELECT
            lc.*,
            ls.avg_length,
            ls.stddev_length,
            (lc.length_count * 100.0 / tr.total_count) as length_percentage,
            ABS(lc.value_length - ls.avg_length) / NULLIF(ls.stddev_length, 0) as length_z_score,
            tr.total_count,
            dl.value_length as expected_length,
            dl.examples as dominant_examples,
            (dl.length_count * 100.0 / tr.total_count) as expected_length_percentage
        FROM length_counts lc, length_stats ls, total_records tr, dominant_length dl
        WHERE lc.value_length != dl.value_length
          AND (
              (lc.length_count * 100.0 / tr.total_count) < {config.pattern_anomaly_threshold}
              OR ABS(lc.value_length - ls.avg_length) / NULLIF(ls.stddev_length, 0) > 2
          )
        ORDER BY length_percentage ASC
    """

    try:
        results = list(client.query(query).result())

        for row in results:
            if row.length_z_score and row.length_z_score > 2:
                affected_pct = round(row.length_percentage, 2)
                total_count = int(row.total_count)
                anomaly_examples = _fmt_examples(row.examples)
                dominant_examples = _fmt_examples(row.dominant_examples)

                explanation = (
                    f"Most values are {row.expected_length} characters long "
                    f"({row.expected_length_percentage:.1f}% of records, e.g. {_examples_str(dominant_examples)}). "
                    f"{row.length_count} records ({affected_pct}%) have {row.value_length}-character values — "
                    f"e.g. {_examples_str(anomaly_examples)}."
                )

                anomalies.append({
                    "anomaly_type": "length_anomaly",
                    "issue": explanation,
                    "severity": "medium" if row.length_percentage > 1.0 else "high",
                    "affected_count": row.length_count,
                    "affected_percentage": affected_pct,
                    "total_records_evaluated": total_count,
                    "human_readable_explanation": explanation,
                    "expected_pattern": f"{row.expected_length}-character value",
                    "observed_pattern": f"{row.value_length}-character value",
                    "dominant_examples": dominant_examples,
                    "unusual_length": row.value_length,
                    "expected_length": row.expected_length,
                    "length_percentage": affected_pct,
                    "length_z_score": round(row.length_z_score, 2),
                    "examples": anomaly_examples,
                    "interpretation": explanation
                })

    except Exception as e:
        print(f"Length anomaly detection failed for {column_name}: {e}")

    return anomalies

def _detect_case_inconsistencies(client, table_ref: str, column_name: str, sample_clause: str,
                               config: AnomalyConfig) -> List[Dict[str, Any]]:
    """Detect case inconsistencies in string columns"""

    anomalies = []

    query = f"""
    WITH case_analysis AS (
        SELECT
            `{column_name}` as original_value,
            CASE
                WHEN `{column_name}` = UPPER(`{column_name}`) THEN 'uppercase'
                WHEN `{column_name}` = LOWER(`{column_name}`) THEN 'lowercase'
                WHEN `{column_name}` = INITCAP(`{column_name}`) THEN 'titlecase'
                ELSE 'mixed'
            END as case_pattern
        FROM `{table_ref}` {sample_clause}
        WHERE `{column_name}` IS NOT NULL
          AND REGEXP_CONTAINS(`{column_name}`, r'[A-Za-z]')
    ),
    case_counts AS (
        SELECT
            case_pattern,
            COUNT(*) as pattern_count,
            ARRAY_AGG(DISTINCT SUBSTR(original_value, 1, 100) LIMIT 5) as examples
        FROM case_analysis
        GROUP BY case_pattern
    ),
    total_records AS (
        SELECT SUM(pattern_count) as total_count FROM case_counts
    ),
    dominant_case AS (
        SELECT * FROM case_counts ORDER BY pattern_count DESC LIMIT 1
    )
    SELECT
        cc.*,
        (cc.pattern_count * 100.0 / tr.total_count) as pattern_percentage,
        tr.total_count,
        dc.case_pattern as expected_case_pattern,
        dc.examples as dominant_examples,
        (dc.pattern_count * 100.0 / tr.total_count) as expected_case_percentage
    FROM case_counts cc, total_records tr, dominant_case dc
    ORDER BY pattern_percentage DESC
    """

    try:
        results = list(client.query(query).result())

        if len(results) > 1:
            expected_case = results[0].expected_case_pattern
            minority_patterns = [row for row in results if row.case_pattern != expected_case]

            if minority_patterns:
                total_count = int(results[0].total_count)
                total_inconsistent = sum(row.pattern_count for row in minority_patterns)
                affected_pct = _safe_percentage(total_inconsistent, total_count)
                expected_pct = round(results[0].expected_case_percentage, 1)
                minority_labels = ", ".join(row.case_pattern for row in minority_patterns)

                dominant_examples = _fmt_examples(results[0].dominant_examples)
                anomaly_examples = []
                for pr in minority_patterns:
                    anomaly_examples.extend(_fmt_examples(pr.examples, 2))
                anomaly_examples = anomaly_examples[:3]

                explanation = (
                    f"Most values use {expected_case} casing "
                    f"({expected_pct}% of records, e.g. {_examples_str(dominant_examples)}). "
                    f"{total_inconsistent} records ({affected_pct}%) use {minority_labels} casing — "
                    f"e.g. {_examples_str(anomaly_examples)}."
                )

                anomalies.append({
                    "anomaly_type": "case_inconsistency",
                    "issue": explanation,
                    "severity": "low",
                    "affected_count": total_inconsistent,
                    "affected_percentage": affected_pct,
                    "total_records_evaluated": total_count,
                    "human_readable_explanation": explanation,
                    "expected_pattern": f"{expected_case} casing",
                    "observed_pattern": f"{minority_labels} casing",
                    "dominant_examples": dominant_examples,
                    "total_inconsistent_count": total_inconsistent,
                    "case_patterns": [
                        {
                            "pattern": row.case_pattern,
                            "count": row.pattern_count,
                            "percentage": round(row.pattern_percentage, 2),
                            "examples": _fmt_examples(row.examples)
                        } for row in results
                    ],
                    "recommendation": "Consider standardizing case format for consistency"
                })

    except Exception as e:
        print(f"Case consistency detection failed for {column_name}: {e}")

    return anomalies

def _detect_table_level_anomalies(client, table_ref: str, table, config: AnomalyConfig) -> List[Dict[str, Any]]:
    """Detect table-level anomalies (row count patterns, completeness, etc.)"""
    
    table_anomalies = []
    
    try:
        # Check for completely empty columns - use per-column null checks instead of UNPIVOT
        # to avoid data type mismatch issues with mixed column types (FLOAT64, BOOL, STRING, etc.)
        eligible_fields = [field for field in table.schema if field.field_type not in ['RECORD', 'REPEATED']]

        if eligible_fields:
            # Generate individual null percentage checks for each column
            null_checks = [
                f"COUNTIF(`{field.name}` IS NULL) * 100.0 / NULLIF(COUNT(*), 0) as `{field.name}_null_pct`"
                for field in eligible_fields
            ]

            empty_columns_query = f"""
            SELECT
                {', '.join(null_checks)}
            FROM `{table_ref}`
            """

            empty_results = list(client.query(empty_columns_query).result())

            if empty_results:
                # Parse the results to find columns with >95% null values
                row = empty_results[0]
                empty_columns = []

                for field in eligible_fields:
                    null_pct_key = f"{field.name}_null_pct"
                    if hasattr(row, null_pct_key):
                        null_percentage = getattr(row, null_pct_key)
                        if null_percentage is not None and null_percentage > 95:
                            empty_columns.append({
                                "column": field.name,
                                "null_percentage": round(null_percentage, 1),
                                "data_type": field.field_type
                            })

                if empty_columns:
                    table_anomalies.append({
                        "anomaly_type": "empty_columns",
                        "issue": "Columns with >95% null values detected",
                        "severity": "medium",
                        "affected_columns": empty_columns,
                        "recommendation": "Consider removing or investigating these nearly empty columns"
                    })
        
        # Check for duplicate rows - use CONCAT + MD5 hash for BigQuery compatibility
        # Avoids GROUP BY column reference issues with STRUCT
        if eligible_fields:
            # Build concatenated hash string - COALESCE handles NULLs
            concat_parts = [
                f"COALESCE(CAST(`{field.name}` AS STRING), 'NULL')"
                for field in eligible_fields
            ]
            concat_expr = " || '|' || ".join(concat_parts)

            duplicate_query = f"""
            WITH row_hash AS (
                SELECT
                    MD5(CONCAT({concat_expr})) as row_signature,
                    COUNT(*) as occurrence_count
                FROM `{table_ref}`
                GROUP BY row_signature
                HAVING COUNT(*) > 1
            )
            SELECT
                COUNT(*) as duplicate_signature_count,
                SUM(occurrence_count) as total_duplicate_rows
            FROM row_hash
            """

            duplicate_result = list(client.query(duplicate_query).result())
        else:
            duplicate_result = []
        
        if duplicate_result and duplicate_result[0].duplicate_signature_count > 0:
            dup_info = duplicate_result[0]
            table_anomalies.append({
                "anomaly_type": "duplicate_rows",
                "issue": "Duplicate rows detected",
                "severity": "high",
                "duplicate_signatures": dup_info.duplicate_signature_count, 
                "total_duplicate_rows": dup_info.total_duplicate_rows, 
                "duplication_percentage": round((dup_info.total_duplicate_rows / table.num_rows) * 100, 2),
                "recommendation": "Investigate and remove duplicate records"
            })
        
    except Exception as e:
        print(f"Table-level anomaly detection failed: {e}")
    
    return table_anomalies

def _safe_percentage(count: Any, total: Any) -> float:
    try:
        total_val = float(total or 0)
        if total_val == 0:
            return 0.0
        return round(float(count or 0) * 100.0 / total_val, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0

_MAX_EXAMPLE_CHARS = 100
_BLOB_AVG_LEN_THRESHOLD = 500  # columns with avg value length > 500 chars are large text/blob — skip pattern analysis

def _fmt_examples(values, limit: int = 3) -> List[str]:
    """Return up to `limit` example values, each capped at _MAX_EXAMPLE_CHARS chars."""
    if not values:
        return []
    result = []
    for v in list(values)[:limit]:
        if v is None:
            continue
        text = str(v)
        result.append(text[:_MAX_EXAMPLE_CHARS] + "..." if len(text) > _MAX_EXAMPLE_CHARS else text)
    return result

def _detect_blob_columns(client, table_ref: str, schema, sample_clause: str = "") -> set:
    """
    Run one query per table to find STRING columns with avg value length > threshold.
    These are large text / blob fields — pattern, format, length, and case analysis
    is meaningless for them and their example values would bloat the LLM token budget.
    Returns a set of column names to skip entirely.
    """
    string_fields = [f for f in schema if f.field_type == "STRING"][:40]
    if not string_fields:
        return set()
    parts = [f"AVG(LENGTH(`{f.name}`)) as col_avg_{i}" for i, f in enumerate(string_fields)]
    query = f"SELECT {', '.join(parts)} FROM `{table_ref}` {sample_clause}"
    try:
        results = list(client.query(query).result())
        if not results:
            return set()
        row = results[0]
        blob_cols = set()
        for i, f in enumerate(string_fields):
            avg_len = getattr(row, f"col_avg_{i}", None)
            if avg_len is not None and float(avg_len or 0) > _BLOB_AVG_LEN_THRESHOLD:
                blob_cols.add(f.name)
                print(f"  Skipping {f.name}: avg value length {avg_len:.0f} chars — large text/blob field")
        return blob_cols
    except Exception as e:
        print(f"Blob column detection failed for {table_ref}: {e}")
        return set()

def _examples_str(examples: List[str]) -> str:
    return ", ".join(f"'{v}'" for v in examples) if examples else "no examples available"

def _merge_duplicate_anomaly_rows(anomalies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge duplicate-looking anomaly rows emitted by bucketed SQL queries.
    BigQuery groups can differ internally while rendering to the same BSA-facing
    expected/observed pattern, so keep one row per anomaly type + display pattern.
    """
    grouped: Dict[Tuple[Any, Any, Any], Dict[str, Any]] = {}

    for anomaly in anomalies:
        key = (
            anomaly.get("anomaly_type"),
            anomaly.get("expected_pattern"),
            anomaly.get("observed_pattern"),
        )

        if key not in grouped:
            grouped[key] = anomaly
            continue

        existing = grouped[key]
        existing_count = int(existing.get("affected_count") or 0)
        new_count = int(anomaly.get("affected_count") or 0)
        total_count = existing.get("total_records_evaluated") or anomaly.get("total_records_evaluated")
        merged_count = existing_count + new_count

        existing["affected_count"] = merged_count
        if total_count:
            existing["affected_percentage"] = _safe_percentage(merged_count, total_count)
        else:
            existing["affected_percentage"] = round(
                float(existing.get("affected_percentage") or 0)
                + float(anomaly.get("affected_percentage") or 0),
                2,
            )

        for key_name in ("examples", "dominant_examples"):
            merged_values = list(dict.fromkeys(
                (existing.get(key_name) or []) + (anomaly.get(key_name) or [])
            ))
            existing[key_name] = merged_values[:5]

        explanation = (
            f"{merged_count} records ({existing['affected_percentage']}%) match this "
            f"{str(existing.get('anomaly_type', 'anomaly')).replace('_', ' ')} pattern. "
            f"Most values look like {existing.get('expected_pattern', 'the common pattern')}; "
            f"anomalous examples include {_examples_str(existing.get('examples') or [])}."
        )
        existing["human_readable_explanation"] = explanation
        existing["issue"] = explanation
        existing["interpretation"] = explanation

    return list(grouped.values())

def _build_pattern_description(row, prefix: str = "") -> str:
    """Return a plain-English description of a pattern using boolean flag columns."""
    def v(name: str) -> Any:
        return getattr(row, f"{prefix}{name}", None)
    length = v("value_length")
    if length is None:
        return "unknown pattern"
    length = int(length)
    if v("is_iso_date"):      return f"{length}-character ISO date (YYYY-MM-DD)"
    if v("is_us_date"):       return f"{length}-character US date (MM/DD/YYYY)"
    if v("is_eu_date"):       return f"{length}-character European date (DD-MM-YYYY)"
    if v("is_numeric"):       return f"{length}-digit numeric value"
    if v("is_code_pattern"):  return f"{length}-character alphanumeric code"
    if v("is_alphabetic") and v("is_uppercase"): return f"{length}-character uppercase text"
    if v("is_alphabetic") and v("is_lowercase"): return f"{length}-character lowercase text"
    if v("is_alphabetic"):    return f"{length}-character alphabetic value"
    if v("has_dash") and v("has_underscore"): return f"{length}-character value with dashes and underscores"
    if v("has_dash"):         return f"{length}-character value with dashes"
    if v("has_underscore"):   return f"{length}-character value with underscores"
    return f"{length}-character mixed value"

def _describe_pattern_signature(signature: Optional[str], length: Any = None) -> str:
    """Convert a compact pattern signature (AAA, NNN, etc.) to plain English."""
    if not signature:
        return "unknown pattern"
    sig = str(signature)
    has_alpha = "A" in sig
    has_num = "N" in sig
    has_dash = "-" in sig
    has_underscore = "_" in sig
    lp = f"{length}-character " if length is not None else ""
    if has_alpha and has_num and (has_dash or has_underscore):
        return f"{lp}alphanumeric value with separators"
    if has_alpha and has_num:
        return f"{lp}alphanumeric value"
    if has_alpha and has_dash:
        return f"{lp}alphabetic value with dashes"
    if has_alpha:
        return f"{lp}alphabetic value"
    if has_num and has_dash:
        return f"{lp}numeric value with dashes"
    if has_num:
        return f"{lp}numeric value"
    return f"{lp}mixed value"

def _trim_anomaly_dict(anomaly: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce per-anomaly token footprint: cap example arrays, drop redundant field."""
    for key in ("dominant_examples", "examples"):
        if isinstance(anomaly.get(key), list):
            anomaly[key] = anomaly[key][:2]
    # Remove `issue` — it duplicates `human_readable_explanation`
    anomaly.pop("issue", None)
    return anomaly

def _trim_for_token_budget(results: Dict[str, Any], max_anomalies_per_column: int = 3) -> Dict[str, Any]:
    """
    Cap the token size of the full results dict before it is returned to the LLM.
    Keeps the highest-severity anomalies per column and shrinks example arrays.
    Structure and keys are preserved so the UI and prompts are unaffected.
    """
    severity_order = {"high": 0, "medium": 1, "low": 2}
    table_reports = results.get("table_anomaly_reports", {}) or {}
    for report in table_reports.values():
        if not isinstance(report, dict):
            continue
        column_anomalies = report.get("column_anomalies", {}) or {}
        for col_name, anomaly_list in column_anomalies.items():
            if not isinstance(anomaly_list, list):
                continue
            # Sort high → medium → low, keep top N
            sorted_anomalies = sorted(
                anomaly_list,
                key=lambda a: severity_order.get(a.get("severity", "medium"), 1)
            )[:max_anomalies_per_column]
            column_anomalies[col_name] = [_trim_anomaly_dict(a) for a in sorted_anomalies]
    return results

def _generate_anomaly_summary(table_reports: Dict[str, Dict]) -> Dict[str, Any]:
    """Generate overall summary statistics across all tables"""
    
    total_anomalies = 0
    total_tables = len([r for r in table_reports.values() if r.get("status") != "error"])
    
    # Aggregate anomaly types across all tables
    global_anomaly_types = {}
    severity_totals = {"high": 0, "medium": 0, "low": 0}
    
    for table_name, report in table_reports.items():
        if report.get("status") != "error":
            table_summary = report.get("anomaly_summary", {})
            table_anomaly_types = table_summary.get("anomaly_types", {})
            
            for anomaly_type, type_info in table_anomaly_types.items():
                if anomaly_type not in global_anomaly_types:
                    global_anomaly_types[anomaly_type] = {"total_count": 0, "affected_tables": []}
                global_anomaly_types[anomaly_type]["total_count"] += type_info["count"]
                global_anomaly_types[anomaly_type]["affected_tables"].append(table_name)
                
                # Add to severity totals
                for severity, count in type_info["severity_counts"].items():
                    severity_totals[severity] += count
            
            total_anomalies += report.get("total_anomalies_found", 0)
    
    return {
        "total_anomalies": total_anomalies,
        "total_tables_analyzed": total_tables,
        "anomaly_categories": global_anomaly_types,
        "severity_distribution": severity_totals
    }

def _calculate_data_quality_score(high_severity: int, medium_severity: int, low_severity: int, total_columns: int) -> float:
    """Calculate data quality score for a single table"""
    if total_columns == 0:
        return 1.0
    
    # Weighted penalty system
    penalty = (high_severity * 0.3) + (medium_severity * 0.2) + (low_severity * 0.1)
    max_possible_penalty = total_columns * 0.3  # Assume worst case
    
    quality_score = max(0.0, 1.0 - (penalty / max(max_possible_penalty, 1.0)))
    return round(quality_score, 3)

def _calculate_overall_quality_score(severity_totals: Dict[str, int], total_tables: int) -> float:
    """Calculate overall data quality score across all tables"""
    if total_tables == 0:
        return 1.0
    
    total_penalty = (severity_totals["high"] * 0.3) + (severity_totals["medium"] * 0.2) + (severity_totals["low"] * 0.1)
    max_penalty = total_tables * 10 * 0.3  # Assume 10 columns per table max penalty
    
    quality_score = max(0.0, 1.0 - (total_penalty / max(max_penalty, 1.0)))
    return round(quality_score, 3)

def _extract_table_name(table_ref: str) -> str:
    """Extract simple table name from full reference"""
    return table_ref.split('.')[-1]

def _make_json_serializable(obj):
    """Convert objects (incl. numpy/pandas scalars from SQLite) to native types."""
    from utils.json_sanitize import to_native

    return to_native(obj)

def _summarize_table_anomalies(table_report: Dict[str, Any]) -> Dict[str, Any]:
    """Generate summary of anomalies for a single table"""
    
    column_anomalies = table_report.get("column_anomalies", {})
    table_anomalies = table_report.get("table_level_anomalies", [])
    
    # Count by anomaly type
    anomaly_types = {}
    for col_name, col_anomalies in column_anomalies.items():
        for anomaly in col_anomalies:
            anomaly_type = anomaly.get("anomaly_type", "unknown")
            if anomaly_type not in anomaly_types:
                anomaly_types[anomaly_type] = {"count": 0, "columns": [], "severity_counts": {"high": 0, "medium": 0, "low": 0}}
            anomaly_types[anomaly_type]["count"] += 1
            if col_name not in anomaly_types[anomaly_type]["columns"]:
                anomaly_types[anomaly_type]["columns"].append(col_name)
            severity = anomaly.get("severity", "medium")
            anomaly_types[anomaly_type]["severity_counts"][severity] += 1
    
    # Add table-level anomalies
    for anomaly in table_anomalies:
        anomaly_type = anomaly.get("anomaly_type", "unknown")
        if anomaly_type not in anomaly_types:
            anomaly_types[anomaly_type] = {"count": 0, "columns": [], "severity_counts": {"high": 0, "medium": 0, "low": 0}}
        anomaly_types[anomaly_type]["count"] += 1
        severity = anomaly.get("severity", "medium")
        anomaly_types[anomaly_type]["severity_counts"][severity] += 1
    
    # Calculate severity distribution
    total_high = sum(types["severity_counts"]["high"] for types in anomaly_types.values())
    total_medium = sum(types["severity_counts"]["medium"] for types in anomaly_types.values())
    total_low = sum(types["severity_counts"]["low"] for types in anomaly_types.values())
    
    return {
        "anomaly_types": anomaly_types,
        "total_anomaly_types": len(anomaly_types),
        "columns_with_anomalies": len(column_anomalies),
        "severity_distribution": {
            "high": total_high,
            "medium": total_medium,
            "low": total_low
        }
    }


def _generate_mock_anomaly_results(tables: List[str], sensitivity: str) -> Dict[str, Any]:
    """Generate mock anomaly results for testing"""
    
    return {
        "status": "success", 
        "analysis_timestamp": int(time.time()), 
        "sensitivity_level": sensitivity, 
        "tables_analyzed": len(tables), 
        "table_anomaly_reports": {
            "claims": {
                "table_reference": "mock.dataset.claims", 
                "table_name": "claims", 
                "column_anomalies": {
                    "member_id": [
                        {
                            "anomaly_type": "format_inconsistency", 
                            "issue": "Mixed ID format patterns detected", 
                            "severity": "medium", 
                            "pattern_percentage": 3.2, 
                            "examples": ["MEM001", "MEMBER-12345", "mem_001"], 
                            "recommendation": "Standardize member ID format"
                        }
                    ], 
                    "claim_amount": [
                        {
                            "anomaly_type": "statistical_outlier", 
                            "issue": "Statistical outliers detected using z_score method", 
                            "severity": "high", 
                            "outlier_count": 47, 
                            "examples": [150000.00, 200000.00, -500.00], 
                            "recommendation": "Investigate unusually high/low claim amounts"
                        }
                    ], 
                    "service_date": [
                        {
                            "anomaly_type": "pattern_deviation", 
                            "issue": "Mixed date format patterns detected", 
                            "severity": "medium", 
                            "pattern_percentage": 2.1, 
                            "examples": ["01/15/2023", "2023-01-15", "15-Jan-2023"], 
                            "recommendation": "Standardize date format"
                        }
                    ]
                }, 
                "table_level_anomalies": [
                    {
                        "anomaly_type": "duplicate_rows", 
                        "issue": "Duplicate rows detected", 
                        "severity": "high", 
                        "duplicate_signatures": 123, 
                        "total_duplicate_rows": 456, 
                        "duplication_percentage": 0.9, 
                        "recommendation": "Remove duplicate records"
                    }
                ], 
                "total_anomalies_found": 4, 
                "anomaly_summary": {
                    "anomaly_types": {
                        "format_inconsistency": {"count": 1, "columns": ["member_id"]}, 
                        "statistical_outlier": {"count": 1, "columns": ["claim_amount"]}, 
                        "pattern_deviation": {"count": 1, "columns": ["service_date"]}, 
                        "duplicate_rows": {"count": 1, "columns": []}
                    }, 
                    "data_quality_score": 0.847
                }
            }
        }, 
        "summary_statistics": {
            "total_anomalies": 4,
            "total_tables_analyzed": len(tables),
            "severity_distribution": {"high": 2, "medium": 2, "low": 0}
        },
        "processing_stats": {
            "total_processing_time": 4.2, 
            "tables_processed": len(tables), 
            "total_anomalies_detected": 4
        }, 
        "processing_mode": "mock_comprehensive"
    }

# Helper function for agent integration
def get_anomaly_detection_tool():
    """Get the anomaly detection tool for agent integration"""
    from google.adk.tools import FunctionTool
    return FunctionTool(data_anomaly_analysis_tool)

# Export the main functions for agent integration
__all__ = [
    'data_anomaly_analysis_tool',
    'get_anomaly_detection_tool',
    'AnomalyConfig'
]
