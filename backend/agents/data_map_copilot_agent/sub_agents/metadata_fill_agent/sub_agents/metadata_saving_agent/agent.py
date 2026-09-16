from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import FunctionTool
from config.settings import config
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.prompts import get_prompts
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.tools.bq_tools import append_chunk_to_bq, append_filespecs_to_bq

from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.tools.csv_tools import signal_exit
from google.genai import types

agent_model = config.AGENT_MODEL

def before_saving_callback(callback_context: CallbackContext):
    state = callback_context.state.to_dict()
    print(f"Saving metadata chunk for rows {state.get('start_index')} to {state.get('end_index')}")
    return callback_context

def after_saving_callback(callback_context: CallbackContext):
    state = callback_context.state.to_dict()
    # Assume the saving agent output contains info about rows appended
    # For now, we'll manually set a flag that the planning_agent will use
    state['rows_appended'] = 25 # Default chunk size
    callback_context.state = state
    return callback_context

# Persistence Agent
persistence_agent = LlmAgent(
    name="persistence_agent",
    model=agent_model,
    tools=[
        FunctionTool(append_chunk_to_bq),
        FunctionTool(append_filespecs_to_bq),
        FunctionTool(signal_exit)
    ],
    instruction=get_prompts("persistence_prompt"),
    description="Saves mapped metadata and file specifications to BigQuery.",
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=1, attempts=3),
        ),
    ),
    # before_agent_callback=before_saving_callback,
    # after_agent_callback=after_saving_callback
)
