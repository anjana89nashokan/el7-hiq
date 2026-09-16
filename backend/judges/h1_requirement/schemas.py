from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from models.judge import JudgeEvaluation, RevisionDirective


class RequirementModelInput(BaseModel):
    """
    The full RequirementModel produced by the RequirementInterpreter.
    This is the artifact the judge evaluates.
    """

    extract_purpose: str
    scope: dict[str, Any]
    explicit_filters: list[dict[str, Any]]
    compliance_flags: list[str]
    stakeholder_references: list[dict]
    output_fields: list[dict[str, Any]]
    total_field_count: int
    implicit_rules: list[dict[str, Any]]
    conflicts_with_brd: list[dict]
    ambiguities: list[dict[str, Any]]
    blocking_count: int
    primary_domain: str
    sub_domain: str
    domain_confidence: float
    complexity_score: int
    recommended_catalogs: list[str]
    confidence_score: float
    agent_notes: list[str]


class JudgeInputH1(BaseModel):
    """Complete input package for the H1 judge."""

    session_id: str
    requirement_model: RequirementModelInput
    brd_text: str = Field(
        description="Full extracted text of the BRD. Judge uses this to verify citations."
    )
    layout_text: str | None = Field(
        default=None,
        description="Full markdown text of the file layout document."
    )
    layout_raw: list[dict] = Field(
        description="Raw parsed layout rows from the file layout document."
    )
    transcript_texts: list[str] = Field(
        default_factory=list,
        description="Full text of each transcript. Empty if none provided.",
    )
    bsa_rejection_feedback: str | None = Field(
        default=None,
        description="Verbatim BSA rejection text. None when running in pre-judge mode.",
    )
    previous_evaluation: dict | None = Field(
        default=None,
        description="The prior JudgeEvaluation dict. None on first pre-judge run.",
    )
    revision_number: int = 0


class JudgeOutputH1(BaseModel):
    """
    Output produced by the H1 judge.
    In pre-judge mode: contains evaluation + optional block directive.
    In post-judge mode: contains evaluation + mandatory revision directive.
    """

    evaluation: JudgeEvaluation
    revision_directive: RevisionDirective | None = None
    annotated_artifact: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The RequirementModel with inline judge annotations added. "
            "This is what the BSA sees at checkpoint H1 — not the raw model."
        ),
    )
    bsa_review_summary: str = Field(
        description=(
            "Plain-English summary for the BSA review interface. "
            "3-5 sentences."
        )
    )
