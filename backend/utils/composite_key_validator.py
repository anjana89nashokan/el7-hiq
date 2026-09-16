# utils/composite_key_validator.py
"""
BigQuery Composite Key Validation Module
Validates uniqueness of LLM-suggested composite key combinations
"""

import logging
from typing import Dict, Any, List
from utils import local_warehouse as bigquery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def validate_composite_keys_in_bigquery(
    client: bigquery.Client,
    table_reference: str,
    composite_key_combos: Dict[str, List[List[str]]],
    total_rows: int
) -> Dict[str, Any]:
    """
    Validate uniqueness of LLM-suggested composite keys using ONE optimized BigQuery query.
    
    Args:
        client: BigQuery client
        table_reference: Table to analyze (project.dataset.table)
        composite_key_combos: {
          "two_column_combos": [["col1", "col2"], ["col3", "col4"]],
          "three_column_combos": [["col1", "col2", "col3"]],
          "four_column_combos": [["col1", "col2", "col3", "col4"]]
        }
        total_rows: Total row count from table
    
    Returns:
        {
          "two_column_results": [
            {
              "columns": ["col1", "col2"],
              "distinct_count": 998,
              "uniqueness_percentage": 99.8,
              "is_unique": true,
              "is_candidate": true
            }
          ],
          "three_column_results": [...],
          "four_column_results": [...]
        }
    """
    
    try:
        # Build single optimized query for all combinations
        select_parts = ["COUNT(*) AS total_rows"]
        
        combo_mapping = {}  # Track combo name to original columns
        
        # Process 2-column combinations
        for combo in composite_key_combos.get("two_column_combos", []):
            combo_key = _generate_combo_key(combo, "two_col")
            combo_mapping[combo_key] = combo
            select_parts.append(_build_distinct_count_clause(combo, combo_key))
        
        # Process 3-column combinations
        for combo in composite_key_combos.get("three_column_combos", []):
            combo_key = _generate_combo_key(combo, "three_col")
            combo_mapping[combo_key] = combo
            select_parts.append(_build_distinct_count_clause(combo, combo_key))
        
        # Process 4-column combinations (if present)
        for combo in composite_key_combos.get("four_column_combos", []):
            combo_key = _generate_combo_key(combo, "four_col")
            combo_mapping[combo_key] = combo
            select_parts.append(_build_distinct_count_clause(combo, combo_key))
        
        # Build and execute query
        sql = f"SELECT {', '.join(select_parts)} FROM `{table_reference}`"
        
        logger.info(f"Validating {len(combo_mapping)} composite key combinations")
        logger.debug(f"Validation query: {sql[:500]}...")
        
        query_job = client.query(sql)
        row = next(iter(query_job.result()))
        
        # Parse results
        results = {
            "two_column_results": [],
            "three_column_results": [],
            "four_column_results": []
        }
        
        for combo_key, original_combo in combo_mapping.items():
            distinct_count = row.get(combo_key, 0)
            uniqueness = (distinct_count / total_rows * 100) if total_rows > 0 else 0
            
            result = {
                "columns": original_combo,
                "distinct_count": int(distinct_count),
                "total_rows": int(total_rows),
                "uniqueness_percentage": round(uniqueness, 2),
                "is_unique": uniqueness >= 99.9,
                "is_candidate": uniqueness >= 98.0,
                "duplicate_count": int(total_rows - distinct_count)
            }
            
            # Categorize by combo size
            if combo_key.startswith("two_col_"):
                results["two_column_results"].append(result)
            elif combo_key.startswith("three_col_"):
                results["three_column_results"].append(result)
            elif combo_key.startswith("four_col_"):
                results["four_column_results"].append(result)
        
        # Sort results by uniqueness (descending)
        for key in results.keys():
            results[key].sort(key=lambda x: x["uniqueness_percentage"], reverse=True)
        
        logger.info(f"Validation complete. Found {len([r for r in results['two_column_results'] if r['is_candidate']])} viable 2-col combos")
        
        return results
        
    except Exception as e:
        logger.error(f"BigQuery validation failed: {e}")
        return {
            "error": str(e),
            "two_column_results": [],
            "three_column_results": [],
            "four_column_results": []
        }


def _generate_combo_key(columns: List[str], prefix: str) -> str:
    """
    Generate a safe SQL alias for a column combination.
    Example: ["member_id", "service_date"] -> "two_col_member_id_service_date"
    """
    # Sanitize column names for SQL alias
    safe_names = [col.replace("`", "").replace(" ", "_") for col in columns]
    combo_name = "_".join(safe_names[:3])  # Limit length
    
    # Ensure alias is not too long (BigQuery limit is 128 chars)
    if len(combo_name) > 60:
        combo_name = combo_name[:60]
    
    return f"{prefix}_{combo_name}"


def _build_distinct_count_clause(columns: List[str], alias: str) -> str:
    """
    Build SQL clause to count distinct combinations of columns.
    Handles NULLs by converting to string and concatenating with separator.
    
    Example:
        Input: ["member_id", "service_date"]
        Output: COUNT(DISTINCT CONCAT(IFNULL(CAST(`member_id` AS STRING), ''), '|', 
                                       IFNULL(CAST(`service_date` AS STRING), ''))) AS combo_alias
    """
    
    # Build CONCAT expression with NULL handling
    cast_expressions = []
    for col in columns:
        # Escape column name with backticks
        safe_col = f"`{col.replace('`', '')}`"
        cast_expressions.append(f"IFNULL(CAST({safe_col} AS STRING), '')")
    
    # Join with separator
    concat_expr = ", '|', ".join(cast_expressions)
    
    return f"COUNT(DISTINCT CONCAT({concat_expr})) AS {alias}"


def filter_composite_keys_by_context(
    validated_results: Dict[str, Any],
    table_context: Dict[str, Any],
    min_uniqueness: float = 98.0
) -> Dict[str, Any]:
    """
    Filter and rank composite key results based on table context and business rules.
    
    Args:
        validated_results: Results from validate_composite_keys_in_bigquery
        table_context: Context from LLM analysis
        min_uniqueness: Minimum uniqueness threshold
    
    Returns:
        Filtered and ranked composite key recommendations
    """
    
    detected_level = table_context.get("detected_level", "unknown")
    
    # Healthcare-specific anti-patterns
    anti_patterns = {
        "authorization_level": ["member", "patient", "subscriber"],
        "claim_level": ["member", "patient", "authorization"],
        "member_level": ["claim", "authorization", "transaction"],
        "transaction_level": [],  # Usually needs composite keys
    }
    
    avoid_keywords = anti_patterns.get(detected_level, [])
    
    filtered_results = {
        "two_column_recommendations": [],
        "three_column_recommendations": [],
        "four_column_recommendations": []
    }
    
    # Filter each category
    for size_key, recommendation_key in [
        ("two_column_results", "two_column_recommendations"),
        ("three_column_results", "three_column_recommendations"),
        ("four_column_results", "four_column_recommendations")
    ]:
        for result in validated_results.get(size_key, []):
            # Check minimum uniqueness
            if result["uniqueness_percentage"] < min_uniqueness:
                continue
            
            # Check for anti-patterns
            columns = result["columns"]
            has_anti_pattern = False
            
            for col in columns:
                col_lower = col.lower()
                if any(keyword in col_lower for keyword in avoid_keywords):
                    has_anti_pattern = True
                    result["warning"] = f"Contains {detected_level} anti-pattern - may not be appropriate"
                    break
            
            # Add business meaning
            result["business_meaning"] = _infer_business_meaning(columns, detected_level)
            
            # Calculate composite score (uniqueness + business appropriateness)
            appropriateness_score = 0.5 if has_anti_pattern else 1.0
            result["composite_score"] = (result["uniqueness_percentage"] / 100.0) * appropriateness_score
            
            filtered_results[recommendation_key].append(result)
    
    # Sort by composite score
    for key in filtered_results.keys():
        filtered_results[key].sort(key=lambda x: x["composite_score"], reverse=True)
    
    return filtered_results


def _infer_business_meaning(columns: List[str], table_level: str) -> str:
    """
    Infer business meaning of a composite key combination.
    """
    
    col_lower = [c.lower() for c in columns]
    
    # Common patterns
    if any("date" in c for c in col_lower) and any("id" in c for c in col_lower):
        return f"Identifies unique records by entity and time period"
    
    if len(columns) == 2 and all("id" in c or "number" in c for c in col_lower):
        return f"Combination of two identifier fields"
    
    if any("line" in c for c in col_lower):
        return f"Identifies detail/line-item records within {table_level} data"
    
    if any(keyword in " ".join(col_lower) for keyword in ["provider", "location", "facility"]):
        return f"Identifies records by service delivery location/provider"
    
    return f"Composite identifier for {table_level} records"