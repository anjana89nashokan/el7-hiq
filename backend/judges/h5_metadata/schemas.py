from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from models.judge import JudgeEvaluation, RevisionDirective


class FileMetadata(BaseModel):
    file_name: str = ""
    file_description: str = ""
    extract_frequency: str = ""
    file_format: str = ""
    delimiter: str | None = None
    effective_date: str = ""
    layout_version: str = ""
    domain: str = ""
    sub_domain: str = ""
    source_system: str = ""
    target_system: str = ""
    record_count_field: str | None = None
    driver_reference: str = ""
    mapping_reference: str = ""
    created_by: str = ""
    approved_by: str | None = None


class AttributeMetadata(BaseModel):
    position: int = 0
    name: str = ""
    description: str = ""
    data_type: str = ""
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    nullable: bool = True
    source_table: str = ""
    source_column: str = ""
    join_path: str | None = None
    transformation: str | None = None
    match_type: str = "no_match"
    confidence_score: float = 0.0
    indimap_reference: str | None = None
    is_derived: bool = False
    default_value: str | None = None
    validation_rule: str | None = None
    semantic_type: str | None = None


class MetadataBuildOutput(BaseModel):
    file_metadata: FileMetadata = Field(default_factory=FileMetadata)
    attributes: list[AttributeMetadata] = Field(default_factory=list)

    naming_conformance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    type_conformance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    completeness_score: float = Field(default=0.0, ge=0.0, le=1.0)

    indimap_template_json: str = ""

    naming_auto_corrections: list[dict[str, Any]] = Field(default_factory=list)
    naming_manual_flags: list[dict[str, Any]] = Field(default_factory=list)
    type_casts_applied: list[dict[str, Any]] = Field(default_factory=list)
    type_cast_warnings: list[dict[str, Any]] = Field(default_factory=list)

    confidence_score: float = 0.0
    agent_notes: list[str] = Field(default_factory=list)


class H4ApprovedMappingSpec(BaseModel):
    session_id: str = ""
    fields: list[dict[str, Any]] = Field(default_factory=list)
    total_field_count: int = 0
    no_match_fields: list[str] = Field(default_factory=list)
    bsa_h4_overrides: dict[str, Any] = Field(default_factory=dict)


class JudgeInputH5(BaseModel):
    session_id: str
    metadata_output: MetadataBuildOutput
    h4_mapping_spec: H4ApprovedMappingSpec
    original_layout_fields: list[dict[str, Any]] = Field(default_factory=list)
    bsa_rejection_feedback: str | None = None
    previous_evaluation: dict | None = None
    revision_number: int = 0


class JudgeOutputH5(BaseModel):
    evaluation: JudgeEvaluation
    revision_directive: RevisionDirective | None = None
    annotated_metadata: dict[str, Any] = Field(default_factory=dict)
    bsa_review_summary: str = ""
    quality_scorecard: dict[str, Any] = Field(default_factory=dict)
    auto_corrected_output: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Metadata Pipeline Judge — 2-step evaluation schemas
# ---------------------------------------------------------------------------

class MetadataBrdContext(BaseModel):
    """BRD context extracted for metadata cross-check."""

    in_scope: str = ""
    out_of_scope: str = ""
    requirements: str = ""
    filters_and_parameters: dict[str, Any] = Field(default_factory=dict)


class MetadataStepJudgment(BaseModel):
    """Judgment for a single metadata pipeline step."""

    step: str    # "normalization" | "extraction"
    verdict: str  # "PASS" | "WARN" | "BLOCK"
    score: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class MetadataPipelineJudgeInput(BaseModel):
    """Complete input for the metadata pipeline judge."""

    session_id: str
    extracted_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Output of metadata_extractor_agent (filespecs + file1 with attributes).",
    )
    brd_context: MetadataBrdContext = Field(default_factory=MetadataBrdContext)
    layout_columns: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Parsed layout column rows from the layout GCS artifact.",
    )
    revision_number: int = 0


class MetadataPipelineJudgeOutput(BaseModel):
    """Output of the unified metadata pipeline judge."""

    session_id: str
    overall_verdict: str  # "PASS" | "WARN" | "BLOCK"
    overall_score: float = Field(ge=0.0, le=1.0)
    overall_summary: str = ""
    can_proceed: bool = False
    step_judgments: list[MetadataStepJudgment] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    bsa_review_summary: str = ""
    judged_at: str = ""
    quality_scorecard: dict[str, Any] = Field(
        default_factory=dict,
        description="Flat KPI scorecard — overall score and key extraction metrics.",
    )
    rule_scores: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-rule scoring (rule_id, verdict, score, weight, evidence, blocking).",
    )
