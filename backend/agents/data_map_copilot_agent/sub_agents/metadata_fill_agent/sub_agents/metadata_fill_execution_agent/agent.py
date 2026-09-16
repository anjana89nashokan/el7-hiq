"""
Metadata Fill Execution Agent - Executes the actual Excel generation using intelligent mapping
"""

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import FunctionTool
from config.settings import config
from utils.intelligent_metadata_fill import intelligent_metadata_fill_tool
from agents.metadata_fill_agent.prompts import get_prompts
from agents.models import IndeMapMetadataResponse
from google.genai.types import GenerateContentConfig

# Agent configuration
agent_model = config.AGENT_MODEL

# Wrap tool
intelligent_fill_tool = FunctionTool(intelligent_metadata_fill_tool)


def before_metadata_generation_callback(callback_context: CallbackContext):
    """Function to be executed before the LLM call."""
    print(f"Executing BEFORE callback for metadata_fill_execution_agent.")
    print(f"Input to the agent is: {callback_context.state.to_dict()}")
    # You can modify the input here if needed, or perform logging/checks
    current_state = callback_context.state.to_dict()
    
    return callback_context


# Metadata Fill Execution Agent
metadata_fill_execution_agent = LlmAgent(
    name="metadata_fill_execution_agent",
    generate_content_config=GenerateContentConfig(
                temperature=0.1,
                top_p=0.95,
                top_k=1,
                candidate_count=1,
                max_output_tokens=50000,
            ),
    model=agent_model,
    instruction=get_prompts("fill_agent_instruction"),
    #tools=[intelligent_fill_tool],
    description=get_prompts("fill_agent_description"),
    output_schema=IndeMapMetadataResponse,
    output_key="metadata_excel_file",
    # before_agent_callback=before_metadata_generation_callback
)
