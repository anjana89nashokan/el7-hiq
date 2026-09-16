"""
Discovery Layer — Pydantic models for warehouse source discovery.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class IndiMapMatch(BaseModel):
    """A historical mapping match from IndiMap (MEM2)."""

    mapping_id: str
    source_table: str
    source_column: str
    target_column: str
    interface_code: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    usage_count: int = 0
    last_used_at: Optional[str] = None


class AdwStandardMatch(BaseModel):
    """A match from ADW Standards document search (MEM3)."""

    standard_id: str
    table_name: str
    column_name: Optional[str] = None
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class FyiDictionaryMatch(BaseModel):
    """A match from FYI / Data Dictionary (MEM3)."""

    field_name: str
    table_name: Optional[str] = None
    description: str = ""
    data_type: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class JoinRepositoryMatch(BaseModel):
    """A match from the Join Repository / ERwin graph."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    join_type: str = "INNER"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    path_hops: int = 1


class DiscoveryContext(BaseModel):
    """Aggregated context for the discovery engine."""

    target_fields: list[str] = Field(default_factory=list)
    extract_drivers: list[dict] = Field(default_factory=dict)
    domain: str = "unknown"
    source_tables_hint: list[str] = Field(default_factory=list)
