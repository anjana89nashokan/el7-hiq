from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import FunctionTool
from google.adk.planners import PlanReActPlanner
from config.settings import config
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.prompts import get_prompts
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.tools.bq_tools import get_bq_table_rows_range
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.tools.csv_tools import signal_exit
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.sub_agents.sql_agent.agent import root_agent
from google.adk.tools.agent_tool import AgentTool
from google.genai import types


agent_model = config.AGENT_MODEL

def before_retrieve_callback(callback_context: CallbackContext):
    state = callback_context.state.to_dict()
    print(f"Retrieving rows {state.get('start_index')} to {state.get('end_index')}")
    return callback_context

retrieve_agent = LlmAgent(
    name="retrieve_agent",
    model=agent_model,
    planner=PlanReActPlanner(),
    instruction="""
You are a Data Retriever Agent. Your ONLY task is to fetch sample data and profile reports from Context.

# Core Process (Strict)
1. Analyze the context and retrieve the following information:
- Column Analysis.
- Default value Analysis.
- Relationship Analysis.

2. Finally return the retrieved information to the generator agent.

## Output Format (Strict JSON)
You must output a JSON object containing the batch. Do not include markdown formatting (```json).

{
  "total_columns": "integer",
  "column_names": "list of column names",
  "column_analysis": "string",48
  "default_value_analysis": "string",
  "relationship_analysis": "string",
  "metadata_tab_headers": ["Col1", "Col2", ...],
  "file_specs_tab_headers": ["Field1", "Field2", ...],
  "metadata_table_name": "TARGET_TABLE_NAME"
}

""",
    description="Fetches chunks of source data from BigQuery for metadata mapping.",
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=1, attempts=3),
        ),
    ),
    # before_agent_callback=before_retrieve_callback
)
