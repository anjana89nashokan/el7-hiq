from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from models.judge import JudgeEvaluation, RevisionDirective


class DriverCriteriaInput(BaseModel):
    """The DriverCriteria artifact produced by the DriverGenerator."""

    where_clause: str = Field(
        default="",
        description="Complete SQL WHERE clause string.",
    )
    predicates: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Structured predicate list. Each entry can include: "
            "id, business_field, standard_field, operator, values, direction, "
            "brd_source_text, brd_section, fyi_used, parameterization_applied, raw."
        ),
    )

    normalized_filters: list[dict[str, Any]] = Field(default_factory=list)
    incomplete_filters: list[dict[str, Any]] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)

    fyi_lookups: list[dict[str, Any]] = Field(default_factory=list)
    logic_connectors: dict[str, Any] = Field(default_factory=dict)
    estimated_row_impact: str | None = None

    activated_rules: list[str] = Field(default_factory=list)
    bypassed_rules: list[str] = Field(default_factory=list)

    validation_passed: bool = True
    validation_notes: list[str] = Field(default_factory=list)

    confidence_score: float = 0.0
    agent_notes: list[str] = Field(default_factory=list)


class H1ApprovedRequirementModel(BaseModel):
    """Snapshot of the H1-approved RequirementModel passed into the H2 judge."""

    extract_purpose: str = ""
    scope: dict[str, Any] = Field(default_factory=dict)
    explicit_filters: list[dict[str, Any]] = Field(default_factory=list)
    implicit_rules: list[dict[str, Any]] = Field(default_factory=list)
    ambiguities: list[dict[str, Any]] = Field(default_factory=list)
    primary_domain: str = "Other"
    complexity_score: int = 1
    bsa_h1_resolutions: dict[str, Any] = Field(default_factory=dict)


class JudgeInputH2(BaseModel):
    """Complete input package for the H2 judge."""

    session_id: str
    driver_criteria: DriverCriteriaInput
    h1_requirement_model: H1ApprovedRequirementModel
    brd_text: str = ""
    standards_dictionary: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of business terms to approved standard field names.",
    )
    bsa_rejection_feedback: str | None = None
    previous_evaluation: dict | None = None
    revision_number: int = 0


class JudgeOutputH2(BaseModel):
    """Output produced by the H2 judge."""

    evaluation: JudgeEvaluation
    revision_directive: RevisionDirective | None = None
    annotated_driver: dict[str, Any] = Field(default_factory=dict)
    bsa_review_summary: str = ""
    sql_analysis_report: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Driver Pipeline Judge — 3-step evaluation schemas
# ---------------------------------------------------------------------------

class BrdContext(BaseModel):
    """Extracted from the BRD JSON so the judge can cross-check agent outputs."""

    in_scope: str = ""
    out_of_scope: str = ""
    requirements: str = ""
    filters_and_parameters: dict[str, Any] = Field(default_factory=dict)
    # Flattened list of non-empty filter keys from filters_and_parameters
    # (the judge uses this to check BRD coverage by Step 1)
    active_filter_keys: list[str] = Field(default_factory=list)


class StepJudgment(BaseModel):
    """Judgment for a single driver pipeline step."""

    step: str   # "business_mapping" | "logic_builder" | "driver_validation"
    verdict: str  # "PASS" | "WARN" | "BLOCK"
    score: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    # Structured detail dict — step-specific keys (unmapped_count, transform_count, etc.)
    details: dict[str, Any] = Field(default_factory=dict)


class DriverPipelineJudgeInput(BaseModel):
    """
    Complete input for the unified driver pipeline judge.

    brd_context is built from the downloaded BRD JSON and contains the
    normalised requirement layer fields so the judge can verify agent coverage.
    """

    session_id: str
    # ---- outputs from the 3 driver pipeline steps ----
    driver_mapping: dict[str, Any] = Field(
        default_factory=dict,
        description="Output of business_mapping_agent (Step 1) from session state.",
    )
    driver_logic: dict[str, Any] = Field(
        default_factory=dict,
        description="Output of logic_builder_agent (Step 2) from session state.",
    )
    driver_validation: dict[str, Any] = Field(
        default_factory=dict,
        description="Output of driver_validator_agent (Step 3) from session state.",
    )
    # ---- BRD context ----
    brd_context: BrdContext = Field(
        default_factory=BrdContext,
        description="Normalised BRD data used to cross-check agent coverage.",
    )
    revision_number: int = 0


class DriverPipelineJudgeOutput(BaseModel):
    """Output of the unified driver pipeline judge."""

    session_id: str
    overall_verdict: str  # "PASS" | "WARN" | "BLOCK"
    overall_score: float = Field(ge=0.0, le=1.0)
    overall_summary: str = ""
    can_proceed: bool = False
    step_judgments: list[StepJudgment] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    bsa_review_summary: str = ""
    judged_at: str = ""
    quality_scorecard: dict[str, Any] = Field(
        default_factory=dict,
        description="Flat KPI scorecard — overall + per-step scores and key metrics.",
    )
    rule_scores: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-rule scoring (rule_id, verdict, score, weight, evidence, blocking).",
    )
