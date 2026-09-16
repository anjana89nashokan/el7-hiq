# utils/data_anomaly_functions_batched.py
"""
Batched anomaly detection pipeline for large profiling sessions.

This module mirrors the large-table strategy used by profiling_functions_batched:
- Tables are chunked into manageable batches to control BigQuery and LLM load.
- Each batch processes tables in parallel with bounded worker pools.
- Processing metadata (batch timings, tables per batch) is surfaced for streaming UX.
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from typing import Any, Dict, Iterable, List, Tuple, OrderedDict

from google.adk.tools import ToolContext

from utils.anomaly_table_resolver import (
    collect_anomaly_source_tables,
    normalize_table_refs as _normalize_table_refs,
    resolve_anomaly_table_references as _resolve_anomaly_table_references,
)
from utils.data_anomaly_functions import (
    AnomalyConfig,
    GCP_AVAILABLE,
    _analyze_table_anomalies,
    _generate_anomaly_summary,
    _generate_mock_anomaly_results,
    _get_bigquery_client,
    _get_sensitivity_config,
    _make_json_serializable,
    _summarize_table_anomalies,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = int(os.getenv("ANOMALY_TABLE_BATCH_SIZE", "8"))


def _chunk(items: Iterable[str], size: int) -> List[List[str]]:
    """Split a list of table references into evenly-sized batches."""
    items = list(items)
    if not items:
        return []

    size = max(1, size)
    return [items[i : i + size] for i in range(0, len(items), size)]


def _should_use_mock(tables: List[str]) -> bool:
    """Determine if the mock anomaly generator should be used."""
    if not GCP_AVAILABLE:
        return True
    return any("mock" in table.lower() for table in tables)


def _process_batch(
    client,
    batch_tables: List[str],
    config: AnomalyConfig,
    batch_num: int,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """
    Process a batch of tables in parallel, returning per-table reports and metadata.

    Fixes applied:
    - Bound worker pool to config.max_workers (from fixed module: conservative=4).
    - Use per-future timeout to avoid indefinite hangs.
    - Key results by full table reference to prevent dataset collisions.
    - Ensure success status is explicit; synthesize summary if missing.
    """
    batch_start = time.time()
    table_results: Dict[str, Dict[str, Any]] = {}
    successes = 0

    max_workers = min(config.max_workers, max(1, len(batch_tables)))
    timeout_s = getattr(config, "query_timeout_seconds", 120)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_analyze_table_anomalies, client, table, config): table for table in batch_tables}

        for future in as_completed(future_map):
            table_ref = future_map[future]
            try:
                report = future.result(timeout=timeout_s)
                if report and isinstance(report, dict):
                    # Backfill status and summary for consistency
                    report.setdefault("status", "success")
                    if not report.get("anomaly_summary"):
                        report["anomaly_summary"] = _summarize_table_anomalies(report)
                    # Use FULL REF as the key to avoid collisions across datasets/projects
                    table_results[table_ref] = report
                    successes += 1
                else:
                    table_results[table_ref] = {
                        "status": "error",
                        "error_message": "No report returned",
                        "table_reference": table_ref,
                    }
            except FuturesTimeout as exc:
                logger.error("Anomaly analysis timed out for %s after %ss", table_ref, timeout_s)
                table_results[table_ref] = {
                    "status": "error",
                    "error_message": f"Timeout after {timeout_s}s",
                    "table_reference": table_ref,
                }
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error("Anomaly analysis failed for %s: %s", table_ref, exc)
                table_results[table_ref] = {
                    "status": "error",
                    "error_message": str(exc),
                    "table_reference": table_ref,
                }

    batch_duration = time.time() - batch_start

    metadata = {
        "batch_num": batch_num,
        "tables_in_batch": len(batch_tables),
        "tables_succeeded": successes,
        "tables_failed": len(batch_tables) - successes,
        "duration_seconds": round(batch_duration, 2),
        "max_workers_used": max_workers,
        "timeout_seconds": timeout_s,
    }

    return table_results, metadata


def _run_batched_analysis(
    client,
    tables: List[str],
    config: AnomalyConfig,
    batch_size: int,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Execute anomaly detection across all tables via batches.

    Returns:
        (table_reports, batch_metadata_list)
    """
    batches = _chunk(tables, batch_size)
    logger.info("Anomaly batching: %s tables -> %s batches (size=%s)", len(tables), len(batches), batch_size)

    ordered_reports: Dict[str, Dict[str, Any]] = {}
    batch_metadata: List[Dict[str, Any]] = []

    for idx, batch in enumerate(batches, start=1):
        logger.info("Processing anomaly batch %s/%s (%s tables)", idx, len(batches), len(batch))
        reports, metadata = _process_batch(client, batch, config, idx)
        # Preserve deterministic order by inserting in batch sequence order
        for table_ref in batch:
            if table_ref in reports:
                ordered_reports[table_ref] = reports[table_ref]
        # Also include any stragglers (shouldn't happen, but just in case)
        for k, v in reports.items():
            if k not in ordered_reports:
                ordered_reports[k] = v
        batch_metadata.append(metadata)

    return ordered_reports, batch_metadata


def _build_processing_stats(
    tables_processed: int,
    summary: Dict[str, Any],
    batch_metadata: List[Dict[str, Any]],
    total_duration: float,
) -> Dict[str, Any]:
    """Assemble processing statistics for the final payload."""
    avg_batch_time = (
        sum(meta["duration_seconds"] for meta in batch_metadata) / len(batch_metadata)
        if batch_metadata
        else 0.0
    )

    return {
        "tables_processed": tables_processed,
        "total_processing_time": round(total_duration, 2),
        "total_anomalies_detected": summary.get("total_anomalies", 0),
        "anomaly_categories_detected": len(summary.get("anomaly_categories", {})),
        "batches_processed": len(batch_metadata),
        "avg_batch_time": round(avg_batch_time, 2),
        # helpful debug fields
        "max_workers_configured": getattr(AnomalyConfig(), "max_workers", None),
        "per_table_timeout_seconds": getattr(AnomalyConfig(), "query_timeout_seconds", None),
    }


def data_anomaly_analysis_tool(
    table_references: Any,
    anomaly_sensitivity: str = "medium",
    tool_context: ToolContext | None = None,
) -> Dict[str, Any]:
    """
    Batched anomaly detection entry point registered with ADK FunctionTool.

    Args:
        table_references: Comma-separated string or iterable of table refs.
        anomaly_sensitivity: low | medium | high (impacts thresholds).
        tool_context: Present for parity with ADK tool signature.

    Returns:
        Dict matching data_anomaly_schema in profiling prompts.
    """
    raw_tables = _normalize_table_refs(table_references)
    tables = _resolve_anomaly_table_references(raw_tables, tool_context)
    if not tables:
        return {
            "status": "error",
            "error_message": (
                "No resolvable source tables for anomaly analysis. "
                "Complete Step 1 profiling first and use uploaded data tables, "
                "not IBC dashboard sheets (_nulls___lengths, _defaults, _foreign_keys)."
            ),
            "table_references": table_references,
            "requested_tables": raw_tables,
        }

    if raw_tables != tables:
        logger.info(
            "Anomaly table references remapped: %s -> %s",
            raw_tables,
            tables,
        )

    logger.info("Starting batched anomaly detection for %s tables", len(tables))
    start_time = time.time()
    processing_mode = "mock" if _should_use_mock(tables) else "bigquery_batched"

    if processing_mode == "mock":
        mock_response = _generate_mock_anomaly_results(tables, anomaly_sensitivity)
        mock_response["processing_mode"] = processing_mode
        mock_response["tables_analyzed"] = len(tables)
        mock_response["analysis_timestamp"] = int(time.time())
        mock_response["processing_stats"] = {
            "tables_processed": len(tables),
            "total_processing_time": round(time.time() - start_time, 2),
        }
        return mock_response

    # Create a single client (safe with conservative concurrency)
    client = _get_bigquery_client()
    config = _get_sensitivity_config(anomaly_sensitivity)
    # Respect conservative batch size and total concurrency
    batch_size = min(max(1, DEFAULT_BATCH_SIZE), len(tables))

    table_reports, batch_metadata = _run_batched_analysis(client, tables, config, batch_size)
    summary = _generate_anomaly_summary(table_reports)
    processing_stats = _build_processing_stats(
        tables_processed=sum(
            1 for report in table_reports.values() if report.get("status", "success") != "error"
        ),
        summary=summary,
        batch_metadata=batch_metadata,
        total_duration=time.time() - start_time,
    )

    result = {
        "status": "success",
        "analysis_timestamp": int(time.time()),
        "sensitivity_level": anomaly_sensitivity,
        "processing_mode": processing_mode,
        "tables_analyzed": len(tables),
        "table_anomaly_reports": table_reports,  # keyed by FULL table ref
        "summary_statistics": summary,
        "processing_stats": processing_stats,
        "batch_details": batch_metadata,
    }

    final_results = _make_json_serializable(result)
    # ==========================================
    # ADK-COMPLIANT SOLUTION: Use ToolContext.state
    # ==========================================
    # Store full results in session state (NOT returned to ADK agent)
    # This prevents token limit errors while keeping data accessible to /send-stream endpoint

    if tool_context and hasattr(tool_context, "state") and tool_context.state is not None:
        tool_context.state["data_anomaly_analysis_tool_response"] = final_results
        logger.warning("Stored anomaly analysis results in ToolContext.state")


    return final_results
