"""
Step 3 - HITL Review (Pydantic Schemas)

What Step 3 does:
  - Consumes Step 2 output (Step2State) which already contains:
      - column_mappings[] (draft rows)
      - table_common_filters[] (mapping/table-scope common filters)
      - open_issues[] (explicit uncertainties/gaps)
      - question_candidates[] (seeds for HITL)
    This is the Step 2 -> Step 3 contract.

  - Generates a UI-ready review package:
      - review_questions[] (curated, deduped, prioritized)
      - enough context for a BSA to answer quickly

  - Collects BSA responses from the UI and persists them.
  - Produces normalized "decisions" (patch instructions) that the Step 4 / finalization agent
    can apply deterministically to the Step 2 draft mapping.

What Step 3 does NOT do:
  - It does NOT rewrite everything itself (that's finalization / versioning step).
  - It does NOT treat EvidenceHub/RAG as truth (same helper-only policy as Step 2).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

# =============================================================================
# Imports (do NOT duplicate Step 1 / Step 2 refs)
# =============================================================================

# Step 1 core references (single source of truth across steps)
from agents.mapping_ingestion.models import ColumnRef, EntityRef

# Step 2 output contract (single source of truth for mapping rows/issues)
from agents.mapping_generation.models import (
    EvidenceRef,
    JoinCondition,
    MappingRow,
    OpenIssue,
    RuleType,
    Step2Metadata,
    Step2State,
)


# =============================================================================
# 1) Enums used by Step 3
# =============================================================================

class CaptureStatus(str, Enum):
    """
    High-level lifecycle of the Step 3.5 capture artifact.

    Why we need it:
      - Distinguishes "capture complete" vs "still editing" without implying Step 4 has run.
      - Avoids conflating capture completion with downstream resolution/finalization.
    """

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class ResolutionStatus(str, Enum):
    """
    Lifecycle of Step 4 resolution/finalization, from the perspective of Step 3 output.

    Step 3.5 should generally emit NOT_STARTED, because Step 4 owns resolution.
    """

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class QuestionPriority(str, Enum):
    """
    Priority for review questions.

    Why we need it:
      - Allows the UI to focus BSAs on correctness-blocking items first.
      - Helps PM/metrics (how many P0 are pending).
    """

    P0 = "P0"  # blocks correctness
    P1 = "P1"  # important, likely impacts mapping quality
    P2 = "P2"  # nice-to-have clarification


class ReviewQuestionKind(str, Enum):
    """
    Coarse category of what we are asking.

    Why we need it:
      - UI can render specialized widgets for each kind (picker, text, boolean, etc.).
      - Downstream decision normalization becomes easier (expected patch type).
    """

    CONFIRM_ROW = "CONFIRM_ROW"
    RULE_TYPE = "RULE_TYPE"
    SOURCE_FIELDS = "SOURCE_FIELDS"
    LOOKUP_TABLE = "LOOKUP_TABLE"
    JOIN_KEYS = "JOIN_KEYS"
    FILTERS = "FILTERS"
    TRANSFORMATION = "TRANSFORMATION"
    DEFAULT_OR_HARDCODE_VALUE = "DEFAULT_OR_HARDCODE_VALUE"
    MULTI_RULE_SPLIT = "MULTI_RULE_SPLIT"
    OTHER = "OTHER"


class AnswerFormat(str, Enum):
    """
    How the UI should collect the answer.

    Why we need it:
      - UI contract: enables consistent widgets.
      - Backend validation: ensures we can interpret responses deterministically.
    """

    TEXT = "TEXT"
    BOOLEAN = "BOOLEAN"
    SINGLE_SELECT = "SINGLE_SELECT"
    MULTI_SELECT = "MULTI_SELECT"
    COLUMN_PICKER = "COLUMN_PICKER"  # structured pick: entity + column(s)
    JOIN_KEY_PICKER = "JOIN_KEY_PICKER"  # structured pick: left/right keys
    RULE_TYPE_SELECT = "RULE_TYPE_SELECT"


class DecisionType(str, Enum):
    """
    Normalized decisions produced from BSA answers (or direct manual edits).

    Why we need it:
      - Keeps Step 3 output "apply-ready" for the finalization/versioning agent.
      - Avoids Step 4 having to interpret raw UI answers.
    """

    APPROVE_ROW = "APPROVE_ROW"
    PATCH_ROW = "PATCH_ROW"
    ADD_ROW = "ADD_ROW"
    REMOVE_ROW = "REMOVE_ROW"
    PATCH_COMMON_FILTERS = "PATCH_COMMON_FILTERS"
    MARK_ISSUE_RESOLVED = "MARK_ISSUE_RESOLVED"


class RowReviewOutcome(str, Enum):
    """
    Outcome of review for a particular row.

    Why we need it:
      - Enables progress tracking: which rows are approved, modified, still pending.
      - Finalization can gate on whether P0 issues are resolved.
    """

    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REMOVED = "REMOVED"
    PENDING = "PENDING"


# =============================================================================
# 2) Helper models for UI options + column picking
# =============================================================================

class SelectOption(BaseModel):
    """
    A generic selectable option for SINGLE_SELECT / MULTI_SELECT.

    Why we need it:
      - Questions often propose candidate answers (e.g., top source candidates).
      - Using stable IDs allows safe round-trips UI -> backend.
    """

    option_id: str = Field(..., description="Stable option id (unique within the question).")
    label: str = Field(..., description="Human-friendly display label.")
    value: Optional[dict] = Field(
        default=None,
        description="Optional structured payload for the option (e.g., candidate source ref).",
    )


class PickedColumn(BaseModel):
    """
    A structured selection of (entity, column_name).

    Why we need it:
      - Many answers are best captured as structured picks rather than free text.
      - Avoids ambiguity and improves automatic patch application.
    """

    entity: EntityRef = Field(..., description="Selected entity (SOURCE_FILE / REF_TABLE / TARGET_TABLE).")
    column_name: str = Field(..., description="Selected column name within that entity.")


class JoinKeyPair(BaseModel):
    """
    One explicit join key pairing: left_entity.left_column = right_entity.right_column

    Why we need it:
      - Step 2 often cannot infer joins without graph/RAG. Step 3 can capture it explicitly.
      - Finalization needs structured join keys for validation and deterministic formatting.
    """

    left: PickedColumn = Field(..., description="Left side join column.")
    right: PickedColumn = Field(..., description="Right side join column.")


# =============================================================================
# 3) Review questions (Step 3 sub-agent output)
# =============================================================================

class AnswerSpec(BaseModel):
    """
    How to answer this question (UI + backend contract).

    Why we need it:
      - Tells UI what control to show (text box, picker, select).
      - Enables backend to validate the incoming answer payload.
    """

    answer_format: AnswerFormat = Field(..., description="How the answer should be captured.")
    is_required: bool = Field(default=True, description="If true, review cannot complete until answered.")
    allow_multi: bool = Field(
        default=False,
        description="If true, multiple selections are allowed (only meaningful for MULTI_SELECT/COLUMN_PICKER).",
    )
    placeholder: Optional[str] = Field(default=None, description="Optional UI placeholder/hint text.")


class ReviewQuestion(BaseModel):
    """
    A UI-ready question to ask the BSA.

    This is the core Step 3 artifact generated by the ReviewQuestionAgent.

    Links:
      - It can point to a Step 2 QuestionCandidate (seed) and/or Step 2 OpenIssue(s).
      - It can also point directly to affected MappingRow.row_id(s).

    Why we need it:
      - Step 2 already detects ambiguities; Step 3 converts them into actionable questions.
      - The question must carry enough context to be answered without deep digging.
    """

    question_id: str = Field(..., description="Stable unique question id within this Step 3 run.")
    priority: QuestionPriority = Field(..., description="Priority (P0 blocks correctness).")
    kind: ReviewQuestionKind = Field(..., description="What this question is about (drives UI control + patch logic).")

    # Traceability back to Step 2
    question_candidate_id: Optional[str] = Field(
        default=None,
        description="If derived from Step2.question_candidates, store that seed id for traceability.",
    )
    issue_ids: List[str] = Field(
        default_factory=list,
        description="Related Step2 OpenIssue.issue_id values (explains why this question exists).",
    )
    row_ids: List[str] = Field(
        default_factory=list,
        description="Affected Step2 MappingRow.row_id(s). Useful when a question impacts multiple rows.",
    )
    target_column: Optional[ColumnRef] = Field(
        default=None,
        description="Primary target column this question is about (when applicable).",
    )

    # The actual content shown to the BSA
    question_text: str = Field(..., description="Clear question text shown to the BSA.")
    context_summary: Optional[str] = Field(
        default=None,
        description="Short context so the BSA can answer quickly (no chain-of-thought).",
    )

    # Evidence is helper-only, but useful to display in UI
    evidence_refs: List[EvidenceRef] = Field(
        default_factory=list,
        description="Evidence pointers to show the BSA (helper-only; may include 'INSTRUCTIONS/MANUAL' refs from Step 2).",
    )

    # How to answer + proposed options (if any)
    answer_spec: AnswerSpec = Field(..., description="UI + backend contract for collecting the answer.")
    options: List[SelectOption] = Field(
        default_factory=list,
        description="Optional list of proposed answers (candidate sources, rule types, etc.).",
    )


# =============================================================================
# 4) Raw UI answers (persist exactly what the BSA provided)
# =============================================================================

class BsaAnswer(BaseModel):
    """
    Raw answer submitted by the BSA for a ReviewQuestion.

    Why we need it:
      - Auditability: we should persist what the human answered verbatim.
      - Debugging: if decisions look wrong, we can trace back to the raw response.
      - Enables re-running normalization if the decision schema evolves.
    """

    question_id: str = Field(..., description="Which question this answer corresponds to.")
    answered_by: Optional[str] = Field(default=None, description="User identifier (if available).")
    answered_at: datetime = Field(default_factory=datetime.utcnow, description="When the answer was submitted.")

    # A single answer can be represented in multiple ways depending on AnswerFormat.
    answer_format: AnswerFormat = Field(..., description="Must match ReviewQuestion.answer_spec.answer_format.")
    answer_text: Optional[str] = Field(default=None, description="Free-text answer (TEXT format).")
    answer_bool: Optional[bool] = Field(default=None, description="Boolean answer (BOOLEAN format).")
    selected_option_ids: List[str] = Field(
        default_factory=list,
        description="Selected options (SINGLE_SELECT/MULTI_SELECT). Uses ReviewQuestion.options.option_id.",
    )
    picked_columns: List[PickedColumn] = Field(
        default_factory=list,
        description="Selected columns (COLUMN_PICKER). Typically used for source fields / lookup table columns.",
    )
    join_key_pairs: List[JoinKeyPair] = Field(
        default_factory=list,
        description="Explicit join keys (JOIN_KEY_PICKER).",
    )
    selected_rule_type: Optional[RuleType] = Field(
        default=None,
        description="If AnswerFormat=RULE_TYPE_SELECT, this stores the chosen RuleType.",
    )

    notes: Optional[str] = Field(
        default=None,
        description="Optional extra comments from the BSA (kept as-is, not interpreted).",
    )


# =============================================================================
# 5) Normalized decisions (apply-ready patches)
# =============================================================================

class MappingRowPatch(BaseModel):
    """
    A targeted patch for a single Step2 MappingRow.

    Why we need it:
      - Finalization should be deterministic: apply patch -> validate -> produce new version.
      - Keeps edits structured (avoid free-text changes to arbitrary JSON paths).
    """

    row_id: str = Field(..., description="MappingRow.row_id to patch (Step 2 stable row identity).")

    # Only include fields that the BSA is changing; leave others as None.
    rule_type: Optional[RuleType] = Field(default=None, description="Override rule type if needed.")
    source_entity: Optional[EntityRef] = Field(default=None, description="Override/choose source entity.")
    source_field_names: Optional[List[str]] = Field(default=None, description="Override/choose source fields.")
    lookup_tables: Optional[List[EntityRef]] = Field(
        default=None,
        description="If LOOKUP, specify lookup/reference tables involved.",
    )
    join_condition: Optional[JoinCondition] = Field(
        default=None,
        description="Override join condition (including structured join keys).",
    )
    row_filter_text: Optional[str] = Field(default=None, description="Override row-specific filter text.")
    transformation_rules_text: Optional[str] = Field(default=None, description="Override transformation logic text.")
    special_considerations_text: Optional[str] = Field(default=None, description="Override special considerations text.")

    needs_review: Optional[bool] = Field(
        default=None,
        description="If explicitly approved/resolved, can set needs_review=False; otherwise leave unchanged.",
    )
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Optional BSA-provided rationale for the patch (kept short, review-friendly).",
    )


class CommonFilterPatch(BaseModel):
    """
    Patch instruction for common filters.

    Note:
      - Step 2 stores `table_common_filters[]` as documentation-first expressions.
      - Step 3 may allow BSA edits to text, scope, or evidence notes.
    """

    target_table_id: str = Field(..., description="Target table_id where the common filter applies.")
    filter_id: Optional[str] = Field(
        default=None,
        description="Existing filter id to patch; if None, treat as new filter to add.",
    )
    new_expression_text: str = Field(..., description="Replacement expression text for the common filter.")
    rationale: Optional[str] = Field(default=None, description="Why this filter change was made.")


class Step3Decision(BaseModel):
    """
    A normalized "apply-ready" decision.

    Why we need it:
      - Step 4 should be able to apply changes deterministically.
      - Decisions are the unit of audit (who changed what and why).
    """

    decision_id: str = Field(..., description="Unique id for this decision within Step 3 state.")
    decision_type: DecisionType = Field(..., description="Which kind of decision this is.")
    question_id: Optional[str] = Field(default=None, description="If derived from an explicit question, link it here.")

    issue_ids: List[str] = Field(
        default_factory=list,
        description="Step2 OpenIssue ids resolved/affected by this decision.",
    )

    # One of the payloads below will be populated depending on decision_type.
    row_patch: Optional[MappingRowPatch] = Field(default=None, description="PATCH_ROW payload.")
    approve_row_id: Optional[str] = Field(default=None, description="APPROVE_ROW payload (row_id).")
    add_row: Optional[MappingRow] = Field(
        default=None,
        description="ADD_ROW payload (a fully specified MappingRow). "
        "Used when BSA says 'you missed a rule instance' or adds a second rule.",
    )
    remove_row_id: Optional[str] = Field(default=None, description="REMOVE_ROW payload (row_id).")
    common_filter_patches: List[CommonFilterPatch] = Field(
        default_factory=list,
        description="PATCH_COMMON_FILTERS payload (0..N patches).",
    )
    mark_issue_resolved_ids: List[str] = Field(
        default_factory=list,
        description="MARK_ISSUE_RESOLVED payload: issue ids to mark resolved after this decision.",
    )

    created_at: datetime = Field(default_factory=datetime.utcnow, description="When this decision was recorded.")
    created_by: Optional[str] = Field(default=None, description="User/system identifier creating the decision.")


class RowOutcome(BaseModel):
    """
    Review outcome for a particular mapping row.

    Why we need it:
      - Enables progress reporting and gating for finalization.
      - Helps UI show which rows are 'done' vs 'still pending'.
    """

    row_id: str = Field(..., description="Step2 MappingRow.row_id.")
    outcome: RowReviewOutcome = Field(..., description="Approved/Modified/Removed/Pending.")
    decision_ids: List[str] = Field(
        default_factory=list,
        description="Which Step3Decision ids produced this outcome.",
    )


# =============================================================================
# 6) Step 3 artifacts: (a) review package, (b) persisted review state
# =============================================================================

class Step3Metadata(BaseModel):
    """
    Metadata for Step 3.

    Why we need it:
      - Allows traceability across step artifacts (Step2 -> Step3 -> Step4).
      - Supports schema versioning + feature flags (e.g., allow_autofill).
    """

    run_id: str = Field(..., description="Inherited from Step 2 metadata / Step 1 run id.")
    interface_code: str = Field(..., description="Inherited from Step 2 metadata / interface identifier.")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="When Step 3 was created.")
    created_by: str = Field(default="Step3MainAgent", description="Which component produced Step 3 artifact.")
    schema_version: str = Field(default="step3_state_v1", description="Schema version label for Step 3 JSON.")
    allow_partial_completion: bool = Field(
        default=False,
        description="If true, Step 3 can be marked COMPLETED even if some non-P0 questions remain unanswered.",
    )


class Step3ReviewPackage(BaseModel):
    """
    Output of Step 3 question-generation (what the UI consumes).

    Why we need it:
      - This is the UI payload: draft mappings + questions + context.
      - Keeping it explicit makes UI integration simpler and reduces coupling.
    """

    metadata: Step3Metadata = Field(..., description="Step 3 run metadata.")
    step2_metadata: Step2Metadata = Field(..., description="Pointer/metadata describing which Step 2 artifact we review.")

    # The UI typically needs the draft mapping rows to display alongside questions.
    # We keep the full Step2State as a snapshot here to avoid re-fetch coupling;
    # if storage concerns arise, replace with a reference (artifact_uri + hash).
    step2_snapshot: Step2State = Field(
        ...,
        description="Snapshot of Step 2 draft mapping being reviewed (rows + filters + issues + question seeds).",
    )

    review_questions: List[ReviewQuestion] = Field(
        default_factory=list,
        description="Curated list of UI-ready questions generated from Step 2 issues + candidates.",
    )

    suggested_order: List[str] = Field(
        default_factory=list,
        description="Optional ordered list of question_ids for UI (e.g., P0 first).",
    )


class Step3State(BaseModel):
    """
    Persisted Step 3 artifact (what Step 3 outputs after review is performed).

    Contains:
      - review_package (what was asked)
      - raw BSA answers (what humans said)
      - normalized decisions (what the system should apply)
      - per-row outcomes + status

    Why we need it:
      - Step 4 (finalization/versioning) should not depend on UI details.
      - Enables audit trail and replays.
    """

    metadata: Step3Metadata = Field(..., description="Step 3 metadata + schema version.")
    step2_metadata: Step2Metadata = Field(..., description="Which Step 2 artifact this review corresponds to.")

    # What we asked
    review_questions: List[ReviewQuestion] = Field(
        default_factory=list,
        description="The final questions presented to the BSA (post-dedupe/prioritization).",
    )

    # What the human answered
    bsa_answers: List[BsaAnswer] = Field(
        default_factory=list,
        description="Raw answers from the UI (auditable source).",
    )

    # What Step 3 normalized into apply-ready instructions
    decisions: List[Step3Decision] = Field(
        default_factory=list,
        description="Normalized decisions that a finalization agent can apply deterministically.",
    )

    # Progress tracking
    row_outcomes: List[RowOutcome] = Field(
        default_factory=list,
        description="Per-row review outcomes (Approved/Modified/Removed/Pending).",
    )

    # NOTE: Step 3.5 is capture-only; Step 4 owns resolution/finalization.
    capture_status: CaptureStatus = Field(
        default=CaptureStatus.IN_PROGRESS,
        description="Status of Step 3.5 capture (does not imply Step 4 resolution).",
    )
    resolution_status: ResolutionStatus = Field(
        default=ResolutionStatus.NOT_STARTED,
        description="Status of Step 4 resolution/finalization. Step 3.5 should normally emit NOT_STARTED.",
    )

    resolved_issue_ids: List[str] = Field(
        default_factory=list,
        description="Issues marked resolved by decisions (helps gating downstream).",
    )
    linked_issue_ids: List[str] = Field(
        default_factory=list,
        description="Issues referenced/linked by this Step 3.5 artifact (does not imply pending/unresolved).",
    )

    superseded_issue_ids: List[str] = Field(
        default_factory=list,
        description=(
            "Issue ids that Step 3.5 considers superseded by a table-level row remap (PATCH_ROW that changes the "
            "mapping identity, e.g., rule_type/source_entity/source_fields/lookup_tables). "
            "Why: prevents ingesting stale Q/A experience and reduces noise for Step 2 learning."
        ),
    )

