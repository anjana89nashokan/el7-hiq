"""
BSA DATAMAP AI Multi-Agent Extract Mapping System — Pipeline Models
===================================================================

Central Pydantic models shared across all five pipeline layers:
  1. Requirement  →  2. Driver  →  3. Discovery  →  4. Metadata  →  5. Mapping

These models define:
  - Pipeline stage enums and status tracking
  - Input/output schemas for each stage
  - MEM1 (short-term) pipeline state container
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ============================================================================
# Pipeline Stage Enums
# ============================================================================


class PipelineStage(str, Enum):
    """Sequential pipeline stages."""

    REQUIREMENT = "requirement"
    DRIVER = "driver"
    DISCOVERY = "discovery"
    METADATA = "metadata"
    MAPPING = "mapping"


class StageStatus(str, Enum):
    """Status of an individual pipeline stage."""

    PENDING = "pending"
    RUNNING = "running"
    DRAFT_READY = "draft_ready"
    BSA_APPROVED = "bsa_approved"
    BSA_REJECTED = "bsa_rejected"
    COMPLETED = "completed"
    FAILED = "failed"


# ============================================================================
# Requirement Layer Outputs (re-export from existing models for composition)
# ============================================================================


class ApprovedRequirements(BaseModel):
    """Snapshot of BSA-approved requirement artifacts."""

    parsed_brd: dict = Field(default_factory=dict)
    parsed_layouts: list[dict] = Field(default_factory=list)
    parsed_transcript: dict = Field(default_factory=dict)
    domain_tagged_fields: dict = Field(default_factory=dict)
    ambiguity_report: dict = Field(default_factory=dict)
    bsa_overrides: dict = Field(default_factory=dict)
    approved_at: Optional[str] = None


# ============================================================================
# Driver Layer Models
# ============================================================================


class NormalizedFilter(BaseModel):
    """A single filter condition extracted from requirements."""

    filter_name: str = Field(..., description="Human-readable filter label")
    filter_type: str = Field(
        ...,
        description="Category: date_range | population | eligibility | custom",
    )
    field_reference: str = Field(
        ..., description="Field name the filter applies to"
    )
    condition: str = Field(..., description="Filter expression/condition")
    source: str = Field(
        ..., description="Origin of this filter: brd | transcript | inferred"
    )


class TargetFieldSpec(BaseModel):
    """A single target field requested in the extract."""

    field_name: str
    data_type: Optional[str] = None
    is_key: bool = False
    brd_instruction: Optional[str] = None
    domain: Optional[str] = None


class ExtractDriver(BaseModel):
    """
    Output of the Driver Layer — a structured extract specification
    derived from approved requirements via the Golden Flow.
    """

    driver_id: str = Field(..., description="Unique driver identifier")
    extract_purpose: str = Field(
        ...,
        description="High-level purpose statement derived from BRD intent",
    )
    normalized_filters: list[NormalizedFilter] = Field(default_factory=list)
    source_tables_hint: list[str] = Field(
        default_factory=list,
        description="Candidate source tables from standards/FYI lookup",
    )
    target_fields: list[TargetFieldSpec] = Field(default_factory=list)
    frequency: Optional[str] = None
    delivery_method: Optional[str] = None
    brd_references: list[str] = Field(default_factory=list)
    fyi_references: list[str] = Field(default_factory=list)
    standards_references: list[str] = Field(default_factory=list)


class ApprovedDrivers(BaseModel):
    """BSA-approved extract drivers."""

    drivers: list[ExtractDriver] = Field(default_factory=list)
    bsa_overrides: dict = Field(default_factory=dict)
    approved_at: Optional[str] = None


# ============================================================================
# Discovery Layer Models
# ============================================================================


class CandidateSource(BaseModel):
    """A single candidate source column/table returned by discovery."""

    source_name: str = Field(..., description="Column or table name")
    source_type: str = Field(
        ..., description="Type: table | column | view"
    )
    database: Optional[str] = None
    schema_name: Optional[str] = None
    table_name: Optional[str] = None
    discovery_source: str = Field(
        ...,
        description="Which discovery tier found this: indimap | adw_standards | fyi | join_repository",
    )
    priority_rank: int = Field(
        ..., description="1=IndiMap, 2=ADW, 3=FYI, 4=JoinRepo"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Discovery confidence score"
    )
    match_evidence: str = Field(
        default="", description="Textual evidence for the match"
    )


class DiscoveryResult(BaseModel):
    """Discovery output for a single target field."""

    target_field: str
    candidates: list[CandidateSource] = Field(default_factory=list)
    selected_source: Optional[CandidateSource] = None
    selection_reasoning: str = ""


class ApprovedDiscovery(BaseModel):
    """BSA-approved source discovery results."""

    discovery_results: list[DiscoveryResult] = Field(default_factory=list)
    bsa_overrides: dict = Field(default_factory=dict)
    approved_at: Optional[str] = None


# ============================================================================
# Metadata Layer Models
# ============================================================================


class NormalizedMetadata(BaseModel):
    """Metadata normalization output for a single field."""

    field_name: str
    normalized_name: str = Field(
        ..., description="snake_case standardized name"
    )
    normalized_data_type: str = Field(
        ...,
        description="Normalized type: STRING | INTEGER | DATE | DECIMAL | BOOLEAN",
    )
    source_data_type: str = Field(
        default="", description="Original data type before normalization"
    )
    length: Optional[int] = None
    precision: Optional[int] = None
    format_pattern: Optional[str] = None
    nullability: Optional[str] = None
    naming_convention: str = Field(
        default="snake_case", description="Applied naming standard"
    )


class ApprovedMetadata(BaseModel):
    """Validated metadata normalization results (auto-approved, no HITL)."""

    normalized_fields: list[NormalizedMetadata] = Field(default_factory=list)
    normalization_summary: dict = Field(default_factory=dict)
    completed_at: Optional[str] = None


# ============================================================================
# Mapping Layer Models
# ============================================================================


class MappingEntry(BaseModel):
    """A single field-level mapping from source to target."""

    target_field: str
    source_field: Optional[str] = None
    source_table: Optional[str] = None
    source_database: Optional[str] = None
    match_type: str = Field(
        ..., description="Match classification: exact | partial | no_match"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Mapping confidence score"
    )
    transformation_rule: Optional[str] = None
    reused_from_indimap: bool = Field(
        default=False,
        description="True if this mapping was reused from IndiMap history (MEM2)",
    )
    indimap_reference_id: Optional[str] = None
    needs_review: bool = Field(
        default=False,
        description="Flagged for BSA review (partial/no_match)",
    )
    mapping_evidence: str = Field(
        default="", description="Evidence supporting this mapping"
    )


class TransformationRule(BaseModel):
    """A single row in the transformation rules table."""

    target_entity: Optional[str] = None
    driver_table_required: Optional[bool] = None
    history_data_pull: Optional[bool] = None
    common_filter: Optional[str] = None
    target_attribute: Optional[str] = None
    logical_attribute_name: Optional[str] = None
    attribute_description: Optional[str] = None
    data_type: Optional[str] = None
    length: Optional[int] = None
    precision: Optional[int] = None
    format: Optional[str] = None
    nullable: Optional[bool] = None
    default_value: Optional[str] = None
    order_no: Optional[int] = None
    cdc_indicator: Optional[str] = None
    key_columns: Optional[str] = None
    rule_type: Optional[str] = None
    rule_name: Optional[str] = None
    source_entity: Optional[str] = None
    source_attribute: Optional[str] = None
    join: Optional[str] = None
    filter: Optional[str] = None
    transformation_rule: Optional[str] = None
    special_consideration: Optional[str] = None
    last_updated: Optional[str] = None


class FinalMapping(BaseModel):
    """BSA-approved final mapping output."""

    mappings: list[MappingEntry] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)
    mapping_summary: dict = Field(default_factory=dict)
    bsa_overrides: dict = Field(default_factory=dict)
    approved_at: Optional[str] = None


# ============================================================================
# Pipeline State (MEM1 — Short-Term Session State)
# ============================================================================


class PipelineState(BaseModel):
    """
    Top-level pipeline state persisted in MEM1 (CloudSQL / session state).

    Tracks which stage the pipeline is on and holds the approved outputs
    from each completed stage. The orchestrator reads/writes this on every
    stage transition.
    """

    session_id: str
    current_stage: PipelineStage = PipelineStage.REQUIREMENT
    stage_statuses: dict[str, str] = Field(
        default_factory=lambda: {
            PipelineStage.REQUIREMENT.value: StageStatus.PENDING.value,
            PipelineStage.DRIVER.value: StageStatus.PENDING.value,
            PipelineStage.DISCOVERY.value: StageStatus.PENDING.value,
            PipelineStage.METADATA.value: StageStatus.PENDING.value,
            PipelineStage.MAPPING.value: StageStatus.PENDING.value,
        }
    )

    # Stage outputs (populated after BSA approval)
    approved_requirements: Optional[ApprovedRequirements] = None
    approved_drivers: Optional[ApprovedDrivers] = None
    approved_discovery: Optional[ApprovedDiscovery] = None
    approved_metadata: Optional[ApprovedMetadata] = None
    final_mapping: Optional[FinalMapping] = None

    # Audit
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = "system"

    def advance_to(self, next_stage: PipelineStage) -> None:
        """Move the pipeline to the next stage."""
        self.current_stage = next_stage
        self.updated_at = datetime.utcnow()

    def set_stage_status(self, stage: PipelineStage, status: StageStatus) -> None:
        """Update the status of a specific stage."""
        self.stage_statuses[stage.value] = status.value
        self.updated_at = datetime.utcnow()

    def is_stage_approved(self, stage: PipelineStage) -> bool:
        """Check if a stage has been BSA-approved."""
        return self.stage_statuses.get(stage.value) == StageStatus.BSA_APPROVED.value

    def get_next_stage(self) -> Optional[PipelineStage]:
        """Return the next stage in the pipeline, or None if complete."""
        order = list(PipelineStage)
        try:
            idx = order.index(self.current_stage)
            if idx + 1 < len(order):
                return order[idx + 1]
        except ValueError:
            pass
        return None
