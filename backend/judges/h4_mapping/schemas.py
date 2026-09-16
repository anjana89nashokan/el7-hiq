from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Mapping Pipeline Judge — input / output schemas
# ---------------------------------------------------------------------------

class MappingBrdContext(BaseModel):
    """BRD context extracted for mapping cross-check."""

    in_scope: str = ""
    out_of_scope: str = ""
    requirements: str = ""
    common_rules: dict[str, Any] = Field(default_factory=dict)
    file_attributes_mapping: dict[str, Any] = Field(default_factory=dict)


class MappingStepJudgment(BaseModel):
    """Judgment for the mapping evaluation step."""

    step: str   # "mapping"
    verdict: str  # "PASS" | "WARN" | "BLOCK"
    score: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class MappingPipelineJudgeInput(BaseModel):
    """Complete input for the mapping pipeline judge."""

    session_id: str
    mapping_result: dict[str, Any] = Field(
        default_factory=dict,
        description="Output of the mapping stage (common_rules + transformation_rules.rows).",
    )
    brd_context: MappingBrdContext = Field(default_factory=MappingBrdContext)
    common_filter: str = Field(
        default="",
        description="SQL WHERE clause from the driver layer (transformation_rules.common_filter).",
    )
    driver_predicates: list[dict[str, Any]] = Field(
        default_factory=list,
        description="common_filters list from driver_data — used for driver/transformation separation check.",
    )
    layout_columns: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Layout column rows used as the source-of-truth for field coverage.",
    )
    metadata_attributes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Full attribute dicts from the metadata extraction (12-key shape).",
    )
    revision_number: int = 0


class MappingPipelineJudgeOutput(BaseModel):
    """Output of the mapping pipeline judge."""

    session_id: str
    overall_verdict: str  # "PASS" | "WARN" | "BLOCK"
    overall_score: float = Field(ge=0.0, le=1.0)
    overall_summary: str = ""
    can_proceed: bool = False
    step_judgments: list[MappingStepJudgment] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    bsa_review_summary: str = ""
    judged_at: str = ""
    quality_scorecard: dict[str, Any] = Field(
        default_factory=dict,
        description="Flat KPI scorecard — overall + per-rule scores and key metrics.",
    )
    rule_scores: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-rule scoring (rule_id, verdict, score, weight, evidence, blocking).",
    )
