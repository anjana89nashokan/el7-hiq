import pandas as pd
import os
import json

DATA_PATH = "data/final_data_dictionary.csv"

def append_chunk_to_csv(rows_json: str) -> dict:
    """
    Appends a list of dictionary rows (JSON string) to the data dictionary CSV.
    Creates the file with headers if it doesn't exist.

    Returns:
        {
            "status": "success" | "error",
            "rows_appended": int,
            "start_index": int,
            "end_index": int,
            "path": str,
            "message": str
        }
    """
    try:
        data = json.loads(rows_json)
        df_chunk = pd.DataFrame(data)

        # Ensure directory exists
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

        file_exists = os.path.exists(DATA_PATH)

        # Count existing rows (exclude header)
        if file_exists:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                existing_rows = sum(1 for _ in f) - 1
            existing_rows = max(existing_rows, 0)
        else:
            existing_rows = 0

        start_index = existing_rows
        end_index = existing_rows + len(df_chunk) - 1 if len(df_chunk) > 0 else existing_rows - 1

        # Append data
        df_chunk.to_csv(
            DATA_PATH,
            mode="a",
            header=not file_exists,
            index=False
        )

        return {
            "status": "success",
            "rows_appended": len(df_chunk),
            "start_index": start_index,
            "end_index": end_index,
            "path": DATA_PATH,
            "message": f"Successfully appended {len(df_chunk)} rows."
        }

    except Exception as e:
        return {
            "status": "error",
            "rows_appended": 0,
            "start_index": None,
            "end_index": None,
            "path": DATA_PATH,
            "message": f"Error appending data: {str(e)}"
        }



def load_final_csv() -> str:
    """
    Reads the full CSV file and returns it as a markdown string.
    """
    try:
        if not os.path.exists(DATA_PATH):
            return "Error: Data dictionary file not found."
        
        df = pd.read_csv(DATA_PATH)
        data = df.to_markdown(index=False)
        df.drop(df.index, inplace=True)
        return data
    except Exception as e:
        return f"Error loading data: {str(e)}"

