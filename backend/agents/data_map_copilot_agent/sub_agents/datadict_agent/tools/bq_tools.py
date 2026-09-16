from utils import local_warehouse as bigquery
from utils.bg_query_utils import get_bigquery_client
from config.settings import config
import json
import logging
import re
from typing import Any, List

from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)


def _strip_json_fence(text: str) -> str:
    if not isinstance(text, str):
        return text
    t = text.strip()
    t = re.sub(r"^```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n?", "", t)
    t = re.sub(r"\r?\n?```[ \t]*$", "", t)
    return t.strip()


def _parse_json_payload(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    text = _strip_json_fence(value.strip())
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return None


def _normalize_dd_rows(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        nested = data.get("data_dictionary")
        if isinstance(nested, list):
            return [row for row in nested if isinstance(row, dict)]
    if isinstance(data, str):
        parsed = _parse_json_payload(data)
        return _normalize_dd_rows(parsed)
    return []


def _qualify_table_name(table_name: str) -> str:
    prefix = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}"
    if table_name.startswith(f"{prefix}."):
        return table_name
    if table_name.count(".") == 2:
        return table_name
    return f"{prefix}.{table_name}"


def _append_rows_to_bq(rows: list[dict], table_name: str) -> dict:
    table_name = _qualify_table_name(table_name)
    client = get_bigquery_client()
    bq_rows = []
    for row in rows:
        bq_rows.append(
            {
                "File Name": row.get("file_name"),
                "Attribute Name": row.get("field_name"),
                "Logical Attribute Name": row.get("business_name"),
                "Attribute Description": row.get("field_description"),
                "Data Type": row.get("data_type"),
                "Length": str(row.get("length", "")),
                "Precision": str(row.get("precision", "")),
                "Format": row.get("format"),
                "Nullability": row.get("nullable"),
                "Most Occurrences": json.dumps(row.get("most_occurrences") or []),
                "Primary Key": row.get("primary_key"),
                "Foreign Key": row.get("foreign_key"),
            }
        )

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    load_job = client.load_table_from_json(bq_rows, table_name, job_config=job_config)
    load_job.result()
    return {
        "status": "success",
        "rows_appended": len(bq_rows),
        "table_name": table_name,
        "message": f"Successfully appended {len(bq_rows)} rows to BigQuery.",
    }

def sample_data_retrieval(table_names: List[str], profile_reports: List[str]) -> str:
    """
    Retrieves a range of rows from a BigQuery table.

    Args:
    table_name (str): The name of the table in project.dataset.table format.
    start_index (int): The starting index (offset).
    end_index (int): The ending index (used to calculate limit).

    Returns:
    str: A JSON string containing the list of rows formatted as data dictionary entries.
    """

    profile_reports = load_profile_reports(profile_reports)

    client = get_bigquery_client()

    # Calculate limit and offset
    offset = 0
    limit = 10

    data = {}
    # Ensure table_name is fully qualified if it's just the table id
    for table_name in table_names:
        if "." not in table_name:
            table_name = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"

        query = f"SELECT * FROM `{table_name}` LIMIT {limit} OFFSET {offset}"

        try:
            query_job = client.query(query)
            results = query_job.result()

            rows = []
            for row in results:
                row_dict = dict(row.items())
                rows.append(row_dict)
                data[table_name] = json.dumps(rows)

        except Exception as e:
            logger.error(f"Error fetching rows from BQ: {e}")
            return {"error": str(e)}

    with open("sample_data.json", "w") as f:
        json.dump(data, f)

    with open("profile_reports.json", "w") as f:
        json.dump(profile_reports, f)
    
    
    return {"orginal_sample_data": data, "profile_reports": profile_reports}

    


import os
import json
from typing import List

def load_profile_reports(file_names: List[str]) -> dict:
    """
    Reads profiling data from local reports and extracts specific column metadata.
    """
    # Assuming reports are in the root /reports directory as per your base_dir variable
    base_dir = "/Volumes/NEOVITA/UST Project/IBX DataMap-Co-Pilot/ibx-DataMap-Copilot/server/reports"
    print(base_dir)
    print("file_names", file_names)
    combined_reports = {}

    try:
        for file_name in file_names:
            file_path = os.path.join(base_dir, file_name)
            
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    raw_data = json.load(f)
                    
                # Extract only the "variables" section
                variables = raw_data.get("variables", {})
                processed_variables = {}

                for col_name, info in variables.items():
                    # 1. Get the highest occurring value from value_counts_without_nan
                    value_counts = info.get("value_counts_without_nan", {})
                    default_value = None
                    if value_counts:
                        # Find the key with the maximum count
                        default_value = max(value_counts, key=value_counts.get)

                    # 2. Build the simplified object for this column
                    processed_variables[col_name] = {
                        "data_type": info.get("type"),
                        "max_length": info.get("max_length"),
                        "null_values": info.get("n_missing")
                    }
                
                combined_reports[file_name] = processed_variables
            else:
                print(f"Warning: Report file {file_name} not found in {base_dir}")

        return {
            "status": "success",
            "profile_data": combined_reports
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

def append_chunk_to_bq(rows_json: str, table_name: str) -> dict:
    """
    Appends a list of dictionary rows (JSON string) to the BigQuery data dictionary table.

    Args:
        rows_json (str): JSON array of rows, or a generator payload with data_dictionary.
        table_name (str): The BigQuery table ID.
    """
    try:
        rows = _normalize_dd_rows(rows_json)
        if not rows:
            return {"status": "error", "message": "No valid data dictionary rows found in rows_json"}
        if not table_name:
            return {"status": "error", "message": "table_name is required"}
        return _append_rows_to_bq(rows, table_name)
    except Exception as e:
        logger.error(f"Error appending to BQ: {e}")
        return {"status": "error", "message": str(e)}


def save_generator_batch_to_bq(tool_context: ToolContext, table_name: str = "") -> dict:
    """
    Save the current generator batch to BigQuery using session state.

    Reads generator_agent_response from the ADK session so the saver agent does
    not need to embed large JSON payloads in tool-call arguments.
    """
    try:
        state = tool_context.state.to_dict()
        generator_payload = _parse_json_payload(state.get("generator_agent_response"))
        if not isinstance(generator_payload, dict):
            return {
                "status": "error",
                "message": "generator_agent_response is missing or not valid JSON",
            }

        rows = _normalize_dd_rows(generator_payload)
        if not rows:
            return {
                "status": "error",
                "message": "No data_dictionary entries found in generator_agent_response",
            }

        resolved_table = table_name or generator_payload.get("data_dictionary_table_name") or ""
        if not resolved_table:
            return {"status": "error", "message": "table_name is required"}

        return _append_rows_to_bq(rows, resolved_table)
    except Exception as e:
        logger.error(f"Error saving generator batch to BQ: {e}")
        return {"status": "error", "message": str(e)}

def load_final_bq(table_name: str) -> dict:
    """
    Loads the final data dictionary from BigQuery.
    """
    client = get_bigquery_client()
    try:
        # Ensure table_name is fully qualified
        if "." not in table_name:
            table_name = f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{table_name}"
            
        query = f"SELECT * FROM `{table_name}`"
        df = client.query(query).to_dataframe()
        
        # Convert back to a format friendly for the agent to process
        result_list = []
        for _, row in df.iterrows():
            item = {
                "file_name": row.get("File Name"),
                "field_name": row.get("Attribute Name"),
                "business_name": row.get("Logical Attribute Name"),
                "field_description": row.get("Attribute Description"),
                "data_type": row.get("Data Type"),
                "length": int(row.get("Length")) if str(row.get("Length")).isdigit() else 0,
                "precision": int(row.get("Precision")) if str(row.get("Precision")).isdigit() else 0,
                "format": row.get("Format"),
                "nullable": row.get("Nullability"),
                "most_occurrences": json.loads(row.get("Most Occurrences") or "[]"),
                "primary_key": row.get("Primary Key"),
                "foreign_key": row.get("Foreign Key"),
            }
            result_list.append(item)
            
        markdown = df.to_markdown(index=False)
        return {
            "status": "success",
            "markdown": markdown,
            "json": result_list,
            "total_rows": len(result_list)
        }
    except Exception as e:
        logger.error(f"Error loading from BQ: {e}")
        return {"status": "error", "message": str(e)}
