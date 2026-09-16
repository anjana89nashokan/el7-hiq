"""
Data Dictionary Agent for Large Data Flow

This agent handles data dictionary generation for large datasets (1000+ columns).
It intelligently chooses between two sources:
1. Vendor DD file (if uploaded) - extracts and maps to standard format
2. Profiling results (if no vendor DD) - generates from BigQuery analysis
"""

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from agents.models import LargeDataDictionaryResponse
from config.settings import config
from .prompts import get_prompts

# Import the tools for DD generation and modification
from utils.datadict_batched import batched_data_dictionary_tool
from utils.datadict_native_streaming import extract_and_map_vendor_dd_streaming
from utils.datadict_modify import modify_data_dictionary_tool

# Agent configuration
agent_model = config.AGENT_MODEL

# Wrap tools as FunctionTools
generate_from_profiling_tool = FunctionTool(batched_data_dictionary_tool)
extract_from_vendor_tool = FunctionTool(extract_and_map_vendor_dd_streaming)
modify_datadict_tool = FunctionTool(modify_data_dictionary_tool)

# Get prompts
instruction, description = get_prompts()

# Create the Data Dictionary Agent for Large Data
datadict_large_agent = LlmAgent(
    name="datadict_large_agent",
    model=agent_model,
    description=description,
    instruction=instruction,
    tools=[
        generate_from_profiling_tool,  # Generates from profiling results (batched)
        extract_from_vendor_tool,       # Extracts from uploaded vendor DD file
        modify_datadict_tool,           # Modifies existing data dictionary based on user feedback
    ],
    output_schema=LargeDataDictionaryResponse,
    output_key="final_data_dict_response"
)
