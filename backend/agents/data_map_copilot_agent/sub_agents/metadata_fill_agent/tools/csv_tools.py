import pandas as pd
import os
import json
from google.adk.tools.tool_context import ToolContext


DATA_PATH = "data/metadata_result.csv"

def append_metadata_chunk_to_csv(rows_json: str) -> str:
    """
    Appends a list of dictionary rows (JSON string) to the metadata result CSV.
    Creates the file with headers if it doesn't exist.
    """
    try:
        data = json.loads(rows_json)
        df_chunk = pd.DataFrame(data)
        
        # Check if file exists to determine if we need to write headers
        header = not os.path.exists(DATA_PATH)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        
        df_chunk.to_csv(DATA_PATH, mode='a', header=header, index=False)
        return f"Successfully appended {len(data)} rows to {DATA_PATH}."
    except Exception as e:
        return f"Error appending data: {str(e)}"

def signal_exit(tool_context: ToolContext):
  """Call this function ONLY when the critique indicates no further changes are needed, signaling the iterative process should end."""
  print(f"  [Tool Call] exit_loop triggered by {tool_context.agent_name}")
  tool_context.actions.escalate = True
  # Return empty dict as tools should typically return JSON-serializable output
  return {}
