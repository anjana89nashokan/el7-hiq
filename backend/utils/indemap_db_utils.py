"""
Indemap DB (SQL Server) connection and query utilities.

This module provides SQLAlchemy-based connection management for the Indemap
SQL Server database, including query execution, stored procedure calls,
and data insertion operations.
"""

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.pool import QueuePool
from sqlalchemy.orm import sessionmaker, Session
from functools import lru_cache
from typing import List, Dict, Any, Optional, Generator
from contextlib import contextmanager
import pandas as pd
import logging
from urllib.parse import quote_plus
from collections import OrderedDict

from utils.secret_manager import get_indemap_credentials
from config.settings import config

try:
    import pyodbc
except ImportError:
    pyodbc = None  # Not required for pymssql (sql_server) mode

logger = logging.getLogger(__name__)


def get_available_odbc_driver():
    """
    Get the best available ODBC driver for SQL Server.

    Only used for Windows Authentication mode (pyodbc). Not needed for
    sql_server mode which uses pymssql instead.

    Returns:
        str: Driver name to use in connection string

    Raises:
        Exception: If pyodbc is not installed or no SQL Server ODBC driver is found
    """
    if pyodbc is None:
        raise Exception(
            "pyodbc is not installed. Install it for Windows Authentication mode, "
            "or use INDEMAP_AUTH_MODE=sql_server which uses pymssql instead."
        )

    available_drivers = pyodbc.drivers()
    logger.info(f"Available ODBC drivers: {available_drivers}")

    # Preferred drivers in order of preference
    preferred_drivers = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server"
    ]

    for driver in preferred_drivers:
        if driver in available_drivers:
            logger.info(f"Selected ODBC driver: {driver}")
            return driver

    # If no driver found, raise error with helpful message
    raise Exception(
        f"No SQL Server ODBC driver found. Available drivers: {available_drivers}. "
        f"Please install ODBC Driver 17 or 18 for SQL Server from: "
        f"https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server"
    )


@lru_cache(maxsize=1)
def get_indemap_engine():
    """
    Get cached SQLAlchemy engine for Indemap DB.

    Creates a connection pool to the Indemap SQL Server database using
    either Windows Authentication or SQL Server Authentication based on
    INDEMAP_AUTH_MODE configuration.

    Connection Features:
        - Windows mode: pyodbc + ODBC Driver (Trusted Connection)
        - SQL Server mode: pymssql + Secret Manager (no ODBC driver needed)
        - Connection pooling (5 connections, max overflow 10)
        - Pre-ping to verify connections before use
        - Connection recycling after 1 hour

    Returns:
        sqlalchemy.engine.Engine: Cached database engine

    Raises:
        Exception: If connection creation fails

    Example:
        >>> engine = get_indemap_engine()
        >>> with engine.connect() as conn:
        ...     result = conn.execute(text("SELECT 1"))
    """
    try:
        auth_mode = config.INDEMAP_AUTH_MODE.lower()
        logger.info(f"Initializing Indemap DB connection with {auth_mode} authentication")

        if auth_mode == "windows":
            # Windows Authentication mode (for development/testing)
            # Requires ODBC driver installed on the machine.
            logger.info("Using Windows Authentication (Trusted Connection)")

            driver = get_available_odbc_driver()
            encoded_driver = quote_plus(driver)

            connection_string = (
                f"mssql+pyodbc://@{config.INDEMAP_SERVER}:{config.INDEMAP_PORT}/{config.INDEMAP_DATABASE}"
                f"?driver={encoded_driver}"
                f"&Trusted_Connection=yes"
                f"&TrustServerCertificate=yes"
            )

            logger.info(f"Connecting to: {config.INDEMAP_SERVER}/{config.INDEMAP_DATABASE} via Windows Auth")

        elif auth_mode == "sql_server":
            # SQL Server Authentication via pymssql — host/port/database
            # from config, only username+password from Secret Manager.
            # pymssql uses FreeTDS under the hood; no ODBC driver required.
            logger.info("Using SQL Server Authentication (pymssql + Secret Manager)")

            creds = get_indemap_credentials()

            encoded_password = quote_plus(creds["password"])
            encoded_username = quote_plus(creds["username"])

            connection_string = (
                f"mssql+pymssql://{encoded_username}:{encoded_password}"
                f"@{quote_plus(config.INDEMAP_SERVER)}:{config.INDEMAP_PORT}"
                f"/{quote_plus(config.INDEMAP_DATABASE)}"
            )

            logger.info(
                f"Connecting to: {config.INDEMAP_SERVER}/{config.INDEMAP_DATABASE} "
                f"as {creds['username']} via pymssql"
            )

        else:
            raise ValueError(f"Invalid INDEMAP_AUTH_MODE: {auth_mode}. Must be 'windows' or 'sql_server'")

        # Build engine kwargs — connect_args only for pyodbc (windows mode)
        engine_kwargs = dict(
            poolclass=QueuePool,
            pool_size=5,                    # Number of connections to maintain
            max_overflow=10,                # Additional connections when needed
            pool_pre_ping=True,             # Verify connections before using
            pool_recycle=3600,              # Recycle connections after 1 hour
            echo=False,                      # Set to True for SQL debugging
        )

        if auth_mode == "windows":
            engine_kwargs["connect_args"] = {
                "timeout": config.INDEMAP_CONNECTION_TIMEOUT
            }

        # Create engine with connection pooling
        engine = create_engine(connection_string, **engine_kwargs)

        logger.info(f"Indemap DB engine created successfully using {auth_mode} authentication")
        return engine

    except Exception as e:
        logger.exception("Failed to create Indemap DB engine")
        raise Exception(f"Indemap DB connection error: {str(e)}")


@lru_cache(maxsize=1)
def get_indemap_session_factory() -> sessionmaker:
    """Cached session factory bound to the IndeMap engine."""
    engine = get_indemap_engine()
    return sessionmaker(bind=engine)


def get_indemap_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields an IndeMap DB session.

    Usage in routers:
        @router.post("/endpoint")
        async def handler(session: Session = Depends(get_indemap_session)):
            repo = IndemapRepository(session)
            ...
    """
    factory = get_indemap_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_indemap_connection() -> bool:
    """
    Test Indemap DB connection health.

    Attempts to execute a simple query to verify the database connection
    is working properly.

    Returns:
        bool: True if connection successful, False otherwise

    Example:
        >>> if test_indemap_connection():
        ...     print("Database is reachable")
        ... else:
        ...     print("Database connection failed")
    """
    try:
        engine = get_indemap_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1 as test"))
            row = result.fetchone()
            if row and row[0] == 1:
                logger.info("Indemap DB connection test successful")
                return True
            else:
                logger.warning("Indemap DB connection test returned unexpected result")
                return False

    except Exception as e:
        logger.exception("Indemap DB connection test failed")
        logger.error(f"Connection error: {str(e)}")
        return False


def execute_indemap_query(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Execute SELECT query and return results as list of dictionaries.

    Args:
        query: SQL query string (use :param_name for parameters)
        params: Dictionary of query parameters (default: None)

    Returns:
        List of dictionaries, one per row

    Raises:
        Exception: If query execution fails

    Example:
        >>> query = "SELECT * FROM Users WHERE UserID = :user_id"
        >>> results = execute_indemap_query(query, {"user_id": 123})
        >>> print(results[0]['UserName'])
    """
    try:
        engine = get_indemap_engine()
        with engine.connect() as conn:
            result = conn.execute(text(query), params or {})
            rows = [dict(row._mapping) for row in result]

        logger.info(f"Query executed successfully, returned {len(rows)} rows")
        return rows

    except Exception as e:
        logger.exception(f"Query execution failed: {query[:100]}...")
        raise Exception(f"Indemap query error: {str(e)}")


def execute_indemap_query_df(query: str, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """
    Execute query and return results as pandas DataFrame.

    Args:
        query: SQL query string
        params: Dictionary of query parameters (default: None)

    Returns:
        pandas.DataFrame with query results

    Raises:
        Exception: If query execution fails

    Example:
        >>> query = "SELECT * FROM TargetMetadata WHERE InterfaceCode = :code"
        >>> df = execute_indemap_query_df(query, {"code": "INT001"})
        >>> print(df.head())
    """
    try:
        engine = get_indemap_engine()
        df = pd.read_sql_query(text(query), engine, params=params or {})

        logger.info(f"Query executed successfully, returned DataFrame with {len(df)} rows, {len(df.columns)} columns")
        return df

    except Exception as e:
        logger.exception(f"DataFrame query execution failed: {query[:100]}...")
        raise Exception(f"Indemap query error: {str(e)}")


def insert_indemap_dataframe(
    df: pd.DataFrame,
    table_name: str,
    if_exists: str = 'append',
    schema: Optional[str] = 'dbo'
) -> int:
    """
    Insert pandas DataFrame into Indemap table.

    Uses pandas.to_sql() for bulk insert operations. Supports append,
    replace, or fail modes.

    Args:
        df: DataFrame to insert
        table_name: Target table name
        if_exists: Action if table exists ('fail', 'replace', 'append')
        schema: Database schema (default: 'dbo')

    Returns:
        Number of rows inserted

    Raises:
        Exception: If insert operation fails

    Example:
        >>> data = pd.DataFrame({
        ...     'column1': [1, 2, 3],
        ...     'column2': ['a', 'b', 'c']
        ... })
        >>> rows_inserted = insert_indemap_dataframe(data, 'MyTable')
        >>> print(f"Inserted {rows_inserted} rows")
    """
    try:
        engine = get_indemap_engine()
        rows_before = len(df)

        df.to_sql(
            name=table_name,
            con=engine,
            schema=schema,
            if_exists=if_exists,
            index=False,
            method='multi',
            chunksize=config.INDEMAP_BATCH_SIZE
        )

        logger.info(f"Successfully inserted {rows_before} rows into {schema}.{table_name}")
        return rows_before

    except Exception as e:
        logger.exception(f"Failed to insert DataFrame into {schema}.{table_name}")
        raise Exception(f"Indemap insert error: {str(e)}")


def call_indemap_stored_procedure(
    sp_name: str,
    params: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Call Indemap stored procedure with parameters.

    Args:
        sp_name: Stored procedure name (e.g., 'sp_GetTargetMetadata')
        params: Dictionary of SP parameters

    Returns:
        List of dictionaries with SP results

    Raises:
        Exception: If SP execution fails

    Example:
        >>> results = call_indemap_stored_procedure(
        ...     "sp_GetTargetMetadata",
        ...     {"InterfaceCode": "INT001"}
        ... )
        >>> print(len(results))
    """
    try:
        engine = get_indemap_engine()

        # Build EXEC statement with parameters
        if params:
            param_str = ', '.join([f"@{k} = :{k}" for k in params.keys()])
            query = f"EXEC {sp_name} {param_str}"
        else:
            query = f"EXEC {sp_name}"

        with engine.connect() as conn:
            result = conn.execute(text(query), params or {})
            rows = [dict(row._mapping) for row in result]

        logger.info(f"Stored procedure {sp_name} executed successfully, returned {len(rows)} rows")
        return rows

    except Exception as e:
        logger.exception(f"Stored procedure {sp_name} execution failed")
        raise Exception(f"Indemap SP error: {str(e)}")


def get_indemap_table_schema(table_name: str, schema: str = "dbo") -> List[Dict[str, Any]]:
    """
    Get column definitions for an Indemap table.

    Queries INFORMATION_SCHEMA.COLUMNS to retrieve table metadata.

    Args:
        table_name: Table name to inspect
        schema: Database schema (default: 'dbo')

    Returns:
        List of dictionaries with column metadata:
            - column_name
            - data_type
            - max_length
            - is_nullable
            - default_value

    Example:
        >>> schema = get_indemap_table_schema("TargetMetadata")
        >>> for col in schema:
        ...     print(f"{col['column_name']}: {col['data_type']}")
    """
    query = """
    SELECT
        COLUMN_NAME as column_name,
        DATA_TYPE as data_type,
        CHARACTER_MAXIMUM_LENGTH as max_length,
        NUMERIC_PRECISION as numeric_precision,
        NUMERIC_SCALE as numeric_scale,
        IS_NULLABLE as is_nullable,
        COLUMN_DEFAULT as default_value,
        ORDINAL_POSITION as ordinal_position
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = :schema
      AND TABLE_NAME = :table_name
    ORDER BY ORDINAL_POSITION
    """

    try:
        results = execute_indemap_query(query, {"schema": schema, "table_name": table_name})
        logger.info(f"Retrieved schema for {schema}.{table_name}: {len(results)} columns")
        return results

    except Exception as e:
        logger.exception(f"Failed to get schema for {schema}.{table_name}")
        raise


def get_indemap_table_list(schema: str = "dbo") -> List[str]:
    """
    Get list of all tables in Indemap database.

    Args:
        schema: Database schema (default: 'dbo')

    Returns:
        List of table names

    Example:
        >>> tables = get_indemap_table_list()
        >>> print(f"Found {len(tables)} tables")
        >>> print(tables)
    """
    query = """
    SELECT TABLE_NAME
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = :schema
      AND TABLE_TYPE = 'BASE TABLE'
    ORDER BY TABLE_NAME
    """

    try:
        results = execute_indemap_query(query, {"schema": schema})
        table_names = [row['TABLE_NAME'] for row in results]
        logger.info(f"Found {len(table_names)} tables in schema {schema}")
        return table_names

    except Exception as e:
        logger.exception(f"Failed to list tables in schema {schema}")
        raise


@contextmanager
def indemap_transaction():
    """
    Context manager for transactional writes to Indemap DB.

    Provides automatic commit/rollback behavior for database operations.

    Yields:
        sqlalchemy.orm.Session: Database session

    Example:
        >>> with indemap_transaction() as session:
        ...     session.execute(text("INSERT INTO ..."))
        ...     session.execute(text("UPDATE ..."))
        ...     # Automatically commits if no exception
    """
    engine = get_indemap_engine()
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        yield session
        session.commit()
        logger.info("Indemap transaction committed successfully")

    except Exception as e:
        session.rollback()
        logger.exception("Indemap transaction rolled back due to error")
        raise

    finally:
        session.close()


def log_indemap_audit(
    run_id: str,
    operation: str,
    interface_code: str,
    rows_affected: int,
    user_id: str,
    status: str = "success",
    error_message: Optional[str] = None
) -> None:
    """
    Write audit log entry to Indemap database.

    Args:
        run_id: Unique identifier for this run
        operation: Operation name (e.g., 'write_profiling', 'extract_metadata')
        interface_code: Interface/project code
        rows_affected: Number of rows affected
        user_id: User performing the operation
        status: Operation status ('success' or 'failed')
        error_message: Error message if status is 'failed'

    Example:
        >>> log_indemap_audit(
        ...     run_id="run_12345",
        ...     operation="write_profiling",
        ...     interface_code="INT001",
        ...     rows_affected=150,
        ...     user_id="user@example.com"
        ... )
    """
    try:
        audit_row = pd.DataFrame([{
            "run_id": run_id,
            "operation": operation,
            "interface_code": interface_code,
            "rows_affected": rows_affected,
            "user_id": user_id,
            "status": status,
            "error_message": error_message,
            "timestamp": pd.Timestamp.now()
        }])

        insert_indemap_dataframe(audit_row, "AuditLog", if_exists='append')
        logger.info(f"Audit log written: {operation} - {status}")

    except Exception as e:
        # Don't fail the main operation if audit logging fails
        logger.warning(f"Failed to write audit log: {str(e)}")


def execute_indemap_command(command: str, params: Optional[Dict[str, Any]] = None) -> int:
    """
    Execute INSERT, UPDATE, or DELETE command.

    Args:
        command: SQL command string
        params: Dictionary of command parameters

    Returns:
        Number of rows affected

    Example:
        >>> cmd = "UPDATE TargetMetadata SET Status = :status WHERE ID = :id"
        >>> rows = execute_indemap_command(cmd, {"status": "active", "id": 123})
        >>> print(f"{rows} rows updated")
    """
    try:
        engine = get_indemap_engine()
        with engine.connect() as conn:
            result = conn.execute(text(command), params or {})
            conn.commit()
            rows_affected = result.rowcount

        logger.info(f"Command executed successfully, {rows_affected} rows affected")
        return rows_affected

    except Exception as e:
        logger.exception(f"Command execution failed: {command[:100]}...")
        raise Exception(f"Indemap command error: {str(e)}")


def fetch_entity_details(db_name: str, table_names: List[str]) -> List[Dict[str, Any]]:
    """
    Fetch entity and column details from IndeMap in a single combined query.
    Joins IM_ENTITY_CUR, IM_DB, IM_SCHEMA, and IM_ENTITY_ATTR_CUR.

    Args:
        db_name: Database name (e.g., 'DB_AEDWPIV')
        table_names: List of physical table names (e.g., ['PRV_DATA', 'PRV_MAP'])

    Returns:
        List of row dicts with entity + column fields, ordered by table then column ordinal.
    """
    if not table_names:
        return []

    # Build parameterized IN clause for table names
    table_params = {f"t_{i}": name for i, name in enumerate(table_names)}
    in_clause = ", ".join(f":t_{i}" for i in range(len(table_names)))

    query = f"""
    SELECT DISTINCT
        E.IM_ENTITY_SK,
        DB.IM_SRVR_NM,
        DB.IM_DB_NM,
        S.IM_SCHEMA_NM,
        E.IM_ENTITY_PHYS_NM,
        E.IM_ENTITY_LOG_NM,
        E.IM_ENTITY_BUS_NM,
        E.IM_ENTITY_DSC,
        E.IM_ENTITY_TP_CD,
        A.IM_ENTITY_COLM_SK,
        A.M_ENTITY_COLM_ORD_NO,
        A.IM_ENTITY_COLM_NM,
        A.IM_ENTITY_COLM_LGC_NM,
        A.IM_ENTITY_COLM_DSC,
        A.IM_ENTITY_ATTR_DATA_TP_CD,
        A.IM_ENTITY_COLM_LNG_NO,
        A.IM_ENTITY_COLM_DATA_TP_PRECISION_NO,
        A.IM_ENTITY_COLM_NULL_IND,
        A.IM_ENTITY_COLM_DFLT_VAL,
        A.IM_ENTITY_COLM_FMT_VAL,
        KY.KEY_TP_CD,
        KA.ALT_KEY_ORD_NO
    FROM IM_DB DB
    JOIN IM_ENTITY_CUR E
        ON DB.IM_DB_SK = E.IM_DB_SK
        AND DB.DEL_IND = 'N'
        AND E.IM_ENTITY_TP_CD = 'TBL'
        AND E.DEL_IND = 'N'
    JOIN IM_ENTITY_ATTR_CUR A
        ON A.IM_ENTITY_SK = E.IM_ENTITY_SK
        AND A.DEL_IND = 'N'
    LEFT JOIN IM_SCHEMA S
        ON E.IM_SCHEMA_SK = S.IM_SCHEMA_SK
    LEFT JOIN IM_ENTITY_KEY_ASOC_CUR KA
        ON KA.IM_ENTITY_COLM_SK = A.IM_ENTITY_COLM_SK
        AND KA.DEL_IND = 'N'
    LEFT JOIN IM_ENTITY_KEY_CUR KY
        ON KY.IM_ENTITY_KEY_SK = KA.IM_ENTITY_KEY_SK
        AND KY.DEL_IND = 'N'
    WHERE DB.IM_DB_NM = :db_name
      AND E.IM_ENTITY_PHYS_NM IN ({in_clause})
    ORDER BY DB.IM_SRVR_NM, E.IM_ENTITY_PHYS_NM, A.M_ENTITY_COLM_ORD_NO
    """

    params = {"db_name": db_name, **table_params}

    try:
        rows = execute_indemap_query(query, params)
        logger.info(f"Fetched {len(rows)} rows for {len(table_names)} table(s) in database '{db_name}'")
        return rows
    except Exception as e:
        logger.exception(f"Failed to fetch entity details for database '{db_name}', tables: {table_names}")
        raise


def build_target_tables(rows: List[Dict[str, Any]]) -> List:
    """
    Convert raw IndeMap rows into TargetTable models with their TargetColumn lists.
    Groups rows by IM_ENTITY_SK and builds the model hierarchy.

    Args:
        rows: Raw query result rows from fetch_entity_details()

    Returns:
        List of TargetTable model instances
    """
    from agents.mapping_ingestion.models import TargetTable, TargetColumn

    if not rows:
        return []

    entity_groups: OrderedDict[int, List[Dict[str, Any]]] = OrderedDict()
    for row in rows:
        entity_sk = row["IM_ENTITY_SK"]
        if entity_sk not in entity_groups:
            entity_groups[entity_sk] = []
        entity_groups[entity_sk].append(row)

    tables = []
    for entity_sk, entity_rows in entity_groups.items():
        first = entity_rows[0]

        # Group by column SK to deduplicate rows from key JOINs
        # (a column in both PK and AK produces multiple rows)
        col_groups: OrderedDict[int, Dict[str, Any]] = OrderedDict()
        for r in entity_rows:
            col_sk = r.get("IM_ENTITY_COLM_SK")
            if col_sk is None:
                continue
            if col_sk not in col_groups:
                col_groups[col_sk] = {"info": r, "key_types": set()}
            key_tp = _clean(r.get("KEY_TP_CD"))
            if key_tp:
                col_groups[col_sk]["key_types"].add(key_tp)

        pk_columns = []
        columns = []
        for col_sk, col_data in col_groups.items():
            r = col_data["info"]
            key_types = col_data["key_types"]

            col_name = (r.get("IM_ENTITY_COLM_NM") or "").strip()
            null_ind = (r.get("IM_ENTITY_COLM_NULL_IND") or "Y").strip()
            is_pk = "PK" in key_types
            is_fk = "FK" in key_types
            ak_groups = sorted(kt for kt in key_types if kt not in ("PK", "FK"))

            if is_pk:
                pk_columns.append(col_name)

            columns.append(TargetColumn(
                attribute_name=col_name,
                logical_attribute_name=_clean(r.get("IM_ENTITY_COLM_LGC_NM")),
                attribute_description=_clean(r.get("IM_ENTITY_COLM_DSC")),
                data_type=(r.get("IM_ENTITY_ATTR_DATA_TP_CD") or "UNKNOWN").strip(),
                length=r.get("IM_ENTITY_COLM_LNG_NO"),
                precision=r.get("IM_ENTITY_COLM_DATA_TP_PRECISION_NO"),
                default_value=_clean(r.get("IM_ENTITY_COLM_DFLT_VAL")),
                nullability=(null_ind != "N"),
                order_no=r.get("IM_ENTITY_COLM_ORD_NO"),
                format=_clean(r.get("IM_ENTITY_COLM_FMT_VAL")),
                is_surrogate_key=col_name.endswith("_SK"),
                is_code_column=col_name.endswith("_CD"),
                is_primary_key=is_pk,
                is_foreign_key=is_fk,
                alternate_key_groups=ak_groups,
            ))

        db_name = _clean(first.get("IM_DB_NM"))

        tables.append(TargetTable(
            table_id=str(entity_sk),
            server_name=_clean(first.get("IM_SRVR_NM")),
            table_name=(first.get("IM_ENTITY_PHYS_NM") or "").strip(),
            logical_name=_clean(first.get("IM_ENTITY_LOG_NM")),
            business_name=_clean(first.get("IM_ENTITY_BUS_NM")),
            description=_clean(first.get("IM_ENTITY_DSC")),
            table_type=_clean(first.get("IM_ENTITY_TP_CD")),
            database=db_name,
            database_name=db_name,
            schema_name=_clean(first.get("IM_SCHEMA_NM")),
            columns=columns,
            primary_key=pk_columns,
        ))

    logger.info(
        f"Built {len(tables)} TargetTable model(s) "
        f"with {sum(len(t.columns) for t in tables)} total columns"
    )
    return tables


def _clean(value: Optional[str]) -> Optional[str]:
    """Strip whitespace from char/varchar values, return None if empty."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


# ------------------------------------------------------------------
# Mapping Rules Lookup
# ------------------------------------------------------------------


def fetch_mapping_rules_by_column(
    target_column_name: str,
    top_n: int = 10,
    im_map_cd: str = "SRC",
) -> List[Dict[str, Any]]:
    """
    Fetch top N mapping rules for a given target column name across all interfaces.

    Joins:
      IM_MAP_APP_TGT_ENTITY_ATTR_CUR (TA) — mapping rules per target column
      IM_TRANS (T)                          — filter for checked-in transactions
      IM_ENTITY_ATTR_CUR (C)               — resolve column SK to column name
      IM_MAP_APP_TGT_ENTITY_CUR (TE)       — mapping header (interface code)
      IM_INTF_CD (I)                        — filter by map code (SRC, etc.)
      IM_MAP_SRC_ENTITY_TRANS_ASOC_CUR (SE) — source column associations

    Args:
        target_column_name: Physical column name (e.g. 'AEDW_PRV_SK')
        top_n: Maximum number of rules to return
        im_map_cd: Map code filter on IM_INTF_CD (default 'SRC')

    Returns:
        List of row dicts with mapping rule fields.
    """
    query = f"""
    SELECT TOP {int(top_n)}
        C.IM_ENTITY_COLM_NM AS TGT_COLM_NM,
        TE.INTF_CD,
        TE.IM_TGT_ENTITY_COMN_FLTR_TXT,
        TA.IM_ENTITY_APP_TRANS_RULE_TP_CD,
        TA.IM_SRC_ENTITY_TXT,
        TA.IM_SRC_COLM_TXT,
        TA.IM_MAP_APP_TGT_TRANS_JOIN_TXT,
        TA.IM_MAP_APP_TGT_TRANS_RULE_TXT,
        TA.IM_MAP_APP_TGT_TRANS_RULE_SEQ_NO,
        TA.IM_MAP_APP_TGT_TRANS_SPCL_TXT,
        TA.IM_MAP_APP_TGT_TRANS_FLTR_TXT,
        TA.IM_ENTITY_COLM_CDC_IND,
        TA.IM_MAP_APP_TGT_ENTITY_ATTR_DOC_VAL,
        SE.IM_ENTITY_SRC_COLM_SK,
        SA.IM_ENTITY_COLM_NM AS SRC_COLM_NM,
        TA.LAST_UPD_TS
    FROM IM_MAP_APP_TGT_ENTITY_CUR TE
    JOIN IM_MAP_APP_TGT_ENTITY_ATTR_CUR TA
        ON TE.IM_MAP_APP_TGT_ENTITY_SK = TA.IM_MAP_APP_TGT_ENTITY_SK
        AND TE.DEL_IND = 'N'
        AND TA.DEL_IND = 'N'
    JOIN IM_INTF_CD I
        ON I.INTF_CD = TE.INTF_CD
        AND I.DEL_IND = 'N'
        AND I.IM_MAP_CD = :im_map_cd
    JOIN IM_TRANS T
        ON TA.IM_TRANS_SK = T.IM_TRANS_SK
        AND T.DEL_IND = 'N'
        AND T.IM_TRANS_TP_CD = 'CHKIN'
    JOIN IM_ENTITY_ATTR_CUR C
        ON C.IM_ENTITY_COLM_SK = TA.IM_ENTITY_TGT_COLM_SK
        AND C.DEL_IND = 'N'
    LEFT JOIN IM_MAP_SRC_ENTITY_TRANS_ASOC_CUR SE
        ON SE.IM_MAP_APP_TGT_ENTITY_ATTR_SK = TA.IM_MAP_APP_TGT_ENTITY_ATTR_SK
        AND SE.DEL_IND = 'N'
    LEFT JOIN IM_ENTITY_ATTR_CUR SA
        ON SA.IM_ENTITY_COLM_SK = SE.IM_ENTITY_SRC_COLM_SK
        AND SA.DEL_IND = 'N'
    WHERE C.IM_ENTITY_COLM_NM = :target_column_name
      AND TA.IM_ENTITY_APP_TRANS_RULE_TP_CD IN ('LU', 'OT')
      AND TA.IM_MAP_APP_TGT_TRANS_RULE_TXT NOT LIKE '%_EXT%'
      AND NOT (
          (TA.IM_MAP_APP_TGT_TRANS_JOIN_TXT IS NULL OR TA.IM_MAP_APP_TGT_TRANS_JOIN_TXT = '')
          AND (TA.IM_MAP_APP_TGT_TRANS_RULE_TXT IS NULL OR TA.IM_MAP_APP_TGT_TRANS_RULE_TXT = '')
      )
    ORDER BY TA.LAST_UPD_TS DESC, TE.INTF_CD
    """

    params = {
        "target_column_name": target_column_name,
        "im_map_cd": im_map_cd,
    }

    try:
        rows = execute_indemap_query(query, params)
        logger.info(
            f"Fetched {len(rows)} mapping rule(s) for column "
            f"'{target_column_name}' (top_n={top_n})"
        )
        return rows
    except Exception as e:
        logger.exception(
            f"Failed to fetch mapping rules for column '{target_column_name}'"
        )
        raise


def build_mapping_rules(rows: List[Dict[str, Any]]) -> List:
    """
    Convert raw IndeMap mapping-rule rows into MappingRuleDetail models.

    Args:
        rows: Raw query result rows from fetch_mapping_rules_by_column()

    Returns:
        List of MappingRuleDetail model instances
    """
    from models.indemap_models import MappingRuleDetail

    if not rows:
        return []

    rules = []
    for r in rows:
        last_upd = r.get("LAST_UPD_TS")
        last_upd_str = str(last_upd) if last_upd is not None else None

        rules.append(MappingRuleDetail(
            target_column_name=_clean(r.get("TGT_COLM_NM")),
            interface_code=_clean(r.get("INTF_CD")),
            common_filter=_clean(r.get("IM_TGT_ENTITY_COMN_FLTR_TXT")),
            rule_type_code=_clean(r.get("IM_ENTITY_APP_TRANS_RULE_TP_CD")),
            source_entity_text=_clean(r.get("IM_SRC_ENTITY_TXT")),
            source_column_text=_clean(r.get("IM_SRC_COLM_TXT")),
            join_text=_clean(r.get("IM_MAP_APP_TGT_TRANS_JOIN_TXT")),
            rule_text=_clean(r.get("IM_MAP_APP_TGT_TRANS_RULE_TXT")),
            rule_sequence_no=r.get("IM_MAP_APP_TGT_TRANS_RULE_SEQ_NO"),
            special_text=_clean(r.get("IM_MAP_APP_TGT_TRANS_SPCL_TXT")),
            filter_text=_clean(r.get("IM_MAP_APP_TGT_TRANS_FLTR_TXT")),
            cdc_indicator=_clean(r.get("IM_ENTITY_COLM_CDC_IND")),
            doc_value=_clean(r.get("IM_MAP_APP_TGT_ENTITY_ATTR_DOC_VAL")),
            source_column_sk=r.get("IM_ENTITY_SRC_COLM_SK"),
            source_column_name=_clean(r.get("SRC_COLM_NM")),
            last_updated=last_upd_str,
        ))

    logger.info(f"Built {len(rules)} MappingRuleDetail model(s)")
    return rules


# ------------------------------------------------------------------
# Mapping Rules Frequency (for DART suggestion ranking)
# ------------------------------------------------------------------


def fetch_recent_mapping_rules_for_column(
    column_name: str,
    top_n: int = 25,
    im_map_cd: str = "SRC",
) -> List[Dict[str, Any]]:
    """
    Fetch the latest N mapping rules for a given DART column name,
    including the physical table name from IM_ENTITY_CUR.

    Used by the DART suggestion agent to rank candidates by table+column
    frequency: fetch recent rules, group by (table, column) occurrence,
    and the most-used combination ranks highest.

    Joins IM_ENTITY_CUR via IM_ENTITY_ATTR_CUR.IM_ENTITY_SK to resolve
    the physical table name (IM_ENTITY_PHYS_NM).

    NOTE: The IM_ENTITY_CUR join needs BSA confirmation to verify whether
    DART tables appear as target entities in IndeMap.

    Args:
        column_name: DART column name (e.g. 'Member_Id')
        top_n: Number of recent records to fetch (default 25)
        im_map_cd: Map code filter on IM_INTF_CD (default 'SRC')

    Returns:
        List of row dicts including TGT_TABLE_NM and TGT_COLM_NM for
        frequency grouping.
    """
    query = f"""
    SELECT TOP {int(top_n)}
        E.IM_ENTITY_PHYS_NM AS TGT_TABLE_NM,
        C.IM_ENTITY_COLM_NM AS TGT_COLM_NM,
        TE.INTF_CD,
        TA.IM_SRC_ENTITY_TXT,
        TA.IM_SRC_COLM_TXT,
        SA.IM_ENTITY_COLM_NM AS SRC_COLM_NM,
        TA.IM_ENTITY_APP_TRANS_RULE_TP_CD,
        TA.IM_MAP_APP_TGT_TRANS_RULE_TXT,
        TA.LAST_UPD_TS
    FROM IM_MAP_APP_TGT_ENTITY_CUR TE
    JOIN IM_MAP_APP_TGT_ENTITY_ATTR_CUR TA
        ON TE.IM_MAP_APP_TGT_ENTITY_SK = TA.IM_MAP_APP_TGT_ENTITY_SK
        AND TE.DEL_IND = 'N'
        AND TA.DEL_IND = 'N'
    JOIN IM_INTF_CD I
        ON I.INTF_CD = TE.INTF_CD
        AND I.DEL_IND = 'N'
        AND I.IM_MAP_CD = :im_map_cd
    JOIN IM_TRANS T
        ON TA.IM_TRANS_SK = T.IM_TRANS_SK
        AND T.DEL_IND = 'N'
        AND T.IM_TRANS_TP_CD = 'CHKIN'
    JOIN IM_ENTITY_ATTR_CUR C
        ON C.IM_ENTITY_COLM_SK = TA.IM_ENTITY_TGT_COLM_SK
        AND C.DEL_IND = 'N'
    JOIN IM_ENTITY_CUR E
        ON E.IM_ENTITY_SK = C.IM_ENTITY_SK
        AND E.DEL_IND = 'N'
    LEFT JOIN IM_MAP_SRC_ENTITY_TRANS_ASOC_CUR SE
        ON SE.IM_MAP_APP_TGT_ENTITY_ATTR_SK = TA.IM_MAP_APP_TGT_ENTITY_ATTR_SK
        AND SE.DEL_IND = 'N'
    LEFT JOIN IM_ENTITY_ATTR_CUR SA
        ON SA.IM_ENTITY_COLM_SK = SE.IM_ENTITY_SRC_COLM_SK
        AND SA.DEL_IND = 'N'
    WHERE C.IM_ENTITY_COLM_NM = :column_name
      AND TA.IM_ENTITY_APP_TRANS_RULE_TP_CD IN ('LU', 'OT')
      AND TA.IM_MAP_APP_TGT_TRANS_RULE_TXT NOT LIKE '%_EXT%'
      AND NOT (
          (TA.IM_MAP_APP_TGT_TRANS_JOIN_TXT IS NULL OR TA.IM_MAP_APP_TGT_TRANS_JOIN_TXT = '')
          AND (TA.IM_MAP_APP_TGT_TRANS_RULE_TXT IS NULL OR TA.IM_MAP_APP_TGT_TRANS_RULE_TXT = '')
      )
    ORDER BY TA.LAST_UPD_TS DESC
    """

    params = {
        "column_name": column_name,
        "im_map_cd": im_map_cd,
    }

    try:
        rows = execute_indemap_query(query, params)
        logger.info(
            f"Fetched {len(rows)} recent mapping rule(s) for column '{column_name}'"
        )
        return rows
    except Exception as e:
        logger.warning(
            "Failed to fetch recent mapping rules for '%s': %s (%s) — IndeMap may be unreachable, returning empty",
            column_name, e, type(e).__name__,
        )
        return []


# ------------------------------------------------------------------
# MDR Recommended Tables Filter (for DART suggestion agent)
# ------------------------------------------------------------------


def fetch_mdr_recommended_tables(table_names: List[str]) -> Dict[str, str]:
    """
    Given a list of table names from vector search, return only those that are
    MDR-recommended (RCMND_STS_CD = 'R') from mdr.dbo.DB_TBL_VW.

    Uses the same SQL Server connection as IndeMap via 3-part name (mdr.dbo.*).

    Args:
        table_names: Table names to check (e.g. ['GBR_SRC_MBR', 'MBR_ELIG_HIST'])

    Returns:
        Dict mapping recommended table_name -> RCMND_STS_DSC for each matched table.
        On DB failure, returns all input tables as fallback (fail open) with empty descriptions.
    """
    if not table_names:
        return {}

    table_params = {f"t_{i}": name for i, name in enumerate(table_names)}
    in_clause = ", ".join(f":t_{i}" for i in range(len(table_names)))

    query = f"""
    SELECT A.TBL_VW_NM, B.RCMND_STS_DSC
    FROM mdr.dbo.DB_TBL_VW A
    JOIN mdr.dbo.RCMND_STS_CD B
        ON A.RCMND_STS_CD = B.RCMND_STS_CD
    WHERE A.RCMND_STS_CD = 'R'
      AND A.TBL_VW_NM IN ({in_clause})
    GROUP BY A.TBL_VW_NM, B.RCMND_STS_DSC
    """

    try:
        rows = execute_indemap_query(query, table_params)
        result = {
            row["TBL_VW_NM"].strip(): (row.get("RCMND_STS_DSC") or "").strip()
            for row in rows
            if row.get("TBL_VW_NM")
        }
        logger.info(
            "[mdr_filter] %d/%d table(s) passed MDR recommended filter: %s",
            len(result), len(table_names), list(result.keys()),
        )
        return result
    except Exception as e:
        logger.warning(
            "[mdr_filter] MDR lookup failed: %s (%s) — failing open, returning all %d table(s)",
            e, type(e).__name__, len(table_names),
        )
        return {t: "" for t in table_names}


# ------------------------------------------------------------------
# ORM-based facade (delegates to IndemapRepository)
# ------------------------------------------------------------------

def get_entity_details_orm(db_name: str, table_names: List[str]) -> List:
    """
    Fetch entity + column details using ORM and return as TargetTable DTOs.
    Internally creates a session, delegates to IndemapRepository for
    ORM query and DTO mapping, then closes the session.

    Args:
        db_name: Database name (e.g., 'DB_AEDWPIV')
        table_names: List of physical table names (e.g., ['PRV_DATA', 'PRV_MAP'])

    Returns:
        List of TargetTable model instances with bound TargetColumn lists
    """
    from utils.indemap_repository import IndemapRepository

    if not table_names:
        return []

    factory = get_indemap_session_factory()
    session = factory()
    try:
        repo = IndemapRepository(session)
        entities = repo.get_entities_with_columns(db_name, table_names)
        return repo.to_target_tables(entities)
    except Exception as e:
        logger.exception(f"ORM entity lookup failed for '{db_name}', tables: {table_names}")
        raise
    finally:
        session.close()
