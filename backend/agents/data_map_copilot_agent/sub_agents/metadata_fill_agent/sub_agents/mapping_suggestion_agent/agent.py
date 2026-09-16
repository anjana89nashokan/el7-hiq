"""
Mapping Suggestion Agent - Creates intelligent mapping suggestions between source data and template columns.
"""

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from config.settings import config
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.prompts import get_prompts

# Agent configuration
agent_model = config.AGENT_MODEL

def before_mapping_callback(callback_context: CallbackContext):
    """Function to be executed before the LLM call."""
    print(f"Executing BEFORE callback for mapping_suggestion_agent.")
    return callback_context

# Mapping Suggestion Agent
mapping_suggestion_agent = LlmAgent(
    name="mapping_suggestion_agent",
    model=agent_model,
    instruction=get_prompts("mapping_suggestion_agent"),
    description="Creates intelligent mapping suggestions between source data and template columns.",
    output_key='mapping_suggestion',
    # before_agent_callback=before_mapping_callback
)
