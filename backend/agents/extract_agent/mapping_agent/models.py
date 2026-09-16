"""
Mapping Layer — Pydantic models.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class MappingRow(BaseModel):
    """One row in transformation_rules.rows — one per target attribute."""

    target_attribute: str
    logical_attribute_name: Optional[str] = None
    attribute_description: Optional[str] = None
    data_type: Optional[str] = None
    length: Optional[str] = None
    precision: Optional[str] = None
    format: Optional[str] = None
    nullable: Optional[str] = None
    default_value: Optional[str] = None
    order_no: Optional[str] = None
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
    match_level: Optional[str] = None       # L1 | L2 | L3 | null
    match_score: Optional[float] = None     # 0.0–1.0
    open_item: bool = False
    open_item_reason: Optional[str] = None


class MappingGenerationSummary(BaseModel):
    """Summary statistics for a mapping run."""

    total_fields: int = 0
    l1_matches: int = 0
    l2_matches: int = 0
    l3_matches: int = 0
    open_items: int = 0
    optional_defaults: int = 0
