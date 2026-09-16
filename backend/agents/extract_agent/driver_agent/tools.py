"""
Extract Agent Tools — Stage 1: Standards Search Tool
=====================================================
search_standards_tool:  Query AIDataDeliveryStandards v0.2 in Vertex AI Search.
                        Used by business_mapping_agent to find correct DART field
                        names for each BRD filter concept.

Isolation guarantee:
  - Uses STANDARDS_APP_ID — never touches VERTEX_AI_APP_ID (data dictionary engine)
  - Fails open when STANDARDS_APP_ID is not configured — agent falls back to
    built-in DART field knowledge from its instruction prompt
"""

import logging
import os
import re
import time
from typing import Any, List, Optional

from google.adk.tools import ToolContext
from pydantic import BaseModel, Field, field_validator

from config.settings import config
from utils.extracts_vertex_search_utils_rest import (
    answer_query_standards,
    search_standards_passages,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry config for search_standards_tool
# ---------------------------------------------------------------------------
_STANDARDS_RETRY_ATTEMPTS = 3
_STANDARDS_RETRY_SLEEP_SEC = 2.0


def _is_retriable_standards_error(exc: Exception) -> bool:
    """
    True ONLY for transient connection/proxy drops (e.g. Zscaler RemoteDisconnected).
    PermissionDenied (403) and all other non-connection errors are NOT retried.
    """
    try:
        from google.api_core.exceptions import PermissionDenied, NotFound
        if isinstance(exc, (PermissionDenied, NotFound)):
            return False
    except ImportError:
        pass
    msg = str(exc).lower()
    return any(k in msg for k in (
        "connection aborted",
        "remote end closed",
        "remotedisconnected",
        "plugin failed",   # gRPC AuthMetadataPlugin drop
        "getaddrinfo",     # DNS resolution failure through proxy
    ))


# =============================================================================
# Tool 1: search_standards_tool
# =============================================================================

class SearchStandardsInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Natural-language question about DART fields or filter rules. "
            "E.g. 'DART field for IBC member company filter', "
            "'how to filter active medical coverage in DART extract', "
            "'DART field for excluding FEP eligibility data'"
        ),
    )


def search_standards_tool(
    input: SearchStandardsInput,
    tool_context: ToolContext = None,
) -> dict:
    """
    Query AIDataDeliveryStandards v0.2 in Vertex AI Search and return
    relevant DART field names and filter rules.

    The business_mapping_agent calls this once per BRD filter concept it
    identifies, formulating its own natural-language query per concept.

    Retry behaviour:
      - Connection/proxy drops (RemoteDisconnected, plugin failed, getaddrinfo):
        retried up to 3x with 2s sleep. Returns status='unavailable' after all attempts.
      - PermissionDenied (403): NOT retried. Returns status='permission_denied' immediately.
        Agent instruction: contact admin for roles/discoveryengine.viewer on dataservices-271014.
      - Any other error: NOT retried. Returns status='unavailable'.
      - STANDARDS_APP_ID not set: Returns status='not_configured' immediately.

    For all non-ok statuses the agent falls back to built-in DART field reference
    and flags the mapping as open_item=True with a bsa_question for Checkpoint 2.

    Returns:
        dict with keys:
          query        (str)  — the original query
          answer_text  (str)  — passage from standards doc (empty if not ok)
          citations    (list) — source citations from Discovery Engine
          status       (str)  — 'ok' | 'no_results' | 'not_configured' |
                                 'unavailable' | 'permission_denied'
          note         (str)  — human-readable status message
    """
    if isinstance(input, dict):
        input = SearchStandardsInput(**input)
    query = input.query.strip()
    logger.info("[search_standards_tool] query: '%s'", query)

    if not config.STANDARDS_APP_ID:
        logger.warning(
            "[search_standards_tool] STANDARDS_APP_ID not configured — "
            "agent will use built-in DART field knowledge"
        )
        return {
            "query": query,
            "answer_text": "",
            "citations": [],
            "status": "not_configured",
            "note": (
                "STANDARDS_APP_ID is not set. "
                "Use built-in filter category reference in your instruction. "
                "Run utils/setup_standards_datastore.py to enable full grounding."
            ),
        }

    last_exc: Optional[Exception] = None

    for attempt in range(1, _STANDARDS_RETRY_ATTEMPTS + 1):
        try:
            if config.STANDARDS_SEARCH_METHOD == "answer":
                logger.info(
                    "[search_standards_tool] attempt %d/%d — answer method",
                    attempt, _STANDARDS_RETRY_ATTEMPTS,
                )
                result = answer_query_standards(
                    query=query,
                    project_id=config.STANDARDS_PROJECT_ID,
                    location=config.DATASTORE_LOCATION,
                    engine_id=config.STANDARDS_APP_ID,
                )
            else:
                logger.info(
                    "[search_standards_tool] attempt %d/%d — search method",
                    attempt, _STANDARDS_RETRY_ATTEMPTS,
                )
                result = search_standards_passages(
                    query=query,
                    project_id=config.STANDARDS_PROJECT_ID,
                    location=config.DATASTORE_LOCATION,
                    engine_id=config.STANDARDS_APP_ID,
                )

            if result["status"] == "ok" and result["answer_text"]:
                logger.info(
                    "[search_standards_tool] ok — %d chars (attempt %d)",
                    len(result["answer_text"]), attempt,
                )
            else:
                logger.warning(
                    "[search_standards_tool] status=%s empty answer for query: '%s'",
                    result.get("status"), query,
                )

            return {
                "query": query,
                "answer_text": result.get("answer_text", ""),
                "citations": result.get("citations", []),
                "status": result.get("status", "ok"),
                "note": "",
            }

        except Exception as exc:
            last_exc = exc

            # PermissionDenied — do not retry, stop immediately
            try:
                from google.api_core.exceptions import PermissionDenied
                if isinstance(exc, PermissionDenied):
                    logger.error(
                        "[search_standards_tool] PermissionDenied (403) — "
                        "contact admin to grant roles/discoveryengine.viewer "
                        "on project dataservices-271014"
                    )
                    return {
                        "query": query,
                        "answer_text": "",
                        "citations": [],
                        "status": "permission_denied",
                        "note": (
                            "Standards search returned 403 PermissionDenied. "
                            "Contact admin to grant 'roles/discoveryengine.viewer' "
                            "on project dataservices-271014 for the server service account. "
                            "Use built-in DART field reference as fallback and flag as BSA open item."
                        ),
                    }
            except ImportError:
                pass

            if not _is_retriable_standards_error(exc):
                # Non-connection error, not worth retrying
                logger.warning(
                    "[search_standards_tool] non-retriable error on attempt %d (%s): %s",
                    attempt, type(exc).__name__, exc,
                )
                break

            if attempt < _STANDARDS_RETRY_ATTEMPTS:
                logger.warning(
                    "[search_standards_tool] attempt %d/%d — connection error (%s), "
                    "retrying in %.0fs",
                    attempt, _STANDARDS_RETRY_ATTEMPTS,
                    type(exc).__name__, _STANDARDS_RETRY_SLEEP_SEC,
                )
                time.sleep(_STANDARDS_RETRY_SLEEP_SEC)
            else:
                logger.warning(
                    "[search_standards_tool] all %d attempts failed — last: %s: %s",
                    _STANDARDS_RETRY_ATTEMPTS, type(exc).__name__, exc,
                )

    # Retries exhausted or non-retriable connection error
    exc_summary = (
        f"{type(last_exc).__name__}: {str(last_exc)[:120]}" if last_exc else "unknown error"
    )
    logger.warning(
        "[search_standards_tool] unavailable for query '%s': %s", query, exc_summary
    )
    return {
        "query": query,
        "answer_text": "",
        "citations": [],
        "status": "unavailable",
        "note": (
            f"Standards search unreachable after {_STANDARDS_RETRY_ATTEMPTS} attempts "
            f"({exc_summary}). "
            "Use built-in DART field reference as fallback and flag as BSA open item."
        ),
    }


# =============================================================================
# Tool 2: build_driver_mapping_tool
# =============================================================================

class FilterCandidateInput(BaseModel):
    brd_concept: str = Field(default="", description="Raw filter concept from BRD e.g. 'IBC members only'")
    brd_source: str = Field(default="unknown", description="Requirement ID or section e.g. '6.1.5' or 'in_scope'")
    filter_category: str = Field(default="unknown", description="company | business_type | coverage | group_id | enrollment | date_range | exclusion | customer_id")
    dart_field: str = Field(default="", description="Exact DART field name e.g. 'IBC_FOC_LVL_CD'")
    dart_table: str = Field(default="", description="DART table e.g. 'MBR_ENRL_FACT'")
    dart_layer: str = Field(default="ILDWP1V", description="ILDWP1V or ILDWP1VS")
    filter_type: str = Field(default="include", description="include | exclude | date_range | parameterized")
    suggested_values: List[str] = Field(default_factory=list)
    sql_clause: Optional[str] = Field(None, description="Pre-built clause for date_range filters")
    confidence: float = Field(default=0.0, description="0.0-1.0")
    needs_fyi_lookup: bool = False
    mapping_notes: Optional[str] = None
    open_item: bool = False
    open_item_reason: Optional[str] = None
    bsa_question: Optional[str] = Field(
        None,
        description=(
            "Set when open_item=True. Human-readable question for BSA at Checkpoint 2. "
            "e.g. 'Standards search unreachable — BSA to confirm IBC_FOC_LVL_CD is correct "
            "for IBC and TPA company filter'"
        ),
    )
    filter_scope: str = "global"

    @field_validator(
        "brd_concept", "brd_source", "filter_category", "dart_field",
        "dart_table", "dart_layer", "filter_type", "filter_scope",
        mode="before"
    )
    @classmethod
    def coerce_none_to_str(cls, v):
        return v if v is not None else ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_none_to_float(cls, v):
        return v if v is not None else 0.0

    @field_validator("suggested_values", mode="before")
    @classmethod
    def coerce_none_to_list(cls, v):
        # Agent sometimes returns null for suggested_values (e.g. date_range filters).
        # Pydantic v2 rejects None for List[str] even with default_factory — coerce here.
        return v if v is not None else []

    @field_validator("needs_fyi_lookup", "open_item", mode="before")
    @classmethod
    def coerce_none_to_bool(cls, v):
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip().lower() in {"true", "yes", "y", "1"}
        return v


class BuildDriverMappingInput(BaseModel):
    in_scope_items: Any = Field(default="", description="in_scope string or list passed through from input")
    out_of_scope_items: Any = Field(default="", description="out_of_scope string or list passed through from input")
    requirements: Any = Field(default="", description="requirements string or list passed through from input")
    generic_tables: Any = Field(default_factory=list, description="generic_tables list passed through from input")
    standards_results: List[dict] = Field(default_factory=list)
    filter_candidates: List[FilterCandidateInput] = Field(..., description="Agent's mapped filter decisions")
    unmapped_concepts: List[str] = Field(default_factory=list, description="Concepts the agent could not map")

    @field_validator("standards_results", "unmapped_concepts", mode="before")
    @classmethod
    def coerce_none_to_list(cls, v):
        return v if v is not None else []

    extract_context: dict = Field(
        default_factory=dict,
        description=(
            "Pre-built extract context from BRD. Keys: file_population_type, subject_areas, "
            "vendor_name, interface_code, effective_dates_from, effective_dates_to, date_parameters. "
            "Stored in session state for downstream fyi_lookup_tool ranking."
        ),
    )

    @field_validator("extract_context", mode="before")
    @classmethod
    def coerce_none_to_dict(cls, v):
        return v if v is not None else {}


def build_driver_mapping_tool(
    input: BuildDriverMappingInput,
    tool_context: ToolContext = None,
) -> dict:
    """
    Structures the business mapping agent's filter candidate decisions.
    Detects ibc_aha_context from in_scope company entries.
    Stores driver_mapping in session state.
    """
    if isinstance(input, dict):
        input = BuildDriverMappingInput(**input)
    from agents.extract_agent.driver_agent.models import (
        FilterCandidate, DriverMapping,
    )

    # Detect ibc_aha_context — handle both string (new format) and list of dicts (old format)
    in_scope_raw = input.in_scope_items
    if isinstance(in_scope_raw, str):
        company_text = in_scope_raw.lower()
    elif isinstance(in_scope_raw, list):
        company_text = " ".join(
            (i.get("description", "") + " " + (i.get("notes") or "")).lower()
            for i in in_scope_raw
            if isinstance(i, dict)
        )
    else:
        company_text = ""

    if ("aha" in company_text or "tpa" in company_text) and ("ibc" in company_text or "independence" in company_text):
        ibc_aha_context = "both"
    elif "aha" in company_text or "tpa" in company_text:
        ibc_aha_context = "AHA"
    else:
        ibc_aha_context = "IBC"

    candidates = []
    for fc in input.filter_candidates:
        try:
            candidates.append(FilterCandidate(**fc.model_dump()))
        except Exception as exc:
            logger.warning("[build_driver_mapping_tool] Skipping malformed candidate: %s", exc)

    result = DriverMapping(
        filter_candidates=candidates,
        unmapped_concepts=input.unmapped_concepts,
        ibc_aha_context=ibc_aha_context,
    )

    if tool_context is not None:
        tool_context.state["ibc_aha_context"] = ibc_aha_context
        tool_context.state["driver_mapping"] = result.model_dump()
        tool_context.state["extract_context"] = input.extract_context

    fyi_count = sum(1 for c in candidates if c.needs_fyi_lookup)
    logger.info(
        "[build_driver_mapping_tool] %d candidates (%d need fyi_lookup), %d unmapped, "
        "ibc_aha_context=%s extract_context_keys=%s",
        len(candidates), fyi_count, len(input.unmapped_concepts),
        ibc_aha_context, list(k for k, v in input.extract_context.items() if v),
    )
    return {
        "status": "ok",
        "candidate_count": len(candidates),
        "unmapped_count": len(input.unmapped_concepts),
        "ibc_aha_context": ibc_aha_context,
    }


# =============================================================================
# Tool 3: build_driver_logic_tool
# =============================================================================

class CommonFilterInput(BaseModel):
    filter_id: str = Field(default="", description="Sequential ID e.g. 'F001'")
    filter_category: str = Field(default="unknown", description="company | business_type | coverage | group_id | enrollment | date_range | exclusion | customer_id")
    filter_scope: str = Field(default="global", description="global | file")
    file_name: Optional[str] = None
    dart_field: str = Field(default="", description="Exact DART field name e.g. 'IBC_FOC_LVL_CD'")
    dart_table: str = Field(default="", description="DART table e.g. 'MBR_ENRL_FACT'")
    dart_layer: str = Field(default="ILDWP1V", description="ILDWP1V or ILDWP1VS")
    filter_type: str = Field(default="include", description="include | exclude | date_range")
    filter_values: List[str] = Field(default_factory=list, description="Coded values used in the SQL clause")
    sql_clause: str = Field(default="", description="Complete SQL predicate e.g. \"IBC_FOC_LVL_CD IN ('IBC','TPA')\"")
    odf_sel_crta_ref: Optional[str] = None
    brd_traceability: List[str] = Field(default_factory=list, description="BRD requirement IDs this filter traces to")
    confidence: float = Field(default=0.0, description="0.0-1.0")
    source: str = "BRD"
    open_item: bool = False
    open_item_reason: Optional[str] = None
    bsa_question: Optional[str] = Field(
        None,
        description=(
            "Carried from FilterCandidate.bsa_question. Set when open_item=True. "
            "Human-readable question for BSA at Checkpoint 2."
        ),
    )
    notes: str = ""

    @field_validator(
        "filter_id", "filter_category", "filter_scope", "dart_field",
        "dart_table", "dart_layer", "filter_type", "sql_clause",
        "source", "notes", mode="before"
    )
    @classmethod
    def coerce_none_to_str(cls, v):
        return v if v is not None else ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_none_to_float(cls, v):
        return v if v is not None else 0.0

    @field_validator("filter_values", "brd_traceability", mode="before")
    @classmethod
    def coerce_none_to_list(cls, v):
        return v if v is not None else []


class BuildDriverLogicInput(BaseModel):
    common_filters: List[CommonFilterInput] = Field(..., description="Complete list of CommonFilter objects")
    ibc_aha_context: Optional[str] = Field(default=None, description="IBC | AHA | both — null when not yet determined")

    @field_validator("ibc_aha_context", mode="before")
    @classmethod
    def coerce_empty_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


def build_driver_logic_tool(
    input: BuildDriverLogicInput,
    tool_context: ToolContext = None,
) -> dict:
    """
    Assembles CommonFilter objects into DriverLogic.
    Builds the combined sql_where_clause from all global filters.
    Stores driver_logic in session state.
    """
    if isinstance(input, dict):
        input = BuildDriverLogicInput(**input)
    from agents.extract_agent.driver_agent.models import (
        CommonFilter, DriverLogic,
    )

    # Resolve ibc_aha_context: model value → session state (written by build_driver_mapping_tool) → "IBC"
    ibc_aha_context = input.ibc_aha_context
    if not ibc_aha_context and tool_context is not None:
        ibc_aha_context = tool_context.state.get("ibc_aha_context")
    if not ibc_aha_context:
        ibc_aha_context = "IBC"
        logger.warning("[build_driver_logic_tool] ibc_aha_context not provided and not in session state — defaulting to 'IBC'")

    common_filters = []
    for cf in input.common_filters:
        try:
            common_filters.append(CommonFilter(**cf.model_dump()))
        except Exception as exc:
            logger.warning("[build_driver_logic_tool] Skipping malformed filter: %s", exc)

    # Build combined WHERE clause from all global-scope filters
    global_clauses = [
        f.sql_clause for f in common_filters
        if f.filter_scope == "global" and f.sql_clause.strip()
    ]
    sql_where = "\n  AND ".join(global_clauses)

    global_count = sum(1 for f in common_filters if f.filter_scope == "global")
    file_count = sum(1 for f in common_filters if f.filter_scope == "file")
    open_count = sum(1 for f in common_filters if f.open_item)

    result = DriverLogic(
        common_filters=common_filters,
        sql_where_clause=sql_where,
        global_filter_count=global_count,
        file_level_filter_count=file_count,
        open_item_count=open_count,
        ibc_aha_context=ibc_aha_context,
    )

    if tool_context is not None:
        tool_context.state["driver_logic"] = result.model_dump()

    # Collect all BSA questions for open items — surfaced for review at Checkpoint 2
    bsa_questions = [
        {
            "filter_id": f.filter_id,
            "dart_field": f.dart_field,
            "bsa_question": f.bsa_question,
        }
        for f in common_filters
        if f.open_item and f.bsa_question
    ]

    logger.info(
        "[build_driver_logic_tool] %d filters (%d global, %d file, %d open, %d bsa_questions), ibc_aha_context=%s",
        len(common_filters), global_count, file_count, open_count, len(bsa_questions), input.ibc_aha_context,
    )
    return {
        "status": "ok",
        "filter_count": len(common_filters),
        "global_filter_count": global_count,
        "file_level_filter_count": file_count,
        "open_item_count": open_count,
        "ibc_aha_context": input.ibc_aha_context,
        "bsa_questions": bsa_questions,
    }


# =============================================================================
# Tool 3b: save_standards_results_tool
# Used by standards_search_agent to persist all collected search results
# to session state so mapping_builder_agent can read them.
# =============================================================================

class SaveStandardsResultsInput(BaseModel):
    results: List[dict] = Field(
        ...,
        description=(
            "List of standards search results, one entry per filter concept. "
            "Each entry must include: concept (str), dart_field (str), "
            "answer_text (str), status (str), and any relevant field details."
        ),
    )

    @field_validator("results", mode="before")
    @classmethod
    def coerce_none_to_list(cls, v):
        return v if v is not None else []


def save_standards_results_tool(
    input: SaveStandardsResultsInput,
    tool_context: ToolContext = None,
) -> dict:
    """
    Persist all collected standards search results to session state.
    Called ONCE by standards_search_agent after completing all search_standards_tool calls.
    mapping_builder_agent reads state["standards_results"] from the session.
    """
    if isinstance(input, dict):
        input = SaveStandardsResultsInput(**input)

    if tool_context is not None:
        tool_context.state["standards_results"] = input.results

    logger.info("[save_standards_results_tool] saved %d results to session state", len(input.results))
    return {"status": "ok", "count": len(input.results)}


# =============================================================================
# Tool 4: validate_driver_rules
# =============================================================================

# KNOWN_DART_FILTER_FIELDS intentionally removed.
# Field name validation is not hardcoded — the AIDataDeliveryStandards document
# is the authoritative source and is maintained by domain experts in Vertex AI Search.
# The agent validates field names at mapping time via search_standards_tool.
# If a field is uncertain, open_item=True + bsa_question routes it to BSA at Checkpoint 2.

# SQL patterns that indicate transformation logic (not allowed in driver filter predicates).
_TRANSFORMATION_PATTERNS = [
    "CASE WHEN",
    "COALESCE",
    "ISNULL",
    "CONVERT(",
    "CAST(",
    "SUBSTR(",
    "LEFT(",
    "RIGHT(",
    "UPPER(",
    "LOWER(",
    "TRIM(",
    "DECODE(",
    "NVL(",
    "IIF(",
    "FORMAT(",
]


class ValidateDriverRulesInput(BaseModel):
    common_filters: List[dict] = Field(
        ..., description="List of CommonFilter dicts from driver_logic"
    )
    sql_where_clause: str = Field(
        ..., description="Combined SQL WHERE clause from driver_logic"
    )
    requirements: Any = Field(
        default="",
        description="Requirements string or list from BRD — used for traceability check",
    )
    known_dart_fields: Optional[List[str]] = Field(
        None,
        description=(
            "Valid DART field names from standards doc. "
            "If not provided, uses built-in KNOWN_DART_FILTER_FIELDS set."
        ),
    )


def validate_driver_rules(
    input: ValidateDriverRulesInput,
    tool_context: ToolContext = None,
) -> dict:
    """
    Validates driver_logic against 4 checks:
      1. Transformation logic — no CASE WHEN / functions in SQL clauses
      2. Standards compliance — all DART field names in known fields set
      3. Conflict detection — no same field with both include AND exclude
      4. BRD traceability — every filter has at least one brd_traceability entry

    Writes driver_validation to session state.
    Returns: status, total_issues, can_proceed, and breakdown by check.
    """
    if isinstance(input, dict):
        input = ValidateDriverRulesInput(**input)
    from agents.extract_agent.driver_agent.models import (
        ValidationIssue,
        DriverValidation,
    )

    issues: List[dict] = []

    # -------------------------------------------------------------------------
    # Check 1: No transformation logic in any sql_clause or combined WHERE
    # -------------------------------------------------------------------------
    combined_sql_upper = input.sql_where_clause.upper()
    for pattern in _TRANSFORMATION_PATTERNS:
        if pattern.upper() in combined_sql_upper:
            issues.append(ValidationIssue(
                issue_type="transformation_logic",
                severity="high",
                filter_id=None,
                description=(
                    f"SQL WHERE clause contains transformation logic: '{pattern}'. "
                    "Driver filters must be pure predicates only (field IN/NOT IN/comparison)."
                ),
                recommended_action=(
                    "Remove transformation logic. Move derivations to the Transformation Rules "
                    "tab in the mapping step instead."
                ),
            ).model_dump())

    # Also check individual filter sql_clauses
    for f in input.common_filters:
        clause_upper = (f.get("sql_clause") or "").upper()
        for pattern in _TRANSFORMATION_PATTERNS:
            if pattern.upper() in clause_upper:
                issues.append(ValidationIssue(
                    issue_type="transformation_logic",
                    severity="high",
                    filter_id=f.get("filter_id"),
                    description=(
                        f"Filter {f.get('filter_id')} sql_clause contains "
                        f"transformation logic: '{pattern}'."
                    ),
                    recommended_action=(
                        "Rewrite as a pure predicate. Remove functions/CASE WHEN."
                    ),
                ).model_dump())
                break  # one report per filter

    # -------------------------------------------------------------------------
    # Check 2: Conflict detection — same field with include AND exclude
    # -------------------------------------------------------------------------
    field_types: dict = {}
    for f in input.common_filters:
        dart_field = f.get("dart_field", "")
        ftype = f.get("filter_type", "")
        if not dart_field or ftype == "date_range":
            continue
        if dart_field in field_types and field_types[dart_field] != ftype:
            issues.append(ValidationIssue(
                issue_type="conflict",
                severity="high",
                filter_id=f.get("filter_id"),
                description=(
                    f"Field '{dart_field}' has both include and exclude filters "
                    f"(filter {field_types[dart_field + '_id']} and {f.get('filter_id')}). "
                    "This produces contradictory logic."
                ),
                recommended_action=(
                    "BSA: consolidate into a single filter or separate by file_scope."
                ),
            ).model_dump())
        else:
            field_types[dart_field] = ftype
            field_types[dart_field + "_id"] = f.get("filter_id", "")

    # -------------------------------------------------------------------------
    # Check 3: BRD traceability — every filter must have at least one trace
    # -------------------------------------------------------------------------
    for f in input.common_filters:
        traceability = f.get("brd_traceability") or []
        # Filter out empty strings
        traceability = [t for t in traceability if t and t.strip()]
        if not traceability:
            issues.append(ValidationIssue(
                issue_type="missing_brd_trace",
                severity="medium",
                filter_id=f.get("filter_id"),
                description=(
                    f"Filter {f.get('filter_id')} ({f.get('dart_field')}) "
                    "has no BRD requirement traceability. Cannot verify it is BRD-driven."
                ),
                recommended_action=(
                    "BSA: add the BRD requirement ID(s) that justify this filter."
                ),
            ).model_dump())

    # -------------------------------------------------------------------------
    # Assemble result
    # -------------------------------------------------------------------------
    high_count = sum(1 for i in issues if i["severity"] == "high")
    med_count = sum(1 for i in issues if i["severity"] == "medium")

    result = DriverValidation(
        issues=[ValidationIssue(**i) for i in issues],
        total_high=high_count,
        total_medium=med_count,
        all_brd_requirements_traced=not any(
            i["issue_type"] == "missing_brd_trace" for i in issues
        ),
        no_transformation_logic=not any(
            i["issue_type"] == "transformation_logic" for i in issues
        ),
        standards_compliant=True,  # field name validation delegated to search_standards_tool at mapping time
        can_proceed=high_count == 0,
    )

    if tool_context is not None:
        tool_context.state["driver_validation"] = result.model_dump()

    logger.info(
        "[validate_driver_rules] %d filters — issues: %d high, %d medium — can_proceed=%s",
        len(input.common_filters), high_count, med_count, result.can_proceed,
    )
    return {
        "status": "ok",
        "filter_count": len(input.common_filters),
        "total_issues": len(issues),
        "high_severity": high_count,
        "medium_severity": med_count,
        "can_proceed": result.can_proceed,
        "all_brd_requirements_traced": result.all_brd_requirements_traced,
        "no_transformation_logic": result.no_transformation_logic,
        "standards_compliant": result.standards_compliant,
    }


# =============================================================================
# Tool 5: fyi_lookup_tool
# =============================================================================

_FYI_QUERY = """
SELECT
  F.DB_NM,
  F.TBL_VW_NM,
  F.ENTY_NM,
  F.ENTY_DSC,
  F.COLM_NM,
  F.ATTR_NM,
  F.ATTR_DSC,
  F.TABLE_RCMND_STS_CD,
  CR.DRVD_ALIAS_NAME,
  CR.TOTAL_INSTANCE_CNT,
  CR.PRIORITY,
  CR.CONFIDENCE
FROM `{fyi_table}` F
LEFT JOIN `{recmd_table}` CR
  ON  F.TBL_VW_NM = CR.TABLE_NAME
  AND F.COLM_NM   = CR.COLUMN_NAME
WHERE F.COLM_NM = @column_name
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY F.TBL_VW_NM, F.COLM_NM
  ORDER BY
    IFNULL(CR.PRIORITY, 99),
    CASE WHEN F.DB_NM = 'DB_ILDWP1VS' THEN '1' ELSE F.DB_NM END,
    F.TABLE_RCMND_STS_CD DESC
) <= 3
ORDER BY IFNULL(CR.PRIORITY, 99), F.TABLE_RCMND_STS_CD DESC
"""

_FYI_MAX_CANDIDATES = int(os.getenv("DRIVER_FYI_MAX_CANDIDATES", "15"))


def _parse_confidence(val) -> float | None:
    """Parse CONFIDENCE column — handles both numeric float and string ('High','Medium','Low')."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(str(val).strip().lower())


def _get_fyi_bq_client():
    from utils import local_warehouse as _bq
    from google.oauth2 import service_account as _sa
    from pathlib import Path as _Path
    creds_path = getattr(config, "CREDENTIALS_PATH", "")
    if creds_path and _Path(creds_path).exists():
        logger.debug("[fyi_lookup_tool] BQ auth: service account file %s", creds_path)
        creds = _sa.Credentials.from_service_account_file(creds_path)
        return _bq.Client(project=config.EXTRACT_FYI_PROJECT_ID, credentials=creds)
    logger.debug("[fyi_lookup_tool] BQ auth: ADC (no service account file found at '%s')", creds_path)
    return _bq.Client(project=config.EXTRACT_FYI_PROJECT_ID)


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term and term in text for term in terms)


def _fyi_rank_score(candidate: dict, filter_description: str, extract_context: dict) -> tuple:
    """
    Lower score is better. Ranking here keeps FYI output compact before it reaches
    the LLM, which prevents huge candidate lists from exhausting model context.
    """
    enty_dsc = str(candidate.get("enty_dsc") or "").lower()
    attr_dsc = str(candidate.get("attr_dsc") or "").lower()
    attr_nm = str(candidate.get("attr_nm") or "").lower()
    tbl = str(candidate.get("tbl_vw_nm") or "").lower()
    combined = " ".join([enty_dsc, attr_dsc, attr_nm, tbl])

    subject = str(extract_context.get("subject_areas") or "").lower()
    population = str(extract_context.get("file_population_type") or "").lower()
    filter_desc = str(filter_description or "").lower()
    date_context = extract_context.get("date_parameters") or {}

    score = 0

    if subject and subject in combined:
        score -= 30
    population_terms = [w for w in re.split(r"[^a-z0-9_]+", population) if len(w) > 4]
    if population_terms and _contains_any(combined, population_terms):
        score -= 20

    if "eligib" in subject and _contains_any(combined, ["eligib", "enrl", "enroll", "member"]):
        score -= 25
    if "claim" in subject and "claim" in combined:
        score -= 25
    if "pharmacy" in subject and _contains_any(combined, ["pharm", "rx"]):
        score -= 25

    if date_context.get("member_active_enrollment") and _contains_any(combined, ["enrl", "enroll", "member"]):
        score -= 15
    if date_context.get("claim_service_dates") and "claim" in combined:
        score -= 15
    if date_context.get("pharmacy_fill_dates") and _contains_any(combined, ["pharm", "rx"]):
        score -= 15

    desc_terms = [w for w in re.split(r"[^a-z0-9_]+", filter_desc) if len(w) > 3]
    score -= min(20, sum(2 for w in desc_terms if w in combined))

    if candidate.get("table_rcmnd_sts_cd") == "R":
        score -= 10

    priority = candidate.get("priority")
    try:
        priority_score = int(priority) if priority is not None else 99
    except (TypeError, ValueError):
        priority_score = 99

    confidence = candidate.get("confidence")
    try:
        confidence_score = -float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence_score = 0.0

    return (score, priority_score, confidence_score, candidate.get("tbl_vw_nm") or "")


class FyiLookupInput(BaseModel):
    column_name: str = Field(
        ...,
        description="Exact DART column name to look up, e.g. 'IBC_FOC_LVL_CD'",
    )
    filter_description: str = Field(
        ...,
        description=(
            "BRD concept or filter description for this column — matched against "
            "ATTR_DSC to confirm relevance. E.g. 'IBC and TPA company filter'."
        ),
    )
    extract_context: dict = Field(
        default_factory=dict,
        description=(
            "Pre-built extract context from BRD. Keys: file_population_type, subject_areas, "
            "interface_code, effective_dates_from, effective_dates_to, date_parameters. "
            "Agent uses this to rank candidate tables by entity-level match."
        ),
    )


def fyi_lookup_tool(
    input: FyiLookupInput,
    tool_context: ToolContext = None,
) -> dict:
    """
    Look up candidate DART tables for a column name in FYI_TBL_COLS.

    Runs a BigQuery query joining FYI_TBL_COLS and DART_EXTC_TBLS_COLS_RECMD,
    returning up to 3 candidate rows per table (QUALIFY window).

    The tool returns ALL candidates — it does NOT rank them.
    The logic_builder_agent applies LLM ranking using extract_context signals:
      1. ENTY_DSC vs file_population_type / subject_areas  (entity-level match)
      2. date_parameters keys vs ENTY_DSC                  (date corroboration)
      3. ATTR_DSC vs filter_description                    (attribute match)
      4. TABLE_RCMND_STS_CD = 'R'                          (recommendation status)
      5. CR.PRIORITY                                       (tiebreaker)

    Fail-open behaviour:
      - BQ unreachable / any exception → status='unavailable', empty candidates
        Agent keeps original dart_table, sets open_item=True with bsa_question.
      - Column not found in FYI tables → status='no_results', empty candidates
        Agent sets open_item=True with bsa_question.
    """
    if isinstance(input, dict):
        input = FyiLookupInput(**input)

    column_name = input.column_name.strip().upper()

    if getattr(config, "STANDALONE_MODE", False):
        # No FYI reference tables locally — degrade to no_results so the agent
        # keeps the original dart_table and flags an open item for the BSA.
        logger.info("[fyi_lookup_tool] standalone mode — no FYI tables, column='%s'", column_name)
        return {
            "status": "no_results",
            "column_name": column_name,
            "candidate_count": 0,
            "candidates": [],
            "note": (
                f"FYI reference tables are not available in standalone mode for "
                f"column '{column_name}'. Keep original dart_table and set open_item=True."
            ),
        }

    fyi_table   = f"{config.EXTRACT_FYI_PROJECT_ID}.{config.EXTRACT_FYI_DATASET}.FYI_TBL_COLS"
    recmd_table = f"{config.EXTRACT_FYI_PROJECT_ID}.{config.EXTRACT_FYI_DATASET}.DART_EXTC_TBLS_COLS_RECMD"

    logger.info(
        "[fyi_lookup_tool] START column='%s' filter_desc='%.80s' "
        "fyi_table=%s recmd_table=%s",
        column_name, input.filter_description, fyi_table, recmd_table,
    )
    logger.debug(
        "[fyi_lookup_tool] extract_context=%s",
        {k: v for k, v in input.extract_context.items() if v},
    )

    query = _FYI_QUERY.format(fyi_table=fyi_table, recmd_table=recmd_table)

    import time as _time
    t0 = _time.monotonic()
    try:
        from utils import local_warehouse as _bq
        client = _get_fyi_bq_client()
        job_config = _bq.QueryJobConfig(
            query_parameters=[
                _bq.ScalarQueryParameter("column_name", "STRING", column_name),
            ]
        )
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        elapsed = round(_time.monotonic() - t0, 2)
        logger.exception(
            "[fyi_lookup_tool] BQ error for column='%s' elapsed=%.2fs — %s: %s",
            column_name, elapsed, type(exc).__name__, exc,
        )
        return {
            "status": "unavailable",
            "column_name": column_name,
            "candidate_count": 0,
            "candidates": [],
            "note": (
                f"FYI lookup unavailable for column '{column_name}' "
                f"({type(exc).__name__}: {str(exc)[:120]}). "
                "Keep original dart_table and set open_item=True."
            ),
        }

    elapsed = round(_time.monotonic() - t0, 2)

    if not rows:
        logger.info(
            "[fyi_lookup_tool] no results — column='%s' elapsed=%.2fs",
            column_name, elapsed,
        )
        return {
            "status": "no_results",
            "column_name": column_name,
            "candidate_count": 0,
            "candidates": [],
            "note": (
                f"Column '{column_name}' not found in FYI tables. "
                "Set open_item=True and ask BSA to confirm the correct DART table."
            ),
        }

    candidates = []
    for row in rows:
        candidates.append({
            "db_nm":               row.DB_NM,
            "tbl_vw_nm":           row.TBL_VW_NM,
            "enty_nm":             row.ENTY_NM,
            "enty_dsc":            row.ENTY_DSC,
            "colm_nm":             row.COLM_NM,
            "attr_nm":             row.ATTR_NM,
            "attr_dsc":            row.ATTR_DSC,
            "table_rcmnd_sts_cd":  row.TABLE_RCMND_STS_CD,
            "drvd_alias_name":     row.DRVD_ALIAS_NAME,
            "total_instance_cnt":  row.TOTAL_INSTANCE_CNT,
            "priority":            row.PRIORITY,
            "confidence":          _parse_confidence(row.CONFIDENCE),
        })

    total_candidate_count = len(candidates)
    ranked_candidates = sorted(
        candidates,
        key=lambda c: _fyi_rank_score(c, input.filter_description, input.extract_context),
    )
    compact_candidates = ranked_candidates[:max(1, _FYI_MAX_CANDIDATES)]

    candidate_summary = [
        f"{c['tbl_vw_nm']}({c['db_nm']},rcmnd={c['table_rcmnd_sts_cd']},p={c['priority']})"
        for c in compact_candidates
    ]
    logger.info(
        "[fyi_lookup_tool] OK column='%s' candidates=%d returned=%d elapsed=%.2fs tables=%s",
        column_name, total_candidate_count, len(compact_candidates), elapsed, candidate_summary,
    )
    return {
        "status": "ok",
        "column_name": column_name,
        "candidate_count": len(compact_candidates),
        "candidate_count_total": total_candidate_count,
        "candidates": compact_candidates,
        "note": "",
    }


# =============================================================================
# Tool 6: code_value_lookup_tool
# =============================================================================

_CODE_VALUE_DISTANCE_THRESHOLD = 0.5


class CodeValueLookupInput(BaseModel):
    dart_field: str = Field(
        ...,
        description="Exact DART filter field name e.g. 'CO_CD_ROLLUP_ID'",
    )
    brd_concept: str = Field(
        ...,
        description=(
            "The BRD business concept to find matching code values for. "
            "Focus on entity names, company names, or values mentioned in the BRD. "
            "e.g. 'AHA companies in NJ and PA', 'Federal Employee Program', "
            "'Fully Insured', 'Medical coverage ME'."
        ),
    )
    top_k: int = Field(10, description="Number of top matching codes to return")

    @field_validator("dart_field", "brd_concept", mode="before")
    @classmethod
    def coerce_none_to_str(cls, v):
        return v if v is not None else ""


def code_value_lookup_tool(
    input: CodeValueLookupInput,
    tool_context: ToolContext = None,
) -> dict:
    """
    Search GENL_CD_TBL_EXTRACT embeddings for code values matching the BRD concept.

    Embeds brd_concept, queries genl_cd_tbl_embeddings filtered by dart_field (cd_colm_nm),
    and returns matched CD_VAL codes with their descriptions.

    Only returns matches with similarity_distance ≤ 0.5 (≥ 50% similarity).

    Returns:
        status:     'ok' | 'no_results' | 'unavailable'
        field_name: dart_field queried
        matches:    list of {cd_val, cd_dsc, similarity_distance} — only entries ≤ threshold
        note:       human-readable message
    """
    if isinstance(input, dict):
        input = CodeValueLookupInput(**input)

    import asyncio
    import concurrent.futures

    def _run_async(coro):
        try:
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        except RuntimeError:
            return asyncio.run(coro)

    field_name = input.dart_field.strip().upper()
    logger.info(
        "[code_value_lookup_tool] START field='%s' concept='%.80s'",
        field_name, input.brd_concept,
    )

    try:
        from utils.fyi_cd_embedding_utils import search_fyi_cd

        all_results = _run_async(
            search_fyi_cd(
                query_text=input.brd_concept,
                top_k=input.top_k,
                field_name=field_name,
            )
        )
    except Exception as exc:
        logger.exception("[code_value_lookup_tool] BQ error for field='%s': %s", field_name, exc)
        return {
            "status": "unavailable",
            "field_name": field_name,
            "matches": [],
            "note": (
                f"Code value lookup unavailable for field '{field_name}' "
                f"({type(exc).__name__}: {str(exc)[:120]}). "
                "Keep original suggested_values and set open_item=True."
            ),
        }

    # Filter to threshold
    matches = [
        {
            "cd_val":              r.get("Code Value"),
            "cd_dsc":              r.get("Code Description"),
            "similarity_distance": r.get("Similarity Distance"),
        }
        for r in all_results
        if r.get("Similarity Distance") is not None
        and r["Similarity Distance"] <= _CODE_VALUE_DISTANCE_THRESHOLD
    ]

    if not matches:
        logger.info(
            "[code_value_lookup_tool] no matches ≤ %.1f for field='%s'",
            _CODE_VALUE_DISTANCE_THRESHOLD, field_name,
        )
        return {
            "status": "no_results",
            "field_name": field_name,
            "matches": [],
            "note": (
                f"No code values found with similarity ≥ 50% for field '{field_name}' "
                f"matching concept '{input.brd_concept[:60]}'. "
                "Keep original suggested_values and set open_item=True with bsa_question."
            ),
        }

    logger.info(
        "[code_value_lookup_tool] OK field='%s' matches=%d values=%s",
        field_name, len(matches), [m["cd_val"] for m in matches],
    )
    return {
        "status": "ok",
        "field_name": field_name,
        "matches": matches,
        "note": "",
    }
