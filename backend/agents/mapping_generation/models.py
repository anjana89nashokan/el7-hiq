"""
Step 2 - Draft Mapping Generation (Pydantic Schemas)

Goal of Step 2:
    Take Step 1 output (SharedState: schemas + mapping_context) and produce a Step 2
    draft mapping artifact as JSON (NOT Excel), containing:
      - column_mappings[]: one MappingRow per (target_column x rule_instance)
      - table_common_filters[]: reusable filters at mapping/table scope
      - open_issues[]: explicit gaps/unknowns discovered during automation
      - question_candidates[]: seeds for Step 3 (HITL) review UI
      - metadata: run_id/interface_code/timestamps/version flags

What Step 2 does NOT do:
    - No Excel generation (later Step 4).
    - No treating RAG evidence as truth. Evidence is helper-only.
    - No inventing tables/columns. Every reference must exist in Step 1 schemas.

Single source of truth:
    - EntityRef / ColumnRef / SharedState / GlobalFilter are imported from Step 1
      (agents.mapping_ingestion.models). We DO NOT duplicate them here to avoid drift.

Common filters policy (locked in for v1):
    - Common filters are ONLY mapping-level or table-level.
      Column-level constraints live on MappingRow.row_filter_text.
    - No common filter IDs in v1. Consumers apply common filters implicitly:
        scope=MAPPING -> applies to all rows
        scope=TABLE   -> applies to all rows where row.target_table.entity_id == target_table_id

LLM policy:
    - Any LLM call must have structured output and must not introduce new entities/columns.
    - Step 2 is heuristic-first. LLM is used only for constrained sub-tasks where the
      model chooses among provided options (candidate indices / allowed rule enums) and
      emits structured JSON validated by these schemas.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

try:
    from agents.mapping_ingestion.models import (  # type: ignore
        ColumnRef,
        EntityRef,
        GlobalFilter,
        SharedState,
        RuleType as Step1RuleType,
    )
except Exception as exc:  # pragma: no cover
    raise ImportError(
        "Step 2 schemas require Step 1 models (SharedState, EntityRef, ColumnRef, GlobalFilter). "
        "Fix imports; do not duplicate Step 1 models in Step 2."
    ) from exc


# =============================================================================
# 1) Controlled Enums (must align with prompts + runtime)
# =============================================================================

class RuleType(str, Enum):
    """
    Controlled list of rule types produced by Step 2.

    Why this exists:
        - Keeps the mapping template output within a controlled vocabulary.
        - Enables deterministic downstream formatting and review UI grouping.
        - Prevents the LLM from inventing new categories.

    Notes:
        - TECHNICAL corresponds to "System Generated" in the BRD language.
        - UNKNOWN is a safety valve that should usually imply needs_review=True.
    """

    DIRECT = "DIRECT"
    LOOKUP = "LOOKUP"
    SK = "SK"
    TECHNICAL = "TECHNICAL"  # ETL framework / audit / SCD scaffolding
    DEFAULT = "DEFAULT"  # fallback when input missing (explicit)
    HARDCODE = "HARDCODE"  # constant always (explicit)
    SUBSTRING = "SUBSTRING"
    CASE = "CASE"
    IF_ELSE = "IF_ELSE"
    UNKNOWN = "UNKNOWN"


class RuleTypeSource(str, Enum):
    """
    Where the rule_type came from.

    Why this exists:
        - Reviewers trust OVERRIDE more than inferred heuristics.
        - Helps auditability: which rows were forced vs guessed.
    """

    OVERRIDE = "OVERRIDE"
    INFERRED = "INFERRED"


class EvidenceSource(str, Enum):
    """
    Evidence sources are helper-only and are stored for transparency.

    Why this exists:
        - EvidenceHub/FYI/Graph can influence confidence and review questions.
        - Evidence is never treated as ground truth; schema and explicit overrides win.
    """

    EVIDENCE_HUB = "EVIDENCE_HUB"
    FYI_DB = "FYI_DB"
    GRAPH = "GRAPH"
    INSTRUCTIONS = "INSTRUCTIONS"
    MANUAL = "MANUAL"


class IssueSeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class IssueType(str, Enum):
    """
    Machine-readable issue categories used to generate HITL questions.
    """

    AMBIGUOUS_MAPPING = "AMBIGUOUS_MAPPING"
    MISSING_SOURCE_FIELD = "MISSING_SOURCE_FIELD"
    MISSING_TARGET_METADATA = "MISSING_TARGET_METADATA"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    JOIN_UNKNOWN = "JOIN_UNKNOWN"
    MISSING_AK_DEFINITION = "MISSING_AK_DEFINITION"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"


class QuestionPriority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class CommonFilterScope(str, Enum):
    MAPPING = "MAPPING"
    TABLE = "TABLE"


class EvidenceType(str, Enum):
    """
    Category of an evidence document/chunk stored in EvidenceHub / RAG.

    Why this exists:
        - Enables precise metadata filtering during retrieval (avoid noisy evidence types).
        - Lets Step 2 prioritize BSA-confirmed "experience" evidence over generic playbooks,
          while still treating ALL evidence as helper-only (never truth).
    """

    TRANSCRIPT = "TRANSCRIPT"
    PLAYBOOK = "PLAYBOOK"
    MAPPING_EXAMPLE = "MAPPING_EXAMPLE"
    # Experience (learning loop)
    BSA_TABLE_FEEDBACK = "BSA_TABLE_FEEDBACK"
    BSA_QA_FEEDBACK_APPLIED = "BSA_QA_FEEDBACK_APPLIED"
    INDEMAP_HISTORY = "INDEMAP_HISTORY"


class EvidenceAuthorityLevel(str, Enum):
    """
    Optional trust/priority signal for evidence items.

    IMPORTANT:
        - This is NOT correctness. Even HIGH authority must not invent schema.
        - It only influences ordering/weighting when multiple evidence items exist.
    """

    LOW = "LOW"
    MED = "MED"
    HIGH = "HIGH"


# =============================================================================
# 2) Evidence, candidates, joins, filters
# =============================================================================

class EvidenceRef(BaseModel):
    """
    Lightweight evidence reference (helper-only, not truth).

    Why this exists:
        - Allows reviewers to see what influenced a decision.
        - Enables "self-check" logic to record evidence mismatch without inventing schema.
        - Keeps evidence small: we store only short snippets / ids, not full documents.
    """

    # Where this evidence came from (RAG, FYI DB, graph, instructions, etc.).
    source: EvidenceSource = Field(..., description="Evidence origin. Evidence is helper-only, not truth.")

    # Optional evidence categorization/priority metadata. When present, retrievers can
    # prioritize BSA-confirmed "experience" evidence over generic playbooks while still
    # respecting schema guardrails (evidence is never authoritative truth).
    evidence_type: Optional[EvidenceType] = Field(
        default=None,
        description="Evidence category (e.g., PLAYBOOK, TRANSCRIPT, BSA_TABLE_FEEDBACK, BSA_QA_FEEDBACK_APPLIED). Helper-only.",
    )
    authority_level: Optional[EvidenceAuthorityLevel] = Field(
        default=None,
        description="Optional priority/trust hint (LOW/MED/HIGH). Not a correctness score.",
    )
    interface_code: Optional[str] = Field(
        default=None,
        description="Interface code this evidence is associated with (if applicable). Useful for retrieval filtering.",
    )
    target_table_id: Optional[str] = Field(
        default=None,
        description="Target table id this evidence is about (if column/table-specific). Useful for retrieval filtering.",
    )
    target_column_name: Optional[str] = Field(
        default=None,
        description="Target column name this evidence is about (if column-specific). Useful for retrieval filtering.",
    )
    rule_type: Optional[str] = Field(
        default=None,
        description="Rule type label associated with this evidence when known (string; must match canonical rule types).",
    )
    source_ref: Optional[str] = Field(
        default=None,
        description="Where this evidence came from (e.g., transcript filename, run_id, system export id).",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="When this evidence was created (source time). Useful for recency bias and audit.",
    )
    version: Optional[str] = Field(
        default=None,
        description="Optional evidence version tag (for upserts/soft-deprecations and reproducibility).",
    )

    # Optional human label (doc title, playbook name, transcript session id, etc.).
    title: Optional[str] = Field(default=None, description="Short label for display in review UI.")

    # Optional short snippet extracted from the evidence source (keep short).
    snippet: Optional[str] = Field(
        default=None,
        description="Small excerpt/summary. Avoid full paragraphs; store only what helps review.",
    )

    # Optional locator string that can be used to fetch the full evidence later
    # (e.g., doc_id#chunk_id, URL, file path, graph edge id).
    locator: Optional[str] = Field(
        default=None,
        description="Pointer to the evidence in its original system (doc/chunk id, edge id, etc.).",
    )

    # Optional relevance score from retrieval (0..1). This is not correctness, just retrieval ranking.
    relevance_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Retrieval relevance (0..1). Not a confidence-of-truth score.",
    )


class CandidateSource(BaseModel):
    """
    Candidate source column considered for a target column.

    Why this exists:
        - AG1 generates a top-k list so reviewers can see what was considered.
        - Step 3 can turn ambiguous cases into multiple-choice questions.
        - LLM sub-tasks can re-rank or choose among candidates safely by index.
    """

    # Which entity the candidate column belongs to (usually SOURCE_FILE).
    source_entity: EntityRef = Field(..., description="Entity that owns the candidate column (usually SOURCE_FILE).")

    # Physical column name in the source entity (e.g., SRC_PRV_ID).
    source_column_name: str = Field(..., description="Physical name of the candidate source column.")

    # Combined ranking score (0..1). Heuristic-first; may be refined by semantic scoring.
    score: float = Field(ge=0.0, le=1.0, description="Overall candidate score (0..1).")

    # Optional extra scoring signals for explainability (not required to be present).
    # These allow us to store "why" a candidate ranked well beyond a single combined score.
    # Name similarity score (0..1), typically from deterministic heuristics.
    name_similarity: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Name-based similarity signal (0..1)."
    )

    # Semantic similarity score (0..1), typically from a structured LLM call on top-k candidates.
    semantic_similarity: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Meaning-based similarity signal (0..1)."
    )

    # Type compatibility score (0..1). This is a weak signal; casts may still be valid.
    datatype_compatibility: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Datatype compatibility signal (0..1)."
    )

    # Short human-readable explanation used in review UI/debug logs.
    reason: Optional[str] = Field(default=None, description="Short rationale for why this candidate is plausible.")


class JoinKeyPair(BaseModel):
    """
    Structured representation of a join key mapping.

    Why this exists:
        - Even if we store human-readable join text, structured keys enable validation.
        - AG2 can populate this from graph edges / explicit lookup rules / evidence hints.
    """

    left_entity: EntityRef = Field(..., description="Left-side entity of the join.")
    left_columns: List[str] = Field(default_factory=list, description="Left-side join key columns.")
    right_entity: EntityRef = Field(..., description="Right-side entity of the join.")
    right_columns: List[str] = Field(default_factory=list, description="Right-side join key columns.")


class JoinCondition(BaseModel):
    """
    Join conditions for LOOKUP or multi-entity mapping rows.

    Why this exists:
        - The mapping template has a "Join Conditions" section.
        - Without ERwin edges (PoC), joins often require HITL clarification.
        - `is_unknown=True` forces needs_review and drives a question.
    """

    # Whether a join is required for this row based on rule type / multi-entity sourcing.
    is_required: bool = Field(default=True, description="True if this row requires join logic.")

    # True when join path/keys are not known (should trigger needs_review/open issue).
    is_unknown: bool = Field(default=False, description="True if join keys/path could not be determined.")

    # Human-readable join description (not SQL), but should name entities and keys when known.
    join_text: Optional[str] = Field(
        default=None,
        description="Human-readable join description. Must name join keys when known; avoid full SQL.",
    )

    # Optional structured join key pairs for validation / future automation.
    join_keys: List[JoinKeyPair] = Field(
        default_factory=list,
        description="Structured join keys; empty when unknown or when only high-level join text is available.",
    )

    # Evidence backing the join choice/hint (helper-only).
    evidence_refs: List[EvidenceRef] = Field(
        default_factory=list,
        description="Evidence supporting join hints (helper-only).",
    )


class TableCommonFilter(BaseModel):
    """
    Common filters apply implicitly by scope (NO IDs in v1):
      - MAPPING: applies to all rows
      - TABLE: applies to all rows for target_table_id

    Why this exists:
        - Prevents repeating the same filter text on hundreds of rows.
        - Mirrors the BRD concept of "Common Filter" at table-level.
        - We intentionally do NOT store column-scope filters here.
    """

    # Scope of the filter (mapping-wide or a specific table).
    scope: CommonFilterScope = Field(..., description="Filter scope (MAPPING or TABLE).")

    # Required when scope=TABLE; omitted when scope=MAPPING.
    target_table_id: Optional[str] = Field(
        default=None,
        description="Target table_id this filter applies to when scope=TABLE.",
    )

    # Optional short label shown in review UI.
    description: Optional[str] = Field(default=None, description="Short label describing the intent of the filter.")

    # The actual filter text (documentation-first, not strict SQL).
    expression_text: str = Field(
        ...,
        description="Human-readable filter expression (not full SQL; explicit enough for ETL developers).",
    )

    # Provenance/trust level for this filter.
    source: EvidenceSource = Field(
        default=EvidenceSource.INSTRUCTIONS,
        description="Where this filter came from (instructions/evidence/manual/etc.).",
    )

    # Evidence references backing this filter (helper-only).
    evidence_refs: List[EvidenceRef] = Field(default_factory=list, description="Evidence supporting this filter.")


# =============================================================================
# 3) Step 2 core output row
# =============================================================================

class MappingRow(BaseModel):
    """
    One draft mapping row for a single rule instance.

    Why this exists:
        - This is the core Step 2 artifact that later becomes rows in the mapping template.
        - A single target column can emit multiple MappingRow entries when CASE/IF_ELSE (multi-rule) applies.
    """

    # Stable row identifier within a run (useful for UI, logging, and linking issues/questions).
    row_id: str = Field(..., description="Stable id within the run, e.g. 'PRV_DATA.AEDW_PRV_SK:RULE_1'.")

    # Target entity for this row (must be TARGET_TABLE).
    target_table: EntityRef = Field(..., description="Target table reference (entity_type=TARGET_TABLE).")

    # Physical target column name (attribute_name from target metadata).
    target_column_name: str = Field(..., description="Target column attribute_name.")

    # -----------------------------------------------------------------
    # Target metadata snapshot (from Step 1 TargetSchema) for review display
    #
    # Why this exists:
    #   - The Step 3 review UI needs target-side context (data type, nullability, defaults, key flags).
    #   - We persist it on each MappingRow so the UI doesn't have to join against TargetSchema.
    #   - These are metadata-only fields; they do NOT change mapping logic.
    # -----------------------------------------------------------------

    # "Database" display value from target metadata (dataset id), e.g., DB_AEDWP1.
    target_database: Optional[str] = Field(
        default=None,
        description="Target metadata database/dataset identifier (from TargetTable.database, e.g., DB_AEDWP1).",
    )

    # Target column metadata (copied from TargetColumn).
    target_logical_attribute_name: Optional[str] = Field(
        default=None,
        description="Target metadata logical attribute name (from TargetColumn.logical_attribute_name).",
    )
    target_attribute_business_description: Optional[str] = Field(
        default=None,
        description="Target metadata business description (from TargetColumn.attribute_description).",
    )
    target_data_type: Optional[str] = Field(
        default=None,
        description="Target metadata canonical data type (from TargetColumn.data_type).",
    )
    target_default: Optional[str] = Field(
        default=None,
        description="Target metadata default value (from TargetColumn.default_value).",
    )
    target_nullability: Optional[bool] = Field(
        default=None,
        description="Target metadata nullability (from TargetColumn.nullability).",
    )
    target_key: Optional[str] = Field(
        default=None,
        description="Target key flags for display: P (primary), F (foreign), A (alternate). Comma-separated when multiple.",
    )

    # Optional rule instance id (RULE_1/RULE_2...) when the same target column needs multiple rules.
    rule_instance_id: Optional[str] = Field(
        default=None,
        description="Rule instance label when multiple rules exist for the same target column (RULE_1, RULE_2...).",
    )

    # High-level mapping classification.
    rule_type: RuleType = Field(..., description="Selected rule type for this row (controlled list).")

    # Whether the rule type came from explicit override vs inferred logic.
    rule_type_source: RuleTypeSource = Field(
        default=RuleTypeSource.INFERRED,
        description="OVERRIDE if forced by instructions; otherwise INFERRED.",
    )

    # Optional short traceability note, especially for OVERRIDE cases.
    forced_reason: Optional[str] = Field(default=None, description="Short reason text for forced/override decisions.")

    # Source selection (may be empty for TECHNICAL/HARDCODE/DEFAULT/UNKNOWN)
    # Chosen primary source entity (typically a SOURCE_FILE). May be absent for TECHNICAL/HARDCODE/DEFAULT/UNKNOWN.
    source_entity: Optional[EntityRef] = Field(
        default=None, description="Primary chosen source entity for this row (often SOURCE_FILE)."
    )

    # One or more source fields used by this row (comma-separated in Excel later; stored as list in JSON).
    source_field_names: List[str] = Field(
        default_factory=list,
        description="Chosen source column names used by this rule instance (empty if unknown/not required).",
    )

    # Reference/lookup tables involved (if known). Typically populated by AG2 later.
    lookup_tables: List[EntityRef] = Field(
        default_factory=list,
        description="Lookup/reference tables involved in this mapping row (if known).",
    )

    # Optional selected graph hypothesis id chosen by AG1 for LOOKUP decisions.
    # AG2 uses this id to materialize join paths deterministically from DataModelGraph edges.
    selected_lookup_hypothesis_id: Optional[str] = Field(
        default=None,
        description="Selected lookup hypothesis id from AG1 (if LOOKUP and graph hypothesis was chosen).",
    )

    # Top-k candidate sources considered (for explainability and HITL questions).
    candidate_sources_topk: Optional[List[CandidateSource]] = Field(
        default=None,
        description="Optional top candidate sources considered. Used for explainability and HITL options.",
    )

    # Join/filter placeholders (JoinAndFilterAgent enriches later)
    # Join placeholder; AG2 (JoinAndFilterAgent) is responsible for populating this.
    join_condition: Optional[JoinCondition] = Field(
        default=None,
        description="Join conditions for LOOKUP/multi-entity rules (usually enriched by AG2).",
    )

    # Row-level filter text (rule-instance specific). Column-scoped filters from Step 1 live here.
    row_filter_text: Optional[str] = Field(
        default=None,
        description="Row-specific filter/condition text (rule-instance scoped).",
    )

    # Human-readable transformation rule text. Typically finalized by AG3.
    transformation_rules_text: Optional[str] = Field(
        default=None,
        description="Human-readable transformation logic (not SQL). Usually finalized by AG3.",
    )

    # Optional extra notes (edge cases, evidence hints, review caveats).
    special_considerations_text: Optional[str] = Field(
        default=None,
        description="Optional notes/caveats for reviewers and ETL developers.",
    )

    # 0..1 confidence heuristic for this row. Used for sorting and HITL triggers (not a probability).
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        default=0.5,
        description="Confidence score (0..1). Low confidence should typically trigger needs_review.",
    )

    # Fast boolean for reviewers: this row should be reviewed/confirmed.
    needs_review: bool = Field(default=False, description="True if BSA review is required.")

    # Short explanation of why the rule type/sources were chosen (stable, review-friendly).
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Short, stable rationale for the decision (no chain-of-thought).",
    )

    # Evidence references used for this row (helper-only).
    evidence_refs: List[EvidenceRef] = Field(
        default_factory=list,
        description="Evidence references used to support or question this row (helper-only).",
    )

    # References into Step2State.open_issues.
    open_issue_ids: List[str] = Field(
        default_factory=list,
        description="IDs of OpenIssue entries that apply to this row (kept separate to avoid repetition).",
    )


# =============================================================================
# 4) Open issues + question candidates (Step 3 seeds)
# =============================================================================

class OpenIssue(BaseModel):
    """
    A concrete uncertainty/gap discovered during Step 2.

    Why this exists:
        - needs_review is a boolean; OpenIssue explains exactly *why*.
        - Step 3 uses these issues to generate review questions.
        - Issues are aggregated at the run level and referenced by MappingRow.open_issue_ids.
    """

    # Stable unique id for the issue (helps linking and deduping).
    issue_id: str = Field(..., description="Unique issue id within the run.")

    # Machine-readable category of the issue.
    issue_type: IssueType = Field(..., description="Issue category used for HITL question generation.")

    # Severity used to prioritize review (ERROR blocks correctness; WARN needs confirmation; INFO is FYI).
    severity: IssueSeverity = Field(default=IssueSeverity.WARN, description="How blocking this issue is.")

    # Which target column this issue is about (must exist in Step 1 target_schema).
    target_column: ColumnRef = Field(..., description="Target column this issue relates to.")

    # Human-readable explanation of what is missing/ambiguous.
    message: str = Field(..., description="Human-readable problem statement.")

    # Optional suggested question text that a UI can show directly.
    suggested_question: Optional[str] = Field(
        default=None,
        description="Optional question seed to ask the BSA (Step 3).",
    )

    # Which component created this issue (for traceability and debugging).
    created_by: Literal[
        "MappingLogicAgent",
        "JoinAndFilterAgent",
        "MappingPostProcessorAgent",
        "Step2MainAgent",
    ] = Field(default="Step2MainAgent", description="Which Step 2 component emitted this issue.")

    # Evidence backing this issue (helper-only).
    evidence_refs: List[EvidenceRef] = Field(default_factory=list, description="Evidence related to this issue.")


class QuestionCandidate(BaseModel):
    """
    A pre-shaped question seed for Step 3 (HITL).

    Why this exists:
        - Step 2 already knows what is ambiguous; Step 3 should focus on UX, not rediscovery.
        - This is a "UI-ready" payload: question text + context + evidence pointers.
    """

    question_id: str = Field(..., description="Unique question id within the run.")
    priority: QuestionPriority = Field(..., description="Priority (P0 blocks correctness; P2 is nice-to-have).")
    target_column: ColumnRef = Field(..., description="Target column this question is about.")
    question_text: str = Field(..., description="The question to ask the BSA (clear and specific).")
    context_summary: Optional[str] = Field(default=None, description="Short context to help the BSA answer quickly.")
    evidence_refs: List[EvidenceRef] = Field(default_factory=list, description="Evidence shown alongside the question.")


# =============================================================================
# 5) Step 2 output artifact (persisted JSON)
# =============================================================================

class Step2Metadata(BaseModel):
    """
    Metadata describing a Step 2 artifact.

    Why this exists:
        - Supports auditability (which run produced this file).
        - Enables versioning as the schema evolves.
        - Captures feature flags (e.g., whether RAG was enabled).
    """

    run_id: str = Field(..., description="Run identifier inherited from Step 1 SharedState.run_id.")
    interface_code: str = Field(..., description="Interface code inherited from Step 1 SharedState.interface_code.")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="UTC timestamp when Step 2 state was written.")
    created_by: str = Field(default="Step2MainAgent", description="Which component produced this Step 2 artifact.")
    schema_version: str = Field(default="step2_state_v1", description="Schema version label for this Step 2 JSON.")
    rag_enabled: bool = Field(default=True, description="Whether the RAG/evidence path was enabled for this run.")


class Step2State(BaseModel):
    """
    Persisted Step 2 artifact (JSON).

    Why this exists:
        - This is the contract from Step 2 -> Step 3 (review) and later Step 4 (Excel generation).
        - It contains both the draft mapping rows and the review/traceability signals.
    """

    metadata: Step2Metadata = Field(..., description="Run metadata and schema versioning info.")
    column_mappings: List[MappingRow] = Field(
        default_factory=list,
        description="All draft mapping rows (may include multiple rows per target column for CASE/IF_ELSE).",
    )
    table_common_filters: List[TableCommonFilter] = Field(
        default_factory=list,
        description="Mapping/table-level common filters applied implicitly by scope.",
    )
    open_issues: List[OpenIssue] = Field(default_factory=list, description="Aggregated issues requiring review.")
    question_candidates: List[QuestionCandidate] = Field(
        default_factory=list,
        description="Pre-built question seeds for HITL review (Step 3).",
    )


# =============================================================================
# 6) Runtime work context (in-memory; not persisted)
# =============================================================================

class Step2WorkContext(BaseModel):
    """
    Precomputed context used by Step2MainAgent and sub-agents.

    This avoids repeated O(N) scans over overrides lists while looping over many columns.
    """

    # Full Step 1 SharedState (schemas + mapping_context). Step 2 consumes this as input.
    shared_state: SharedState = Field(..., description="Step 1 output consumed by Step 2.")

    # Source scope (file_ids) for this Step 2 run. Empty means "all sources in source_schema".
    selected_source_ids: List[str] = Field(
        default_factory=list,
        description="Source file_ids in scope (derived from Step 1 mapping_context).",
    )

    # Target scope (table_ids) for this Step 2 run. Empty means "all targets in target_schema".
    selected_target_ids: List[str] = Field(
        default_factory=list,
        description="Target table_ids in scope (derived from Step 1 mapping_context).",
    )
    # Canonical key format used across Step 2: "TGT:<table_id>|COL:<column_name>"
    # Using a set keeps membership checks O(1) while looping hundreds/thousands of columns.
    ignore_fields_keys: set[str] = Field(
        default_factory=set,
        description="O(1) ignore set using normalized keys: 'TGT:<table_id>|COL:<column_name>'.",
    )

    rule_type_overrides_map: Dict[str, RuleType] = Field(
        default_factory=dict,
        description="Normalized target key -> forced RuleType (highest priority).",
    )
    # Stored as dict payloads (DefaultRule.model_dump()) so this Step 2 schema does not duplicate Step 1 models.
    default_rules_map: Dict[str, dict] = Field(
        default_factory=dict,
        description="Normalized target key -> DefaultRule payload (from Step 1). Stored as dict to avoid schema drift.",
    )
    lookup_rules_map: Dict[str, dict] = Field(
        default_factory=dict,
        description="Normalized target key -> LookupRule payload (from Step 1). Stored as dict to avoid schema drift.",
    )

    # Optional traceability (helps reviewers quickly understand why a rule was forced).
    rule_type_override_reasons: Dict[str, str] = Field(
        default_factory=dict,
        description="Normalized target key -> short reason explaining why the rule was forced.",
    )

    # Optional natural-key hints from instructions (CompositeKeyRule.model_dump()).
    # Keyed by entity_id (SOURCE_FILE id or TARGET_TABLE id).
    composite_key_rules_by_entity: Dict[str, List[dict]] = Field(
        default_factory=dict,
        description="entity_id -> CompositeKeyRule payloads (from Step 1), used as natural-key hints for SK decisions.",
    )

    global_filters_mapping: List[GlobalFilter] = Field(
        default_factory=list,
        description="Mapping-scope filters from Step 1 mapping_context.global_filters (scope=MAPPING).",
    )
    global_filters_by_table: Dict[str, List[GlobalFilter]] = Field(
        default_factory=dict,
        description="Table-scope filters keyed by target_table_id (scope=TABLE).",
    )
    global_filters_by_column: Dict[str, List[GlobalFilter]] = Field(
        default_factory=dict,
        description="Column-scope filters keyed by normalized target key (scope=COLUMN). Applied at row level.",
    )

    # Optional per-target scoping derived from Step 1 MappingContext.explicit_mappings.
    # If present for a given target table, candidate search should be restricted to those source file_ids.
    # This keeps candidate selection aligned with BSA-provided "file -> table" scoping hints.
    explicit_source_ids_by_target_table: Dict[str, set[str]] = Field(
        default_factory=dict,
        description="target_table_id -> set(source_file_id) derived from Step 1 explicit_mappings for tighter candidate scoping.",
    )

    rag_enabled: bool = Field(
        default=True,
        description="Whether EvidenceHub/RAG path is enabled for this run (controls pre-retrieve + self-check stages).",
    )

    # Whether technical/system columns are treated as deterministic forced policy before AG1 inferred chooser.
    force_technical_rules: bool = Field(
        default=True,
        description="If true, strong technical/system columns bypass inferred chooser and remain deterministic TECHNICAL.",
    )


# =============================================================================
# Helpers
# =============================================================================

def map_step1_rule_type(step1_rule: Step1RuleType) -> RuleType:
    """
    Convert Step 1 rule type enum values into Step 2 rule type labels.
    """
    mapping = {
        Step1RuleType.DIRECT_MOVE: RuleType.DIRECT,
        Step1RuleType.LOOKUP: RuleType.LOOKUP,
        Step1RuleType.SK_CREATION: RuleType.SK,
        Step1RuleType.DEFAULT_HARDCODE: RuleType.HARDCODE,
        Step1RuleType.SYSTEM_GENERATED: RuleType.TECHNICAL,
    }
    return mapping.get(step1_rule, RuleType.UNKNOWN)
