"""
Mapping Layer — build_mapping_row_tool

Assembles the final MappingRow dict from agent search results and appends it to
session state["mapping_rows"]. Called exactly once per field at the end of the
L1 → L2 → L3 waterfall.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from google.adk.tools import ToolContext
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class BuildMappingRowInput(BaseModel):
    # ── Target attribute metadata (passed through from the input payload) ──────
    target_attribute: str = Field(..., description="Physical column name e.g. 'SSN_LAST4'")
    logical_attribute_name: Optional[str] = None
    attribute_description: Optional[str] = None
    data_type: Optional[str] = None
    length: Optional[str] = None
    precision: Optional[str] = None
    format: Optional[str] = None
    nullable: Optional[str] = None
    default_value: Optional[str] = None
    key_columns: Optional[str] = None

    # ── Mapping result (from L1 / L2 / L3 or open_item) ──────────────────────
    rule_type: Optional[str] = Field(
        None,
        description=(
            "Mapping rule type. "
            "L1: copy as-is from IndeMap (e.g. 'Direct', 'Derived', 'Lookup'). "
            "L2/L3: use 'Lookup' as default."
        ),
    )
    source_entity: Optional[str] = Field(
        None, description="Source DART table name e.g. 'MBR_ENRL'"
    )
    source_attribute: Optional[str] = Field(
        None,
        description=(
            "Source column name or expression e.g. 'SRC_MBR_ID'. "
            "Null when L3 is used (L3 returns table-level only)."
        ),
    )
    join: Optional[str] = Field(None, description="JOIN clause from L1 IndeMap history")
    filter_text: Optional[str] = Field(
        None,
        description=(
            "Row-level filter from L1 IndeMap history. "
            "Named filter_text to avoid Python keyword conflict with 'filter'."
        ),
    )
    transformation_rule: Optional[str] = Field(
        None,
        description=(
            "Transformation logic. "
            "L1: copy as-is from IndeMap Transformation Rule. "
            "L2: use Transformation Logic field if not blank or N/A. "
            "L3 / open_item: null."
        ),
    )
    special_consideration: Optional[str] = Field(
        None, description="Special notes from L1 IndeMap history"
    )
    cdc_indicator: Optional[str] = Field(
        None, description="CDC indicator from L1 IndeMap history"
    )

    # ── Match provenance ───────────────────────────────────────────────────────
    match_level: Optional[str] = Field(
        None, description="'L1' | 'L2' | 'L3' | null when open_item"
    )
    match_score: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence score 0–1. "
            "L1/L3: 1 - Similarity Distance. "
            "L2: null (AnswerQuery API does not return a numeric score)."
        ),
    )
    open_item: bool = Field(
        False,
        description="True when no match ≥ 50% threshold found at any level",
    )
    open_item_reason: Optional[str] = Field(
        None,
        description="Human-readable reason shown to BSA at review. Set when open_item=True.",
    )

    @field_validator("open_item", mode="before")
    @classmethod
    def coerce_open_item(cls, v):
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "yes", "1")

    @field_validator("nullable", "rule_type", "source_entity", "source_attribute",
                     "join", "filter_text", "transformation_rule",
                     "special_consideration", "cdc_indicator", "match_level",
                     "open_item_reason", mode="before")
    @classmethod
    def coerce_empty_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return None if s in ("", "[N/A]", "N/A", "Not specified", "None") else s


def build_mapping_row_tool(
    input: BuildMappingRowInput,
    tool_context: ToolContext = None,
) -> str:
    """
    Assemble the final MappingRow dict and append it to session state["mapping_rows"].

    Called exactly ONCE per field at the end of the L1 → L2 → L3 waterfall.
    The endpoint reads state["mapping_rows"] after the agent completes and returns
    the list as transformation_rules.rows in the API response.

    Field name note: 'filter_text' in the input maps to 'filter' in the output row
    to match the transformation_rules.rows schema expected by the UI.
    """
    if isinstance(input, dict):
        input = BuildMappingRowInput(**input)

    row = {
        "target_attribute":       input.target_attribute,
        "logical_attribute_name": input.logical_attribute_name,
        "attribute_description":  input.attribute_description,
        "data_type":              input.data_type,
        "length":                 input.length,
        "precision":              input.precision,
        "format":                 input.format,
        "nullable":               input.nullable,
        "default_value":          input.default_value,
        "order_no":               None,
        "cdc_indicator":          input.cdc_indicator,
        "key_columns":            input.key_columns,
        "rule_type":              input.rule_type,
        "rule_name":              None,
        "source_entity":          input.source_entity,
        "source_attribute":       input.source_attribute,
        "join":                   input.join,
        "filter":                 input.filter_text,
        "transformation_rule":    input.transformation_rule,
        "special_consideration":  input.special_consideration,
        "last_updated":           datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "match_level":            input.match_level,
        "match_score":            round(input.match_score, 3) if input.match_score is not None else None,
        "open_item":              input.open_item,
        "open_item_reason":       input.open_item_reason if input.open_item else None,
    }

    if tool_context is not None:
        existing: list = tool_context.state.get("mapping_rows") or []
        existing.append(row)
        tool_context.state["mapping_rows"] = existing

    status = "open_item" if input.open_item else f"matched via {input.match_level}"
    logger.info(
        "[build_mapping_row_tool] target=%r status=%s source=%s.%s rule_type=%s",
        input.target_attribute,
        status,
        input.source_entity or "—",
        input.source_attribute or "—",
        input.rule_type or "—",
    )
    return (
        f"Row built for '{input.target_attribute}': "
        f"source={input.source_entity or '—'}.{input.source_attribute or '—'}, "
        f"rule_type={input.rule_type or '—'}, "
        f"match_level={input.match_level or 'none'}, "
        f"open_item={input.open_item}."
    )
