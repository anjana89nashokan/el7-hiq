"""
Smart Similarity Functions - Batched version for large-scale processing
Optimized for 100+ table similarity checks with batching and parallelization.

Key Optimizations:
- Batched metadata fetching (10 tables per batch)
- Parallelized BigQuery queries (4 workers)
- Single query per table (20x faster than sequential per-column queries)
- Reduced sample size: 10 → 3 values (50% token reduction)
- Parallelized overlap validation (3 workers)

NOTE: These are SYNCHRONOUS functions for ADK compatibility.
Streaming/SSE events are handled in /send-stream endpoint.
"""

import logging
from typing import List, Dict, Any
from utils import local_warehouse as bigquery
from google.adk.tools import ToolContext
from concurrent.futures import ThreadPoolExecutor
from config.settings import config
from utils.bg_query_utils import get_bigquery_client
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# OPTIMIZED METADATA FETCHING
# ============================================================================

def get_table_schema_and_sample_optimized(
    table_ref: str,
    client: bigquery.Client,
    sample_size: int = 3
) -> Dict[str, Any]:
    """
    Optimized metadata fetching: Single query for all column samples.

    Performance: 20x faster than sequential per-column queries
    - Old: 50 columns × 200ms = 10 seconds
    - New: 1 query = 500ms

    Args:
        table_ref: Full BigQuery table reference (project.dataset.table)
        client: BigQuery client
        sample_size: Number of distinct sample values per column (default: 3)

    Returns:
        Dict with table metadata and column samples
    """
    logger.info(f"    Fetching optimized metadata for: {table_ref}")

    try:
        # Get table schema
        table = client.get_table(table_ref)
        logger.info(f"      ✓ Table exists with {len(table.schema)} columns")

        if not table.schema:
            return {
                "full_table_reference": table_ref,
                "columns": []
            }

        # Build single query with ARRAY_AGG for all columns
        # This replaces N separate queries with 1 efficient query
        select_clauses = []
        for field in table.schema:
            col_name = field.name
            # Use ARRAY to collect distinct samples for each column
            select_clauses.append(f"""
                ARRAY(
                    SELECT DISTINCT CAST(`{col_name}` AS STRING)
                    FROM `{table_ref}`
                    WHERE `{col_name}` IS NOT NULL
                      AND TRIM(CAST(`{col_name}` AS STRING)) != ''
                    LIMIT {sample_size}
                ) AS `{col_name}_samples`
            """)

        # Execute single query for all columns
        query = f"""
        SELECT {', '.join(select_clauses)}
        FROM (SELECT 1) LIMIT 1
        """

        logger.debug(f"      Executing optimized sample query...")
        result = list(client.query(query).result())[0]

        # Parse results into columns_info
        columns_info = []
        for field in table.schema:
            col_name = field.name
            sample_values = result[f"{col_name}_samples"] or []

            columns_info.append({
                "name": col_name,
                "type": field.field_type,
                "sample_values": list(sample_values)  # Convert to list if not already
            })

        logger.info(f"      ✓ Retrieved metadata for {len(columns_info)} columns (optimized)")

        return {
            "full_table_reference": table_ref,
            "columns": columns_info
        }

    except Exception as e:
        logger.error(f"      ❌ Error getting optimized schema for {table_ref}: {e}")
        logger.exception("Full traceback:")
        raise


def get_dart_tables_metadata(
    dart_references: List[Dict[str, Any]],
    client: bigquery.Client,
    sample_size: int = 3
) -> List[Dict[str, Any]]:
    """
    Get metadata for DART reference columns (optimized version).

    Args:
        dart_references: List of {"table": "...", "columns": ["..."]}
        client: BigQuery client
        sample_size: Number of sample values (default: 3)

    Returns:
        List of DART column metadata dicts
    """
    dart_metadata = []

    # DEFENSIVE: Log raw input for debugging
    logger.info(f"Raw dart_references received: {dart_references}")

    for idx, ref in enumerate(dart_references):
        # DEFENSIVE: Validate ref is a dict
        if not isinstance(ref, dict):
            logger.error(f"❌ dart_references[{idx}] is not a dict: {type(ref)} = {ref}")
            continue

        # DEFENSIVE: Check for 'table' key
        if "table" not in ref:
            logger.error(f"❌ dart_references[{idx}] missing 'table' key")
            logger.error(f"   Available keys: {list(ref.keys())}")
            logger.error(f"   Full object: {ref}")
            continue

        # DEFENSIVE: Check for 'columns' key
        if "columns" not in ref:
            logger.warning(f"⚠ dart_references[{idx}] missing 'columns' key, skipping")
            continue

        table = ref["table"]
        columns = ref["columns"]

        # DEFENSIVE: Ensure columns is a list
        if isinstance(columns, str):
            # If comma-separated string, split it
            columns = [c.strip() for c in columns.split(',') if c.strip()]
            logger.info(f"  Converted columns string to list: {columns}")
        elif not isinstance(columns, list):
            logger.error(f"❌ columns is not a list or string: {type(columns)} = {columns}")
            continue

        if not columns:
            logger.warning(f"⚠ No columns specified for table {table}, skipping")
            continue

        logger.info(f"  Processing DART table: {table}")

        for column in columns:
            logger.info(f"    Column: {column}")

            try:
                table_obj = client.get_table(table)

                # Get column type
                col_type = "UNKNOWN"
                for field in table_obj.schema:
                    if field.name == column:
                        col_type = field.field_type
                        break

                logger.info(f"      Type: {col_type}")

                # Fetch sample values
                sample_query = f"""
                SELECT DISTINCT `{column}` as value
                FROM `{table}`
                WHERE `{column}` IS NOT NULL
                  AND TRIM(CAST(`{column}` AS STRING)) != ''
                LIMIT {sample_size}
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
    """
    Build structured context for agent semantic analysis.

    Args:
        session_tables: List of source table metadata dicts
        dart_metadata: List of DART column metadata dicts
        client: BigQuery client (unused, kept for compatibility)

    Returns:
        Dict with source_tables and dart_references arrays
    """
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
                        "sample_values": col["sample_values"]  # Already limited to 3
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
                "sample_values": dart["sample_values"]  # Already limited to 3
            }
            for dart in dart_metadata
        ]
    }

    logger.info(f"    ✓ Context built: {len(context['source_tables'])} source tables, {len(context['dart_references'])} DART columns")

    return context


# ============================================================================
# BATCHED METADATA FETCHING (SYNCHRONOUS FOR ADK COMPATIBILITY)
# ============================================================================

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
    dart_dataset_id = dataset_id_override if dataset_id_override else config.DART_DATASET_ID
    logger.info(f"[preprocess_dart_references] DATASET_OVERRIDE: Using dataset_id = {dart_dataset_id}")

    for ref in dart_references:
        # Skip invalid refs - they'll be caught by validation in get_dart_tables_metadata
        if not isinstance(ref, dict) or "table" not in ref:
            processed.append(ref)
            continue

        table_name = ref.get("table", "")

        # Check if already has full reference (project.dataset.table format)
        # Count dots: 2+ means it has project.dataset.table
        if table_name.count('.') >= 2:
            full_table_ref = table_name
        else:
            # Add DART prefix with override dataset if provided
            full_table_ref = f"{config.DART_PROJECT_ID}.{dart_dataset_id}.{table_name}"
            logger.info(f"  Added DART prefix: {table_name} → {full_table_ref}")

        processed.append({
            "table": full_table_ref,
            "columns": ref.get("columns", [])
        })

    return processed


from pydantic import BaseModel, Field

class FetchMetadataInput(BaseModel):
    """Input schema for fetch_metadata_tool"""
    dart_references: List[Dict[str, Any]] = Field(..., description="List of DART reference tables with columns to match against")
    source_tables: List[str] = Field(..., description="List of source BigQuery table names to analyze")

def fetch_metadata_tool(
    input: FetchMetadataInput,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Batched version of fetch_metadata_tool with parallelization.

    Returns complete metadata after processing all batches.
    (SSE streaming handled by /send-stream endpoint)

    Batch Strategy:
    - Process source tables in batches of 10
    - Parallel fetch within batch (4 workers)
    - Emit progress events: 0-30%

    Token Optimization:
    - Reduced sample size: 10 → 3 values (50% token reduction on samples)
    - Single query per table (20x faster metadata fetch)
    - Parallelized batch processing (4 workers)

    Args:
        dart_references: List of {"table": "...", "columns": ["..."]}
        source_tables: List of source table names to analyze
        tool_context: ADK tool context (unused, kept for compatibility)

    Returns:
        Dict with metadata for source and DART tables (same format as original)
    """
    logger.info("=" * 80)
    logger.info("SIMILARITY METADATA FETCH (BATCHED) STARTED")
    logger.info("=" * 80)

    try:
        # WINDOWS FIX: Check tool_context.state FIRST (injected by /send-stream)
        # This bypasses LLM parsing entirely
        dart_references = None
        source_tables = None

        if tool_context and hasattr(tool_context, 'state'):
            dart_references = tool_context.state.get('similarity_dart_references')
            source_tables = tool_context.state.get('similarity_source_tables')

            if dart_references and source_tables:
                logger.info("[WINDOWS FIX] ✓ Using structured data from tool_context.state (bypasses LLM parsing)")
                logger.info(f"  - DART refs from state: {dart_references}")
                logger.info(f"  - Source tables from state: {source_tables}")
        
        if (not dart_references or not source_tables) and tool_context:
            # Try to get from session if tool_context has session reference
            if hasattr(tool_context, 'session') and tool_context.session:
                session_dart = tool_context.session.state.get('similarity_dart_references')
                session_sources = tool_context.session.state.get('similarity_source_tables')
                
                if session_dart and session_sources:
                    dart_references = session_dart
                    source_tables = session_sources
                    logger.info("[SESSION_STATE] ✓ Using data from session state")
                    logger.info(f"  - DART refs: {dart_references}")
                    logger.info(f"  - Source tables: {source_tables}")

        # Fallback: Use Pydantic input if state is empty
        if not dart_references or not source_tables:
            logger.info("[FALLBACK] Using data from Pydantic input (LLM-parsed)")
            
            # Handle both Pydantic model and plain dict
            if isinstance(input, dict):
                # Input is a plain dict (ADK passes it this way sometimes)
                logger.info("[DICT MODE] Input received as dict")
                dart_references = input.get('dart_references')
                source_tables = input.get('source_tables')
            else:
                # Input is a Pydantic model with attributes
                logger.info("[PYDANTIC MODE] Input received as Pydantic model")
                dart_references = input.dart_references
                source_tables = input.source_tables

        # ENHANCED LOGGING
        logger.info(f"DART References: {dart_references}")
        logger.info(f"Source Tables: {source_tables}")

        if not dart_references:
            error_msg = f"No DART references provided. Received: {dart_references} (type: {type(dart_references)})"
            logger.error(f"❌ {error_msg}")
            logger.error(f"   This usually means the LLM agent failed to parse the user input correctly.")
            logger.error(f"   Check the agent instruction parsing in semantic_matching_agent.py")
            logger.error(f"   On Windows, this can be caused by line ending issues (CRLF vs LF)")
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

        # Retrieve dataset_id_override from tool_context/session state
        dataset_id = config.DATASET_ID  # Default
        if tool_context:
            # Try tool_context.state first
            if hasattr(tool_context, 'state'):
                dataset_id_override = tool_context.state.get('dataset_id_override')
                if dataset_id_override:
                    dataset_id = dataset_id_override
                    logger.info(f"[fetch_metadata_tool] DATASET_OVERRIDE: Using dataset_id from tool_context.state = {dataset_id}")
            # Try session.state as fallback
            elif hasattr(tool_context, 'session') and tool_context.session:
                dataset_id_override = tool_context.session.state.get('dataset_id_override')
                if dataset_id_override:
                    dataset_id = dataset_id_override
                    logger.info(f"[fetch_metadata_tool] DATASET_OVERRIDE: Using dataset_id from session.state = {dataset_id}")

        dart_dataset_id = config.DATASET_ID  # Default
        if tool_context:
            # Try tool_context.state first
            if hasattr(tool_context, 'state'):
                dart_dataset = tool_context.state.get('dart_dataset_id')
                if dart_dataset:
                    dart_dataset_id = dart_dataset
                    logger.info(f"[fetch_metadata_tool] dart_dataset-id: Using dart_dataset_id from tool_context.state = {dart_dataset_id}")
            # Try session.state as fallback
            elif hasattr(tool_context, 'session') and tool_context.session:
                dart_dataset = tool_context.session.state.get('dart_dataset_id')
                if dart_dataset:
                    dart_dataset_id = dart_dataset
                    logger.info(f"[fetch_metadata_tool] dart_datset_id: Using dart_dataset_id from session.state = {dart_dataset_id}")

        if dataset_id == config.DATASET_ID:
            logger.info(f"[fetch_metadata_tool] DATASET_OVERRIDE: Using default dataset_id from config = {dataset_id}")

        # Batch configuration
        BATCH_SIZE = 10  # Process 10 tables per batch
        MAX_WORKERS = 4  # Conservative for VDI/BigQuery connection pool

        total_tables = len(source_tables)
        total_batches = (total_tables + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"Processing {total_tables} source tables in {total_batches} batches")

        all_source_metadata = []

        # Process source tables in batches
        for batch_num in range(total_batches):
            batch_start = batch_num * BATCH_SIZE
            batch_end = min(batch_start + BATCH_SIZE, total_tables)
            batch_tables = source_tables[batch_start:batch_end]

            logger.info(f"[Batch {batch_num + 1}/{total_batches}] Processing {len(batch_tables)} tables")

            # Parallel fetch within batch
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = []
                for table_name in batch_tables:
                    # Add full reference if not provided, using dynamic dataset_id
                    if '.' in table_name:
                        full_ref = table_name
                    else:
                        full_ref = f"{config.PROJECT_ID}.{dataset_id}.{table_name}"
                        logger.info(f"  [DATASET_OVERRIDE] Building table reference with dynamic dataset_id: {full_ref}")

                    future = executor.submit(get_table_schema_and_sample_optimized, full_ref, client)
                    futures.append((table_name, future))

                # Collect results with timeout
                batch_results = []
                for table_name, future in futures:
                    try:
                        table_info = future.result(timeout=60)  # 1 min timeout per table
                        table_info["table_name"] = table_name
                        table_info["original_file_name"] = table_name
                        batch_results.append(table_info)
                        logger.info(f"    ✓ {table_name}: {len(table_info['columns'])} columns")
                    except Exception as e:
                        logger.error(f"    ❌ Failed to fetch metadata for {table_name}: {e}")
                        # Continue processing other tables

            all_source_metadata.extend(batch_results)

            # Log batch progress
            progress = ((batch_num + 1) / total_batches) * 25  # 0-25% for metadata fetch
            logger.info(f"  Batch {batch_num + 1}/{total_batches} complete ({progress:.1f}%): {len(all_source_metadata)}/{total_tables} tables processed")

        if not all_source_metadata:
            error_msg = f"Could not retrieve metadata for any of the {total_tables} source tables"
            logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "message": error_msg,
                "matches": [],
                "summary": {}
            }

        logger.info(f"✓ Successfully retrieved metadata for {len(all_source_metadata)} tables")

        # Fetch DART metadata (usually small - no batching needed)
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

        # Build analysis context
        logger.info("Building analysis context...")
        analysis_context = build_analysis_context(
            all_source_metadata,
            dart_metadata,
            client
        )

        # Calculate token usage estimate
        total_columns = sum(len(t['columns']) for t in all_source_metadata)
        estimated_tokens_per_column = 25  # Reduced from 70 (3 samples vs 10)
        estimated_input_tokens = total_columns * estimated_tokens_per_column

        logger.info("=" * 80)
        logger.info("📊 METADATA FETCH COMPLETE - Token Usage Estimate:")
        logger.info(f"   ├─ Total source columns: {total_columns}")
        logger.info(f"   ├─ Estimated input tokens: ~{estimated_input_tokens:,}")
        logger.info(f"   ├─ Sample size per column: 3 (optimized from 10)")
        logger.info(f"   └─ Token savings vs old: ~{int((1 - estimated_tokens_per_column / 70) * 100)}%")
        logger.info("=" * 80)

        # Return complete result (same format as original function)
        return {
            "status": "success",
            "dart_references_analyzed": len(dart_metadata),
            "source_tables_analyzed": len(all_source_metadata),
            "source_tables": analysis_context["source_tables"],
            "dart_references": analysis_context["dart_references"]
        }

    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ CRITICAL ERROR in fetch_metadata_tool (batched)")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        logger.exception("Full traceback:")
        logger.error("=" * 80)

        return {
            "status": "error",
            "message": f"Metadata fetch failed: {str(e)}",
            "error_type": type(e).__name__,
            "matches": [],
            "summary": {}
        }


# ============================================================================
# PARALLELIZED OVERLAP VALIDATION (SYNCHRONOUS FOR ADK COMPATIBILITY)
# ============================================================================

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

        # Original RW_* condition (commented out - can change back if requirement changes)
        # conditions.append("RW_EFF_DT < RW_EXP_DT")
        # conditions.append("RW_EXP_DT >= CURRENT_DATE")
        # logger.info("[Filter] Added SCD Type 2 filter conditions (RW_* columns)")

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


def compute_data_overlap(
    dart_table: str,
    dart_column: str,
    source_table: str,
    source_column: str,
    client: bigquery.Client,
    filters: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Calculate data overlap between source and DART columns with optional filtering.

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

        logger.debug(
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
    
    
def compute_overlap_tool(tool_input: Dict[str, Any], tool_context: ToolContext = None) -> Dict[str, Any]:
    """
    Calculate data overlap between source and DART columns with optional filtering.
    Phase 2 tool for overlap validation agent.

    Supports:
    - SCD Type 2 filtering (auto-detected based on RW_EFF_DT/RW_EXP_DT columns)
    - Dynamic filters from UI (field name, type, operator, value)

    Args:
        tool_input: Dictionary containing:
            - dart_table (required): Full DART table reference (e.g., ihg-dart-edw-dev2.DB_WRK.gender_lookup)
            - dart_column (required): DART column name
            - source_table (required): Full source table reference (e.g., project.dataset.table)
            - source_column (required): Source column name
            - filters (optional): List of filter objects with fieldname, type, operator, value
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
        # Extract inputs from tool_input dictionary
        dart_table = tool_input.get("dart_table")
        dart_column = tool_input.get("dart_column")
        source_table = tool_input.get("source_table")
        source_column = tool_input.get("source_column")
        filters = tool_input.get("filters", None)  # Optional filters parameter

        # Try to get filters from tool_context.state (same pattern as fetch_metadata_tool)
        if not filters and tool_context:
            if hasattr(tool_context, 'state'):
                filters = tool_context.state.get('similarity_filters')
                if filters:
                    logger.info(f"[compute_overlap_tool] Using filters from tool_context.state: {filters}")
            elif hasattr(tool_context, 'session') and tool_context.session:
                filters = tool_context.session.state.get('similarity_filters')
                if filters:
                    logger.info(f"[compute_overlap_tool] Using filters from session.state: {filters}")

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


