# agent.py
"""
DataMap Copilot Agent - Data Profiling support for Business System Analysts

- profiling_agent: For profiling/relationship analysis (uses ToolResponse)
- profiling_agent_anomaly: For anomaly detection (uses DataAnomalyAnalysisToolResponse)
- Orchestrator routes based on message content ([Data Profiling] vs [Data Anomaly Analysis])
"""
import logging
import os
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from config.settings import config
from pydantic import BaseModel, Field
from google.adk.agents.callback_context import CallbackContext

from .prompts import get_prompts
from .prompts_chat import get_prompts_chat

from utils.profiling_dispatcher import intelligent_profiling_tool,data_anomaly_analysis_tool
from utils.relationship_functions import relationship_analysis_tool

agent_model = config.AGENT_MODEL
logger = logging.getLogger(__name__)

profiling_tool = FunctionTool(intelligent_profiling_tool)
relationship_tool = FunctionTool(relationship_analysis_tool)
data_anomaly_analysis_tool = FunctionTool(data_anomaly_analysis_tool)

# Import both output formats
from .output_format_profiling import OutputFormatProfiling
from .output_format_anomaly import OutputFormatAnomaly

# Create callable instruction function for dynamic dataset_id retrieval
def get_dynamic_instruction(context):
    """
    Callable instruction that retrieves dataset_id from session at runtime.

    ADK calls this function and passes a context object that may contain session state.
    The parameter name 'context' is intentionally generic to work with different ADK versions.

    Args:
        context: ADK context object (may be tool_context, callback_context, or other)

    Returns:
        str: Formatted instruction with dynamic dataset_id
    """
    import logging
    logging.info("[get_dynamic_instruction] DATASET_OVERRIDE: Called by ADK")
    logging.info(f"[get_dynamic_instruction] DATASET_OVERRIDE: context type = {type(context)}")

    try:
        instruction, _ = get_prompts(context)
        logging.info("[get_dynamic_instruction] DATASET_OVERRIDE: Instruction retrieved successfully")
        return instruction
    except Exception as e:
        logging.error(f"[get_dynamic_instruction] DATASET_OVERRIDE: Error retrieving instruction: {e}")
        # Fallback to default instruction
        logging.info("[get_dynamic_instruction] DATASET_OVERRIDE: Using fallback with default dataset_id")
        instruction, _ = get_prompts(None)
        return instruction

# Get static description for agent initialization (description must be string, not callable)
# The description is less critical than instruction for dataset_id references
_, description_static = get_prompts()
logger.info(
    "[PROFILING AGENT] Description preview: %s...",
    description_static[:100].encode("ascii", "backslashreplace").decode("ascii"),
)

def before_callback(callback_context: CallbackContext):
    """Function to be executed before the LLM call."""
    print(f"Executing BEFORE callback for [PROFILING AGENT]..")
    # print(f"Input to the agent is: {callback_context.state.to_dict().keys()}")

    state = callback_context.state.to_dict()

    return None


def after_callback(callback_context: CallbackContext):
    """Function to be executed after the LLM call."""
    print(f"Executing AFTER callback for [PROFILING AGENT].")
    # print(f"Output from the agent is: {callback_context.state.to_dict().keys()}")
    # You can modify the output here if needed, or perform logging/checks
    # with open("state.txt", "a") as f:
    #     f.write(str(callback_context.state.to_dict()))
    # current_state = callback_context.state.to_dict()
    return None

def before_callback_anomaly(callback_context: CallbackContext):
    """Function to be executed before the LLM call."""
    print(f"Executing BEFORE callback for [PROFILING AGENT anomaly]..")
    # print(f"Input to the agent is: {callback_context.state.to_dict().keys()}")

    state = callback_context.state.to_dict()

    return None


def after_callback_anomaly(callback_context: CallbackContext):
    """Function to be executed after the LLM call."""
    print(f"Executing AFTER callback for [PROFILING AGENT anomaly].")
    # print(f"Output from the agent is: {callback_context.state.to_dict().keys()}")
    # You can modify the output here if needed, or perform logging/checks
    # with open("state.txt", "a") as f:
    #     f.write(str(callback_context.state.to_dict()))
    # current_state = callback_context.state.to_dict()
    return None

# The base instruction describes all profiling tools. Each agent below only has a
# subset of those tools registered, so we constrain the instruction per agent to
# the tools it actually owns — otherwise the model may call a tool that isn't
# available on that agent (e.g. "Tool 'intelligent_profiling_tool' not found").
def get_dynamic_instruction_profiling(context):
    base = get_dynamic_instruction(context)
    return (
        base
        + "\n\n## TOOL CONSTRAINT — IMPORTANT\n"
        "You can ONLY call these tools: `intelligent_profiling_tool`, "
        "`relationship_analysis_tool`. The `data_anomaly_analysis_tool` is NOT "
        "available to you — never call it."
    )


def get_dynamic_instruction_anomaly(context):
    base = get_dynamic_instruction(context)
    return (
        base
        + "\n\n## TOOL CONSTRAINT — IMPORTANT\n"
        "You are the Data Anomaly Analysis agent. You can ONLY call "
        "`data_anomaly_analysis_tool(table_references, anomaly_sensitivity=\"medium\")`. "
        "The `intelligent_profiling_tool` and `relationship_analysis_tool` are NOT "
        "available to you — never call them."
    )


# ============================================================================
# AGENT 1: For Data Profiling and Relationship Analysis
# ============================================================================
profiling_agent = Agent(
    name="profiling_agent",
    model=agent_model,
    instruction=get_dynamic_instruction_profiling,  # constrained to profiling/relationship tools
    description=description_static,  # Static string - uses default dataset_id
    tools=[
        profiling_tool,
        relationship_tool,
    ],
    before_agent_callback=before_callback,
    after_agent_callback=after_callback,
    output_schema=OutputFormatProfiling,
    output_key="final_profiling_response",
)

# ============================================================================
# AGENT 2: For Data Anomaly Analysis
# ============================================================================
profiling_agent_anomaly = Agent(
    name="profiling_agent_anomaly",
    model=agent_model,
    instruction=get_dynamic_instruction_anomaly,  # constrained to the anomaly tool
    description=description_static,  # Static string - uses default dataset_id
    tools=[
        data_anomaly_analysis_tool,
    ],
    before_agent_callback=before_callback_anomaly,
    after_agent_callback=after_callback_anomaly,
    output_schema=OutputFormatAnomaly,
    output_key="final_profiling_response",  # Same key for compatibility
)
