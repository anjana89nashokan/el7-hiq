from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Tuple

from agents.mapping_ingestion.models import DataModelGraph, GraphEdge, GraphMetadata, GraphNode


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _node_db_table(node: GraphNode) -> tuple[str, str]:
    db = _norm(getattr(node, "database_name", None))
    table = _norm(getattr(node, "table_name", None))
    return db, table


def _node_candidate_key(node: GraphNode) -> tuple[str, str]:
    db, table = _node_db_table(node)
    if db and table:
      return db, table
    return "", _norm(getattr(node, "node_id", ""))


def _node_columns_signature(node: GraphNode) -> tuple[str, ...]:
    return tuple(sorted({_norm(c) for c in (node.columns or []) if _norm(c)}))


def _node_index(graph: DataModelGraph) -> Dict[str, GraphNode]:
    return {node.node_id: node for node in (graph.nodes or [])}


def _neighbor_signature(node: GraphNode, graph: DataModelGraph) -> tuple[tuple[str, ...], ...]:
    idx = _node_index(graph)
    current_id = node.node_id
    parts: list[tuple[str, ...]] = []

    for edge in graph.edges or []:
        if edge.from_node_id == current_id:
            other = idx.get(edge.to_node_id)
            other_db, other_table = _node_db_table(other) if other else ("", _norm(edge.to_node_id))
            parts.append(
                (
                    "OUT",
                    _norm(edge.relationship_type),
                    other_db,
                    other_table,
                    ",".join(sorted(_norm(c) for c in (edge.from_columns or []) if _norm(c))),
                    ",".join(sorted(_norm(c) for c in (edge.to_columns or []) if _norm(c))),
                )
            )
        elif edge.to_node_id == current_id:
            other = idx.get(edge.from_node_id)
            other_db, other_table = _node_db_table(other) if other else ("", _norm(edge.from_node_id))
            parts.append(
                (
                    "IN",
                    _norm(edge.relationship_type),
                    other_db,
                    other_table,
                    ",".join(sorted(_norm(c) for c in (edge.to_columns or []) if _norm(c))),
                    ",".join(sorted(_norm(c) for c in (edge.from_columns or []) if _norm(c))),
                )
            )
        else:
            continue

    return tuple(sorted(parts))


def _structural_signature(node: GraphNode, graph: DataModelGraph) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    return _node_columns_signature(node), _neighbor_signature(node, graph)


def _choose_node_type(nodes: list[GraphNode]) -> str:
    types = {n.node_type for n in nodes}
    if "TARGET_TABLE" in types:
        return "TARGET_TABLE"
    if "REF_TABLE" in types:
        return "REF_TABLE"
    return next(iter(types), "TARGET_TABLE")


def _canonical_node_id(node_type: str, db: str, table: str, variant_index: int) -> str:
    prefix = "TGT" if node_type == "TARGET_TABLE" else "REF"
    base = f"{prefix}:{db}.{table}" if db and table else f"{prefix}:{table or 'UNKNOWN'}"
    if variant_index == 0:
        return base
    return f"{base}#v{variant_index + 1}"


def _edge_signature(edge: GraphEdge) -> tuple[str, ...]:
    return (
        edge.from_node_id,
        edge.to_node_id,
        _norm(edge.relationship_type),
        ",".join(sorted(_norm(c) for c in (edge.from_columns or []) if _norm(c))),
        ",".join(sorted(_norm(c) for c in (edge.to_columns or []) if _norm(c))),
    )


def merge_subject_area_graphs(
    *,
    run_id: str,
    subject_areas: list[str],
    graphs_with_sources: list[tuple[str, DataModelGraph, str]],
) -> DataModelGraph:
    if not graphs_with_sources:
        raise ValueError("At least one subject-area graph is required for merge.")

    if len(graphs_with_sources) == 1:
        _subject_area, graph, graph_uri = graphs_with_sources[0]
        graph.metadata.selected_subject_areas = list(subject_areas)
        graph.metadata.source_graph_artifact_paths = [graph_uri]
        graph.metadata.merge_warnings = []
        for node in graph.nodes or []:
            if not node.provenance_subject_areas:
                node.provenance_subject_areas = [subject_areas[0]]
        return graph

    grouped: dict[tuple[str, str], dict[str, list[tuple[str, GraphNode, DataModelGraph]]]] = defaultdict(lambda: defaultdict(list))
    original_nodes: list[tuple[str, GraphNode, DataModelGraph]] = []
    source_graph_paths: list[str] = []
    aggregated_source_files: list[dict] = []
    aggregated_limitations: list[str] = []
    aggregated_warnings: list[dict] = []
    aggregated_missing_refs: list[dict] = []

    for subject_area, graph, graph_uri in graphs_with_sources:
        source_graph_paths.append(graph_uri)
        for source_file in graph.metadata.source_files or []:
            enriched = dict(source_file)
            enriched.setdefault("subject_area", subject_area)
            aggregated_source_files.append(enriched)
        aggregated_limitations.extend(graph.metadata.limitations or [])
        for warning in graph.warnings or []:
            aggregated_warnings.append({"subject_area": subject_area, **dict(warning)})
        for missing in graph.missing_refs or []:
            aggregated_missing_refs.append({"subject_area": subject_area, **dict(missing)})
        for node in graph.nodes or []:
            original_nodes.append((subject_area, node, graph))
            key = _node_candidate_key(node)
            grouped[key][json.dumps(_structural_signature(node, graph))].append((subject_area, node, graph))

    merged_nodes: list[GraphNode] = []
    node_id_map: dict[tuple[str, str], str] = {}
    merge_warnings: list[dict] = []

    for key in sorted(grouped.keys()):
        db, table = key
        variants = list(grouped[key].items())
        variants.sort(key=lambda item: (-len(item[1]), item[0]))
        if len(variants) > 1 and db and table:
            merge_warnings.append(
                {
                    "code": "STRUCTURAL_CONFLICT",
                    "message": "Kept multiple node variants because db/table matched but structure differed.",
                    "context": {
                        "database_name": db,
                        "table_name": table,
                        "variant_count": len(variants),
                        "subject_areas": sorted({subject_area for members in grouped[key].values() for subject_area, _node, _graph in members}),
                    },
                }
            )

        for variant_index, (_signature, members) in enumerate(variants):
            subject_area_list = sorted({subject_area for subject_area, _node, _graph in members})
            nodes = [node for _subject_area, node, _graph in members]
            node_type = _choose_node_type(nodes)
            base_node = next((n for n in nodes if not n.is_stub), nodes[0])
            canonical_node_id = _canonical_node_id(node_type, db, table, variant_index)
            label = base_node.label or f"{db}.{table}"
            if variant_index > 0 and subject_area_list:
                label = f"{label} [{', '.join(subject_area_list)}]"

            merged_node = GraphNode(
                node_id=canonical_node_id,
                label=label,
                node_type=node_type,
                database_name=base_node.database_name,
                table_name=base_node.table_name,
                columns=sorted({col for node in nodes for col in (node.columns or [])}),
                is_stub=all(node.is_stub for node in nodes),
                provenance_subject_areas=subject_area_list,
            )
            merged_nodes.append(merged_node)

            for subject_area, node, _graph in members:
                node_id_map[(subject_area, node.node_id)] = canonical_node_id

    merged_edges: list[GraphEdge] = []
    seen_edge_sigs: set[tuple[str, ...]] = set()

    for subject_area, graph, _graph_uri in graphs_with_sources:
        for edge in graph.edges or []:
            mapped_from = node_id_map.get((subject_area, edge.from_node_id))
            mapped_to = node_id_map.get((subject_area, edge.to_node_id))
            if not mapped_from or not mapped_to:
                continue
            merged_edge = GraphEdge(
                edge_id=edge.edge_id,
                from_node_id=mapped_from,
                to_node_id=mapped_to,
                relationship_type=edge.relationship_type,
                from_columns=list(edge.from_columns or []),
                to_columns=list(edge.to_columns or []),
                cardinality=edge.cardinality,
                source=edge.source,
                comment=edge.comment,
            )
            sig = _edge_signature(merged_edge)
            if sig in seen_edge_sigs:
                continue
            seen_edge_sigs.add(sig)
            merged_edges.append(merged_edge)

    metadata = GraphMetadata(
        graph_mode="erwin_subject_area_extract",
        has_erwin=True,
        interface_code="MULTI_SUBJECT_AREA",
        created_at=datetime.utcnow(),
        run_id=run_id,
        subject_area=subject_areas[0] if len(subject_areas) == 1 else None,
        selected_subject_areas=list(subject_areas),
        source_graph_artifact_paths=_dedupe_preserve_order(source_graph_paths),
        merge_warnings=merge_warnings,
        source_files=[
            dict(item)
            for item in {
                json.dumps(item, sort_keys=True): item
                for item in aggregated_source_files
            }.values()
        ],
        limitations=_dedupe_preserve_order([*aggregated_limitations, "merged_subject_area_graph"]),
    )

    return DataModelGraph(
        nodes=merged_nodes,
        edges=merged_edges,
        metadata=metadata,
        warnings=aggregated_warnings,
        missing_refs=aggregated_missing_refs,
    )
