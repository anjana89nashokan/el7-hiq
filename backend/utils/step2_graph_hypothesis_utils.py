from __future__ import annotations

import hashlib
from typing import Any

from agents.mapping_ingestion.models import DataModelGraph, GraphEdge, GraphNode


def _table_id_from_graph_node(node: GraphNode | None) -> str | None:
    if not node:
        return None
    if getattr(node, "table_name", None):
        return str(node.table_name)
    nid = str(getattr(node, "node_id", "") or "")
    if nid.startswith("TGT:") and "." in nid:
        return nid.split(".", 1)[1]
    if nid.startswith("TGT:"):
        return nid.split(":", 1)[1]
    return None


def _target_node_ids_for_table(graph: DataModelGraph | None, target_table_id: str) -> list[str]:
    if not graph:
        return []
    wanted = (target_table_id or "").strip().upper()
    if not wanted:
        return []

    out: list[str] = []
    for n in graph.nodes or []:
        if n.node_type != "TARGET_TABLE":
            continue
        table_name = (getattr(n, "table_name", None) or "").strip().upper()
        nid = (n.node_id or "").strip().upper()
        if table_name == wanted:
            out.append(n.node_id)
            continue
        if nid.endswith(f".{wanted}") or nid == f"TGT:{wanted}":
            out.append(n.node_id)
    return out


def _mk_path_id(*, target_table_id: str, edge_ids: list[str], node_ids: list[str]) -> str:
    raw = "|".join(
        [
            target_table_id,
            "->".join(node_ids),
            ",".join(edge_ids),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"PATH_{digest}"


def _table_family_token(table_id: str | None) -> str:
    t = str(table_id or "").strip().upper()
    if not t:
        return ""
    if "_" in t:
        return t.split("_", 1)[0]
    return t


def _path_relevance_score(*, target_table_id: str, option: dict[str, Any]) -> int:
    """
    Deterministic path relevance score used before truncation.

    Higher score is better.
    """
    score = 0
    hop_count = int(option.get("hop_count") or 99)
    lookup_table_id = str(option.get("lookup_table_id") or "")
    target_family = _table_family_token(target_table_id)
    lookup_family = _table_family_token(lookup_table_id)

    # Prefer fewer hops.
    score += max(0, 40 - (hop_count * 8))
    # Prefer same table family (e.g., PRV_* to PRV_*).
    if target_family and lookup_family and target_family == lookup_family:
        score += 30
    # Avoid trivial self lookups.
    if lookup_table_id.strip().upper() == str(target_table_id or "").strip().upper():
        score -= 20

    # Light preference for map/ref style lookup endpoints.
    lookup_upper = lookup_table_id.upper()
    if lookup_upper.endswith("_MAP") or "_MAP_" in lookup_upper:
        score += 6
    if lookup_upper.endswith("_CD") or "_CD_" in lookup_upper:
        score += 4

    return score


def _oriented_edge_steps_for_node(edge: GraphEdge, current_node_id: str) -> tuple[str, list[str], str, list[str], str] | None:
    """
    Return an oriented step tuple for traversal from current_node_id.

    Output tuple:
      (from_node_id, from_columns, to_node_id, to_columns, edge_id)
    """
    if not edge.edge_id:
        return None
    if not edge.from_node_id or not edge.to_node_id:
        return None
    if not edge.from_columns or not edge.to_columns:
        return None
    if len(edge.from_columns) != len(edge.to_columns):
        return None

    if edge.from_node_id == current_node_id:
        return (
            edge.from_node_id,
            list(edge.from_columns),
            edge.to_node_id,
            list(edge.to_columns),
            edge.edge_id,
        )
    if edge.to_node_id == current_node_id:
        return (
            edge.to_node_id,
            list(edge.to_columns),
            edge.from_node_id,
            list(edge.from_columns),
            edge.edge_id,
        )
    return None


def build_join_path_options_for_target(
    *,
    graph: DataModelGraph | None,
    target_table_id: str,
    max_hops: int = 3,
    max_options: int = 40,
) -> list[dict[str, Any]]:
    """
    Build bounded join path options (1..max_hops) around a target table.

    Notes:
      - Paths are built only from explicit-key edges.
      - Output is compact and option-constrained for AG2 LLM path selection.
      - Deterministic validation/apply is done by AG2 caller.
    """
    if not graph:
        return []

    max_hops = max(1, int(max_hops))
    max_options = max(1, int(max_options))
    # Explore beyond final option cap so we can rank before truncation.
    exploration_limit = max(200, max_options * 25)

    node_by_id = {n.node_id: n for n in (graph.nodes or [])}
    target_node_ids = _target_node_ids_for_table(graph, target_table_id)
    if not target_node_ids:
        return []

    edges = list(graph.edges or [])
    options: list[dict[str, Any]] = []
    seen_path_ids: set[str] = set()

    def _dfs(
        *,
        start_node_id: str,
        current_node_id: str,
        visited_node_ids: set[str],
        steps: list[dict[str, Any]],
        edge_ids: list[str],
    ) -> None:
        if len(options) >= exploration_limit:
            return

        if steps:
            endpoint = node_by_id.get(current_node_id)
            lookup_table_id = _table_id_from_graph_node(endpoint) or current_node_id
            path_node_ids = [start_node_id] + [str(s["to_node_id"]) for s in steps]
            path_id = _mk_path_id(
                target_table_id=target_table_id,
                edge_ids=edge_ids,
                node_ids=path_node_ids,
            )
            if path_id not in seen_path_ids:
                seen_path_ids.add(path_id)
                join_pairs = []
                summary_parts = []
                for s in steps:
                    left_table = _table_id_from_graph_node(node_by_id.get(str(s["from_node_id"]))) or str(s["from_node_id"])
                    right_table = _table_id_from_graph_node(node_by_id.get(str(s["to_node_id"]))) or str(s["to_node_id"])
                    left_cols = list(s.get("from_columns") or [])
                    right_cols = list(s.get("to_columns") or [])
                    summary_parts.append(
                        " AND ".join(
                            [f"{left_table}.{l} = {right_table}.{r}" for l, r in zip(left_cols, right_cols, strict=False)]
                        )
                    )
                    join_pairs.append(
                        {
                            "left_entity": {"entity_type": "TARGET_TABLE", "entity_id": left_table},
                            "left_columns": left_cols,
                            "right_entity": {"entity_type": "TARGET_TABLE", "entity_id": right_table},
                            "right_columns": right_cols,
                        }
                    )
                options.append(
                    {
                        "path_id": path_id,
                        "target_table_id": target_table_id,
                        "lookup_table_id": lookup_table_id,
                        "hop_count": len(steps),
                        "steps": [dict(s) for s in steps],
                        "join_pairs": join_pairs,
                        "key_complete": True,
                        "path_summary": " -> ".join([p for p in summary_parts if p]),
                    }
                )

        if len(steps) >= max_hops:
            return

        for e in edges:
            oriented = _oriented_edge_steps_for_node(e, current_node_id)
            if not oriented:
                continue
            from_node_id, from_cols, to_node_id, to_cols, edge_id = oriented
            if to_node_id in visited_node_ids:
                continue
            next_step = {
                "edge_id": edge_id,
                "from_node_id": from_node_id,
                "to_node_id": to_node_id,
                "from_columns": from_cols,
                "to_columns": to_cols,
            }
            _dfs(
                start_node_id=start_node_id,
                current_node_id=to_node_id,
                visited_node_ids=visited_node_ids | {to_node_id},
                steps=steps + [next_step],
                edge_ids=edge_ids + [edge_id],
            )

    for start in target_node_ids:
        _dfs(
            start_node_id=start,
            current_node_id=start,
            visited_node_ids={start},
            steps=[],
            edge_ids=[],
        )
        if len(options) >= exploration_limit:
            break

    for opt in options:
        opt["_relevance_score"] = _path_relevance_score(target_table_id=target_table_id, option=opt)
    options.sort(
        key=lambda x: (
            -int(x.get("_relevance_score") or 0),
            int(x.get("hop_count") or 99),
            str(x.get("path_id") or ""),
        )
    )
    for opt in options:
        opt.pop("_relevance_score", None)
    return options[:max_options]
