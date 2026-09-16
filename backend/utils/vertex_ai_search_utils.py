from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from fastapi import UploadFile, HTTPException
from google.api_core.client_options import ClientOptions
from google.cloud import storage
from utils import local_warehouse as bigquery
from google.cloud import discoveryengine_v1 as discoveryengine

from config.settings import config
from utils.bg_query_utils import (
    create_data_dictionary_table,
    get_tables_metadata,
    upload_to_bigquery_sync,
)

## Function to extract response from the retirever 
def extract_json_from_llm_text(text: str) -> dict:
    """
    Extracts JSON from LLM output even if wrapped in markdown fences.
    Supports:
    - ```json ... ```
    - ``` ... ```
    - text before/after JSON
    """
    text = text.strip()

    # Case 1: fenced code block
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        candidate = text

    # Case 2: extract first {...} JSON object
    match = re.search(r"\{[\s\S]*\}", candidate)
    if not match:
        raise ValueError(f"No JSON object found in text:\n{text}")

    json_str = match.group(0)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON after extraction.\nExtracted:\n{json_str}") from e

## Function to retrieve the information from datastore 

def answer_query_data_dictionary_json(
    query: str,
    project_id: str = config.PROJECT_ID,
    location: str = config.DATASTORE_LOCATION,
    engine_id: str = config.VERTEX_AI_APP_ID,
):
    if getattr(config, "STANDALONE_MODE", False) or not engine_id:
        # No Discovery Engine data-dictionary datastore locally — return empty.
        return {}
    client_options = (
        ClientOptions(api_endpoint=f"{location}-discoveryengine.googleapis.com")
        if location != "global"
        else None
    )

    client = discoveryengine.ConversationalSearchServiceClient(
        client_options=client_options
    )

    serving_config = (
        f"projects/{project_id}/locations/{location}/collections/default_collection"
        f"/engines/{engine_id}/servingConfigs/default_serving_config"
    )

    query_understanding_spec = discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec(
        query_rephraser_spec=discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec.QueryRephraserSpec(
            disable=False,
            max_rephrase_steps=2
        )
    )

    # Force JSON-only response
    json_preamble = """
You are a data-extraction system.

Task:
Extract the DATA DICTIONARY from the response.

Output rules:
- Output MUST be valid JSON ONLY. No markdown. No explanation.
- If a field is missing in the document, set it to null.
- Do NOT hallucinate (only extract what is explicitly present).
- Preserve table/column names exactly as in document.
- In the JSON schema put the column names from the document as keys 

Return JSON schema EXACTLY for the provided columns only, donot provide anything extra:
Return the most appropriate JSON schema if there are more in the response 

Return STRICT JSON only in this format:
{{
  "rows": [
    {{
      "File Name": "{table_id}",
      "Attribute Name": "<column_name>",
      "Logical Attribute Name": "<business friendly name>",
      "Attribute Description": "<meaning>",
      "Data Type": "<STRING/INTEGER/etc>",
      "Length": "",
      "Precision": "",
      "Format": "",
      "Nullability": "<NULLABLE/REQUIRED>",
      "Default Value": "",
      "Primary Key": "",
      "Foreign Key": ""
    }}
  ]
}}

Rules:
- One row per column from this batch.
- Keep output valid JSON.
"""

    answer_generation_spec = discoveryengine.AnswerQueryRequest.AnswerGenerationSpec(
        ignore_adversarial_query=False,
        ignore_non_answer_seeking_query=False,
        ignore_low_relevant_content=False,
        model_spec=discoveryengine.AnswerQueryRequest.AnswerGenerationSpec.ModelSpec(
            model_version="gemini-2.5-flash/answer_gen/v1",
        ),
        prompt_spec=discoveryengine.AnswerQueryRequest.AnswerGenerationSpec.PromptSpec(
            preamble=json_preamble
        ),
        include_citations=True,
        answer_language_code="en",
    )

    # Ask explicitly for data dictionary
    request = discoveryengine.AnswerQueryRequest(
        serving_config=serving_config,
        query=discoveryengine.Query(
            text=query
        ),
        session=None,
        query_understanding_spec=query_understanding_spec,
        answer_generation_spec=answer_generation_spec,
        user_pseudo_id="user-pseudo-id",
    )

    response = client.answer_query(request)

    print(response)
    # Answer text from response
    answer_text = ""
    if response.answer and response.answer.answer_text:
        answer_text = response.answer.answer_text.strip()

    # Parse JSON strictly
    try:
        parsed = extract_json_from_llm_text(answer_text)
    except Exception as e:
        raise ValueError(
            f"Model did not return valid JSON.\n\nRaw output:\n{answer_text}"
        ) from e

    return parsed

## Function to load the gcs bucket file to datastore linked to application 
def full_import_to_existing_datastore(
    gcs_uris: list[str], # list of gs://bucket/path/file.pdf
    project_id: str = config.PROJECT_ID,
    location: str = config.DATASTORE_LOCATION,# "global", "us", "eu"
    data_store_id: str = config.DATASTORE_ID, 
):
    client_options = (
        ClientOptions(api_endpoint=f"{location}-discoveryengine.googleapis.com")
        if location != "global"
        else None
    )

    client = discoveryengine.DocumentServiceClient(client_options=client_options)

    parent = (
        f"projects/{project_id}/locations/{location}/collections/default_collection"
        f"/dataStores/{data_store_id}/branches/default_branch"
    )

    request = discoveryengine.ImportDocumentsRequest(
        parent=parent,
        gcs_source=discoveryengine.GcsSource(
            input_uris=gcs_uris,
            data_schema="content"  # unstructured docs (PDF/DOCX/HTML/TXT)
        ),
        reconciliation_mode=discoveryengine.ImportDocumentsRequest.ReconciliationMode.FULL
    )

    op = client.import_documents(request=request)
    print("Import started:", op.operation.name)

    result = op.result(timeout=1000)
    print("FULL import completed")
    return result

import pandas as pd
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def chunk_list(items, batch_size=10):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]

def build_query_prompt(
    table_id: str,
    schema_batch: List[dict]
) -> str:
    # Wrap column names in double quotes for exact keyword matching in the JSONL
    cols_str = " ".join([f'"{c["name"]}"' for c in schema_batch])

    return f'{cols_str}'
    
def normalize_llm_rows(rows: List[Dict[str, Any]], table_id: str) -> List[Dict[str, Any]]:
    """
    Ensure required fields exist so df concat doesn't break.
    """
    required_cols = [
        "File Name",
        "Attribute Name",
        "Logical Attribute Name",
        "Attribute Description",
        "Data Type",
        "Length",
        "Precision",
        "Format",
        "Nullability",
        "Default Value",
        "Primary Key",
        "Foreign Key",
    ]

    normalized = []
    for r in rows:
        if not isinstance(r, dict):
            continue

        # Ensure file name always filled
        r.setdefault("File Name", table_id)

        # Fill missing required keys
        for c in required_cols:
            r.setdefault(c, "")

        normalized.append(r)

    return normalized


def generate_data_dictionary_in_batches(
    project_id: str,
    dataset_id: str,
    source_table_id: str,
    table_schema: List[dict],
    dictionary_table_id: str,
    batch_size: int = 10
):
    """
    1) Process schema in batches
    2) Call LLM each batch
    3) Append responses into a single dataframe
    4) After all batches: create BQ table + upload dataframe
    """

    total_cols = len(table_schema)
    logger.info(f"Generating dictionary for {total_cols} columns in batches of {batch_size}")

    all_rows: List[Dict[str, Any]] = []
    df_master = pd.DataFrame()   # will keep growing

    for batch_num, schema_batch in enumerate(chunk_list(table_schema, batch_size=batch_size), start=1):
        logger.info(f"Batch {batch_num}: {len(schema_batch)} columns")

        prompt = build_query_prompt(
            table_id=source_table_id,
            schema_batch=schema_batch
        )
        logger.info(f"Prompt for the current batch:\n{prompt}")

        
        llm_json = answer_query_data_dictionary_json(query=prompt,
                                                 project_id=config.PROJECT_ID,
                                                 location=config.DATASTORE_LOCATION,
                                                 engine_id=config.VERTEX_AI_APP_ID)
        logger.info(llm_json)
        # Validate response
        if not isinstance(llm_json, dict) or "rows" not in llm_json:
            raise ValueError(f"LLM response invalid for batch {batch_num}: {llm_json}")

        rows = llm_json["rows"]
        if not isinstance(rows, list):
            raise ValueError(f"LLM rows not list for batch {batch_num}: {type(rows)}")

        # Normalize rows
        rows = normalize_llm_rows(rows, source_table_id)

        # Append to list + dataframe
        all_rows.extend(rows)

        df_batch = pd.DataFrame(rows)
        df_master = pd.concat([df_master, df_batch], ignore_index=True)

        logger.info(f"Batch {batch_num} appended {len(rows)} dictionary rows")

    logger.info(f"All batches complete. Total dictionary rows = {len(df_master)}")

    # ----------------------------------------------------------
    # Create Data Dictionary table (your function)
    # ----------------------------------------------------------
    # dictionary_table_full_id = create_data_dictionary_table(dictionary_table_id)

    dictionary_table_full_id = create_data_dictionary_table(dictionary_table_id)

    # dictionary_table_full_id example:
    # ust-genai-pa-poc-gcp.DATAMAP_COPILOT.<table_id>

    logger.info(f"Dictionary table ensured: {dictionary_table_full_id}")

    # ----------------------------------------------------------
    # Upload dataframe to BigQuery
    # ----------------------------------------------------------
    client = bigquery.Client(project=config.PROJECT_ID)

    # In your code upload_to_bigquery_sync does WRITE_TRUNCATE
    # This will replace the whole dictionary table with df_master
    rows_uploaded = upload_to_bigquery_sync(
        client=client,
        df=df_master,
        table_name=dictionary_table_id,
        dataset_id=dataset_id
    )

    logger.info(f"Uploaded data dictionary to BQ. Rows uploaded = {rows_uploaded}")

    return {
        "dictionary_table_id": dictionary_table_id,
        "dictionary_table_full_id": dictionary_table_full_id,
        "rows_generated": len(df_master),
        "rows_uploaded": rows_uploaded
    }

# ---- CONFIG ----
GCP_PROJECT = "ust-genai-pa-poc-gcp"
GCS_BUCKET = "bsa_datamap_rag"
DEFAULT_FOLDER = "uploads"  

# -------------------------------------------------------------------
# Client init (cached)
# -------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_storage_client() -> storage.Client:
    """
    Creates a single cached Storage client.
    """
    return storage.Client(project=GCP_PROJECT)


# -------------------------------------------------------------------
# Object naming
# -------------------------------------------------------------------
def make_object_path(filename: str, folder: str = DEFAULT_FOLDER) -> str:
    """
    Simple object structure:
      uploads/<filename>

    Removes spaces and path fragments.
    """
    clean = os.path.basename(filename).replace(" ", "_")
    folder = folder.strip("/")

    return f"{folder}/{clean}" if folder else clean


# -------------------------------------------------------------------
# Upload: FastAPI UploadFile
# -------------------------------------------------------------------
def upload_uploadfile_to_gcs(
    file: UploadFile,
    *,
    bucket_name: str = GCS_BUCKET,
    folder: str = DEFAULT_FOLDER,
    overwrite: bool = True,
) -> str:
    """
    Uploads FastAPI UploadFile into:
      gs://bucket/folder/filename

    Returns:
      gs://... path only
    """
    client = get_storage_client()
    bucket = client.bucket(bucket_name)

    object_path = make_object_path(file.filename or "uploaded_file", folder=folder)
    blob = bucket.blob(object_path)

    try:
        if not overwrite and blob.exists(client):
            raise HTTPException(
                status_code=409,
                detail=f"File already exists in GCS: gs://{bucket_name}/{object_path}",
            )

        file.file.seek(0)
        blob.upload_from_file(
            file.file,
            content_type=file.content_type or "application/octet-stream",
            rewind=True,
        )

        return f"gs://{bucket_name}/{object_path}"

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GCS upload failed: {e}")

# -------------------------------------------------------------------
# Upload: Local file path
# -------------------------------------------------------------------
def upload_local_file_to_gcs(
    local_path: str,
    *,
    bucket_name: str = GCS_BUCKET,
    folder: str = DEFAULT_FOLDER,
    overwrite: bool = True,
) -> str:
    """
    Upload local file to GCS in:
      gs://bucket/folder/<filename>

    Returns:
      gs://... path only
    """
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Local file not found: {local_path}")

    client = get_storage_client()
    bucket = client.bucket(bucket_name)

    object_path = make_object_path(os.path.basename(local_path), folder=folder)
    blob = bucket.blob(object_path)

    if not overwrite and blob.exists(client):
        raise FileExistsError(f"Already exists: gs://{bucket_name}/{object_path}")

    blob.upload_from_filename(local_path)
    return f"gs://{bucket_name}/{object_path}"

import json
import os
import docx
try:  # camelot is only needed for the offline PDF->JSONL datastore loader (not standalone)
    import camelot
except ImportError:  # noqa: F401
    camelot = None
import pandas as pd

def generate_vertex_jsonl(rows, source_filename, output_file):
    """
    Standardizes rows into the Vertex AI Search JSONL format.
    Each row becomes a unique 'document' for perfect keyword indexing.
    """
    if not rows:
        print(f"No data to write for {source_filename}")
        return

    with open(output_file, 'w', encoding='utf-8') as f:
        for i, row in enumerate(rows):
            # 1. Create a unique ID for this specific row
            doc_id = f"{source_filename.replace('.', '_')}_{i}"
            
            # 2. Construct searchable 'content' 
            # We explicitly format keys and values so 'ParentId' is easily indexed
            content_parts = []
            for k, v in row.items():
                if v and str(v).lower() != 'none':
                    # Clean the value for the search index
                    clean_val = str(v).replace('\n', ' ').strip()
                    content_parts.append(f"{k}: {clean_val}")
            
            searchable_text = " | ".join(content_parts)
            
            # 3. Construct the final Vertex JSON object
            vertex_doc = {
                "id": doc_id,
                "content": searchable_text, # Used for Keyword/Semantic search
                "structData": row           # Used for metadata and display
            }
            
            f.write(json.dumps(vertex_doc) + "\n")

def extract_from_docx(file_path):
    """Extracts all tables from a Word document."""
    doc = docx.Document(file_path)
    all_rows = []
    for table in doc.tables:
        if len(table.rows) < 2: continue # Skip empty tables
        
        # Get headers from the first row
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        
        for row in table.rows[1:]:
            values = [cell.text.strip() for cell in row.cells]
            row_dict = dict(zip(headers, values))
            all_rows.append(row_dict)
    return all_rows

def extract_from_pdf(file_path):
    """Extracts tables from PDF using Camelot with robust cell cleaning."""
    print(f"Starting PDF extraction: {file_path}")
    
    # 1. Read PDF
    tables = camelot.read_pdf(file_path, pages='all', flavor='lattice')
    if len(tables) == 0:
        print("No bordered tables found. Trying Stream mode...")
        tables = camelot.read_pdf(file_path, pages='all', flavor='stream')

    if len(tables) == 0:
        print("Warning: No tables detected in PDF.")
        return []

    all_rows = []
    global_headers = []

    for i, table in enumerate(tables):
        df = table.df
        
        # 2. Clean EVERY cell in the dataframe immediately to avoid newline issues
        # Using applymap (older pandas) or map (newer pandas) to avoid column-name errors
        if hasattr(df, 'map'):
            df = df.map(lambda x: str(x).replace('\n', ' ').strip() if x else "")
        else:
            df = df.applymap(lambda x: str(x).replace('\n', ' ').strip() if x else "")

        # 3. Handle Headers
        if i == 0:
            # First table: extract headers from row 0
            global_headers = df.iloc[0].tolist()
            df = df[1:]
            # Ensure headers themselves are clean strings
            global_headers = [str(h) if h else f"column_{idx}" for idx, h in enumerate(global_headers)]
        
        # Assign headers to the current table
        # If the number of columns doesn't match, we pad/truncate to avoid errors
        if len(df.columns) == len(global_headers):
            df.columns = global_headers
        else:
            print(f"Warning: Table {i} column count mismatch. Using default headers.")
            df.columns = [f"col_{idx}" for idx in range(len(df.columns))]

        # 4. Final cleaning
        df = df.replace('None', None).replace('', None).dropna(how='all')
        
        all_rows.extend(df.to_dict(orient='records'))
        
    return all_rows

def run_pipeline(input_file, output_jsonl="data/vertex_data_dictionary.jsonl"):
    """Main function to handle file types and generate the JSONL output."""
    if not os.path.exists(input_file):
        print(f"Error: File not found: {input_file}")
        return

    ext = os.path.splitext(input_file)[1].lower()
    extracted_data = []

    if ext == ".docx":
        extracted_data = extract_from_docx(input_file)
    elif ext == ".pdf":
        extracted_data = extract_from_pdf(input_file)
    else:
        print(f"Unsupported file extension: {ext}")
        return

    print(f"Successfully extracted {len(extracted_data)} rows.")
    
    generate_vertex_jsonl(extracted_data, os.path.basename(input_file), output_jsonl)
    print(f"Done! Created '{output_jsonl}'. Ready for Vertex AI Search ingestion.")
