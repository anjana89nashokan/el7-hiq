"""
Smart Similarity Agent - Sequential two-phase column matching
Phase 1: Semantic matching based on names, types, and sample data
Phase 2: Overlap validation with actual data overlap percentages
"""

from google.adk.agents import SequentialAgent
from config.settings import config

# Import the two sub-agents
from .semantic_matching_agent import semantic_matching_agent
from .overlap_validation_agent import overlap_validation_agent

# Agent configuration
agent_model = config.AGENT_MODEL

# Create the Smart Similarity Sequential Agent
# This ensures semantic_matching_agent ALWAYS runs before overlap_validation_agent
smart_similarity_agent = SequentialAgent(
    name="smart_similarity_agent",
    sub_agents=[
        semantic_matching_agent,        # Step 1: ALWAYS runs first - identifies potential matches
        overlap_validation_agent        # Step 2: ALWAYS runs second - validates with data overlap
    ],
    description="""Smart Similarity Agent: Two-phase column matching for DART reference tables.

Phase 1 (Semantic Matching):
- Fetches all source table column metadata from BigQuery
- Fetches specified DART target column metadata
- Analyzes column name similarity, data types, and sample values
- Identifies potential matches with semantic scores

Phase 2 (Overlap Validation):
- Takes potential matches from Phase 1
- Calculates actual data overlap percentages using BigQuery
- Compares distinct values between source and DART columns
- Assigns confidence levels (HIGH/MEDIUM/LOW)
- Generates comprehensive markdown report with recommendations

Input Requirements:
- dart_references: List of DART tables and columns to match
  Example: [{"table": "ihg-dart-edw-dev2.DB_WRK.gender_lookup", "columns": ["gender_code"]}]
- source_tables: List of source table names to analyze
  Example: ["account_table_123", "identity_table_56g"]

Output:
- Ranked column matches with similarity scores
- Data overlap percentages for each match
- Confidence levels and detailed reasoning
- Formatted markdown report for BSA review
"""
)
