# utils/profiling_query_tool.py
"""
Profiling Query Tool - Enables followup questions on profiling results

This tool allows users to ask questions about their profiling data without
re-running the expensive profiling operation. It uses the searchable_index
that was generated during the initial profiling.

Usage:
- "Which tables have high null rates?"
- "Show me all primary key recommendations"
- "Which columns are foreign key candidates?"
- "What tables have quality scores below 70%?"
"""

import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ProfilingQueryInput(BaseModel):
    """Input schema for profiling query tool"""
    query_type: str = Field(
        description=(
            "Type of query to run. Options: "
            "'high_null_columns' - Find columns with high null rates (>50%), "
            "'low_quality_tables' - Find tables with quality scores <70%, "
            "'pk_recommendations' - List primary key recommendations, "
            "'composite_keys' - List composite key recommendations, "
            "'fk_candidates' - List foreign key candidates, "
            "'tables_by_context' - Group tables by business context, "
            "'critical_issues' - List critical data quality issues, "
            "'table_summary' - Get summary for all tables, "
            "'specific_table' - Get details for a specific table"
        )
    )
    table_name: Optional[str] = Field(
        default=None,
        description="Table name (required for 'specific_table' query_type)"
    )
    min_null_threshold: Optional[float] = Field(
        default=50.0,
        description="Minimum null percentage threshold (for 'high_null_columns')"
    )
    min_quality_threshold: Optional[float] = Field(
        default=70.0,
        description="Minimum quality score threshold (for 'low_quality_tables')"
    )


class ProfilingQueryOutput(BaseModel):
    """Output schema for profiling query tool"""
    status: str = Field(description="Status: 'success', 'error', or 'no_data'")
    query_type: str = Field(description="Type of query that was executed")
    result: Dict[str, Any] = Field(description="Query results")
    message: str = Field(description="Human-readable message about the results")
    total_results: int = Field(description="Number of results returned")


def query_profiling_context(
    query_type: str,
    table_name: Optional[str] = None,
    min_null_threshold: float = 50.0,
    min_quality_threshold: float = 70.0,
    session_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Query profiling results from session context.

    This tool uses the searchable_index that was generated during profiling
    to quickly answer followup questions without re-running profiling.

    Args:
        query_type: Type of query to run
        table_name: Optional table name for specific queries
        min_null_threshold: Minimum null percentage for high_null_columns
        min_quality_threshold: Minimum quality score for low_quality_tables
        session_context: Session context containing searchable_index

    Returns:
        Dictionary with query results
    """
    logger.info(f"[query_profiling_context] Query type: {query_type}, table: {table_name}")

    # Check if session context has profiling data
    if not session_context:
        logger.warning("[query_profiling_context] No session context provided")
        return {
            "status": "no_data",
            "query_type": query_type,
            "result": {},
            "message": "No profiling data found in session. Please run profiling first.",
            "total_results": 0
        }

    # Extract searchable_index from session context
    # The searchable_index is stored in final_profiling_response.tool_response.searchable_index
    searchable_index = None

    # Try to find searchable_index in different possible locations
    if "final_profiling_response" in session_context:
        profiling_response = session_context["final_profiling_response"]
        if isinstance(profiling_response, dict):
            tool_response = profiling_response.get("tool_response", {})
            searchable_index = tool_response.get("searchable_index")

    # Fallback: check if searchable_index is at root level
    if not searchable_index and "searchable_index" in session_context:
        searchable_index = session_context["searchable_index"]

    if not searchable_index:
        logger.warning("[query_profiling_context] No searchable_index found in session context")
        return {
            "status": "no_data",
            "query_type": query_type,
            "result": {},
            "message": "Profiling index not found. Please run profiling first to generate the searchable index.",
            "total_results": 0
        }

    logger.info(f"[query_profiling_context] Found searchable_index with {len(searchable_index.get('table_summary', {}))} tables")

    # Execute query based on type
    try:
        if query_type == "high_null_columns":
            result = [
                col for col in searchable_index.get("high_null_columns", [])
                if col["null_percentage"] >= min_null_threshold
            ]
            message = f"Found {len(result)} columns with null rate >= {min_null_threshold}%"

        elif query_type == "low_quality_tables":
            tables_by_quality = searchable_index.get("tables_by_quality", {})
            result = {
                "low_quality": tables_by_quality.get("low", []),
                "medium_quality": tables_by_quality.get("medium", []),
                "threshold": min_quality_threshold
            }
            message = f"Found {len(result['low_quality'])} tables with quality score < {min_quality_threshold}%"

        elif query_type == "pk_recommendations":
            result = searchable_index.get("pk_recommendations", {})
            message = f"Found primary key recommendations for {len(result)} tables"

        elif query_type == "composite_keys":
            result = searchable_index.get("composite_key_recommendations", {})
            message = f"Found composite key recommendations for {len(result)} tables"

        elif query_type == "fk_candidates":
            result = searchable_index.get("fk_candidates", [])
            message = f"Found {len(result)} foreign key candidates"

        elif query_type == "tables_by_context":
            result = searchable_index.get("tables_by_context", {})
            total_tables = sum(len(tables) for tables in result.values())
            message = f"Grouped {total_tables} tables into {len(result)} business contexts"

        elif query_type == "critical_issues":
            result = searchable_index.get("critical_issues", [])
            message = f"Found {len(result)} critical data quality issues"

        elif query_type == "table_summary":
            result = searchable_index.get("table_summary", {})
            message = f"Retrieved summary for {len(result)} tables"

        elif query_type == "specific_table":
            if not table_name:
                return {
                    "status": "error",
                    "query_type": query_type,
                    "result": {},
                    "message": "table_name is required for 'specific_table' query",
                    "total_results": 0
                }

            table_summary = searchable_index.get("table_summary", {})
            if table_name not in table_summary:
                return {
                    "status": "error",
                    "query_type": query_type,
                    "result": {},
                    "message": f"Table '{table_name}' not found in profiling results",
                    "total_results": 0
                }

            result = {
                "summary": table_summary[table_name],
                "pk_recommendation": searchable_index.get("pk_recommendations", {}).get(table_name),
                "composite_keys": searchable_index.get("composite_key_recommendations", {}).get(table_name),
                "high_null_columns": [
                    col for col in searchable_index.get("high_null_columns", [])
                    if col["table"] == table_name
                ],
                "critical_issues": [
                    issue for issue in searchable_index.get("critical_issues", [])
                    if issue["table"] == table_name
                ]
            }
            message = f"Retrieved detailed information for table '{table_name}'"

        else:
            return {
                "status": "error",
                "query_type": query_type,
                "result": {},
                "message": f"Unknown query_type: {query_type}",
                "total_results": 0
            }

        return {
            "status": "success",
            "query_type": query_type,
            "result": result,
            "message": message,
            "total_results": len(result) if isinstance(result, (list, dict)) else 1
        }

    except Exception as e:
        logger.error(f"[query_profiling_context] Error executing query: {e}")
        import traceback
        traceback.print_exc()

        return {
            "status": "error",
            "query_type": query_type,
            "result": {},
            "message": f"Error executing query: {str(e)}",
            "total_results": 0
        }
