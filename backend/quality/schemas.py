from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Request payloads
#
# Field names mirror the existing judge endpoints in extracts.py (e.g.
# JudgeDriverRequest at line 2854) so callers don't have to change.  Internals
# are entirely new and do not import from server/judges.
# --------------------------------------------------------------------------- #


class JudgeDriverRequest(BaseModel):
    userId: str
    sessionId: str
    brd_uri: str
    driver_mapping: Dict[str, Any]
    driver_logic: Dict[str, Any]
    driver_validation: Dict[str, Any]
    revision_number: int = 0


class JudgeMetadataRequest(BaseModel):
    userId: str
    sessionId: str
    brd_uri: str
    layout_uri: str
    extracted_metadata: Dict[str, Any]
    revision_number: int = 0


class JudgeMappingRequest(BaseModel):
    userId: str
    sessionId: str
    brd_uri: str
    driver_uri: str
    metadata_uri: str
    mapping_result: Optional[Dict[str, Any]] = None
    mapping_uri: Optional[str] = None
    revision_number: int = 0


class JudgeRequirementsRequest(BaseModel):
    user_id: str
    session_id: str
    brd_gcs_uri: str
    layout_gcs_uri: str
    transcript_gcs_uri: Optional[str] = None
    brd_markdown_gcs_uri: Optional[str] = None
    layout_markdown_gcs_uri: Optional[str] = None
    requirement_layer: Optional[Dict[str, Any]] = None
    requirement_layer_uri: Optional[str] = None
    file_layout_tables: Optional[List[Dict[str, Any]]] = None
    revision_number: int = 0


# --------------------------------------------------------------------------- #
# Per-item + KPI + response models
# --------------------------------------------------------------------------- #


ItemType = Literal["required", "produced"]
Verdict = Literal["pass", "warn", "fail"]
LayerName = Literal["requirements", "metadata", "mapping", "driver"]


class PerItemJudgment(BaseModel):
    item_id: str
    item_type: ItemType
    present_in_output: Optional[bool] = None
    supported_by_source: Optional[bool] = None
    contradicts_source: Optional[bool] = None
    follows_instructions: bool
    evidence_quote: Optional[str] = None
    rationale: str = ""


class LlmJudgment(BaseModel):
    verdict: Verdict
    summary: str
    findings: List[str] = Field(default_factory=list)
    per_item_judgments: List[PerItemJudgment] = Field(default_factory=list)


class KpiScore(BaseModel):
    score: float
    numerator: int
    denominator: int
    definition: str


class LayerJudgmentResponse(BaseModel):
    success: bool
    session_id: str
    layer: LayerName
    revision_number: int
    judged_at: str
    kpis: Dict[str, KpiScore]
    llm_judgment: LlmJudgment
    artifact_gcs_uri: str
