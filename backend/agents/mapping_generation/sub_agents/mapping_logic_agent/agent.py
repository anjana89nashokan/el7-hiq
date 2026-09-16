"""
MappingLogicAgent (Step 2) - Subagent #1.

Responsibilities (per Step 2 runtime design):
  - Iterate scoped target tables/columns (respect ignore_fields)
  - Build source candidate sets from indexed source catalog (schema-constrained)
  - Run LLM-major inferred chooser (pass1 -> pass2 -> self-check) for inferred rows
  - Apply deterministic guardrails/validation to all LLM outputs
  - Emit MappingRow entries + immediate OpenIssue seeds (e.g., missing AK for SK, missing source, conflicting evidence)

LLM:
  - Candidate discovery uses an indexed Source Catalog (Option A):
      * Build a stable, indexed catalog of all source columns in-scope for this run.
      * LLM may ONLY return indices into that catalog (structured output).
      * Post-validation resolves indices -> (file_id, column_name) and verifies they exist in Step 1 schemas.
  - Inferred rule/candidate selection uses structured pass1 + pass2 + self-check prompts.
  - Multi-rule drafting (CASE/IF_ELSE) remains structured and schema-constrained.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from pydantic import BaseModel

from google.adk import Runner
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App
from utils.adk_runtime import VertexAiSessionService
from google.genai import types

from agents.mapping_generation.models import (
    CandidateSource,
    EvidenceAuthorityLevel,
    EvidenceRef,
    EvidenceSource,
    EvidenceType,
    IssueSeverity,
    IssueType,
    MappingRow,
    OpenIssue,
    RuleType,
)
from agents.mapping_ingestion.models import EntityRef
from config.settings import config
from utils.indemap_history_mapping_utils import (
    fetch_indemap_past_mappings_for_target,
    prefilter_history_rules,
)
from utils.mapping_logic_utils import (
    finalize_needs_review,
    is_case_ifelse_eligible,
    normalize_target_key,
    run_mapping_logic,
    validate_multi_rule_concreteness,
)
from utils.step2_rag_tools import (
    _format_indemap_history_snippet,
    retrieve_evidence_pack,
    retrieve_experience_refs_bq,
)
from utils.step2_graph_hypothesis_utils import build_join_path_options_for_target
from utils.step2_subgraph_context_utils import build_connected_component_subgraph_json
from .models import (
    CatalogCandidatesOutput,
    DecisionSelfCheckOutput,
    HistoricalMappingCandidate,
    HistoricalMappingRerankOutput,
    LookupPathSelectionOutput,
    MultiRuleOutput,
    RuleCandidateDecisionOutput,
    RuleCandidateDecisionRefinementOutput,
    SourceCatalogItem,
)
from .prompts import (
    get_catalog_candidate_prompt,
    get_sk_natural_key_prompt,
    get_history_mapping_rerank_prompt,
    get_rule_decision_prompt,
    get_rule_refinement_prompt,
    get_decision_self_check_prompt,
    get_lookup_path_selection_prompt,
    get_multi_rule_prompt,
)  


STEP2_MODEL = getattr(config, "STEP2_AGENT_MODEL", config.AGENT_MODEL)
logger = logging.getLogger(__name__)


def _get_agent_engine_id() -> str | None:
    resource = getattr(config, "REASONING_ENGINE_RESOURCE", None)
    if not resource:
        return None
    resource = resource.strip()
    if not resource:
        return None
    return resource.split("/")[-1]

def _decision_generate_cfg() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        temperature=float(getattr(config, "STEP2_LLM_DECISION_TEMPERATURE", 0.1)),
    )

multi_rule_agent = LlmAgent(
    name="multi_rule_agent",
    model=STEP2_MODEL,
    description="Drafts rule instances for CASE/IF_ELSE (structured output).",
    instruction=get_multi_rule_prompt(),
    output_schema=MultiRuleOutput,
    output_key="multi_rule",
    generate_content_config=_decision_generate_cfg(),
)


def _llm_max_catalog_candidate_calls() -> int:
    """
    Upper bound on catalog candidate-discovery calls per Step 2 run.

    This call is typically made once per target column that needs a source field.
    """
    if not bool(getattr(config, "STEP2_LLM_ENFORCE_BUDGETS", False)):
        return 1_000_000
    return max(0, int(getattr(config, "STEP2_LLM_MAX_CANDIDATE_CALLS", 10_000)))


def _llm_max_rule_decisions() -> int:
    if not bool(getattr(config, "STEP2_LLM_ENFORCE_BUDGETS", False)):
        return 1_000_000
    return max(0, int(getattr(config, "STEP2_LLM_MAX_RULE_DECISIONS", 25)))


def _context_cache_config() -> ContextCacheConfig | None:
    if not bool(getattr(config, "STEP2_CONTEXT_CACHE_ENABLED", True)):
        return None
    return ContextCacheConfig(
        min_tokens=max(0, int(getattr(config, "STEP2_CONTEXT_CACHE_MIN_TOKENS", 4096))),
        ttl_seconds=max(1, int(getattr(config, "STEP2_CONTEXT_CACHE_TTL_SECONDS", 1800))),
        cache_intervals=max(1, int(getattr(config, "STEP2_CONTEXT_CACHE_INTERVALS", 10))),
    )


def _sanitize_for_app_name(value: str) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(value or "").strip())
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")
    sanitized = sanitized.strip("_").lower()
    return sanitized[:40] or "target"


def _build_ag1_static_context_for_target(
    *,
    graph,
    target_table_id: str,
) -> str:
    if not bool(getattr(config, "STEP2_AG1_SUBGRAPH_CONTEXT_ENABLED", True)):
        return ""

    subgraph_json = build_connected_component_subgraph_json(
        graph=graph,
        target_table_id=target_table_id,
        max_nodes=max(1, int(getattr(config, "STEP2_AG1_SUBGRAPH_MAX_NODES", 600))),
        max_edges=max(1, int(getattr(config, "STEP2_AG1_SUBGRAPH_MAX_EDGES", 3000))),
        max_columns_per_node=max(1, int(getattr(config, "STEP2_AG1_SUBGRAPH_MAX_COLUMNS_PER_NODE", 600))),
    )
    path_options = build_join_path_options_for_target(
        graph=graph,
        target_table_id=target_table_id,
        max_hops=max(1, int(getattr(config, "STEP2_AG2_MAX_PATH_HOPS", 3))),
        max_options=max(1, int(getattr(config, "STEP2_AG2_MAX_PATH_OPTIONS", 200))),
    )
    return (
        "SUBGRAPH_CONTEXT_JSON for this target table (target + related tables with columns and PK/FK/SK tags).\n"
        "Use it as context only; do not invent identifiers.\n"
        f"{subgraph_json}\n\n"
        "LOOKUP_PATH_OPTIONS_JSON for this target table (bounded explicit-key graph paths).\n"
        "When choosing LOOKUP, select only from these path_id values.\n"
        f"{json.dumps(path_options, ensure_ascii=False)}\n"
    )


class _StructuredTool:
    """
    Generic reusable ADK Runner wrapper for a single LlmAgent with structured output.

    Why we do this:
      - Creating App/Runner/SessionService per column is extremely slow.
      - We reuse one Runner and one session for the whole run.

    Guardrails:
      - LLM output is validated against the provided Pydantic schema.
    """

    def __init__(
        self,
        app_name: str,
        root_agent: LlmAgent,
        output_key: str,
        output_model: type[BaseModel],
        *,
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
        max_retries = 3
        base_delay = 2.0

        for attempt in range(max_retries + 1):
            try:
                async def _create() -> str:
                    session = await self._session_service.create_session(app_name=self._app.name, user_id="system", state={})
                    return session.id

                session_id = await asyncio.wait_for(_create(), timeout=call_timeout) if call_timeout else await _create()
                self._session_id = session_id
                return session_id
            except Exception as e:
                if attempt == max_retries:
                    return None
                # Exponential backoff
                delay = min(base_delay * (2 ** attempt), 30.0)
                await asyncio.sleep(delay)

    async def call(self, payload: dict) -> BaseModel | None:
        msg = types.Content(role="user", parts=[types.Part(text=f"INPUT_JSON:\n{json.dumps(payload, indent=2)}")])
        session_id = await self._ensure_session_id()
        if not session_id:
            return None

        call_timeout = max(0, int(getattr(config, "STEP2_LLM_CALL_TIMEOUT_SEC", 120)))
        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries + 1):
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

                if call_timeout:
                    return await asyncio.wait_for(_run_once(), timeout=call_timeout)
                return await _run_once()
            except (asyncio.TimeoutError, Exception) as e:
                if attempt == max_retries:
                    return None
                # Exponential backoff for retries
                delay = min(base_delay * (2 ** attempt), 10.0)
                await asyncio.sleep(delay)


def _get_or_create_decision_tools_for_target_table(
    *,
    target_table_id: str,
    graph,
    cache: dict[str, tuple[_StructuredTool, _StructuredTool, _StructuredTool, _StructuredTool]],
) -> tuple[_StructuredTool, _StructuredTool, _StructuredTool, _StructuredTool]:
    key = str(target_table_id or "").strip()
    if key in cache:
        return cache[key]

    static_text = _build_ag1_static_context_for_target(
        graph=graph,
        target_table_id=key,
    )
    suffix = _sanitize_for_app_name(key)

    pass1_agent = LlmAgent(
        name=f"rule_decision_pass1_agent_{suffix}",
        model=STEP2_MODEL,
        description="Pass-1 inferred chooser for rule + candidates + lookup hypothesis (structured output).",
        static_instruction=static_text,
        instruction=get_rule_decision_prompt(),
        output_schema=RuleCandidateDecisionOutput,
        output_key="rule_decision_pass1",
        generate_content_config=_decision_generate_cfg(),
    )
    pass2_agent = LlmAgent(
        name=f"rule_decision_pass2_agent_{suffix}",
        model=STEP2_MODEL,
        description="Pass-2 inferred refinement/challenger for rule + candidates + lookup hypothesis (structured output).",
        static_instruction=static_text,
        instruction=get_rule_refinement_prompt(),
        output_schema=RuleCandidateDecisionRefinementOutput,
        output_key="rule_decision_pass2",
        generate_content_config=_decision_generate_cfg(),
    )
    self_check_agent = LlmAgent(
        name=f"decision_self_check_agent_{suffix}",
        model=STEP2_MODEL,
        description="Final contradiction/self-check over inferred decision (structured output).",
        static_instruction=static_text,
        instruction=get_decision_self_check_prompt(),
        output_schema=DecisionSelfCheckOutput,
        output_key="decision_self_check",
        generate_content_config=_decision_generate_cfg(),
    )
    lookup_path_selector_agent = LlmAgent(
        name=f"lookup_path_selector_agent_{suffix}",
        model=STEP2_MODEL,
        description="Dedicated AG1 selector for lookup path id (structured output).",
        static_instruction=static_text,
        instruction=get_lookup_path_selection_prompt(),
        output_schema=LookupPathSelectionOutput,
        output_key="lookup_path_selection",
        generate_content_config=_decision_generate_cfg(),
    )

    pass1_tool = _StructuredTool(
        app_name=f"step2_rule_decision_pass1_{suffix}",
        root_agent=pass1_agent,
        output_key="rule_decision_pass1",
        output_model=RuleCandidateDecisionOutput,
        context_cache_config=_context_cache_config(),
    )
    pass2_tool = _StructuredTool(
        app_name=f"step2_rule_decision_pass2_{suffix}",
        root_agent=pass2_agent,
        output_key="rule_decision_pass2",
        output_model=RuleCandidateDecisionRefinementOutput,
        context_cache_config=_context_cache_config(),
    )
    self_check_tool = _StructuredTool(
        app_name=f"step2_decision_self_check_{suffix}",
        root_agent=self_check_agent,
        output_key="decision_self_check",
        output_model=DecisionSelfCheckOutput,
        context_cache_config=_context_cache_config(),
    )
    lookup_path_selector_tool = _StructuredTool(
        app_name=f"step2_lookup_path_selection_{suffix}",
        root_agent=lookup_path_selector_agent,
        output_key="lookup_path_selection",
        output_model=LookupPathSelectionOutput,
        context_cache_config=_context_cache_config(),
    )
    cache[key] = (pass1_tool, pass2_tool, self_check_tool, lookup_path_selector_tool)
    return cache[key]


def _build_source_catalog(ctx) -> tuple[list[SourceCatalogItem], dict[int, SourceCatalogItem]]:
    """
    Build an indexed catalog of all source columns in-scope for this Step 2 run.

    This catalog is used for LLM-safe candidate discovery:
      - The LLM returns indices only.
      - We resolve indices to (file_id, column_name) and validate against Step 1 schemas.
    """
    items: list[SourceCatalogItem] = []
    by_index: dict[int, SourceCatalogItem] = {}

    scoped_source_ids = set(ctx.selected_source_ids or [])
    i = 0
    for src in ctx.shared_state.source_schema.files:
        if scoped_source_ids and src.file_id not in scoped_source_ids:
            continue
        for col in src.columns:
            item = SourceCatalogItem(
                i=i,
                f=src.file_id,
                c=col.physical_name,
                t=getattr(col, "data_type", None),
                ln=getattr(col, "logical_name", None),
                d=getattr(col, "description", None),
            )
            items.append(item)
            by_index[i] = item
            i += 1
    return items, by_index


def _build_policy_manifest(*, allowed_rule_types: list[str], candidate_count: int, lookup_hypothesis_ids: list[str]) -> dict:
    return {
        "allowed_rule_types": allowed_rule_types,
        "allowed_candidate_indices": list(range(max(0, int(candidate_count)))),
        "allowed_lookup_hypothesis_ids": lookup_hypothesis_ids,
        "hard_precedence_applied_upstream": True,
        "must_not_invent_identifiers": True,
        "evidence_priority": [
            "BSA_TABLE_FEEDBACK_HIGH",
            "BSA_QA_FEEDBACK_APPLIED_MED",
            "INDEMAP_HISTORY_MED",
            "PLAYBOOK_TRANSCRIPT_LOW",
        ],
        "validation_rejects_invalid_ids": True,
    }


def _to_candidate_payload(candidates: list[CandidateSource]) -> list[dict]:
    return [
        {
            "index": idx,
            "source_entity_id": c.source_entity.entity_id,
            "source_column_name": c.source_column_name,
            "heuristic_score": c.score,
            "heuristic_reason": c.reason,
            "semantic_similarity": getattr(c, "semantic_similarity", None),
            "datatype_compatibility": getattr(c, "datatype_compatibility", None),
        }
        for idx, c in enumerate(candidates)
    ]


def _build_ag1_lookup_hypotheses_from_paths(
    *,
    graph,
    target_table_id: str,
) -> list[dict]:
    """
    Build AG1 lookup hypotheses from bounded multi-hop graph path options.

    AG1 prompt contract currently expects `lookup_hypotheses[*].hypothesis_id`, so we
    map `path_id -> hypothesis_id` while preserving lookup/path semantics.
    """
    max_hops = max(1, int(getattr(config, "STEP2_AG2_MAX_PATH_HOPS", 3)))
    max_options = max(1, int(getattr(config, "STEP2_AG2_MAX_PATH_OPTIONS", 200)))
    path_options = build_join_path_options_for_target(
        graph=graph,
        target_table_id=target_table_id,
        max_hops=max_hops,
        max_options=max_options,
    )

    hypotheses: list[dict] = []
    for p in path_options:
        pid = str(p.get("path_id") or "").strip()
        if not pid:
            continue
        hypotheses.append(
            {
                "hypothesis_id": pid,
                "target_table_id": target_table_id,
                "lookup_table_id": p.get("lookup_table_id"),
                "hop_count": int(p.get("hop_count") or 0),
                "key_complete": bool(p.get("key_complete", False)),
                "path_summary": p.get("path_summary"),
            }
        )
    return hypotheses


async def _run_rule_pass1(
    *,
    payload: dict,
    tool: _StructuredTool,
) -> RuleCandidateDecisionOutput | None:
    result = await tool.call(payload)
    if not result:
        return None
    return RuleCandidateDecisionOutput.model_validate(result.model_dump())


async def _run_rule_pass2(
    *,
    payload: dict,
    tool: _StructuredTool,
) -> RuleCandidateDecisionRefinementOutput | None:
    result = await tool.call(payload)
    if not result:
        return None
    return RuleCandidateDecisionRefinementOutput.model_validate(result.model_dump())


async def _run_decision_self_check(
    *,
    payload: dict,
    tool: _StructuredTool,
) -> DecisionSelfCheckOutput | None:
    result = await tool.call(payload)
    if not result:
        return None
    return DecisionSelfCheckOutput.model_validate(result.model_dump())


async def _run_lookup_path_selection(
    *,
    payload: dict,
    tool: _StructuredTool,
) -> LookupPathSelectionOutput | None:
    result = await tool.call(payload)
    if not result:
        return None
    return LookupPathSelectionOutput.model_validate(result.model_dump())


async def _run_history_mapping_rerank(
    *,
    payload: dict,
    tool: _StructuredTool,
) -> HistoricalMappingRerankOutput | None:
    result = await tool.call(payload)
    if not result:
        return None
    return HistoricalMappingRerankOutput.model_validate(result.model_dump())


async def _catalog_candidates_for_target(
    ctx,
    *,
    target_table_id: str,
    target_column_name: str,
    target_logical_name: str | None,
    target_description: str | None,
    target_data_type: str | None,
    is_code_column: bool,
    is_surrogate_key: bool,
    target_natural_key_columns: list[str] | None = None,
    allowed_source_file_ids: list[str],
    top_n: int,
    evidence_snippets: list[str] | None,
    tool: _StructuredTool,
) -> CatalogCandidatesOutput | None:
    payload = {
        "interface_code": ctx.shared_state.interface_code,
        "target_table_id": target_table_id,
        "target_column_name": target_column_name,
        "target_logical_name": target_logical_name,
        "target_description": target_description,
        "target_data_type": target_data_type,
        "is_code_column": bool(is_code_column),
        "is_surrogate_key": bool(is_surrogate_key),
        "target_natural_key_columns": target_natural_key_columns or [],
        "allowed_source_file_ids": allowed_source_file_ids,
        "top_n": int(top_n),
        # Evidence is helper-only. Prefer BSA_TABLE_FEEDBACK when it is explicit and schema-valid.
        "evidence_snippets": evidence_snippets or [],
    }
    result = await tool.call(payload)
    if not result:
        return None
    return CatalogCandidatesOutput.model_validate(result.model_dump())


def _validate_decision_indices(
    *,
    indices: list[int],
    candidate_count: int,
) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for idx in indices or []:
        i = int(idx)
        if i < 0 or i >= candidate_count:
            continue
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


async def run_mapping_logic_agent(ctx):
    """
    Entrypoint for MappingLogicAgent.

    Current behavior:
      - Run deterministic heuristics to generate rows/issues
      - For rows that require a source and have multiple candidates, call LLM to re-rank
        candidates (structured output) and update the chosen source.
    """
    rows, issues = run_mapping_logic(ctx)

    max_failures = max(0, int(getattr(config, "STEP2_LLM_MAX_CONSECUTIVE_FAILURES", 3)))

    # ---------------------------------------------------------------------
    # Candidate discovery (Option A): indexed Source Catalog + structured LLM.
    # ---------------------------------------------------------------------
    source_catalog, catalog_by_index = _build_source_catalog(ctx)

    # Build stable static prefix for caching. Keep keys short to reduce token footprint.
    # We do NOT embed full Step 1 SharedState; only the indexed catalog.
    catalog_text = (
        "SOURCE_CATALOG (JSON list). Each item keys:\n"
        "  i = index (int)\n"
        "  f = source file_id (string)\n"
        "  c = source column name (string)\n"
        "  t = source data type (string|null)\n"
        "  ln = logical name (string|null)\n"
        "  d = description (string|null)\n\n"
        f"SOURCE_CATALOG_JSON:\n{json.dumps([it.model_dump() for it in source_catalog], ensure_ascii=False)}\n"
    )

    catalog_candidate_agent = LlmAgent(
        name="catalog_candidate_agent",
        model=STEP2_MODEL,
        description="Finds best source candidates from SOURCE_CATALOG (index-only structured output).",
        static_instruction=catalog_text,
        instruction=get_catalog_candidate_prompt(),
        output_schema=CatalogCandidatesOutput,
        output_key="catalog_candidates",
        generate_content_config=_decision_generate_cfg(),
    )

    sk_natural_key_agent = LlmAgent(
        name="sk_natural_key_agent",
        model=STEP2_MODEL,
        description="Suggests SK natural-key input candidates from SOURCE_CATALOG (index-only structured output).",
        static_instruction=catalog_text,
        instruction=get_sk_natural_key_prompt(),
        output_schema=CatalogCandidatesOutput,
        output_key="sk_natural_key_candidates",
        generate_content_config=_decision_generate_cfg(),
    )

    catalog_tool = _StructuredTool(
        app_name="step2_catalog_candidate_app",
        root_agent=catalog_candidate_agent,
        output_key="catalog_candidates",
        output_model=CatalogCandidatesOutput,
        context_cache_config=_context_cache_config(),
    )

    sk_natural_key_tool = _StructuredTool(
        app_name="step2_sk_natural_key_app",
        root_agent=sk_natural_key_agent,
        output_key="sk_natural_key_candidates",
        output_model=CatalogCandidatesOutput,
        context_cache_config=_context_cache_config(),
    )

    history_enabled = bool(getattr(config, "STEP2_INDEMAP_HISTORY_ENABLED", False))
    history_rerank_tool: _StructuredTool | None = None
    if history_enabled:
        history_rerank_agent = LlmAgent(
            name="history_mapping_rerank_agent",
            model=STEP2_MODEL,
            description="Ranks historical IndeMap mapping candidates for one target column (structured output).",
            instruction=get_history_mapping_rerank_prompt(),
            output_schema=HistoricalMappingRerankOutput,
            output_key="history_mapping_rerank",
            generate_content_config=_decision_generate_cfg(),
        )
        history_rerank_tool = _StructuredTool(
            app_name="step2_history_mapping_rerank_app",
            root_agent=history_rerank_agent,
            output_key="history_mapping_rerank",
            output_model=HistoricalMappingRerankOutput,
            context_cache_config=_context_cache_config(),
        )

    # Fast existence check: (file_id -> set(column_name))
    source_cols_by_file: dict[str, set[str]] = {}
    for f in ctx.shared_state.source_schema.files:
        source_cols_by_file[f.file_id] = {c.physical_name for c in f.columns}

    # Cache BigQuery experience lookups per target column to avoid repeated queries across stages.
    _experience_snips_by_key: dict[str, list[str]] = {}
    _experience_refs_by_key: dict[str, list] = {}
    _history_snips_by_key: dict[str, list[str]] = {}
    _history_refs_by_key: dict[str, list[EvidenceRef]] = {}

    def _get_experience_snippets_for_row(row) -> list[str]:
        if not ctx.rag_enabled:
            return []
        key = f"{row.target_table.entity_id}.{row.target_column_name}"
        if key not in _experience_snips_by_key:
            refs = retrieve_experience_refs_bq(
                target_table_id=row.target_table.entity_id,
                target_column_name=row.target_column_name,
                table_k=int(getattr(config, "STEP2_EVIDENCE_TABLE_FEEDBACK_TOP_K", 3)),
                qa_k=int(getattr(config, "STEP2_EVIDENCE_QA_FEEDBACK_TOP_K", 3)),
            )
            _experience_refs_by_key[key] = refs
            _experience_snips_by_key[key] = [e.snippet for e in refs if getattr(e, "snippet", None)]
        # Attach for transparency (Step2State persists row.evidence_refs).
        refs = _experience_refs_by_key.get(key) or []
        if refs:
            existing = {e.locator for e in (row.evidence_refs or []) if e.locator}
            for r in refs:
                if getattr(r, "locator", None) and r.locator in existing:
                    continue
                row.evidence_refs.append(r)
        return _experience_snips_by_key.get(key, [])

    async def _get_history_snippets_for_row(row: MappingRow, table, tgt_col) -> list[str]:
        if not ctx.rag_enabled or not history_enabled or not history_rerank_tool:
            return []

        key = f"{row.target_table.entity_id}.{row.target_column_name}"
        log_ctx = f"{row.target_table.entity_id}.{row.target_column_name}"
        if key in _history_snips_by_key:
            refs = _history_refs_by_key.get(key) or []
            logger.info(
                "[step2-history] cache_hit target=%s snippets=%s refs=%s",
                log_ctx,
                len(_history_snips_by_key.get(key) or []),
                len(refs),
            )
            if refs:
                existing = {e.locator for e in (row.evidence_refs or []) if e.locator}
                for r in refs:
                    if getattr(r, "locator", None) and r.locator in existing:
                        continue
                    row.evidence_refs.append(r)
            return _history_snips_by_key.get(key, [])

        fetch_top_n = max(1, int(getattr(config, "STEP2_INDEMAP_HISTORY_FETCH_TOP_N", 10)))
        rerank_top_k = max(1, int(getattr(config, "STEP2_INDEMAP_HISTORY_RERANK_TOP_K", 5)))
        keep_top_k = max(1, int(getattr(config, "STEP2_INDEMAP_HISTORY_KEEP_TOP_K", 3)))
        max_snip = max(120, int(getattr(config, "STEP2_INDEMAP_HISTORY_MAX_SNIPPET_CHARS", 800)))
        med_threshold = float(getattr(config, "STEP2_INDEMAP_HISTORY_MED_THRESHOLD", 0.70))
        low_threshold = float(getattr(config, "STEP2_INDEMAP_HISTORY_LOW_THRESHOLD", 0.55))
        fail_open = bool(getattr(config, "STEP2_INDEMAP_HISTORY_FAIL_OPEN", True))

        database_name = str(getattr(table, "database_name", None) or getattr(table, "database", None) or "").strip() or None
        target_table_name = str(getattr(table, "table_name", None) or row.target_table.entity_id).strip()
        target_column_name = str(row.target_column_name or "").strip()

        try:
            raw_payload = await asyncio.to_thread(
                fetch_indemap_past_mappings_for_target,
                database_name=database_name,
                target_table_name=target_table_name,
                target_column_name=target_column_name,
                top_n=fetch_top_n,
            )
        except Exception as exc:
            if not fail_open:
                raise
            logger.warning(
                "Step2 IndeMap history fetch failed for %s.%s: %s",
                row.target_table.entity_id,
                row.target_column_name,
                exc,
            )
            _history_snips_by_key[key] = []
            _history_refs_by_key[key] = []
            return []

        raw_rules = list((raw_payload or {}).get("rules") or [])
        logger.info(
            "[step2-history] fetched target=%s database=%s table=%s column=%s top_n=%s raw_rules=%s",
            log_ctx,
            database_name,
            target_table_name,
            target_column_name,
            fetch_top_n,
            len(raw_rules),
        )
        prefiltered = prefilter_history_rules(
            raw_rules,
            ctx.shared_state.source_schema,
            row.target_table.entity_id,
            row.target_column_name,
            max_keep=rerank_top_k,
        )
        logger.info(
            "[step2-history] prefiltered target=%s rerank_top_k=%s kept=%s",
            log_ctx,
            rerank_top_k,
            len(prefiltered),
        )
        if not prefiltered:
            logger.info("[step2-history] no_candidates_after_prefilter target=%s", log_ctx)
            _history_snips_by_key[key] = []
            _history_refs_by_key[key] = []
            return []

        candidates: list[HistoricalMappingCandidate] = [
            HistoricalMappingCandidate.model_validate(
                {
                    "candidate_id": str(item.get("candidate_id") or "").strip(),
                    "canonical_rule_type": str(item.get("canonical_rule_type") or "UNKNOWN").strip(),
                    "source_hints": item.get("source_hints") or {},
                    "join_text": item.get("join_text"),
                    "filter_text": item.get("filter_text"),
                    "rule_text": item.get("rule_text"),
                    "special_text": item.get("special_text"),
                    "last_updated": item.get("last_updated"),
                    "schema_compatible": item.get("schema_compatible"),
                    "schema_compat_reason": item.get("schema_compat_reason"),
                    "candidate_summary": str(item.get("candidate_summary") or "").strip(),
                }
            )
            for item in prefiltered
            if str(item.get("candidate_id") or "").strip()
        ]
        logger.info(
            "[step2-history] structured_candidates target=%s count=%s",
            log_ctx,
            len(candidates),
        )
        if not candidates:
            logger.info("[step2-history] no_valid_structured_candidates target=%s", log_ctx)
            _history_snips_by_key[key] = []
            _history_refs_by_key[key] = []
            return []

        candidate_ids = {c.candidate_id for c in candidates}
        source_context = []
        for c in list(row.candidate_sources_topk or [])[:5]:
            source_context.append(
                {
                    "source_entity_id": c.source_entity.entity_id,
                    "source_column_name": c.source_column_name,
                    "score": float(c.score or 0.0),
                }
            )

        bsa_snippets = _get_experience_snippets_for_row(row) or []
        rerank_payload = {
            "interface_code": ctx.shared_state.interface_code,
            "target_context": {
                "target_table_id": row.target_table.entity_id,
                "target_column_name": row.target_column_name,
                "target_logical_name": getattr(tgt_col, "logical_attribute_name", None),
                "target_description": getattr(tgt_col, "attribute_description", None),
                "target_data_type": getattr(tgt_col, "data_type", None),
            },
            "source_context": source_context,
            "higher_priority_evidence_snippets": bsa_snippets,
            "historical_candidates": [c.model_dump() for c in candidates],
            "policy_manifest": {
                "allowed_candidate_ids": sorted(candidate_ids),
                "max_selected": keep_top_k,
                "priority_order": [
                    "BSA_TABLE_FEEDBACK_HIGH",
                    "BSA_QA_FEEDBACK_APPLIED_MED",
                    "INDEMAP_HISTORY_MED",
                    "PLAYBOOK_TRANSCRIPT_LOW",
                ],
            },
        }

        rerank_out = await _run_history_mapping_rerank(payload=rerank_payload, tool=history_rerank_tool)
        if not rerank_out:
            logger.info("[step2-history] rerank_empty target=%s", log_ctx)
            _history_snips_by_key[key] = []
            _history_refs_by_key[key] = []
            return []
        logger.info(
            "[step2-history] rerank_done target=%s selected_top_ids=%s scored_items=%s global_conflict=%s needs_review=%s",
            log_ctx,
            len(rerank_out.selected_top_ids or []),
            len(rerank_out.scores or []),
            bool(rerank_out.global_conflict_flag),
            bool(rerank_out.needs_review),
        )

        score_by_id = {
            str(s.candidate_id): (
                float(s.score or 0.0),
                bool(s.conflict_flag),
                (s.conflict_reason or "").strip() or None,
            )
            for s in (rerank_out.scores or [])
            if str(s.candidate_id or "").strip() in candidate_ids
        }

        selected_ids: list[str] = []
        for cid in rerank_out.selected_top_ids or []:
            c = str(cid or "").strip()
            if not c or c not in candidate_ids or c in selected_ids:
                continue
            selected_ids.append(c)
        if not selected_ids:
            ranked_from_scores = sorted(score_by_id.items(), key=lambda kv: kv[1][0], reverse=True)
            selected_ids = [cid for cid, _meta in ranked_from_scores[:keep_top_k]]
            logger.info(
                "[step2-history] selected_from_scores_fallback target=%s keep_top_k=%s selected=%s",
                log_ctx,
                keep_top_k,
                len(selected_ids),
            )
        if keep_top_k > 0:
            selected_ids = selected_ids[:keep_top_k]

        by_id = {c.candidate_id: c for c in candidates}
        refs: list[EvidenceRef] = []
        snippets: list[str] = []
        for cid in selected_ids:
            cand = by_id.get(cid)
            if not cand:
                continue
            score, conflict_flag, conflict_reason = score_by_id.get(cid, (0.0, False, None))
            if score >= med_threshold and not conflict_flag:
                auth = EvidenceAuthorityLevel.MED
            elif score >= low_threshold:
                auth = EvidenceAuthorityLevel.LOW
            else:
                auth = EvidenceAuthorityLevel.LOW

            summary = cand.candidate_summary or ""
            if conflict_reason:
                summary = f"{summary} | conflict_reason={conflict_reason}".strip()

            source_ref = f"{str(database_name or '').strip()}|{target_table_name}|{target_column_name}|{cid}".strip("|")
            snippet = _format_indemap_history_snippet(
                target_table_id=row.target_table.entity_id,
                target_column_name=row.target_column_name,
                candidate_id=cid,
                score=score,
                conflict_flag=conflict_flag,
                summary=summary,
                source_ref=source_ref,
                max_chars=max_snip,
            )
            snippets.append(snippet)
            refs.append(
                EvidenceRef(
                    source=EvidenceSource.EVIDENCE_HUB,
                    evidence_type=EvidenceType.INDEMAP_HISTORY,
                    authority_level=auth,
                    interface_code=ctx.shared_state.interface_code,
                    target_table_id=row.target_table.entity_id,
                    target_column_name=row.target_column_name,
                    source_ref=source_ref,
                    title="IndeMap Historical Mapping",
                    snippet=snippet,
                    locator=f"INDEMAP_HISTORY:{row.target_table.entity_id}.{row.target_column_name}:{cid}",
                    relevance_score=max(0.0, min(1.0, float(score or 0.0))),
                )
            )

        _history_snips_by_key[key] = snippets
        _history_refs_by_key[key] = refs
        logger.info(
            "[step2-history] finalized target=%s kept_ids=%s refs=%s snippets=%s",
            log_ctx,
            len(selected_ids),
            len(refs),
            len(snippets),
        )
        if refs:
            sample_ref = refs[0]
            logger.info(
                "[step2-history] sample_ref target=%s locator=%s authority=%s relevance=%.3f source_ref=%s",
                log_ctx,
                sample_ref.locator,
                sample_ref.authority_level,
                float(sample_ref.relevance_score or 0.0),
                sample_ref.source_ref,
            )

        if refs:
            existing = {e.locator for e in (row.evidence_refs or []) if e.locator}
            for r in refs:
                if getattr(r, "locator", None) and r.locator in existing:
                    continue
                row.evidence_refs.append(r)

        return snippets

    cand_budget = _llm_max_catalog_candidate_calls()
    cand_used = 0
    cand_failures = 0

    def _needs_source_candidates(row) -> bool:
        if row.rule_type in {RuleType.TECHNICAL, RuleType.HARDCODE, RuleType.DEFAULT}:
            return False
        if row.rule_type == RuleType.UNKNOWN:
            return True
        if row.rule_type in {RuleType.DIRECT, RuleType.SUBSTRING, RuleType.CASE, RuleType.IF_ELSE}:
            return True
        if row.rule_type == RuleType.LOOKUP:
            return True  # driving key is still needed even if joins are resolved by AG2
        if row.rule_type == RuleType.SK:
            return False  # handled separately (SK is not a direct source-field mapping)
        return True

    for row in rows:
        if cand_used >= cand_budget:
            break

        # Special handling for SK:
        # - SK is not a direct source-field mapping, so we do not select a single source field as the SK value.
        # - Instead, we optionally propose *natural-key input* source columns that likely define uniqueness.
        if row.rule_type == RuleType.SK:
            table = next(
                (t for t in ctx.shared_state.target_schema.tables if t.table_id == row.target_table.entity_id),
                None,
            )
            tgt_col = None
            if table:
                tgt_col = next((c for c in table.columns if c.attribute_name == row.target_column_name), None)
            if not tgt_col:
                continue

            allowed_source_ids = sorted(
                list(ctx.explicit_source_ids_by_target_table.get(row.target_table.entity_id) or set(ctx.selected_source_ids))
            )
            if not allowed_source_ids:
                allowed_source_ids = sorted([f.file_id for f in ctx.shared_state.source_schema.files])

            # Natural key basis from target metadata (AKs), excluding surrogate-only keys.
            nk_cols: list[str] = []
            surrogate_cols = set()
            if table:
                surrogate_cols = {
                    (c.attribute_name or "")
                    for c in getattr(table, "columns", []) or []
                    if bool(getattr(c, "is_surrogate_key", False))
                }
            if table and getattr(table, "alternate_keys", None):
                for ak in table.alternate_keys or []:
                    for cn in getattr(ak, "column_names", []) or []:
                        if not cn:
                            continue
                        if surrogate_cols and cn in surrogate_cols:
                            continue
                        if cn not in nk_cols:
                            nk_cols.append(cn)

            result = await _catalog_candidates_for_target(
                ctx,
                target_table_id=row.target_table.entity_id,
                target_column_name=row.target_column_name,
                target_logical_name=getattr(tgt_col, "logical_attribute_name", None),
                target_description=getattr(tgt_col, "attribute_description", None),
                target_data_type=getattr(tgt_col, "data_type", None),
                is_code_column=False,
                is_surrogate_key=True,
                target_natural_key_columns=nk_cols,
                allowed_source_file_ids=allowed_source_ids,
                top_n=min(int(getattr(config, "STEP2_CANDIDATE_TOP_N", 8)), 8),
                evidence_snippets=_get_experience_snippets_for_row(row),
                tool=sk_natural_key_tool,
            )
            if not result:
                cand_failures += 1
                if max_failures and cand_failures >= max_failures:
                    break
                continue

            cand_used += 1
            cand_failures = 0

            valid_candidates: list[CandidateSource] = []
            seen: set[int] = set()
            allowed_set = set(allowed_source_ids)
            for item in result.candidates:
                idx = int(item.index)
                if idx in seen:
                    continue
                seen.add(idx)
                meta = catalog_by_index.get(idx)
                if not meta:
                    continue
                if allowed_set and meta.f not in allowed_set:
                    continue
                if meta.c not in source_cols_by_file.get(meta.f, set()):
                    continue
                valid_candidates.append(
                    CandidateSource(
                        source_entity={"entity_type": "SOURCE_FILE", "entity_id": meta.f},
                        source_column_name=meta.c,
                        score=float(item.match_score),
                        semantic_similarity=float(item.match_score),
                        reason=(item.rationale or "").strip() or "SK natural key candidate",
                    )
                )

            valid_candidates.sort(key=lambda c: c.score, reverse=True)
            row.candidate_sources_topk = valid_candidates[: int(getattr(config, "STEP2_CANDIDATE_TOP_N", 8))] or None

            # Document SK intent and missing specifics explicitly.
            note_parts: list[str] = [
                "SK intent: SK creation is a map-table style process; SK value is not a direct move from a single source field."
            ]
            if nk_cols:
                note_parts.append(f"Uniqueness basis (target AK/composite columns): {', '.join(nk_cols)}.")
            else:
                note_parts.append("Uniqueness basis (AK/composite key) missing in target metadata; requires HITL.")
            if row.candidate_sources_topk:
                top = [f"{c.source_entity.entity_id}.{c.source_column_name}" for c in row.candidate_sources_topk[:5]]
                note_parts.append(f"Proposed natural-key source candidates (not final): {', '.join(top)}.")
            sk_note = " ".join([p for p in note_parts if p]).strip()
            if sk_note:
                if row.special_considerations_text:
                    row.special_considerations_text = (row.special_considerations_text + " | " + sk_note)[:2000]
                else:
                    row.special_considerations_text = sk_note[:2000]

            # Only expose a *set* of potential natural-key source fields when we can propose at least
            # two columns from the same source entity. Otherwise we keep the selection in top-k form
            # to avoid implying a finalized SK uniqueness definition.
            row.source_entity = None
            row.source_field_names = []
            if row.candidate_sources_topk and len(row.candidate_sources_topk) >= 2:
                first_entity = row.candidate_sources_topk[0].source_entity.entity_id
                same_entity = [c for c in row.candidate_sources_topk if c.source_entity.entity_id == first_entity]
                if len(same_entity) >= 2:
                    row.source_entity = row.candidate_sources_topk[0].source_entity
                    row.source_field_names = [c.source_column_name for c in same_entity[:4]]

            # If we could not propose any natural-key candidates, raise an issue.
            if not row.candidate_sources_topk:
                issue_id = f"ISSUE_SK_NK_{row.target_table.entity_id}_{row.target_column_name}"
                issues.append(
                    OpenIssue(
                        issue_id=issue_id,
                        issue_type=IssueType.MISSING_SOURCE_FIELD,
                        severity=IssueSeverity.WARN,
                        target_column={
                            "entity_type": "TARGET_TABLE",
                            "entity_id": row.target_table.entity_id,
                            "column_name": row.target_column_name,
                        },
                        message="SK detected but could not propose natural-key input source fields from the catalog.",
                        suggested_question="Which source columns form the natural key used for SK creation?",
                        created_by="MappingLogicAgent",
                        evidence_refs=[],
                    )
                )
                row.open_issue_ids.append(issue_id)
                row.needs_review = True

            continue

        if not _needs_source_candidates(row):
            continue

        table = next((t for t in ctx.shared_state.target_schema.tables if t.table_id == row.target_table.entity_id), None)
        tgt_col = None
        if table:
            tgt_col = next((c for c in table.columns if c.attribute_name == row.target_column_name), None)
        if not tgt_col:
            continue

        allowed_source_ids = sorted(
            list(ctx.explicit_source_ids_by_target_table.get(row.target_table.entity_id) or set(ctx.selected_source_ids))
        )
        if not allowed_source_ids:
            allowed_source_ids = sorted([f.file_id for f in ctx.shared_state.source_schema.files])

        result = await _catalog_candidates_for_target(
            ctx,
            target_table_id=row.target_table.entity_id,
            target_column_name=row.target_column_name,
            target_logical_name=getattr(tgt_col, "logical_attribute_name", None),
            target_description=getattr(tgt_col, "attribute_description", None),
            target_data_type=getattr(tgt_col, "data_type", None),
            is_code_column=bool(getattr(tgt_col, "is_code_column", False)),
            is_surrogate_key=bool(getattr(tgt_col, "is_surrogate_key", False)),
            allowed_source_file_ids=allowed_source_ids,
            top_n=int(getattr(config, "STEP2_CANDIDATE_TOP_N", 8)),
            evidence_snippets=_get_experience_snippets_for_row(row),
            tool=catalog_tool,
        )
        if not result:
            cand_failures += 1
            if max_failures and cand_failures >= max_failures:
                break
            continue

        cand_used += 1
        cand_failures = 0

        # Post-validation: indices must exist, belong to allowed sources, and correspond to real schema columns.
        valid_candidates: list[CandidateSource] = []
        seen: set[int] = set()
        for item in result.candidates:
            idx = int(item.index)
            if idx in seen:
                continue
            seen.add(idx)
            meta = catalog_by_index.get(idx)
            if not meta:
                continue
            if allowed_source_ids and meta.f not in set(allowed_source_ids):
                continue
            if meta.c not in source_cols_by_file.get(meta.f, set()):
                continue
            valid_candidates.append(
                CandidateSource(
                    source_entity={"entity_type": "SOURCE_FILE", "entity_id": meta.f},
                    source_column_name=meta.c,
                    score=float(item.match_score),
                    semantic_similarity=float(item.match_score),
                    reason=(item.rationale or "").strip() or "Catalog match",
                )
            )

        # Ensure best candidate is first by score.
        valid_candidates.sort(key=lambda c: c.score, reverse=True)
        row.candidate_sources_topk = valid_candidates[: int(getattr(config, "STEP2_CANDIDATE_TOP_N", 8))] or None

        if row.candidate_sources_topk:
            row.source_entity = row.candidate_sources_topk[0].source_entity
            row.source_field_names = [row.candidate_sources_topk[0].source_column_name]
            row.confidence_score = max(row.confidence_score, min(0.95, row.candidate_sources_topk[0].score))
        else:
            issue_id = f"ISSUE_SRC_{row.target_table.entity_id}_{row.target_column_name}"
            issues.append(
                OpenIssue(
                    issue_id=issue_id,
                    issue_type=IssueType.MISSING_SOURCE_FIELD,
                    severity=IssueSeverity.WARN,
                    target_column={
                        "entity_type": "TARGET_TABLE",
                        "entity_id": row.target_table.entity_id,
                        "column_name": row.target_column_name,
                    },
                    message="No suitable source candidate found in catalog selection.",
                    suggested_question="Which source field populates this target column?",
                    created_by="MappingLogicAgent",
                    evidence_refs=[],
                )
            )
            row.open_issue_ids.append(issue_id)
            row.needs_review = True

    # LLM-major inferred chooser (pass1 -> pass2 -> self-check) with deterministic guardrails.
    decision_budget = _llm_max_rule_decisions()
    decision_used = 0
    decision_failures = 0
    decision_tools_by_target_table: dict[str, tuple[_StructuredTool, _StructuredTool, _StructuredTool, _StructuredTool]] = {}

    all_inferred_allowed = [
        RuleType.DIRECT,
        RuleType.LOOKUP,
        RuleType.SK,
        RuleType.SUBSTRING,
        RuleType.CASE,
        RuleType.IF_ELSE,
        RuleType.UNKNOWN,
    ]

    table_lookup = {t.table_id: t for t in ctx.shared_state.target_schema.tables}

    def _add_issue_if_missing(row: MappingRow, issue: OpenIssue) -> None:
        if issue.issue_id not in {i.issue_id for i in issues}:
            issues.append(issue)
        if issue.issue_id not in row.open_issue_ids:
            row.open_issue_ids.append(issue.issue_id)

    def _has_strong_non_direct_signal(snippets: list[str]) -> bool:
        """
        Detect strong evidence cues that indicate non-DIRECT logic is required.
        """
        keywords = ("lookup", "crosswalk", "xwalk", "join ", "derive", "translation", "translate", "code table")
        for snip in snippets or []:
            s = str(snip or "").lower()
            if "|high]" not in s and "|med]" not in s:
                continue
            if any(k in s for k in keywords):
                return True
        return False

    if bool(getattr(config, "STEP2_LLM_INFERRED_RULES_ENABLED", True)):
        for row in rows:
            if decision_used >= decision_budget:
                break
            if row.rule_type_source != "INFERRED":
                continue
            # Deterministic forced technical path is already final.
            if row.rule_type == RuleType.TECHNICAL and (row.forced_reason or "").startswith("Technical/system"):
                continue

            table = table_lookup.get(row.target_table.entity_id)
            tgt_col = None
            if table:
                tgt_col = next((c for c in table.columns if c.attribute_name == row.target_column_name), None)
            if not tgt_col:
                continue

            pass1_tool, pass2_tool, decision_self_check_tool, lookup_path_selector_tool = _get_or_create_decision_tools_for_target_table(
                target_table_id=row.target_table.entity_id,
                graph=ctx.shared_state.data_model_graph,
                cache=decision_tools_by_target_table,
            )

            decision_used += 1
            candidates = list(row.candidate_sources_topk or [])
            candidate_payload = _to_candidate_payload(candidates)

            lookup_hypotheses = []
            if bool(getattr(config, "STEP2_GRAPH_HYPOTHESES_ENABLED", True)):
                lookup_hypotheses = _build_ag1_lookup_hypotheses_from_paths(
                    graph=ctx.shared_state.data_model_graph,
                    target_table_id=row.target_table.entity_id,
                )
            hypothesis_ids = [str(h.get("hypothesis_id") or "").strip() for h in lookup_hypotheses if h.get("hypothesis_id")]
            hypothesis_ids = [hid for hid in hypothesis_ids if hid]
            hypothesis_id_set = set(hypothesis_ids)

            experience_snippets = _get_experience_snippets_for_row(row) or []
            history_snippets: list[str] = []
            if history_enabled:
                history_snippets = await _get_history_snippets_for_row(row, table, tgt_col)

            evidence_refs = []
            if ctx.rag_enabled:
                evidence_refs = await retrieve_evidence_pack(
                    interface_code=ctx.shared_state.interface_code,
                    target_table_id=row.target_table.entity_id,
                    target_column_name=row.target_column_name,
                    target_logical_name=getattr(tgt_col, "logical_attribute_name", None),
                    target_description=getattr(tgt_col, "attribute_description", None),
                    target_data_type=getattr(tgt_col, "data_type", None),
                    target_key="A" if getattr(tgt_col, "alternate_key_groups", None) else None,
                    forced_rule_type=None,
                )
            vector_snippets = [
                e.snippet
                for e in evidence_refs
                if getattr(e, "snippet", None)
                and str(getattr(e, "evidence_type", "") or "").strip().upper() in {"PLAYBOOK", "TRANSCRIPT"}
            ]
            # Priority order: BSA experience (HIGH/MED) -> IndeMap history (MED/LOW) -> vector evidence (LOW).
            evidence_snippets = list(dict.fromkeys(experience_snippets + history_snippets + vector_snippets))
            if evidence_refs:
                existing = {e.locator for e in (row.evidence_refs or []) if e.locator}
                for er in evidence_refs:
                    if er.locator and er.locator in existing:
                        continue
                    row.evidence_refs.append(er)

            allowed_rule_types = list(all_inferred_allowed)
            case_gate_enabled = bool(getattr(config, "STEP2_CASE_IFELSE_STRICT_GATE_ENABLED", True))
            case_ifelse_allowed = True
            if case_gate_enabled:
                target_key = normalize_target_key(row.target_table.entity_id, row.target_column_name)
                instruction_hints: list[str] = []
                override_reason = (ctx.rule_type_override_reasons or {}).get(target_key)
                if override_reason:
                    instruction_hints.append(override_reason)
                mapping_notes = getattr(getattr(ctx.shared_state, "mapping_context", None), "notes", None)
                if mapping_notes:
                    instruction_hints.append(str(mapping_notes))
                row_filter_text = (row.row_filter_text or "").strip()
                if row_filter_text:
                    instruction_hints.append(row_filter_text)

                case_ifelse_allowed = is_case_ifelse_eligible(
                    target_logical_name=getattr(tgt_col, "logical_attribute_name", None),
                    target_description=getattr(tgt_col, "attribute_description", None),
                    evidence_snippets=evidence_snippets,
                    instruction_hints=instruction_hints,
                )
                if not case_ifelse_allowed:
                    allowed_rule_types = [r for r in allowed_rule_types if r not in {RuleType.CASE, RuleType.IF_ELSE}]

            policy_manifest = _build_policy_manifest(
                allowed_rule_types=[r.value for r in allowed_rule_types],
                candidate_count=len(candidate_payload),
                lookup_hypothesis_ids=hypothesis_ids,
            )

            common_payload = {
                "interface_code": ctx.shared_state.interface_code,
                "target_table_id": row.target_table.entity_id,
                "target_column_name": row.target_column_name,
                "target_logical_name": getattr(tgt_col, "logical_attribute_name", None),
                "target_description": getattr(tgt_col, "attribute_description", None),
                "target_data_type": getattr(tgt_col, "data_type", None),
                "is_code_column": bool(getattr(tgt_col, "is_code_column", False)),
                "is_surrogate_key": bool(getattr(tgt_col, "is_surrogate_key", False)),
                "source_candidates": candidate_payload,
                "lookup_hypotheses": lookup_hypotheses,
                "evidence_snippets": evidence_snippets,
                "policy_manifest": policy_manifest,
                "decision_manifest": policy_manifest,
            }

            pass1 = await _run_rule_pass1(payload=common_payload, tool=pass1_tool)
            if not pass1:
                decision_failures += 1
                if max_failures and decision_failures >= max_failures:
                    break
                continue

            refined = RuleCandidateDecisionRefinementOutput(
                thought_process=getattr(pass1, "thought_process", "") or "",
                selected_rule_type=pass1.selected_rule_type,
                selected_source_candidate_indices=list(pass1.selected_source_candidate_indices or []),
                selected_lookup_hypothesis_id=pass1.selected_lookup_hypothesis_id,
                confidence=float(pass1.confidence or 0.0),
                needs_review=bool(pass1.needs_review),
                decision_basis=pass1.decision_basis,
                conflict_flags=list(pass1.conflict_flags or []),
                reasoning_summary=pass1.reasoning_summary or "",
            )

            if bool(getattr(config, "STEP2_LLM_TWO_PASS_ENABLED", True)):
                pass2 = await _run_rule_pass2(
                    payload={**common_payload, "pass_1_decision": pass1.model_dump()},
                    tool=pass2_tool,
                )
                if pass2:
                    refined = pass2

            # If CASE/IF_ELSE is not eligible for this row, force one non-CASE refinement pass
            # before coercing to UNKNOWN. This avoids unnecessary UNKNOWN regressions.
            if case_gate_enabled and (not case_ifelse_allowed) and refined.selected_rule_type in {RuleType.CASE, RuleType.IF_ELSE}:
                non_case_allowed = [r for r in allowed_rule_types if r not in {RuleType.CASE, RuleType.IF_ELSE}]
                non_case_policy_manifest = _build_policy_manifest(
                    allowed_rule_types=[r.value for r in non_case_allowed],
                    candidate_count=len(candidate_payload),
                    lookup_hypothesis_ids=hypothesis_ids,
                )
                retry_refined = await _run_rule_pass2(
                    payload={
                        **common_payload,
                        "policy_manifest": non_case_policy_manifest,
                        "pass_1_decision": refined.model_dump(),
                        "retry_reason": "CASE_IFELSE_BLOCKED_RESELECT_NON_CASE",
                    },
                    tool=pass2_tool,
                )
                if retry_refined:
                    refined = retry_refined
                    policy_manifest = non_case_policy_manifest

            decision_failures = 0

            selected_rule = refined.selected_rule_type
            selected_indices = _validate_decision_indices(
                indices=list(refined.selected_source_candidate_indices or []),
                candidate_count=len(candidate_payload),
            )
            selected_hyp_raw = str(refined.selected_lookup_hypothesis_id or "").strip()
            selected_hyp_id = selected_hyp_raw if selected_hyp_raw in hypothesis_id_set else None

            if selected_rule not in set(all_inferred_allowed):
                _add_issue_if_missing(
                    row,
                    OpenIssue(
                        issue_id=f"ISSUE_RULE_INVALID_{row.target_table.entity_id}_{row.target_column_name}",
                        issue_type=IssueType.AMBIGUOUS_MAPPING,
                        severity=IssueSeverity.WARN,
                        target_column={
                            "entity_type": "TARGET_TABLE",
                            "entity_id": row.target_table.entity_id,
                            "column_name": row.target_column_name,
                        },
                        message=f"Inferred chooser returned unsupported rule type '{selected_rule.value}'. Coerced to UNKNOWN.",
                        suggested_question="Confirm the correct rule type for this target column.",
                        created_by="MappingLogicAgent",
                        evidence_refs=[],
                    ),
                )
                selected_rule = RuleType.UNKNOWN
                selected_indices = []
                selected_hyp_id = None

            if case_gate_enabled and (not case_ifelse_allowed) and selected_rule in {RuleType.CASE, RuleType.IF_ELSE}:
                _add_issue_if_missing(
                    row,
                    OpenIssue(
                        issue_id=f"ISSUE_RULE_BLOCKED_CASE_{row.target_table.entity_id}_{row.target_column_name}",
                        issue_type=IssueType.AMBIGUOUS_MAPPING,
                        severity=IssueSeverity.WARN,
                        target_column={
                            "entity_type": "TARGET_TABLE",
                            "entity_id": row.target_table.entity_id,
                            "column_name": row.target_column_name,
                        },
                        message="CASE/IF_ELSE was blocked because explicit branching cues were not found; non-CASE re-selection remained unresolved.",
                        suggested_question="Confirm the non-CASE rule for this target column (DIRECT/LOOKUP/SK/SUBSTRING/UNKNOWN).",
                        created_by="MappingLogicAgent",
                        evidence_refs=[],
                    ),
                )
                selected_rule = RuleType.UNKNOWN
                selected_indices = []
                selected_hyp_id = None

            # Dedicated AG1 path selector call for LOOKUP when path is still missing.
            has_key_complete_hypothesis = any(bool(h.get("key_complete", False)) for h in lookup_hypotheses)
            path_selector_note: str | None = None
            path_selector_needs_review = False
            if selected_rule == RuleType.LOOKUP and (not selected_hyp_id) and has_key_complete_hypothesis:
                selected_source_candidates = [candidate_payload[i] for i in selected_indices if 0 <= i < len(candidate_payload)]
                path_selector_payload = {
                    "interface_code": ctx.shared_state.interface_code,
                    "target_context": {
                        "target_table_id": row.target_table.entity_id,
                        "target_column_name": row.target_column_name,
                        "target_logical_name": getattr(tgt_col, "logical_attribute_name", None),
                        "target_description": getattr(tgt_col, "attribute_description", None),
                        "target_data_type": getattr(tgt_col, "data_type", None),
                    },
                    "source_context": {
                        "selected_source_candidate_indices": selected_indices,
                        "selected_source_candidates": selected_source_candidates,
                    },
                    "lookup_hypotheses": lookup_hypotheses,
                    "key_complete_lookup_hypotheses": [h for h in lookup_hypotheses if bool(h.get("key_complete", False))],
                    "evidence_snippets": evidence_snippets,
                    "policy_manifest": {
                        "allowed_lookup_hypothesis_ids": hypothesis_ids,
                        "must_select_from_options_only": True,
                        "must_choose_if_key_complete_exists": True,
                    },
                }
                lookup_selection = await _run_lookup_path_selection(
                    payload=path_selector_payload,
                    tool=lookup_path_selector_tool,
                )
                if lookup_selection:
                    selected_from_selector = str(lookup_selection.selected_lookup_hypothesis_id or "").strip()
                    if selected_from_selector and selected_from_selector in hypothesis_id_set:
                        selected_hyp_id = selected_from_selector
                    else:
                        path_selector_needs_review = True
                    if lookup_selection.rejection_reason:
                        path_selector_note = lookup_selection.rejection_reason.strip()
                    elif lookup_selection.reasoning_summary:
                        path_selector_note = lookup_selection.reasoning_summary.strip()
                    elif not selected_hyp_id:
                        path_selector_note = "No defensible lookup path was selected from key-complete options."
                    if lookup_selection.needs_review:
                        path_selector_needs_review = True

            # Deterministic guardrail: invalid lookup hypothesis id is rejected and flagged.
            if selected_hyp_raw and not selected_hyp_id:
                _add_issue_if_missing(
                    row,
                    OpenIssue(
                        issue_id=f"ISSUE_LOOKUP_HYP_{row.target_table.entity_id}_{row.target_column_name}",
                        issue_type=IssueType.AMBIGUOUS_MAPPING,
                        severity=IssueSeverity.WARN,
                        target_column={
                            "entity_type": "TARGET_TABLE",
                            "entity_id": row.target_table.entity_id,
                            "column_name": row.target_column_name,
                        },
                        message="Selected lookup hypothesis is not valid for this target column context.",
                        suggested_question="Confirm the correct lookup path for this target column.",
                        created_by="MappingLogicAgent",
                        evidence_refs=[],
                    ),
                )

            # Stabilization guard:
            # Avoid unnecessary DIRECT -> UNKNOWN regressions when DIRECT remains the strongest defensible choice.
            direct_recovery_applied = False
            direct_recovery_note: str | None = None
            strong_direct_threshold = float(getattr(config, "STEP2_DIRECT_RECOVERY_MIN_SCORE", 0.90))
            if selected_rule == RuleType.UNKNOWN and candidates:
                top_candidate = candidates[0]
                top_score = float(getattr(top_candidate, "score", 0.0) or 0.0)
                target_is_code = bool(getattr(tgt_col, "is_code_column", False))
                target_is_surrogate = bool(getattr(tgt_col, "is_surrogate_key", False))
                non_direct_signal = _has_strong_non_direct_signal(evidence_snippets)
                if (
                    top_score >= strong_direct_threshold
                    and not target_is_code
                    and not target_is_surrogate
                    and not selected_hyp_id
                    and not non_direct_signal
                ):
                    selected_rule = RuleType.DIRECT
                    selected_indices = [0]
                    direct_recovery_applied = True
                    direct_recovery_note = (
                        "Recovered UNKNOWN to DIRECT because a strong source match exists and no strong "
                        "non-DIRECT signal/path is available."
                    )
                    _add_issue_if_missing(
                        row,
                        OpenIssue(
                            issue_id=f"ISSUE_DIRECT_VALIDATE_{row.target_table.entity_id}_{row.target_column_name}",
                            issue_type=IssueType.CONFLICTING_EVIDENCE,
                            severity=IssueSeverity.WARN,
                            target_column={
                                "entity_type": "TARGET_TABLE",
                                "entity_id": row.target_table.entity_id,
                                "column_name": row.target_column_name,
                            },
                            message=(
                                "Direct mapping retained with review required due cautionary evidence. "
                                "Validate sample/source alignment before finalization."
                            ),
                            suggested_question="Confirm direct mapping is correct after sample-value validation.",
                            created_by="MappingLogicAgent",
                            evidence_refs=[],
                        ),
                    )

            check_decision = refined.model_copy(
                update={
                    "selected_rule_type": selected_rule,
                    "selected_source_candidate_indices": selected_indices,
                    "selected_lookup_hypothesis_id": selected_hyp_id,
                }
            )
            decision_self_check = await _run_decision_self_check(
                payload={
                    "interface_code": ctx.shared_state.interface_code,
                    "target_table_id": row.target_table.entity_id,
                    "target_column_name": row.target_column_name,
                    "refined_decision": check_decision.model_dump(),
                    "policy_manifest": policy_manifest,
                    "decision_manifest": policy_manifest,
                    "evidence_snippets": evidence_snippets,
                },
                tool=decision_self_check_tool,
            )

            row.rule_type = selected_rule
            row.reasoning_summary = (refined.reasoning_summary or row.reasoning_summary or "").strip()[:500]
            if direct_recovery_applied and direct_recovery_note:
                row.reasoning_summary = (f"{row.reasoning_summary} {direct_recovery_note}".strip())[:500]
            if refined.conflict_flags:
                flags_text = ", ".join(sorted({str(f).strip() for f in (refined.conflict_flags or []) if str(f).strip()}))
                if flags_text:
                    note = f"Decision conflict flags: {flags_text}"
                    row.special_considerations_text = (
                        note
                        if not row.special_considerations_text
                        else f"{row.special_considerations_text} | {note}"
                    )[:2000]
            if selected_rule == RuleType.LOOKUP and path_selector_note and not selected_hyp_id:
                note = f"AG1 path selector: {path_selector_note}"
                row.special_considerations_text = (
                    note
                    if not row.special_considerations_text
                    else f"{row.special_considerations_text} | {note}"
                )[:2000]
            conf = float(refined.confidence or 0.0)
            if decision_self_check:
                conf += float(decision_self_check.confidence_delta or 0.0)
            row.confidence_score = max(0.0, min(1.0, conf))
            row.needs_review = bool(refined.needs_review)
            if path_selector_needs_review:
                row.needs_review = True
            if direct_recovery_applied:
                row.needs_review = True

            if decision_self_check and decision_self_check.needs_review:
                row.needs_review = True
            if decision_self_check and decision_self_check.contradiction_found and decision_self_check.issue_message:
                _add_issue_if_missing(
                    row,
                    OpenIssue(
                        issue_id=f"ISSUE_DECISION_{row.target_table.entity_id}_{row.target_column_name}",
                        issue_type=IssueType.CONFLICTING_EVIDENCE,
                        severity=IssueSeverity.WARN,
                        target_column={
                            "entity_type": "TARGET_TABLE",
                            "entity_id": row.target_table.entity_id,
                            "column_name": row.target_column_name,
                        },
                        message=decision_self_check.issue_message,
                        suggested_question=decision_self_check.question_text,
                        created_by="MappingLogicAgent",
                        evidence_refs=[],
                    ),
                )
                row.needs_review = True

        # Apply source selection by rule type.
            if selected_rule in {RuleType.DIRECT, RuleType.SUBSTRING, RuleType.CASE, RuleType.IF_ELSE}:
                row.source_entity = None
                row.source_field_names = []
                if selected_indices:
                    first = candidates[selected_indices[0]]
                    row.source_entity = first.source_entity
                    row.source_field_names = [first.source_column_name]
            elif selected_rule == RuleType.LOOKUP:
                row.source_entity = None
                row.source_field_names = []
                if selected_indices:
                    first = candidates[selected_indices[0]]
                    first_entity = first.source_entity.entity_id
                    same_entity = [candidates[i] for i in selected_indices if candidates[i].source_entity.entity_id == first_entity]
                    cross_entity = [candidates[i] for i in selected_indices if candidates[i].source_entity.entity_id != first_entity]
                    if same_entity:
                        row.source_entity = same_entity[0].source_entity
                        row.source_field_names = [c.source_column_name for c in same_entity[:4]]
                    if cross_entity:
                        _add_issue_if_missing(
                            row,
                            OpenIssue(
                                issue_id=f"ISSUE_LOOKUP_XENTITY_{row.target_table.entity_id}_{row.target_column_name}",
                                issue_type=IssueType.AMBIGUOUS_MAPPING,
                                severity=IssueSeverity.WARN,
                                target_column={
                                    "entity_type": "TARGET_TABLE",
                                    "entity_id": row.target_table.entity_id,
                                    "column_name": row.target_column_name,
                                },
                                message="LOOKUP selected with cross-entity driving keys; only same-entity composite keys are auto-applied.",
                                suggested_question="Confirm the lookup driving key source entity/columns.",
                                created_by="MappingLogicAgent",
                                evidence_refs=[],
                            ),
                        )
                        row.needs_review = True
            elif selected_rule == RuleType.SK:
                row.source_entity = None
                row.source_field_names = []
                if selected_indices:
                    first = candidates[selected_indices[0]]
                    same_entity = [candidates[i] for i in selected_indices if candidates[i].source_entity.entity_id == first.source_entity.entity_id]
                    if same_entity:
                        row.source_entity = first.source_entity
                        row.source_field_names = [c.source_column_name for c in same_entity[:4]]
            else:
                row.source_entity = None
                row.source_field_names = []

        # Missing source for source-dependent rules => explicit issue.
            if selected_rule in {RuleType.DIRECT, RuleType.LOOKUP, RuleType.SUBSTRING, RuleType.CASE, RuleType.IF_ELSE, RuleType.SK}:
                if not row.source_entity or not row.source_field_names:
                    _add_issue_if_missing(
                        row,
                        OpenIssue(
                            issue_id=f"ISSUE_SRC_{row.target_table.entity_id}_{row.target_column_name}",
                            issue_type=IssueType.MISSING_SOURCE_FIELD,
                            severity=IssueSeverity.WARN,
                            target_column={
                                "entity_type": "TARGET_TABLE",
                                "entity_id": row.target_table.entity_id,
                                "column_name": row.target_column_name,
                            },
                            message="Rule requires source selection, but no valid source candidate was selected.",
                            suggested_question="Which source entity/column should populate this target column?",
                            created_by="MappingLogicAgent",
                            evidence_refs=[],
                        ),
                    )
                    row.needs_review = True

        # Lookup hypothesis application.
            row.selected_lookup_hypothesis_id = None
            if selected_rule == RuleType.LOOKUP:
                row.selected_lookup_hypothesis_id = selected_hyp_id
                require_lookup_path = bool(getattr(config, "STEP2_AG1_REQUIRE_LOOKUP_PATH_ID", True))
                if require_lookup_path and has_key_complete_hypothesis and not row.selected_lookup_hypothesis_id:
                    _add_issue_if_missing(
                        row,
                        OpenIssue(
                            issue_id=f"ISSUE_LOOKUP_PATH_REQUIRED_{row.target_table.entity_id}_{row.target_column_name}",
                            issue_type=IssueType.JOIN_UNKNOWN,
                            severity=IssueSeverity.WARN,
                            target_column={
                                "entity_type": "TARGET_TABLE",
                                "entity_id": row.target_table.entity_id,
                                "column_name": row.target_column_name,
                            },
                            message="LOOKUP selected but no lookup path_id was chosen even though key-complete options exist.",
                            suggested_question="Select the correct lookup path from available graph options.",
                            created_by="MappingLogicAgent",
                            evidence_refs=[],
                        ),
                    )
                    row.needs_review = True
                if selected_hyp_id:
                    chosen_hyp = next((h for h in lookup_hypotheses if h.get("hypothesis_id") == selected_hyp_id), None)
                    lookup_table_id = str((chosen_hyp or {}).get("lookup_table_id") or "").strip()
                    if lookup_table_id:
                        row.lookup_tables = [
                            EntityRef(entity_type="TARGET_TABLE", entity_id=lookup_table_id)
                        ]
                elif not row.lookup_tables:
                    _add_issue_if_missing(
                        row,
                        OpenIssue(
                            issue_id=f"ISSUE_JOIN_{row.target_table.entity_id}_{row.target_column_name}",
                            issue_type=IssueType.JOIN_UNKNOWN,
                            severity=IssueSeverity.WARN,
                            target_column={
                                "entity_type": "TARGET_TABLE",
                                "entity_id": row.target_table.entity_id,
                                "column_name": row.target_column_name,
                            },
                            message="LOOKUP selected but no valid lookup hypothesis or explicit lookup rule is available.",
                            suggested_question="Provide the correct lookup path (table + join keys).",
                            created_by="MappingLogicAgent",
                            evidence_refs=[],
                        ),
                    )
                    row.needs_review = True
            else:
                row.lookup_tables = []

    # Multi-rule generation: expand CASE/IF_ELSE into RULE_1/RULE_2/... entries.
    if bool(getattr(config, "STEP2_LLM_ENFORCE_BUDGETS", False)):
        multi_budget = max(0, int(getattr(config, "STEP2_LLM_MAX_MULTI_RULE_CALLS", 10)))
    else:
        multi_budget = 1_000_000
    multi_used = 0
    multi_failures = 0
    multi_disabled = False
    multi_tool = _StructuredTool(
        app_name="step2_multi_rule_app",
        root_agent=multi_rule_agent,
        output_key="multi_rule",
        output_model=MultiRuleOutput,
        context_cache_config=_context_cache_config(),
    )

    expanded = []
    for row in rows:
        if multi_disabled:
            expanded.append(row)
            continue
        if row.rule_type not in {RuleType.CASE, RuleType.IF_ELSE}:
            expanded.append(row)
            continue
        if not row.candidate_sources_topk or multi_used >= multi_budget:
            row.needs_review = True
            expanded.append(row)
            continue

        payload = {
            "interface_code": ctx.shared_state.interface_code,
            "target_table_id": row.target_table.entity_id,
            "target_column_name": row.target_column_name,
            "rule_type": row.rule_type.value,
            "candidates": [
                {
                    "index": idx,
                    "source_entity_id": c.source_entity.entity_id,
                    "source_column_name": c.source_column_name,
                    "heuristic_score": c.score,
                    "semantic_similarity": getattr(c, "semantic_similarity", None),
                }
                for idx, c in enumerate(row.candidate_sources_topk)
            ],
        }
        result = await multi_tool.call(payload)
        multi_used += 1
        if not result:
            multi_failures += 1
            if max_failures and multi_failures >= max_failures:
                row.needs_review = True
                expanded.append(row)
                multi_disabled = True
                continue
            row.needs_review = True
            expanded.append(row)
            continue
        multi_failures = 0

        out = MultiRuleOutput.model_validate(result.model_dump())
        multi_issue_id: str | None = None
        require_concrete = bool(getattr(config, "STEP2_MULTI_RULE_REQUIRE_CONCRETE", True))
        min_instances = max(2, int(getattr(config, "STEP2_MULTI_RULE_MIN_INSTANCES", 2)))
        normalized_instances: list[dict] = []

        if out.instances:
            normalized_instances = [inst.model_dump() for inst in out.instances]
        if require_concrete:
            normalized_instances = validate_multi_rule_concreteness(
                instances=normalized_instances,
                candidate_count=len(row.candidate_sources_topk or []),
                min_instances=min_instances,
            )

        if not normalized_instances:
            row.needs_review = True
            multi_issue_id = f"ISSUE_MULTI_{row.target_table.entity_id}_{row.target_column_name}"
            _add_issue_if_missing(
                row,
                OpenIssue(
                    issue_id=multi_issue_id,
                    issue_type=IssueType.AMBIGUOUS_MAPPING,
                    severity=IssueSeverity.WARN,
                    target_column={
                        "entity_type": "TARGET_TABLE",
                        "entity_id": row.target_table.entity_id,
                        "column_name": row.target_column_name,
                    },
                    message="CASE/IF_ELSE output was not concrete enough to expand. Kept single row for review.",
                    suggested_question="Provide concrete CASE/IF_ELSE branches (conditions + outcomes) for this target column.",
                    created_by="MappingLogicAgent",
                    evidence_refs=[],
                ),
            )
            expanded.append(row)
            continue

        if out.needs_review:
            multi_issue_id = f"ISSUE_MULTI_{row.target_table.entity_id}_{row.target_column_name}"
            _add_issue_if_missing(
                row,
                OpenIssue(
                    issue_id=multi_issue_id,
                    issue_type=IssueType.AMBIGUOUS_MAPPING,
                    severity=IssueSeverity.WARN,
                    target_column={
                        "entity_type": "TARGET_TABLE",
                        "entity_id": row.target_table.entity_id,
                        "column_name": row.target_column_name,
                    },
                    message="CASE/IF_ELSE multi-rule output requires BSA review for completeness.",
                    suggested_question="Confirm CASE/IF_ELSE branches (conditions + outcomes) for this target column.",
                    created_by="MappingLogicAgent",
                    evidence_refs=[],
                ),
            )

        for inst in normalized_instances:
            inst_row = row.model_copy(deep=True)
            inst_rule_id = str(inst.get("rule_instance_id") or "").strip()
            inst_row.rule_instance_id = inst_rule_id or inst_row.rule_instance_id
            inst_row.row_id = f"{row.target_table.entity_id}.{row.target_column_name}:{inst_row.rule_instance_id}"
            inst_row.row_filter_text = inst.get("row_filter_text") or inst_row.row_filter_text
            inst_row.transformation_rules_text = inst.get("transformation_rules_text") or inst_row.transformation_rules_text
            selected_candidate_index = inst.get("selected_candidate_index")
            if isinstance(selected_candidate_index, int) and 0 <= selected_candidate_index < len(row.candidate_sources_topk):
                chosen = row.candidate_sources_topk[selected_candidate_index]
                inst_row.source_entity = chosen.source_entity
                inst_row.source_field_names = [chosen.source_column_name]
            inst_row.needs_review = True if out.needs_review else inst_row.needs_review
            inst_row.confidence_score = max(inst_row.confidence_score, float(out.confidence or 0.0))
            if out.reasoning_summary:
                inst_row.reasoning_summary = out.reasoning_summary
            if multi_issue_id and multi_issue_id not in inst_row.open_issue_ids:
                inst_row.open_issue_ids.append(multi_issue_id)
            expanded.append(inst_row)

    rows = expanded

    # Finalize needs_review after all AG1 stages (confidence can change after LLM calls).
    threshold = float(getattr(config, "STEP2_NEEDS_REVIEW_CONFIDENCE_THRESHOLD", 0.85))
    for row in rows:
        finalize_needs_review(row, confidence_threshold=threshold)

    return rows, issues


mapping_logic_agent = SequentialAgent(
    name="mapping_logic_agent",
    sub_agents=[],
    description="MappingLogicAgent (Step 2) stub; logic exposed via run_mapping_logic_agent(ctx).",
)

__all__ = ["mapping_logic_agent", "run_mapping_logic_agent"]
