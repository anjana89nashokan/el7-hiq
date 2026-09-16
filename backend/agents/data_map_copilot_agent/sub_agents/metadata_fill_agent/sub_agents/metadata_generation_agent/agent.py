"""
Metadata Generation Agent - Generates the final comprehensive metadata mapping in structured JSON format.
"""

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from config.settings import config
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.prompts import get_prompts
from google.adk.planners import PlanReActPlanner
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.tools.csv_tools import signal_exit
from google.genai import types

# Agent configuration
agent_model = config.AGENT_MODEL


# Callback functions
def before_metadata_generation_callback(callback_context: CallbackContext):
    """Function to be executed before the LLM call."""
    print(f"Executing BEFORE callback for metadata_generation_agent.")
    print(f"Input to the agent is: {callback_context.state.to_dict()}")
    return callback_context


def after_metadata_generation_callback(callback_context: CallbackContext):
    """Function to be executed after the LLM call."""
    print(f"Executing AFTER callback for metadata_generation_agent.")
    print(f"Output from the agent is: {callback_context.state.to_dict()}")
    return callback_context


# Metadata Mapping Agent
metadata_mapping_agent = LlmAgent(
    name="metadata_mapping_agent",
    model=agent_model,
    instruction=get_prompts("metadata_mapping_prompt"),
    description="Maps source data chunks to the template structure using the data dictionary.",
    output_key='mapping_result',
    planner=PlanReActPlanner(),
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=1, attempts=3),
        ),
    ),

)
