# tools/relationship_function.py
"""
Enhanced relationship analysis tool with comprehensive primary, foreign, and composite key detection
"""
import os
import time
import json
from typing import Dict, Any, List, Tuple
from itertools import combinations
from decimal import Decimal
from datetime import datetime, date
import logging
from google.adk.tools import ToolContext
from config.settings import config
from utils.bg_query_utils import get_bigquery_client
import cachetools
import asyncio
from utils.semantic_analyzer import suggest_composite_keys_with_llm
from utils.composite_key_validator import (
    validate_composite_keys_in_bigquery,
    filter_composite_keys_by_context
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from concurrent.futures import ThreadPoolExecutor, as_completed


try:
    from utils import local_warehouse as bigquery
    from utils.local_warehouse import SchemaField
    GCP_AVAILABLE = True
except ImportError:
    GCP_AVAILABLE = False


cache_ttl = int(os.getenv('CACHE_TTL', '300'))
schema_cache = cachetools.TTLCache(maxsize=100, ttl=cache_ttl)



def _get_batch_column_statistics(client, table_ref: str, schema: List[SchemaField], total_rows: int) -> Dict[str, Any]:
    """Get statistics for all columns in a single optimized query"""
    
    # Build dynamic SELECT clauses for all columns
    select_clauses = []
    for field in schema:
        if field.field_type not in ["RECORD", "REPEATED"]:
            col_name = field.name
            select_clauses.extend([
                f"COUNT(DISTINCT `{col_name}`) as {col_name}_unique_count",
                f"COUNTIF(`{col_name}` IS NULL) as {col_name}_null_count",
                f"COUNTIF(TRIM(CAST(`{col_name}` AS STRING)) = '') as {col_name}_empty_count"
            ])
    
    if not select_clauses:
        return {}
    
    query = f"""
    SELECT 
        {', '.join(select_clauses)}
    FROM `{table_ref}`
    """
    
    try:
        result = list(client.query(query).result())[0]
        stats = {}
        
        for field in schema:
            if field.field_type in ["RECORD", "REPEATED"]:
                continue
                
            col_name = field.name
            unique_count = getattr(result, f'{col_name}_unique_count', 0)
            null_count = getattr(result, f'{col_name}_null_count', 0)
            empty_count = getattr(result, f'{col_name}_empty_count', 0)
            
            stats[col_name] = {
                "total_count": total_rows,
                "unique_count": unique_count,
                "null_count": null_count,
                "empty_count": empty_count,
                "uniqueness_percentage": (unique_count / total_rows * 100) if total_rows > 0 else 0,
                "null_percentage": (null_count / total_rows * 100) if total_rows > 0 else 0
            }
        
        return stats
        
    except Exception as e:
        logging.info(f"Error in batch column stats for {table_ref}: {e}")
        return {}



def _analyze_individual_table_optimized(client, table_ref: str) -> Dict[str, Any]:
    """Optimized individual table analysis with batch column statistics"""
    
    cache_key = f"schema_{table_ref}"
    if cache_key in schema_cache:
        logging.info(f"  Using cached schema for: {_extract_table_name(table_ref)}")
        return schema_cache[cache_key]
    
    try:
        table = client.get_table(table_ref)
        table_analysis = {
            "table_reference": table_ref,
            "total_rows": table.num_rows,
            "total_columns": len(table.schema),
            "columns": {},
            "primary_key_candidates": []
        }
        
        # Batch column statistics in a single query
        column_stats = _get_batch_column_statistics(client, table_ref, table.schema, table.num_rows)
        
        for field in table.schema:
            if field.field_type in ["RECORD", "REPEATED"]:
                continue
                
            stats = column_stats.get(field.name, {})
            pk_score = _calculate_pk_score(stats, field.name)
            
            table_analysis["columns"][field.name] = {
                "data_type": field.field_type,
                "stats": stats,
                "pk_score": pk_score,
                "fk_potential": _assess_fk_potential(stats, field.name)
            }
            
            if pk_score >= 0.8:
                table_analysis["primary_key_candidates"].append({
                    "column": field.name,
                    "score": pk_score
                })
        
        # Cache the results
        schema_cache[cache_key] = table_analysis
        return table_analysis
        
    except Exception as e:
        logging.info(f"Error analyzing table {table_ref}: {e}")
        return {
            "table_reference": table_ref,
            "error": str(e),
            "columns": {}
        }


def _parallel_table_analysis(client, tables: List[str]) -> Dict[str, Any]:
    """Perform parallel table analysis using ThreadPoolExecutor"""
    
    table_schemas = {}
    
    with ThreadPoolExecutor(max_workers=min(config.max_workers, len(tables))) as executor:
        future_to_table = {
            executor.submit(_analyze_individual_table_optimized, client, table): table 
            for table in tables
        }
        
        for future in as_completed(future_to_table):
            table_ref = future_to_table[future]
            try:
                table_analysis = future.result(timeout=config.query_timeout)
                table_schemas[table_ref] = table_analysis
                logging.info(f"  ✓ Completed analysis for: {_extract_table_name(table_ref)}")
            except Exception as e:
                logging.info(f"  ✗ Error analyzing table {table_ref}: {e}")
                # Provide basic table info even if analysis fails
                table_schemas[table_ref] = {
                    "table_reference": table_ref,
                    "error": str(e),
                    "columns": {}
                }
    
    return table_schemas


def _optimized_value_overlap_analysis(client, source_table: str, source_col: str, 
                                    target_table: str, target_col: str) -> Dict[str, Any]:
    """Optimized value overlap analysis with sampling for large tables"""
    
    # Check table sizes and apply sampling if needed
    source_size = _get_table_size(client, source_table)
    target_size = _get_table_size(client, target_table)
    
    sampling_clause = ""
    if source_size > config.sampling_threshold or target_size > config.sampling_threshold:
        sampling_clause = "TABLESAMPLE SYSTEM (10 PERCENT)"
        logging.info(f"  Using sampling for large tables: {source_col} -> {target_col}")
    
    query = f"""
    WITH source_values AS (
        SELECT 
            `{source_col}` as value,
            COUNT(*) as source_frequency
        FROM `{source_table}` {sampling_clause}
        WHERE `{source_col}` IS NOT NULL
        GROUP BY `{source_col}`
    ),
    target_values AS (
        SELECT 
            `{target_col}` as value,
            COUNT(*) as target_frequency
        FROM `{target_table}` {sampling_clause}
        WHERE `{target_col}` IS NOT NULL
        GROUP BY `{target_col}`
    ),
    overlap_stats AS (
        SELECT 
            COUNT(DISTINCT s.value) as total_source_values,
            COUNT(DISTINCT t.value) as total_target_values,
            COUNT(DISTINCT CASE WHEN t.value IS NOT NULL THEN s.value END) as overlapping_values,
            SUM(s.source_frequency) as total_source_records,
            SUM(CASE WHEN t.value IS NOT NULL THEN s.source_frequency ELSE 0 END) as overlapping_source_records
        FROM source_values s
        LEFT JOIN target_values t ON s.value = t.value
    )
    SELECT 
        total_source_values,
        total_target_values,
        overlapping_values,
        total_source_records,
        overlapping_source_records,
        SAFE_DIVIDE(overlapping_values, total_source_values) * 100 as value_overlap_percentage,
        SAFE_DIVIDE(overlapping_source_records, total_source_records) * 100 as record_overlap_percentage
    FROM overlap_stats
    """
    
    try:
        result = list(client.query(query).result())[0]
        
        return {
            "total_source_values": result.total_source_values,
            "total_target_values": result.total_target_values,
            "overlapping_values": result.overlapping_values,
            "total_source_records": result.total_source_records,
            "overlapping_source_records": result.overlapping_source_records,
            "value_overlap_percentage": float(result.value_overlap_percentage or 0),
            "record_overlap_percentage": float(result.record_overlap_percentage or 0),
            "overlap_percentage": max(float(result.value_overlap_percentage or 0), float(result.record_overlap_percentage or 0)),
            "analysis_summary": f"{result.overlapping_values}/{result.total_source_values} distinct values match ({result.value_overlap_percentage:.1f}%), covering {result.overlapping_source_records}/{result.total_source_records} records ({result.record_overlap_percentage:.1f}%)",
            "sampling_used": bool(sampling_clause)
        }
        
    except Exception as e:
        logging.info(f"Error analyzing overlap between {source_col} and {target_col}: {e}")
        return _get_fallback_overlap_analysis()
    

def _find_foreign_key_relationships_optimized(client, source_table: str, target_table: str, 
                                            source_data: Dict, target_data: Dict) -> List[Dict[str, Any]]:
    """Optimized foreign key detection with pre-filtering"""
    
    relationships = []
    source_table_name = _extract_table_name(source_table)
    target_table_name = _extract_table_name(target_table)
    
    logging.info(f"Analyzing FK relationships: {source_table_name} -> {target_table_name}")
    
    # Pre-filter columns by data type and naming similarity
    potential_pairs = []
    
    for source_col, source_info in source_data.get("columns", {}).items():
        for target_col, target_info in target_data.get("columns", {}).items():
            if source_info["data_type"] != target_info["data_type"]:
                continue
            
            naming_similarity = _calculate_naming_similarity(source_col, target_col)
            if naming_similarity >= 0.3:  # Reduced threshold for more candidates
                potential_pairs.append((source_col, target_col, naming_similarity))
    
    # Process potential pairs in batches
    for source_col, target_col, naming_similarity in potential_pairs:
        overlap_analysis = _optimized_value_overlap_analysis(
            client, source_table, source_col, target_table, target_col
        )
        
        overlap_pct = overlap_analysis["overlap_percentage"]
        
        if overlap_pct >= 40 and naming_similarity >= 0.5:
            confidence_level = "HIGH" if overlap_pct >= 80 else "MEDIUM" if overlap_pct >= 60 else "LOW"
            
            relationships.append({
                "source_table": source_table_name,
                "source_column": source_col,
                "target_table": target_table_name,
                "target_column": target_col,
                "relationship_type": "foreign_key",
                "confidence_score": _calculate_fk_confidence(overlap_pct, naming_similarity),
                "confidence_level": confidence_level,
                "naming_similarity": naming_similarity,
                "data_overlap_details": overlap_analysis,
                "interpretation": _generate_fk_interpretation(
                    source_col, target_col, overlap_analysis, confidence_level
                )
            })
    
    return relationships

def _parallel_relationship_detection(client, table_schemas: Dict[str, Any], analysis_depth: str) -> List[Dict[str, Any]]:
    """
    LLM-Enhanced parallel detection of cross-table relationships with business context.

    For comprehensive analysis:
    1. Analyzes business context for all tables (in parallel)
    2. Uses LLM to suggest business-relevant relationships
    3. Validates suggestions with actual data overlap
    4. Combines LLM business logic (60%) + data validation (40%)

    For standard analysis:
    - Falls back to statistical name matching + data overlap only
    """

    logger.info("Relationship detection starting...")

    # Use LLM for comprehensive mode, statistical for standard mode
    use_llm = analysis_depth == "comprehensive"

    if use_llm:
        logger.info("Using LLM-enhanced relationship detection (business-aware)")
        return _parallel_relationship_detection_with_llm(client, table_schemas)
    else:
        logger.info("Using statistical relationship detection (name matching + overlap)")
        return _parallel_relationship_detection_statistical(client, table_schemas)


def _parallel_relationship_detection_with_llm(client, table_schemas: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    LLM-ENHANCED: Use business context to guide relationship detection.
    """

    relationships = []
    table_refs = list(table_schemas.keys())

    # Step 1: Analyze context for all tables in parallel
    logger.info(f"Step 1: Analyzing business context for {len(table_refs)} tables...")
    table_contexts = {}

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_table = {
            executor.submit(_analyze_table_context_for_relationships,
                          client, table_ref, table_data): table_ref
            for table_ref, table_data in table_schemas.items()
        }

        for future in as_completed(future_to_table):
            table_ref = future_to_table[future]
            try:
                table_contexts[table_ref] = future.result(timeout=config.query_timeout)
            except Exception as e:
                logger.warning(f"Context analysis failed for {table_ref}: {e}")
                table_contexts[table_ref] = _get_fallback_context(table_schemas[table_ref])

    # Step 2: For each table pair, ask LLM if they should be related
    logger.info(f"Step 2: Requesting LLM suggestions for {len(table_refs)*(len(table_refs)-1)//2} table pairs...")

    # Process pairs in parallel (but limit concurrency to avoid rate limits)
    tasks = []
    for i, source_table in enumerate(table_refs):
        for j, target_table in enumerate(table_refs):
            if i >= j:
                continue
            tasks.append((source_table, target_table))

    # Process in batches to avoid overwhelming LLM
    batch_size = min(10, config.max_workers)

    for batch_start in range(0, len(tasks), batch_size):
        batch_tasks = tasks[batch_start:batch_start + batch_size]

        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            future_to_task = {
                executor.submit(
                    _process_table_pair_with_llm,
                    client, source_table, target_table,
                    table_schemas[source_table], table_schemas[target_table],
                    table_contexts[source_table], table_contexts[target_table]
                ): (source_table, target_table)
                for source_table, target_table in batch_tasks
            }

            for future in as_completed(future_to_task):
                try:
                    pair_relationships = future.result(timeout=config.query_timeout * 2)  # Longer timeout for LLM
                    relationships.extend(pair_relationships)
                except Exception as e:
                    source_table, target_table = future_to_task[future]
                    logger.warning(f"LLM relationship detection failed for {source_table} -> {target_table}: {e}")

    logger.info(f"Enhanced relationship detection complete: {len(relationships)} relationships found")
    return relationships


def _process_table_pair_with_llm(
    client,
    source_table: str,
    target_table: str,
    source_data: Dict,
    target_data: Dict,
    source_context: Dict,
    target_context: Dict
) -> List[Dict[str, Any]]:
    """
    Process a single table pair: get LLM suggestions and validate with data.
    """

    relationships = []
    source_name = _extract_table_name(source_table)
    target_name = _extract_table_name(target_table)

    # Step 1: Ask LLM if these tables should be related
    llm_suggestions = _suggest_cross_table_relationships_with_llm(
        source_table_ref=source_table,
        target_table_ref=target_table,
        source_context=source_context,
        target_context=target_context,
        source_columns=source_data.get("columns", {}),
        target_columns=target_data.get("columns", {})
    )

    # If LLM says they shouldn't be related, skip data validation
    if not llm_suggestions.get("should_relate", False):
        logger.info(f"  LLM: {source_name} and {target_name} should NOT be related")
        return []

    # Step 2: Validate LLM's suggested relationships with actual data
    logger.info(f"  LLM: {source_name} -> {target_name} should be related, validating...")

    for suggested_rel in llm_suggestions.get("suggested_relationships", []):
        source_col = suggested_rel.get("source_column")
        target_col = suggested_rel.get("target_column")
        llm_confidence = suggested_rel.get("confidence", 0.5)

        if not source_col or not target_col:
            continue

        # Check if columns exist
        if (source_col not in source_data.get("columns", {}) or
            target_col not in target_data.get("columns", {})):
            logger.warning(f"  Suggested columns {source_col}/{target_col} not found, skipping")
            continue

        # Step 3: Validate with actual data overlap
        try:
            overlap_analysis = _optimized_value_overlap_analysis(
                client, source_table, source_col, target_table, target_col
            )

            overlap_pct = overlap_analysis["overlap_percentage"]

            # Combined confidence: LLM business logic + data validation
            # Only accept if data overlap supports the LLM's suggestion (>40% overlap)
            if overlap_pct >= 40:
                combined_confidence = _calculate_combined_confidence(llm_confidence, overlap_pct)
                confidence_level = _determine_confidence_level(combined_confidence, overlap_pct)

                relationships.append({
                    "source_table": source_name,
                    "source_column": source_col,
                    "target_table": target_name,
                    "target_column": target_col,
                    "relationship_type": "foreign_key",
                    "confidence_score": combined_confidence,
                    "confidence_level": confidence_level,
                    "llm_confidence": llm_confidence,
                    "data_overlap_percentage": overlap_pct,
                    "data_overlap_details": overlap_analysis,
                    "business_reasoning": suggested_rel.get("business_reasoning", ""),
                    "expected_cardinality": suggested_rel.get("expected_cardinality", "unknown"),
                    "interpretation": _generate_fk_interpretation(source_col, target_col, overlap_analysis, confidence_level),
                    "detection_method": "llm_enhanced"
                })

                logger.info(f"  ✓ Validated: {source_col} -> {target_col} "
                          f"(LLM: {llm_confidence:.2f}, Overlap: {overlap_pct:.1f}%, Combined: {combined_confidence:.2f})")
            else:
                logger.info(f"  ✗ Rejected: {source_col} -> {target_col} "
                          f"(LLM suggested but data overlap too low: {overlap_pct:.1f}%)")

        except Exception as e:
            logger.warning(f"  Failed to validate {source_col} -> {target_col}: {e}")
            continue

    return relationships


def _calculate_combined_confidence(llm_confidence: float, overlap_pct: float) -> float:
    """
    Combine LLM business logic confidence with data overlap validation.

    Args:
        llm_confidence: LLM's confidence in the business relationship (0-1)
        overlap_pct: Actual data overlap percentage (0-100)

    Returns:
        Combined confidence score (0-1)
    """
    # Convert overlap to 0-1 scale
    overlap_score = min(overlap_pct / 100.0, 1.0)

    # Weighted average: 60% LLM business logic, 40% data validation
    # This prioritizes business meaning while ensuring data supports it
    combined = (llm_confidence * 0.6) + (overlap_score * 0.4)

    return min(combined, 1.0)


def _determine_confidence_level(combined_confidence: float, overlap_pct: float) -> str:
    """
    Determine confidence level based on combined score and overlap.
    """
    if combined_confidence >= 0.85 and overlap_pct >= 80:
        return "HIGH"
    elif combined_confidence >= 0.7 and overlap_pct >= 60:
        return "MEDIUM"
    else:
        return "LOW"


def _parallel_relationship_detection_statistical(client, table_schemas: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    FALLBACK: Statistical-only relationship detection (original implementation).
    Used when LLM is disabled or unavailable.
    """

    relationships = []
    table_refs = list(table_schemas.keys())
    tasks = []

    # Prepare tasks for parallel execution
    for i, source_table in enumerate(table_refs):
        for j, target_table in enumerate(table_refs):
            if i >= j:
                continue
            tasks.append((source_table, target_table))

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_task = {
            executor.submit(
                _find_foreign_key_relationships_optimized,
                client, source_table, target_table,
                table_schemas[source_table], table_schemas[target_table]
            ): (source_table, target_table)
            for source_table, target_table in tasks
        }

        for future in as_completed(future_to_task):
            try:
                fk_relationships = future.result(timeout=config.query_timeout)
                # Mark as statistical detection
                for rel in fk_relationships:
                    rel["detection_method"] = "statistical"
                relationships.extend(fk_relationships)
            except Exception as e:
                source_table, target_table = future_to_task[future]
                logging.info(f"Error analyzing relationship {source_table} -> {target_table}: {e}")

    return relationships


def _parallel_composite_analysis(client, table_schemas: Dict[str, Any]):
    """Parallel composite key analysis"""
    
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_table = {
            executor.submit(_optimized_composite_analysis, client, table_ref, table_data): table_ref
            for table_ref, table_data in table_schemas.items()
        }
        
        for future in as_completed(future_to_table):
            table_ref = future_to_table[future]
            try:
                composite_keys = future.result(timeout=config.query_timeout)
                table_schemas[table_ref]["composite_keys"] = composite_keys
                logging.info(f"  ✓ Composite analysis completed for: {_extract_table_name(table_ref)}")
            except Exception as e:
                logging.info(f"  ✗ Error in composite analysis for {table_ref}: {e}")
                table_schemas[table_ref]["composite_keys"] = {}

def _optimized_composite_analysis(client, table_ref: str, table_data: Dict) -> Dict[str, Any]:
    """
    ENHANCED: LLM-based composite key analysis with business context awareness.

    This function now:
    1. Uses LLM to understand table context (authorization-level, claim-level, etc.)
    2. Gets business-aware composite key suggestions from LLM
    3. Validates suggestions in BigQuery
    4. Filters by business rules

    Falls back to statistical analysis if LLM fails.
    """

    logger.info(f"  Starting enhanced composite analysis for {_extract_table_name(table_ref)}")

    try:
        # Step 1: Analyze table context with LLM
        context_analysis = _analyze_table_context_for_relationships(
            client, table_ref, table_data
        )

        # Step 2: Get LLM's composite key suggestions
        llm_suggestions = context_analysis["composite_suggestions"]

        # Check if we have meaningful suggestions from LLM
        has_llm_suggestions = (
            len(llm_suggestions.get("two_column_combos", [])) > 0 or
            len(llm_suggestions.get("three_column_combos", [])) > 0
        )

        if not has_llm_suggestions:
            logger.warning(f"  No LLM suggestions for {table_ref}, falling back to statistical analysis")
            return _statistical_composite_analysis(client, table_ref, table_data)

        # Step 3: Validate LLM suggestions in BigQuery
        total_rows = table_data.get("total_rows", 0)

        validated_combos = validate_composite_keys_in_bigquery(
            client=client,
            table_reference=table_ref,
            composite_key_combos={
                "two_column_combos": llm_suggestions.get("two_column_combos", []),
                "three_column_combos": llm_suggestions.get("three_column_combos", []),
                "four_column_combos": llm_suggestions.get("four_column_combos", [])
            },
            total_rows=total_rows
        )

        # Step 4: Filter by business context
        min_uniqueness = getattr(config, 'MIN_COMPOSITE_UNIQUENESS', 98.0)

        filtered_combos = filter_composite_keys_by_context(
            validated_results=validated_combos,
            table_context=context_analysis["table_context"],
            min_uniqueness=min_uniqueness
        )

        # Step 5: Convert to original format for backward compatibility
        composite_results = {}

        two_col_recs = filtered_combos.get("two_column_recommendations", [])
        if two_col_recs:
            composite_results["2_column_combinations"] = [
                {
                    "columns": rec["columns"],
                    "uniqueness_percentage": rec["uniqueness_percentage"],
                    "combination_score": rec.get("composite_score", 0.9),
                    "business_meaning": rec.get("business_meaning", ""),
                    "source": "llm_enhanced"
                }
                for rec in two_col_recs
            ]

        three_col_recs = filtered_combos.get("three_column_recommendations", [])
        if three_col_recs:
            composite_results["3_column_combinations"] = [
                {
                    "columns": rec["columns"],
                    "uniqueness_percentage": rec["uniqueness_percentage"],
                    "combination_score": rec.get("composite_score", 0.9),
                    "business_meaning": rec.get("business_meaning", ""),
                    "source": "llm_enhanced"
                }
                for rec in three_col_recs
            ]

        # Include table context for downstream use
        composite_results["table_context"] = context_analysis["table_context"]

        logger.info(f"  ✓ Enhanced composite analysis complete: {len(composite_results.get('2_column_combinations', []))} 2-col, "
                   f"{len(composite_results.get('3_column_combinations', []))} 3-col combinations")

        return composite_results

    except Exception as e:
        logger.error(f"  Enhanced composite analysis failed for {table_ref}: {e}")
        logger.info(f"  Falling back to statistical analysis")
        return _statistical_composite_analysis(client, table_ref, table_data)


def _statistical_composite_analysis(client, table_ref: str, table_data: Dict) -> Dict[str, Any]:
    """
    FALLBACK: Statistical-only composite key analysis (original implementation).
    Used when LLM analysis fails or is unavailable.
    """

    suitable_columns = []
    for col_name, col_info in table_data.get("columns", {}).items():
        uniqueness = col_info.get("stats", {}).get("uniqueness_percentage", 0)
        null_pct = col_info.get("stats", {}).get("null_percentage", 0)

        # More selective criteria for composite key candidates
        if uniqueness > 10 and null_pct < 50:  # More realistic thresholds
            suitable_columns.append((col_name, uniqueness))

    # Sort by uniqueness and take top candidates
    suitable_columns.sort(key=lambda x: x[1], reverse=True)
    top_columns = [col for col, _ in suitable_columns[:8]]  # Limit to top 8 columns

    if len(top_columns) < 2:
        return {}

    composite_results = {}

    # Test 2-column combinations (most common case)
    two_col_combos = list(combinations(top_columns, 2))[:config.max_composite_combinations]
    valid_2_col = _test_composite_combinations(client, table_ref, two_col_combos, 85.0)

    if valid_2_col:
        # Mark as statistical source
        for combo in valid_2_col:
            combo["source"] = "statistical"
            combo["business_meaning"] = "Statistical analysis - no business context available"
        composite_results["2_column_combinations"] = valid_2_col

    # Only test 3-column if we have good 2-column results
    if len(top_columns) >= 3 and valid_2_col:
        three_col_combos = list(combinations(top_columns, 3))[:config.max_composite_combinations//2]
        valid_3_col = _test_composite_combinations(client, table_ref, three_col_combos, 90.0)

        if valid_3_col:
            # Mark as statistical source
            for combo in valid_3_col:
                combo["source"] = "statistical"
                combo["business_meaning"] = "Statistical analysis - no business context available"
            composite_results["3_column_combinations"] = valid_3_col

    return composite_results

def _test_composite_combinations(client, table_ref: str, combinations_list: List[Tuple], threshold: float) -> List[Dict]:
    """Test multiple composite combinations efficiently"""
    
    valid_combinations = []
    
    for combo in combinations_list:
        try:
            uniqueness = _test_composite_uniqueness(client, table_ref, list(combo))
            
            if uniqueness >= threshold:
                valid_combinations.append({
                    "columns": list(combo),
                    "uniqueness_percentage": uniqueness,
                    "combination_score": 0.9 if uniqueness >= 95 else 0.8 if uniqueness >= 90 else 0.7
                })
        except Exception as e:
            logging.info(f"Error testing combo {combo}: {e}")
            continue
    
    return valid_combinations

def _get_table_size(client, table_ref: str) -> int:
    """Get table size in bytes"""
    try:
        table = client.get_table(table_ref)
        return table.num_bytes
    except:
        return 0

def _get_fallback_overlap_analysis() -> Dict[str, Any]:
    """Return fallback overlap analysis when query fails"""
    return {
        "total_source_values": 0,
        "total_target_values": 0,
        "overlapping_values": 0,
        "total_source_records": 0,
        "overlapping_source_records": 0,
        "value_overlap_percentage": 0.0,
        "record_overlap_percentage": 0.0,
        "overlap_percentage": 0.0,
        "analysis_summary": "Analysis failed",
        "error": "Query execution failed"
    }


def _perform_optimized_analysis(client, tables: List[str], analysis_depth: str) -> Dict[str, Any]:
    """
    LLM-Enhanced multi-table relationship analysis with business context awareness.

    Always uses LLM to:
    - Understand table context (claim-level, member-level, etc.)
    - Suggest business-relevant composite keys
    - Detect meaningful cross-table relationships
    """

    start_time = time.time()

    # LLM is ALWAYS enabled for comprehensive analysis
    use_llm = analysis_depth == "comprehensive"

    results = {
        "status": "success",
        "analysis_timestamp": int(time.time()),
        "analysis_depth": analysis_depth,
        "tables_analyzed": len(tables),
        "table_details": {},
        "cross_table_relationships": [],
        "processing_stats": {},
        "processing_mode": "llm_enhanced" if use_llm else "statistical_only",
        "llm_features_active": use_llm
    }

    logging.info("=" * 60)
    logging.info("LLM-ENHANCED RELATIONSHIP ANALYSIS")
    logging.info(f"Analysis Depth: {analysis_depth}")
    logging.info(f"LLM Business Intelligence: {'ENABLED' if use_llm else 'DISABLED (standard mode)'}")
    logging.info("=" * 60)

    logging.info("Phase 1: Parallel table analysis...")

    # Phase 1: Parallel table analysis
    table_schemas = _parallel_table_analysis(client, tables)
    results["table_details"] = {_extract_table_name(ref): data for ref, data in table_schemas.items()}

    if len(table_schemas) > 1:
        logging.info("Phase 2: Parallel cross-table relationship detection...")
        logging.info(f"  Mode: {'LLM-Enhanced (Business-Aware)' if use_llm else 'Statistical Only'}")
        results["cross_table_relationships"] = _parallel_relationship_detection(
            client, table_schemas, analysis_depth
        )

    if analysis_depth == "comprehensive":
        logging.info("Phase 3: Optimized composite key analysis...")
        logging.info("  Mode: LLM-Enhanced (Business-Aware)")
        _parallel_composite_analysis(client, table_schemas)

    logging.info("Phase 4: Generating column classifications and formatting composite keys...")
    for table_name, table_data in results["table_details"].items():
        # Generate column classifications with AK notation
        column_classifications = _generate_column_classifications(
            table_data, results["cross_table_relationships"], table_name, results["table_details"]
        )
        results["table_details"][table_name]["column_classifications"] = column_classifications

        # Format composite keys with AK notation at table level
        if "composite_keys" in table_data:
            formatted_aks = _format_composite_keys_with_ak_notation(table_data["composite_keys"])
            results["table_details"][table_name]["alternate_keys"] = formatted_aks

            # Remove the raw composite_keys to reduce noise
            # Keep table_context for reference
            if "table_context" in table_data["composite_keys"]:
                results["table_details"][table_name]["table_context"] = table_data["composite_keys"]["table_context"]

            # Remove raw composite key data (now replaced with alternate_keys)
            del results["table_details"][table_name]["composite_keys"]

    # Processing statistics (focused on key metrics)
    processing_time = time.time() - start_time

    # Count LLM-enhanced vs statistical relationships
    llm_enhanced_count = sum(1 for r in results["cross_table_relationships"]
                            if r.get("detection_method") == "llm_enhanced")

    # Count total composite keys found
    total_aks = sum(len(table_data.get("alternate_keys", []))
                   for table_data in results["table_details"].values())

    results["summary"] = {
        "tables_analyzed": len(table_schemas),
        "cross_table_relationships": len(results["cross_table_relationships"]),
        "composite_keys_found": total_aks,
        "llm_business_intelligence": use_llm,
        "processing_time_seconds": round(processing_time, 2)
    }

    # Remove verbose processing_stats and replace with focused summary
    if "processing_stats" in results:
        del results["processing_stats"]

    logging.info("=" * 60)
    logging.info(f"ANALYSIS COMPLETE in {processing_time:.2f}s")
    logging.info(f"Tables: {len(table_schemas)}, FK Relationships: {len(results['cross_table_relationships'])}, Composite Keys: {total_aks}")
    logging.info(f"LLM Business Intelligence: {'ENABLED' if use_llm else 'DISABLED'}")
    logging.info("=" * 60)

    return _make_json_serializable(results)


def relationship_analysis_tool(table_references: str, analysis_depth: str = "comprehensive", tool_context: ToolContext | None = None) -> Dict[str, Any]:
    """
    LLM-Enhanced relationship analysis with business-aware composite and foreign key detection.

    This tool ALWAYS uses LLM to understand business context and suggest meaningful relationships.

    Args:
        table_references (str): Comma-separated list of table references (project.dataset.table)
        analysis_depth (str): "standard" (basic stats) or "comprehensive" (includes LLM analysis)
        tool_context (ToolContext): ADK tool context

    Returns:
        dict: Enhanced relationship analysis with business-aware key classifications
    """
    logging.info("=" * 80)
    logging.info("=== LLM-ENHANCED RELATIONSHIP ANALYSIS ===")
    logging.info(f"Table references: {table_references}")
    logging.info(f"Analysis depth: {analysis_depth}")
    logging.info("=" * 80)

    try:
        # Parse table references
        tables = [t.strip() for t in table_references.split(',')]

        # Check if BigQuery is available
        if not GCP_AVAILABLE:
            return {
                "status": "error",
                "error_message": "BigQuery libraries not available. Please install google-cloud-bigquery.",
                "table_references": table_references
            }

        # Perform LLM-enhanced BigQuery analysis
        client = get_bigquery_client()

        final_results = _perform_optimized_analysis(client, tables, analysis_depth)
        # Sanitize numpy/pandas scalar types (from the local SQLite warehouse) to
        # native Python so pydantic/JSON serialization downstream doesn't fail.
        final_results = _make_json_serializable(final_results)
        # ==========================================
        # ADK-COMPLIANT SOLUTION: Use ToolContext.state
        # ==========================================
        # Store full results in session state (NOT returned to ADK agent)
        # This prevents token limit errors while keeping data accessible to /send-stream endpoint

        tool_context.state['relationship_analysis_tool_response'] = final_results
        logger.warning(f"✓ Stored {len(final_results)} full results in ToolContext.state")


        return final_results

    except Exception as e:
        logging.error(f"Relationship analysis failed: {e}", exc_info=True)
        return {
            "status": "error",
            "error_message": str(e),
            "table_references": table_references,
            "details": "Check server logs for full error details"
        }

def _calculate_fk_confidence(overlap_pct: float, naming_similarity: float) -> float:
    """Calculate FK relationship confidence score"""
    # Weighted combination of overlap and naming similarity
    overlap_weight = 0.7
    naming_weight = 0.3
    
    confidence = (overlap_pct / 100 * overlap_weight) + (naming_similarity * naming_weight)
    return min(confidence, 1.0)

def _generate_fk_interpretation(source_col: str, target_col: str, overlap_data: Dict, confidence: str) -> str:
    """Generate business-friendly interpretation of FK relationship"""
    
    overlap_pct = overlap_data.get("overlap_percentage", 0)
    
    if confidence == "HIGH":
        return f"Strong foreign key relationship: {source_col} references {target_col} with {overlap_pct:.1f}% data overlap, indicating reliable referential integrity"
    elif confidence == "MEDIUM":
        return f"Likely foreign key relationship: {source_col} appears to reference {target_col} with {overlap_pct:.1f}% data overlap, some referential integrity issues may exist"
    else:
        return f"Potential foreign key relationship: {source_col} may reference {target_col} with {overlap_pct:.1f}% data overlap, requires validation for referential integrity"

def _test_composite_uniqueness(client, table_ref: str, columns: List[str]) -> float:
    """Test uniqueness of column combination"""
    
    columns_concat = "CONCAT(" + ", '|', ".join([f"COALESCE(CAST(`{col}` AS STRING), 'NULL')" for col in columns]) + ")"
    
    query = f"""
    SELECT 
        COUNT(*) as total_rows,
        COUNT(DISTINCT {columns_concat}) as unique_combinations
    FROM `{table_ref}`
    """
    
    try:
        result = list(client.query(query).result())[0]
        if result.total_rows > 0:
            return (result.unique_combinations / result.total_rows) * 100
        return 0.0
    except Exception as e:
        logging.info(f"Error testing composite uniqueness for {columns}: {e}")
        return 0.0

def _format_composite_keys_with_ak_notation(composite_keys: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Transform composite keys into AK notation format.

    Input: {
        "2_column_combinations": [
            {"columns": ["claim_id", "line_number"], "uniqueness_percentage": 99.8},
        ]
    }

    Output: [
        {
            "ak_id": "AK1",
            "columns": [
                {"name": "claim_id", "position": "AK1.1"},
                {"name": "line_number", "position": "AK1.2"}
            ],
            "uniqueness_percentage": 99.8
        }
    ]
    """
    formatted_keys = []
    ak_counter = 1

    # Process all combination sizes in order
    for combo_size_key in ["2_column_combinations", "3_column_combinations", "4_column_combinations", "5_column_combinations"]:
        combinations = composite_keys.get(combo_size_key, [])

        if combinations and isinstance(combinations, list):
            for combo in combinations:
                if isinstance(combo, dict):
                    ak_id = f"AK{ak_counter}"
                    columns_with_notation = []

                    for idx, col_name in enumerate(combo.get("columns", []), start=1):
                        columns_with_notation.append({
                            "name": col_name,
                            "position": f"{ak_id}.{idx}"
                        })

                    formatted_keys.append({
                        "ak_id": ak_id,
                        "columns": columns_with_notation,
                        "uniqueness_percentage": combo.get("uniqueness_percentage", 0.0),
                        "business_meaning": combo.get("business_meaning", ""),
                        "source": combo.get("source", "llm_enhanced")
                    })

                    ak_counter += 1

    return formatted_keys


def _generate_column_classifications(table_data: Dict, cross_relationships: List[Dict],
                                   current_table: str, all_tables: Dict) -> Dict[str, Any]:
    """
    Generate per-column key classifications (PK, FK, AK).

    Now includes AK notation (AK1.1, AK1.2, AK2.1, etc.)
    """

    classifications = {}
    
    for column_name, column_info in table_data["columns"].items():
        
        # Primary Key Assessment
        pk_score = column_info.get("pk_score", 0)
        is_pk = pk_score >= 0.8
        
        # Foreign Key Assessment - find relationships where this column is the source
        fk_relationships = []
        is_fk = False
        
        for relationship in cross_relationships:
            if (relationship.get("source_table") == current_table and 
                relationship.get("source_column") == column_name):
                is_fk = True
                fk_relationships.append({
                    "referenced_table": relationship.get("target_table"),
                    "referenced_column": relationship.get("target_column"),
                    "confidence": relationship.get("confidence_score", 0.0)
                })
        
        # Extract associated files from foreign key relationships
        associated_files = []
        for rel in fk_relationships:
            if rel.get("referenced_table") and rel["referenced_table"] not in associated_files:
                associated_files.append(rel["referenced_table"])
        
        # Composite Key Assessment (Alternate Keys) - find all AKs this column participates in
        # Use AK notation (AK1.1, AK1.2, AK2.1, etc.)
        alternate_keys = []
        composite_data = table_data.get("composite_keys", {})

        logging.info(f"Checking AK for column {column_name}")

        # First, format all composite keys with AK notation
        formatted_aks = _format_composite_keys_with_ak_notation(composite_data)

        # Find which AKs this column participates in
        for ak in formatted_aks:
            # Check if this column is in this AK
            for col_info in ak["columns"]:
                if col_info["name"] == column_name:
                    alternate_keys.append({
                        "ak_id": ak["ak_id"],
                        "position": col_info["position"],  # e.g., "AK1.2"
                        "full_key": [c["name"] for c in ak["columns"]],
                        "uniqueness_percentage": ak.get("uniqueness_percentage", 0.0)
                    })
                    logging.info(f"Found {column_name} in {ak['ak_id']} at position {col_info['position']}")
                    break

        logging.info(f"Total AK found for {column_name}: {len(alternate_keys)}")
        
        # Build final classification for this column
        classifications[column_name] = {
            "pk": "yes" if is_pk else "no",
            "fk": "yes" if is_fk else "no",
            "associated_files": associated_files,
            "ak": alternate_keys
        }
    
    return classifications

def _calculate_pk_score(stats: Dict, column_name: str) -> float:
    """Calculate primary key confidence score"""
    if "error" in stats:
        return 0.0
    
    uniqueness = stats.get("uniqueness_percentage", 0)
    null_pct = stats.get("null_percentage", 100)
    
    score = 0.0
    
    # Uniqueness component (60%)
    if uniqueness == 100:
        score += 0.6
    elif uniqueness >= 99:
        score += 0.5
    elif uniqueness >= 95:
        score += 0.4
    elif uniqueness >= 90:
        score += 0.3
    
    # Null component (30%)
    if null_pct == 0:
        score += 0.3
    elif null_pct <= 1:
        score += 0.2
    elif null_pct <= 5:
        score += 0.1
    
    # Naming component (10%)
    name_lower = column_name.lower()
    if any(keyword in name_lower for keyword in ['id', 'key', 'pk']):
        score += 0.1
    
    return min(score, 1.0)

def _assess_fk_potential(stats: Dict, column_name: str) -> float:
    """Assess foreign key potential"""
    if "error" in stats:
        return 0.0
    
    uniqueness = stats.get("uniqueness_percentage", 100)
    null_pct = stats.get("null_percentage", 100)
    
    # Good FK candidates have medium uniqueness (not too unique, not too repetitive)
    if 5 <= uniqueness <= 80 and null_pct <= 20:
        return 0.8
    elif 1 <= uniqueness <= 90 and null_pct <= 30:
        return 0.6
    else:
        return 0.3

# Helper functions
def _extract_table_name(table_ref: str) -> str:
    """Extract simple table name from full reference"""
    return table_ref.split('.')[-1]

def _calculate_naming_similarity(name1: str, name2: str) -> float:
    """Enhanced similarity calculation for column names"""
    name1, name2 = name1.lower(), name2.lower()
    
    # Exact match
    if name1 == name2:
        return 1.0
    
    # Common business patterns
    business_patterns = [
        # Customer patterns
        (['cust_id', 'customer_id', 'custid'], 0.95),
        (['cust_no', 'customer_no', 'customer_number'], 0.95),
        (['cust_code', 'customer_code'], 0.95),
        
        # Order patterns  
        (['order_id', 'orderid', 'order_no', 'order_number'], 0.95),
        
        # Product patterns
        (['prod_id', 'product_id', 'productid'], 0.95),
        (['prod_code', 'product_code'], 0.95),
        
        # Provider patterns
        (['prov_id', 'provider_id', 'providerid'], 0.95),
        (['prov_code', 'provider_code'], 0.95),
        
        # Member patterns
        (['mem_id', 'member_id', 'memberid'], 0.95),
        (['mem_no', 'member_no', 'member_number'], 0.95),
        
        # Generic ID patterns
        (['id', '_id'], 0.8),
        (['code', '_code'], 0.8),
        (['no', '_no', 'number'], 0.8)
    ]
    
    # Check business patterns
    for pattern_group, score in business_patterns:
        if name1 in pattern_group and name2 in pattern_group:
            return score
    
    # Fuzzy matching for similar roots
    root1 = _extract_root_word(name1)
    root2 = _extract_root_word(name2)
    
    if root1 and root2:
        if root1 == root2:
            return 0.85
        elif root1 in root2 or root2 in root1:
            return 0.75
    
    # Substring matching
    if name1 in name2 or name2 in name1:
        return 0.6
    
    # Character similarity (for typos/variations)
    char_similarity = _character_similarity(name1, name2)
    if char_similarity > 0.8:
        return 0.5
    
    return 0.0

def _extract_root_word(column_name: str) -> str:
    """Extract root business word from column name"""
    # Remove common suffixes
    suffixes = ['_id', '_no', '_code', '_number', 'id', 'no', 'code', 'number']
    
    root = column_name
    for suffix in suffixes:
        if root.endswith(suffix):
            root = root[:-len(suffix)]
            break
    
    return root.strip('_')

def _character_similarity(str1: str, str2: str) -> float:
    """Calculate character-level similarity (simple Jaccard similarity)"""
    if not str1 or not str2:
        return 0.0
    
    set1 = set(str1)
    set2 = set(str2)
    
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
    
    return intersection / union if union > 0 else 0.0

def _make_json_serializable(obj):
    """Convert complex objects to JSON-serializable format"""
    if obj is ...:  # Handle ellipsis objects
        return None
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {key: _make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [_make_json_serializable(item) for item in obj]
    elif type(obj).__module__ == "numpy":
        # numpy scalar (int64/float64/bool_) -> native; numpy array -> list
        if hasattr(obj, "tolist"):
            return obj.tolist()
        return obj.item() if hasattr(obj, "item") else obj
    elif hasattr(obj, "item") and type(obj).__module__ in ("numpy", "pandas"):
        return obj.item()
    elif isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None  # NaN/Inf are not valid JSON
    elif hasattr(obj, 'isoformat'):  # Any date-like object
        return obj.isoformat()
    elif hasattr(obj, '__dict__'):  # Custom objects
        return str(obj)
    else:
        return obj


# ==========================================
# ENHANCEMENT: LLM-Based Table Context Analysis
# ==========================================

def _fetch_sample_data(client, table_ref: str, total_rows: int = None, total_columns: int = None) -> List[Dict[str, Any]]:
    """
    Fetch sample data from BigQuery table for LLM analysis with adaptive sampling.

    Adaptive strategy for large files:
    - Small tables (<10K rows): 20 sample rows
    - Medium tables (10K-1M rows): 10 sample rows
    - Large tables (>1M rows): 5 sample rows
    - Wide tables (>50 columns): Schema-only mode (0 rows)

    Args:
        client: BigQuery client
        table_ref: Table reference (project.dataset.table)
        total_rows: Total row count (for adaptive sampling)
        total_columns: Total column count (for wide table detection)

    Returns:
        List of row dictionaries with serialized values
    """
    try:
        # Determine adaptive sample size
        if total_columns and total_columns > config.LLM_MAX_COLUMNS_FOR_SAMPLES:
            # Schema-only mode for very wide tables
            logger.info(f"  Schema-only mode: {total_columns} columns > {config.LLM_MAX_COLUMNS_FOR_SAMPLES} threshold")
            return []

        if total_rows:
            if total_rows < 10000:
                limit = config.LLM_SAMPLE_ROWS_SMALL  # 20 rows
            elif total_rows < 1000000:
                limit = config.LLM_SAMPLE_ROWS_MEDIUM  # 10 rows
            else:
                limit = config.LLM_SAMPLE_ROWS_LARGE  # 5 rows
        else:
            limit = config.LLM_SAMPLE_ROWS_MEDIUM  # Default to medium

        sample_query = f"SELECT * FROM `{table_ref}` LIMIT {limit}"
        sample_result = client.query(sample_query).result()

        sample_rows = []
        for row in sample_result:
            # Convert Row to dict and make serializable
            row_dict = dict(row)
            serializable_row = {k: _make_json_serializable(v) for k, v in row_dict.items()}
            sample_rows.append(serializable_row)

        logger.info(f"  Fetched {len(sample_rows)} sample rows from {_extract_table_name(table_ref)} (adaptive: {limit} for {total_rows or 'unknown'} rows)")
        return sample_rows

    except Exception as e:
        logger.warning(f"  Failed to fetch sample data from {table_ref}: {e}")
        return []


def _is_key_like_column(col_name: str, col_info: Dict[str, Any]) -> bool:
    """
    Determine if a column is a good composite key candidate.

    Good candidates:
    - Have meaningful uniqueness (not 100% = single PK, not <20% = categorical)
    - Low null percentage
    - Key-like names (id, code, number, date, key)
    - Not free-text fields (description, comment, notes)

    Args:
        col_name: Column name
        col_info: Column statistics

    Returns:
        True if column is a good composite key candidate
    """
    stats = col_info.get("stats", {})
    data_type = col_info.get("data_type", "")

    # Get statistics
    uniqueness = stats.get("uniqueness_percentage", 0)
    null_pct = stats.get("null_percentage", 100)

    # Filter 1: Uniqueness range (20-90%)
    # Skip columns that are too unique (single PK) or too repetitive (categorical)
    if uniqueness < config.COMPOSITE_KEY_MIN_UNIQUENESS or uniqueness > config.COMPOSITE_KEY_MAX_UNIQUENESS:
        return False

    # Filter 2: Null percentage (<20%)
    if null_pct > config.COMPOSITE_KEY_MAX_NULL_PCT:
        return False

    # Filter 3: Skip text/blob fields (likely descriptions)
    if data_type in ["TEXT", "STRING"] and stats.get("avg_length", 0) > 100:
        return False

    # Filter 4: Column name patterns (positive signals)
    name_lower = col_name.lower()
    key_indicators = ["id", "key", "code", "number", "no", "date", "time", "seq", "index"]

    # Filter 5: Skip obvious non-key columns (negative signals)
    skip_indicators = ["description", "desc", "comment", "note", "name", "text", "address", "email"]

    has_key_pattern = any(indicator in name_lower for indicator in key_indicators)
    has_skip_pattern = any(indicator in name_lower for indicator in skip_indicators)

    # Accept if has key pattern and no skip pattern
    # OR if has good uniqueness characteristics even without key pattern
    return (has_key_pattern and not has_skip_pattern) or (uniqueness >= 40 and not has_skip_pattern)


def _filter_composite_key_candidates(table_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter table columns to only include good composite key candidates.

    Uses smart filtering to reduce combinations tested:
    - Only columns with 20-90% uniqueness
    - Only columns with <20% nulls
    - Prioritize "key-like" column names
    - Skip description/text fields
    - Return top N candidates

    Args:
        table_data: Full table analysis data

    Returns:
        Filtered column metadata with only key candidates
    """
    candidates = []

    for col_name, col_info in table_data.get("columns", {}).items():
        if _is_key_like_column(col_name, col_info):
            stats = col_info.get("stats", {})
            candidates.append({
                "name": col_name,
                "info": col_info,
                "uniqueness": stats.get("uniqueness_percentage", 0),
                "null_pct": stats.get("null_percentage", 100)
            })

    # Sort by uniqueness (descending) and take top N
    candidates.sort(key=lambda x: (-x["uniqueness"], x["null_pct"]))
    top_candidates = candidates[:config.MAX_COMPOSITE_CANDIDATES_PER_TABLE]

    # Build filtered metadata
    filtered_metadata = {}
    for candidate in top_candidates:
        col_name = candidate["name"]
        col_info = candidate["info"]
        stats = col_info.get("stats", {})

        filtered_metadata[col_name] = {
            "data_type": col_info.get("data_type", "UNKNOWN"),
            "uniqueness": stats.get("uniqueness_percentage", 0),
            "null_percentage": stats.get("null_percentage", 0),
            "sample_values": []  # Will be filled from sample data if needed
        }

    logger.info(f"  Filtered {len(table_data.get('columns', {}))} columns → {len(filtered_metadata)} key candidates")

    return filtered_metadata


def _prepare_column_metadata(table_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare column metadata for LLM analysis from table analysis results.

    NOW WITH SMART FILTERING: Only returns key-like columns to reduce LLM token usage.

    Args:
        table_data: Table analysis data from _analyze_individual_table_optimized

    Returns:
        Filtered column metadata dict suitable for LLM input (only key candidates)
    """
    # Use smart filtering to only pass key-like columns to LLM
    return _filter_composite_key_candidates(table_data)


def _analyze_table_context_for_relationships(
    client,
    table_ref: str,
    table_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Analyze table business context using LLM before relationship detection.

    This function adds business intelligence to relationship analysis by:
    1. Fetching sample data
    2. Using LLM to understand table context (authorization-level, claim-level, etc.)
    3. Getting business-aware composite key suggestions

    Args:
        client: BigQuery client
        table_ref: Table reference (project.dataset.table)
        table_data: Table analysis data from _analyze_individual_table_optimized

    Returns:
        {
            "table_context": {
                "detected_level": "authorization_level",
                "confidence": 0.9,
                "reasoning": "...",
                "primary_entity": "authorization"
            },
            "composite_suggestions": {
                "two_column_combos": [["auth_id", "line_number"]],
                "three_column_combos": [...]
            },
            "sample_data": [...] # For potential cross-table analysis
        }
    """

    logger.info(f"  Analyzing business context for: {_extract_table_name(table_ref)}")

    try:
        # Step 1: Fetch sample data for LLM (adaptive sampling based on table size)
        total_rows = table_data.get("total_rows", 0)
        total_columns = table_data.get("total_columns", 0)
        sample_rows = _fetch_sample_data(client, table_ref, total_rows=total_rows, total_columns=total_columns)

        # Schema-only mode OR no data available
        if not sample_rows and total_columns <= config.LLM_MAX_COLUMNS_FOR_SAMPLES:
            logger.warning(f"  No sample data available for {table_ref}, skipping LLM analysis")
            return _get_fallback_context(table_data)

        # Step 2: Prepare column metadata
        column_metadata = _prepare_column_metadata(table_data)

        # Step 3: Call LLM to analyze context and suggest composite keys
        max_composite_size = getattr(config, 'MAX_COMPOSITE_KEY_SIZE', 3)

        llm_analysis = suggest_composite_keys_with_llm(
            table_reference=table_ref,
            column_metadata=column_metadata,
            sample_rows=sample_rows,
            max_composite_size=max_composite_size
        )

        logger.info(f"  ✓ Context detected: {llm_analysis['table_context']['detected_level']} "
                   f"(confidence: {llm_analysis['table_context']['confidence']*100:.0f}%)")

        return {
            "table_context": llm_analysis["table_context"],
            "composite_suggestions": llm_analysis,
            "sample_data": sample_rows[:5]  # Keep first 5 for cross-table analysis
        }

    except Exception as e:
        logger.error(f"  LLM context analysis failed for {table_ref}: {e}")
        return _get_fallback_context(table_data)


def _get_fallback_context(table_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fallback context when LLM analysis fails.
    Uses statistical analysis only.
    """
    return {
        "table_context": {
            "detected_level": "unknown",
            "confidence": 0.3,
            "reasoning": "LLM analysis unavailable, using statistical analysis only",
            "primary_entity": "unknown"
        },
        "composite_suggestions": {
            "two_column_combos": [],
            "three_column_combos": [],
            "four_column_combos": []
        },
        "sample_data": []
    }


def _suggest_cross_table_relationships_with_llm(
    source_table_ref: str,
    target_table_ref: str,
    source_context: Dict[str, Any],
    target_context: Dict[str, Any],
    source_columns: Dict[str, Any],
    target_columns: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Use LLM to suggest which columns should have foreign key relationships
    based on business logic and data patterns.

    Args:
        source_table_ref: Source table reference
        target_table_ref: Target table reference
        source_context: Context analysis for source table
        target_context: Context analysis for target table
        source_columns: Column metadata for source table
        target_columns: Column metadata for target table

    Returns:
        {
            "should_relate": true/false,
            "suggested_relationships": [
                {
                    "source_column": "member_id",
                    "target_column": "member_id",
                    "relationship_type": "foreign_key",
                    "business_reasoning": "Claims must reference member records",
                    "confidence": 0.95
                }
            ]
        }
    """

    try:
        from google import genai
        from google.genai import types

        # Initialize Gemini client
        client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )
        model = config.AGENT_MODEL

        # Extract table names
        source_name = _extract_table_name(source_table_ref)
        target_name = _extract_table_name(target_table_ref)

        # Prepare column summaries (only include key columns to reduce tokens)
        source_key_cols = _summarize_key_columns(source_columns)
        target_key_cols = _summarize_key_columns(target_columns)

        # Build prompt
        prompt = f"""You are a healthcare data expert analyzing potential foreign key relationships between two tables.

**SOURCE TABLE:** {source_name}
- Level: {source_context['table_context']['detected_level']}
- Entity: {source_context['table_context']['primary_entity']}
- Context: {source_context['table_context']['business_context']}
- Key Columns: {json.dumps(source_key_cols, indent=2)}

**TARGET TABLE:** {target_name}
- Level: {target_context['table_context']['detected_level']}
- Entity: {target_context['table_context']['primary_entity']}
- Context: {target_context['table_context']['business_context']}
- Key Columns: {json.dumps(target_key_cols, indent=2)}

**TASK:**
Analyze whether these tables should have foreign key relationships based on:
1. Business logic (e.g., claims reference members, authorizations reference providers)
2. Healthcare domain knowledge
3. Column naming patterns and data types
4. Table granularity levels

**CRITICAL: Return ONLY valid JSON. No markdown, no explanations, no code blocks.**

**REQUIRED OUTPUT FORMAT:**
{{
  "should_relate": true,
  "business_reasoning": "Claims typically reference member demographic information",
  "suggested_relationships": [
    {{
      "source_column": "member_id",
      "target_column": "member_id",
      "relationship_type": "foreign_key",
      "business_reasoning": "Claims must reference member records to identify patient",
      "confidence": 0.95,
      "expected_cardinality": "many-to-one"
    }}
  ]
}}

If tables should NOT be related, return:
{{
  "should_relate": false,
  "business_reasoning": "These tables operate at different business contexts with no logical relationship"
}}

Return your analysis now:"""

        logger.info(f"  Requesting LLM analysis for relationship: {source_name} -> {target_name}")

        # Call LLM
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json"
                )
            )
        except (TypeError, AttributeError):
            # Fallback for older SDK
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1)
            )

        # Extract response
        if hasattr(response, 'text'):
            response_text = response.text.strip()
        elif hasattr(response, 'candidates') and len(response.candidates) > 0:
            response_text = response.candidates[0].content.parts[0].text.strip()
        else:
            raise ValueError("Unable to extract text from LLM response")

        # Extract JSON
        json_text = _extract_json_from_response(response_text)
        llm_suggestions = json.loads(json_text)

        logger.info(f"  LLM suggests relationship: {llm_suggestions.get('should_relate', False)}")

        return llm_suggestions

    except Exception as e:
        logger.warning(f"  LLM relationship suggestion failed for {source_name} -> {target_name}: {e}")
        # Return empty result to fall back to statistical matching
        return {
            "should_relate": False,
            "business_reasoning": "LLM analysis unavailable",
            "suggested_relationships": []
        }


def _summarize_key_columns(columns: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Summarize key columns for LLM prompt (reduce token usage).
    Only include PK candidates and high-cardinality columns.
    """
    key_cols = []

    for col_name, col_info in columns.items():
        stats = col_info.get("stats", {})
        uniqueness = stats.get("uniqueness_percentage", 0)
        pk_score = col_info.get("pk_score", 0)

        # Include if: PK candidate OR high cardinality OR ID-like name
        if (pk_score >= 0.5 or uniqueness >= 80 or
            any(keyword in col_name.lower() for keyword in ['id', 'key', 'number', 'code'])):
            key_cols.append({
                "name": col_name,
                "data_type": col_info.get("data_type", "UNKNOWN"),
                "uniqueness": round(uniqueness, 1),
                "pk_score": round(pk_score, 2)
            })

    return key_cols[:10]  # Limit to top 10 to reduce tokens


def _extract_json_from_response(text: str) -> str:
    """
    Extract JSON from LLM response, handling markdown code blocks.
    """
    import re

    # Remove markdown code blocks if present
    if "```" in text:
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    # Find JSON object boundaries
    start = text.find('{')
    end = text.rfind('}')

    if start != -1 and end != -1 and end > start:
        text = text[start:end+1]

    return text.strip()


def _generate_mock_enhanced_results(tables: List[str], analysis_depth: str) -> Dict[str, Any]:
    """Generate mock results for the enhanced analysis"""
    
    return {
        "status": "success",
        "analysis_timestamp": int(time.time()),
        "analysis_depth": analysis_depth,
        "tables_analyzed": len(tables),
        "table_details": {
            "customers": {
                "table_reference": tables[0] if tables else "mock_customers",
                "total_rows": 1000,
                "total_columns": 5,
                "composite_keys": {
                    "2_column_combinations": [
                        {
                            "columns": ["customer_id", "email"],
                            "uniqueness_percentage": 99.8,
                            "combination_score": 0.9
                        }
                    ]
                },
                "column_classifications": {
                    "customer_id": {
                        "pk": "yes",
                        "fk": "no",
                        "associated_files": [],
                        "ak": [
                            {
                                "key_set": ["customer_id", "email"],
                                "uniqueness_percentage": 99.8,
                                "combination_score": 0.9
                            }
                        ]
                    },
                    "email": {
                        "pk": "no",
                        "fk": "no", 
                        "associated_files": [],
                        "ak": [
                            {
                                "key_set": ["customer_id", "email"],
                                "uniqueness_percentage": 99.8,
                                "combination_score": 0.9
                            }
                        ]
                    }
                }
            }
        },
        "cross_table_relationships": [
            {
                "source_table": "orders",
                "source_column": "customer_id",
                "target_table": "customers",
                "target_column": "customer_id",
                "relationship_type": "foreign_key",
                "confidence_score": 0.95
            }
        ],
        "processing_stats": {
            "total_processing_time": 5.2,
            "tables_processed": len(tables),
            "relationships_found": 1
        },
        "processing_mode": "mock"
    }