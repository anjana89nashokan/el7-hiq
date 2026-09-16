"""
Mapping Layer — mapping_row_agent.

Single LlmAgent that processes ONE target field per run:
  L1: search_indemap_mappings   — IndeMap historical BQ vector search
  L2: search_standards_for_mapping — AI Data Delivery Standards (AnswerQuery API)
  L3: search_fyi_table_columns  — FYI_TBL_COLS BQ vector search (table-level)
  Output: build_mapping_row_tool — appends to session state["mapping_rows"]

"""

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from config.settings import config
from .prompts import MAPPING_ROW_INSTRUCTION, MAPPING_FIELD_CHECKPOINT_INSTRUCTION
from .tools.indemap_search_tools import search_indemap_mappings
from .tools.standards_search_tools import search_standards_for_mapping
from .tools.mapping_tools import build_mapping_row_tool

# L3 — imported when fyi_search_tools.py is available (colleague's work)
try:
    from .tools.fyi_search_tools import search_fyi_table_columns
    _l3_tools = [FunctionTool(search_fyi_table_columns)]
except ImportError:
    _l3_tools = []

mapping_row_agent = LlmAgent(
    name="mapping_row_agent",
    model="gemini-2.5-flash",  # flash is faster for structured waterfall — no deep reasoning needed
    instruction=MAPPING_ROW_INSTRUCTION,
    tools=[
        FunctionTool(search_indemap_mappings),       # L1
        FunctionTool(search_standards_for_mapping),  # L2
        *_l3_tools,                                  # L3 (when available)
        FunctionTool(build_mapping_row_tool),         # Output
    ],
    description=(
        "Processes one target field through the L1→L2→L3 mapping waterfall and "
        "records the result via build_mapping_row_tool. "
        "L1: IndeMap BQ vector search. "
        "L2: AI Data Delivery Standards AnswerQuery. "
        "L3: FYI_TBL_COLS BQ vector search (table-level)."
    ),
)


mapping_field_checkpoint_agent = LlmAgent(
    name="mapping_field_checkpoint_agent",
    model="gemini-2.5-flash",
    instruction=MAPPING_FIELD_CHECKPOINT_INSTRUCTION,
    tools=[
        FunctionTool(search_indemap_mappings),
        FunctionTool(search_standards_for_mapping),
        *_l3_tools,
        FunctionTool(build_mapping_row_tool),
    ],
    description=(
        "Re-maps one target field using BSA checkpoint feedback and records the "
        "corrected result via build_mapping_row_tool. Uses the same L1/L2/L3 "
        "tools as mapping_row_agent when the BSA instruction requires search."
    ),
)
