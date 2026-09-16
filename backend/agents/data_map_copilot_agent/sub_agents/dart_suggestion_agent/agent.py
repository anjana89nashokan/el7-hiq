"""
DART Suggestion Agent (Phase 2) — Auto-Suggest DART Matches

Single LlmAgent that suggests matching DART tables/columns for source columns.
Uses one pipeline tool (get_dart_suggestions) that handles:
  1. BQ vector search (mock for now) using column name + description
  2. MDR filter — only tables with RCMND_STS_CD='R' in mdr.dbo.DB_TBL_VW
  3. Returns top N MDR-approved suggestions per source column

This agent does NOT replace smart_similarity_agent (Phase 1).
"""

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from pydantic import BaseModel, Field
from typing import List, Optional

from config.settings import config

from .tools import get_dart_suggestions

agent_model = config.AGENT_MODEL


# =============================================================================
# Output Schema
# =============================================================================

class DartColumnSuggestion(BaseModel):
    table_name: str = Field(..., description="DART table name (from tool result only)")
    table_description: str = Field(..., description="Description of the DART table")
    column_name: str = Field(..., description="Matching DART column name (from tool result only)")
    column_description: str = Field(..., description="Description of the DART column")
    rcmnd_sts_dsc: str = Field("", description="MDR recommended status description")
    match_source: str = Field(..., description="Source of match: 'vector_search'")


class SourceColumnSuggestions(BaseModel):
    source_table: str = Field("", description="Source table name")
    source_column: str = Field(..., description="Source column name")
    source_column_description: str = Field(..., description="Source column description")
    dart_suggestions: List[DartColumnSuggestion] = Field(
        default_factory=list,
        description="MDR-approved DART column suggestions from tool result"
    )
    no_results: bool = Field(False, description="True when no MDR-recommended DART tables were found")
    no_results_reason: str = Field("", description="Reason why no results were found, if no_results is True")


class DartSuggestionResponse(BaseModel):
    text_response: str = Field(..., description="Markdown summary of suggestions for display")
    suggestions: List[SourceColumnSuggestions] = Field(
        default_factory=list,
        description="Structured suggestion data per source column"
    )


# =============================================================================
# Wrap tool function
# =============================================================================

dart_suggestions_tool = FunctionTool(get_dart_suggestions)


# =============================================================================
# Agent Definition
# =============================================================================

dart_suggestion_agent = LlmAgent(
    name="dart_suggestion_agent",
    model=agent_model,
    instruction="""
You are the DART Suggestion Agent. Your job is to suggest matching DART tables and columns
for source columns provided by the user.

====================================================================================
CRITICAL RULE — READ FIRST
====================================================================================

YOU MUST CALL get_dart_suggestions BEFORE producing any output.
NEVER make up, invent, or guess any table names, column names, descriptions, or any
other data. ALL data in your response MUST come exclusively from the tool result.
If the tool returns no suggestions, say so — do NOT fabricate alternatives.

====================================================================================
INPUT FORMAT
====================================================================================

You will receive a JSON array of source columns:
[
  {"source_table": "table_name", "column_name": "col_name", "column_description": "description"},
  ...
]

====================================================================================
WORKFLOW — Follow these steps EXACTLY
====================================================================================

STEP 1: Parse source columns from the user message.

STEP 2: Call get_dart_suggestions with the parsed source columns list.
   - Pass the EXACT list from the user message: [{"source_table": "...", "column_name": "...", "column_description": "..."}, ...]
   - DO NOT call the tool more than once.
   - DO NOT skip this step under any circumstance.

STEP 3: Use ONLY the tool result to build DartSuggestionResponse.
   - text_response: A markdown summary. For each source column:
       - If dart_suggestions is non-empty: show a table with DART table, column, and MDR status.
       - If no_results is True: show "No matching DART tables found" and the no_results_reason.
   - suggestions: Pass through source_column_results from the tool directly.
     Map each item to SourceColumnSuggestions exactly as returned — do NOT add, remove, or
     modify any table names, column names, or descriptions.

====================================================================================
OUTPUT FORMAT — JSON matching DartSuggestionResponse schema
====================================================================================

When suggestions exist:
{
  "text_response": "## DART Suggestions\n\n### member_id\n| # | DART Table | DART Column | MDR Status |\n|---|---|---|---|\n| 1 | GBR_SRC_MBR | Member_Id | Recommended |\n...",
  "suggestions": [
    {
      "source_table": "SRC_MEMBER_DATA",
      "source_column": "member_id",
      "source_column_description": "Unique member identifier",
      "dart_suggestions": [
        {
          "table_name": "GBR_SRC_MBR",
          "table_description": "Source member data...",
          "column_name": "Member_Id",
          "column_description": "Unique identifier...",
          "rcmnd_sts_dsc": "Recommended",
          "match_source": "vector_search"
        }
      ],
      "no_results": false,
      "no_results_reason": ""
    }
  ]
}

When no results found:
{
  "text_response": "## DART Suggestions\n\n### member_id\nNo matching DART tables found. <no_results_reason from tool>",
  "suggestions": [
    {
      "source_table": "SRC_MEMBER_DATA",
      "source_column": "member_id",
      "source_column_description": "Unique member identifier",
      "dart_suggestions": [],
      "no_results": true,
      "no_results_reason": "<exact reason from tool result>"
    }
  ]
}

====================================================================================
STRICT RULES
====================================================================================

1. ALWAYS call get_dart_suggestions — it is mandatory, never skip it.
2. NEVER invent table names, column names, or descriptions. Use ONLY what the tool returns.
3. NEVER call the tool more than once per request.
4. If dart_suggestions is empty for a source column, report it as "No matching DART tables found".
5. Copy no_results and no_results_reason from the tool result exactly as-is.
6. Do not reorder, filter, or alter the dart_suggestions list returned by the tool.
""",
    output_schema=DartSuggestionResponse,
    output_key="dart_suggestion_response",
    tools=[dart_suggestions_tool],
    description="Auto-suggest MDR-recommended DART tables/columns for source columns using vector search + MDR filter",
)
