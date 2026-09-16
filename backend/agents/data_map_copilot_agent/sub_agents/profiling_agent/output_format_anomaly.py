from pydantic import BaseModel, Field
from agents.models import DataAnomalyAnalysisToolResponse


class OutputFormatAnomaly(BaseModel):
    """
    Output format for profiling agent when handling:
    - Data Anomaly Analysis requests

    These use the data_anomaly_analysis_tool,
    which returns DataAnomalyAnalysisToolResponse structure.
    """

    text_response: str = Field(
        description="A markdown-formatted text response for the user."
    )

    tool_response: DataAnomalyAnalysisToolResponse = Field(
        description="The raw response from data_anomaly_analysis_tool."
    )
