# server/data_map_copilot_ag.../utils/datadict_functions.py

import pandas as pd
import json
from typing import Dict, Any, List


import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _calculate_buffered_length(row: pd.Series) -> int:
    """
    Calculates a buffered string length based on the actual max length
    provided by the Profiling Agent.
    """
    # This logic is now dependent on the 'Max Length' field from the profiler.
    if row.get('Data Type') == 'STRING':
        # Safely get the 'Max Length' value from the row.
        max_len = row.get('Max Length', 0)

        # Apply the business rules discussed in the meetings.
        if pd.isna(max_len) or max_len == 0: return 50
        if max_len < 20: return 50
        if max_len < 100: return 255
        return int(max_len * 1.5) + 50
    # For non-string types, length is not applicable.
    return 0


def _calculate_precision_from_samples(sample_values):
    if not sample_values:
        return None
    max_precision = 0
    for val in sample_values:
        try:
            s = str(val)
            if "." in s:
                decimals = len(s.split(".")[1].rstrip("0"))
                if decimals > max_precision:
                    max_precision = decimals
        except:
            continue
    return max_precision if max_precision > 0 else None


def data_dictionary_tool(tool_input: Dict[str, Any]) -> dict:
    """
    Assembles the final technical Data Dictionary by merging the detailed outputs
    from the intelligent_profiling_tool and the relationship_analysis_tool.
    """

    try:
        profiling_output = tool_input.get("profiling_output", [])
        relationships_output = tool_input.get("relationships_output", {})

        # Get the optional validation output ---
        validation_output = tool_input.get("validation_output", {})
 
        print("\n--- [PRINT DEBUG | DD TOOL INPUTS] ---")
        print(f"  -> Received profiling_output: {'Yes' if profiling_output else 'No'}")
        print(f"  -> Received relationships_output: {'Yes' if relationships_output else 'No'}")
        print(f"  -> Received validation_output: {'Yes' if validation_output else 'No'}")

        # Validate early
        if not isinstance(profiling_output, list):
            return json.dumps({"error": "Expected 'profiling_output' to be a list of dictionaries."})
        if not profiling_output:
            return json.dumps({"error": "The 'profiling_output' list is empty."})

        # Extract relationship info only once
        rel_table_details = relationships_output.get("table_details", {})

        # Build rows quickly using list comprehension
        all_dict_rows: List[Dict[str, Any]] = [
            {
                "File Name": table_ref.split(".")[-1],
                "Field Name": col_name,
                "Data Type": analysis.get("data_type", "UNKNOWN"),
                "Sample Values": analysis.get("sample_values", []),
                "Primary Key": (
                    rel_table_details.get(table_ref.split(".")[-1], {})
                    .get("column_classifications", {})
                    .get(col_name, {})
                    .get("pk", "")
                ).title(),
                "Foreign Key": (
                    rel_table_details.get(table_ref.split(".")[-1], {})
                    .get("column_classifications", {})
                    .get(col_name, {})
                    .get("fk", "")
                ).title(),
            }
            for table_data in profiling_output
            if (table_ref := table_data.get("table_reference"))
            for col_name, analysis in (table_data.get("column_analysis") or {}).items()
        ]

        if not all_dict_rows:
            return json.dumps({"error": "No columns found in any of the profiling outputs."})

        # Create DataFrame from rows
        df = pd.DataFrame.from_records(all_dict_rows)

        # --- Step 2: (NEW) Create a Vendor Claims Lookup from the Validation Output ---
        vendor_claims_lookup = {}
        if validation_output:
            logger.log_agent_action("Validation output found. Parsing audit log to extract vendor claims.")
           
            # The actual log is inside the tool_response of the first item in the list
            audit_log = validation_output[0].get("tool_response", {}).get("validation_audit_log", [])
           
            for finding in audit_log:
                col_name = finding.get("column_name")
                check_type = finding.get("check_type")
                vendor_claim = finding.get("vendor_claim")
               
                if not col_name or not check_type:
                    continue
               
                if col_name not in vendor_claims_lookup:
                    vendor_claims_lookup[col_name] = {}
               
                # We only care about the Data Type claim for now, but can add more
                if "Data Type" in check_type:
                    vendor_claims_lookup[col_name]["Data Type"] = vendor_claim
                # We can add more elifs here for other fields like Nullability, PK, etc.
       
        print("\n--- [PRINT DEBUG | VENDOR CLAIMS LOOKUP] ---")
        print(json.dumps(vendor_claims_lookup, indent=2))
 
        # --- Step 3: Apply Logic and Enrich the DataFrame ---
        def enrich_row(row):
            field_name = row["Field Name"]
            vendor_claims = vendor_claims_lookup.get(field_name, {})
 
            # If a vendor claim for Data Type exists, use it. Otherwise, use our system's finding.
            if vendor_claims.get("Data Type"):
                row["Data Type"] = vendor_claims["Data Type"]
                print(f"  -> For '{field_name}', using vendor-claimed Data Type: '{row['Data Type']}'")
           
            # Placeholder for business name and description to be filled by LLM or BSA
            row["Field Business Name"] = ""
            row["Field Description"] = ""

            if row["Data Type"] in ["FLOAT", "DECIMAL"]:
                row["Precision"] = _calculate_precision_from_samples(row.get("Sample Values", []))
            else:
                row["Precision"] = None

            return row
 
        df = df.apply(enrich_row, axis=1)
 
        # =======================

        # Vectorized buffered length calculation (avoid row-wise apply)
        df["Length"] = df.apply(_calculate_buffered_length, axis=1)

        # Select and order final columns
        final_columns = ["File Name", "Field Name", "Data Type", "Length","Precision", "Primary Key", "Foreign Key"]
        # Ensure all required columns exist
        for col in final_columns:
            if col not in df.columns:
                df[col] = ""
       
        df_final = df[final_columns]
 
        return df_final.to_json(orient="records")
 
    except (KeyError, TypeError) as e:
        return json.dumps({"error": f"A required key is missing or data is malformed in the input artifacts: {e}"})
    except Exception as e:
        logger.error(f"Error in data_dictionary_tool: {e}", exc_info=True)
        return json.dumps({"error": f"Unexpected error while processing data dictionary: {e}"})
