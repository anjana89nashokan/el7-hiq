import json
import logging
import time
from typing import Dict, Any
from utils.bg_query_utils import get_bigquery_client
from config.settings import config
from utils.profiling_artifact_store import load_profiling_session_context

from utils import local_warehouse as bigquery
from google.api_core.exceptions import NotFound

# Optional: BigQuery Storage Write API (not used in standalone/local SQLite mode).
try:
    from google.cloud import bigquery_storage_v1
    from google.cloud.bigquery_storage_v1 import writer
    from google.cloud.bigquery_storage_v1 import types
except Exception:  # noqa: BLE001
    bigquery_storage_v1 = None
    writer = None
    types = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _is_retryable_table_write_error(exc: Exception) -> bool:
    """
    BigQuery can briefly reject writes while a table is being truncated/replaced.
    Those failures usually succeed on a short retry.
    """
    message = str(exc).lower()
    return (
        "table is truncated" in message
        or ("not found" in message and "table" in message)
        or ("404" in message and "tables/" in message)
    )


def _run_bq_write_with_retry(write_operation, *, description: str, max_attempts: int = 5):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return write_operation()
        except Exception as exc:
            last_error = exc
            if attempt == max_attempts or not _is_retryable_table_write_error(exc):
                raise

            delay_seconds = min(0.5 * (2 ** (attempt - 1)), 4.0)
            logger.warning(
                "Retrying BigQuery write for %s after transient error (attempt %s/%s): %s",
                description,
                attempt,
                max_attempts,
                exc,
            )
            time.sleep(delay_seconds)

    if last_error:
        raise last_error


def _normalize_table_id(table_name: str) -> str:
    if table_name.startswith(f"{config.BQ_PROJECT_ID}."):
        return table_name
    return f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"


def _load_rows_to_bq(
    client: bigquery.Client,
    rows: list[dict],
    table_name: str,
    *,
    write_disposition: str,
    description: str,
) -> None:
    full_table_id = _normalize_table_id(table_name)
    job_config = bigquery.LoadJobConfig(
        write_disposition=write_disposition,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )

    def _write():
        load_job = client.load_table_from_json(
            rows, full_table_id, job_config=job_config
        )
        load_job.result()

    _run_bq_write_with_retry(_write, description=description)


def _build_filespecs_rows(session_data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"Field": "Physical File Name", "Value": session_data.get("physical_file_name")},
        {"Field": "Vendor Name", "Value": session_data.get("vendor_name")},
        {"Field": "Transfer Method", "Value": session_data.get("transfer_method")},
        {"Field": "Vendor Contact Name", "Value": session_data.get("vendor_contact_name")},
        {"Field": "Frequency Mode", "Value": session_data.get("file_delivery_frequency")},
        {"Field": "Vendor Phone Number", "Value": session_data.get("vendor_phone_number")},
        {"Field": "Dependencies", "Value": session_data.get("dependencies")},
        {"Field": "Vendor Email", "Value": session_data.get("vendor_contact_person")},
        {"Field": "Email Notification DL", "Value": session_data.get("email_notification_dl")},
        {"Field": "File Delimiter", "Value": None},
        {"Field": "File Extension", "Value": session_data.get("file_extension")},
        {"Field": "Date Timestamp Format", "Value": session_data.get("date_timestamp_format")},
        {"Field": "Header Record Number", "Value": session_data.get("header_record_number")},
        {"Field": "Trailer Record Number", "Value": session_data.get("trailer_record_number")},
        {"Field": "Quote Indicator", "Value": session_data.get("quote_indicator")},
        {"Field": "File Population Type", "Value": session_data.get("file_population_type")},
        {"Field": "File Compression Type", "Value": session_data.get("file_compression_type")},
        {"Field": "Receive File when no Data (Empty Files)", "Value": session_data.get("receive_file_when_no_data")},
        {"Field": "Assumptions", "Value": session_data.get("assumptions")},
        {"Field": "Vendor Server Name", "Value": session_data.get("vendor_server_name")},
    ]


def get_bq_table_rows_range(
    table_name: str, start_index: int, end_index: int
) -> Dict[str, Any]:
    """
    Retrieves a range of rows from a BigQuery table.
    """
    client = get_bigquery_client()

    offset = start_index
    limit = end_index - start_index

    if "." not in table_name:
        table_name = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"

    query = f"SELECT * FROM `{table_name}` LIMIT {limit} OFFSET {offset}"

    try:
        query_job = client.query(query)
        results = query_job.result()

        rows = []
        for row in results:
            rows.append(dict(row.items()))

        return {
            "start_index": start_index,
            "end_index": end_index,
            "rows": json.dumps(rows),
        }

    except Exception as e:
        logger.error(f"Error fetching rows from BQ: {e}")
        return {"error": str(e)}


def create_metadata_and_filespecs_tables(unique_id: str, session_id: str):
    """
    Ensures two BigQuery tables exist:
    - metadata_template_<UUID>
    - Filespecs_<UUID>

    Seeds Filespecs with profiling context only when the table is empty.
    """

    client = get_bigquery_client()

    # -----------------------------
    # Table names
    # -----------------------------
    metadata_table_name = f"metadata_template_{unique_id}"
    filespecs_table_name = f"Filespecs_{unique_id}"

    metadata_table_id = (
        f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{metadata_table_name}"
    )
    filespecs_table_id = (
        f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{filespecs_table_name}"
    )

    # -----------------------------
    # metadata_template schema
    # -----------------------------
    metadata_schema = [
        bigquery.SchemaField("File_Name", "STRING"),
        bigquery.SchemaField("Attribute_Name", "STRING"),
        bigquery.SchemaField("Logical_Attribute_Name", "STRING"),
        bigquery.SchemaField("Attribute_Description", "STRING"),
        bigquery.SchemaField("Data_Type", "STRING"),
        bigquery.SchemaField("Length", "STRING"),
        bigquery.SchemaField("Precision", "STRING"),
        bigquery.SchemaField("Format", "STRING"),
        bigquery.SchemaField("Nullability", "STRING"),
        bigquery.SchemaField("Default_Value", "STRING"),
        bigquery.SchemaField("Most_Occurrences", "STRING"),
        bigquery.SchemaField("Primary_Key", "STRING"),
        bigquery.SchemaField("Foreign_Key", "STRING"),
        bigquery.SchemaField("Alternate_Key1", "STRING"),
    ]

    # -----------------------------
    # Filespecs schema
    # -----------------------------
    filespecs_schema = [
        bigquery.SchemaField("Field", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("Value", "STRING"),
    ]

    # -----------------------------
    # Create metadata_template table
    # -----------------------------
    try:
        client.get_table(metadata_table_id)
        print(f"Table already exists: {metadata_table_id}")
    except NotFound:
        metadata_table = bigquery.Table(metadata_table_id, schema=metadata_schema)
        client.create_table(metadata_table)
        print(f"Created table: {metadata_table_id}")

    # -----------------------------
    # Create Filespecs table
    # -----------------------------
    filespecs_table_created = False
    try:
        client.get_table(filespecs_table_id)
        print(f"Table already exists: {filespecs_table_id}")
    except NotFound:
        filespecs_table = bigquery.Table(filespecs_table_id, schema=filespecs_schema)
        client.create_table(filespecs_table)
        filespecs_table_created = True
        print(f"Created table: {filespecs_table_id}")
    # -----------------------------
    # Load session data from shared profiling session context.
    # -----------------------------
    session_data = load_profiling_session_context(session_id) if session_id else {}
    logger.info("Profiling session context contents from Metadata BQ tools %s", session_data)

    # -----------------------------
    # Insert example rows into Filespecs
    # -----------------------------
    filespecs_rows = _build_filespecs_rows(session_data)

    filespecs_table = client.get_table(filespecs_table_id)
    should_seed_filespecs = filespecs_table_created or filespecs_table.num_rows == 0

    if should_seed_filespecs:
        _load_rows_to_bq(
            client,
            filespecs_rows,
            filespecs_table_id,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            description=f"seed Filespecs table {filespecs_table_id}",
        )
        print(f"Inserted {len(filespecs_rows)} rows into {filespecs_table_id}")
    else:
        logger.info(
            "Skipping Filespecs seed for existing populated table %s",
            filespecs_table_id,
        )

    return metadata_table_id, filespecs_table_id


def append_chunk_to_bq(rows_json: str, table_name: str) -> dict:
    """
    Appends a list of dictionary rows (JSON string) to the BigQuery metadata template table.

    Args:
        rows_json (str): The JSON string containing the list of rows.
        table_name (str): The full BigQuery table ID.
    """
    client = get_bigquery_client()
    try:
        data = json.loads(rows_json)

        # Mapping to metadata_template schema with underscores
        def get_value(row: dict, *keys, default=None):
            """
            Try multiple possible keys and return the first non-None value found.
            """
            for key in keys:
                if key in row and row[key] is not None:
                    return row[key]
            return default

        bq_rows = []

        for row in data:
            # Extract file name from attribute name prefix (e.g., "CUSTOMERS_CUST_ID" -> "CUSTOMERS")
            attr_name = get_value(row, "attribute_name", "Attribute Name", default="")
            file_name = get_value(row, "file_name", "File Name", default="")

            # If file_name not provided, try to extract from attribute name prefix
            if not file_name and attr_name and "_" in attr_name:
                file_name = attr_name.split("_")[0]

            bq_row = {
                "File_Name": file_name,
                "Attribute_Name": attr_name,
                "Logical_Attribute_Name": get_value(
                    row, "logical_attribute_name", "Logical Attribute Name"
                ),
                "Attribute_Description": get_value(
                    row, "attribute_description", "Attribute Description"
                ),
                "Data_Type": get_value(row, "data_type", "Data Type"),
                "Length": str(get_value(row, "length", "Length", default="")),
                "Precision": str(get_value(row, "precision", "Precision", default="")),
                "Format": get_value(row, "format", "Format"),
                "Nullability": get_value(row, "nullability", "Nullability"),
                "Default_Value": str(
                    get_value(row, "default_values", "Default Value", "Default_Value", default="")
                ),
                "Most_Occurrences": str(
                    get_value(row, "most_occurrences", "Most Occurrences", "Most_Occurrences", default="")
                ),
                "Primary_Key": str(
                    get_value(row, "primary_key", "Primary Key", default="0")
                ),
                "Foreign_Key": str(
                    get_value(row, "foreign_key", "Foreign Key", default="0")
                ),
                "Alternate_Key1": get_value(row, "alternate_key1", "Alternate Key1"),
            }

            bq_rows.append(bq_row)

        _load_rows_to_bq(
            client,
            bq_rows,
            table_name,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            description=f"append metadata rows into {table_name}",
        )

        return {
            "status": "success",
            "rows_appended": len(bq_rows),
            "table_name": table_name,
            "message": f"Successfully appended {len(bq_rows)} rows to BigQuery.",
        }
    except Exception as e:
        logger.error(f"Error appending to BQ: {e}")
        return {"status": "error", "message": str(e)}


def overwrite_chunk_to_bq(rows_json: str, table_name: str) -> dict:
    """
    Replaces the contents of the BigQuery metadata template table.
    """
    client = get_bigquery_client()
    try:
        data = json.loads(rows_json)

        def get_value(row: dict, *keys, default=None):
            for key in keys:
                if key in row and row[key] is not None:
                    return row[key]
            return default

        bq_rows = []
        for row in data:
            attr_name = get_value(row, "attribute_name", "Attribute Name", default="")
            file_name = get_value(row, "file_name", "File Name", default="")

            if not file_name and attr_name and "_" in attr_name:
                file_name = attr_name.split("_")[0]

            bq_rows.append(
                {
                    "File_Name": file_name,
                    "Attribute_Name": attr_name,
                    "Logical_Attribute_Name": get_value(
                        row, "logical_attribute_name", "Logical Attribute Name"
                    ),
                    "Attribute_Description": get_value(
                        row, "attribute_description", "Attribute Description"
                    ),
                    "Data_Type": get_value(row, "data_type", "Data Type"),
                    "Length": str(get_value(row, "length", "Length", default="")),
                    "Precision": str(get_value(row, "precision", "Precision", default="")),
                    "Format": get_value(row, "format", "Format"),
                    "Nullability": get_value(row, "nullability", "Nullability"),
                    "Default_Value": str(
                        get_value(row, "default_values", "Default Value", "Default_Value", default="")
                    ),
                    "Most_Occurrences": str(
                        get_value(row, "most_occurrences", "Most Occurrences", "Most_Occurrences", default="")
                    ),
                    "Primary_Key": str(
                        get_value(row, "primary_key", "Primary Key", default="0")
                    ),
                    "Foreign_Key": str(
                        get_value(row, "foreign_key", "Foreign Key", default="0")
                    ),
                    "Alternate_Key1": get_value(row, "alternate_key1", "Alternate Key1"),
                }
            )

        _load_rows_to_bq(
            client,
            bq_rows,
            table_name,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            description=f"overwrite metadata rows into {table_name}",
        )

        return {
            "status": "success",
            "rows_appended": len(bq_rows),
            "table_name": table_name,
            "message": f"Successfully overwrote {len(bq_rows)} rows in BigQuery.",
        }
    except Exception as e:
        logger.error(f"Error overwriting BQ metadata table: {e}")
        return {"status": "error", "message": str(e)}


def append_filespecs_to_bq(rows_json: str, table_name: str) -> dict:
    """
    Appends a list of dictionary rows (JSON string) to the BigQuery Filespecs table
    using the BigQuery Storage Write API (JsonStreamWriter).

    Args:
        rows_json (str): The JSON string containing the list of rows.
        table_name (str): The table ID (e.g., "my_table" or "project.dataset.table").
    """
    try:
        # 1. Parse the Data
        data = json.loads(rows_json)

        bq_rows = []
        for row in data:
            bq_row = {"Field": row.get("Field") or "", "Value": row.get("Value") or ""}
            bq_rows.append(bq_row)

        if not bq_rows:
            return {"status": "success", "message": "No rows to append."}

        client = get_bigquery_client()
        full_table_id = _normalize_table_id(table_name)
        _load_rows_to_bq(
            client,
            bq_rows,
            full_table_id,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            description=f"append Filespecs rows into {full_table_id}",
        )

        return {
            "status": "success",
            "rows_appended": len(bq_rows),
            "table_name": full_table_id,
            "message": f"Successfully appended {len(bq_rows)} rows to Filespecs table.",
        }

    except Exception as e:
        logger.error(f"Error appending to Filespecs in BQ via Storage API: {e}")
        return {"status": "error", "message": str(e)}


def overwrite_filespecs_in_bq(rows_json: str, table_name: str) -> dict:
    """
    Overwrites the BigQuery Filespecs table with a list of dictionary rows (JSON string).
    """
    try:
        data = json.loads(rows_json)
        bq_rows = []
        for row in data:
            bq_row = {"Field": row.get("Field") or "", "Value": row.get("Value") or ""}
            bq_rows.append(bq_row)

        full_table_id = table_name
        if not table_name.startswith(f"{config.BQ_PROJECT_ID}."):
            full_table_id = (
                f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"
            )

        client = get_bigquery_client()
        _load_rows_to_bq(
            client,
            bq_rows,
            full_table_id,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            description=f"overwrite Filespecs rows into {full_table_id}",
        )

        return {
            "status": "success",
            "rows_appended": len(bq_rows),
            "table_name": full_table_id,
            "message": f"Successfully overwrote {len(bq_rows)} rows to Filespecs table.",
        }
    except Exception as e:
        logger.error(f"Error overwriting Filespecs in BQ: {e}")
        return {"status": "error", "message": str(e)}
