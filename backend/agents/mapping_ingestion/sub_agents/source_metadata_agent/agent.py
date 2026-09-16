from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agents.mapping_ingestion.models import SourceSchema
from utils.mapping_ingestion_tools import build_source_schema

source_metadata_tool = FunctionTool(build_source_schema)

source_metadata_agent = Agent(
    name="source_metadata_agent",
    model="deterministic",
    description="Parses IndeMap source metadata Excel files into SourceSchema",
    tools=[source_metadata_tool],
    output_schema=SourceSchema,
    output_key="source_schema",
)
