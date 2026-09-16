"""
Similarity Executor Agent - Handles similarity check execution
Uses batched functions from utils/smart_similarity_functions.py
"""

from google.adk.agents import LlmAgent
from config.settings import config
from .prompts import instruction

# Import tools from existing utils
from utils.smart_similarity_functions_batched import fetch_metadata_tool, compute_overlap_tool

# Agent configuration
agent_model = config.AGENT_MODEL

similarity_executor_agent = LlmAgent(
    name="similarity_executor_agent",
    model=agent_model,
    description="Executes similarity check using two-phase batched approach: metadata fetch + overlap validation",

    instruction=instruction,

    # Register the batched tools
    tools=[
        fetch_metadata_tool,
        compute_overlap_tool
    ]
)