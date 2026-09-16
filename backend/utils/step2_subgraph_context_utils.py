from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Any


def _table_family_token(table_id: str | None) -> str:
    value = str(table_id or "").strip().upper()
    if not value:
        return ""
    if "." in value:
        value = value.split(".", 1)[1]
    if "_" in value:
        return value.split("_", 1)[0]
    return value


def _node_table_identifiers(node) -> set[str]:
    out: set[str] = set()
    node_id = str(getattr(node, "node_id", "") or "").strip()
    table_name = str(getattr(node, "table_name", "") or "").strip()
    database_name = str(getattr(node, "database_name", "") or "").strip()
    if table_name:
        out.add(table_name)
        out.add(table_name.upper())
    if table_name and database_name:
        out.add(f"{database_name}.{table_name}")
        out.add(f"{database_name}.{table_name}".upper())
    if node_id.startswith("TGT:"):
        raw = node_id.split(":", 1)[1]
        if raw:
            out.add(raw)
            out.add(raw.upper())
            if "." in raw:
                tail = raw.split(".", 1)[1]
                out.add(tail)
                out.add(tail.upper())
    return out


def _find_target_node_ids(graph, target_table_id: str) -> list[str]:
    wanted = str(target_table_id or "").strip()
    if not graph or not wanted:
        return []
    wanted_upper = wanted.upper()
    out: list[str] = []
    for node in graph.nodes or []:
        if getattr(node, "node_type", None) not in {"TARGET_TABLE", "REF_TABLE"}:
            continue
        ids = _node_table_identifiers(node)
        if wanted in ids or wanted_upper in ids:
            out.append(str(node.node_id))
    return sorted(set(out))


def _node_table_key_variants(node) -> list[str]:
    table_name = str(getattr(node, "table_name", "") or "").strip()
    database_name = str(getattr(node, "database_name", "") or "").strip()
    keys: list[str] = []
    if database_name and table_name:
        keys.append(f"{database_name}.{table_name}")
    if table_name:
        keys.append(table_name)
    return keys


def build_connected_component_subgraph(
    *,
    graph,
    target_table_id: str,
    max_nodes: int,
    max_edges: int,
    max_columns_per_node: int,
) -> dict[str, Any]:
    """
    Build connected-component subgraph context for a target table.

    Includes:
      - target node ids
      - nodes with columns + derived pk/fk/sk tags
      - edges with explicit keys
      - optional sk_generators subset for included tables
    """
    if not graph:
        return {
            "target_table_id": target_table_id,
            "target_node_ids": [],
            "nodes": [],
            "edges": [],
            "sk_generators_subset": {},
        }

    max_nodes = max(1, int(max_nodes))
    max_edges = max(1, int(max_edges))
    max_columns_per_node = max(1, int(max_columns_per_node))

    start_node_ids = _find_target_node_ids(graph, target_table_id)
    if not start_node_ids:
        return {
            "target_table_id": target_table_id,
            "target_node_ids": [],
            "nodes": [],
            "edges": [],
            "sk_generators_subset": {},
        }

    node_by_id = {str(n.node_id): n for n in (graph.nodes or []) if getattr(n, "node_id", None)}
    neighbors: dict[str, set[str]] = defaultdict(set)

    for edge in graph.edges or []:
        from_id = str(getattr(edge, "from_node_id", "") or "")
        to_id = str(getattr(edge, "to_node_id", "") or "")
        if not from_id or not to_id:
            continue
        neighbors[from_id].add(to_id)
        neighbors[to_id].add(from_id)

    distances: dict[str, int] = {}
    queue = deque()
    for start in start_node_ids:
        distances[start] = 0
        queue.append(start)

    while queue:
        current = queue.popleft()
        next_distance = distances[current] + 1
        for nb in neighbors.get(current, set()):
            if nb in distances:
                continue
            distances[nb] = next_distance
            queue.append(nb)

    target_family = _table_family_token(target_table_id)

    def _rank_node(node_id: str) -> tuple[int, int, str]:
        node = node_by_id.get(node_id)
        table_id = str(getattr(node, "table_name", "") or "")
        same_family = 1 if _table_family_token(table_id) == target_family and target_family else 0
        return (int(distances.get(node_id, 9999)), -same_family, node_id)

    all_nodes = sorted(distances.keys(), key=_rank_node)
    included_node_ids = set(all_nodes[:max_nodes])

    included_edges_raw = []
    for edge in graph.edges or []:
        from_id = str(getattr(edge, "from_node_id", "") or "")
        to_id = str(getattr(edge, "to_node_id", "") or "")
        if from_id in included_node_ids and to_id in included_node_ids:
            included_edges_raw.append(edge)

    def _rank_edge(edge) -> tuple[int, int, str]:
        from_id = str(getattr(edge, "from_node_id", "") or "")
        to_id = str(getattr(edge, "to_node_id", "") or "")
        hop_rank = max(int(distances.get(from_id, 9999)), int(distances.get(to_id, 9999)))
        from_node = node_by_id.get(from_id)
        to_node = node_by_id.get(to_id)
        same_family = 0
        if target_family:
            if _table_family_token(getattr(from_node, "table_name", "")) == target_family:
                same_family += 1
            if _table_family_token(getattr(to_node, "table_name", "")) == target_family:
                same_family += 1
        return (hop_rank, -same_family, str(getattr(edge, "edge_id", "") or ""))

    included_edges = sorted(included_edges_raw, key=_rank_edge)[:max_edges]

    fk_cols_by_node: dict[str, set[str]] = defaultdict(set)
    pk_cols_by_node: dict[str, set[str]] = defaultdict(set)
    for edge in included_edges:
        from_id = str(getattr(edge, "from_node_id", "") or "")
        to_id = str(getattr(edge, "to_node_id", "") or "")
        for col in list(getattr(edge, "from_columns", None) or []):
            fk_cols_by_node[from_id].add(str(col))
        for col in list(getattr(edge, "to_columns", None) or []):
            pk_cols_by_node[to_id].add(str(col))

    sk_generators = getattr(graph, "sk_generators", None) or {}
    sk_generators_subset: dict[str, dict[str, list[str]]] = {}

    nodes_payload = []
    for node_id in sorted(included_node_ids, key=_rank_node):
        node = node_by_id.get(node_id)
        if not node:
            continue

        table_keys = _node_table_key_variants(node)
        table_sk_map: dict[str, list[str]] = {}
        for key in table_keys:
            raw = sk_generators.get(key) or {}
            for sk_col, generators in raw.items():
                table_sk_map[str(sk_col)] = [str(x) for x in (generators or [])]
        if table_sk_map:
            table_id = str(getattr(node, "table_name", "") or "")
            lookup_key = table_id or str(node_id)
            sk_generators_subset[lookup_key] = table_sk_map

        cols_payload = []
        columns = [str(c) for c in (getattr(node, "columns", None) or [])][:max_columns_per_node]
        for col in columns:
            is_sk = col.upper().endswith("_SK") or col in table_sk_map
            cols_payload.append(
                {
                    "name": col,
                    "is_fk": col in fk_cols_by_node.get(node_id, set()),
                    "is_pk_like": col in pk_cols_by_node.get(node_id, set()),
                    "is_sk": is_sk,
                }
            )

        nodes_payload.append(
            {
                "node_id": str(node_id),
                "table_id": str(getattr(node, "table_name", "") or ""),
                "database_name": str(getattr(node, "database_name", "") or ""),
                "node_type": str(getattr(node, "node_type", "") or ""),
                "is_stub": bool(getattr(node, "is_stub", False)),
                "columns": cols_payload,
            }
        )

    edges_payload = []
    for edge in included_edges:
        edges_payload.append(
            {
                "edge_id": str(getattr(edge, "edge_id", "") or ""),
                "from_node_id": str(getattr(edge, "from_node_id", "") or ""),
                "to_node_id": str(getattr(edge, "to_node_id", "") or ""),
                "relationship_type": str(getattr(edge, "relationship_type", "") or ""),
                "from_columns": [str(c) for c in (getattr(edge, "from_columns", None) or [])],
                "to_columns": [str(c) for c in (getattr(edge, "to_columns", None) or [])],
            }
        )

    return {
        "target_table_id": target_table_id,
        "target_node_ids": [nid for nid in start_node_ids if nid in included_node_ids],
        "nodes": nodes_payload,
        "edges": edges_payload,
        "sk_generators_subset": sk_generators_subset,
    }


def build_connected_component_subgraph_json(
    *,
    graph,
    target_table_id: str,
    max_nodes: int,
    max_edges: int,
    max_columns_per_node: int,
) -> str:
    payload = build_connected_component_subgraph(
        graph=graph,
        target_table_id=target_table_id,
        max_nodes=max_nodes,
        max_edges=max_edges,
        max_columns_per_node=max_columns_per_node,
    )
    return json.dumps(payload, ensure_ascii=False)


__all__ = [
    "build_connected_component_subgraph",
    "build_connected_component_subgraph_json",
]
