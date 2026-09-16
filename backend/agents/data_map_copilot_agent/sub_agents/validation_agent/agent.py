# **Action:** Create this new file and add the following code.

import os
from typing import Optional
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from config.settings import config

# Use a robust, absolute import path starting from the 'server' root
from utils.validation_functions import content_extraction_tool, validation_engine_tool
# Use a relative import for the local prompts file
from .prompts import get_prompts

# Import the set_model_response function
def set_model_response(text_response: str, tool_response: dict, should_update: bool = True, artifact_delta: Optional[dict] = None) -> str:
    """Set the final model response with structured output.
    
    Args:
        text_response: The text response to display to the user
        tool_response: The structured tool response object
        should_update: Whether to update the session state
        artifact_delta: Additional artifacts to store in state
    
    Returns:
        Success message
    """
    return "Response set successfully"

# Wrap the Python functions to make them usable by the agent
extraction_tool = FunctionTool(content_extraction_tool)
validation_tool = FunctionTool(validation_engine_tool)
set_response_tool = FunctionTool(set_model_response)

# Get the instruction and description from our prompts file
instruction, description = get_prompts()

# Define the final agent object
validation_agent = Agent(
    name="validation_agent",
    model=config.AGENT_MODEL,
    instruction=instruction,
    description=description,
    tools=[
        extraction_tool,
        validation_tool,
        set_response_tool
    ]
)
 