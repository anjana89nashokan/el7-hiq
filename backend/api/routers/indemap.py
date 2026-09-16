"""
Indemap DB API Router

This module provides FastAPI endpoints for interacting with the Indemap
SQL Server database, including health checks, metadata retrieval, mapping
rules extraction, and result persistence.
"""

from fastapi import APIRouter, HTTPException, Query
from google.genai.errors import ServerError
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

from utils.indemap_db_utils import (
    test_indemap_connection,
    get_indemap_table_list,
    get_indemap_table_schema,
    execute_indemap_query_df,
    fetch_entity_details,
    build_target_tables,
    fetch_mapping_rules_by_column,
    build_mapping_rules,
)
from models.indemap_models import (
    DatabaseTablesRequest,
    EntityLookupResponse,
    MappingRulesLookupRequest,
    MappingRulesLookupResponse,
)
from config.settings import config

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/indemap",
    tags=["indemap"],
    responses={404: {"description": "Not found"}},
)


@router.get("/health")
async def indemap_health_check() -> Dict[str, Any]:
    """
    Check Indemap DB connection status.

    Tests the connection to the Indemap SQL Server database and returns
    the connection health status along with configuration information.

    Returns:
        Dictionary with:
            - status: 'healthy' or 'unhealthy'
            - database: Database type
            - service_account: Service account name
            - timestamp: Check timestamp
            - connection_timeout: Configured timeout in seconds

    Example Response:
        {
            "status": "healthy",
            "database": "Indemap SQL Server",
            "service_account": "SRV_MDR_NP",
            "timestamp": "2026-02-06T10:30:00.000Z",
            "connection_timeout": 30
        }
    """
    try:
        is_healthy = test_indemap_connection()

        # Build connection info based on auth mode
        auth_mode = config.INDEMAP_AUTH_MODE.lower()
        connection_info = {
            "server": config.INDEMAP_SERVER,
            "database": config.INDEMAP_DATABASE,
            "port": config.INDEMAP_PORT,
        }

        if auth_mode == "windows":
            connection_info["driver"] = "pyodbc (ODBC)"
        else:
            connection_info["driver"] = "pymssql (FreeTDS)"
            connection_info["service_account"] = config.INDEMAP_SERVICE_ACCOUNT

        response = {
            "status": "healthy" if is_healthy else "unhealthy",
            "database": "Indemap SQL Server",
            "auth_mode": config.INDEMAP_AUTH_MODE,
            "connection": connection_info,
            "timestamp": datetime.utcnow().isoformat(),
            "connection_timeout": config.INDEMAP_CONNECTION_TIMEOUT,
            "tables_configured": {
                "target_metadata": config.INDEMAP_TARGET_METADATA_TABLE,
                "mapping_rules": config.INDEMAP_MAPPING_RULES_TABLE,
                "profiling_results": config.INDEMAP_PROFILING_RESULTS_TABLE,
                "filespecs": config.INDEMAP_FILESPECS_TABLE
            }
        }

        if not is_healthy:
            logger.warning("Indemap DB health check failed")
            raise HTTPException(status_code=503, detail=response)

        logger.info("Indemap DB health check passed")
        return response

    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Indemap health check error")
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "message": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
        )


@router.get("/tables")
async def list_indemap_tables(schema: str = Query("dbo", description="Database schema")) -> Dict[str, Any]:
    """
    List all tables in Indemap database.

    Args:
        schema: Database schema name (default: 'dbo')

    Returns:
        Dictionary with:
            - schema: Schema name
            - table_count: Number of tables
            - tables: List of table names

    Example Response:
        {
            "schema": "dbo",
            "table_count": 15,
            "tables": ["TargetMetadata", "MappingRules", ...]
        }
    """
    try:
        tables = get_indemap_table_list(schema=schema)

        return {
            "schema": schema,
            "table_count": len(tables),
            "tables": tables,
            "timestamp": datetime.utcnow().isoformat()
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        logger.exception(f"Failed to list tables in schema {schema}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list Indemap tables: {str(e)}"
        )


@router.get("/tables/{table_name}/schema")
async def get_table_schema(
    table_name: str,
    schema: str = Query("dbo", description="Database schema")
) -> Dict[str, Any]:
    """
    Get schema/column definitions for a specific Indemap table.

    Args:
        table_name: Name of the table
        schema: Database schema name (default: 'dbo')

    Returns:
        Dictionary with:
            - table_name: Table name
            - schema: Schema name
            - column_count: Number of columns
            - columns: List of column definitions

    Example Response:
        {
            "table_name": "TargetMetadata",
            "schema": "dbo",
            "column_count": 10,
            "columns": [
                {
                    "column_name": "TableName",
                    "data_type": "varchar",
                    "max_length": 255,
                    "is_nullable": "NO"
                },
                ...
            ]
        }
    """
    try:
        columns = get_indemap_table_schema(table_name=table_name, schema=schema)

        if not columns:
            raise HTTPException(
                status_code=404,
                detail=f"Table {schema}.{table_name} not found or has no columns"
            )

        return {
            "table_name": table_name,
            "schema": schema,
            "column_count": len(columns),
            "columns": columns,
            "timestamp": datetime.utcnow().isoformat()
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get schema for {schema}.{table_name}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get table schema: {str(e)}"
        )


@router.get("/query")
async def execute_custom_query(
    query: str = Query(..., description="SQL SELECT query to execute"),
    limit: int = Query(100, description="Maximum rows to return", ge=1, le=10000)
) -> Dict[str, Any]:
    """
    Execute a custom SELECT query against Indemap DB.

    Args:
        query: SQL SELECT query (limited to SELECT statements)
        limit: Maximum number of rows to return (1-10000)

    Returns:
        Dictionary with:
            - row_count: Number of rows returned
            - columns: List of column names
            - data: Query results

    Security Note:
        This endpoint is for administrative use. In production, implement
        proper authentication and query validation.

    Example:
        GET /indemap/query?query=SELECT TOP 10 * FROM TargetMetadata&limit=10
    """
    try:
        # Basic security check - only allow SELECT queries
        query_upper = query.strip().upper()
        if not query_upper.startswith("SELECT"):
            raise HTTPException(
                status_code=400,
                detail="Only SELECT queries are allowed"
            )

        # Prevent dangerous keywords
        dangerous_keywords = ["DROP", "DELETE", "INSERT", "UPDATE", "EXEC", "EXECUTE", "ALTER"]
        if any(keyword in query_upper for keyword in dangerous_keywords):
            raise HTTPException(
                status_code=400,
                detail="Query contains forbidden keywords"
            )

        # Add LIMIT if not present
        if "TOP" not in query_upper:
            query = query.replace("SELECT", f"SELECT TOP {limit}", 1)

        # Execute query
        df = execute_indemap_query_df(query)

        return {
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": df.columns.tolist(),
            "data": df.to_dict(orient='records'),
            "timestamp": datetime.utcnow().isoformat()
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to execute custom query")
        raise HTTPException(
            status_code=500,
            detail=f"Query execution failed: {str(e)}"
        )


@router.get("/config")
async def get_indemap_config() -> Dict[str, Any]:
    """
    Get current Indemap DB configuration.

    Returns configuration settings loaded from environment variables
    and config file. Useful for debugging and verification.

    Returns:
        Dictionary with current Indemap configuration

    Example Response:
        {
            "auth_mode": "windows",
            "server": "INFSQLQ08",
            "database": "MDR",
            "connection_timeout": 30,
            "query_timeout": 60,
            "batch_size": 1000,
            "tables": {...}
        }
    """
    config_data = {
        "auth_mode": config.INDEMAP_AUTH_MODE,
        "connection_timeout": config.INDEMAP_CONNECTION_TIMEOUT,
        "query_timeout": config.INDEMAP_QUERY_TIMEOUT,
        "batch_size": config.INDEMAP_BATCH_SIZE,
        "top_n_mappings": config.INDEMAP_TOP_N_MAPPINGS,
        "ranking_criteria": config.INDEMAP_RANKING_CRITERIA,
        "tables": {
            "target_metadata": config.INDEMAP_TARGET_METADATA_TABLE,
            "mapping_rules": config.INDEMAP_MAPPING_RULES_TABLE,
            "profiling_results": config.INDEMAP_PROFILING_RESULTS_TABLE,
            "filespecs": config.INDEMAP_FILESPECS_TABLE,
            "audit_log": config.INDEMAP_AUDIT_LOG_TABLE
        },
        "timestamp": datetime.utcnow().isoformat()
    }

    # Add connection details based on auth mode
    if config.INDEMAP_AUTH_MODE.lower() == "windows":
        config_data["connection"] = {
            "server": config.INDEMAP_SERVER,
            "database": config.INDEMAP_DATABASE,
            "port": config.INDEMAP_PORT
        }
    else:
        config_data["connection"] = {
            "secret_id": config.INDEMAP_SECRET_ID,
            "service_account": config.INDEMAP_SERVICE_ACCOUNT
        }

    return config_data


@router.post("/entities/lookup", response_model=EntityLookupResponse)
async def lookup_entities(request: List[DatabaseTablesRequest]) -> EntityLookupResponse:
    """
    Lookup table and column metadata from IndeMap DB.

    Accepts a list of database-table groups, fetches entity and column details
    from IM_ENTITY_CUR / IM_ENTITY_ATTR_CUR, and returns them as TargetTable
    models with bound TargetColumn lists.

    Request Body:
        [
            {"database_name": "DB_AEDWPIV", "tables": ["PRV_DATA", "PRV_MAP"]},
            {"database_name": "DB_AEDWP1", "tables": ["PRV_SRC"]}
        ]
    """
    try:
        all_tables = []
        not_found = []

        for db_group in request:
            logger.info(
                f"Looking up {len(db_group.tables)} table(s) "
                f"in database '{db_group.database_name}'"
            )

            rows = fetch_entity_details(db_group.database_name, db_group.tables)
            tables = build_target_tables(rows)

            found_names = {t.table_name for t in tables}
            for requested_table in db_group.tables:
                if requested_table not in found_names:
                    not_found.append({
                        "database_name": db_group.database_name,
                        "table_name": requested_table,
                    })

            all_tables.extend(tables)

        if not_found:
            logger.warning(f"Tables not found in IndeMap: {not_found}")

        return EntityLookupResponse(
            total_tables=len(all_tables),
            tables=[t.model_dump() for t in all_tables],
            not_found=not_found,
            timestamp=datetime.utcnow().isoformat(),
        )
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        logger.exception("Entity lookup failed")
        raise HTTPException(
            status_code=500,
            detail=f"Entity lookup failed: {str(e)}"
        )


@router.post("/mapping-rules/lookup", response_model=MappingRulesLookupResponse)
async def lookup_mapping_rules(
    request: MappingRulesLookupRequest,
) -> MappingRulesLookupResponse:
    """
    Look up top N mapping rules for a target column across all interfaces.

    Searches IM_MAP_APP_TGT_ENTITY_ATTR_CUR joined to the target entity
    header and source associations to return historical mapping rules
    matching the given target column name.

    Request Body:
        {
            "target_column_name": "AEDW_PRV_SK",
            "top_n": 10
        }
    """
    try:
        top_n = (
            request.top_n
            if request.top_n is not None
            else config.INDEMAP_TOP_N_MAPPINGS
        )

        logger.info(
            f"Looking up mapping rules for column "
            f"'{request.target_column_name}' (top_n={top_n})"
        )

        im_map_cd = request.im_map_cd if request.im_map_cd else "SRC"

        rows = fetch_mapping_rules_by_column(
            target_column_name=request.target_column_name,
            top_n=top_n,
            im_map_cd=im_map_cd,
        )
        rules = build_mapping_rules(rows)

        return MappingRulesLookupResponse(
            column_name=request.target_column_name,
            top_n=top_n,
            total_rules=len(rules),
            rules=[r.model_dump() for r in rules],
            timestamp=datetime.utcnow().isoformat(),
        )
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        logger.exception("Mapping rules lookup failed")
        raise HTTPException(
            status_code=500,
            detail=f"Mapping rules lookup failed: {str(e)}"
        )
