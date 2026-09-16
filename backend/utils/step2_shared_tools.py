"""
Shared utilities for Step 2 (Mapping Generation).

Responsibilities:
    - Work context builder (scope, overrides, filters)
    - Common filter builder from Step 1 global filters
    - Target key normalizer (imported where needed)
"""

from __future__ import annotations

from typing import Dict, List

from agents.mapping_generation.models import (
    CommonFilterScope,
    EvidenceSource,
    Step2WorkContext,
    TableCommonFilter,
)
from agents.mapping_ingestion.models import GlobalFilter, SharedState

from utils.mapping_logic_utils import map_rule_type, normalize_target_key  # re-export convenience
from config.settings import config


def build_work_context(shared_state: SharedState) -> Step2WorkContext:
    """
    Derive scope, overrides, and quick-lookup maps from SharedState.

    This is the glue all sub-agents rely on.
    """
    mc = shared_state.mapping_context

    selected_source_ids = mc.selected_sources or [f.file_id for f in shared_state.source_schema.files]
    selected_target_ids = mc.selected_targets or [t.table_id for t in shared_state.target_schema.tables]

    ignore_fields_keys = {
        normalize_target_key(ref.entity_id, ref.column_name)
        for ref in mc.overrides.ignore_fields
        if ref.entity_type == "TARGET_TABLE"
    }

    rule_type_overrides_map = {}
    rule_type_override_reasons = {}
    for rto in mc.overrides.rule_type_overrides:
        if rto.target_column.entity_type != "TARGET_TABLE":
            continue
        key = normalize_target_key(rto.target_column.entity_id, rto.target_column.column_name)
        rule_type_overrides_map[key] = map_rule_type(rto.forced_rule_type)
        if rto.reason:
            rule_type_override_reasons[key] = rto.reason

    default_rules_map = {}
    for dr in mc.overrides.default_rules:
        if dr.target_column.entity_type != "TARGET_TABLE":
            continue
        key = normalize_target_key(dr.target_column.entity_id, dr.target_column.column_name)
        default_rules_map[key] = dr.model_dump()

    lookup_rules_map = {}
    for lr in mc.overrides.lookup_rules:
        if lr.target_column.entity_type != "TARGET_TABLE":
            continue
        key = normalize_target_key(lr.target_column.entity_id, lr.target_column.column_name)
        lookup_rules_map[key] = lr.model_dump()

    composite_key_rules_by_entity: Dict[str, List[dict]] = {}
    for ck in mc.overrides.composite_key_rules:
        composite_key_rules_by_entity.setdefault(ck.entity.entity_id, []).append(ck.model_dump())

    # Global filters indexed by scope (MAPPING/TABLE). Column-scoped filters stay on rows later.
    global_filters_mapping: List[GlobalFilter] = []
    global_filters_by_table: Dict[str, List[GlobalFilter]] = {}
    global_filters_by_column: Dict[str, List[GlobalFilter]] = {}
    for gf in mc.global_filters:
        if gf.scope == "MAPPING":
            global_filters_mapping.append(gf)
        elif gf.scope == "TABLE" and gf.target_table_id:
            global_filters_by_table.setdefault(gf.target_table_id, []).append(gf)
        elif gf.scope == "COLUMN" and gf.target_table_id and gf.target_column_name:
            key = normalize_target_key(gf.target_table_id, gf.target_column_name)
            global_filters_by_column.setdefault(key, []).append(gf)

    # Explicit mappings (Step 1) can be used to restrict candidate sources per target table.
    # Example: if BSA says "Account + Identifier feed PRV_MAP", we should not consider unrelated files for PRV_MAP.
    explicit_source_ids_by_target_table: Dict[str, set[str]] = {}
    for em in getattr(mc, "explicit_mappings", []) or []:
        if getattr(em.source, "entity_type", None) != "SOURCE_FILE":
            continue
        if getattr(em.target, "entity_type", None) != "TARGET_TABLE":
            continue
        src_id = em.source.entity_id
        tgt_id = em.target.entity_id
        explicit_source_ids_by_target_table.setdefault(tgt_id, set()).add(src_id)

    # Normalize explicit mapping scope to selected_sources to prevent leakage.
    for tgt_id, src_ids in list(explicit_source_ids_by_target_table.items()):
        explicit_source_ids_by_target_table[tgt_id] = {s for s in src_ids if s in selected_source_ids}

    return Step2WorkContext(
        shared_state=shared_state,
        selected_source_ids=selected_source_ids,
        selected_target_ids=selected_target_ids,
        ignore_fields_keys=ignore_fields_keys,
        rule_type_overrides_map=rule_type_overrides_map,
        rule_type_override_reasons=rule_type_override_reasons,
        default_rules_map=default_rules_map,
        lookup_rules_map=lookup_rules_map,
        composite_key_rules_by_entity=composite_key_rules_by_entity,
        global_filters_mapping=global_filters_mapping,
        global_filters_by_table=global_filters_by_table,
        global_filters_by_column=global_filters_by_column,
        explicit_source_ids_by_target_table=explicit_source_ids_by_target_table,
        rag_enabled=bool(config.STEP2_RAG_ENABLED),
        force_technical_rules=bool(getattr(config, "STEP2_FORCE_TECHNICAL_RULES", True)),
    )


def build_table_common_filters(ctx: Step2WorkContext) -> List[TableCommonFilter]:
    """
    Convert mapping/table-level filters from Step 1 into TableCommonFilter.
    Column-level filters stay on rows.
    """
    filters: List[TableCommonFilter] = []
    for gf in ctx.global_filters_mapping:
        filters.append(
            TableCommonFilter(
                scope=CommonFilterScope.MAPPING,
                target_table_id=None,
                description=gf.description,
                expression_text=gf.expression_text,
                source=EvidenceSource.INSTRUCTIONS,
                evidence_refs=[],
            )
        )
    for table_id, gfs in ctx.global_filters_by_table.items():
        for gf in gfs:
            filters.append(
                TableCommonFilter(
                    scope=CommonFilterScope.TABLE,
                    target_table_id=table_id,
                    description=gf.description,
                    expression_text=gf.expression_text,
                    source=EvidenceSource.INSTRUCTIONS,
                    evidence_refs=[],
                )
            )
    return filters
