"""Resolve uploaded source table references for data anomaly analysis."""

from __future__ import annotations

import logging
import re
from typing import Any, List

from google.adk.tools import ToolContext

try:
    from utils.local_warehouse import normalize_table_name
except ImportError:

    def normalize_table_name(ref):  # type: ignore[misc]
        s = str(ref or "").strip().strip("`").strip('"')
        return s.split(".")[-1].strip()


logger = logging.getLogger(__name__)

IBC_DASHBOARD_SUFFIXES = (
    "_nulls___lengths",
    "_nulls_and_lengths",
    "_defaults",
    "_foreign_keys",
    "_foreignkeys",
)

METADATA_TABLE_PREFIXES = (
    "datadict_",
    "metadata_template_",
    "filespecs_",
    "metadata_",
)

_PROFILING_TEMPLATE_RE = re.compile(r"^(sttm_)?profiling_\d{4}")


def _canonical_table_key(name: str) -> str:
    return normalize_table_name(name).lower().replace("-", "_")


def normalize_table_refs(table_references: Any) -> List[str]:
    if not table_references:
        return []

    if isinstance(table_references, str):
        return [tbl.strip() for tbl in table_references.split(",") if tbl.strip()]

    if isinstance(table_references, (list, tuple, set)):
        tables: List[str] = []
        for item in table_references:
            if item:
                tables.extend(
                    normalize_table_refs(item)
                    if isinstance(item, (list, tuple, set))
                    else [str(item).strip()]
                )
        return [tbl for tbl in tables if tbl]

    return [str(table_references).strip()]


def is_ibc_dashboard_table(name: str) -> bool:
    lower = _canonical_table_key(name)
    return any(lower.endswith(suffix) for suffix in IBC_DASHBOARD_SUFFIXES)


def is_metadata_table(name: str) -> bool:
    lower = _canonical_table_key(name)
    return any(lower.startswith(prefix) for prefix in METADATA_TABLE_PREFIXES)


def is_profiling_template_table(name: str) -> bool:
    """True for IBC profiling export tables and profiling timestamp labels."""
    if is_ibc_dashboard_table(name):
        return True
    return bool(_PROFILING_TEMPLATE_RE.match(_canonical_table_key(name)))


def is_anomaly_source_candidate(name: str) -> bool:
    return not is_metadata_table(name) and not is_profiling_template_table(name)


def list_warehouse_table_names() -> List[str]:
    try:
        from utils.data_anomaly_functions import GCP_AVAILABLE, _get_bigquery_client

        if not GCP_AVAILABLE:
            return []
        client = _get_bigquery_client()
        return [normalize_table_name(table) for table in client.list_tables()]
    except Exception as exc:
        logger.warning("Failed to list warehouse tables for anomaly resolution: %s", exc)
        return []


def list_warehouse_source_tables() -> List[str]:
    return [
        table_name
        for table_name in list_warehouse_table_names()
        if is_anomaly_source_candidate(table_name)
    ]


def table_exists_in_warehouse(name: str, warehouse_tables: List[str] | None = None) -> bool:
    tables = warehouse_tables or list_warehouse_table_names()
    target = _canonical_table_key(name)
    return any(_canonical_table_key(table_name) == target for table_name in tables)


def resolve_to_warehouse_table(ref: str, warehouse_tables: List[str]) -> str | None:
    bare = _canonical_table_key(ref)
    if not bare or not warehouse_tables:
        return None

    searchable = [
        table_name
        for table_name in warehouse_tables
        if is_anomaly_source_candidate(table_name)
    ] or warehouse_tables

    for table_name in searchable:
        if _canonical_table_key(table_name) == bare:
            return table_name

    candidate_keys = {bare}
    if not bare.startswith("sttm_"):
        candidate_keys.add(f"sttm_{bare}")

    for candidate in candidate_keys:
        for table_name in searchable:
            if _canonical_table_key(table_name) == candidate:
                return table_name

    best_match = None
    for table_name in searchable:
        table_key = _canonical_table_key(table_name)
        if bare in table_key or table_key in bare:
            if best_match is None or len(table_key) < len(_canonical_table_key(best_match)):
                best_match = table_name
    return best_match


def collect_anomaly_source_tables(
    session_state: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> List[str]:
    """Collect uploaded source table refs suitable for anomaly analysis."""
    session_state = session_state or {}
    collected: List[str] = []

    def add(ref: Any) -> None:
        if ref is None:
            return
        value = str(ref).strip()
        if value:
            collected.append(value)

    for item in session_state.get("profiling_full_results") or []:
        if isinstance(item, dict):
            add(item.get("table_reference") or item.get("table_name"))

    for key in ("final_profiling_response_streaming", "final_profiling_response"):
        payload = session_state.get(key)
        if not isinstance(payload, dict):
            continue
        tool_response = payload.get("tool_response") or {}
        rows = tool_response.get("all_tables") or tool_response.get("result") or []
        if isinstance(rows, dict):
            rows = [rows]
        for item in rows:
            if isinstance(item, dict):
                add(item.get("table_reference") or item.get("table_name"))

    injected = session_state.get("anomaly_source_tables")
    if injected:
        collected.extend(normalize_table_refs(injected))

    if session_id:
        try:
            from utils.profiling_artifact_store import load_profiling_session_context

            context = load_profiling_session_context(session_id)
            for upload in context.get("successful_uploads") or []:
                if isinstance(upload, dict):
                    add(upload.get("table_name"))
                    access_info = upload.get("access_info") or {}
                    for entry in access_info.get("tables_created") or []:
                        table_name = (
                            entry.get("table_name") if isinstance(entry, dict) else entry
                        )
                        if table_name:
                            add(table_name)
                else:
                    add(getattr(upload, "table_name", None))
        except Exception:
            logger.exception(
                "Failed to load profiling session context while resolving anomaly tables"
            )

    deduped: List[str] = []
    seen: set[str] = set()
    for ref in collected:
        if not is_anomaly_source_candidate(ref):
            continue
        key = _canonical_table_key(ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)

    if not deduped:
        deduped = list_warehouse_source_tables()

    resolved: List[str] = []
    warehouse_tables = list_warehouse_table_names()
    seen_resolved: set[str] = set()
    for ref in deduped:
        matched = resolve_to_warehouse_table(ref, warehouse_tables)
        if matched and matched.lower() not in seen_resolved:
            resolved.append(matched)
            seen_resolved.add(matched.lower())

    return resolved or list_warehouse_source_tables()


def resolve_anomaly_table_references(
    tables: List[str],
    tool_context: ToolContext | None = None,
) -> List[str]:
    """Map LLM-provided refs to real warehouse source tables."""
    session_state: dict[str, Any] = {}
    session_id: str | None = None
    preferred_tables: List[str] | None = None

    if tool_context and hasattr(tool_context, "state") and tool_context.state:
        injected = tool_context.state.get("anomaly_source_tables")
        if injected:
            preferred_tables = normalize_table_refs(injected)

    if tool_context and hasattr(tool_context, "session") and tool_context.session:
        session_state = tool_context.session.state or {}
        session_id = getattr(tool_context.session, "id", None)
        if not preferred_tables:
            injected = session_state.get("anomaly_source_tables")
            if injected:
                preferred_tables = normalize_table_refs(injected)

    if preferred_tables:
        tables = preferred_tables

    source_tables = collect_anomaly_source_tables(session_state, session_id)
    warehouse_tables = list_warehouse_table_names()

    resolved: List[str] = []
    seen: set[str] = set()

    for ref in tables:
        if not is_anomaly_source_candidate(ref):
            logger.info("Skipping non-source table for anomaly analysis: %s", ref)
            continue

        matched = resolve_to_warehouse_table(ref, warehouse_tables)
        if matched and matched.lower() not in seen:
            resolved.append(matched)
            seen.add(matched.lower())
            continue

        logger.info("Could not resolve anomaly table reference in warehouse: %s", ref)

    if not resolved:
        logger.info(
            "Falling back to profiling/upload source tables for anomaly analysis: %s",
            source_tables,
        )
        for ref in source_tables:
            matched = resolve_to_warehouse_table(ref, warehouse_tables)
            if matched and matched.lower() not in seen:
                resolved.append(matched)
                seen.add(matched.lower())

    if not resolved:
        for ref in list_warehouse_source_tables():
            if ref.lower() not in seen:
                resolved.append(ref)
                seen.add(ref.lower())

    if resolved:
        logger.info("Resolved anomaly analysis tables: %s", resolved)
    else:
        logger.warning(
            "No resolvable source tables for anomaly analysis (input=%s, source=%s)",
            tables,
            source_tables,
        )
    return resolved
