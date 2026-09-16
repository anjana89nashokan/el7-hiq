import os
from dotenv import load_dotenv
from google.adk.agents import Agent, LoopAgent, SequentialAgent, LlmAgent
from google.adk.tools import FunctionTool
from pydantic import BaseModel, Field
from typing import List
from .prompts import get_prompts
from config.settings import config
from google.adk.agents import Agent, LoopAgent
from google.adk.tools import FunctionTool
from .tools.bq_tools import sample_data_retrieval, load_final_bq, save_generator_batch_to_bq
from .prompts import RETRIEVER_INSTRUCTION, GENERATOR_INSTRUCTION, SUB_AGENT_INSTRUCTION, MAIN_AGENT_INSTRUCTION, SAVER_PROMPT
from config.settings import config
from google.adk.tools.tool_context import ToolContext
from google.adk.agents.callback_context import CallbackContext
# Import the new, dedicated tool function for this agent
from utils.datadict_functions import data_dictionary_tool
from google.adk.planners import PlanReActPlanner
# from .code_executor_agent import code_executor_agent
import json
from google.genai import types

# Agent configuration from environment
agent_model = config.AGENT_MODEL

# Create a FunctionTool wrapper for our new tool function
datadict_tool = FunctionTool(data_dictionary_tool)
    
instruction, description = get_prompts()


# Create the Data Dictionary Agent

model_name = config.AGENT_MODEL



from .models import DataDictionaryResponse, RetrieverResponse, GeneratorResponse, SaverResponse

def before_callback(callback_context: CallbackContext):
    """Function to be executed before the LLM call."""
    print(f"Executing BEFORE callback for data_dict_agent.")
    print(f"Input to the agent is: {callback_context.state.to_dict()}")

    state = callback_context.state.to_dict()
    # fetch_agent_response = json.loads(state['fetch_agent_response'])
    # generator_agent_response = json.loads(state['generator_agent_response'])
    # generator_agent_response['data_dictionary_last_index'] = fetch_agent_response['end_index']
    # state['generator_agent_response'] = generator_agent_response

    # You can modify the input here if needed, or perform logging/checks
    # with open("state.txt", "a") as f:
    #     f.write("\n BEFORE: " + str(callback_context.state.to_dict()))
    #     f.write("\n Updated: " + str(state))

    return None


def after_callback(callback_context: CallbackContext):
    """Function to be executed after the LLM call."""
    print(f"Executing AFTER callback for data_dict_agent.")
    print(f"Output from the agent is: {callback_context.state.to_dict()}")
    # You can modify the output here if needed, or perform logging/checks
    with open("state.txt", "a") as f:
        f.write(str(callback_context.state.to_dict()))
    current_state = callback_context.state.to_dict()
    return None

def signal_exit(tool_context: ToolContext):
  """Call this function ONLY when the critique indicates no further changes are needed, signaling the iterative process should end."""
  print(f"  [Tool Call] exit_loop triggered by {tool_context.agent_name}")
  tool_context.actions.escalate = True
  # Return empty dict as tools should typically return JSON-serializable output
  return {}


def run_code(input):
    """Call this function to run code."""
    print(f"  [Tool Call] run_code triggered by {input}")
    return input

# --- AGENT 1: The Retriever ---
# Dedicated to fetching data from BigQuery/Local Reports
retriever_agent = LlmAgent(
    name="retriever_agent",
    model=model_name,
    planner=PlanReActPlanner(),
    instruction=RETRIEVER_INSTRUCTION,
    output_key="retriever_agent_response",
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=1, attempts=3),
        ),
    ),
)

# --- AGENT 2: The Generator ---
# The "Brain" that processes retrieved data and generates dictionary entries
generator_agent = LlmAgent(
    name="generator_agent",
    model=model_name,
    planner=PlanReActPlanner(),
    instruction=GENERATOR_INSTRUCTION,
    output_key="generator_agent_response",
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=1, attempts=3),
        ),
    ),
)

# --- AGENT 2: The Saver ---
# Simple worker agent that just executes the save tool
saver_agent = LlmAgent(
    name="saver_agent",
    model=model_name,
    tools=[
        FunctionTool(save_generator_batch_to_bq),
        FunctionTool(signal_exit),
    ],
    instruction=SAVER_PROMPT,
    before_agent_callback=before_callback,
    output_key="saver_agent_response",
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=1, attempts=3),
        ),
    ),
)

# --- AGENT 3: The Sub-Agent (Controller) ---
# This agent orchestrates the loop. 
# In ADK, we can pass other agents as tools (Agent-as-a-Tool pattern)
sub_agent = LoopAgent(
    name="execution_loop_agent",
    sub_agents=[generator_agent, saver_agent],
    max_iterations=1000,
    # instruction=SUB_AGENT_INSTRUCTION
)


final_answer_agent = LlmAgent(
    name="final_answer_agent",
    model=model_name,
    instruction="""
Return the final message to the user. and include the data dictionary table id in a json format.
{{
    "message": "message",
    "data_dictionary_table_id": "table_id"
}}
    """,
    output_schema=DataDictionaryResponse,
    output_key="final_data_dict_response",
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=1, attempts=3),
        ),
    ),
)

data_dict_agent = SequentialAgent(
    name="data_dict_agent",
    description=MAIN_AGENT_INSTRUCTION,
    sub_agents=[retriever_agent, sub_agent, final_answer_agent],
)
