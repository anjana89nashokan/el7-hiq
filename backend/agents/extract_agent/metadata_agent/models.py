"""
Metadata Layer — Pydantic models for normalization.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DataTypeMapping(BaseModel):
    """Mapping from a source data type to a normalized type."""

    source_type: str
    normalized_type: str
    length: Optional[int] = None
    precision: Optional[int] = None


class NamingStandardization(BaseModel):
    """Result of name standardization."""

    original_name: str
    standardized_name: str
    convention_applied: str = "snake_case"
    abbreviations_expanded: list[str] = Field(default_factory=list)


class MetadataValidationIssue(BaseModel):
    """A single validation issue found during metadata normalization."""

    field_name: str
    issue_type: str  # type_mismatch | name_conflict | missing_type | format_error
    severity: str  # HIGH | MEDIUM | LOW
    description: str
    suggested_fix: Optional[str] = None
