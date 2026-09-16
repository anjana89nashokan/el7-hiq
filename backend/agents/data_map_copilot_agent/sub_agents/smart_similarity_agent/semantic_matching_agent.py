"""
Semantic Matching Agent - Phase 1: Analyzes column name similarity and semantic matches
"""

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from config.settings import config
from typing import List, Dict, Any

# Import the metadata fetching tool
from utils.smart_similarity_functions import fetch_metadata_tool

# Agent configuration
agent_model = config.AGENT_MODEL
dart_prefix = f"{config.DART_PROJECT_ID}.{config.DART_DATASET_ID}"
source_prefix = f"{config.BQ_PROJECT_ID}.{config.DATASET_ID}"

# Wrap the tool
metadata_tool = FunctionTool(fetch_metadata_tool)



from pydantic import BaseModel, Field
from typing import List


class PotentialMatch(BaseModel):
    source_table_name: str = Field(..., description="Actual source table name from input")
    source_column_name: str = Field(..., description="Actual source column name from metadata")
    source_column_type: str = Field(..., description="Data type of source column (e.g., STRING, INTEGER, DATE)")
    source_sample_values: List[str] = Field(..., description="List of sample values from source column")

    dart_table_name: str = Field(..., description="Actual DART table name from input")
    dart_column_name: str = Field(..., description="Actual DART column name from input")
    dart_column_type: str = Field(..., description="Data type of DART column (e.g., STRING, INTEGER, DATE)")
    dart_sample_values: List[str] = Field(..., description="List of sample values from DART column")

    semantic_score: float = Field(..., ge=0, le=100, description="Semantic similarity score between 0 and 100")
    match_reasoning: str = Field(..., description="Explanation of why this is a potential match")
    type_compatible: bool = Field(..., description="Whether the data types are compatible between source and DART")


class ToolResponse(BaseModel):
    potential_matches: List[PotentialMatch] = Field(
        ..., description="List of potential source-to-DART column matches"
    )
    store_for_next_agent: bool = Field(
        True,
        description="Indicates if data should be stored for the next phase (Phase 2 overlap validation)"
    )


class SemanticMatchingResponse(BaseModel):
    tool_response: ToolResponse = Field(
        ..., description="Wrapper object containing all potential matches and control flag"
    )



# Create the Semantic Matching Agent
semantic_matching_agent = LlmAgent(
    name="semantic_matching_agent",
    model=agent_model,
    instruction=f"""

You are the Semantic Matching Agent specialized in identifying potential column matches between source tables and DART reference tables.
Your job: analyze source table columns and identify which ones could potentially match specified DART target columns based on column names, data types, and sample value patterns.

This analysis will be used by a subsequent validation step to calculate actual data overlap percentages.

====================================================================================
CRITICAL WORKFLOW - STEP 1 OF 2
====================================================================================

YOU MUST FOLLOW THIS EXACT PROCESS:

STEP 1: Parse the user's input to extract the ACTUAL table and column names they provided:
   - dart_references: List of DART tables and columns to match against (FROM USER INPUT)
   - source_tables: List of source table names to analyze (FROM USER INPUT)

   CRITICAL: Use the EXACT table/column names the user provides in their request.
   DO NOT use example names like "gender_lookup" or "account_table_123".

   Example parsing:
   If user says: "Find columns matching DART table product_types column product_code in source tables inventory_456"
   Then extract:
     dart_references = [{{"table": "product_types", "columns": ["product_code"]}}]
     source_tables = ["inventory_456"]

STEP 2: Add full paths to table references (only if user didn't provide full path):
   - DART tables: If not provided, prefix with "{dart_prefix}."
   - Source tables: If not provided, prefix with "{source_prefix}."

STEP 3: Call fetch_metadata_tool with the PARSED parameters from user input:
   fetch_metadata_tool(
       dart_references=dart_references,  # Use values extracted from user input
       source_tables=source_tables        # Use values extracted from user input
   )

STEP 4: Analyze the returned metadata for each source column:
   For each source column, compare against each DART target column:

   A. Column Name Similarity (0-100 score):
      - Exact match: 100 (e.g., "gender_code" = "gender_code")
      - Strong semantic: 80-90 (e.g., "sex" vs "gender", "country" vs "country_code")
      - Partial match: 60-80 (e.g., "gender_id" vs "gender_code")
      - Related terms: 40-60 (e.g., "type" vs "category")
      - Unrelated: 0-40

   B. Data Type Compatibility:
      - Exact type match: Compatible
      - STRING to/from any: Usually compatible
      - Numeric types: Check if codes vs values

   C. Sample Value Analysis:
      - Compare sample values from source vs DART
      - Look for pattern matches (e.g., ["M", "F"] vs ["M", "F", "Other"])
      - Consider value formats (codes, descriptions, etc.)

STEP 5: Filter and rank potential matches:
   - ONLY include matches with semantic_score >= 40
   - Higher scores = stronger name similarity or sample pattern matches
   - Prioritize type-compatible matches

STEP 6: Output in the required JSON format (see below).

====================================================================================
IMPORTANT RULES:
====================================================================================

1. ALWAYS call fetch_metadata_tool FIRST - do not make up data
2. Base all analysis on actual metadata returned by the tool
3. Be inclusive - include matches with score >= 40 (Phase 2 will validate with data)
4. Explain your reasoning clearly for each match
5. Consider business context (e.g., email columns won't match gender columns)
6. Look beyond exact name matches - use semantic understanding

This output will be passed to Phase 2 (Overlap Validation Agent) which will calculate
actual data overlap percentages for each potential match you identify.

Your goal: Cast a wide but intelligent net of potential matches for Phase 2 to validate.

====================================================================================
OUTPUT FORMAT (JSON) - REQUIRED structure
====================================================================================

Return a JSON object exactly following this structure.

{{"tool_response":
{{
  "potential_matches": [
    {{
      "source_table_name": "actual source table from input",
      "source_column_name": "actual source column from metadata",
      "source_column_type": "STRING or INTEGER or DATE etc",
      "source_sample_values": ["sample1", "sample2", "sample3"],
      "dart_table_name": "actual DART table from input",
      "dart_column_name": "actual DART column from input",
      "dart_column_type": "STRING or INTEGER or DATE etc",
      "dart_sample_values": ["dart_sample1", "dart_sample2"],
      "semantic_score": 85.0,
      "match_reasoning": "Your analysis of why this is a potential match",
      "type_compatible": true
    }}
  ],
  "store_for_next_agent": true
}}
}}

**Important output rules:**
- CRITICAL: Set `store_for_next_agent: true` so Phase 2 can access this data
- Include ALL fields shown above for each potential match
- Use actual values from BigQuery metadata, not placeholders
- semantic_score should be a number between 0 and 100
- type_compatible should be true or false (boolean)
- Each match MUST have: source_table_name, source_column_name, dart_table_name, dart_column_name, semantic_score, match_reasoning
""",
    output_schema=SemanticMatchingResponse,
    output_key='semantic_matching_response',
    tools=[metadata_tool],
    description="Phase 1: Semantic column matching - identifies potential column matches based on names, types, and sample values"
)
