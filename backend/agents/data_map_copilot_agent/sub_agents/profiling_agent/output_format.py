
from pydantic import BaseModel, Field
from agents.models import DataAnomalyAnalysisToolResponse,ToolResponse 
# Data Dictionary
 
class  OutputFormat(BaseModel):
    text_response: str = Field(description="A markdown text response.")
    tool_response: ToolResponse = Field(description="The raw response from any tools used.")
 
        