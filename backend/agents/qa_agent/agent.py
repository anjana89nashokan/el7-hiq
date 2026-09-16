from google.adk.agents import LlmAgent, BaseAgent, Agent, SequentialAgent
from .sub_agents.query_understanding_agent.agent import query_understanding_agent
from .sub_agents.query_generation_agent.agent import query_generation_agent
from .sub_agents.query_review_rewrite_agent.agent import query_review_rewrite_agent
from .sub_agents.query_execution_agent.agent import query_execution_agent
from .tools.initialize_state import initialize_state_var

from typing import Dict, Any, List
from typing import AsyncGenerator
from typing_extensions import override
from google.adk.events import Event, EventActions
from google.adk.agents.invocation_context import InvocationContext
from google.adk.tools import ToolContext
import logging
from utils.bg_query_utils import get_tables_metadata
from config.settings import config


# --- Configure Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


agent_model = config.AGENT_MODEL
bigquery_project = config.BQ_PROJECT_ID
dataset_id = config.BQ_DATASET_ID



qa_root_agent = SequentialAgent(
    name="orchestrator_agent",
    # model="gemini-2.5-flash",
    description="You are an **orchestrator agent** responsible for overseeing and delegating tasks to specialized agents in a **text-to-SQL pipeline**.",
    # tools=[get_tables_metadata],
    sub_agents=[query_understanding_agent, query_generation_agent, query_review_rewrite_agent, query_execution_agent],

)
