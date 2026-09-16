"""
Step 2 (AG2) Join + Filter utilities.

Scope:
  - Deterministic helpers only (no LLM calls in this module).
  - AG2 (JoinAndFilterAgent) uses these utilities to:
      1) Resolve joins with strict precedence:
           (a) data_model_graph edges
           (b) explicit lookup rules from Step 1 mapping_context.overrides.lookup_rules
           (c) (optional) evidence hints (handled in AG2 agent, not here)
           (d) else JOIN_UNKNOWN
      2) Build mapping/table common filters (Step 1 global filters) and leave
         column/rule-instance filters on MappingRow.row_filter_text.

Important guardrails:
  - Do not invent entities/columns.
  - Any join keys emitted must be validated against Step 1 schemas by the caller.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from agents.mapping_generation.models import (
    EvidenceRef,
    EvidenceSource,
    JoinCondition,
    JoinKeyPair,
    MappingRow,
    OpenIssue,
    IssueSeverity,
    IssueType,
    RuleType,
)
from agents.mapping_ingestion.models import DataModelGraph, GraphEdge
from config.settings import config
from utils.step2_graph_hypothesis_utils import build_join_path_options_for_target
from utils.mapping_logic_utils import normalize_target_key

_TRANSLATION_TARGET_TOKENS = (
    "CODE",
    "INDICATOR",
    "FLAG",
    "STATUS",
    "REASON",
    "DESIGNATION",
    "TYPE",
    "DOMAIN",
    "TRANSLAT",
)

_DESCRIPTIVE_SOURCE_TOKENS = ("NAME", "DESC", "DESCRIPTION", "TYPE", "ROLE", "TEXT", "LABEL")
_CODELIKE_SOURCE_TOKENS = ("_CD", "_ID", "_SK", "CODE", "KEY")
_CROSSWALK_TABLE_TOKENS = ("XWALK", "CROSSWALK", "MAP", "REF", "LOOKUP")
_CROSSWALK_COLUMN_PREFIXES = ("SRC_", "SOURCE_", "REF_", "XWALK_")
_CROSSWALK_COLUMN_HINTS = ("DESC", "DESCRIPTION", "NAME", "TYPE", "SRC_REF", "SOURCE_REF")


def lookup_join_unknown_issue_id(*, target_table_id: str, target_column_name: str) -> str:
    """
    Canonical issue_id used when a row requires a join but join keys/path are unknown.

    Important:
      - This must stay consistent with Step2 AG1 deterministic seeding in
        `server/utils/mapping_logic_utils.py` so AG2 can clear stale join issues
        when a row is no longer a LOOKUP row.
    """
    return f"ISSUE_JOIN_{target_table_id}_{target_column_name}"


def lookup_path_required_issue_id(*, target_table_id: str, target_column_name: str) -> str:
    """
    Canonical issue_id used when LOOKUP is selected but no path_id was chosen in AG1.
    """
    return f"ISSUE_LOOKUP_PATH_REQUIRED_{target_table_id}_{target_column_name}"


def lookup_path_fitness_issue_id(*, target_table_id: str, target_column_name: str) -> str:
    """
    Canonical issue_id used when a selected lookup path is structurally valid but semantically weak.
    """
    return f"ISSUE_LOOKUP_PATH_FIT_{target_table_id}_{target_column_name}"


def clear_stale_lookup_join_issue_if_not_lookup(row: MappingRow) -> None:
    """
    Remove LOOKUP join-unknown issue ids from rows that are no longer LOOKUP.

    Why this exists:
      - AG1 may seed a JOIN_UNKNOWN issue when it initially infers LOOKUP.
      - Later, AG1 tie-break/self-check can flip the row to DIRECT (or other).
      - If we don't clear the seeded issue, Step2 output becomes inconsistent:
        DIRECT rows would carry JOIN_UNKNOWN issues.
    """
    if row.rule_type == RuleType.LOOKUP:
        return

    join_issue_id = lookup_join_unknown_issue_id(
        target_table_id=row.target_table.entity_id,
        target_column_name=row.target_column_name,
    )
    path_required_id = lookup_path_required_issue_id(
        target_table_id=row.target_table.entity_id,
        target_column_name=row.target_column_name,
    )
    path_fit_id = lookup_path_fitness_issue_id(
        target_table_id=row.target_table.entity_id,
        target_column_name=row.target_column_name,
    )
    if join_issue_id not in row.open_issue_ids and path_required_id not in row.open_issue_ids and path_fit_id not in row.open_issue_ids:
        return
    row.open_issue_ids = [x for x in row.open_issue_ids if x not in {join_issue_id, path_required_id, path_fit_id}]


def clear_stale_direct_source_join_placeholders(row: MappingRow) -> None:
    """
    Remove legacy "cross-source DIRECT join" placeholders from a MappingRow.

    Context:
      - Earlier AG2 iterations attempted to attach JOIN_UNKNOWN placeholders to DIRECT rows when a
        target table had DIRECT mappings from multiple source files.
      - This produced false positives: a DIRECT row can be fully populated from a single source
        file without needing to document how other source files relate.

    Current policy:
      - AG2 only creates join placeholders for rows that explicitly require joins (LOOKUP / multi-entity).
      - Therefore, any leftover `ISSUE_SRCJOIN_*` or matching JOIN_UNKNOWN join_text on DIRECT rows
        should be cleared to keep the output consistent.
    """
    if row.rule_type != RuleType.DIRECT:
        return

    # Remove legacy per-row issue ids.
    if row.open_issue_ids:
        prefix = f"ISSUE_SRCJOIN_{row.target_table.entity_id}_{row.target_column_name}"
        if prefix in row.open_issue_ids:
            row.open_issue_ids = [x for x in row.open_issue_ids if x != prefix]

    # Remove legacy join_condition placeholder (only if it matches the old pattern).
    if row.join_condition and row.join_condition.is_unknown:
        jt = (row.join_condition.join_text or "").strip()
        if jt.startswith("JOIN_UNKNOWN between source files"):
            row.join_condition = None


def _node_id(entity_type: str, entity_id: str) -> str:
    if entity_type == "SOURCE_FILE":
        return f"SRC:{entity_id}"
    if entity_type == "TARGET_TABLE":
        return f"TGT:{entity_id}"
    return entity_id


def _iter_graph_edges(graph: DataModelGraph | None) -> Iterable[GraphEdge]:
    if not graph:
        return []
    return graph.edges or []


def resolve_join_from_graph(
    *,
    graph: DataModelGraph | None,
    source_entity_id: str,
    lookup_table_id: str,
) -> Optional[JoinCondition]:
    """
    Attempt join resolution using DataModelGraph edges only.

    We only use edges when they explicitly carry join columns.
    """
    src_node = _node_id("SOURCE_FILE", source_entity_id)
    tgt_node = _node_id("TARGET_TABLE", lookup_table_id)

    for e in _iter_graph_edges(graph):
        if not e.from_columns or not e.to_columns:
            continue
        if e.from_node_id == src_node and e.to_node_id == tgt_node:
            pairs = list(zip(e.from_columns, e.to_columns, strict=False))
            join_text = f"JOIN {source_entity_id} to {lookup_table_id} ON " + " AND ".join(
                [f"{source_entity_id}.{l} = {lookup_table_id}.{r}" for l, r in pairs]
            )
            return JoinCondition(
                is_required=True,
                is_unknown=False,
                join_text=join_text,
                join_keys=[
                    JoinKeyPair(
                        left_entity={"entity_type": "SOURCE_FILE", "entity_id": source_entity_id},
                        left_columns=list(e.from_columns),
                        right_entity={"entity_type": "TARGET_TABLE", "entity_id": lookup_table_id},
                        right_columns=list(e.to_columns),
                    )
                ],
                evidence_refs=[
                    EvidenceRef(
                        source=EvidenceSource.GRAPH,
                        title="DataModelGraph edge",
                        snippet=e.comment,
                        locator=e.edge_id,
                        relevance_score=None,
                    )
                ],
            )
        if e.from_node_id == tgt_node and e.to_node_id == src_node:
            pairs = list(zip(e.to_columns, e.from_columns, strict=False))
            join_text = f"JOIN {source_entity_id} to {lookup_table_id} ON " + " AND ".join(
                [f"{source_entity_id}.{l} = {lookup_table_id}.{r}" for l, r in pairs]
            )
            return JoinCondition(
                is_required=True,
                is_unknown=False,
                join_text=join_text,
                join_keys=[
                    JoinKeyPair(
                        left_entity={"entity_type": "SOURCE_FILE", "entity_id": source_entity_id},
                        left_columns=list(e.to_columns),
                        right_entity={"entity_type": "TARGET_TABLE", "entity_id": lookup_table_id},
                        right_columns=list(e.from_columns),
                    )
                ],
                evidence_refs=[
                    EvidenceRef(
                        source=EvidenceSource.GRAPH,
                        title="DataModelGraph edge (reversed)",
                        snippet=e.comment,
                        locator=e.edge_id,
                        relevance_score=None,
                    )
                ],
            )

    return None


def resolve_join_from_lookup_rule(
    *,
    source_entity_id: str,
    lookup_rule: dict,
) -> Optional[JoinCondition]:
    """
    Resolve join from an explicit Step 1 LookupRule payload.

    Expected payload (Step 1):
      {
        "target_column": {...},
        "lookup_table": {"entity_type":"TARGET_TABLE","entity_id":"..."},
        "source_join_columns": ["..."],
        "lookup_join_columns": ["..."],
        "description": "..."
      }
    """
    lt = (lookup_rule or {}).get("lookup_table") if isinstance(lookup_rule, dict) else None
    if not isinstance(lt, dict):
        return None

    lookup_table_id = lt.get("entity_id")
    if not lookup_table_id:
        return None

    left_cols = list((lookup_rule or {}).get("source_join_columns") or [])
    right_cols = list((lookup_rule or {}).get("lookup_join_columns") or [])
    if not left_cols or not right_cols:
        return None

    pairs = list(zip(left_cols, right_cols, strict=False))
    join_text = f"JOIN {source_entity_id} to {lookup_table_id} ON " + " AND ".join(
        [f"{source_entity_id}.{l} = {lookup_table_id}.{r}" for l, r in pairs]
    )
    description = (lookup_rule or {}).get("description")

    return JoinCondition(
        is_required=True,
        is_unknown=False,
        join_text=join_text if not description else f"{join_text} ({description})",
        join_keys=[
            JoinKeyPair(
                left_entity={"entity_type": "SOURCE_FILE", "entity_id": source_entity_id},
                left_columns=left_cols,
                right_entity={"entity_type": "TARGET_TABLE", "entity_id": lookup_table_id},
                right_columns=right_cols,
            )
        ],
        evidence_refs=[
            EvidenceRef(
                source=EvidenceSource.INSTRUCTIONS,
                title="Explicit lookup rule",
                snippet=description,
                locator=None,
                relevance_score=None,
            )
        ],
    )


def ensure_join_unknown_issue(
    *,
    row: MappingRow,
    issues: list[OpenIssue],
) -> str:
    """
    Ensure a JOIN_UNKNOWN issue exists for a LOOKUP row missing join details.

    Returns the issue_id (existing or newly added).
    """
    issue_id = lookup_join_unknown_issue_id(
        target_table_id=row.target_table.entity_id,
        target_column_name=row.target_column_name,
    )
    if issue_id in {i.issue_id for i in issues}:
        return issue_id

    issues.append(
        OpenIssue(
            issue_id=issue_id,
            issue_type=IssueType.JOIN_UNKNOWN,
            severity=IssueSeverity.WARN,
            target_column={
                "entity_type": "TARGET_TABLE",
                "entity_id": row.target_table.entity_id,
                "column_name": row.target_column_name,
            },
            message="Lookup required but join keys/path are still unknown after AG2 resolution.",
            suggested_question="Which lookup table(s) and join keys should be used for this column?",
            created_by="JoinAndFilterAgent",
            evidence_refs=[],
        )
    )
    return issue_id


def clear_join_unknown_issue_id(row: MappingRow) -> None:
    """
    If AG2 resolves a join, it should clear the corresponding JOIN_UNKNOWN issue id from the row.
    """
    issue_id = lookup_join_unknown_issue_id(
        target_table_id=row.target_table.entity_id,
        target_column_name=row.target_column_name,
    )
    if issue_id in row.open_issue_ids:
        row.open_issue_ids = [x for x in row.open_issue_ids if x != issue_id]


def clear_lookup_path_required_issue_id(row: MappingRow) -> None:
    """
    If AG2 resolves a join, it should clear AG1 path-required issue id from the row.
    """
    issue_id = lookup_path_required_issue_id(
        target_table_id=row.target_table.entity_id,
        target_column_name=row.target_column_name,
    )
    if issue_id in row.open_issue_ids:
        row.open_issue_ids = [x for x in row.open_issue_ids if x != issue_id]


def clear_lookup_path_fitness_issue_id(row: MappingRow) -> None:
    """
    If path fitness concerns are no longer present, clear stale path-fitness issue id from row.
    """
    issue_id = lookup_path_fitness_issue_id(
        target_table_id=row.target_table.entity_id,
        target_column_name=row.target_column_name,
    )
    if issue_id in row.open_issue_ids:
        row.open_issue_ids = [x for x in row.open_issue_ids if x != issue_id]


def _target_requires_translation(row: MappingRow) -> bool:
    target_col = str(row.target_column_name or "").upper()
    if target_col.endswith(("_CD", "_IND", "_FLG")):
        return True
    text = " ".join(
        [
            str(row.target_logical_attribute_name or ""),
            str(row.target_attribute_business_description or ""),
            str(row.reasoning_summary or ""),
        ]
    ).upper()
    return any(tok in text for tok in _TRANSLATION_TARGET_TOKENS)


def _source_values_look_descriptive(row: MappingRow) -> bool:
    fields = [str(x or "").upper() for x in (row.source_field_names or []) if str(x or "").strip()]
    if not fields:
        return False
    descriptive_hits = 0
    codelike_hits = 0
    for field in fields:
        if any(tok in field for tok in _DESCRIPTIVE_SOURCE_TOKENS):
            descriptive_hits += 1
        if field.endswith(("_CD", "_ID", "_SK")) or any(tok in field for tok in _CODELIKE_SOURCE_TOKENS):
            codelike_hits += 1
    return descriptive_hits > 0 and descriptive_hits >= codelike_hits


def _lookup_table_columns_from_graph(*, graph: DataModelGraph | None, lookup_table_id: str) -> set[str]:
    if not graph or not lookup_table_id:
        return set()
    wanted = str(lookup_table_id).strip().upper()
    cols: set[str] = set()
    for node in graph.nodes or []:
        table_name = str(getattr(node, "table_name", "") or "").strip().upper()
        node_id = str(getattr(node, "node_id", "") or "").strip().upper()
        if table_name != wanted and not node_id.endswith(f".{wanted}") and node_id != f"TGT:{wanted}":
            continue
        for col in list(getattr(node, "columns", None) or []):
            cols.add(str(col).upper())
    return cols


def _path_is_code_to_code_equality(*, row: MappingRow, path_option: dict[str, Any]) -> bool:
    if int(path_option.get("hop_count") or 0) != 1:
        return False
    join_pairs = list(path_option.get("join_pairs") or [])
    if not join_pairs:
        return False
    pair = join_pairs[0]
    left_cols = [str(c).upper() for c in (pair.get("left_columns") or [])]
    right_cols = [str(c).upper() for c in (pair.get("right_columns") or [])]
    if len(left_cols) != len(right_cols) or not left_cols:
        return False
    target_col = str(row.target_column_name or "").upper()
    if not all(l == r for l, r in zip(left_cols, right_cols, strict=False)):
        return False
    if target_col in left_cols or target_col in right_cols:
        return True
    return all(c.endswith("_CD") for c in left_cols + right_cols)


def _path_has_circular_target_key_propagation(*, row: MappingRow, path_option: dict[str, Any]) -> bool:
    """
    Detect validation-like joins that propagate the same target key/code through lookup.
    """
    target_col = str(row.target_column_name or "").upper()
    if not target_col:
        return False

    join_pairs = list(path_option.get("join_pairs") or [])
    if not join_pairs:
        return False

    for pair in join_pairs:
        left_cols = [str(c).upper() for c in (pair.get("left_columns") or [])]
        right_cols = [str(c).upper() for c in (pair.get("right_columns") or [])]
        if len(left_cols) != len(right_cols):
            continue
        for l, r in zip(left_cols, right_cols, strict=False):
            if l == r and l == target_col:
                return True
    return False


def is_population_valid_lookup_path(
    *,
    row: MappingRow,
    path_option: dict[str, Any] | None,
    graph: DataModelGraph | None,
) -> tuple[bool, str | None, str | None]:
    """
    Validate whether a selected lookup path is usable to populate the target value.

    Returns:
      (is_population_valid, reason_code, reason_text)
    """
    if not path_option:
        return True, None, None

    target_translation = _target_requires_translation(row)
    descriptive_source = _source_values_look_descriptive(row)
    lookup_table_id = str(path_option.get("lookup_table_id") or "")
    lookup_table_upper = lookup_table_id.upper()

    table_token_match = any(tok in lookup_table_upper for tok in _CROSSWALK_TABLE_TOKENS)
    lookup_cols = _lookup_table_columns_from_graph(graph=graph, lookup_table_id=lookup_table_id)
    has_src_ref_cols = any(col.startswith(_CROSSWALK_COLUMN_PREFIXES) for col in lookup_cols)
    has_mapping_hint_cols = any(any(tok in col for tok in _CROSSWALK_COLUMN_HINTS) for col in lookup_cols)
    mapping_style_path = table_token_match or has_src_ref_cols or has_mapping_hint_cols

    circular_target_key = _path_has_circular_target_key_propagation(row=row, path_option=path_option)
    code_to_code = _path_is_code_to_code_equality(row=row, path_option=path_option)

    if circular_target_key:
        return (
            False,
            "CIRCULAR_TARGET_KEY_PROPAGATION",
            "Selected lookup path propagates the target key/code itself and looks validation-only rather than population logic.",
        )

    if target_translation and descriptive_source and code_to_code:
        return (
            False,
            "TRANSLATION_VALIDATION_PATH",
            "Selected lookup path is a code-to-code validation join while chosen source fields appear descriptive.",
        )

    if target_translation and descriptive_source and not mapping_style_path:
        return (
            False,
            "TRANSLATION_PATH_LACKS_REFERENCE",
            "Target appears to require value translation, but selected lookup endpoint does not look like a mapping/crosswalk reference.",
        )

    return True, None, None


def build_join_condition_from_path_option(path_option: dict[str, Any]) -> JoinCondition | None:
    """
    Build JoinCondition from a selected graph path option.

    Expected path_option shape:
      - path_summary: str
      - path_id: str
      - join_pairs: list[{"left_entity","left_columns","right_entity","right_columns"}]
    """
    if not isinstance(path_option, dict):
        return None
    join_pairs_raw = list(path_option.get("join_pairs") or [])
    if not join_pairs_raw:
        return None

    join_pairs: list[JoinKeyPair] = []
    for p in join_pairs_raw:
        try:
            join_pairs.append(JoinKeyPair.model_validate(p))
        except Exception:
            return None

    path_summary = str(path_option.get("path_summary") or "").strip()
    path_id = str(path_option.get("path_id") or "").strip()
    join_text = path_summary or f"Graph lookup path selected: {path_id}"

    return JoinCondition(
        is_required=True,
        is_unknown=False,
        join_text=join_text,
        join_keys=join_pairs,
        evidence_refs=[
            EvidenceRef(
                source=EvidenceSource.GRAPH,
                title="AG2 selected graph path",
                snippet=path_summary or None,
                locator=path_id or None,
                relevance_score=None,
            )
        ],
    )


def validate_join_condition_keys_exist(
    *,
    join: JoinCondition,
    source_cols_by_file: dict[str, set[str]],
    target_cols_by_table: dict[str, set[str]],
) -> bool:
    """
    Validate that all join key columns exist in Step 1 source/target schemas.
    """
    for pair in join.join_keys or []:
        left_cols = list(pair.left_columns or [])
        right_cols = list(pair.right_columns or [])
        if not left_cols or not right_cols:
            return False
        if len(left_cols) != len(right_cols):
            return False

        le = pair.left_entity
        re = pair.right_entity

        if le.entity_type == "SOURCE_FILE":
            allowed = source_cols_by_file.get(le.entity_id, set())
            if not all(c in allowed for c in left_cols):
                return False
        elif le.entity_type == "TARGET_TABLE":
            allowed = target_cols_by_table.get(le.entity_id, set())
            if not all(c in allowed for c in left_cols):
                return False
        else:
            return False

        if re.entity_type == "SOURCE_FILE":
            allowed = source_cols_by_file.get(re.entity_id, set())
            if not all(c in allowed for c in right_cols):
                return False
        elif re.entity_type == "TARGET_TABLE":
            allowed = target_cols_by_table.get(re.entity_id, set())
            if not all(c in allowed for c in right_cols):
                return False
        else:
            return False

    return True


def resolve_join_for_row(
    *,
    row: MappingRow,
    graph: DataModelGraph | None,
    lookup_rule_payload: dict | None,
    source_cols_by_file: dict[str, set[str]] | None = None,
    target_cols_by_table: dict[str, set[str]] | None = None,
) -> Optional[JoinCondition]:
    """
    Resolve join for a single MappingRow.

    Resolution precedence:
      1) Graph (only if lookup_tables are present and edge includes explicit keys)
      2) Explicit lookup_rule_payload (Step 1)
      3) None (unknown) - caller handles evidence fallback + issues
    """
    if row.rule_type != RuleType.LOOKUP:
        return None

    # 0) Selected graph hypothesis (AG1 chooser output): target-table path materialization.
    if row.selected_lookup_hypothesis_id:
        path_options = build_join_path_options_for_target(
            graph=graph,
            target_table_id=row.target_table.entity_id,
            max_hops=max(1, int(getattr(config, "STEP2_AG2_MAX_PATH_HOPS", 3))),
            max_options=max(1, int(getattr(config, "STEP2_AG2_MAX_PATH_OPTIONS", 200))),
        )
        chosen = next((p for p in path_options if str(p.get("path_id") or "") == row.selected_lookup_hypothesis_id), None)
        if chosen and bool(chosen.get("key_complete", False)):
            candidate = build_join_condition_from_path_option(chosen)
            if candidate:
                if source_cols_by_file is not None and target_cols_by_table is not None:
                    if not validate_join_condition_keys_exist(
                        join=candidate,
                        source_cols_by_file=source_cols_by_file,
                        target_cols_by_table=target_cols_by_table,
                    ):
                        candidate = None
                if candidate:
                    return candidate

    # We cannot build join keys if we don't have a source entity context.
    if not row.source_entity or row.source_entity.entity_type != "SOURCE_FILE":
        return None

    source_entity_id = row.source_entity.entity_id

    # 1) Graph edges (requires a known lookup table id)
    for lt in row.lookup_tables or []:
        if lt.entity_type != "TARGET_TABLE":
            continue
        resolved = resolve_join_from_graph(graph=graph, source_entity_id=source_entity_id, lookup_table_id=lt.entity_id)
        if resolved:
            return resolved

    # 2) Explicit lookup rule payload
    if lookup_rule_payload:
        return resolve_join_from_lookup_rule(source_entity_id=source_entity_id, lookup_rule=lookup_rule_payload)

    return None


def should_enrich_join(row: MappingRow) -> bool:
    """
    Decide whether AG2 should attempt join enrichment for this row.
    """
    return row.rule_type == RuleType.LOOKUP


def normalize_target_key_for_row(row: MappingRow) -> str:
    return normalize_target_key(row.target_table.entity_id, row.target_column_name)
