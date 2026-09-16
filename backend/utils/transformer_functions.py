import pandas as pd
import json
from typing import Dict, Any

# Assuming your logger is set up
try:
    from utils.bg_query_utils import DataMapLogger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    class DataMapLogger:
        def __init__(self, name): self.name = name
        def log_agent_action(self, msg): logger.info(f"[{self.name}] {msg}")

# --- NEW HELPER FUNCTION FOR LENGTH CALCULATION ---
def _calculate_length_from_profiler(row: Dict[str, Any]) -> int:
    """
    Calculates a buffered string length based on the 'avg_length' provided
    by the Profiling Agent, since 'max_length' is not available.
    This logic is inspired by the agent-created DD workflow.
    """
    if row.get('Data Type') == 'STRING':
        # Use 'avg_length' as it's the only length metric available from the profiler
        avg_len = row.get('Average Length')
        
        # Apply a similar buffering logic
        if avg_len is None or pd.isna(avg_len) or avg_len == 0: return 50
        if avg_len < 20: return 50
        if avg_len < 100: return 255
        # For larger average lengths, provide a generous buffer
        return int(avg_len * 2) + 50
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


def create_ground_truth_summary(profiling_output: Dict[str, Any], relationships_output: Dict[str, Any]) -> Dict[str, Any]:
    """
    (Final Version) Transforms raw agent outputs into a clean 'ground truth' summary
    that precisely matches the detailed Data Dictionary requirements, without modifying upstream agents.
    """
    logger = DataMapLogger("ground_truth_transformer")
    logger.log_agent_action("Starting transformation to match detailed DD specification.")
    
    ground_truth_dict = {}
    
    try:
        profiling_result = profiling_output.get("result", [])[0] if profiling_output.get("result") else {}
        if not profiling_result:
            logger.log_agent_action("Error: Profiling output is empty. Cannot create ground truth.")
            return {"error": "Profiling output is empty."}
            
        table_ref = profiling_result.get("table_reference")
        table_name = table_ref.split(".")[-1]
        column_analysis = profiling_result.get("column_analysis", {})
        default_analysis = profiling_result.get("default_value_analysis", {})
        column_classifications = relationships_output.get("table_details", {}).get(table_name, {}).get("column_classifications", {})

        print("\n" + "="*20 + " [TRANSFORMER INPUTS] " + "="*20)
        print("--- Received Column Analysis (sample) ---")
        print(json.dumps(list(column_analysis.items())[0:2], indent=2))
        print("--- Received Default Analysis (sample) ---")
        print(json.dumps(list(default_analysis.items())[0:2], indent=2))
        print("--- Received Column Classifications (sample) ---")
        print(json.dumps(list(column_classifications.items())[0:2], indent=2))
        print("="*65 + "\n")

        for col_name, analysis in column_analysis.items():
            print(f"\n--- [PRINT DEBUG] Transforming Column: [{col_name}] ---")
            
            # --- Step 1: Extract all available raw data ---
            null_pct = analysis.get("null_percentage", 0.0)
            data_type = analysis.get("data_type", "UNKNOWN")
            avg_len = analysis.get("avg_length")
            unique_count = analysis.get("unique_count")
            sample_values = analysis.get("sample_values", [])

            
            default_info = default_analysis.get(col_name, {})
            default_pct = default_info.get("default_pct", 0.0)
            default_val = default_info.get("default_value")
            
            key_info = column_classifications.get(col_name, {})
            is_pk = key_info.get("pk") == "yes"
            is_fk = key_info.get("fk") == "yes"
            
            # --- Step 2: Assemble a temporary dictionary for the length calculation ---
            temp_row_for_length = {
                "Data Type": data_type,
                "Average Length": avg_len
            }
            
            # --- Step 3: Apply all business rules from your document ---
            calculated_length = _calculate_length_from_profiler(temp_row_for_length)
            nullability_rule = 'Y' if null_pct == 100.0 else 'N'
            default_value_rule = default_val if default_pct == 100.0 and unique_count == 1 else ""

            if data_type in ["FLOAT", "DECIMAL"]:
                precision_value = _calculate_precision_from_samples(sample_values)
            else:
                precision_value = None


            # --- Step 4: Assemble the final ground truth object for this column ---
            ground_truth_dict[col_name] = {
                "Attribute Name": col_name,
                "Data Type": data_type,
                "Length": calculated_length,
                "Nullability": nullability_rule,
                "Default Values": default_value_rule,
                "Primary Key": 'Y' if is_pk else 'N',
                "Foreign Key": 'Y' if is_fk else 'N',
                # Add placeholders for other fields from your DD doc
                "Precision": precision_value,
                "Format": None,
                "Alternate Key 1": None
            }
            print(f"  -> [PRINT DEBUG] Transformed Output: {ground_truth_dict[col_name]}")

        logger.log_agent_action(f"Successfully created ground truth summary for {len(ground_truth_dict)} columns.")
        print("\n" + "="*20 + " [FINAL TRANSFORMER OUTPUT] " + "="*20)
        print(json.dumps(ground_truth_dict, indent=2))
        print("="*68 + "\n")
        
        return ground_truth_dict

    except Exception as e:
        logger.log_agent_action(f"Error during transformation: {e}")
        logging.error("Transformation failed", exc_info=True)
        return {"error": f"Failed to create ground truth summary: {e}"}