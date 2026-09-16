"""
Smart Similarity Functions - Agent-driven column matching no embeddings.
"""

import logging
import re
from typing import List, Dict, Any, Optional
from utils import local_warehouse as bigquery
from google.adk.tools import ToolContext
from config.settings import config
from utils.bg_query_utils import get_bigquery_client
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _sanitize_tool_result(fn):
    """Convert numpy/pandas scalars (from SQLite) in a tool's return to native types."""
    import functools

    from utils.json_sanitize import to_native

    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        return to_native(fn(*args, **kwargs))

    return _wrapper


class DARTReference(BaseModel):
    table: str = Field(..., description="Full DART table name (e.g. project.dataset.table)")
    columns: List[str] = Field(..., description="List of column names to match")


@_sanitize_tool_result
def fetch_metadata_tool(
    dart_references: List[DARTReference],  
    source_tables: List[str],
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Fetch metadata (schema + sample values) for source and DART tables.
    Phase 1 tool for semantic matching agent.

    Args:
        dart_references: List of {"table": "...", "columns": ["..."]}
            Example: [
                {
                    "table": "ihg-dart-edw-dev2.DB_WRK.datamap_copilot_test_gender",
                    "columns": ["gender_code", "gender_val"]
                }
            ]
        source_tables: List of BigQuery table names to analyze
            Example: [
                "ust-genai-pa-poc-gcp.DATAMAP_COPILOT.datamap_accountdata_46fd98ee",
                "ust-genai-pa-poc-gcp.DATAMAP_COPILOT.datamap_orders_xyz789"
            ]
        tool_context: ADK tool context

    Returns:
        Dict with metadata for source and DART tables
    """
    logger.info("=" * 80)
    logger.info("SMART SIMILARITY ANALYSIS STARTED")
    logger.info("=" * 80)
    logger.info(f"DART References: {len(dart_references)} tables")
    logger.info(f"Source Tables: {len(source_tables)} tables")
    
    try:
        if not dart_references:
            error_msg = "No DART references provided"
            logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "message": error_msg,
                "matches": [],
                "summary": {}
            }
        
        if not source_tables:
            error_msg = "No source tables provided"
            logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "message": error_msg,
                "matches": [],
                "summary": {}
            }

        client = get_bigquery_client()
        logger.info("✓ BigQuery client initialized")

        # Retrieve dart_dataset_id from tool_context/session state
        dart_dataset_id = config.DART_DATASET_ID if hasattr(config, 'DART_DATASET_ID') else config.BQ_DATASET_ID  # Default
        if tool_context:
            # Try tool_context.state first
            if hasattr(tool_context, 'state'):
                dart_dataset = tool_context.state.get('dart_dataset_id')
                if dart_dataset:
                    dart_dataset_id = dart_dataset
                    logger.info(f"[fetch_metadata_tool] dart_dataset_id: Using dart_dataset_id from tool_context.state = {dart_dataset_id}")
            # Try session.state as fallback
            elif hasattr(tool_context, 'session') and tool_context.session:
                dart_dataset = tool_context.session.state.get('dart_dataset_id')
                if dart_dataset:
                    dart_dataset_id = dart_dataset
                    logger.info(f"[fetch_metadata_tool] dart_dataset_id: Using dart_dataset_id from session.state = {dart_dataset_id}")

        if dart_dataset_id == (config.DART_DATASET_ID if hasattr(config, 'DART_DATASET_ID') else config.BQ_DATASET_ID):
            logger.info(f"[fetch_metadata_tool] dart_dataset_id: Using default dataset_id from config = {dart_dataset_id}")
        
        logger.info(f"Fetching metadata for {len(source_tables)} source tables...")
        source_tables_metadata = []
        
        for table_name in source_tables:
            if '.' in table_name:
                full_ref = table_name
            else:
                full_ref = f"{config.PROJECT_ID}.{config.DATASET_ID}.{table_name}"
            
            logger.info(f"  Processing: {full_ref}")
            
            try:
                table_info = get_table_schema_and_sample(full_ref, client)
                table_info["table_name"] = table_name
                table_info["original_file_name"] = table_name
                source_tables_metadata.append(table_info)
                logger.info(f"    ✓ Retrieved {len(table_info['columns'])} columns")
            except Exception as e:
                logger.error(f"    ❌ Failed to get metadata: {e}")
                continue
        
        if not source_tables_metadata:
            error_msg = f"Could not retrieve metadata for any of the {len(source_tables)} source tables"
            logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "message": error_msg,
                "matches": [],
                "summary": {}
            }
        
        logger.info(f"✓ Successfully retrieved metadata for {len(source_tables_metadata)} tables")

        logger.info(f"Fetching DART metadata for {len(dart_references)} references...")

        # PREPROCESS: Add full DART prefix to table names (with dataset override if provided)
        logger.info(f"[fetch_metadata_tool] DATASET_OVERRIDE: Passing dataset_id = {dart_dataset_id} to preprocess_dart_references")
        dart_references = preprocess_dart_references(dart_references, dataset_id_override=dart_dataset_id)
        logger.info(f"Preprocessed DART references: {dart_references}")

        dart_metadata = get_dart_tables_metadata(dart_references, client)
        
        if not dart_metadata:
            error_msg = "Could not retrieve DART table metadata"
            logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "message": error_msg,
                "matches": [],
                "summary": {}
            }
        
        logger.info(f"✓ Retrieved metadata for {len(dart_metadata)} DART columns")
        
        logger.info("Building analysis context...")
        analysis_context = build_analysis_context(
            source_tables_metadata,
            dart_metadata,
            client
        )
        
        logger.info("✓ Metadata fetched successfully")
        logger.info("=" * 80)

        # Return metadata for semantic analysis (Phase 1)
        return {
            "status": "success",
            "dart_references_analyzed": len(dart_metadata),
            "source_tables_analyzed": len(source_tables_metadata),
            "source_tables": analysis_context["source_tables"],
            "dart_references": analysis_context["dart_references"]
        }
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ CRITICAL ERROR in smart_column_similarity_tool")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        logger.exception("Full traceback:")
        logger.error("=" * 80)
        
        return {
            "status": "error",
            "message": f"Analysis failed: {str(e)}",
            "error_type": type(e).__name__,
            "matches": [],
            "summary": {}
        }


def get_table_schema_and_sample(
    table_ref: str,
    client: bigquery.Client,
    sample_size: int = 10
) -> Dict[str, Any]:
    """Get table schema and sample data for agent analysis."""
    logger.info(f"    Getting schema and samples for: {table_ref}")
    
    try:
        table = client.get_table(table_ref)
        logger.info(f"      ✓ Table exists with {len(table.schema)} columns")
        
        columns_info = []
        for field in table.schema:
            col_name = field.name
            col_type = field.field_type
            
            sample_query = f"""
            SELECT DISTINCT `{col_name}` as value
            FROM `{table_ref}`
            WHERE `{col_name}` IS NOT NULL
              AND TRIM(CAST(`{col_name}` AS STRING)) != ''
            LIMIT {sample_size}
            """
            
            try:
                sample_results = client.query(sample_query).result()
                sample_values = [str(row.value) for row in sample_results]
                logger.debug(f"      ✓ {col_name}: {len(sample_values)} sample values")
            except Exception as e:
                logger.warning(f"      ⚠ Could not get sample values for {col_name}: {e}")
                sample_values = []
            
            columns_info.append({
                "name": col_name,
                "type": col_type,
                "sample_values": sample_values
            })
        
        logger.info(f"      ✓ Retrieved metadata for {len(columns_info)} columns")
        
        return {
            "full_table_reference": table_ref,
            "columns": columns_info
        }
        
    except Exception as e:
        logger.error(f"      ❌ Error getting schema for {table_ref}: {e}")
        logger.exception("Full traceback:")
        raise


def preprocess_dart_references(dart_references: List[Dict[str, Any]], dataset_id_override: str = None) -> List[Dict[str, Any]]:
    """
    Add full project.dataset prefix to DART table names if not present.

    Args:
        dart_references: List of dicts with 'table' and 'columns' keys
        dataset_id_override: Optional dataset ID override (for testing different datasets)

    Returns:
        Updated list with full table references
    """
    processed = []

    # Use override dataset if provided, otherwise use default DART dataset
    dart_dataset_id = dataset_id_override if dataset_id_override else (
        config.DART_DATASET_ID if hasattr(config, 'DART_DATASET_ID') else config.BQ_DATASET_ID
    )
    logger.info(f"[preprocess_dart_references] DATASET_OVERRIDE: Using dataset_id = {dart_dataset_id}")

    for ref in dart_references:
        # Skip invalid refs - they'll be caught by validation in get_dart_tables_metadata
        if not isinstance(ref, dict) or "table" not in ref:
            processed.append(ref)
            continue

        table_name = ref.get("table", "")

        # Check if already has full reference (project.dataset.table format)
        if table_name.count('.') >= 2:
            parts = table_name.split(".")  # [project, dataset, table]
            project = parts[0]
            table_only = ".".join(parts[2:])  # usually parts[2]
            full_table_ref = f"{project}.{dart_dataset_id}.{table_only}"
            logger.info(f"  Forced dataset override: {table_name} → {full_table_ref}")
        else:
            # Add DART prefix with override dataset if provided
            dart_project_id = config.DART_PROJECT_ID if hasattr(config, 'DART_PROJECT_ID') else config.PROJECT_ID
            full_table_ref = f"{dart_project_id}.{dart_dataset_id}.{table_name}"
            logger.info(f"  Added DART prefix: {table_name} → {full_table_ref}")

        processed.append({
            "table": full_table_ref,
            "columns": ref.get("columns", [])
        })

    return processed


def get_dart_tables_metadata(
    dart_references: List[Dict[str, Any]],
    client: bigquery.Client
) -> List[Dict[str, Any]]:
    """Get metadata for DART reference tables."""
    dart_metadata = []
    
    for ref in dart_references:
        table = ref["table"]
        logger.info(f"  Processing DART table: {table}")
        
        for column in ref["columns"]:
            logger.info(f"    Column: {column}")
            
            try:
                table_obj = client.get_table(table)
                
                col_type = "UNKNOWN"
                for field in table_obj.schema:
                    if field.name == column:
                        col_type = field.field_type
                        break
                
                logger.info(f"      Type: {col_type}")
                
                sample_query = f"""
                SELECT DISTINCT `{column}` as value
                FROM `{table}`
                WHERE `{column}` IS NOT NULL
                  AND TRIM(CAST(`{column}` AS STRING)) != ''
                LIMIT 10
                """
                
                sample_results = client.query(sample_query).result()
                sample_values = [str(row.value) for row in sample_results]
                
                logger.info(f"      ✓ Retrieved {len(sample_values)} sample values")
                
                dart_metadata.append({
                    "table": table,
                    "column": column,
                    "type": col_type,
                    "sample_values": sample_values
                })
                
            except Exception as e:
                logger.error(f"      ❌ Error getting DART metadata for {table}.{column}: {e}")
                logger.exception("Full traceback:")
                dart_metadata.append({
                    "table": table,
                    "column": column,
                    "type": "UNKNOWN",
                    "sample_values": [],
                    "error": str(e)
                })
    
    return dart_metadata


def build_analysis_context(
    session_tables: List[Dict],
    dart_metadata: List[Dict],
    client: bigquery.Client
) -> Dict[str, Any]:
    """Build structured context for agent to analyze."""
    logger.info("  Building analysis context...")
    
    context = {
        "source_tables": [
            {
                "file_name": table.get("original_file_name", table["table_name"]),
                "table_name": table["table_name"],
                "full_reference": table["full_table_reference"],
                "columns": [
                    {
                        "name": col["name"],
                        "type": col["type"],
                        "sample_values": col["sample_values"][:5]
                    }
                    for col in table["columns"]
                ]
            }
            for table in session_tables
        ],
        "dart_references": [
            {
                "table": dart["table"],
                "column": dart["column"],
                "type": dart["type"],
                "sample_values": dart["sample_values"][:5]
            }
            for dart in dart_metadata
        ]
    }
    
    logger.info(f"    ✓ Context built: {len(context['source_tables'])} source tables, {len(context['dart_references'])} DART columns")
    
    return context


def has_scd_columns(table_ref: str, client: bigquery.Client) -> bool:
    """
    Check if table has all 4 SCD Type 2 columns required for filtering.

    This function checks the table sent from UI (not automatically checking _cur table).
    If UI sends _cur table name, it will check that table.

    Required columns:
    - RW_EFF_DT: Row Effective Date
    - RW_EXP_DT: Row Expiry Date
    - PRV_EFF_DT: Previous Effective Date
    - PRV_EXP_DT: Previous Expiry Date

    Args:
        table_ref: Full BigQuery table reference (whatever table UI sends)
        client: BigQuery client

    Returns:
        True if all 4 SCD Type 2 columns exist, False otherwise
    """
    try:
        table = client.get_table(table_ref)
        column_names = {field.name.upper() for field in table.schema}

        has_rw_eff_dt = "RW_EFF_DT" in column_names
        has_rw_exp_dt = "RW_EXP_DT" in column_names
        has_prv_eff_dt = "PRV_EFF_DT" in column_names
        has_prv_exp_dt = "PRV_EXP_DT" in column_names

        result = has_rw_eff_dt and has_rw_exp_dt and has_prv_eff_dt and has_prv_exp_dt

        logger.info(f"[SCD Check] Table {table_ref}:")
        logger.info(f"  RW_EFF_DT={has_rw_eff_dt}, RW_EXP_DT={has_rw_exp_dt}")
        logger.info(f"  PRV_EFF_DT={has_prv_eff_dt}, PRV_EXP_DT={has_prv_exp_dt}")
        logger.info(f"  Has all 4 SCD columns={result}")

        return result
    except Exception as e:
        logger.warning(f"[SCD Check] Error checking SCD columns for {table_ref}: {e}")
        return False


def format_filter_value(field_type: str, value: Any, operator: str = "=") -> str:
    """
    Format filter value based on BigQuery data type.

    Handles different data types with proper quoting and formatting:
    - STRING/BYTES: Single-quoted with escaping
    - NUMERIC types: Unquoted numbers
    - BOOLEAN: TRUE/FALSE
    - DATE: Single-quoted dates
    - TIMESTAMP/DATETIME: TIMESTAMP() function

    Args:
        field_type: BigQuery field type (STRING, INT64, DATE, etc.)
        value: Filter value (can be single value or list for IN operator)
        operator: SQL operator (=, !=, <, >, <=, >=, IN, LIKE, IS NULL)

    Returns:
        Formatted SQL fragment (e.g., "= 'value'", "IN (1, 2, 3)", "IS NULL")
    """
    field_type = field_type.upper()
    operator = operator.upper()

    # Handle NULL checks
    if value is None or str(value).upper() == "NULL":
        if operator == "=":
            return "IS NULL"
        elif operator in ("!=", "<>"):
            return "IS NOT NULL"

    # STRING, BYTES - Need quoting and escape single quotes
    if field_type in ("STRING", "BYTES"):
        if operator == "IN":
            if isinstance(value, list):
                # Escape single quotes by doubling them
                values = [f"'{str(v).replace(chr(39), chr(39)+chr(39))}'" for v in value]
                return f"IN ({', '.join(values)})"
            else:
                return f"IN ('{str(value).replace(chr(39), chr(39)+chr(39))}')"
        elif operator == "LIKE":
            return f"LIKE '{str(value).replace(chr(39), chr(39)+chr(39))}'"
        else:
            return f"{operator} '{str(value).replace(chr(39), chr(39)+chr(39))}'"

    # NUMERIC types - No quoting needed
    elif field_type in ("INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC", "INTEGER", "FLOAT"):
        if operator == "IN":
            if isinstance(value, list):
                return f"IN ({', '.join(map(str, value))})"
            else:
                return f"IN ({value})"
        else:
            return f"{operator} {value}"

    # BOOLEAN - TRUE/FALSE keywords
    elif field_type in ("BOOL", "BOOLEAN"):
        if isinstance(value, str):
            bool_val = value.upper()
        else:
            bool_val = "TRUE" if value else "FALSE"
        return f"{operator} {bool_val}"

    # DATE - Quoted date strings
    elif field_type == "DATE":
        if operator == "IN":
            if isinstance(value, list):
                values = [f"'{v}'" for v in value]
                return f"IN ({', '.join(values)})"
            else:
                return f"IN ('{value}')"
        else:
            return f"{operator} '{value}'"

    # TIMESTAMP, DATETIME - Use TIMESTAMP() function
    elif field_type in ("TIMESTAMP", "DATETIME"):
        if operator == "IN":
            if isinstance(value, list):
                values = [f"TIMESTAMP('{v}')" for v in value]
                return f"IN ({', '.join(values)})"
            else:
                return f"IN (TIMESTAMP('{value}'))"
        else:
            return f"{operator} TIMESTAMP('{value}')"

    # Default: treat as string
    else:
        logger.warning(f"[Filter] Unknown type {field_type}, treating as STRING")
        return f"{operator} '{str(value).replace(chr(39), chr(39)+chr(39))}'"


def build_filter_conditions(
    filters: List[Dict[str, Any]] = None,
    has_scd: bool = False
) -> str:
    """
    Build WHERE clause from dynamic filters and SCD Type 2 filter.

    Combines:
    1. Static SCD Type 2 filter (if table has RW_EFF_DT and RW_EXP_DT)
    2. Dynamic filters from UI (field name, type, operator, value)

    Args:
        filters: List of filter objects from UI, each containing:
            - fieldname: Column name to filter
            - type: BigQuery data type (STRING, INT64, DATE, etc.)
            - operator: SQL operator (=, !=, <, >, <=, >=, IN, LIKE)
            - value: Filter value
        has_scd: Whether table has SCD Type 2 columns

    Returns:
        WHERE clause conditions string (without "WHERE" keyword)
        Returns empty string if no filters
    """
    conditions = []

    # Add SCD Type 2 filter if applicable
    if has_scd:
        # Using PRV_* columns as per requirement document
        # If DART table has all 4 SCD columns, filter: PRV_EFF_DT < PRV_EXP_DT AND PRV_EXP_DT >= CURRENT_DATE
        conditions.append("PRV_EFF_DT < PRV_EXP_DT")
        conditions.append("PRV_EXP_DT >= CURRENT_DATE")
        logger.info("[Filter] Added SCD Type 2 filter conditions (PRV_* columns)")

    # Add dynamic filters from UI
    if filters:
        logger.info(f"[Filter] Processing {len(filters)} dynamic filters")
        for idx, f in enumerate(filters):
            fieldname = f.get("fieldname")
            field_type = f.get("type", "STRING")
            value = f.get("value")

            # Auto-detect operator: if value is a list/array, default to IN instead of =
            if "operator" in f:
                operator = f.get("operator")
            elif isinstance(value, list):
                operator = "IN"
                logger.info(f"[Filter] Auto-detected IN operator for array value (filter {idx})")
            else:
                operator = "="

            if not fieldname:
                logger.warning(f"[Filter] Skipping filter {idx}: missing fieldname")
                continue

            # Escape field name with backticks (handles reserved words and special chars)
            escaped_field = f"`{fieldname}`"

            try:
                formatted_value = format_filter_value(field_type, value, operator)
                condition = f"{escaped_field} {formatted_value}"
                conditions.append(condition)
                logger.info(f"[Filter] Added filter {idx}: {condition}")
            except Exception as e:
                logger.error(f"[Filter] Error formatting filter {idx}: {e}")
                continue

    result = " AND ".join(conditions) if conditions else ""
    logger.info(f"[Filter] Final WHERE clause: {result if result else '(no filters)'}")
    return result


@_sanitize_tool_result
def compute_overlap_tool(
    dart_table: str,
    dart_column: str,
    source_table: str,
    source_column: str,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Calculate data overlap between source and DART columns with optional filtering.
    Phase 2 tool for overlap validation agent.

    Supports:
    - SCD Type 2 filtering (auto-detected based on RW_EFF_DT/RW_EXP_DT columns)
    - Dynamic filters from UI (field name, type, operator, value)

    Args:
        dart_table (required): Full DART table reference (e.g., ihg-dart-edw-dev2.DB_WRK.gender_lookup)
        dart_column (required): DART column name
        source_table (required): Full source table reference (e.g., project.dataset.table)
        source_column (required): Source column name
        tool_context: ADK tool context

    Returns:
        Dict with overlap statistics including:
            - total_rows: Total rows in source table
            - null_blank_count: Count of null/blank values
            - null_blank_percent: Percentage of null/blank values
            - data_overlap_percent: Percentage of source distinct values found in DART
            - overlap_count: Count of matching distinct values
            - source_distinct_count: Count of distinct values in source
            - sample_matching_values: Sample of matching values
    """
    try:
        # Validate required inputs
        if not all([dart_table, dart_column, source_table, source_column]):
            missing = []
            if not dart_table: missing.append("dart_table")
            if not dart_column: missing.append("dart_column")
            if not source_table: missing.append("source_table")
            if not source_column: missing.append("source_column")

            error_msg = f"Missing required parameters: {', '.join(missing)}"
            logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "error": error_msg,
                "total_rows": 0,
                "null_blank_count": 0,
                "null_blank_percent": 0.0,
                "non_null_count": 0,
                "data_overlap_percent": 0.0,
                "source_distinct_count": 0,
                "dart_distinct_count": 0,
                "overlap_count": 0,
                "sample_matching_values": []
            }

        # Try to get filters from tool_context.state (same pattern as batched version)
        filters = None
        if tool_context:
            if hasattr(tool_context, 'state'):
                filters = tool_context.state.get('similarity_filters')
                if filters:
                    logger.info(f"[compute_overlap_tool] Using filters from tool_context.state: {filters}")
            elif hasattr(tool_context, 'session') and tool_context.session:
                filters = tool_context.session.state.get('similarity_filters')
                if filters:
                    logger.info(f"[compute_overlap_tool] Using filters from session.state: {filters}")

        logger.info("=" * 80)
        logger.info(f"OVERLAP CALCULATION: {source_table}.{source_column} vs {dart_table}.{dart_column}")
        if filters:
            logger.info(f"  With {len(filters)} filter(s): {filters}")

        client = get_bigquery_client()
        result = compute_data_overlap(dart_table, dart_column, source_table, source_column, client, filters)
        logger.info("=" * 80)
        return result

    except Exception as e:
        logger.error(f"❌ Overlap calculation failed: {e}")
        logger.exception("Full traceback:")
        return {
            "status": "error",
            "error": str(e),
            "total_rows": 0,
            "null_blank_count": 0,
            "null_blank_percent": 0.0,
            "non_null_count": 0,
            "data_overlap_percent": 0.0,
            "source_distinct_count": 0,
            "dart_distinct_count": 0,
            "overlap_count": 0,
            "sample_matching_values": []
        }


def compute_data_overlap(
    dart_table: str,
    dart_column: str,
    source_table: str,
    source_column: str,
    client: bigquery.Client,
    filters: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Internal function to calculate data overlap between source and DART columns with optional filtering.

    Supports:
    - SCD Type 2 filtering (auto-detected based on RW_EFF_DT/RW_EXP_DT columns)
    - Dynamic filters from UI (field name, type, operator, value)

    Args:
        dart_table: Full DART table reference
        dart_column: DART column name
        source_table: Full source table reference
        source_column: Source column name
        client: BigQuery client
        filters: Optional list of filter objects for dynamic WHERE clause
                 Each filter should have 'table_name' to match against dart_table

    Returns:
        Dict with overlap statistics
    """
    logger.debug(f"Computing data overlap: {source_column} vs {dart_column}")

    # Check for SCD Type 2 columns in DART table
    has_scd = has_scd_columns(dart_table, client)

    # Filter the filters array to only include those matching the current dart_table
    table_specific_filters = []
    if filters:
        logger.info(f"[Filter Matching] Processing {len(filters)} filter(s) for DART table: {dart_table}")
        for f in filters:
            filter_table_name = f.get("table_name", "")
            # Match if table_name matches the current dart_table
            # Handle both full paths (project.dataset.table) and partial matches
            if filter_table_name and (
                filter_table_name == dart_table or
                dart_table.endswith(f".{filter_table_name}") or
                filter_table_name.endswith(dart_table.split(".")[-1])
            ):
                table_specific_filters.append(f)
                logger.info(f"[Filter Match] ✓ Including filter for field '{f.get('fieldname')}' (table: {filter_table_name})")
            else:
                logger.info(f"[Filter Skip] ✗ Skipping filter for field '{f.get('fieldname')}' (table: {filter_table_name}) - doesn't match {dart_table}")

        logger.info(f"[Filter Matching] Result: {len(table_specific_filters)} filter(s) matched for {dart_table}")

    # Build filter conditions (combines SCD filter + table-specific dynamic filters)
    filter_clause = build_filter_conditions(table_specific_filters, has_scd)

    # Build WHERE clause for dart_values CTE
    dart_where_conditions = [
        f"`{dart_column}` IS NOT NULL",
        f"TRIM(CAST(`{dart_column}` AS STRING)) != ''"
    ]

    # Add filter clause if present
    if filter_clause:
        dart_where_conditions.append(f"({filter_clause})")

    dart_where_clause = " AND ".join(dart_where_conditions)

    logger.info(f"[Overlap Query] DART WHERE clause: {dart_where_clause}")

    query = f"""
    WITH source_stats AS (
        SELECT
            COUNT(*) AS total_rows,
            COUNTIF(
                `{source_column}` IS NULL
                OR TRIM(CAST(`{source_column}` AS STRING)) = ''
            ) AS null_blank_count,
            COUNT(*) - COUNTIF(
                `{source_column}` IS NULL
                OR TRIM(CAST(`{source_column}` AS STRING)) = ''
            ) AS non_null_count
        FROM `{source_table}`
    ),
    dart_values AS (
        SELECT DISTINCT LOWER(TRIM(CAST(`{dart_column}` AS STRING))) AS value
        FROM `{dart_table}`
        WHERE {dart_where_clause}
    ),
    source_values AS (
        SELECT DISTINCT LOWER(TRIM(CAST(`{source_column}` AS STRING))) AS value
        FROM `{source_table}`
        WHERE `{source_column}` IS NOT NULL
          AND TRIM(CAST(`{source_column}` AS STRING)) != ''
    ),
    overlap AS (
        SELECT s.value
        FROM source_values s
        INNER JOIN dart_values d ON s.value = d.value
    )
    SELECT 
        (SELECT total_rows FROM source_stats) AS total_rows,
        (SELECT null_blank_count FROM source_stats) AS null_blank_count,
        (SELECT non_null_count FROM source_stats) AS non_null_count,
        (SELECT COUNT(*) FROM source_values) AS source_distinct_count,
        (SELECT COUNT(*) FROM dart_values) AS dart_distinct_count,
        (SELECT COUNT(*) FROM overlap) AS overlap_count,
        (SELECT ARRAY_AGG(value LIMIT 5) FROM overlap) AS sample_values
    """
    
    try:
        logger.debug(f"  Executing overlap query...")
        result = list(client.query(query).result())[0]
        
        total_rows = result.total_rows or 0
        null_blank_count = result.null_blank_count or 0
        non_null_count = result.non_null_count or 0
        source_distinct = result.source_distinct_count or 0
        dart_distinct = result.dart_distinct_count or 0
        overlap_count = result.overlap_count or 0
        
        null_blank_percent = (
            (null_blank_count / total_rows * 100.0)
            if total_rows > 0
            else 0.0
        )
        
        data_overlap_percent = (
            (overlap_count / source_distinct * 100.0)
            if source_distinct > 0
            else 0.0
        )
        
        sample_values = result.sample_values if result.sample_values else []
        sample_values = [v for v in sample_values if v is not None and v != '']
        
        logger.info(
            f"  ✓ Overlap: {overlap_count}/{source_distinct} ({data_overlap_percent:.1f}%), "
            f"NULL: {null_blank_count}/{total_rows} ({null_blank_percent:.1f}%)"
        )
        
        return {
            "total_rows": total_rows,
            "null_blank_count": null_blank_count,
            "null_blank_percent": round(null_blank_percent, 2),
            "non_null_count": non_null_count,
            "data_overlap_percent": round(data_overlap_percent, 2),
            "source_distinct_count": source_distinct,
            "dart_distinct_count": dart_distinct,
            "overlap_count": overlap_count,
            "sample_matching_values": sample_values[:5]
        }
        
    except Exception as e:
        logger.error(f"  ❌ Data overlap query failed: {e}")
        logger.error(f"  Query: {query}")
        logger.exception("Full traceback:")
        
        return {
            "total_rows": 0,
            "null_blank_count": 0,
            "null_blank_percent": 0.0,
            "non_null_count": 0,
            "data_overlap_percent": 0.0,
            "source_distinct_count": 0,
            "dart_distinct_count": 0,
            "overlap_count": 0,
            "sample_matching_values": [],
            "error": str(e)
        }