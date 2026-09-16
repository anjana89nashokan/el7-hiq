from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from config.settings import config
from agents.data_map_copilot_agent.sub_agents.metadata_fill_agent.prompts import get_prompts
from pydantic import BaseModel, Field

agent_model = config.AGENT_MODEL

class FinalResponse(BaseModel):
    message: str = Field(description="A markdown text response.")
    metadata_table_id: str = Field(description="The ID of the metadata table.")
    filespecs_table_id: str = Field(description="The ID of the filespecs table.")

final_answer_agent = LlmAgent(
    name="final_answer_agent",
    model=agent_model,
    instruction=get_prompts("metadata_final_answer_instruction"),
    description="Presents the final metadata result summary.",
    output_schema=FinalResponse,
    output_key="final_metadata_response"
)
