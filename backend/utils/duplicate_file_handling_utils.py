'''
Contains utils for primary key detection and grouping files 
'''
import logging
from fastapi import  UploadFile
import os
import re
import json
import uuid
from typing import List, Optional, Dict
from google import genai
from collections import defaultdict,Counter
import pandas as pd
import asyncio
from config.settings import config
from datetime import datetime
from utils.file_convertor import FixedWidthDelimitedConverter
from api.models import FileUploadResponse, BatchUploadResponse
from utils.file_convertor import convert_to_csv_if_needed
from utils.bg_query_utils import (
    get_bigquery_client,
    validate_file,
    generate_table_name,
    read_file_to_dataframe,
    #upload_to_bigquery,
    get_access_info,
    query_dataset_tables,
    upload_to_bigquery_sync,
    query_and_profile_sync,
    expand_zip_files
)
from utils.profiling_artifact_store import (
    profiling_report_proxy_path,
    update_profiling_session_context,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


DATE_REGEX = re.compile(
    r"""
    (\d{14})|                # YYYYMMDDHHMMSS
    (\d{8})|                 # YYYYMMDD / DDMMYYYY
    (\d{6})|                 # YYMMDD / DDMMYY
    (\d{4}-\d{2}-\d{2})|     # YYYY-MM-DD
    (\d{2}-\d{2}-\d{4})      # DD-MM-YYYY
    """,
    re.VERBOSE,
)


def normalize(name: str) -> str:
    name = name.lower()
    name = re.sub(r'[-\s]+', '_', name)
    return name

def tokenize(name: str):
    return re.findall(r'[a-zA-Z]+|\d+', name)

def is_variable_token(token: str) -> bool:
    if token.isdigit():
        return True
    if DATE_REGEX.fullmatch(token):
        return True
    if re.match(r'v\d+', token):
        return True
    return False

def extract_datetime_from_name(filename: str) -> datetime:
    """
    Extract most precise datetime from filename.
    Supports:
    - YYYYMMDDHHMMSS
    - YYYYMMDD[_-.]HHMMSS
    - YYYY-MM-DD
    - DD-MM-YYYY
    - YYYYMMDD
    - YYMMDD
    """

    name = filename  # DO NOT use splitext here

    # YYYMMDD[_-.]HHMMSS
    match = re.search(r"(\d{8})[_.-](\d{6})", name)
    if match:
        try:
            return datetime.strptime(
                match.group(1) + match.group(2),
                "%Y%m%d%H%M%S"
            )
        except:
            pass

    # Continuous 14-digit timestamp
    match = re.search(r"\d{14}", name)
    if match:
        try:
            return datetime.strptime(match.group(), "%Y%m%d%H%M%S")
        except:
            pass

    # YYYY-MM-DD
    match = re.search(r"\d{4}-\d{2}-\d{2}", name)
    if match:
        try:
            return datetime.strptime(match.group(), "%Y-%m-%d")
        except:
            pass

    #  DD-MM-YYYY
    match = re.search(r"\d{2}-\d{2}-\d{4}", name)
    if match:
        try:
            return datetime.strptime(match.group(), "%d-%m-%Y")
        except:
            pass

    # 8-digit date
    match = re.search(r"\d{8}", name)
    if match:
        try:
            return datetime.strptime(match.group(), "%Y%m%d")
        except:
            try:
                return datetime.strptime(match.group(), "%d%m%Y")
            except:
                pass

    # 6-digit date
    match = re.search(r"\d{6}", name)
    if match:
        try:
            return datetime.strptime(match.group(), "%y%m%d")
        except:
            try:
                return datetime.strptime(match.group(), "%d%m%y")
            except:
                pass

    return datetime.min

def group_files_by_name(files: List[UploadFile]) -> Dict[str, List[UploadFile]]:
    token_frequency = Counter()
    file_tokens = {}

    # First pass: tokenize
    for file in files:
        name_parts = file.filename.split(".")
        if len(name_parts) > 1 and not name_parts[-1].isdigit():
            base_name = ".".join(name_parts[:-1])
        else:
            base_name = file.filename
        
        norm = normalize(base_name)
        tokens = tokenize(norm)

        file_tokens[file] = tokens

        for t in tokens:
            if not is_variable_token(t):
                token_frequency[t] += 1

    groups = defaultdict(list)

    # Second pass: build group key
    for file, tokens in file_tokens.items():
        # Better extension handling
        name_parts = file.filename.split(".")
        if len(name_parts) > 1 and not name_parts[-1].isdigit():
            ext = "." + name_parts[-1].lower()
            base_name = ".".join(name_parts[:-1])
        else:
            ext = ""
            base_name = file.filename

        stable_tokens = [
            t for t in tokens
            if not is_variable_token(t)
        ]

        if not stable_tokens:
            stable_tokens = ["unknown"]

        group_key = "_".join(stable_tokens) + ext.lower()

        groups[group_key].append(file)

    # ----------------------------------------
    # SORT FILES INSIDE EACH GROUP BY DATE ASC
    # ----------------------------------------
    for key in groups:
        groups[key].sort(
            key=lambda f: extract_datetime_from_name(f.filename)
        )

    return dict(groups)

async def group_files_by_schema(
    files: List[UploadFile],
    metadata_path: Optional[str]
) -> Dict[str, List[UploadFile]]:
    """
    Strict schema-based grouping.
    Groups files ONLY if column headers match exactly.
    No fuzzy logic.
    No merging unless identical schema.
    """

    schema_groups: Dict[tuple, List[UploadFile]] = {}

    for file in files:

        validate_file(file)

        processed_file, _ = await convert_to_csv_if_needed(
            file,
            metadata_path
        )

        df = await asyncio.to_thread(
            read_file_to_dataframe,
            processed_file,
            metadata_path
        )

        # Apply prefixing if needed
        if metadata_path:
            converter = FixedWidthDelimitedConverter(
                metadata_file=metadata_path,
                input_file="",
                output_file=""
            )
            meta = await asyncio.to_thread(converter._load_metadata)
            df = converter._apply_header_trailer_prefixes(df, meta)

        schema_key = tuple(df.columns)  # STRICT MATCH

        if schema_key not in schema_groups:
            schema_groups[schema_key] = []

        schema_groups[schema_key].append(file)

    # Convert tuple keys to readable group names
    final_groups = {}
    for idx, (schema, group_files) in enumerate(schema_groups.items(), start=1):
        group_name = f"schema_group_{idx}"
        final_groups[group_name] = group_files

    return final_groups

def deduplicate_dataframe(df: pd.DataFrame, primary_keys: List[str]) -> pd.DataFrame:
    """Keep latest row for each primary key combination"""
    if not primary_keys or df.empty:
        return df
    if 'upload_timestamp' not in df.columns:
        df['upload_timestamp'] = pd.Timestamp.now()
    df_sorted = df.sort_values('upload_timestamp')
    return df_sorted.drop_duplicates(subset=primary_keys, keep='last')

async def find_primary_key_from_metadata(metadata_path: Optional[str]) -> List[str]:
    """
    Detect PRIMARY KEY columns from structured metadata (CSV or Excel)
    using Gemini (Vertex AI).
    """

    if not metadata_path or not os.path.exists(metadata_path):
        logger.info("PK DETECTION: No metadata path provided or file missing.")
        return []

    try:
        logger.info(f"PK DETECTION: Loading metadata file {metadata_path}")

        # -------------------------
        # Load structured metadata
        # -------------------------
        ext = os.path.splitext(metadata_path)[1].lower()

        if ext == ".csv":
            meta_df = pd.read_csv(metadata_path)
        elif ext in [".xlsx", ".xls"]:
            meta_df = pd.read_excel(metadata_path)
        else:
            logger.warning("PK DETECTION: Unsupported metadata format.")
            return []

        if meta_df.empty:
            logger.warning("PK DETECTION: Metadata file is empty.")
            return []

        meta_df = meta_df.head(200)  # protect tokens
        metadata_json = meta_df.to_dict(orient="records")

        prompt = f"""
You are a senior Data Architect.

Below is a STRUCTURED METADATA TABLE in JSON format.

Your task:
Identify PRIMARY KEY column(s) strictly based on explicit metadata indicators.

Rules:
- Look for columns like: "Primary Key", "PK", "Is_Key", "Key", "Key Type", etc.
- PK indicator values may be: Y, Yes, TRUE, 1, PK, Primary.
- DO NOT infer based on column names like 'id'.
- Only return columns explicitly marked as primary key.
- If multiple rows are marked → return all (composite key).
- If none explicitly marked → return empty list.

Return STRICT JSON only:

{{
  "primary_keys": ["column_name_1", "column_name_2"]
}}

Metadata:
-----------------------------------
{json.dumps(metadata_json, indent=2, default=str)}
-----------------------------------
"""

        # -------------------------
        # Call Gemini (Vertex AI)
        # -------------------------
        logger.info("PK DETECTION: Calling Gemini model")

        genai_client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION 
        )

        response = await asyncio.to_thread(
            genai_client.models.generate_content,
            model="gemini-2.5-pro",
            contents=prompt,
            config={
                "temperature": 0.0,
                "max_output_tokens": 1024,
                "response_mime_type": "application/json"
            }
        )

        if not response or not response.text:
            logger.warning("PK DETECTION: Empty Gemini response.")
            return []

        parsed = json.loads(response.text)

        primary_keys = parsed.get("primary_keys", [])

        if not isinstance(primary_keys, list):
            logger.warning("PK DETECTION: Invalid JSON structure.")
            return []

        primary_keys = [col.strip() for col in primary_keys if col.strip()]

        logger.info(f"PK DETECTION: Detected primary keys → {primary_keys}")

        return primary_keys

    except Exception:
        logger.exception("PK DETECTION: Gemini primary key detection failed.")
        return []

async def find_primary_keys_from_bigquery(
    client,
    project_id: str,
    dataset_id: str,
    table_name: str,
    max_composite_size: int = 2
) -> List[str]:
    """
    Detect primary keys from an uploaded BigQuery table using:
    - Uniqueness check
    - Null check
    - Optional composite key detection
    """

    try:
        full_table_id = f"{project_id}.{dataset_id}.{table_name}"

        logger.info(f"PK DETECTION (BQ): Starting analysis for {full_table_id}")

        total_rows_query = f"SELECT COUNT(*) as total_rows FROM {full_table_id}"
        total_rows_result = list(client.query(total_rows_query).result())
        total_rows = total_rows_result[0]["total_rows"]

        if total_rows == 0:
            logger.warning("PK DETECTION (BQ): Table empty.")
            return []

        logger.info(f"PK DETECTION (BQ): Total rows = {total_rows}")

        table = client.get_table(f"{project_id}.{dataset_id}.{table_name}")
        columns = [field.name for field in table.schema]
        field_types = {field.name: field.field_type for field in table.schema}
        field_modes = {field.name: field.mode for field in table.schema}

        logger.info(f"PK DETECTION (BQ): Columns found: {columns}")

        candidate_columns = []

        for col in columns:
            # Skip complex or repeated types for PK detection
            if field_types.get(col) == "RECORD" or field_modes.get(col) == "REPEATED":
                logger.info(
                    "PK DETECTION (BQ): Skipping non-scalar column %s (type=%s, mode=%s)",
                    col,
                    field_types.get(col),
                    field_modes.get(col),
                )
                continue

            query = f"""
            SELECT
                COUNT(*) as total_rows,
                COUNT(DISTINCT `{col}`) as distinct_count,
                COUNTIF(`{col}` IS NULL) as null_count
            FROM {full_table_id}
            """

            result = list(client.query(query).result())[0]

            if (
                result["distinct_count"] == total_rows and
                result["null_count"] == 0
            ):
                logger.info(f"PK DETECTION (BQ): Single-column PK found → {col}")
                return [col]

            if result["null_count"] == 0:
                candidate_columns.append(col)

        if max_composite_size > 1 and len(candidate_columns) > 1:

            from itertools import combinations

            logger.info("PK DETECTION (BQ): Trying composite keys")

            for combo in combinations(candidate_columns, max_composite_size):
                combo_cols = ", ".join([f"`{c}`" for c in combo])

                query = f"""
                SELECT COUNT(*) as total_rows,
                       (
                         SELECT COUNT(*) FROM (
                           SELECT DISTINCT {combo_cols}
                           FROM {full_table_id}
                         )
                       ) as distinct_count
                FROM {full_table_id}
                """

                result = list(client.query(query).result())[0]

                if result["distinct_count"] == total_rows:
                    logger.info(f"PK DETECTION (BQ): Composite PK found → {combo}")
                    return list(combo)

        logger.info("PK DETECTION (BQ): No primary key detected.")
        return []

    except Exception:
        logger.exception("PK DETECTION (BQ): Error detecting primary key.")
        return []
   
async def handle_duplicate_file_groups(
    expanded_files,
    metadata_path,
    client,
    dataset_id,
    session_id,
    process_file,
):

    file_groups = group_files_by_name(expanded_files)
    logger.info(f"DEDUP: Grouped {len(expanded_files)} files into {len(file_groups)} groups")
    
    # -----------------------------------------
    # SCHEMA VALIDATION PASS
    # If any group contains schema mismatch,
    # fallback to strict schema grouping
    # -----------------------------------------
    
    needs_schema_regroup = False
    
    for base_name, files in file_groups.items():
        if len(files) <= 1:
            continue
        
        base_schema = None
    
        for file in files:
            processed_file, _ = await convert_to_csv_if_needed(
                file,
                metadata_path
            )
    
            df = await asyncio.to_thread(
                read_file_to_dataframe,
                processed_file,
                metadata_path
            )
    
            if metadata_path:
                converter = FixedWidthDelimitedConverter(
                    metadata_file=metadata_path,
                    input_file="",
                    output_file=""
                )
                meta = await asyncio.to_thread(converter._load_metadata)
                df = converter._apply_header_trailer_prefixes(df, meta)
    
            current_schema = tuple(df.columns)
    
            if base_schema is None:
                base_schema = current_schema
            elif current_schema != base_schema:
                logger.warning(
                    f"Schema mismatch detected inside group '{base_name}'. "
                    "Switching to strict schema-based grouping."
                )
                needs_schema_regroup = True
                break
            
        if needs_schema_regroup:
            break
        
    if needs_schema_regroup:
        file_groups = await group_files_by_schema(expanded_files, metadata_path)
        logger.info(
            f"Schema regrouping activated. "
            f"{len(file_groups)} schema-based groups created."
        )
    results = []

    for base_name, files in file_groups.items():
        logger.info(f"DEDUP: Processing group '{base_name}' with {len(files)} files")

        # ----------------------------------
        # SINGLE FILE → Use Existing Logic
        # ----------------------------------
        if len(files) == 1:
            logger.info(f"DEDUP: Single file '{files[0].filename}', processing normally")
            result = await process_file(files[0])
            results.append(result)
            continue

        # ----------------------------------
        # DUPLICATE FILES → FULL SAFE FLOW
        # ----------------------------------
        try:
            logger.info(f"DEDUP: Starting duplicate file processing for base_name='{base_name}'")

            combined_df = pd.DataFrame()
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            table_name = generate_table_name(f"{base_name}_{timestamp}")
            base_columns = None

            logger.info(f"DEDUP: Generated table_name='{table_name}'")

            for i, file in enumerate(files):

                logging.info("ROUTER: Starting processing for file=%s", file.filename)

                # -------------------------------
                # 1. VALIDATION
                # -------------------------------
                validate_file(file)

                # -------------------------------
                # 2. Metadata Logging
                # -------------------------------
                if metadata_path:
                    logging.info("ROUTER: Metadata dictionary detected at path=%s", metadata_path)
                else:
                    logging.info("ROUTER: No metadata dictionary provided")

                # -------------------------------
                # 3. Convert if needed
                # -------------------------------
                processed_file, was_converted = await convert_to_csv_if_needed(
                    file,
                    metadata_path
                )

                logging.info(
                    "ROUTER: File conversion step completed | was_converted=%s | final_filename=%s",
                    was_converted,
                    processed_file.filename
                )

                # -------------------------------
                # 4. Read into DataFrame
                # -------------------------------
                df = await asyncio.to_thread(
                    read_file_to_dataframe,
                    processed_file,
                    metadata_path
                )

                logging.info(
                    "ROUTER: DataFrame loaded | rows=%d | cols=%d",
                    df.shape[0],
                    df.shape[1]
                )

                logging.info(
                    "ROUTER: Columns BEFORE prefixing = %s",
                    list(df.columns)
                )

                # -------------------------------
                # 5. APPLY HEADER/TRAILER PREFIXING
                # -------------------------------
                if metadata_path:
                    logging.info("ROUTER: Attempting header/trailer prefix normalization")

                    converter = FixedWidthDelimitedConverter(
                        metadata_file=metadata_path,
                        input_file="",
                        output_file=""
                    )

                    meta = await asyncio.to_thread(converter._load_metadata)

                    before_cols = list(df.columns)
                    df = converter._apply_header_trailer_prefixes(df, meta)
                    after_cols = list(df.columns)

                    if before_cols != after_cols:
                        logging.info(
                            "ROUTER: Prefixing APPLIED successfully\nBEFORE=%s\nAFTER=%s",
                            before_cols,
                            after_cols
                        )
                    else:
                        logging.info("ROUTER: Prefixing evaluated — no changes required")

                else:
                    logging.info("ROUTER: Prefixing skipped (no metadata dictionary)")

                # -------------------------------
                # 6. SCHEMA CONSISTENCY CHECK
                # -------------------------------
                if i == 0:
                    base_columns = set(df.columns)
                else:
                    if set(df.columns) != base_columns:
                        raise ValueError(
                            f"Schema mismatch in duplicate group '{base_name}'. "
                            f"Expected columns: {base_columns}, "
                            f"Got: {set(df.columns)}"
                        )

                df["upload_timestamp"] = pd.Timestamp.utcnow()
                combined_df = pd.concat([combined_df, df], ignore_index=True)

            # ----------------------------------
            # Upload first file for PK detection
            # ----------------------------------
            first_df = combined_df.head(len(df))  # first chunk equivalent

            first_table_name = generate_table_name(base_name)

            await asyncio.to_thread(
                upload_to_bigquery_sync,
                client,
                first_df.drop(columns=["upload_timestamp"], errors="ignore"),
                first_table_name,
                dataset_id
            )

            # ----------------------------------
            # Primary Key Detection
            # ----------------------------------
            logger.info("DEDUP: Starting primary key detection")

            primary_keys = await find_primary_key_from_metadata(metadata_path)

            if not primary_keys:
                logger.info("DEDUP: No primary keys from metadata. Trying BigQuery analysis.")
                primary_keys = await find_primary_keys_from_bigquery(
                    client,
                    config.BQ_PROJECT_ID,
                    config.DATASET_ID,
                    first_table_name
                )

            logger.info(f"DEDUP: Primary keys detected: {primary_keys}")

            # ----------------------------------
            # Deduplication
            # ----------------------------------
            if primary_keys:
                original_rows = combined_df.shape[0]
                combined_df = deduplicate_dataframe(combined_df, primary_keys)
                final_rows = combined_df.shape[0]

                logger.info(
                    f"DEDUP: Deduplication complete: {original_rows} -> {final_rows}"
                )
            else:
                logger.warning("DEDUP: No primary keys found. Skipping deduplication.")

            combined_df = combined_df.drop(columns=["upload_timestamp"], errors="ignore")

            # ----------------------------------
            # Upload Combined Data
            # ----------------------------------
            rows_uploaded = await asyncio.to_thread(
                upload_to_bigquery_sync,
                client,
                combined_df,
                table_name,
                dataset_id
            )

            logger.info(f"DEDUP: Uploaded {rows_uploaded} rows to table '{table_name}'")

            # ----------------------------------
            # Profiling
            # ----------------------------------
            access_info = get_access_info(
                config.PROJECT_ID,
                dataset_id,
                table_name
            )

            profiling_results = await asyncio.to_thread(
                query_and_profile_sync,
                client,
                access_info["sql_query"],
                session_id=session_id,
            )

            logger.info(
                "ROUTER: Profiling completed | profiling_uuid=%s",
                profiling_results.get("profiling_uuid")
            )

            # ----------------------------------
            # Response Construction
            # ----------------------------------
            file_id = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()

            from utils.session_identity import session_user_email

            response_object = FileUploadResponse(
                sessionID=session_id,
                user=session_user_email(session_id),
                createdDate=now,
                lastUpdateDate=now,
                file_id=file_id,
                filename=f"{base_name}_combined",
                table_name=table_name,
                dataset_id=dataset_id,
                project_id=config.PROJECT_ID,
                rows_uploaded=rows_uploaded,
                upload_timestamp=now,
                access_info=access_info,
                initial_profiling_report=profiling_results.get("profiling_uuid", ""),
                profiling_report_url=profiling_report_proxy_path(
                    session_id,
                    profiling_results.get("profiling_uuid", ""),
                ),
                data_quality_score=profiling_results.get("data_quality_score"),
            )

            update_profiling_session_context(
                session_id,
                {"initial_profiling_report": profiling_results.get("profiling_uuid", "")},
            )

            results.append((response_object, None))

        except Exception as e:
            logger.error(
                f"DEDUP: Error processing duplicate files for '{base_name}': {str(e)}"
            )
            results.append((None, {"filename": base_name, "error": str(e)}))

    return results
