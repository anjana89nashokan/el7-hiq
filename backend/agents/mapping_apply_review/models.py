"""
Step 4 - Apply Review / Finalization (Pydantic Schemas)

Step 4 goal (aligned with our agreed runtime rules):
  - Load Step 1 SharedState (schemas + mapping_context) and Step 2 + Step 3.5 artifacts
    for the same run_id/interface_code.
  - Gate: Step 4 must NOT run unless Step 3.5 capture is completed
    (step3_state.capture_status == COMPLETED).
  - Use an LLM ONLY to interpret BSA intent from:
      * structured Step 3 decisions (row patches)
      * free-text feedback (reasoning_summary inside the patch)
      * question answers (free-text today)
    The LLM must NOT "improve" the mapping on its own.
  - Apply changes deterministically (no hidden side effects).
  - NEVER change target identifiers (target_table / target_column).
  - If schema validation fails:
      * keep BSA patch / feedback changes (preserve intent),
      * force needs_review=True,
      * record warnings + manual actions,
      * keep issue status UNRESOLVED / PARTIALLY_RESOLVED.
  - Always produce an output artifact (even if unresolved items remain).

This file defines the persisted Step 4 artifact (Step4State):
  - updated MappingRow list (re-using Step 2 MappingRow shape)
  - issue resolution ledger (separate section; not hidden inside rows)
  - warnings + manual actions
  - interpretation plans (LLM output, for transparency)
  - change log (applied changes, source, rationale)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

from agents.mapping_generation.models import (
    MappingRow,
    OpenIssue,
    Step2Metadata,
    Step2State,
    TableCommonFilter,
)
from agents.mapping_review.models import CaptureStatus, Step3Metadata, Step3State


# =============================================================================
# 1) Artifact references (traceability)
# =============================================================================


class ArtifactKind(str, Enum):
    STEP1_SHARED_STATE = "STEP1_SHARED_STATE"
    STEP2_STATE = "STEP2_STATE"
    STEP3_REVIEW_PACKAGE = "STEP3_REVIEW_PACKAGE"
    STEP3_CAPTURE_STATE = "STEP3_CAPTURE_STATE"


class ArtifactRef(BaseModel):
    """
    Pointer to an artifact used during Step 4.

    Why we need it:
      - Reproducibility: run Step 4 again with the same inputs.
      - Debuggability: know exactly which JSON files were loaded.
    """

    kind: ArtifactKind = Field(..., description="Which artifact this refers to.")
    uri: str = Field(..., description="Path/URI to the artifact (file path in v1).")
    checksum: Optional[str] = Field(default=None, description="Optional integrity hash (future).")
    loaded_at: datetime = Field(default_factory=datetime.utcnow, description="When it was loaded.")


# =============================================================================
# 2) Step 4 statuses / provenance
# =============================================================================


class Step4IssueStatus(str, Enum):
    """
    Resolution status tracked separately from mapping rows.

    Why we need it:
      - Step 4 should deliver output even if some items remain unresolved.
      - Consumers need an explicit ledger of what's still blocked.
    """

    RESOLVED = "RESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    UNRESOLVED = "UNRESOLVED"


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class WarningType(str, Enum):
    """
    Structured warning categories.

    Why we need it:
      - Consumers can filter/group warnings in UI and reports without parsing text.
      - Keeps validation outcomes consistent across runs.
    """

    INVALID_SOURCE_ENTITY = "INVALID_SOURCE_ENTITY"
    INVALID_SOURCE_FIELD = "INVALID_SOURCE_FIELD"
    INVALID_LOOKUP_TABLE = "INVALID_LOOKUP_TABLE"
    INVALID_JOIN_KEYS = "INVALID_JOIN_KEYS"
    HALLUCINATION_REJECTED = "HALLUCINATION_REJECTED"
    AMBIGUOUS_FEEDBACK = "AMBIGUOUS_FEEDBACK"
    OTHER = "OTHER"


class ChangeSource(str, Enum):
    """
    Provenance for applied changes.

    Why we need it:
      - Audit: answer "why did this field change?".
      - Determinism: avoids hidden LLM "improvements".
    """

    BSA_PATCH = "BSA_PATCH"
    BSA_FEEDBACK = "BSA_FEEDBACK"
    BSA_ANSWER = "BSA_ANSWER"
    NORMALIZATION = "NORMALIZATION"


class ConflictWinner(str, Enum):
    """
    Explicit record of which source won in a conflict.
    """

    FEEDBACK = "FEEDBACK"
    PATCH = "PATCH"
    ANSWER_P0 = "ANSWER_P0"
    ANSWER_P1 = "ANSWER_P1"
    ANSWER_P2 = "ANSWER_P2"
    NONE = "NONE"


# =============================================================================
# 3) Interpretation plan (LLM output, validated + applied deterministically)
# =============================================================================


class EvidenceSpan(BaseModel):
    """
    A verbatim piece of text extracted from BSA input.

    Why we need it:
      - Enforces "no hallucination": any identifier the LLM proposes must be backed
        by a substring of the feedback/answer text.
    """

    source: Literal["FEEDBACK", "ANSWER"] = Field(..., description="Where this evidence came from.")
    evidence_text: str = Field(..., description="Exact substring taken from the input text.")


class StructuredFieldUpdate(BaseModel):
    """
    A single candidate update proposed by the interpretation phase.

    Notes:
      - Only source-side fields are allowed.
      - Step 4 still validates against schema; if invalid, we keep intent but warn.
    """

    field_name: Literal[
        "rule_type",
        "source_entity",
        "source_field_names",
        "lookup_tables",
        "join_condition",
        "row_filter_text",
        "transformation_rules_text",
        "special_considerations_text",
    ] = Field(..., description="Which MappingRow field to update (source-side only).")
    new_value: Any = Field(..., description="Proposed value (validated at apply-time).")
    source: ChangeSource = Field(..., description="Where this change comes from.")
    evidence: List[EvidenceSpan] = Field(
        default_factory=list,
        description="Verbatim evidence spans backing this update (no hallucinated identifiers).",
    )
    rationale: Optional[str] = Field(default=None, description="Short explanation for this update.")


class InterpretationPlan(BaseModel):
    """
    LLM-produced plan for one mapping row (and optionally linked issues).

    Why we store it:
      - Transparency: what did the LLM interpret from BSA input?
      - Debugging: compare plan vs applied changes.
    """

    plan_id: str = Field(..., description="Stable id for this plan item (unique within Step 4 run).")
    row_id: str = Field(..., description="MappingRow.row_id this plan applies to.")

    conflict_winner: ConflictWinner = Field(
        default=ConflictWinner.NONE,
        description="Which input the LLM chose when patch/feedback/answers conflict.",
    )
    conflict_notes: Optional[str] = Field(default=None, description="Human-readable conflict notes.")

    updates: List[StructuredFieldUpdate] = Field(default_factory=list, description="Proposed updates to apply.")

    unresolved: bool = Field(
        default=False,
        description="True if feedback is ambiguous / lacks explicit identifiers; apply nothing and request manual action.",
    )
    extracted_phrases: List[EvidenceSpan] = Field(
        default_factory=list,
        description="Extracted phrases when feedback is too vague to apply deterministically.",
    )


# =============================================================================
# 4) Issue ledger + warnings + audit log
# =============================================================================


class ManualAction(BaseModel):
    """
    Human next-step instruction when Step 4 cannot fully resolve.
    """

    action_title: str = Field(..., description="Short label, e.g. 'Provide exact source column'.")
    action_details: str = Field(..., description="Detailed guidance for the BSA.")
    suggested_location: Optional[str] = Field(default=None, description="Where to fix (UI/Excel column hint).")


class IssueResolution(BaseModel):
    """
    Step 4 resolution status for one Step 2 OpenIssue.
    """

    issue_id: str = Field(..., description="OpenIssue.issue_id from Step 2.")
    issue_type: str = Field(..., description="OpenIssue.issue_type (string for forward compatibility).")
    status: Step4IssueStatus = Field(..., description="Resolution status.")

    affected_row_ids: List[str] = Field(default_factory=list, description="Row ids impacted by this issue.")
    reason_summary: Optional[str] = Field(default=None, description="Short reason for the status decision.")
    manual_actions: List[ManualAction] = Field(default_factory=list, description="Manual actions if unresolved.")

    used_decision_ids: List[str] = Field(default_factory=list, description="Which Step 3 decision_ids were used.")
    used_question_ids: List[str] = Field(default_factory=list, description="Which Step 3 question_ids were used.")

    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Status update timestamp.")


class WarningItem(BaseModel):
    """
    Warning emitted by Step 4.

    Why we need it:
      - Preserve BSA intent while clearly flagging schema conflicts / ambiguity.
    """

    warning_id: str = Field(..., description="Unique warning id.")
    warning_type: WarningType = Field(
        default=WarningType.OTHER,
        description="Structured warning type (avoid relying on text parsing).",
    )
    severity: Severity = Field(default=Severity.WARN, description="Severity level.")
    message: str = Field(..., description="Human-readable warning message.")

    row_id: Optional[str] = Field(default=None, description="Related MappingRow.row_id (if applicable).")
    issue_id: Optional[str] = Field(default=None, description="Related OpenIssue.issue_id (if applicable).")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Warning creation time.")


class IssuePlan(BaseModel):
    """
    LLM-produced issue-centric plan (Subagent B output), stored for transparency.

    Why we store it:
      - Step 4 resolves issues under hard constraints; this captures what the LLM *tried* to do.
      - Final statuses are still derived post-apply + validation (deterministic).
    """

    plan_id: str = Field(..., description="Unique id for this issue plan within the Step 4 run.")
    issue_id: str = Field(..., description="Step2 OpenIssue.issue_id.")
    status_hint: Step4IssueStatus = Field(
        default=Step4IssueStatus.UNRESOLVED,
        description="LLM hint only; final status is computed post-apply + validation.",
    )
    reason_summary: Optional[str] = Field(default=None, description="Why this issue is (un)resolvable based on BSA input.")
    affected_row_ids: List[str] = Field(default_factory=list, description="Row ids this plan targets.")
    used_question_ids: List[str] = Field(
        default_factory=list,
        description="Which Step 3 question_ids were used as evidence for this plan (traceability).",
    )
    used_decision_ids: List[str] = Field(
        default_factory=list,
        description="Which Step 3 decision_ids were used as evidence for this plan (traceability).",
    )

    # Proposed row-level updates to resolve the issue (source-side only).
    row_plans: List[InterpretationPlan] = Field(
        default_factory=list,
        description="Row-level update plans that the resolver suggests for this issue.",
    )

    manual_actions: List[ManualAction] = Field(
        default_factory=list,
        description="If unresolved, what the BSA should do next (explicit identifiers, join keys, etc.).",
    )


class AppliedChange(BaseModel):
    """
    One deterministic applied field change (audit log).
    """

    change_id: str = Field(..., description="Unique change id.")
    row_id: str = Field(..., description="Row id that was changed.")
    field_name: str = Field(..., description="Field that changed.")
    before_value: Any = Field(default=None, description="Value before apply.")
    after_value: Any = Field(default=None, description="Value after apply.")
    source: ChangeSource = Field(..., description="Provenance of the change.")
    rationale: Optional[str] = Field(default=None, description="Short rationale.")
    decision_ids: List[str] = Field(default_factory=list, description="Step 3 decision ids that contributed.")
    question_ids: List[str] = Field(default_factory=list, description="Step 3 question ids that contributed.")
    applied_at: datetime = Field(default_factory=datetime.utcnow, description="When this change was applied.")


# =============================================================================
# 5) Step 4 persisted artifact
# =============================================================================


class Step4Metadata(BaseModel):
    """
    Metadata for Step 4 execution.
    """

    run_id: str = Field(..., description="Run id shared across steps.")
    interface_code: str = Field(..., description="Interface code for this run.")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="When Step 4 artifact was created.")
    created_by: str = Field(default="Step4MainAgent", description="Producer identifier.")

    # Timestamped, unique id for this Step 4 execution (allows multiple Step 4 runs).
    step4_run_id: str = Field(..., description="Unique id for this Step 4 execution.")

    # Traceability to inputs
    input_artifacts: List[ArtifactRef] = Field(default_factory=list, description="Artifacts used to produce this output.")
    step1_metadata_uri: Optional[str] = Field(default=None, description="Optional pointer to Step 1 SharedState file.")
    step2_metadata: Optional[Step2Metadata] = Field(default=None, description="Embedded Step 2 metadata for convenience.")
    step3_metadata: Optional[Step3Metadata] = Field(default=None, description="Embedded Step 3 metadata for convenience.")


class Step4Summary(BaseModel):
    """
    Quick counters for dashboards/QA.
    """

    total_rows_in: int = 0
    total_rows_out: int = 0

    issues_total: int = 0
    issues_resolved: int = 0
    issues_partially_resolved: int = 0
    issues_unresolved: int = 0

    warnings_total: int = 0
    changes_applied_total: int = 0


class Step4State(BaseModel):
    """
    Persisted Step 4 output artifact.

    Contains:
      - Step 2 rows, after applying Step 3 patches + interpreted feedback/answers
      - Issue ledger (separate section)
      - Warnings + manual actions
      - Interpretation plans + applied change log for auditability
    """

    metadata: Step4Metadata = Field(..., description="Step 4 execution metadata.")
    capture_status: CaptureStatus = Field(..., description="Capture status observed from Step 3.5 (gate).")

    # Output mapping rows (same shape as Step 2 MappingRow).
    column_mappings: List[MappingRow] = Field(
        default_factory=list,
        description="Final mapping rows (Step 2 MappingRow shape), with Step 4 changes applied.",
    )
    table_common_filters: List[TableCommonFilter] = Field(
        default_factory=list,
        description="Common filters carried forward from Step 2 (and optionally updated).",
    )

    issue_resolutions: List[IssueResolution] = Field(
        default_factory=list,
        description="Separate issue ledger: resolved/partial/unresolved + manual actions.",
    )
    warnings: List[WarningItem] = Field(default_factory=list, description="Warnings emitted during apply/validation.")

    interpretation_plans: List[InterpretationPlan] = Field(
        default_factory=list,
        description="LLM interpretation outputs (what it proposed), stored for transparency.",
    )
    issue_plans: List[IssuePlan] = Field(
        default_factory=list,
        description="LLM issue-resolution plans (Subagent B output), stored for transparency.",
    )
    change_log: List[AppliedChange] = Field(default_factory=list, description="Deterministic applied changes log.")

    summary: Step4Summary = Field(default_factory=Step4Summary, description="Counters for QA/UI.")
    notes: Optional[str] = Field(default=None, description="Optional notes/debug context.")


__all__ = [
    "AppliedChange",
    "ArtifactKind",
    "ArtifactRef",
    "ChangeSource",
    "ConflictWinner",
    "EvidenceSpan",
    "InterpretationPlan",
    "IssueResolution",
    "IssuePlan",
    "ManualAction",
    "Severity",
    "WarningType",
    "Step4IssueStatus",
    "Step4Metadata",
    "Step4State",
    "Step4Summary",
]
