from pydantic import BaseModel, Field
from typing import List, Optional, Any

class RetrieverResponse(BaseModel):
    """Output schema for the Retriever Agent."""
    total_columns: int = Field(..., description="The total number of columns identified.")
    column_names: List[str] = Field(..., description="List of column names.")
    column_analysis: List[dict] = Field(..., description="List of objects containing column analysis results.")
    default_value_analysis: List[dict] = Field(..., description="List of objects containing default value analysis results.")
    relationship_analysis: str = Field(..., description="Markdown format analysis of table relationships.")
    batch_size: int = Field(..., description="Number of columns to be processed in a batch.")
    batch_count: int = Field(..., description="Number of batches needed.")

class DataDictionaryItem(BaseModel):
    """Represents a single entry in the data dictionary."""
    file_name: str = Field(..., description="Name of the file or table.")
    field_name: str = Field(..., description="Physical name of the field.")
    business_name: str = Field(..., description="Business-friendly name of the field.")
    data_type: str = Field(..., description="Data type of the field.")
    length: Optional[str] = Field(None, description="Maximum length or size.")
    precision: Optional[str] = Field(None, description="Decimal precision.")
    format: Optional[str] = Field(None, description="Format pattern.")
    nullable: str = Field(..., description="Whether the field is nullable (true or false).")
    default_value: str = Field(..., description="Default value.")
    primary_key: str = Field(..., description="Whether it's a primary key (true or false).")
    foreign_key: str = Field(..., description="Whether it's a foreign key (true or false).")
    field_description: str = Field(..., description="Business description.")

class GeneratorResponse(BaseModel):
    """Output schema for the Generator Agent."""
    data_dictionary: List[DataDictionaryItem] = Field(..., description="The generated batch of data dictionary entries.")
    data_dictionary_table_name: str = Field(..., description="The name of the target BigQuery table.")
    remaining_columns: List[str] = Field(..., description="List of columns yet to be processed.")
    total_columns: int = Field(..., description="Total columns in original dataset.")
    remaining_columns_count: int = Field(..., description="Count of remaining columns.")
    processed_batches: int = Field(..., description="Count of processed batches.")
    current_batch: int = Field(..., description="Current batch number.")

class SaverResponse(BaseModel):
    """Output schema for the Saver Agent."""
    remaining_columns: List[str] = Field(..., description="List of columns remaining for next cycle.")
    total_columns: int = Field(..., description="Total columns in original dataset.")
    remaining_columns_count: int = Field(..., description="Count of remaining columns.")
    message: str = Field(..., description="Status message.")
    data_dictionary_table_name: str = Field(..., description="The name of the target BigQuery table.")

class DataDictionaryResponse(BaseModel):
    """The final response schema for the Data Dictionary Agent."""
    message: str = Field(description="A markdown text response summarizing the work performed.")
    data_dictionary_table_id: str = Field(description="The ID of the BigQuery table where the data dictionary was saved.")
