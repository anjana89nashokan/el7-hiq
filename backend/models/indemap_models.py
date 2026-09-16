from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class DatabaseTablesRequest(BaseModel):
    """Request model for a single database with its table names."""
    database_name: str
    tables: List[str] = Field(..., min_length=1)


class EntityLookupResponse(BaseModel):
    """Response wrapper for entity lookup results."""
    total_tables: int
    tables: List[Dict[str, Any]]
    not_found: List[Dict[str, str]] = []
    timestamp: Optional[str] = None


# ------------------------------------------------------------------
# Mapping Rules Lookup
# ------------------------------------------------------------------


class MappingRulesLookupRequest(BaseModel):
    """Request model for mapping rules lookup by target column name."""
    target_column_name: str = Field(
        ..., min_length=1,
        description="Target column name to look up mapping rules for",
    )
    top_n: Optional[int] = Field(
        None, ge=1, le=100,
        description="Max rules to return (defaults to config INDEMAP_TOP_N_MAPPINGS)",
    )
    im_map_cd: Optional[str] = Field(
        "SRC",
        description="Map code filter on IM_INTF_CD (default 'SRC')",
    )


class MappingRuleDetail(BaseModel):
    """A single historical mapping rule from IndeMap."""
    # From C (target column name)
    target_column_name: Optional[str] = None

    # From TE (mapping header)
    interface_code: Optional[str] = None
    common_filter: Optional[str] = None

    # From TA (rule detail)
    rule_type_code: Optional[str] = None
    source_entity_text: Optional[str] = None
    source_column_text: Optional[str] = None
    join_text: Optional[str] = None
    rule_text: Optional[str] = None
    rule_sequence_no: Optional[int] = None
    special_text: Optional[str] = None
    filter_text: Optional[str] = None
    cdc_indicator: Optional[str] = None
    doc_value: Optional[str] = None
    last_updated: Optional[str] = None

    # From SE (source association — may be null)
    source_column_sk: Optional[int] = None

    # From SA (resolved source column name — may be null)
    source_column_name: Optional[str] = None


class MappingRulesLookupResponse(BaseModel):
    """Response wrapper for mapping rules lookup results."""
    column_name: str
    top_n: int
    total_rules: int
    rules: List[MappingRuleDetail]
    timestamp: Optional[str] = None
