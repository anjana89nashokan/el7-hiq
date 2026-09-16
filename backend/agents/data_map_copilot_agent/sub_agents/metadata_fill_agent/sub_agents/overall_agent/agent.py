"""
Overall Agent - Main orchestrator agent
"""

from google.adk.agents import LlmAgent
from config.settings import config
from adk_datamap_copilot.agents.metadata_fill_agent.prompts import get_prompts

# Agent configuration
agent_model = config.AGENT_MODEL

# Overall Agent
overall_agent = LlmAgent(
    name="overall_agent",
    model=agent_model,
    instruction=get_prompts("sing_agent_prompt"),
    description=get_prompts("description"),
    output_key='template_analysis'
)
