from pydantic import BaseModel, Field
from agents.models import ToolResponse


class OutputFormatProfiling(BaseModel):
    """
    Output format for profiling agent when handling:
    - Data Profiling requests
    - Relationship Analysis requests

    These use the intelligent_profiling_tool or relationship_analysis_tool,
    which return ToolResponse structure.
    """

    text_response: str = Field(
        description="A markdown-formatted text response for the user."
    )

    tool_response: ToolResponse = Field(
        description="The raw response from intelligent_profiling_tool or relationship_analysis_tool."
    )
