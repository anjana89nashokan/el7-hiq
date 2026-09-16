"""
Extract Agent Models — Driver Layer
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional


class FilterCandidate(BaseModel):
    brd_concept: str = ""
    brd_source: str = "unknown"
    filter_category: str = "unknown"
    dart_field: str = ""
    dart_table: str = ""
    dart_layer: str = "ILDWP1V"
    filter_type: str = "include"
    suggested_values: List[str] = Field(default_factory=list)
    sql_clause: Optional[str] = None
    standards_reference: Optional[str] = None
    confidence: float = 0.0
    needs_fyi_lookup: bool = False
    mapping_notes: Optional[str] = None
    open_item: bool = False
    open_item_reason: Optional[str] = None
    # Set when open_item=True: human-readable question surfaced to BSA at Checkpoint 2.
    # e.g. "Standards search unreachable — BSA to confirm IBC_FOC_LVL_CD is correct for 'IBC and TPA company filter'"
    bsa_question: Optional[str] = None
    filter_scope: str = "global"
    file_name: Optional[str] = None

    @field_validator(
        "brd_concept", "brd_source", "filter_category", "dart_field",
        "dart_table", "dart_layer", "filter_type", "filter_scope",
        mode="before"
    )
    @classmethod
    def coerce_none_to_str(cls, v, info):
        defaults = {
            "dart_layer": "ILDWP1V",
            "filter_scope": "global",
            "brd_source": "unknown",
            "filter_category": "unknown",
            "filter_type": "include"
        }
        if v is not None:
            return v
        return defaults.get(info.field_name, "")

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_none_to_float(cls, v):
        return v if v is not None else 0.0

    @field_validator("suggested_values", mode="before")
    @classmethod
    def coerce_none_to_list(cls, v):
        return v if v is not None else []


class DriverMapping(BaseModel):
    filter_candidates: List[FilterCandidate]
    unmapped_concepts: List[str] = Field(default_factory=list)
    ibc_aha_context: str = "IBC"  # "IBC" | "AHA" | "both"


class CommonFilter(BaseModel):
    filter_id: str = ""
    filter_category: str = "unknown"
    filter_scope: str = "global"
    file_name: Optional[str] = None
    dart_field: str = ""
    dart_table: str = ""
    dart_layer: str = "ILDWP1V"
    filter_type: str = "include"
    filter_values: List[str] = Field(default_factory=list)
    sql_clause: str = ""
    odf_sel_crta_ref: Optional[str] = None
    brd_traceability: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    source: str = "BRD"
    open_item: bool = False
    open_item_reason: Optional[str] = None
    # Human-readable question for BSA at Checkpoint 2.
    # Carried from FilterCandidate.bsa_question — surfaced in the open-items panel.
    bsa_question: Optional[str] = None
    notes: str = ""

    @field_validator(
        "filter_id", "filter_category", "filter_scope", "dart_field",
        "dart_table", "dart_layer", "filter_type", "sql_clause",
        "source", "notes", mode="before"
    )
    @classmethod
    def coerce_none_to_str(cls, v, info):
        defaults = {
            "filter_scope": "global",
            "dart_layer": "ILDWP1V",
            "source": "BRD",
            "notes": "",
            "filter_category": "unknown",
            "filter_type": "include"
        }
        if v is not None:
            return v
        return defaults.get(info.field_name, "")

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_none_to_float(cls, v):
        return v if v is not None else 0.0

    @field_validator("filter_values", "brd_traceability", mode="before")
    @classmethod
    def coerce_none_to_list(cls, v):
        return v if v is not None else []


class DriverLogic(BaseModel):
    common_filters: List[CommonFilter]
    sql_where_clause: str
    global_filter_count: int
    file_level_filter_count: int
    open_item_count: int
    ibc_aha_context: str


class ValidationIssue(BaseModel):
    issue_type: str  # missing_brd_trace | conflict | transformation_logic | standards_violation
    severity: str    # high | medium | low
    filter_id: Optional[str] = None
    description: str
    recommended_action: str


class DriverValidation(BaseModel):
    issues: List[ValidationIssue]
    total_high: int
    total_medium: int
    all_brd_requirements_traced: bool
    no_transformation_logic: bool
    standards_compliant: bool
    can_proceed: bool


class ApprovedDriverLogic(BaseModel):
    common_filters: List[CommonFilter]
    sql_where_clause: str
    ibc_aha_context: str
    bsa_edits: Optional[dict] = None
    bsa_notes: Optional[str] = None
    approved_at: Optional[str] = None
