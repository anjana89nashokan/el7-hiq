"""
Vendor Data Dictionary Streaming Agent (Plan 2)

Handles extraction and mapping of vendor-provided data dictionaries
using native Gemini document understanding.
"""

import os
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from .prompts import get_prompts
from agents.models import DataDictionaryResponse
from config.settings import config

# Import the dispatcher for vendor DD extraction
from utils.profiling_dispatcher import extract_and_map_vendor_dd

# Agent configuration
agent_model = config.AGENT_MODEL

# Create FunctionTool wrapper
vendor_dd_extraction_tool = FunctionTool(extract_and_map_vendor_dd)

instruction, description = get_prompts()

# Create the Vendor DD Streaming Agent
datadict_streaming_agent = LlmAgent(
    name="datadict_streaming_agent",
    model=agent_model,
    description=description,
    instruction=instruction,
    tools=[vendor_dd_extraction_tool],
    output_schema=DataDictionaryResponse,
    output_key="vendor_dd_streaming_response"
)
