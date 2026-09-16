"""
Extract Agent — Driver Layer Agents

Business mapping is now split into two focused agents to enforce correct ordering:
  standards_search_agent  : searches standards doc per concept, saves results (no build tool)
  mapping_builder_agent   : reads results, builds FilterCandidates, calls build tool (no search tool)

logic_builder_agent     : FYI lookup + code value lookup + builds SQL predicates
driver_validator_agent  : validates driver logic
"""

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from config.settings import config
from .tools import (
    search_standards_tool,
    save_standards_results_tool,
    build_driver_mapping_tool,
    build_driver_logic_tool,
    validate_driver_rules,
    fyi_lookup_tool,
    code_value_lookup_tool,
)
from .prompts import (
    STANDARDS_SEARCH_INSTRUCTION,
    MAPPING_BUILDER_INSTRUCTION,
    LOGIC_BUILDER_INSTRUCTION,
    DRIVER_VALIDATOR_INSTRUCTION,
)

agent_model = config.AGENT_MODEL

# =============================================================================
# Agent 1a — Standards Search (searches standards doc, saves results)
# Tools: search_standards_tool + save_standards_results_tool ONLY
# Cannot call build_driver_mapping_tool — structural enforcement
# =============================================================================

standards_search_agent = LlmAgent(
    name="standards_search_agent",
    model=agent_model,
    instruction=STANDARDS_SEARCH_INSTRUCTION,
    tools=[
        FunctionTool(search_standards_tool),
        FunctionTool(save_standards_results_tool),
    ],
    description=(
        "Searches AIDataDeliveryStandards for each BRD filter concept and saves all "
        "results to session state. Has no access to build_driver_mapping_tool — "
        "cannot build filter candidates prematurely."
    ),
)

# =============================================================================
# Agent 1b — Mapping Builder (reads results, builds candidates, calls build tool)
# Tools: build_driver_mapping_tool ONLY
# Cannot call search_standards_tool — structural enforcement
# =============================================================================

mapping_builder_agent = LlmAgent(
    name="mapping_builder_agent",
    model=agent_model,
    instruction=MAPPING_BUILDER_INSTRUCTION,
    tools=[
        FunctionTool(build_driver_mapping_tool),
    ],
    description=(
        "Reads standards search results from session state, builds FilterCandidates, "
        "and calls build_driver_mapping_tool once. Has no access to search_standards_tool."
    ),
)

# =============================================================================
# Agent 2 — Logic Builder
# =============================================================================

logic_builder_agent = LlmAgent(
    name="logic_builder_agent",
    model=agent_model,
    instruction=LOGIC_BUILDER_INSTRUCTION,
    tools=[
        FunctionTool(fyi_lookup_tool),           # STEP 0   — confirms DART table
        FunctionTool(code_value_lookup_tool),    # STEP 0.5 — confirms code values from BQ
        FunctionTool(build_driver_logic_tool),   # STEP 1-3 — builds SQL predicates
    ],
    description=(
        "Resolves DART tables via FYI lookup, resolves code values via GENL_CD_TBL "
        "semantic search, then builds SQL filter predicates (CommonFilter objects)."
    ),
)

# =============================================================================
# Agent 3 — Driver Validator
# =============================================================================

driver_validator_agent = LlmAgent(
    name="driver_validator_agent",
    model=agent_model,
    instruction=DRIVER_VALIDATOR_INSTRUCTION,
    tools=[
        FunctionTool(validate_driver_rules),
    ],
    description="Validates driver logic: transformation logic, conflict detection, BRD traceability",
)
