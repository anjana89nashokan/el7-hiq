import os
import sys
import time
import math
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import vertexai
from vertexai.language_models import TextEmbeddingModel
from utils import local_warehouse as bigquery
from google.api_core.exceptions import ResourceExhausted

# -------------------------------
# PATH SETUP
# -------------------------------
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../")
)
sys.path.append(PROJECT_ROOT)

from config.settings import config
from bq_client import get_bq_client
from utils.indemap_db_utils import execute_indemap_query_df

# -------------------------------
# CONFIG
# -------------------------------
TARGET_TABLE = "datamap_simialrity_search_FYI_search"

CHUNK_SIZE = 50
MAX_WORKERS = 5
MAX_RETRIES = 5
BATCH_COOLDOWN = 1

BATCH_SIZE = 10000
DB_NAME = "DB_AEDWD2V"

EMBEDDING_COLUMN = "combined_embedding"

# -------------------------------
# INIT VERTEX AI
# -------------------------------
vertexai.init(
    project=config.GOOGLE_CLOUD_PROJECT,
    location="us-central1"
)

embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-005")

# -------------------------------
# HELPERS
# -------------------------------
def chunk_list(data, size):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def embed_with_retry(text_chunk):
    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = embedding_model.get_embeddings(text_chunk)
            return [r.values for r in response]

        except ResourceExhausted:
            wait_time = 2 ** retries
            print(f"429 received. Retrying in {wait_time}s...")
            time.sleep(wait_time)
            retries += 1

    raise Exception("Max retries exceeded due to rate limits.")


def generate_embeddings(text_list):
    cleaned = []

    for t in text_list:
        if t is None:
            cleaned.append(" ")
        else:
            val = str(t).strip()
            cleaned.append(val if val else " ")

    chunks = list(chunk_list(cleaned, CHUNK_SIZE))
    results = [None] * len(cleaned)

    def process_chunk(start_index, chunk):
        embeddings = embed_with_retry(chunk)
        return start_index, embeddings

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        start = 0

        for chunk in chunks:
            futures.append(
                executor.submit(process_chunk, start, chunk)
            )
            start += len(chunk)

        for future in futures:
            start_index, embeddings = future.result()
            results[start_index:start_index + len(embeddings)] = embeddings

    time.sleep(BATCH_COOLDOWN)
    return results


def build_combined_text(column_name, attr_name, description):
    parts = [
        str(column_name).strip() if column_name else "",
        str(attr_name).strip() if attr_name else "",
        str(description).strip() if description else "",
    ]

    combined = ". ".join(p for p in parts if p)
    return combined if combined else " "


# -------------------------------
# MAIN PIPELINE
# -------------------------------
def load_batches():

    client = get_bq_client()

    # 🔹 Truncate target table
    print("Truncating target table...")
    client.query(
        f"TRUNCATE TABLE `{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{TARGET_TABLE}`"
    ).result()

    # 🔹 Count total rows
    count_query = """
    SELECT COUNT(*) AS total_count
    FROM MDR.dbo.DB_TBL_VW_COLM VW
    INNER JOIN MDR.dbo.COLM CO
        ON CO.COLM_NM = VW.COLM_NM
    WHERE VW.DB_NM = :db_name
    """

    count_df = execute_indemap_query_df(
        count_query, {"db_name": DB_NAME}
    )

    total_rows = int(count_df.iloc[0]["total_count"])
    print(f"Total rows: {total_rows}")

    num_batches = math.ceil(total_rows / BATCH_SIZE)

    for batch_num in range(num_batches):

        print(f"\nProcessing batch {batch_num + 1}/{num_batches}")

        offset = batch_num * BATCH_SIZE

        batch_query = f"""
        SELECT
            VW.DB_NM,
            VW.TBL_VW_NM,
            TBL.ENTY_NM,
            TBL.ENTY_DSC,
            VW.COLM_NM,
            CO.ATTR_NM,
            CO.ATTR_DSC
        FROM MDR.dbo.DB_TBL_VW_COLM VW
        INNER JOIN MDR.dbo.COLM CO
            ON CO.COLM_NM = VW.COLM_NM
        INNER JOIN MDR.dbo.TBL_VW TBL
            ON TBL.TBL_VW_NM = VW.TBL_VW_NM
        WHERE VW.DB_NM = :db_name
        ORDER BY VW.TBL_VW_NM
        OFFSET {offset} ROWS FETCH NEXT {BATCH_SIZE} ROWS ONLY
        """

        df = execute_indemap_query_df(
            batch_query, {"db_name": DB_NAME}
        )

        if df.empty:
            print("No more data.")
            break

        # 🔹 Prepare columns
        df["database_name"] = df["DB_NM"]
        df["table_name"] = df["TBL_VW_NM"]
        df["table_business_name"] = df["ENTY_NM"]
        df["table_business_description"] = df["ENTY_DSC"]
        df["column_name"] = df["COLM_NM"]
        df["column_business_name"] = df["ATTR_NM"]
        df["column_business_description"] = df["ATTR_DSC"]
        df["last_updated"] = datetime.utcnow()
        df["indexed_at"] = datetime.utcnow()

        # 🔹 Build combined text
        print("Generating combined text...")
        df["combined_text"] = df.apply(
            lambda row: build_combined_text(
                row["column_name"],
                row["column_business_name"],
                row["column_business_description"]
            ),
            axis=1
        )

        # 🔹 Generate embeddings
        print("Generating embeddings...")
        df[EMBEDDING_COLUMN] = generate_embeddings(
            df["combined_text"].tolist()
        )

        # 🔹 Select final columns
        df = df[
            [
                "database_name",
                "table_name",
                "table_business_name",
                "table_business_description",
                "column_name",
                "column_business_name",
                "column_business_description",
                EMBEDDING_COLUMN,
                "last_updated",
                "indexed_at"
            ]
        ]

        # 🔹 Load into BigQuery
        print("Loading into BigQuery...")
        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND"
        )

        load_job = client.load_table_from_dataframe(
            df,
            f"{config.BQ_PROJECT_ID}.{config.BQ_DATASET_ID}.{TARGET_TABLE}",
            job_config=job_config
        )

        load_job.result()

        print(f"Batch {batch_num + 1} loaded successfully.")

    print("\nAll batches completed.")


# -------------------------------
# ENTRY POINT
# -------------------------------
if __name__ == "__main__":
    load_batches()