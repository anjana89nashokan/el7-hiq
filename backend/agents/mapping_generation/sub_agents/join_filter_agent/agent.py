"""
JoinAndFilterAgent (Step 2) - Subagent #2.

Runtime responsibilities:
  1) Keep deterministic pre-resolution from graph / explicit lookup rules.
  2) Apply AG1-selected path_id first when valid.
  3) Call AG2 LLM join-path selector only when AG1 path is missing/invalid.
  4) Deterministically validate/apply selected path (authoritative gate).
  5) Fall back to deterministic pre-resolution when selector output is invalid/unresolved.
  6) Otherwise keep JOIN_UNKNOWN + review issue.

Guardrails:
  - LLM selects only from provided path_ids (no join generation).
  - All applied join keys must exist in Step 1 source schema and known target-table columns
    (selected target metadata + graph target tables for bounded path validation).
"""

from __future__ import annotations

import asyncio
import json
import re

from pydantic import BaseModel

from google.adk import Runner
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App
from utils.adk_runtime import VertexAiSessionService
from google.genai import types

from agents.mapping_generation.models import (
    IssueSeverity,
    IssueType,
    JoinCondition,
    MappingRow,
    OpenIssue,
    RuleType,
    Step2WorkContext,
    TableCommonFilter,
)
from agents.mapping_ingestion.models import EntityRef
from config.settings import config
from utils.join_filter_utils import (
    build_join_condition_from_path_option,
    clear_stale_direct_source_join_placeholders,
    clear_lookup_path_fitness_issue_id,
    clear_lookup_path_required_issue_id,
    clear_stale_lookup_join_issue_if_not_lookup,
    clear_join_unknown_issue_id,
    ensure_join_unknown_issue,
    is_population_valid_lookup_path,
    normalize_target_key_for_row,
    resolve_join_for_row,
    should_enrich_join,
    lookup_path_fitness_issue_id,
    validate_join_condition_keys_exist,
)
from utils.mapping_logic_utils import finalize_needs_review
from utils.step2_graph_hypothesis_utils import (
    build_join_path_options_for_target,
)
from utils.step2_subgraph_context_utils import build_connected_component_subgraph_json
from utils.step2_shared_tools import build_table_common_filters
from .models import JoinPathSelectionOutput
from .prompts import get_join_path_selection_prompt


STEP2_MODEL = getattr(config, "STEP2_AGENT_MODEL", config.AGENT_MODEL)


def _get_agent_engine_id() -> str | None:
    resource = getattr(config, "REASONING_ENGINE_RESOURCE", None)
    if not resource:
        return None
    resource = resource.strip()
    if not resource:
        return None
    return resource.split("/")[-1]


def _context_cache_config() -> ContextCacheConfig | None:
    if not bool(getattr(config, "STEP2_CONTEXT_CACHE_ENABLED", True)):
        return None
    return ContextCacheConfig(
        min_tokens=max(0, int(getattr(config, "STEP2_CONTEXT_CACHE_MIN_TOKENS", 4096))),
        ttl_seconds=max(1, int(getattr(config, "STEP2_CONTEXT_CACHE_TTL_SECONDS", 1800))),
        cache_intervals=max(1, int(getattr(config, "STEP2_CONTEXT_CACHE_INTERVALS", 10))),
    )


def _join_selector_generate_cfg() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        temperature=float(getattr(config, "STEP2_AG2_LLM_JOIN_TEMPERATURE", 0.1)),
    )


class _StructuredTool:
    """
    Lightweight ADK runner wrapper for one structured-output LLM tool.
    """

    def __init__(
        self,
        *,
        app_name: str,
        root_agent: LlmAgent,
        output_key: str,
        output_model: type[BaseModel],
        context_cache_config: ContextCacheConfig | None = None,
    ) -> None:
        self._app = App(name=app_name, root_agent=root_agent, context_cache_config=context_cache_config)
        self._session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
            agent_engine_id=_get_agent_engine_id(),
        )
        self._runner = Runner(app=self._app, session_service=self._session_service)
        self._session_id: str | None = None
        self._output_key = output_key
        self._output_model = output_model

    async def _ensure_session_id(self) -> str | None:
        if self._session_id:
            return self._session_id
        call_timeout = max(0, int(getattr(config, "STEP2_LLM_CALL_TIMEOUT_SEC", 120)))
        try:
            async def _create() -> str:
                session = await self._session_service.create_session(app_name=self._app.name, user_id="system", state={})
                return session.id

            sid = await asyncio.wait_for(_create(), timeout=call_timeout) if call_timeout else await _create()
            self._session_id = sid
            return sid
        except Exception:
            return None

    async def call(self, payload: dict) -> BaseModel | None:
        session_id = await self._ensure_session_id()
        if not session_id:
            return None

        msg = types.Content(role="user", parts=[types.Part(text=f"INPUT_JSON:\n{json.dumps(payload, indent=2)}")])
        call_timeout = max(0, int(getattr(config, "STEP2_LLM_CALL_TIMEOUT_SEC", 120)))
        try:
            async def _run_once() -> BaseModel | None:
                raw_json = None
                async for event in self._runner.run_async(user_id="system", session_id=session_id, new_message=msg):
                    if hasattr(event, "actions") and event.actions and getattr(event.actions, "state_delta", None):
                        if self._output_key in event.actions.state_delta:
                            raw_json = json.dumps(event.actions.state_delta[self._output_key])
                            break
                if not raw_json:
                    return None
                return self._output_model.model_validate_json(raw_json)

            return await asyncio.wait_for(_run_once(), timeout=call_timeout) if call_timeout else await _run_once()
        except Exception:
            return None


def _dedupe_path_options(path_options: list[dict], max_options: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for p in path_options:
        pid = str(p.get("path_id") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(p)
    return out[:max_options]


def _table_family_token(table_id: str | None) -> str:
    t = str(table_id or "").strip().upper()
    if not t:
        return ""
    if "_" in t:
        return t.split("_", 1)[0]
    return t


def _score_option_for_row(*, row: MappingRow, option: dict) -> int:
    """
    Lightweight deterministic scorer used before final option truncation.
    """
    score = 0
    hop_count = int(option.get("hop_count") or 99)
    lookup_table_id = str(option.get("lookup_table_id") or "")
    target_table_id = str(row.target_table.entity_id or "")

    # Prefer fewer hops.
    score += max(0, 40 - (hop_count * 8))

    # Prefer same table family.
    tf = _table_family_token(target_table_id)
    lf = _table_family_token(lookup_table_id)
    if tf and lf and tf == lf:
        score += 25

    # Prefer lookup tables already hinted on row (explicit rule or AG1 selection trace).
    hinted_lookup_ids = {str(x.entity_id) for x in (row.lookup_tables or []) if getattr(x, "entity_id", None)}
    if lookup_table_id and lookup_table_id in hinted_lookup_ids:
        score += 35

    # Penalize obvious self-loop endpoint.
    if lookup_table_id.strip().upper() == target_table_id.strip().upper():
        score -= 20

    # Mild boost for paths that touch source-selected column names.
    src_names = {str(s).upper() for s in (row.source_field_names or [])}
    if src_names:
        for jp in option.get("join_pairs") or []:
            left_cols = {str(c).upper() for c in (jp.get("left_columns") or [])}
            right_cols = {str(c).upper() for c in (jp.get("right_columns") or [])}
            if src_names.intersection(left_cols.union(right_cols)):
                score += 6
                break

    return score


def _rank_and_truncate_path_options(*, row: MappingRow, path_options: list[dict], max_options: int) -> list[dict]:
    deduped = _dedupe_path_options(path_options=path_options, max_options=max(1, len(path_options)))
    scored: list[tuple[int, dict]] = []
    for p in deduped:
        scored.append((_score_option_for_row(row=row, option=p), p))
    scored.sort(key=lambda item: (-item[0], int(item[1].get("hop_count") or 99), str(item[1].get("path_id") or "")))
    return [p for _, p in scored[: max(1, int(max_options))]]


def _sanitize_for_app_name(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower()[:40] or "target"


def _get_or_create_selector_tool_for_target(
    *,
    target_table_id: str,
    graph,
    cache: dict[str, _StructuredTool],
) -> _StructuredTool:
    key = str(target_table_id or "").strip()
    if key in cache:
        return cache[key]

    subgraph_json = build_connected_component_subgraph_json(
        graph=graph,
        target_table_id=key,
        max_nodes=max(1, int(getattr(config, "STEP2_AG1_SUBGRAPH_MAX_NODES", 600))),
        max_edges=max(1, int(getattr(config, "STEP2_AG1_SUBGRAPH_MAX_EDGES", 3000))),
        max_columns_per_node=max(1, int(getattr(config, "STEP2_AG1_SUBGRAPH_MAX_COLUMNS_PER_NODE", 600))),
    )
    static_text = (
        "SUBGRAPH_CONTEXT_JSON for this target table (target + all related tables in the connected component).\n"
        "Use it as structural context only; never invent identifiers.\n\n"
        f"{subgraph_json}\n"
    )
    table_suffix = _sanitize_for_app_name(key)
    local_agent = LlmAgent(
        name=f"join_path_selector_agent_{table_suffix}",
        model=STEP2_MODEL,
        description="Selects one join path for a LOOKUP row from provided path options (structured output).",
        static_instruction=static_text,
        instruction=get_join_path_selection_prompt(),
        output_schema=JoinPathSelectionOutput,
        output_key="join_path_selection",
        generate_content_config=_join_selector_generate_cfg(),
    )
    tool = _StructuredTool(
        app_name=f"step2_ag2_join_path_selector_{table_suffix}",
        root_agent=local_agent,
        output_key="join_path_selection",
        output_model=JoinPathSelectionOutput,
        context_cache_config=_context_cache_config(),
    )
    cache[key] = tool
    return tool


def _build_selector_payload(
    *,
    ctx: Step2WorkContext,
    row: MappingRow,
    path_options: list[dict],
    deterministic_join: JoinCondition | None,
) -> dict:
    return {
        "interface_code": ctx.shared_state.interface_code,
        "target_context": {
            "target_table_id": row.target_table.entity_id,
            "target_column_name": row.target_column_name,
            "target_logical_name": row.target_logical_attribute_name,
            "target_business_description": row.target_attribute_business_description,
            "target_data_type": row.target_data_type,
        },
        "source_context": {
            "source_entity_id": row.source_entity.entity_id if row.source_entity else None,
            "source_field_names": list(row.source_field_names or []),
        },
        "rule_type": row.rule_type.value,
        "ag1_selected_lookup_hypothesis_id": row.selected_lookup_hypothesis_id,
        "deterministic_candidate_join": deterministic_join.model_dump() if deterministic_join else None,
        "path_options": path_options,
        "evidence_snippets": [e.snippet for e in (row.evidence_refs or []) if getattr(e, "snippet", None)],
        "policy_manifest": {
            "allowed_path_ids": [str(p.get("path_id")) for p in path_options if p.get("path_id")],
            "must_select_from_options_only": True,
            "must_not_invent_identifiers": True,
            "max_hops": int(getattr(config, "STEP2_AG2_MAX_PATH_HOPS", 3)),
        },
    }


async def run_join_and_filter_agent(
    ctx: Step2WorkContext,
    rows: list[MappingRow],
    issues: list[OpenIssue],
) -> tuple[list[MappingRow], list[OpenIssue], list[TableCommonFilter]]:
    """
    Entrypoint for AG2.
    """

    source_cols_by_file: dict[str, set[str]] = {
        f.file_id: {c.physical_name for c in f.columns}
        for f in ctx.shared_state.source_schema.files
    }
    target_cols_by_table: dict[str, set[str]] = {
        t.table_id: {c.attribute_name for c in t.columns}
        for t in ctx.shared_state.target_schema.tables
    }
    # Allow AG2 path validation across graph-connected target tables (not only selected target metadata tables).
    # This keeps source-side validation strict while permitting explicit-key multi-hop graph paths.
    if ctx.shared_state.data_model_graph:
        for n in ctx.shared_state.data_model_graph.nodes or []:
            if getattr(n, "node_type", None) != "TARGET_TABLE":
                continue
            cols = set(getattr(n, "columns", None) or [])
            if not cols:
                continue

            candidate_ids: set[str] = set()
            table_name = str(getattr(n, "table_name", "") or "").strip()
            database_name = str(getattr(n, "database_name", "") or "").strip()
            node_id = str(getattr(n, "node_id", "") or "").strip()

            if table_name:
                candidate_ids.add(table_name)
            if database_name and table_name:
                candidate_ids.add(f"{database_name}.{table_name}")
            if node_id.startswith("TGT:"):
                raw = node_id.split(":", 1)[1]
                if raw:
                    candidate_ids.add(raw)
                    if "." in raw:
                        candidate_ids.add(raw.split(".", 1)[1])

            for tid in candidate_ids:
                target_cols_by_table.setdefault(tid, set()).update(cols)

    llm_enabled = bool(getattr(config, "STEP2_AG2_LLM_JOIN_ENABLED", True))
    max_hops = max(1, int(getattr(config, "STEP2_AG2_MAX_PATH_HOPS", 3)))
    max_options = max(1, int(getattr(config, "STEP2_AG2_MAX_PATH_OPTIONS", 200)))
    review_conf_threshold = float(getattr(config, "STEP2_AG2_LLM_JOIN_CONFIDENCE_REVIEW_THRESHOLD", 0.7))
    path_fitness_guard_enabled = bool(getattr(config, "STEP2_LOOKUP_PATH_FITNESS_GUARD_ENABLED", True))
    path_fitness_strict_reject = bool(getattr(config, "STEP2_LOOKUP_PATH_FITNESS_STRICT_REJECT", False))

    selector_tools_by_target: dict[str, _StructuredTool] = {}

    def _upsert_row_issue(*, row: MappingRow, issue: OpenIssue) -> None:
        if issue.issue_id not in {i.issue_id for i in issues}:
            issues.append(issue)
        if issue.issue_id not in row.open_issue_ids:
            row.open_issue_ids.append(issue.issue_id)

    def _mark_population_invalid(*, row: MappingRow, reason_code: str | None, reason_text: str | None) -> None:
        row.needs_review = True
        if reason_text:
            note = f"Lookup path invalid for population: {reason_text}"
            row.special_considerations_text = (
                note
                if not row.special_considerations_text
                else f"{row.special_considerations_text} | {note}"
            )[:2000]
        _upsert_row_issue(
            row=row,
            issue=OpenIssue(
                issue_id=lookup_path_fitness_issue_id(
                    target_table_id=row.target_table.entity_id,
                    target_column_name=row.target_column_name,
                ),
                issue_type=IssueType.AMBIGUOUS_MAPPING,
                severity=IssueSeverity.WARN,
                target_column={
                    "entity_type": "TARGET_TABLE",
                    "entity_id": row.target_table.entity_id,
                    "column_name": row.target_column_name,
                },
                message=reason_text
                or "Selected lookup path is semantically invalid for population.",
                suggested_question="Confirm the correct lookup path and join logic for populating this target column.",
                created_by="JoinAndFilterAgent",
                evidence_refs=[],
            ),
        )

    def _fitness_accepts_join(
        *,
        row: MappingRow,
        path_option: dict | None,
    ) -> tuple[bool, bool]:
        if not path_fitness_guard_enabled:
            return True, False
        path_valid, reason_code, reason_text = is_population_valid_lookup_path(
            row=row,
            path_option=path_option,
            graph=ctx.shared_state.data_model_graph,
        )
        if path_valid:
            return True, False
        _mark_population_invalid(row=row, reason_code=reason_code, reason_text=reason_text)
        return (not path_fitness_strict_reject), True

    for row in rows:
        clear_stale_lookup_join_issue_if_not_lookup(row)
        clear_stale_direct_source_join_placeholders(row)

    for row in rows:
        if not should_enrich_join(row):
            continue

        key = normalize_target_key_for_row(row)
        # Normalize potential dict payloads into EntityRef objects (defensive).
        normalized_lookup_tables: list[EntityRef] = []
        for lt in list(row.lookup_tables or []):
            try:
                normalized_lookup_tables.append(EntityRef.model_validate(lt))
            except Exception:
                continue
        row.lookup_tables = normalized_lookup_tables

        lr_payload = ctx.lookup_rules_map.get(key)
        if isinstance(lr_payload, dict):
            lt = lr_payload.get("lookup_table")
            if isinstance(lt, dict) and lt.get("entity_id"):
                lt_ref = EntityRef.model_validate(lt)
                if not any(
                    (x.entity_type == lt_ref.entity_type and x.entity_id == lt_ref.entity_id)
                    for x in row.lookup_tables
                ):
                    row.lookup_tables.append(lt_ref)

        deterministic_resolved = resolve_join_for_row(
            row=row,
            graph=ctx.shared_state.data_model_graph,
            lookup_rule_payload=lr_payload if isinstance(lr_payload, dict) else None,
            source_cols_by_file=source_cols_by_file,
            target_cols_by_table=target_cols_by_table,
        )
        deterministic_valid = bool(
            deterministic_resolved
            and validate_join_condition_keys_exist(
                join=deterministic_resolved,
                source_cols_by_file=source_cols_by_file,
                target_cols_by_table=target_cols_by_table,
            )
        )

        path_options = build_join_path_options_for_target(
            graph=ctx.shared_state.data_model_graph,
            target_table_id=row.target_table.entity_id,
            max_hops=max_hops,
            max_options=max_options,
        )
        path_options = _rank_and_truncate_path_options(
            row=row,
            path_options=path_options,
            max_options=max_options,
        )

        selected_join: JoinCondition | None = None
        selected_join_has_fitness_issue = False
        selector_out: JoinPathSelectionOutput | None = None

        ag1_selected_path_id = str(row.selected_lookup_hypothesis_id or "").strip()
        if ag1_selected_path_id:
            selected = next(
                (p for p in path_options if str(p.get("path_id") or "") == ag1_selected_path_id),
                None,
            )
            if selected:
                candidate_join = build_join_condition_from_path_option(selected)
                if candidate_join and validate_join_condition_keys_exist(
                    join=candidate_join,
                    source_cols_by_file=source_cols_by_file,
                    target_cols_by_table=target_cols_by_table,
                ):
                    accepts, flagged_invalid = _fitness_accepts_join(row=row, path_option=selected)
                    if accepts:
                        selected_join = candidate_join
                        selected_join_has_fitness_issue = flagged_invalid
                else:
                    row.needs_review = True
            else:
                row.needs_review = True

        if (not selected_join) and llm_enabled and row.rule_type == RuleType.LOOKUP:
            selector_tool = _get_or_create_selector_tool_for_target(
                target_table_id=row.target_table.entity_id,
                graph=ctx.shared_state.data_model_graph,
                cache=selector_tools_by_target,
            )
            payload = _build_selector_payload(
                ctx=ctx,
                row=row,
                path_options=path_options,
                deterministic_join=deterministic_resolved if deterministic_valid else None,
            )
            raw_out = await selector_tool.call(payload)
            if raw_out:
                selector_out = JoinPathSelectionOutput.model_validate(raw_out.model_dump())
                if selector_out.selected_path_id:
                    selected_path_id = str(selector_out.selected_path_id).strip()
                    selected = next(
                        (p for p in path_options if str(p.get("path_id") or "") == selected_path_id),
                        None,
                    )
                    if selected:
                        candidate_join = build_join_condition_from_path_option(selected)
                        if candidate_join and validate_join_condition_keys_exist(
                            join=candidate_join,
                            source_cols_by_file=source_cols_by_file,
                            target_cols_by_table=target_cols_by_table,
                        ):
                            accepts, flagged_invalid = _fitness_accepts_join(row=row, path_option=selected)
                            if accepts:
                                selected_join = candidate_join
                                selected_join_has_fitness_issue = flagged_invalid
                        else:
                            row.needs_review = True

        if not selected_join and deterministic_valid:
            path_ref = None
            for ref in (deterministic_resolved.evidence_refs or []):
                locator = str(getattr(ref, "locator", "") or "").strip()
                if locator.startswith("PATH_"):
                    path_ref = locator
                    break
            if path_fitness_guard_enabled and path_ref:
                deterministic_path = next((p for p in path_options if str(p.get("path_id") or "") == path_ref), None)
                if deterministic_path:
                    accepts, flagged_invalid = _fitness_accepts_join(row=row, path_option=deterministic_path)
                    if not accepts:
                        deterministic_valid = False
                    elif flagged_invalid:
                        selected_join_has_fitness_issue = True
            if deterministic_valid:
                selected_join = deterministic_resolved

        if selected_join:
            row.join_condition = selected_join
            if selector_out and selector_out.selected_path_id and not (row.selected_lookup_hypothesis_id or "").strip():
                row.selected_lookup_hypothesis_id = str(selector_out.selected_path_id).strip()
            if not (row.selected_lookup_hypothesis_id or "").strip():
                for ref in (selected_join.evidence_refs or []):
                    locator = str(getattr(ref, "locator", "") or "").strip()
                    if locator.startswith("PATH_"):
                        row.selected_lookup_hypothesis_id = locator
                        break
            if selected_join_has_fitness_issue:
                row.needs_review = True
            else:
                clear_lookup_path_fitness_issue_id(row)

            clear_join_unknown_issue_id(row)
            clear_lookup_path_required_issue_id(row)
        else:
            row.join_condition = JoinCondition(
                is_required=True,
                is_unknown=True,
                join_text="JOIN_UNKNOWN",
                join_keys=[],
                evidence_refs=[],
            )
            issue_id = ensure_join_unknown_issue(row=row, issues=issues)
            if issue_id not in row.open_issue_ids:
                row.open_issue_ids.append(issue_id)

        if selector_out:
            if selector_out.needs_review or selector_out.confidence < review_conf_threshold:
                row.needs_review = True
            row.confidence_score = min(float(row.confidence_score), float(selector_out.confidence))
            if selector_out.reasoning_summary:
                prefix = "AG2 join selector"
                text = selector_out.reasoning_summary.strip()
                if text:
                    row.special_considerations_text = (
                        f"{prefix}: {text}"
                        if not row.special_considerations_text
                        else f"{row.special_considerations_text} | {prefix}: {text}"
                    )

    referenced = {iid for r in rows for iid in (r.open_issue_ids or [])}
    issues = [i for i in issues if i.issue_id in referenced]

    threshold = float(getattr(config, "STEP2_NEEDS_REVIEW_CONFIDENCE_THRESHOLD", 0.85))
    for row in rows:
        finalize_needs_review(row, confidence_threshold=threshold)

    table_common_filters = build_table_common_filters(ctx)
    return rows, issues, table_common_filters


join_and_filter_agent = SequentialAgent(
    name="join_and_filter_agent",
    sub_agents=[],
    description="JoinAndFilterAgent (Step 2) exposed via run_join_and_filter_agent(ctx, rows, issues).",
)

__all__ = ["join_and_filter_agent", "run_join_and_filter_agent"]
