"""
Step 2 — Subagent #1 (MappingLogicAgent) internal models.

Why these models live here (and NOT in the global Step 2 schema):
    - They are *not* part of the persisted Step2State JSON contract.
    - They exist only to enforce structured outputs for the LLM calls made by AG1.
    - Keeping them local prevents cluttering the main Step 2 schema with implementation details.

Safety / anti-hallucination contract:
    - Any LLM output is validated against these Pydantic models.
    - Candidate discovery returns catalog *indices only* (never column names), and we post-validate
      indices against Step 1 schemas before converting them to real selections.

Used by:
    - MappingLogicAgent (server/agents/mapping_generation/sub_agents/mapping_logic_agent/agent.py)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.mapping_generation.models import RuleType


class SourceCatalogItem(BaseModel):
    """
    Compact representation of a source column used for LLM-safe selection by index.

    This model is NOT emitted into Step2State; it's an internal runtime structure.

    Keys are intentionally short to reduce prompt size. The LLM must reference items by `i` only.
    """

    i: int = Field(..., ge=0, description="Stable index into the catalog.")
    f: str = Field(..., description="Source file_id (Step 1 schema id).")
    c: str = Field(..., description="Source physical column name.")
    t: str | None = Field(default=None, description="Canonical/normalized source data type if available.")
    ln: str | None = Field(default=None, description="Optional source logical name.")
    d: str | None = Field(default=None, description="Optional source description.")


class CatalogCandidateItem(BaseModel):
    """
    One candidate (by index) proposed by the LLM for a given target column.

    The model must ONLY return indices that exist in the provided SOURCE_CATALOG.
    """

    index: int = Field(..., ge=0, description="Index into SOURCE_CATALOG.")
    match_score: float = Field(..., ge=0.0, le=1.0, description="0..1 match score (higher is better).")
    rationale: str = Field(default="", description="Short reason for why this catalog item matches.")



class CatalogCandidatesOutput(BaseModel):
    """
    Structured output for catalog-based candidate discovery (Option A).

    Guardrails:
      - The LLM may NOT output any column names; only indices into SOURCE_CATALOG.
      - All indices are validated post-call before being converted into CandidateSource objects.
    """

    thought_process: str = Field(
        default="",
        description="Optional model rationale trace. Keep concise; may be empty.",
    )
    selected_index: int | None = Field(default=None, description="Best candidate index, or null if none.")
    candidates: list[CatalogCandidateItem] = Field(default_factory=list, description="Ranked best->worst candidates.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = Field(default="", description="Optional short notes/caveats.")


class RuleCandidateDecisionOutput(BaseModel):
    """
    Pass-1 AG1 chooser output (LLM-major for inferred rows).
    """

    thought_process: str = Field(
        default="",
        description="Optional model rationale trace. Keep concise; may be empty.",
    )
    selected_rule_type: RuleType
    selected_source_candidate_indices: list[int] = Field(default_factory=list)
    selected_lookup_hypothesis_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    decision_basis: str | None = None
    conflict_flags: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""


class RuleCandidateDecisionRefinementOutput(BaseModel):
    """
    Pass-2 AG1 refinement/challenger output.
    """

    thought_process: str = Field(
        default="",
        description="Optional model rationale trace. Keep concise; may be empty.",
    )
    selected_rule_type: RuleType
    selected_source_candidate_indices: list[int] = Field(default_factory=list)
    selected_lookup_hypothesis_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    decision_basis: str | None = None
    conflict_flags: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""


class DecisionSelfCheckOutput(BaseModel):
    """
    Final self-check over refined decision.
    """

    thought_process: str = Field(
        default="",
        description="Optional model rationale trace. Keep concise; may be empty.",
    )
    contradiction_found: bool = False
    confidence_delta: float = Field(default=0.0, ge=-1.0, le=1.0)
    needs_review: bool = False
    issue_message: str | None = None
    question_text: str | None = None


class LookupPathSelectionOutput(BaseModel):
    """
    Dedicated AG1 lookup-path selector output.
    """

    thought_process: str = Field(
        default="",
        description="Optional model rationale trace. Keep concise; may be empty.",
    )
    selected_lookup_hypothesis_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    reasoning_summary: str = ""
    rejection_reason: str | None = None


class HistoricalMappingCandidate(BaseModel):
    """
    One normalized historical mapping candidate passed to AG1 history reranker.
    """

    candidate_id: str
    canonical_rule_type: str
    source_hints: dict = Field(default_factory=dict)
    join_text: str | None = None
    filter_text: str | None = None
    rule_text: str | None = None
    special_text: str | None = None
    last_updated: str | None = None
    schema_compatible: bool | None = None
    schema_compat_reason: str | None = None
    candidate_summary: str = ""


class HistoricalMappingRerankItem(BaseModel):
    """
    Scored output item from AG1 historical mapping rerank stage.
    """

    candidate_id: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    conflict_flag: bool = False
    conflict_reason: str | None = None


class HistoricalMappingRerankOutput(BaseModel):
    """
    Structured output for AG1 historical mapping rerank.
    """

    thought_process: str = Field(
        default="",
        description="Optional model rationale trace. Keep concise; may be empty.",
    )
    selected_top_ids: list[str] = Field(default_factory=list, max_length=3)
    scores: list[HistoricalMappingRerankItem] = Field(default_factory=list)
    global_conflict_flag: bool = False
    reasoning_summary: str = ""
    needs_review: bool = False


class MultiRuleInstance(BaseModel):
    """
    One rule instance for CASE/IF_ELSE expansion.

    Used by AG1 multi-rule stage (CASE/IF_ELSE only).
    """

    rule_instance_id: str
    row_filter_text: str | None = None
    selected_candidate_index: int | None = None
    transformation_rules_text: str | None = None
    rationale: str = ""


class MultiRuleOutput(BaseModel):
    """
    Structured multi-rule output for CASE/IF_ELSE expansion.

    Used by AG1 multi-rule stage (CASE/IF_ELSE only).
    """

    thought_process: str = Field(
        default="",
        description="Optional model rationale trace. Keep concise; may be empty.",
    )
    instances: list[MultiRuleInstance] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    reasoning_summary: str = ""


__all__ = [
    "CatalogCandidateItem",
    "CatalogCandidatesOutput",
    "DecisionSelfCheckOutput",
    "HistoricalMappingCandidate",
    "HistoricalMappingRerankItem",
    "HistoricalMappingRerankOutput",
    "LookupPathSelectionOutput",
    "MultiRuleInstance",
    "MultiRuleOutput",
    "RuleCandidateDecisionOutput",
    "RuleCandidateDecisionRefinementOutput",
    "SourceCatalogItem",
]
