from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
from fastapi import HTTPException

from utils.gcs_artifact_utils import make_json_compatible
from utils.profiling_artifact_store import (
    load_profiling_session_context,
    materialize_profiling_artifact,
    save_generated_file,
    update_profiling_session_context,
)


logger = logging.getLogger(__name__)
MAX_DD_ROWS = 100000


def load_dd_session_state(session_id: str) -> dict[str, Any]:
    if not session_id:
        return {}
    return load_profiling_session_context(session_id)


def _first_path_from_value(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, list):
        if not value:
            return None
        first = value[0]
        if isinstance(first, dict):
            return str(first.get("file_path") or "").strip() or None
        return str(first).strip() or None
    if isinstance(value, dict):
        return str(value.get("file_path") or "").strip() or None
    return str(value).strip() or None


def primary_metadata_path_from_state(session_state: dict[str, Any]) -> Optional[str]:
    resolved_path = _first_path_from_value(session_state.get("resolved_metadata_path"))
    if resolved_path:
        return resolved_path
    selected_path = _first_path_from_value(session_state.get("data_dict_file_path"))
    if selected_path:
        return selected_path
    return _first_path_from_value(session_state.get("dd_candidates"))


def materialize_metadata_path(metadata_path: Optional[str]) -> Optional[str]:
    raw = _first_path_from_value(metadata_path)
    if not raw:
        return None
    return str(materialize_profiling_artifact(raw))


def persist_dd_candidates(
    *,
    session_id: str,
    dd_candidates: list[dict[str, Any]],
    brd_extraction_status: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "dd_candidates": make_json_compatible(dd_candidates),
    }
    if brd_extraction_status is not None:
        updates["brd_extraction_status"] = make_json_compatible(brd_extraction_status)
    context, _ = update_profiling_session_context(session_id, updates)
    return context


def persist_resolved_metadata_path(
    *,
    session_id: str,
    metadata_path: Optional[str],
    selected_dd_paths: Optional[list[str]] = None,
    brd_extraction_status: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    raw = _first_path_from_value(metadata_path)
    updates: dict[str, Any] = {
        "resolved_metadata_path": raw,
    }
    if raw:
        updates["data_dict_file_path"] = [raw]
    if selected_dd_paths is not None:
        updates["selected_dd_paths"] = make_json_compatible(selected_dd_paths)
    if brd_extraction_status is not None:
        updates["brd_extraction_status"] = make_json_compatible(brd_extraction_status)
    context, _ = update_profiling_session_context(session_id, updates)
    return context


def extract_selected_dd_paths(
    *,
    session_state: dict[str, Any],
    selected_paths: list[str],
) -> list[str]:
    current_dd_data = session_state.get("dd_candidates") or session_state.get("data_dict_file_path", [])
    if (
        isinstance(current_dd_data, list)
        and current_dd_data
        and isinstance(current_dd_data[0], dict)
    ):
        selected_set = {str(path).strip() for path in selected_paths if str(path).strip()}
        return [
            str(candidate["file_path"]).strip()
            for candidate in current_dd_data
            if str(candidate.get("file_path") or "").strip() in selected_set
        ]
    return [str(path).strip() for path in selected_paths if str(path).strip()]


def save_selected_dd_choice(
    *,
    session_id: str,
    selected_paths: list[str],
    should_merge: bool,
    column_mappings: Optional[str],
    target_schema: Optional[str],
    apply_column_mapping: Callable[[pd.DataFrame, str, list[dict[str, Any]], list[dict[str, Any]]], pd.DataFrame],
) -> dict[str, Any]:
    logger.info(
        "--- [save-selected-dd] Processing %d DD files for session %s, merge=%s ---",
        len(selected_paths),
        session_id,
        should_merge,
    )

    session_state = load_dd_session_state(session_id)
    selected_file_paths = extract_selected_dd_paths(
        session_state=session_state,
        selected_paths=selected_paths,
    )

    if should_merge and len(selected_file_paths) > 1:
        logger.info("[MERGE] Starting merge process for %d DD files", len(selected_file_paths))
        logger.info("[MERGE] Selected files: %s", selected_file_paths)

        parsed_mappings: list[dict[str, Any]] = []
        parsed_target_schema: list[dict[str, Any]] = []

        if column_mappings:
            try:
                parsed_mappings = json.loads(column_mappings)
                logger.info("[MERGE] Column mappings provided: %d mappings", len(parsed_mappings))
            except json.JSONDecodeError as exc:
                logger.warning("[MERGE] Failed to parse column mappings: %s", exc)

        if target_schema:
            try:
                parsed_target_schema = json.loads(target_schema)
                logger.info("[MERGE] Target schema provided: %d columns", len(parsed_target_schema))
            except json.JSONDecodeError as exc:
                logger.warning("[MERGE] Failed to parse target schema: %s", exc)

        merged_dfs: list[pd.DataFrame] = []
        merge_stats = {
            "files_processed": 0,
            "files_failed": 0,
            "total_rows_before": 0,
            "files_skipped": 0,
            "schema_normalized": bool(parsed_mappings and parsed_target_schema),
        }

        file_order_map = {path: idx + 1 for idx, path in enumerate(selected_paths)}
        selected_file_paths_ordered = sorted(
            selected_file_paths,
            key=lambda item: file_order_map.get(item, float("inf")),
        )
        logger.info(
            "[MERGE] Processing files in extraction order: %s",
            [os.path.basename(item) for item in selected_file_paths_ordered],
        )

        for idx, file_path in enumerate(selected_file_paths_ordered, 1):
            logger.info("[MERGE] Processing file %d/%d: %s", idx, len(selected_file_paths_ordered), file_path)
            try:
                materialized_path = materialize_profiling_artifact(file_path)
                if not materialized_path.exists():
                    logger.warning("[MERGE] File not found, skipping: %s", file_path)
                    merge_stats["files_skipped"] += 1
                    continue

                file_ext = materialized_path.suffix.lower()
                logger.info("[MERGE] Reading file with extension: %s", file_ext)

                if file_ext == ".xlsx":
                    df = pd.read_excel(materialized_path, nrows=MAX_DD_ROWS)
                elif file_ext == ".csv":
                    df = pd.read_csv(materialized_path, nrows=MAX_DD_ROWS)
                else:
                    logger.warning("[MERGE] Unsupported file type %s, skipping: %s", file_ext, file_path)
                    merge_stats["files_skipped"] += 1
                    continue

                if "extraction_order" not in df.columns:
                    extraction_order_val = file_order_map[file_path]
                    df["extraction_order"] = extraction_order_val
                    logger.info("[MERGE] Added extraction_order %s to file %d", extraction_order_val, idx)
                else:
                    logger.info("[MERGE] Using existing extraction_order from file")

                if df.empty:
                    logger.warning("[MERGE] Empty dataframe from file, skipping: %s", file_path)
                    merge_stats["files_skipped"] += 1
                    continue

                logger.info("[MERGE] File columns: %s", list(df.columns))

                if parsed_mappings and parsed_target_schema:
                    extraction_order_val = df["extraction_order"]
                    df = apply_column_mapping(df, file_path, parsed_mappings, parsed_target_schema)
                    df["extraction_order"] = extraction_order_val.values
                    logger.info("[MERGE] Applied column mapping, new columns: %s", list(df.columns))

                merged_dfs.append(df)
                merge_stats["files_processed"] += 1
                merge_stats["total_rows_before"] += len(df)
                logger.info("[MERGE] File %d processed successfully: %d rows added to merge queue", idx, len(df))
            except Exception as exc:
                logger.error("[MERGE] Failed to read DD file %s: %s", file_path, exc)
                logger.error("[MERGE] Error type: %s", type(exc).__name__)
                merge_stats["files_failed"] += 1

        logger.info("[MERGE] File processing complete:")
        logger.info("[MERGE]   - Files processed: %s", merge_stats["files_processed"])
        logger.info("[MERGE]   - Files failed: %s", merge_stats["files_failed"])
        logger.info("[MERGE]   - Files skipped: %s", merge_stats["files_skipped"])
        logger.info("[MERGE]   - Total rows before merge: %s", merge_stats["total_rows_before"])

        if not merged_dfs:
            error_msg = (
                "No valid DD files could be loaded for merging. "
                f"Processed: {merge_stats['files_processed']}, "
                f"Failed: {merge_stats['files_failed']}, "
                f"Skipped: {merge_stats['files_skipped']}"
            )
            logger.error("[MERGE] %s", error_msg)
            raise HTTPException(status_code=400, detail=error_msg)

        try:
            merged_df = pd.concat(merged_dfs, ignore_index=True, sort=False)
            del merged_dfs

            if "extraction_order" in merged_df.columns:
                merged_df = merged_df.sort_values("extraction_order", kind="stable").reset_index(drop=True)
                logger.info("[MERGE] Sorted merged dataframe by extraction_order")

            if "extraction_order" in merged_df.columns:
                merged_df = merged_df.drop(columns=["extraction_order"])
                logger.info("[MERGE] Dropped extraction_order from final dataframe")

            if parsed_target_schema:
                target_columns = [col["name"] for col in parsed_target_schema]
                if "Section" in merged_df.columns and "Section" not in target_columns:
                    target_columns.insert(0, "Section")
                merged_df = merged_df.reindex(columns=target_columns, fill_value=pd.NA)
                logger.info("[MERGE] Applied strict schema alignment with %d columns", len(target_columns))
            else:
                cols = list(merged_df.columns)
                ordered_cols = [col for col in ["Section"] if col in cols]
                ordered_cols.extend([col for col in cols if col not in ordered_cols])
                merged_df = merged_df[ordered_cols]
                logger.info("[MERGE] Reordered columns with Section first")

            logger.info("[MERGE] Concatenation successful: %d total rows, %d total columns", len(merged_df), len(merged_df.columns))
            logger.info("[MERGE] Final dataframe memory usage: %.2f MB", merged_df.memory_usage(deep=True).sum() / 1024 / 1024)

            null_counts = merged_df.isnull().sum()
            if null_counts.any():
                logger.info("[MERGE] Null value counts per column: %s", null_counts[null_counts > 0].to_dict())
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("[MERGE] Failed to concatenate dataframes: %s", exc)
            logger.error("[MERGE] Error type: %s", type(exc).__name__)
            raise HTTPException(status_code=500, detail=f"Failed to merge dataframes: {exc}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        merged_filename = f"merged_dd_{session_id}_{timestamp}.xlsx"

        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                temp_path = Path(tmp.name)
            merged_df.to_excel(temp_path, index=False)
            file_size = temp_path.stat().st_size
            logger.info("[MERGE] Temp merged file saved successfully: %s", temp_path)
            logger.info("[MERGE] File size: %s bytes (%.2f MB)", f"{file_size:,}", file_size / 1024 / 1024)
            result_path = save_generated_file(
                session_id=session_id,
                local_path=temp_path,
                generated_kind="merged_dd",
                filename=merged_filename,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("[MERGE] Failed to save merged file: %s", exc)
            logger.error("[MERGE] Error type: %s", type(exc).__name__)
            raise HTTPException(status_code=500, detail=f"Failed to save merged file: {exc}")
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

        merged_info = {
            "source_files": make_json_compatible(selected_file_paths),
            "total_rows": len(merged_df),
            "total_columns": len(merged_df.columns),
            "created_at": timestamp,
            "is_merged": True,
            "merge_stats": make_json_compatible(merge_stats),
        }
        update_profiling_session_context(
            session_id,
            {
                "data_dict_file_path": [result_path],
                "selected_dd_paths": make_json_compatible(selected_file_paths),
                "resolved_metadata_path": result_path,
                "merged_dd_info": merged_info,
            },
        )

        result_info = {
            "merged": True,
            "total_rows": len(merged_df),
            "total_columns": len(merged_df.columns),
            "source_files_count": len(selected_file_paths),
            "files_processed": merge_stats["files_processed"],
            "files_failed": merge_stats["files_failed"],
            "files_skipped": merge_stats["files_skipped"],
        }

        logger.info("[MERGE] Merge process completed successfully:")
        logger.info("[MERGE]   - Final merged file: %s", result_path)
        logger.info("[MERGE]   - Total rows in merged file: %d", len(merged_df))
        logger.info("[MERGE]   - Total columns in merged file: %d", len(merged_df.columns))
        logger.info("[MERGE]   - Source files processed: %d/%d", merge_stats["files_processed"], len(selected_file_paths))
    else:
        logger.info("[SAVE] Saving %d DD file paths without merging", len(selected_file_paths))
        logger.info("[SAVE] Selected files: %s", selected_file_paths)

        result_path = selected_file_paths[0] if selected_file_paths else None
        result_info = {
            "merged": False,
            "files_count": len(selected_file_paths),
        }
        update_profiling_session_context(
            session_id,
            {
                "data_dict_file_path": make_json_compatible(selected_file_paths),
                "selected_dd_paths": make_json_compatible(selected_file_paths),
                "resolved_metadata_path": result_path,
                "merged_dd_info": None,
            },
        )

        logger.info("[SAVE] Files saved successfully: %d paths stored", len(selected_file_paths))
        logger.info("[SAVE] Primary file path: %s", result_path)

    return {
        "status": "saved",
        "session_id": session_id,
        "path": result_path,
        "info": result_info,
    }
