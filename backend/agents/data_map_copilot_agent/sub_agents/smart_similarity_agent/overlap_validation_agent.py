"""
Overlap Validation Agent - Phase 2: Calculates data overlap percentages for identified matches
"""

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from config.settings import config
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Literal

# Import the overlap calculation tool
# Ensure your utils/smart_similarity_functions.py has the flattened compute_overlap_tool signature
from utils.smart_similarity_functions import compute_overlap_tool

# Agent configuration
agent_model = config.AGENT_MODEL

# Wrap the tool
overlap_tool = FunctionTool(compute_overlap_tool)

# --- Output Schema Definitions ---

class OverlapMatch(BaseModel):
    rank: int = Field(..., description="Rank of the match based on combined_score (highest first)")
    dart_table_name: str = Field(..., description="Full DART table name with dataset prefix")
    dart_field_name: str = Field(..., description="DART column name")
    filename: str = Field(..., description="Source filename or table name")
    source_column_name: str = Field(..., description="Source column name")
    dart_sample_values: str = Field(..., description="Comma-separated DART sample values")
    header_name_similarity: float = Field(..., ge=0, le=100, description="Phase 1 semantic similarity score (0–100)")
    data_overlap_similarity: float = Field(..., ge=0, le=100, description="Data overlap percent from compute_overlap_tool (0–100)")
    combined_score: float = Field(..., ge=0, le=100, description="Weighted score (0.4×header + 0.6×overlap)")
    match_reasoning: str = Field(..., description="Reason for considering this a valid or potential match")
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(..., description="Confidence level derived from combined_score")
    null_blank_percent: float = Field(..., ge=0, le=100, description="Percentage of null or blank values")
    total_rows: int = Field(..., ge=0, description="Total number of rows evaluated")
    overlap_count: int = Field(..., ge=0, description="Number of overlapping distinct values between source and DART")
    source_distinct_count: int = Field(..., ge=0, description="Number of distinct values in the source column")

class OverlapSummary(BaseModel):
    total_potential_matches: int = Field(..., description="Total number of matches analyzed")
    high_confidence_count: int = Field(..., description="Number of high confidence matches (>=75%)")
    medium_confidence_count: int = Field(..., description="Number of medium confidence matches (50–74%)")
    low_confidence_count: int = Field(..., description="Number of low confidence matches (<50%)")
    average_header_name_similarity: float = Field(..., description="Average Phase 1 header name similarity score")
    average_data_overlap_similarity: float = Field(..., description="Average actual data overlap score")
    average_combined_score: float = Field(..., description="Average combined score across all matches")
    best_match_description: str = Field(..., description="Text summary of the best match found")

class OverlapToolResponse(BaseModel):
    status: Literal["success", "error"] = Field(..., description="Status of the overlap validation process")
    potential_matches: List[OverlapMatch] = Field(..., description="List of ranked match results with detailed statistics")
    summary: OverlapSummary = Field(..., description="Summary statistics for the validation process")

class OverlapValidationResponse(BaseModel):
    text_response: str = Field(..., description="Markdown formatted report with all tables and statistics")
    tool_response: OverlapToolResponse = Field(..., description="Structured JSON data of matches and summary")


# --- Agent Definition ---

overlap_validation_agent = LlmAgent(
    name="overlap_validation_agent",
    model=agent_model,
    instruction=f"""
YOUR RESPONSE MUST BE IN STRUCTURED JSON FORMAT:
- text_response: string, The markdown formatted report with tables showing all matches
- tool_response: dict, The structured data containing potential_matches array and summary statistics

You are the Overlap Validation Agent - Phase 2 of column similarity analysis.

YOUR RESPONSIBILITY:
Take potential matches from Phase 1 and validate them with actual data overlap calculations.

{{semantic_matching_response}}

**YOUR PRIMARY TOOL:**

Tool: `compute_overlap_tool(dart_table, dart_column, source_table, source_column)`

- CRITICAL: You MUST call this tool for EACH potential match from Phase 1.
- Input: Call the function with direct named arguments (do not wrap in 'tool_input').
- Returns: Data overlap statistics including data_overlap_percent.

**How to call the tool:**
For each match from Phase 1, call the tool using direct arguments like this:

```python
compute_overlap_tool(
    dart_table="ihg-dart-edw-dev2.DB_WRK.gender_lookup",
    dart_column="gender_code",
    source_table="ihg-ibc-poc-ai.DATAMAP_COPILOT.account_data",
    source_column="sex"
)
====================================================================================
CRITICAL WORKFLOW - STEP 2 OF 2
Context:
You are the second step in a sequential workflow. The previous agent (semantic_matching_agent) has already:

Called fetch_metadata_tool
Identified potential column matches with semantic scores
The matching data is available in the session context
YOUR TASK:

STEP 1: Extract potential_matches array from semantic_matching_agent's output.

Look for the tool_response from the previous agent.
Extract the matches.
STEP 2: For EACH potential match, YOU MUST call compute_overlap_tool:

CRITICAL: Use the actual table/column names extracted from Phase 1 matches.

Example of calling the tool (Direct arguments):

compute_overlap_tool(dart_table=match.dart_table, dart_column=match.dart_column, source_table=match.source_table, source_column=match.source_column)

The tool returns a dictionary like:
{{
"total_rows": 10000,
"null_blank_percent": 1.5,
"data_overlap_percent": 92.5,
"overlap_count": 9250,
"source_distinct_count": 10000,
...
}}

STEP 3: Calculate THREE separate scores for each match:

A. Header Name Similarity (from Phase 1):
- This is the semantic_score passed from Phase 1
- Measures column name similarity (0-100)

B. Data Overlap Similarity (from tool response):
- This is the data_overlap_percent from compute_overlap_tool
- Measures actual data matching (0-100)

C. Combined Score (weighted average):
- Formula: (header_name_similarity × 0.4) + (data_overlap_similarity × 0.6)
- Why: Data overlap (60%) weighted higher because actual data match is more important.

STEP 4: Assign Confidence Levels based on combined_score:

HIGH Confidence (combined_score >= 75)
MEDIUM Confidence (combined_score 50-74)
LOW Confidence (combined_score < 50)
STEP 5: Rank matches by combined_score (highest first).

STEP 6: Generate comprehensive markdown report with TABULAR format:

Column Similarity Analysis Report
Executive Summary
Total potential matches analyzed: X
High/Medium/Low confidence counts...
High Confidence Matches (>= 75% Combined Score)
| DART Table Name | Field Name | Filename | Source Column | DART Sample Values | Header Name Similarity | Data Overlap Similarity | Combined Score | Match Reasoning |
|----------------|------------|----------|---------------|-------------------|----------------------|------------------------|----------------|----------------|
| project.dataset.table1 | column1 | source_file_123 | col_name | val1, val2 | 85.0% | 92.5% | 89.5% | Strong semantic match... |

(Repeat tables for Medium and Low confidence)

STEP 7: Format your final output as OutputFormat

Use the output schema defined.
text_response: The full markdown report.
tool_response: The structured JSON data.
====================================================================================
IMPORTANT RULES:
YOU MUST CALL compute_overlap_tool FOR EVERY SINGLE MATCH from Phase 1.
Do NOT wrap arguments in a "tool_input" dictionary. Pass them directly.
Calculate the Combined Score correctly: (Header * 0.4) + (Overlap * 0.6).
Output must use the OverlapValidationResponse schema with text_response and tool_response.
""",
tools=[overlap_tool],
output_schema=OverlapValidationResponse,
output_key="final_similarity_response",
description="Phase 2: Overlap validation - calculates data overlap percentages and confidence scores for semantic matches"
)