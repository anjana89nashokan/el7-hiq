from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

import pandas as pd

from agents.mapping_ingestion.models import DataModelGraph, GraphEdge, GraphMetadata, GraphNode
from config.settings import config


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


_COLUMNS_HEADER_ALIASES: Dict[str, str] = {
    "database": "database_name",
    "databasename": "database_name",
    "tablename": "table_name",
    "table": "table_name",
    "columnname": "column_name",
    "column": "column_name",
    "datatype": "data_type",
    "ispk": "is_pk",
    "isfk": "is_fk",
    "fkparentcolumnname": "fk_parent_column_name",
    "fkparenttablename": "fk_parent_table_name",
}

_INDEXES_HEADER_ALIASES: Dict[str, str] = {
    "database": "database_name",
    "databasename": "database_name",
    "tablename": "table_name",
    "table": "table_name",
    "akname": "ak_name",
    "akcolumnname": "ak_column_name",
    "akcolumnorder": "ak_column_order",
    "indexname": "ak_name",
    "indexcolumnname": "ak_column_name",
    "indexcolumnorder": "ak_column_order",
}

_REQUIRED_COLUMNS_FIELDS = {
    "database_name",
    "table_name",
    "column_name",
    "is_fk",
    "fk_parent_table_name",
    "fk_parent_column_name",
}
_REQUIRED_INDEX_FIELDS = {
    "database_name",
    "table_name",
    "ak_name",
    "ak_column_name",
    "ak_column_order",
}


def _as_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


def _to_bool(value: str) -> bool:
    return _as_text(value).upper() in {"TRUE", "T", "Y", "YES", "1"}


def _slugify_subject_area(subject_area: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", subject_area.strip().lower())
    return re.sub(r"_+", "_", slug).strip("_")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_extension(path: Path) -> None:
    if path.suffix.lower() not in {".csv", ".xlsx"}:
        raise ValueError(f"Unsupported file format: {path.name}. Only CSV and XLSX are supported.")


def _read_csv(path: Path) -> pd.DataFrame:
    # utf-8-sig handles BOM exports from spreadsheet tools.
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig")


def _select_best_sheet(
    workbook: pd.ExcelFile,
    aliases: Dict[str, str],
    required_fields: Iterable[str],
) -> Tuple[str, pd.DataFrame, set[str]]:
    required = set(required_fields)
    best_sheet = ""
    best_df: Optional[pd.DataFrame] = None
    best_hits: set[str] = set()

    for sheet_name in workbook.sheet_names:
        df = workbook.parse(sheet_name=sheet_name, dtype=str)
        normalized = {_normalize_header(str(c)): str(c) for c in df.columns}
        matched = {aliases[n] for n in normalized if n in aliases}
        score = len(matched & required)
        if best_df is None or score > len(best_hits):
            best_sheet = sheet_name
            best_df = df
            best_hits = matched

    if best_df is None:
        raise ValueError("No sheets found in workbook.")
    return best_sheet, best_df, best_hits


def _read_xlsx(path: Path, aliases: Dict[str, str], required_fields: Iterable[str]) -> Tuple[pd.DataFrame, str]:
    workbook = pd.ExcelFile(path)
    sheet_name, df, _ = _select_best_sheet(workbook, aliases, required_fields)
    return df, sheet_name


def _normalize_dataframe(df: pd.DataFrame, aliases: Dict[str, str]) -> Tuple[pd.DataFrame, set[str]]:
    rename: Dict[str, str] = {}
    for raw in df.columns:
        norm = _normalize_header(str(raw))
        if norm in aliases:
            rename[str(raw)] = aliases[norm]
    out = df.rename(columns=rename).copy()
    present = set(out.columns)
    return out, present


def _read_report_rows(
    path: Path,
    aliases: Dict[str, str],
    required_fields: Iterable[str],
) -> Tuple[List[Dict[str, str]], str]:
    _validate_extension(path)
    if path.suffix.lower() == ".csv":
        df = _read_csv(path)
        source_note = "csv"
    else:
        df, sheet_name = _read_xlsx(path, aliases=aliases, required_fields=required_fields)
        source_note = f"xlsx:{sheet_name}"

    normalized_df, present = _normalize_dataframe(df, aliases)
    required = set(required_fields)
    missing = sorted(required - present)
    if missing:
        raise ValueError(f"{path.name} is missing required fields: {', '.join(missing)}")

    rows: List[Dict[str, str]] = []
    for raw_row in normalized_df.to_dict(orient="records"):
        row: Dict[str, str] = {}
        for k, v in raw_row.items():
            row[str(k)] = _as_text(v)
        rows.append(row)

    if not rows:
        raise ValueError(f"{path.name} has no rows to parse.")

    return rows, source_note


@dataclass
class ErwinGraphBuildResult:
    graph: DataModelGraph
    artifact_path: Path
    stats: Dict[str, int]
    warnings_preview: List[Dict[str, object]]
    run_id: str
    subject_area: str


def _add_warning(
    warnings: List[Dict[str, object]],
    *,
    code: str,
    message: str,
    context: Optional[Dict[str, object]] = None,
) -> None:
    payload: Dict[str, object] = {"code": code, "message": message}
    if context:
        payload["context"] = context
    warnings.append(payload)


def _table_key(db: str, table: str) -> str:
    return f"{db}.{table}"


def _node_id_for_table(db: str, table: str) -> str:
    return f"TGT:{db}.{table}"


def _build_akmap_generators(
    index_rows: List[Dict[str, str]],
    warnings: List[Dict[str, object]],
) -> Dict[str, Dict[str, List[str]]]:
    grouped: Dict[Tuple[str, str, str], List[Tuple[Optional[int], int, str]]] = {}

    for row_idx, row in enumerate(index_rows):
        ak_name = _as_text(row.get("ak_name", ""))
        if not ak_name.upper().startswith("AKMAP_"):
            continue
        db = _as_text(row.get("database_name", ""))
        table = _as_text(row.get("table_name", ""))
        column = _as_text(row.get("ak_column_name", ""))
        if not (db and table and column):
            _add_warning(
                warnings,
                code="AKMAP_INCOMPLETE_ROW",
                message="Skipped AKMAP row with missing database/table/column.",
                context={"row_index": row_idx},
            )
            continue

        sk_column = ak_name[len("AKMAP_") :].strip()
        order_raw = _as_text(row.get("ak_column_order", ""))
        order_val = int(order_raw) if order_raw.isdigit() else None
        if order_val is None:
            _add_warning(
                warnings,
                code="AKMAP_MISSING_COLUMN_ORDER",
                message="AKMAP row missing AK column order; falling back to file order.",
                context={
                    "table": _table_key(db, table),
                    "ak_name": ak_name,
                    "column_name": column,
                },
            )

        grouped.setdefault((db, table, sk_column), []).append((order_val, row_idx, column))

    out: Dict[str, Dict[str, List[str]]] = {}
    for (db, table, sk_column), values in grouped.items():
        values.sort(key=lambda x: (x[0] is None, x[0] if x[0] is not None else 9999, x[1]))
        deduped_cols: List[str] = []
        seen: set[str] = set()
        for _, _, col in values:
            if col not in seen:
                seen.add(col)
                deduped_cols.append(col)
        out.setdefault(_table_key(db, table), {})[sk_column] = deduped_cols
    return out


def _resolve_sk_origin(
    *,
    start_db: str,
    start_table: str,
    start_sk_col: str,
    fk_map: Dict[Tuple[str, str, str], List[Tuple[str, str, str]]],
    sk_generators: Dict[str, Dict[str, List[str]]],
    warnings: List[Dict[str, object]],
) -> Optional[Dict[str, object]]:
    current_db = start_db
    current_table = start_table
    current_col = start_sk_col
    visited: set[Tuple[str, str, str]] = set()
    lineage: List[Dict[str, str]] = []

    for _ in range(64):
        state = (current_db, current_table, current_col)
        if state in visited:
            _add_warning(
                warnings,
                code="AKMAP_ORIGIN_CYCLE",
                message="Cycle detected while resolving SK generator origin.",
                context={
                    "start_table": _table_key(start_db, start_table),
                    "start_sk_column": start_sk_col,
                    "at_table": _table_key(current_db, current_table),
                    "at_column": current_col,
                },
            )
            return None
        visited.add(state)

        table_generators = sk_generators.get(_table_key(current_db, current_table), {})
        if current_col in table_generators:
            return {
                "origin_table": _table_key(current_db, current_table),
                "origin_sk_column": current_col,
                "generator_columns": list(table_generators[current_col]),
                "lineage_path": lineage,
            }

        parents = fk_map.get((current_db, current_table, current_col), [])
        unique_parents = list(dict.fromkeys(parents))
        if not unique_parents:
            _add_warning(
                warnings,
                code="AKMAP_ORIGIN_NOT_FOUND",
                message="Could not find AKMAP origin by FK-chain traversal.",
                context={
                    "start_table": _table_key(start_db, start_table),
                    "start_sk_column": start_sk_col,
                },
            )
            return None
        if len(unique_parents) > 1:
            _add_warning(
                warnings,
                code="AKMAP_ORIGIN_AMBIGUOUS",
                message="Multiple FK parents found while resolving AKMAP origin.",
                context={
                    "start_table": _table_key(start_db, start_table),
                    "start_sk_column": start_sk_col,
                    "parent_candidates": [
                        {"table": _table_key(pdb, ptbl), "column": pcol}
                        for pdb, ptbl, pcol in unique_parents
                    ],
                },
            )
            return None

        parent_db, parent_table, parent_col = unique_parents[0]
        lineage.append(
            {
                "from_table": _table_key(current_db, current_table),
                "from_column": current_col,
                "to_table": _table_key(parent_db, parent_table),
                "to_column": parent_col,
            }
        )
        current_db, current_table, current_col = parent_db, parent_table, parent_col

    _add_warning(
        warnings,
        code="AKMAP_ORIGIN_DEPTH_EXCEEDED",
        message="Stopped AKMAP origin traversal after maximum depth.",
        context={
            "start_table": _table_key(start_db, start_table),
            "start_sk_column": start_sk_col,
        },
    )
    return None


def build_erwin_subject_area_graph(
    *,
    subject_area: str,
    tables_and_columns_path: Path,
    tables_and_indexes_path: Path,
    run_id: Optional[str] = None,
    output_root: Optional[Path] = None,
) -> ErwinGraphBuildResult:
    if not subject_area or not subject_area.strip():
        raise ValueError("subject_area is required.")

    resolved_run_id = (run_id or "").strip() or str(uuid4())

    column_rows, columns_source_note = _read_report_rows(
        tables_and_columns_path,
        aliases=_COLUMNS_HEADER_ALIASES,
        required_fields=_REQUIRED_COLUMNS_FIELDS,
    )
    index_rows, indexes_source_note = _read_report_rows(
        tables_and_indexes_path,
        aliases=_INDEXES_HEADER_ALIASES,
        required_fields=_REQUIRED_INDEX_FIELDS,
    )

    warnings: List[Dict[str, object]] = []
    missing_refs: List[Dict[str, object]] = []
    nodes_by_key: Dict[Tuple[str, str], GraphNode] = {}
    system_link_columns = {c.upper() for c in (getattr(config, "ERWIN_SYSTEM_LINK_COLUMNS", set()) or set())}
    table_columns_map: Dict[Tuple[str, str], List[str]] = {}

    for row in column_rows:
        db = _as_text(row.get("database_name", ""))
        table = _as_text(row.get("table_name", ""))
        column = _as_text(row.get("column_name", ""))
        if not db or not table:
            continue
        key = (db, table)
        if column:
            table_columns_map.setdefault(key, [])
            if column not in table_columns_map[key]:
                table_columns_map[key].append(column)
        if key not in nodes_by_key:
            nodes_by_key[key] = GraphNode(
                node_id=_node_id_for_table(db, table),
                label=_table_key(db, table),
                node_type="TARGET_TABLE",
                database_name=db,
                table_name=table,
                columns=list(table_columns_map.get(key, [])),
                is_stub=False,
            )
        else:
            # Keep node columns synchronized with observed table columns.
            nodes_by_key[key].columns = list(table_columns_map.get(key, []))

    fk_groups: Dict[Tuple[str, str, str, str], List[Tuple[str, str]]] = {}
    fk_map: Dict[Tuple[str, str, str], List[Tuple[str, str, str]]] = {}
    incomplete_fk_count = 0
    missing_parent_tables: set[Tuple[str, str]] = set()

    for idx, row in enumerate(column_rows):
        if not _to_bool(row.get("is_fk", "")):
            continue
        child_db = _as_text(row.get("database_name", ""))
        child_table = _as_text(row.get("table_name", ""))
        child_col = _as_text(row.get("column_name", ""))
        parent_table = _as_text(row.get("fk_parent_table_name", ""))
        parent_col = _as_text(row.get("fk_parent_column_name", ""))
        parent_db = child_db  # locked by design decision

        if not (child_db and child_table and child_col and parent_table and parent_col):
            incomplete_fk_count += 1
            _add_warning(
                warnings,
                code="INCOMPLETE_FK_ROW",
                message="Skipped FK row with missing join key fields.",
                context={"row_index": idx},
            )
            continue

        if child_col.upper() in system_link_columns:
            _add_warning(
                warnings,
                code="SYSTEM_COLUMN_EXCEPTION",
                message="Skipped FK edge for system-level linkage column by policy.",
                context={
                    "child_table": _table_key(child_db, child_table),
                    "child_column": child_col,
                    "parent_table": _table_key(parent_db, parent_table),
                    "parent_column": parent_col,
                },
            )
            continue

        parent_key = (parent_db, parent_table)
        if parent_key not in nodes_by_key:
            nodes_by_key[parent_key] = GraphNode(
                node_id=_node_id_for_table(parent_db, parent_table),
                label=_table_key(parent_db, parent_table),
                node_type="REF_TABLE",
                database_name=parent_db,
                table_name=parent_table,
                columns=[],
                is_stub=True,
            )
            missing_parent_tables.add(parent_key)
            missing_info = {
                "code": "MISSING_PARENT_TABLE",
                "child_table": _table_key(child_db, child_table),
                "parent_table": _table_key(parent_db, parent_table),
            }
            missing_refs.append(missing_info)
            _add_warning(
                warnings,
                code="MISSING_PARENT_TABLE",
                message="FK parent table is not present in this subject-area extract; stub node created.",
                context=missing_info,
            )

        group_key = (child_db, child_table, parent_db, parent_table)
        pair = (child_col, parent_col)
        if pair not in fk_groups.setdefault(group_key, []):
            fk_groups[group_key].append(pair)

        fk_map.setdefault((child_db, child_table, child_col), [])
        parent_triplet = (parent_db, parent_table, parent_col)
        if parent_triplet not in fk_map[(child_db, child_table, child_col)]:
            fk_map[(child_db, child_table, child_col)].append(parent_triplet)

    edges: List[GraphEdge] = []
    for (child_db, child_table, parent_db, parent_table), pairs in fk_groups.items():
        if not pairs:
            continue
        pair_serialized = ";".join(f"{l}={r}" for l, r in pairs)
        pair_hash = hashlib.sha1(pair_serialized.encode("utf-8")).hexdigest()[:12]
        edge_id = (
            f"FK:{child_db}.{child_table}->{parent_db}.{parent_table}:{pair_hash}"
        )
        edges.append(
            GraphEdge(
                edge_id=edge_id,
                from_node_id=_node_id_for_table(child_db, child_table),
                to_node_id=_node_id_for_table(parent_db, parent_table),
                relationship_type="FK",
                from_columns=[l for l, _ in pairs],
                to_columns=[r for _, r in pairs],
                source="ERWIN",
                comment=None,
            )
        )

    sk_generators = _build_akmap_generators(index_rows=index_rows, warnings=warnings)
    sk_generator_origins: Dict[str, Dict[str, Dict[str, object]]] = {}

    fk_sk_keys: set[Tuple[str, str, str]] = set()
    for row in column_rows:
        if not _to_bool(row.get("is_fk", "")):
            continue
        col = _as_text(row.get("column_name", ""))
        if not col.upper().endswith("_SK"):
            continue
        db = _as_text(row.get("database_name", ""))
        table = _as_text(row.get("table_name", ""))
        if db and table and col:
            fk_sk_keys.add((db, table, col))

    for db, table, sk_col in sorted(fk_sk_keys):
        if sk_col.upper() in system_link_columns:
            _add_warning(
                warnings,
                code="SYSTEM_COLUMN_EXCEPTION",
                message="Skipped SK origin resolution for system-level column by policy.",
                context={"table": _table_key(db, table), "sk_column": sk_col},
            )
            continue
        table_key = _table_key(db, table)
        if sk_col in sk_generators.get(table_key, {}):
            continue
        resolved = _resolve_sk_origin(
            start_db=db,
            start_table=table,
            start_sk_col=sk_col,
            fk_map=fk_map,
            sk_generators=sk_generators,
            warnings=warnings,
        )
        if resolved:
            sk_generator_origins.setdefault(table_key, {})[sk_col] = resolved

    now = datetime.now(timezone.utc)
    subject_slug = _slugify_subject_area(subject_area)
    graph_metadata = GraphMetadata(
        graph_mode="erwin_subject_area_extract",
        has_erwin=True,
        interface_code=subject_area,
        created_at=now,
        run_id=resolved_run_id,
        subject_area=subject_area,
        source_files=[
            {
                "filename": tables_and_columns_path.name,
                "sha256": _sha256_file(tables_and_columns_path),
                "source_note": columns_source_note,
            },
            {
                "filename": tables_and_indexes_path.name,
                "sha256": _sha256_file(tables_and_indexes_path),
                "source_note": indexes_source_note,
            },
        ],
        limitations=[
            "subject_area_extract_may_be_partial",
            "missing_parent_tables_are_stubbed_when_referenced_by_fk",
            "joins_are_created_only_when_fk_keys_are_explicit",
        ],
    )

    graph = DataModelGraph(
        nodes=list(nodes_by_key.values()),
        edges=edges,
        metadata=graph_metadata,
        sk_generators=sk_generators,
        sk_generator_origins=sk_generator_origins,
        warnings=warnings,
        missing_refs=missing_refs,
    )

    root = output_root or (Path("server") / "data" / "graphs")
    folder = root / subject_slug
    folder.mkdir(parents=True, exist_ok=True)
    artifact_name = f"data_model_graph_{subject_slug}_v1_{now.strftime('%Y%m%d%H%M%S')}.json"
    artifact_path = folder / artifact_name

    with artifact_path.open("w", encoding="utf-8") as f:
        json.dump(json.loads(graph.model_dump_json()), f, indent=2)

    stats = {
        "tables_count": len([n for n in graph.nodes if n.node_type == "TARGET_TABLE" and not n.is_stub]),
        "fk_edges_count": len(graph.edges),
        "akmap_sk_count": sum(len(v) for v in graph.sk_generators.values()),
        "derived_origin_sk_count": sum(len(v) for v in graph.sk_generator_origins.values()),
        "incomplete_fk_count": incomplete_fk_count,
        "missing_parent_tables_count": len(missing_parent_tables),
    }

    return ErwinGraphBuildResult(
        graph=graph,
        artifact_path=artifact_path,
        stats=stats,
        warnings_preview=warnings[:50],
        run_id=resolved_run_id,
        subject_area=subject_area,
    )
