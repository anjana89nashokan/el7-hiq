"""
Template Analysis Agent - Analyzes the Excel template structure to understand target columns and sheets.
"""

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from config.settings import config
from utils.template_analysis_tool import analyze_template_structure
from google.genai import types
from utils.bg_query_utils import create_metadata_table_from_headers
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.prompts import get_prompts
# Agent configuration
agent_model = config.AGENT_MODEL

# Wrap tools
template_analysis_tool = FunctionTool(analyze_template_structure)

# Template Analysis Agent
template_analysis_agent = LlmAgent(
    name="template_analysis_agent",
    model=agent_model,
    instruction=get_prompts("template_analysis_prompt"),
    description="Analyzes the Excel template structure to identify metadata and file specs headers.",
    tools=[template_analysis_tool],
    output_key='template_analysis',
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=1, attempts=3),
        ),
    ),
)
