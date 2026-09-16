from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any
from utils.bg_query_utils import get_table, get_bigquery_client
from config.settings import config
from google.genai.errors import ServerError
import json
import pandas as pd
import logging
from config.settings import Config
from collections import defaultdict

router = APIRouter()
logger = logging.getLogger(__name__)


def _enrich_dd_rows(rows: list[dict]) -> list[dict]:
    """
    For each DD row, query the source BQ table to fill:
    - most_occurrences: top-N most frequent values
    """
    top_n = getattr(config, "DD_MOST_OCCURRENCES_TOP_N", 5)
    client = get_bigquery_client()

    # Group by source table ("File Name" column)
    tables: dict = defaultdict(list)
    for row in rows:
        file_name = row.get("File Name") or row.get("file_name") or ""
        if file_name:
            tables[file_name].append(row)

    for table_name, table_rows in tables.items():
        full_table = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"
        # field name key may be BQ-style or snake_case
        columns = [
            r.get("Attribute Name") or r.get("field_name") or ""
            for r in table_rows
        ]
        columns = [c for c in columns if c]
        if not columns:
            continue
        try:
            parts = [f"APPROX_TOP_COUNT(`{col}`, {top_n}) AS `{col}_top`" for col in columns]
            sql = f"SELECT COUNT(*) AS total_rows, {', '.join(parts)} FROM `{full_table}`"
            bq_row = next(iter(client.query(sql).result()))
            total_rows = bq_row["total_rows"]

            for dd_row in table_rows:
                col = dd_row.get("Attribute Name") or dd_row.get("field_name") or ""
                if not col:
                    continue
                top_info = bq_row.get(f"{col}_top") or []
                dd_row["Most Occurrences"] = [
                    f"{e['value']} ({round(e['count'] / total_rows * 100)}%)"
                    for e in top_info if e["value"] is not None
                ]
                # Remove any existing Default Value field
                dd_row.pop("Default Value", None)
                dd_row.pop("default_value", None)
        except Exception as exc:
            logger.warning("[DD_ENRICH] BQ query failed for table %s: %s", table_name, exc)
            for dd_row in table_rows:
                dd_row.setdefault("Most Occurrences", [])
                # Remove any existing Default Value field
                dd_row.pop("Default Value", None)
                dd_row.pop("default_value", None)

    return rows


def _write_enriched_columns_to_bq(table_name: str, rows: list[dict]) -> None:
    """
    Writes the enriched Most Occurrences back to the DD BQ table
    using a single MERGE/UPDATE DML keyed on File Name + Attribute Name.
    """
    if not rows:
        return

    # Resolve full table reference
    if "." not in table_name or table_name.count(".") < 2:
        full_table = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"
    else:
        full_table = table_name

    client = get_bigquery_client()

    select_rows = [
        "SELECT '{}' AS file_name, '{}' AS attr_name, '{}' AS most_occ".format(
            (row.get("File Name") or row.get("file_name") or "").replace("'", "''"),
            (row.get("Attribute Name") or row.get("field_name") or "").replace("'", "''"),
            ", ".join(str(v) for v in (row.get("Most Occurrences") or row.get("most_occurrences") or [])).replace("'", "''"),
        )
        for row in rows
    ]
    if not select_rows:
        return

    union_clause = "\n        UNION ALL\n        ".join(select_rows)
    dml = f"""
        MERGE `{full_table}` T
        USING (
            {union_clause}
        ) S
        ON T.`File Name` = S.file_name AND T.`Attribute Name` = S.attr_name
        WHEN MATCHED THEN UPDATE SET
            T.`Most Occurrences` = S.most_occ
    """
    try:
        client.query(dml).result()
        logger.info("[DD_ENRICH] Updated %d rows in %s", len(rows), full_table)
    except Exception as exc:
        logger.warning("[DD_ENRICH] BQ update failed for %s: %s", full_table, exc)


def _enrich_metadata_rows(rows: list[dict]) -> list[dict]:
    """
    For each metadata row, query the source BQ table to fill:
    - Most_Occurrences: top-N most frequent values
    Uses File_Name / Attribute_Name column naming (metadata_template_ schema).
    """
    top_n = getattr(config, "DD_MOST_OCCURRENCES_TOP_N", 5)
    client = get_bigquery_client()

    tables: dict = defaultdict(list)
    for row in rows:
        file_name = row.get("File_Name") or row.get("file_name") or ""
        if file_name:
            tables[file_name].append(row)

    for table_name, table_rows in tables.items():
        full_table = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"
        columns = [
            r.get("Attribute_Name") or r.get("attribute_name") or ""
            for r in table_rows
        ]
        columns = [c for c in columns if c]
        if not columns:
            continue
        try:
            parts = [f"APPROX_TOP_COUNT(`{col}`, {top_n}) AS `{col}_top`" for col in columns]
            sql = f"SELECT COUNT(*) AS total_rows, {', '.join(parts)} FROM `{full_table}`"
            bq_row = next(iter(client.query(sql).result()))
            total_rows = bq_row["total_rows"]

            for meta_row in table_rows:
                col = meta_row.get("Attribute_Name") or meta_row.get("attribute_name") or ""
                if not col:
                    continue
                top_info = bq_row.get(f"{col}_top") or []
                meta_row["Most_Occurrences"] = ", ".join(
                    f"{e['value']} ({round(e['count'] / total_rows * 100)}%)"
                    for e in top_info if e["value"] is not None
                )
                # Remove any existing Default_Value field
                meta_row.pop("Default_Value", None)
                meta_row.pop("default_value", None)
        except Exception as exc:
            logger.warning("[META_ENRICH] BQ query failed for table %s: %s", table_name, exc)
            for meta_row in table_rows:
                meta_row.setdefault("Most_Occurrences", "")
                # Remove any existing Default_Value field
                meta_row.pop("Default_Value", None)
                meta_row.pop("default_value", None)

    return rows


def _write_enriched_metadata_to_bq(table_name: str, rows: list[dict]) -> None:
    """
    Writes enriched Most_Occurrences back to the metadata_template BQ table.
    """
    if not rows:
        return

    if "." not in table_name or table_name.count(".") < 2:
        full_table = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"
    else:
        full_table = table_name

    client = get_bigquery_client()

    select_rows_meta = [
        "SELECT '{}' AS file_name, '{}' AS attr_name, '{}' AS most_occ".format(
            (row.get("File_Name") or row.get("file_name") or "").replace("'", "''"),
            (row.get("Attribute_Name") or row.get("attribute_name") or "").replace("'", "''"),
            str(row.get("Most_Occurrences") or "").replace("'", "''"),
        )
        for row in rows
    ]
    if not select_rows_meta:
        return

    union_clause_meta = "\n        UNION ALL\n        ".join(select_rows_meta)
    dml = f"""
        MERGE `{full_table}` T
        USING (
            {union_clause_meta}
        ) S
        ON T.`File_Name` = S.file_name AND T.`Attribute_Name` = S.attr_name
        WHEN MATCHED THEN UPDATE SET
            T.`Most_Occurrences` = S.most_occ
    """
    try:
        client.query(dml).result()
        logger.info("[META_ENRICH] Updated %d rows in %s", len(rows), full_table)
    except Exception as exc:
        logger.warning("[META_ENRICH] BQ update failed for %s: %s", full_table, exc)


@router.get("/table")
async def get_bq_table_data(table_name: str = Query(..., description="The name of the table in project.dataset.table format or just table_id")):
    """
    Retrieves data from a BigQuery table and returns it in JSON and Markdown formats.
    """
    try:
        df = get_table(table_name)

        if df is None:
            raise HTTPException(status_code=404, detail=f"Table {table_name} not found or empty.")

        json_data = df.to_dict(orient="records")

        # Enrich with default_value and most_occurrences for data dictionary tables
        if "datadict_" in table_name:
            json_data = _enrich_dd_rows(json_data)
            _write_enriched_columns_to_bq(table_name, json_data)
            df = pd.DataFrame(json_data)
        elif "metadata_template_" in table_name:
            json_data = _enrich_metadata_rows(json_data)
            _write_enriched_metadata_to_bq(table_name, json_data)
            df = pd.DataFrame(json_data)

        markdown_data = df.to_markdown(index=False)

        return {
            "status": "success",
            "table_name": table_name,
            "tool_response": json_data,
            "text_response": markdown_data
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving table data: {str(e)}")


@router.get("/table-schema")
async def get_table_schema(
    table_name: str = Query(..., description="BigQuery table name (short name or full reference project.dataset.table)"),
    dataset_id: Optional[str] = Query(None, description="Optional dataset ID override (defaults to config.BQ_DATASET_ID)")
) -> Dict[str, Any]:
    """
    Fetch schema (columns and datatypes) for a BigQuery table.

    **Purpose**: Support UI features that need table column information for filter
    configuration, column selection, or schema validation.

    **Usage**:
    - GET /api/table-schema?table_name=my_table
    - GET /api/table-schema?table_name=my_table&dataset_id=CUSTOM_DATASET
    - GET /api/table-schema?table_name=project.dataset.my_table

    **Returns**:
    ```json
    {
        "status": "success",
        "table_name": "project.dataset.my_table",
        "total_columns": 10,
        "columns": [
            {
                "name": "column_name",
                "data_type": "STRING",
                "mode": "NULLABLE",
                "description": "Column description"
            },
            ...
        ]
    }
    ```

    **Args**:
        table_name: Table name (short or full reference)
        dataset_id: Optional dataset ID (defaults to config.BQ_DATASET_ID)

    **Returns**:
        Dict with status, table_name, total_columns, and columns list

    **Raises**:
        HTTPException: 404 if table not found, 500 for other errors
    """
    try:
        logger.info(f"[table-schema] Fetching schema for table: {table_name}")
        logger.info(f"[table-schema] dataset_id override: {dataset_id}")

        # Build full table reference
        if '.' not in table_name or table_name.count('.') < 2:
            # Short table name - add project.dataset prefix
            dataset = dataset_id if dataset_id else config.BQ_DATASET_ID
            full_table_ref = f"{config.PROJECT_ID}.{dataset}.{table_name}"
            logger.info(f"[table-schema] Built full reference: {full_table_ref}")
        else:
            # Already a full reference
            full_table_ref = table_name
            logger.info(f"[table-schema] Using provided full reference: {full_table_ref}")

        # Get BigQuery client and fetch table metadata
        client = get_bigquery_client()
        table = client.get_table(full_table_ref)

        logger.info(f"[table-schema] ✓ Table found with {len(table.schema)} columns")

        # Detect SCD Type 2 tables
        is_type2 = False
        table_to_use = table
        table_ref_to_use = full_table_ref

        # Extract the table name part (without project.dataset prefix)
        table_parts = full_table_ref.split('.')
        if len(table_parts) == 3:
            project_id, dataset_id_part, base_table_name = table_parts

            # Check if input already has _cur suffix (case-insensitive)
            if base_table_name.lower().endswith('_cur'):
                logger.info(f"[table-schema] Input table already has _cur suffix: {base_table_name}")
                # Input is already a _cur table - check if it has all 4 SCD columns
                required_scd_columns = {"RW_EFF_DT", "RW_EXP_DT", "PRV_EFF_DT", "PRV_EXP_DT"}
                cur_column_names = {field.name for field in table.schema}

                if required_scd_columns.issubset(cur_column_names):
                    is_type2 = True
                    logger.info(f"[table-schema] ✓ Type 2 SCD detected: Input _cur table has all 4 required columns")
                else:
                    missing_columns = required_scd_columns - cur_column_names
                    logger.info(f"[table-schema] Input _cur table missing SCD columns: {missing_columns}")
            else:
                # Check if corresponding _cur table exists
                cur_table_ref = f"{project_id}.{dataset_id_part}.{base_table_name}_cur"

                try:
                    # First check if _cur table exists
                    cur_table = client.get_table(cur_table_ref)
                    logger.info(f"[table-schema] ✓ _cur table exists: {cur_table_ref}")

                    # Then check if _cur table has all 4 required SCD Type 2 columns
                    required_scd_columns = {"RW_EFF_DT", "RW_EXP_DT", "PRV_EFF_DT", "PRV_EXP_DT"}
                    cur_column_names = {field.name for field in cur_table.schema}

                    if required_scd_columns.issubset(cur_column_names):
                        is_type2 = True
                        table_to_use = cur_table
                        table_ref_to_use = cur_table_ref
                        logger.info(f"[table-schema] ✓ Type 2 SCD detected: {base_table_name} (using _cur table schema)")
                    else:
                        missing_columns = required_scd_columns - cur_column_names
                        logger.info(f"[table-schema] _cur table exists but missing SCD columns: {missing_columns}")

                except Exception as cur_err:
                    # _cur table doesn't exist
                    logger.info(f"[table-schema] No _cur table found: {cur_table_ref}")
        else:
            logger.warning(f"[table-schema] Could not parse table reference for _cur check: {full_table_ref}")

        # Extract schema information from the appropriate table
        columns = []
        for field in table_to_use.schema:
            columns.append({
                "name": field.name,
                "data_type": field.field_type,
                "mode": field.mode,
                "description": field.description or ""
            })

        logger.info(f"[table-schema] ✓ Schema extraction complete from {table_ref_to_use}")

        return {
            "status": "success",
            "table_name": table_ref_to_use,
            "total_columns": len(columns),
            "columns": columns,
            "isType2": is_type2
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)

        logger.error(f"[table-schema] ❌ Error: {error_type}: {error_msg}")

        # Check if table not found
        if "Not found" in error_msg or "404" in error_msg:
            raise HTTPException(
                status_code=404,
                detail=f"Table not found: {table_name}. Error: {error_msg}"
            )

        # Other errors
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch table schema: {error_msg}"
        )
@router.get("/default-dataset")
async def get_default_dataset():
    """
    Returns the default BigQuery dataset ID from configuration.
    Used by the UI to get the default database name.
    """
    return {
        "status": "success",
        "dataset_id": Config.BQ_DATASET_ID
    }
