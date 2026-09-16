from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.mapping_ingestion.models import TargetSchema
from utils.mapping_ingestion_tools import build_target_schema

target_metadata_tool = FunctionTool(build_target_schema)

target_metadata_agent = Agent(
    name="target_metadata_agent",
    model="deterministic",
    description="Parses target metadata Excel files into TargetSchema",
    tools=[target_metadata_tool],
    output_schema=TargetSchema,
    output_key="target_schema",
)
