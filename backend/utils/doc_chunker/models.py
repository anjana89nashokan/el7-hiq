"""
Pydantic models for all LLM inputs/outputs and pipeline state.

Call 1 — ExtractionResult   : typed section extraction (requirements, scope, file layout, etc.)
Call 2 — DomainScoringResult: domain classification scores (separate call, no bleed)

ChunkContext is the rolling handoff state passed between chunks.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Section-typed extraction models
# ---------------------------------------------------------------------------

class Requirement(BaseModel):
    """A single requirement item (functional, non-functional, business rule, etc.)."""
    id: Optional[str] = None            # e.g. "REQ-001" if labelled in the doc
    category: Optional[str] = None      # e.g. "Functional", "Non-Functional", "Business Rule"
    description: str
    priority: Optional[str] = None      # e.g. "High", "Medium", "Low", "Must", "Should"
    source: Optional[str] = None        # e.g. section heading or page reference


class ScopeItem(BaseModel):
    """A single in-scope or out-of-scope item."""
    description: str
    notes: Optional[str] = None


class FileLayoutRecord(BaseModel):
    """
    One row from a file layout / record layout table.
    Covers fixed-width, delimited, and positional formats.
    """
    field_name: str
    position_start: Optional[str] = None
    position_end: Optional[str] = None
    length: Optional[str] = None
    data_type: Optional[str] = None
    format: Optional[str] = None
    nullable: Optional[str] = None
    default_value: Optional[str] = None
    description: Optional[str] = None
    constraints: Optional[str] = None
    section: Optional[str] = None
    extra: Optional[Dict[str, str]] = None

    @field_validator("field_name", mode="before")
    @classmethod
    def _coerce_field_name(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @field_validator("extra", mode="before")
    @classmethod
    def _coerce_extra(cls, v: Any) -> Optional[Dict[str, str]]:
        if not isinstance(v, dict):
            return None
        return {k: ("" if val is None else str(val)) for k, val in v.items()}


class GenericTable(BaseModel):
    """
    Fallback for tables that don't match requirements / scope / file layout.
    Preserves all content without loss.
    """
    heading: Optional[str] = None
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    is_continuation: bool = False
    is_complete: bool = True

    @field_validator("headers", mode="before")
    @classmethod
    def _coerce_headers(cls, v: Any) -> List[str]:
        if not isinstance(v, list):
            return []
        return ["" if cell is None else str(cell) for cell in v]

    @field_validator("rows", mode="before")
    @classmethod
    def _coerce_rows(cls, v: Any) -> List[List[str]]:
        if not isinstance(v, list):
            return []
        coerced = []
        for row in v:
            if not isinstance(row, list):
                continue
            coerced.append(["" if cell is None else str(cell) for cell in row])
        return coerced


# ---------------------------------------------------------------------------
# Open-section state (what is mid-flight at chunk boundary)
# ---------------------------------------------------------------------------

class OpenSectionState(BaseModel):
    """
    Describes the section that was still in progress at the end of the previous chunk.
    Injected verbatim into the next chunk's extraction prompt.
    """
    section_type: str
    heading: Optional[str] = None
    headers: List[str] = Field(default_factory=list)
    last_row: List[str] = Field(default_factory=list)
    last_item_description: Optional[str] = None

    @field_validator("headers", "last_row", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> List[str]:
        if not isinstance(v, list):
            return []
        return ["" if cell is None else str(cell) for cell in v]


# ---------------------------------------------------------------------------
# Call 1 — typed extraction output
# ---------------------------------------------------------------------------

class ExtractionResult(BaseModel):
    chunk_index: int
    page_range: str

    # Typed section buckets
    requirements: List[Requirement] = Field(default_factory=list)
    in_scope: List[ScopeItem] = Field(default_factory=list)
    out_of_scope: List[ScopeItem] = Field(default_factory=list)
    file_layout: List[FileLayoutRecord] = Field(default_factory=list)
    generic_tables: List[GenericTable] = Field(default_factory=list)

    # Continuation flags — which section (if any) continues into the next chunk
    open_section: Optional[OpenSectionState] = None

    # Handoff summary for the next chunk's prompt context
    handoff_summary: str = Field(
        description=(
            "1-3 sentences describing what is still open at the end of this chunk. "
            "State the section type, heading, and last item/row so the next chunk "
            "can continue without loss. E.g.: 'File layout table \"Detail Record\" is "
            "still in progress — last row was [\"TXN_AMT\",\"9\",\"15\",\"DECIMAL\"]. "
            "A new section \"Out of Scope\" appears to be starting on the last page.'"
        )
    )


# ---------------------------------------------------------------------------
# Call 2 — domain scoring output (unchanged, kept separate to avoid bleed)
# ---------------------------------------------------------------------------

class DomainScoringResult(BaseModel):
    chunk_index: int
    scores: Dict[str, float] = Field(
        description="Domain label → confidence score 0-10. All labels must be present."
    )
    top_domain: str
    rationale: str


# ---------------------------------------------------------------------------
# Rolling context passed between chunks
# ---------------------------------------------------------------------------

class ChunkContext(BaseModel):
    """State carried forward from chunk N to chunk N+1."""
    previous_handoff_summary: Optional[str] = None
    open_section: Optional[OpenSectionState] = None
    accumulated_domain_scores: Dict[str, float] = Field(default_factory=dict)
    chunks_processed: int = 0


# ---------------------------------------------------------------------------
# Final pipeline output
# ---------------------------------------------------------------------------

class PipelineResult(BaseModel):
    document_path: str
    total_chunks: int
    total_pages: int

    chunk_extractions: List[ExtractionResult] = Field(default_factory=list)
    chunk_domain_scores: List[DomainScoringResult] = Field(default_factory=list)

    # Merged / stitched outputs across all chunks
    requirements: List[Requirement] = Field(default_factory=list)
    in_scope: List[ScopeItem] = Field(default_factory=list)
    out_of_scope: List[ScopeItem] = Field(default_factory=list)
    file_layout: List[FileLayoutRecord] = Field(default_factory=list)
    generic_tables: List[GenericTable] = Field(default_factory=list)

    final_domain: str
    final_domain_scores: Dict[str, float] = Field(default_factory=dict)
    failed_chunks: Dict[int, str] = Field(default_factory=dict)
