import asyncio
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote
import base64

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from google.genai.errors import ServerError
from pydantic import BaseModel

from api.dependencies.auth import CurrentUser, resolve_current_user
from api.models import FileUploadResponse
from config.settings import config
from db.engine import app_db_session, is_app_db_enabled
from db.repositories import AppSessionRepository
from utils.bg_query_utils import (
    expand_zip_files,
    generate_table_name,
    get_access_info,
    get_bigquery_client,
    query_and_profile_sync,
    read_file_to_dataframe,
    upload_to_bigquery_sync,
    validate_file,
)
from utils.duplicate_file_handling_utils import handle_duplicate_file_groups
from utils.dd_session_utils import (
    materialize_metadata_path,
    persist_dd_candidates,
    persist_resolved_metadata_path,
    save_selected_dd_choice,
)
from utils.file_convertor import FixedWidthDelimitedConverter, convert_to_csv_if_needed
from utils.gcs_artifact_utils import make_json_compatible
from utils.profiling_artifact_store import (
    load_profiling_report_html,
    profiling_context_uri,
    profiling_report_proxy_path,
    save_document_bytes,
    save_raw_file,
    update_profiling_session_context,
    session_base,
)
from utils.gcs_artifact_utils import artifact_storage_client, artifact_bucket_name, list_blobs
from utils.table_extractor_utils_large import resolve_metadata_path


class SchemaRequest(BaseModel):
    file_paths: List[str]


MAX_DD_ROWS = 100000
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


async def _save_optional_document(session_id: str, upload_file: UploadFile, document_kind: str) -> str:
    content = await upload_file.read()
    return save_document_bytes(
        session_id=session_id,
        document_kind=document_kind,
        filename=upload_file.filename,
        content=content,
    )

def apply_column_mapping(df, file_path, mappings, target_schema):
    """Apply column mapping to normalize dataframe schema"""
    logger.info(f"[MAPPING] Applying column mapping for file: {os.path.basename(file_path)}")
    
    # Create a mapping dictionary for this specific file
    file_mappings = {
        m["sourceColumn"]: m["targetColumn"]
        for m in mappings
        if m.get("sourceFile") == file_path
    }
    
    logger.info(f"[MAPPING] File-specific mappings: {file_mappings}")
    
    # Create target dataframe with same index as source
    target_columns = [col['name'] for col in target_schema]
    logger.info(f"[MAPPING] Target columns: {target_columns}")
    
    # Initialize result dataframe with correct index
    result_df = pd.DataFrame(index=df.index)
    
    # Map existing columns to target schema
    for source_col in df.columns:
        if source_col in file_mappings:
            target_col = file_mappings[source_col]
            if target_col in target_columns:
                result_df[target_col] = df[source_col]
                logger.info(f"[MAPPING] Mapped {source_col} -> {target_col}")
        elif source_col in target_columns:
            # Direct match (no mapping needed)
            result_df[source_col] = df[source_col]
            logger.info(f"[MAPPING] Direct match: {source_col}")
    
    # Fill unmapped columns with NaN
    for target_col in target_columns:
        if target_col not in result_df.columns:
            result_df[target_col] = pd.NA
            logger.info(f"[MAPPING] Filled unmapped column with NULL: {target_col}")
    
    # Ensure column order matches target schema
    result_df = result_df[target_columns]
    if "extraction_order" in df.columns:
        result_df["extraction_order"] = df["extraction_order"]
        
    logger.info(f"[MAPPING] Result dataframe: {len(result_df)} rows, {len(result_df.columns)} columns")
    return result_df



# async def process_file(
#     file: UploadFile,
#     metadata_path: str,
#     client,
#     dataset_id: str,
#     session_id: str
# ) -> Tuple[Any, Any]:
#     try:
#         logging.info("ROUTER: Starting processing for file=%s", file.filename)

#         validate_file(file)
#         table_name = generate_table_name(file.filename)

#         logging.info("ROUTER: Validation complete. Initial table_name=%s", table_name)

#         if metadata_path == "":
#             metadata_path = None
#         if metadata_path:
#             logging.info("ROUTER: Metadata dictionary detected at path=%s", metadata_path)
#         else:
#             logging.info("ROUTER: No metadata dictionary provided")

#         processed_file, was_converted = await convert_to_csv_if_needed(file, metadata_path)
#         file = processed_file
#         table_name = generate_table_name(file.filename)

#         logging.info("ROUTER: File conversion step completed | was_converted=%s | final_filename=%s", was_converted, file.filename)

#         logging.info("ROUTER: Reading file into DataFrame (pre-prefixing)")
#         df = await asyncio.to_thread(read_file_to_dataframe, file, metadata_path)

#         logging.info("ROUTER: DataFrame loaded | rows=%d | cols=%d", df.shape[0], df.shape[1])
#         logging.info("ROUTER: Columns BEFORE prefixing = %s", list(df.columns))

#         if metadata_path:
#             logging.info("ROUTER: Attempting header/trailer prefix normalization")
#             converter = FixedWidthDelimitedConverter(
#                 metadata_file=metadata_path,
#                 input_file="",
#                 output_file=""
#             )
#             meta = await asyncio.to_thread(converter._load_metadata)
#             logging.info("ROUTER: Metadata loaded for prefixing | meta.columns=%s", list(meta.columns))
#             before_cols = list(df.columns)
#             df = converter._apply_header_trailer_prefixes(df, meta)
#             after_cols = list(df.columns)
#             if before_cols != after_cols:
#                 logging.info("ROUTER: Prefixing APPLIED successfully\nBEFORE=%s\nAFTER=%s", before_cols, after_cols)
#             else:
#                 logging.info("ROUTER: Prefixing evaluated — no changes required")
#         else:
#             logging.info("ROUTER: Prefixing skipped (no metadata dictionary)")

#         logging.info("ROUTER: Uploading DataFrame to BigQuery | table=%s", table_name)
#         rows_uploaded = await asyncio.to_thread(
#             upload_to_bigquery_sync,
#             client,
#             df,
#             table_name,
#             dataset_id
#         )
#         logging.info("ROUTER: Upload successful | rows_uploaded=%d | table=%s", rows_uploaded, table_name)

#         file_id = str(uuid.uuid4())
#         now = datetime.utcnow().isoformat()
#         access_info = get_access_info(config.PROJECT_ID, dataset_id, table_name)

#         profiling_task = asyncio.create_task(
#             asyncio.to_thread(
#                 query_and_profile_sync,
#                 client,
#                 access_info["sql_query"],
#                 session_id=session_id,
#             )
#         )
#         profiling_results = await profiling_task
#         logging.info("ROUTER: Profiling completed | profiling_uuid=%s", profiling_results.get("profiling_uuid"))

#         response_object = FileUploadResponse(
#             sessionID=session_id,
#             user="john@example.com",
#             createdDate=now,
#             lastUpdateDate=now,
#             file_id=file_id,
#             filename=file.filename,
#             table_name=table_name,
#             dataset_id=dataset_id,
#             project_id=config.PROJECT_ID,
#             rows_uploaded=rows_uploaded,
#             upload_timestamp=now,
#             access_info=access_info,
#             initial_profiling_report=profiling_results.get("profiling_uuid", ""),
#             profiling_report_url=profiling_report_proxy_path(
#                 session_id,
#                 profiling_results.get("profiling_uuid", ""),
#             ),
#             data_quality_score=profiling_results.get("data_quality_score"),
#         )

#         update_profiling_session_context(
#             session_id,
#             {"initial_profiling_report": profiling_results.get("profiling_uuid", "")},
#         )

#         logging.info("ROUTER: File processing COMPLETE for file=%s", file.filename)
#         return response_object, None

#     except HTTPException as e:
#         logging.error("ROUTER: HTTPException while processing file=%s | error=%s", file.filename, e.detail)
#         return None, {"filename": file.filename, "error": e.detail}
#     except Exception as e:
#         logging.exception("ROUTER: Unhandled exception while processing file=%s", file.filename)
#         return None, {"filename": file.filename, "error": str(e)}

def _detect_duplicates(df: pd.DataFrame, sample_n: int = 5) -> dict:
    dup_mask = df.duplicated(keep=False)
    dup_df = df[dup_mask]
    return {
        "duplicate_count": int(dup_mask.sum()),
        "sample_duplicates": dup_df.drop_duplicates().head(sample_n).to_dict(orient="records"),
        "all_duplicates": dup_df.to_dict(orient="records"),
    }


async def process_file(
    file: UploadFile,
    metadata_path: str,
    client,
    dataset_id: str,
    session_id: str
) -> Tuple[Any, Any]:
    try:
        logging.info("ROUTER: Starting processing for file=%s", file.filename)

        validate_file(file)
        base_table_name = generate_table_name(file.filename)

        if metadata_path == "":
            metadata_path = None

        processed_file, was_converted = await convert_to_csv_if_needed(file, metadata_path)
        file = processed_file
        base_table_name = generate_table_name(file.filename)

        logging.info("ROUTER: File conversion step completed | was_converted=%s | final_filename=%s", was_converted, file.filename)

        data = await asyncio.to_thread(read_file_to_dataframe, file, metadata_path)

        upload_results = []
        total_rows_uploaded = 0

        # inner helper — handles duplicate detection + prefix + upload + profile for ONE sheet
        async def _upload_sheet(df: pd.DataFrame, table_name: str) -> dict:
            nonlocal total_rows_uploaded

            dup_info = _detect_duplicates(df)
            dup_key = f"duplicates_{table_name}"
            update_profiling_session_context(
                session_id,
                {dup_key: {"table": table_name, "duplicate_count": dup_info["duplicate_count"], "sample_duplicates": dup_info["sample_duplicates"]}},
            )
            if dup_info["all_duplicates"]:
                update_profiling_session_context(session_id, {f"{dup_key}_all": dup_info["all_duplicates"]})
            logging.info("ROUTER: Duplicates | table=%s | count=%d", table_name, dup_info["duplicate_count"])

            if metadata_path:
                converter = FixedWidthDelimitedConverter(
                    metadata_file=metadata_path,
                    input_file="",
                    output_file=""
                )
                meta = await asyncio.to_thread(converter._load_metadata)
                before = list(df.columns)
                df = converter._apply_header_trailer_prefixes(df, meta)
                after = list(df.columns)
                if before != after:
                    logging.info("ROUTER: Prefixing applied | BEFORE=%s | AFTER=%s", before, after)

            rows = await asyncio.to_thread(
                upload_to_bigquery_sync, client, df, table_name, dataset_id
            )

            access_info = get_access_info(config.PROJECT_ID, dataset_id, table_name)
            profiling_results = await asyncio.to_thread(
                query_and_profile_sync, client, access_info["sql_query"]
            )
            total_rows_uploaded += rows
            return {
                "table": table_name,
                "rows_uploaded": rows,
                "access_info": access_info,
                "profiling_uuid": profiling_results.get("profiling_uuid", ""),
                "data_quality_score": profiling_results.get("data_quality_score"),
                "duplicate_count": dup_info["duplicate_count"],
                "sample_duplicates": dup_info["sample_duplicates"],
                "all_duplicates": dup_info["all_duplicates"],
                "all_duplicates":  dup_info["all_duplicates"]
            }
        if isinstance(data, dict):
            logging.info("ROUTER: Multi-sheet Excel | sheets=%s", list(data.keys()))
            for sheet_name, df in data.items():
                if df is None or df.empty:
                    logging.info("ROUTER: Skipping empty sheet=%s", sheet_name)
                    continue
                safe_sheet = "".join(c if c.isalnum() else "_" for c in sheet_name).lower()
                sheet_table_name = f"{base_table_name}_{safe_sheet}"
                result = await _upload_sheet(df, sheet_table_name)
                result["sheet"] = sheet_name
                upload_results.append(result)

        else:
            logging.info("ROUTER: Single DataFrame | rows=%d | cols=%d", data.shape[0], data.shape[1])
            result = await _upload_sheet(data, base_table_name)
            result["sheet"] = None
            upload_results.append(result)

        if not upload_results:
            raise HTTPException(status_code=400, detail="No valid sheets/data found in file.")

        first = upload_results[0]
        file_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        from utils.session_identity import session_user_email

        response_object = FileUploadResponse(
            sessionID=session_id,
            user=session_user_email(session_id),
            createdDate=now,
            lastUpdateDate=now,
            file_id=file_id,
            filename=file.filename,
            table_name=first["table"],
            dataset_id=dataset_id,
            project_id=config.PROJECT_ID,
            rows_uploaded=total_rows_uploaded,
            upload_timestamp=now,
            access_info={
                "tables_created": [
                    {
                        "sheet_name": r["sheet"],
                        "table_name": r["table"],
                        "rows_uploaded": r["rows_uploaded"],
                        "duplicate_count": r["duplicate_count"],
                        "sample_duplicates": r["sample_duplicates"],
                        "all_duplicates": r["all_duplicates"],
                    }
                    for r in upload_results
                ],
                **first["access_info"],
            },
            initial_profiling_report=first["profiling_uuid"],
            profiling_report_url=f"/reports/{first['profiling_uuid']}.html",
            data_quality_score=first["data_quality_score"],
        )

        logging.info("ROUTER: File processing COMPLETE for file=%s | sheets=%d | total_rows=%d",
                     file.filename, len(upload_results), total_rows_uploaded)
        return response_object, None

    except HTTPException as e:
        logging.error("ROUTER: HTTPException while processing file=%s | error=%s", file.filename, e.detail)
        return None, {"filename": file.filename, "error": e.detail}
    except Exception as e:
        logging.exception("ROUTER: Unhandled exception while processing file=%s", file.filename)
        return None, {"filename": file.filename, "error": str(e)}  
                
@router.post("/upload-batch")
async def upload_batch(
    files: List[UploadFile] = File(...),
    app_session_id: Optional[str] = Form(None, description="Owning app session id."),
    data_dict_files: Optional[List[UploadFile]] = File(None, description="Optional Vendor-provided Data Dictionaries."),
    data_dict_file: Optional[UploadFile] = File(None, description="Optional Vendor-provided Data Dictionary."),
    brd_file: Optional[UploadFile] = File(None, description="Optional Business Requirements Document."),
    file_spec_file: Optional[UploadFile] = File(None, description="Optional File Specification document."),
    project_name: Optional[str] = Form(None, description="Name of the overall project."),
    vendor_name: Optional[str] = Form(None, description="Name of the data vendor."),
    vendor_contact_person: Optional[str] = Form(None, description="Contact email for the vendor."),
    file_delivery_frequency: Optional[str] = Form(None, description="How often files are delivered (e.g., daily=1, weekly=7)."),
    brd_description: Optional[str] = Form(None, description="A brief description of the BRD content (if provided)."),
    spec_description: Optional[str] = Form(None, description="A brief description of the specification file content (if provided)."),
    session_id: str = Form(None, description="Current session id"),
    transfer_method: Optional[str] = Form(None, description="Method used to transfer the file (e.g., SFTP, API, Email)."),
    vendor_contact_name: Optional[str] = Form(None, description="Full name of the vendor's primary contact person."),
    frequency_mode: Optional[str] = Form(None, description="Delivery mode classification such as daily, weekly, monthly, or ad-hoc."),
    vendor_phone_number: Optional[str] = Form(None, description="Phone number of the vendor's primary contact."),
    dependencies: Optional[str] = Form(None, description="Any upstream or downstream system dependencies related to this file."),
    vendor_email: Optional[str] = Form(None, description="Vendor email address used for operational communications."),
    email_notification_dl: Optional[str] = Form(None, description="Distribution list email address for automated file notifications."),
    date_timestamp_format: Optional[str] = Form(None, description="Date and timestamp format used within the file (e.g., YYYY-MM-DD)."),
    header_record_number: Optional[str] = Form(None, description="Expected record identifier or count for header rows."),
    trailer_record_number: Optional[str] = Form(None, description="Expected record identifier or count for trailer rows."),
    quote_indicator: Optional[str] = Form(None, description="Character used to wrap text values in the file (e.g., double quote)."),
    file_population_type: Optional[str] = Form(None, description="Indicates whether file is full population, delta, or incremental load."),
    file_compression_type: Optional[str] = Form(None, description="Compression format applied to the file (e.g., ZIP, GZIP)."),
    receive_file_when_no_data: Optional[str] = Form(None, description="Indicates whether a file is expected even when no data is present."),
    assumptions: Optional[str] = Form(None, description="Any documented assumptions related to file processing or data interpretation."),
    vendor_server_name: Optional[str] = Form(None, description="Name or address of the vendor server from which files are received."),
    current_user: CurrentUser = Depends(resolve_current_user),
):
    """Step 1: Upload files and detect DD candidates - STATELESS"""
    try:
        session_key = app_session_id or session_id
        logger.info(f"--- [/upload-batch] Received request for session_id: {session_key} ---")
        # --- Handle additional information ---
        physical_file_name = ", ".join(
            uploaded_file.filename for uploaded_file in files
        )

        file_extension = ", ".join(
            os.path.splitext(uploaded_file.filename)[1]
            for uploaded_file in files
        )
        additional_info = {
            "added_to_context": False,
            "project_name": project_name,
            "vendor_name": vendor_name,
            "vendor_contact_person": vendor_contact_person,
            "file_delivery_frequency": file_delivery_frequency,
            "brd_description": brd_description,
            "spec_description": spec_description,
            "physical_file_name": physical_file_name,
            "transfer_method": transfer_method,
            "vendor_contact_name": vendor_contact_name,
            "frequency_mode": frequency_mode,
            "vendor_phone_number": vendor_phone_number,
            "dependencies": dependencies,
            "vendor_email": vendor_email,
            "email_notification_dl": email_notification_dl,
            "file_extension": file_extension,
            "date_timestamp_format": date_timestamp_format,
            "header_record_number": header_record_number,
            "trailer_record_number": trailer_record_number,
            "quote_indicator": quote_indicator,
            "file_population_type": file_population_type,
            "file_compression_type": file_compression_type,
            "receive_file_when_no_data": receive_file_when_no_data,
            "assumptions": assumptions,
            "vendor_server_name": vendor_server_name,
        }

        if not session_key:
            raise HTTPException(status_code=400, detail="session_id is required.")

        profiling_run_id: str | None = None
        if app_session_id:
            if not is_app_db_enabled():
                raise HTTPException(status_code=503, detail="App session database is not configured.")
            with app_db_session() as db:
                repo = AppSessionRepository(db)
                app_session = repo.get_session(session_id=app_session_id, user_key=current_user.user_key)
                if not app_session:
                    raise HTTPException(status_code=404, detail="Session not found.")
                profiling_run = repo.create_profiling_run(
                    session=app_session,
                    profiling_context_uri=profiling_context_uri(session_key),
                    vertex_session_id=app_session.active_vertex_session_id,
                    vertex_app_name=app_session.active_vertex_app_name,
                )
                profiling_run_id = profiling_run.id

        uploaded_info: dict[str, Any] = {}
        brd_extraction_status = None

        # Upload all input files to GCS under raw_files/{input_name}/
        raw_file_urls: dict[str, list[dict[str, str]]] = {}
        _raw_inputs: list[tuple[str, list[UploadFile]]] = [
            ("files", files),
            ("data_dict_files", list(data_dict_files or [])),
            ("data_dict_file", [data_dict_file] if data_dict_file else []),
            ("brd_file", [brd_file] if brd_file else []),
        ]
        for input_name, input_files in _raw_inputs:
            for raw_file in input_files:
                try:
                    content = await raw_file.read()
                    uri = save_raw_file(
                        session_id=session_key,
                        filename=raw_file.filename,
                        content=content,
                        subfolder=input_name,
                    )
                    raw_file.file.seek(0)
                    raw_file_urls.setdefault(input_name, []).append({"filename": raw_file.filename, "gcs_uri": uri})
                    logger.info("Uploaded raw file [%s] to GCS: %s", input_name, uri)
                except Exception as e:
                    logger.error("Failed to upload raw file [%s] %s to GCS: %s", input_name, raw_file.filename, e)

        # Save DD files if provided
        dd_uploads: List[UploadFile] = []
        if data_dict_files:
            dd_uploads.extend(data_dict_files)
        if data_dict_file:
            dd_uploads.append(data_dict_file)

        if dd_uploads:
            dd_paths: List[str] = []
            for upload_file in dd_uploads:
                try:
                    artifact_uri = await _save_optional_document(session_key, upload_file, "data_dict")
                    dd_paths.append(artifact_uri)
                    logging.info("Saved data_dict_file_path item to %s", artifact_uri)
                except Exception as e:
                    logging.error(f"Failed to save data_dict_file_path item: {str(e)}")
            if dd_paths:
                uploaded_info["data_dict_file_path"] = dd_paths

        for file_label, upload_file, document_kind in [
            ("brd_file", brd_file, "brd"),
            ("file_spec_file", file_spec_file, "file_spec"),
        ]:
            if upload_file:
                try:
                    artifact_uri = await _save_optional_document(session_key, upload_file, document_kind)
                    uploaded_info[file_label] = artifact_uri
                    logging.info("Saved %s to %s", file_label, artifact_uri)
                except Exception as e:
                    error_msg = f"Failed to save {file_label}: {str(e)}"
                    logging.error(error_msg)
                    if file_label == "brd_file":
                        brd_extraction_status = {
                            "brd_exists": True,
                            "extraction_attempted": False,
                            "extraction_success": False,
                            "error_description": error_msg
                        }

        session_entry = {**additional_info, **uploaded_info}
        context_data, _ = update_profiling_session_context(session_key, session_entry)
        logging.info("Session %s updated in profiling context artifact", session_key)

        # Resolve metadata path
        if brd_extraction_status is None:
            metadata_path, brd_extraction_status = await resolve_metadata_path(
                uploaded_info=uploaded_info,
                brd_file_path=uploaded_info.get("brd_file"),
                session_id=session_key,
            )
        else:
            metadata_path = None

        logger.info(f"Metadata resolution complete | metadata_path={metadata_path} | brd_status={brd_extraction_status}")

        if (
            isinstance(metadata_path, list)
            and metadata_path
            and isinstance(metadata_path[0], dict)
        ):
            persist_dd_candidates(
                session_id=session_key,
                dd_candidates=metadata_path,
                brd_extraction_status=brd_extraction_status,
            )
            return {
                "status": "awaiting_dd_selection",
                "dd_candidates": metadata_path,
                "brd_extraction_status": brd_extraction_status,
                "raw_files": raw_file_urls,
            }

        if isinstance(metadata_path, list) and len(metadata_path) > 0:
            if isinstance(metadata_path[0], dict):
                metadata_path = metadata_path[0]["file_path"]
            else:
                metadata_path = metadata_path[0]

        if metadata_path:
            context_data = persist_resolved_metadata_path(
                session_id=session_key,
                metadata_path=str(metadata_path),
                selected_dd_paths=[str(metadata_path)],
                brd_extraction_status=brd_extraction_status,
            )
            logger.info("Updated session %s with data_dict_file_path=%s", session_key, metadata_path)
        logger.info("Profiling context contents after upload: %s", context_data)

        metadata_path_for_response: Optional[str] = None
        if metadata_path:
            metadata_path_for_response = materialize_metadata_path(metadata_path)

        if app_session_id and profiling_run_id:
            try:
                with app_db_session() as db:
                    repo = AppSessionRepository(db)
                    app_session = repo.get_session(session_id=app_session_id, user_key=current_user.user_key)
                    if app_session:
                        run = repo.get_current_profiling_run(session=app_session)
                        if run and run.id == profiling_run_id:
                            logger.info(
                                "Persisting profiling resume state | session_id=%s | run_id=%s | payload_keys=%s",
                                app_session_id,
                                run.id,
                                list({"currentStep": 1, "uploadResponse": {}}.keys()),
                            )
                            repo.update_profiling_run(
                                run=run,
                                status="READY",
                                current_step="dataset_overview",
                                profiling_context_uri=profiling_context_uri(session_key),
                                resume_state_json={
                                    "currentStep": 1,
                                    "uploadResponse": {
                                        "total_files": len(files),
                                        "status": "ready_for_processing",
                                        "metadata_path": metadata_path_for_response,
                                        "brd_extraction_status": make_json_compatible(brd_extraction_status),
                                    },
                                },
                            )
                            logger.info(
                                "Profiling resume state persisted | session_id=%s | run_id=%s | status=%s",
                                app_session_id,
                                run.id,
                                "READY",
                            )
            except Exception:
                logger.exception("Failed to persist profiling resume state for session_id=%s", app_session_id)
                raise

        logger.info(
            "Returning upload batch response | session_id=%s | status=%s | metadata_path=%s",
            session_key,
            "ready_for_processing",
            metadata_path_for_response,
        )
        return {
            "status": "ready_for_processing",
            "metadata_path": metadata_path_for_response,
            "brd_extraction_status": make_json_compatible(brd_extraction_status),
            "raw_files": raw_file_urls,
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        logger.exception(
            "Upload batch failed | session_id=%s | app_session_id=%s | file_count=%d",
            app_session_id or session_id,
            app_session_id,
            len(files) if files else 0,
        )
        raise HTTPException(status_code=500, detail=f"Error sending message: {e}")


@router.post("/process-files")
async def process_files(
    files: List[UploadFile] = File(...),
    metadata_path: str = Form(...),
    session_id: str = Form(...),
):
    """Process files after the user selects the desired DD metadata."""
    try:
        logger.info(
            "--- [/process-files] Processing %d files with metadata_path: %s ---",
            len(files),
            metadata_path,
        )

        if not files:
            raise HTTPException(status_code=400, detail="No files provided")
        if len(files) > 10:
            raise HTTPException(status_code=400, detail="Maximum 10 files per batch")

        client = get_bigquery_client()
        dataset_id = config.DATASET_ID
        processing_metadata_path = materialize_metadata_path(metadata_path)

        async def process_one(file: UploadFile):
            return await process_file(file, processing_metadata_path, client, dataset_id, session_id)

        expanded_files = expand_zip_files(files)
        if len(expanded_files) > 50:
            raise HTTPException(
                status_code=400,
                detail="Too many files after ZIP extraction. Maximum 50 files total.",
            )

        results = await handle_duplicate_file_groups(
            expanded_files=expanded_files,
            metadata_path=processing_metadata_path if processing_metadata_path else None,
            client=client,
            dataset_id=dataset_id,
            session_id=session_id,
            process_file=process_one,
        )

        successful_uploads: List[FileUploadResponse] = []
        failed_uploads: List[dict[str, Any]] = []

        for success, failure in results:
            if success:
                successful_uploads.append(success)
            if failure:
                failed_uploads.append(failure)

        return {
            "status": "completed",
            "successful_uploads": successful_uploads,
            "failed_uploads": failed_uploads,
            "summary": {
                "successful": len(successful_uploads),
                "failed": len(failed_uploads),
                "total_rows_uploaded": sum(
                    getattr(upload, "rows_uploaded", 0) for upload in successful_uploads
                ),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Processing failed")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/get-table-schemas")
async def get_table_schemas(req: SchemaRequest):
    """Get schema information for selected table files"""
    try:
        file_paths = req.file_paths
        logger.info(f"[SCHEMA] Getting schemas for {len(file_paths)} files: {file_paths}")
        
        schemas = []
        
        for file_path in file_paths:
            logger.info(f"[SCHEMA] Processing file: {file_path}")
            
            if not os.path.exists(file_path):
                logger.warning(f"[SCHEMA] File not found: {file_path}")
                continue
                
            try:
                # Read file to get schema
                if file_path.endswith('.xlsx'):
                    df = pd.read_excel(file_path, nrows=10)  # Just read first 10 rows for schema
                    logger.info(f"[SCHEMA] Read Excel file with {len(df.columns)} columns")
                elif file_path.endswith('.csv'):
                    df = pd.read_csv(file_path, nrows=10)  # Just read first 10 rows for schema
                    logger.info(f"[SCHEMA] Read CSV file with {len(df.columns)} columns")
                else:
                    logger.warning(f"[SCHEMA] Unsupported file type: {file_path}")
                    continue
                
                # Extract column information
                columns = []
                for col_name in df.columns:
                    col_info = {
                        "name": str(col_name),
                        "dataType": str(df[col_name].dtype),
                        "sampleValues": df[col_name].dropna().head(3).astype(str).tolist()
                    }
                    columns.append(col_info)
                
                schema = {
                    "tableName": os.path.basename(file_path),
                    "filePath": file_path,
                    "columns": columns
                }
                
                schemas.append(schema)
                logger.info(f"[SCHEMA] Extracted schema for {file_path}: {len(columns)} columns - {[c['name'] for c in columns]}")
                
            except Exception as e:
                logger.error(f"[SCHEMA] Failed to read file {file_path}: {str(e)}")
                continue
        
        logger.info(f"[SCHEMA] Successfully extracted {len(schemas)} schemas")
        logger.info(f"[SCHEMA] Returning schemas: {[s['tableName'] for s in schemas]}")
        return schemas
        
    except Exception as e:
        logger.error(f"[SCHEMA] Failed to get table schemas: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get table schemas: {str(e)}")


@router.post("/save-selected-dd")
async def save_selected_dd(
    session_id: str = Form(...),
    selected_paths: List[str] = Form(...),
    should_merge: bool = Form(False),
    column_mappings: Optional[str] = Form(None),
    target_schema: Optional[str] = Form(None)
):
    """Save user-selected DD file paths and optionally merge them into a single file"""
    try:
        return save_selected_dd_choice(
            session_id=session_id,
            selected_paths=selected_paths,
            should_merge=should_merge,
            column_mappings=column_mappings,
            target_schema=target_schema,
            apply_column_mapping=apply_column_mapping,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save/merge DD files: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save selection: {str(e)}")


@router.get("/table-info/{table_name}")
async def get_table_info(table_name: str):
    """Get information about an uploaded table"""
    client = get_bigquery_client()
    dataset_id = config.DATASET_ID

    try:
        table_ref = client.dataset(dataset_id).table(table_name)
        table = client.get_table(table_ref)
        
        return {
            "table_name": table_name,
            "dataset_id": dataset_id,
            "project_id": config.PROJECT_ID,
            "created": table.created.isoformat() if table.created else None,
            "modified": table.modified.isoformat() if table.modified else None,
            "num_rows": table.num_rows,
            "num_bytes": table.num_bytes,
            "schema": [{"name": field.name, "type": field.field_type, "mode": field.mode} 
                      for field in table.schema],
            "access_info": get_access_info(config.PROJECT_ID, dataset_id, table_name)
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Table not found: {str(e)}")

@router.get("/datasets")
async def list_datasets():
    """List all datasets in the project"""
    client = get_bigquery_client()
    
    try:
        datasets = list(client.list_datasets())
        return {
            "project_id": config.PROJECT_ID,
            "datasets": [
                {
                    "dataset_id": dataset.dataset_id,
                    "created": dataset.created.isoformat() if dataset.created else None,
                    "modified": dataset.modified.isoformat() if dataset.modified else None,
                    "location": dataset.location
                }
                for dataset in datasets
            ]
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error sending message: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list datasets: {str(e)}")

@router.get("/tables/{dataset_id}")
async def list_tables(dataset_id: str):
    """List all tables in a dataset"""
    client = get_bigquery_client()
    
    try:
        tables = list(client.list_tables(dataset_id))
        return {
            "dataset_id": dataset_id,
            "project_id": config.PROJECT_ID,
            "tables": [
                {
                    "table_name": table.table_id,
                    "created": table.created.isoformat() if table.created else None,
                    "modified": table.modified.isoformat() if table.modified else None,
                    "num_rows": table.num_rows,
                    "table_type": table.table_type
                }
                for table in tables
            ]
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Dataset not found: {str(e)}")

@router.get("/profiling-reports/{session_id}/{report_id}", response_class=HTMLResponse)
async def get_profiling_report(session_id: str, report_id: str):
    """Proxy a profiling HTML report stored in GCS."""
    try:
        html = load_profiling_report_html(session_id, report_id)
        return HTMLResponse(content=html)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load profiling report: {str(e)}")




@router.get("/html_report/")
async def get_html_report():
    """Get the latest profiling HTML report"""
    try:
        with open("bigquery_data_profile.html", "r", encoding="utf-8") as file:
            df_as_html = file.read()
        return f"""{df_as_html}"""
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read HTML report: {str(e)}")


def _signed_url(blob_name: str, expiration_seconds: int = 3600) -> str:
    from datetime import timedelta
    client = artifact_storage_client()
    bucket = client.bucket(artifact_bucket_name())
    blob = bucket.blob(blob_name)
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=expiration_seconds),
        method="GET",
    )


@router.get("/download/{session_id}")
async def get_session_download_urls(session_id: str):
    """Return signed download URLs for brd, uploaded files, and data dict for a session."""
    try:
        base = session_base(session_id)
        bucket_name = artifact_bucket_name()

        def blobs_for(prefix: str):
            return list_blobs(prefix=f"{prefix}/")

        brd_blobs = blobs_for(f"{base}/raw_files/brd_file")
        files_blobs = blobs_for(f"{base}/raw_files/files")
        dd_blobs = blobs_for(f"{base}/raw_files/data_dict_files")

        brd_url = _signed_url(brd_blobs[0].name) if brd_blobs else None
        files_urls = [_signed_url(b.name) for b in files_blobs]
        dd_url = _signed_url(dd_blobs[0].name) if dd_blobs else None

        return {
            "brd": brd_url,
            "files": files_urls,
            "dd": dd_url,
        }
    except Exception as e:
        logger.exception("Failed to generate download URLs for session_id=%s", session_id)
        raise HTTPException(status_code=500, detail=f"Failed to generate download URLs: {e}")


@router.get("/download/{session_id}/{file_url:path}")
async def download_file_by_url(session_id: str, file_url: str):
    """Download a specific file using its signed URL for a given session."""
    try:
        logger.info(f"Download request for session_id={session_id}, file_url={file_url}")
        
        # Decode the URL if it's encoded
        decoded_url = unquote(file_url)
        logger.info(f"Decoded URL: {decoded_url}")
        
        # Extract blob name from the signed URL
        blob_name = None
        
        if "storage.googleapis.com" in decoded_url:
            # Parse signed URL: https://storage.googleapis.com/bucket-name/blob/path?X-Goog-Algorithm=...
            url_parts = decoded_url.split("?")
            if len(url_parts) >= 1:
                base_url = url_parts[0]  # Remove query parameters
                path_parts = base_url.split("/")
                if len(path_parts) >= 5:  # https://storage.googleapis.com/bucket/blob/path
                    bucket_from_url = path_parts[3]
                    expected_bucket = artifact_bucket_name()
                    
                    if bucket_from_url == expected_bucket:
                        blob_name = "/".join(path_parts[4:])
                        logger.info(f"Extracted blob name from signed URL: {blob_name}")
                    else:
                        logger.warning(f"Bucket mismatch: {bucket_from_url} != {expected_bucket}")
                        raise HTTPException(status_code=400, detail="Invalid bucket in URL")
        
        if not blob_name:
            raise HTTPException(status_code=400, detail="Could not extract blob name from signed URL")
        
        # Validate that the blob belongs to the session
        base = session_base(session_id)
        if not blob_name.startswith(base):
            logger.warning(f"Access denied: blob {blob_name} does not belong to session {session_id}")
            raise HTTPException(status_code=403, detail="Access denied: File does not belong to this session")
        
        # Get the blob from GCS
        client = artifact_storage_client()
        bucket = client.bucket(artifact_bucket_name())
        blob = bucket.blob(blob_name)
        
        # Check if blob exists
        if not blob.exists():
            logger.warning(f"Blob does not exist: {blob_name}")
            # List available files for debugging
            try:
                all_blobs = list_blobs(prefix=f"{base}/")
                available_names = [os.path.basename(b.name) for b in all_blobs[:10]]
                logger.info(f"Available files in session: {available_names}")
            except Exception as list_error:
                logger.error(f"Could not list available files: {list_error}")
            
            raise HTTPException(status_code=404, detail="File not found in storage")
        
        # Get file info
        blob.reload()
        file_size = blob.size
        content_type = blob.content_type or "application/octet-stream"
        filename = os.path.basename(blob_name)
        
        logger.info(f"Downloading file: {filename}, size: {file_size}, type: {content_type}")
        
        # Stream the file content
        def generate_file_stream():
            try:
                # For smaller files, download all at once
                if file_size <= 10 * 1024 * 1024:  # 10MB or less
                    content = blob.download_as_bytes()
                    yield content
                else:
                    # For larger files, download in chunks
                    chunk_size = 1024 * 1024  # 1MB chunks
                    start = 0
                    
                    while start < file_size:
                        end = min(start + chunk_size - 1, file_size - 1)
                        chunk = blob.download_as_bytes(start=start, end=end)
                        yield chunk
                        start = end + 1
                        
            except Exception as e:
                logger.error(f"Error streaming file {blob_name}: {e}")
                raise
        
        # Return streaming response
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(file_size),
        }
        
        return StreamingResponse(
            generate_file_stream(),
            media_type=content_type,
            headers=headers
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to download file for session_id={session_id}, file_url={file_url}")
        raise HTTPException(status_code=500, detail=f"Failed to download file: {e}")


@router.get("/sample-data")
def download_sample_data():
    """Stream a zip of the bundled sample datasets so users can try the app
    without supplying their own files. Very large samples are skipped to keep
    the download lightweight. Public (no auth) so it works in any auth mode."""
    import io
    import zipfile
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    sample_dir = repo_root / "sample_data"
    if not sample_dir.is_dir():
        raise HTTPException(status_code=404, detail="Sample data not found.")

    max_bytes = 50 * 1024 * 1024  # skip very large samples (e.g. the ~99MB file)
    buffer = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(sample_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() == ".zip":
                continue  # skip nested zips
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            zf.write(path, arcname=path.name)
            added += 1

    if added == 0:
        raise HTTPException(status_code=404, detail="No sample data files available.")

    buffer.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="sttm_sample_data.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)
