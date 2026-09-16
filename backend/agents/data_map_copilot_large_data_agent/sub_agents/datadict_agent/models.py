from pydantic import BaseModel, Field
from typing import List, Optional

class DataDictionaryItem(BaseModel):
    """Represents a single entry in the data dictionary."""
    file_name: str = Field(..., description="Name of the file or table.")
    field_name: str = Field(..., description="Physical name of the field.")
    business_name: str = Field(..., description="Business-friendly name of the field.")
    data_type: str = Field(..., description="Data type of the field (e.g., STRING, INTEGER).")
    length: int = Field(0, description="Maximum length or size of the field.")
    precision: int = Field(0, description="Decimal precision for numeric types.")
    format: str = Field("-", description="Format pattern (e.g., YYYY-MM-DD).")
    nullable: str = Field("Yes", description="Whether the field is nullable (Yes/No).")
    default_value: str = Field("-", description="Default value if any.")
    primary_key: str = Field("No", description="Whether the field is a primary key (Yes/No).")
    foreign_key: str = Field("No", description="Whether the field is a foreign key (Yes/No).")
    field_description: str = Field(..., description="Business description of the field.")

class DataDictionaryToolResult(BaseModel):
    """The structured result returned by the data dictionary tools."""
    result: List[DataDictionaryItem] = Field(..., description="List of processed data dictionary items.")
    source: str = Field(..., description="Method used to obtain the data dictionary (vendor_upload or generated_from_profiling).")
    total_fields: int = Field(..., description="Total number of fields in the dictionary.")
    source_file: Optional[str] = Field(None, description="Path to the vendor data dictionary file if used.")
    error: Optional[str] = Field(None, description="Error message if processing failed.")

class DataDictionaryResponse(BaseModel):
    """The final output schema for the Data Dictionary Agent."""
    text_response: str = Field(..., description="Markdown summary and preview table of the data dictionary.")
    tool_response: DataDictionaryToolResult = Field(..., description="The raw structured data dictionary.")
