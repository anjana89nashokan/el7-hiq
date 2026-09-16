from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.mapping_ingestion.models import DataModelGraph
from utils.mapping_ingestion_tools import build_data_model_graph

data_model_tool = FunctionTool(build_data_model_graph)

data_model_agent = Agent(
    name="data_model_agent",
    model="deterministic",
    description="Builds lightweight data model graph (PoC nodes only).",
    tools=[data_model_tool],
    output_schema=DataModelGraph,
    output_key="data_model_graph",
)
