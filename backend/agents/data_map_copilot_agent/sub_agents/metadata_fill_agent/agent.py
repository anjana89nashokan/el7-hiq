from google.adk.agents import SequentialAgent, LoopAgent, LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.planners import PlanReActPlanner
from config.settings import config
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.prompts import get_prompts

# Import sub-agents
from .sub_agents.template_analysis_agent import template_analysis_agent
from .sub_agents.retrieve_agent.agent import retrieve_agent
from .sub_agents.metadata_generation_agent.agent import metadata_mapping_agent
from .sub_agents.metadata_saving_agent.agent import persistence_agent
from .sub_agents.final_answer_agent.agent import final_answer_agent
from google.genai.types import Content, Part
from google.genai import types

# Callback for planning_agent to manage state indices
def planning_agent_before_callback(callback_context: CallbackContext):
    state = callback_context.state.to_dict()
    
    # Initialize indices if they don't exist
    if 'start_index' not in state:
        state['start_index'] = 0
    if 'end_index' not in state:
        state['end_index'] = 25
    if 'total_processed' not in state:
        state['total_processed'] = 0
        
    # After the first iteration, increment indices
    # We check if saving_agent has run by looking for 'rows_appended' in state
    if state.get('rows_appended'):
        rows_saved = state.get('rows_appended', 0)
        state['total_processed'] += rows_saved
        state['start_index'] = state['end_index']
        state['end_index'] += 25
        # Clear the flag to avoid double increment
        state['rows_appended'] = 0
    
    
    callback_context.state = state

    return callback_context

planning_agent = LlmAgent(
    name="planning_agent",
    model="gemini-2.5-flash",
    instruction=get_prompts("orchestrator_prompt"),
    description="Orchestrates the metadata fill process loop.",
    planner=PlanReActPlanner(),
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=1, attempts=3),
        ),
    ),
    # before_agent_callback=planning_agent_before_callback
)

metadata_loop_agent = LoopAgent(
    name="metadata_execution_loop",
    sub_agents=[
        metadata_mapping_agent,
        persistence_agent
    ],
    max_iterations=10
)

# Create the main Metadata Fill Agent (Sequential orchestrator)
# Flow: Analysis -> Loop (Fetch/Process/Save) -> Final Answer
metadata_fill_agent = SequentialAgent(
    name="metadata_fill_agent",
    sub_agents=[
        template_analysis_agent,
        retrieve_agent,
        metadata_loop_agent,
        final_answer_agent
    ],
    description=get_prompts("description")
)
