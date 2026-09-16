from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


# ============================================================================
# PROFILING SUMMARY

class ProfilingSummary(BaseModel):
    null_pct: Optional[float] = Field(None, description="Percentage of null values")
    cardinality: Optional[int] = Field(None, description="Number of unique values")
    top_values: Optional[List[str]] = Field(None, description="Most frequent values")
    suggested_data_type: Optional[str] = Field(None, description="Inferred data type")
    format: Optional[str] = Field(None, description="Detected format pattern")
# ============================================================================
class AttributeMetadata(BaseModel):
    attribute_name: str = Field(..., alias="Attribute Name", description="Physical attribute name (e.g., CUSTOMERS_CUST_ID)")
    logical_attribute_name: Optional[str] = Field(None, alias="Logical Attribute Name", description="Business-friendly name")
    attribute_description: Optional[str] = Field(None, alias="Attribute Description", description="Detailed description of the attribute")
    data_type: str = Field(..., alias="Data Type", description="Data type (INTEGER, STRING, DATE, etc.)")
    length: Optional[str] = Field(None, alias="Length", description="Maximum length or size")
    precision: Optional[str] = Field(None, alias="Precision", description="Decimal precision for numeric types")
    format: Optional[str] = Field(None, alias="Format", description="Format pattern (e.g., date format)")
    nullability: str = Field(..., alias="Nullability", description="Whether nulls are allowed (Y/N)")
    default_values: Optional[str] = Field(None, alias="Default Values", description="Default value if any")
    primary_key: Optional[str] = Field(None, alias="Primary Key", description="Primary key indicator (1 if PK)")
    foreign_key: Optional[str] = Field(None, alias="Foreign Key", description="Foreign key indicator (Y/N)")
    alternate_key1: Optional[str] = Field(None, alias="Alternate Key1", description="Alternate key indicator")
    value: Optional[str | int | float | bool] = Field(None, alias="Value", description="Actual value from this specific row")
    
    # Additional metadata for internal use
    source: Optional[str] = Field(None, description="Source of the mapping (datadict, profiling, static)")
    field: Optional[str] = Field(None, description="Source field name")
    transform: Optional[str] = Field(None, description="Transformation applied")
    reasoning: Optional[str] = Field(None, description="Mapping reasoning")
    confidence: Optional[str] = Field(None, description="Confidence level (low, medium, high)")
    profiling_summary: Optional[ProfilingSummary] = Field(None, description="Profiling statistics")

    class Config:
        populate_by_name = True


# ============================================================================
# ROW LEVEL METADATA (Single row's complete metadata)
# ============================================================================
class RowLevelMetadata(BaseModel):
    row_id: int = Field(..., description="Sequential row identifier")
    table_name: str = Field(..., description="Source table name (e.g., CUSTOMERS, ORDERS)")
    attributes: List[AttributeMetadata] = Field(..., description="All attribute mappings for this row")


# ============================================================================
# FILE SPECS MAPPING (File-level metadata)
# ============================================================================
class FileSpecsMapping(BaseModel):
    template_field: str = Field(..., description="Template field name")
    source: str = Field(..., description="Source type (datadict, static, inferred)")
    field: Optional[str] = Field(None, description="Source field name")
    value: Optional[str] = Field(None, description="Value for file-level metadata")
    transform: Optional[str] = Field(None, description="Transformation applied")
    reasoning: Optional[str] = Field(None, description="Mapping explanation")
    confidence: Optional[str] = Field(None, description="Confidence level")


# ============================================================================
# RELATIONSHIP ANALYSIS
# ============================================================================
class RelationshipAnalysis(BaseModel):
    from_table: str = Field(..., description="Origin table name")
    from_column: str = Field(..., description="Column in the origin table")
    to_table: str = Field(..., description="Destination table name")
    to_column: str = Field(..., description="Column in the destination table")
    relationship_type: str = Field(..., description="Type of relationship (one_to_many, many_to_one, etc.)")
    matching_strategy: Optional[str] = Field(None, description="Strategy used to identify relationship")
    confidence: str = Field(..., description="Confidence level (low, medium, high)")


# ============================================================================
# MAIN OUTPUT SCHEMA (New structure for row-by-row metadata)
# ============================================================================
class MetadataFillExecutionResponse(BaseModel):
    row_level_metadata: List[RowLevelMetadata] = Field(
        ..., 
        description="Array of complete metadata for each row in the input data"
    )
    file_specs_mapping: List[FileSpecsMapping] = Field(
        default_factory=list, 
        description="File-level metadata mappings"
    )
    relationship_analysis: List[RelationshipAnalysis] = Field(
        default_factory=list, 
        description="Detected relationships between tables"
    )
    unmapped_columns: List[str] = Field(
        default_factory=list, 
        description="Source columns not mapped to any target"
    )
    notes: Optional[str] = Field(
        None, 
        description="Additional notes or comments for the BSA"
    )

    # class Config:
    #     json_schema_extra = {
    #         "example": {
    #             "row_level_metadata": [
    #                 {
    #                     "row_id": 1,
    #                     "table_name": "CUSTOMERS",
    #                     "attributes": [
    #                         {
    #                             "Attribute Name": "CUSTOMERS_CUST_ID",
    #                             "Logical Attribute Name": "Customer Identifier",
    #                             "Attribute Description": "Unique system-generated ID",
    #                             "Data Type": "INTEGER",
    #                             "Length": "3",
    #                             "Precision": "",
    #                             "Format": "",
    #                             "Nullability": "N",
    #                             "Default Values": "",
    #                             "Primary Key": "1",
    #                             "Foreign Key": "N",
    #                             "Alternate Key1": "",
    #                             "Value": 101
    #                         }
    #                     ]
    #                 }
    #             ],
    #             "file_specs_mapping": [
    #                 {
    #                     "template_field": "Physical File Name",
    #                     "source": "datadict",
    #                     "field": "File Name",
    #                     "value": "CUSTOMERS_20231101.csv",
    #                     "transform": "none",
    #                     "reasoning": "From data dictionary",
    #                     "confidence": "high"
    #                 }
    #             ],
    #             "relationship_analysis": [
    #                 {
    #                     "from_table": "ORDERS",
    #                     "from_column": "CUST_ID",
    #                     "to_table": "CUSTOMERS",
    #                     "to_column": "CUST_ID",
    #                     "relationship_type": "many_to_one",
    #                     "confidence": "high"
    #                 }
    #             ],
    #             "unmapped_columns": [],
    #             "notes": "All mappings completed successfully"
    #         }
    #     }

 
# ============================================================================
# COLUMN LEVEL MAPPING
# ============================================================================
class ColumnLevelMapping(BaseModel):
    template_column: str = Field(..., description="The template field name (e.g., Attribute Name)")
    source: str = Field(..., description="Source of the field (datadict, profiling, static, etc.)")
    field: Optional[str] = Field(None, description="The field name from the source, can be empty")
    value: Optional[str] = Field(None, description="Actual or sample value of the field")
    transform: str = Field(..., description="Transformation rule applied to the source field")
    reasoning: str = Field(..., description="Explanation for how this mapping was derived")
    confidence: str = Field(..., description="Confidence level of this mapping (low, medium, high)")
    profiling_summary: Optional[ProfilingSummary] = Field(
        None, description="Summary of profiling data for this field"
    )


# ============================================================================
# FILE SPECS MAPPING
# ============================================================================
class FileSpecsMapping(BaseModel):
    template_field: str = Field(..., description="Template field name")
    source: str = Field(..., description="Source type (datadict, static, inferred)")
    field: Optional[str] = Field(None, description="Source field name")
    value: Optional[str] = Field(None, description="Value for file-level metadata")
    transform: Optional[str] = Field(None, description="Transformation applied")
    reasoning: Optional[str] = Field(None, description="Mapping explanation")
    confidence: Optional[str] = Field(None, description="Confidence level")


# ============================================================================
# RELATIONSHIP ANALYSIS
# ============================================================================
class RelationshipAnalysis(BaseModel):
    from_table: str = Field(..., description="Origin table name of the relationship")
    from_column: str = Field(..., description="Column in the origin table")
    to_table: str = Field(..., description="Destination table name of the relationship")
    to_column: Optional[str] = Field(None, description="Column in the destination table")
    relationship_type: str = Field(..., description="Type of relationship (e.g., PK-FK, candidate_foreign_key)")
    matching_strategy: str = Field(..., description="Strategy used to identify the relationship (e.g., name_match)")
    confidence: str = Field(..., description="Confidence level in the detected relationship")


# ============================================================================
# TOOL RESPONSE (Nested in final output)
# ============================================================================
class ToolResponse(BaseModel):
    column_level_mapping: List[ColumnLevelMapping] = Field(
        default_factory=list, description="List of column-level mappings"
    )
    file_specs_mapping: List[FileSpecsMapping] = Field(
        default_factory=list, description="List of file-level mappings"
    )
    relationship_analysis: Optional[List[RelationshipAnalysis]] = Field(
        None, description="Detected relationships"
    )
    unmapped_columns: Optional[List[str]] = Field(
        default_factory=list, description="Unmapped columns"
    )
    notes: Optional[str] = Field(None, description="Additional notes")
    store_for_next_agent: bool = Field(True, description="Store for next agent")


# ============================================================================
# INDEMAP METADATA RESPONSE (Main output schema)
# ============================================================================
class IndeMapMetadataResponse(BaseModel):
    tool_response: MetadataFillExecutionResponse = Field(..., description="Structured tool response")
    text_response: str = Field(..., description="Human-readable summary")


# ============================================================================
# METADATA GENERATION OUTPUT (Alternative format)
# ============================================================================
class MetadataGenerationOutput(BaseModel):
    tool_response: ToolResponse = Field(..., description="Structured metadata mapping results")
    text_response: str = Field(..., description="Narrative summary")


# ============================================================================
# TEMPLATE ANALYSIS AGENT TASK
# ============================================================================
class TemplateAnalysisAgentTask(BaseModel):
    role: str = Field(default="Template Analysis Agent", description="Agent role")
    purpose: str = Field(default="Analyze Excel template structure", description="Agent purpose")
    instructions: str = Field(default="Use analyze_template_structure tool", description="Instructions")
    tasks: List[str] = Field(default_factory=list, description="Agent tasks")
    required_column_level_targets: List[str] = Field(default_factory=list, description="Required columns")
    required_file_level_targets: List[str] = Field(default_factory=list, description="Required file fields")
    output_format: str = Field(default="Dictionary format", description="Output format")
    important_note: str = Field(default="Do NOT write to Excel files", description="Important note")


# ============================================================================
# MAPPING SUGGESTION AGENT
# ============================================================================
class MappingSuggestion(BaseModel):
    source_field: str = Field(..., description="Source field name")
    target_column: str = Field(..., description="Target column name")
    match_type: str = Field(..., description="Match type (direct, semantic, inferred, static)")
    confidence: str = Field(..., description="Confidence level")
    reasoning: str = Field(..., description="Reasoning for match")


class MappingSuggestionAgent(BaseModel):

    role: str = Field(default="Mapping Suggestion Agent", description="Agent role")
    purpose: str = Field(default="Create intelligent mapping suggestions", description="Purpose")
    tasks: List[str] = Field(default_factory=list, description="Tasks")
    mapping_strategy: Dict[str, str] = Field(default_factory=dict, description="Mapping strategies")
    input_format: str = Field(default="{template_analysis}", description="Input format")
    output_description: str = Field(default="Mapping suggestions list", description="Output description")
    mapping_suggestions: List[MappingSuggestion] = Field(
        default_factory=list, description="Generated mapping suggestions"
    )


class DataDictionaryItem(BaseModel):
    file_name: str
    field_name: str
    data_type: str
    nullable: str
    default_value: Optional[str]
    format: Optional[str]
    length: int
    primary_key: str
    foreign_key: str
    field_description: str
    business_name: str
    precision: Optional[int] = 0  # present in example but not in main schema


class ToolResponse(BaseModel):
    result: List[DataDictionaryItem]


class DataDictionaryResponse(BaseModel):
    text_response: str = Field(description="A markdown text response.")
    tool_response: ToolResponse = Field(description="The raw response from any tools used.")

class LargeDataDictionaryItem(BaseModel):
    file_name: str
    field_name: str
    data_type: str
    business_name: Optional[str] = None
    field_description: Optional[str] = None

    # Metadata fields - OPTIONAL with sensible defaults
    nullable: Optional[str] = "Yes"           # Default to Yes if not specified
    primary_key: Optional[str] = "No"         # Default to No
    foreign_key: Optional[str] = "No"         # Default to No
    default_value: Optional[str] = None
    format: Optional[str] = None
    length: Optional[int] = 0
    precision: Optional[int] = 0 

class LargeDtaDicToolResponse(BaseModel):
    result: List[LargeDataDictionaryItem]


class LargeDataDictionaryResponse(BaseModel):
    text_response: str = Field(description="A markdown text response.")
    tool_response: LargeDtaDicToolResponse = Field(description="The raw response from any tools used.")


class ProfilingSummary(BaseModel):
    null_pct: Optional[float] = None
    cardinality: Optional[int] = None
    top_values: Optional[List[Any]] = None
    suggested_data_type: Optional[str] = None
    format: Optional[str] = None


class ColumnLevelMapping(BaseModel):
    template_column: str
    source: str
    field: str
    transform: str
    reasoning: str
    confidence: str
    value: Optional[Any] = None
    profiling_summary: Optional[ProfilingSummary] = None


class FileSpecsMapping(BaseModel):
    template_field: str
    source: str
    field: Optional[str] = None
    value: Optional[Any] = None
    transform: Optional[str] = None
    reasoning: Optional[str] = None
    confidence: Optional[str] = None


class RelationshipAnalysis(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relationship_type: str
    matching_strategy: str
    confidence: str


class ToolResponse(BaseModel):
    column_level_mapping: List[ColumnLevelMapping]
    file_specs_mapping: List[FileSpecsMapping]
    relationship_analysis: List[RelationshipAnalysis]
    unmapped_columns: Optional[List[Any]] = []
    notes: Optional[str] = None
    store_for_next_agent: bool



class ProfilingSummary(BaseModel):
    null_pct: Optional[float] = Field(None, description="Percentage of null values in the column")
    cardinality: Optional[int] = Field(None, description="Number of unique values in the column")
    top_values: Optional[List[str]] = Field(None, description="Most common values observed")
    suggested_data_type: Optional[str] = Field(None, description="Data type inferred from profiling")
    format: Optional[str] = Field(None, description="Format derived from sample data or profiling")


class ColumnLevelMapping(BaseModel):
    template_column: str = Field(..., description="The template field name (e.g., Attribute Name)")
    source: str = Field(..., description="Source of the field (datadict, profiling, static, etc.)")
    field: Optional[str] = Field(None, description="The field name from the source, can be empty")
    value: Optional[Any] = Field(None, description="Actual or sample value of the field")
    transform: str = Field(..., description="Transformation rule applied to the source field")
    reasoning: str = Field(..., description="Explanation for how this mapping was derived")
    confidence: str = Field(..., description="Confidence level of this mapping (low, medium, high)")
    profiling_summary: Optional[ProfilingSummary] = Field(
        None, description="Summary of profiling data for this field"
    )



class RelationshipAnalysis(BaseModel):
    from_table: str = Field(..., description="Origin table name of the relationship")
    from_column: str = Field(..., description="Column in the origin table")
    to_table: str = Field(..., description="Destination table name of the relationship")
    to_column: str = Field(..., description="Column in the destination table")
    relationship_type: str = Field(..., description="Type of relationship (e.g., PK-FK, candidate_foreign_key)")
    matching_strategy: str = Field(..., description="Strategy used to identify the relationship (e.g., name_match)")
    confidence: str = Field(..., description="Confidence level in the detected relationship")


class MetadataGenerationResponse(BaseModel):
    column_level_mapping: List[ColumnLevelMapping] = Field(..., description="List of column-level metadata mappings.")
    file_specs_mapping: List[FileSpecsMapping] = Field(..., description="List of file-level metadata mappings.")
    relationship_analysis: Optional[List[RelationshipAnalysis]] = Field(None, description="Detected relationships among columns.")
    unmapped_columns: Optional[List[str]] = Field(None, description="List of source columns not mapped to any target.")
    notes: Optional[str] = Field(None, description="Additional notes or manual intervention required.")
    store_for_next_agent: bool = Field(True, description="Whether to persist data for subsequent agents.")


class MetadataGenerationAgentOutput(BaseModel):
    tool_response: MetadataGenerationResponse = Field(..., description="Structured JSON output from the Metadata Generation Agent.")
    text_response: str = Field(..., description="Human-readable explanation of the mapping summary.")

from typing import Dict, List, Optional
from pydantic import BaseModel, Field
 
 
class SeverityDistribution(BaseModel):
    low: int = 0
    medium: int = 0
    high: int = 0
 
 
class AnomalySummary(BaseModel):
    columns_with_anomalies: int = 0
    total_anomaly_types: int = 0
    anomaly_types: Dict[str, int] = Field(default_factory=dict)
    data_quality_score: float = 0.0
    severity_distribution: SeverityDistribution = SeverityDistribution()
 
 
class TableAnomalyReport(BaseModel):
    table_name: str = ""
    table_reference: str = ""
    total_anomalies_found: int = 0
    anomaly_summary: AnomalySummary = AnomalySummary()
    column_anomalies: Dict[str, dict] = Field(default_factory=dict)
    table_level_anomalies: List[dict] = Field(default_factory=list)
 
 
class ProcessingStats(BaseModel):
    anomaly_categories_detected: int = 0
    total_anomalies_detected: int = 0
    tables_processed: int = 0
    total_processing_time: float = 0.0
 
 
class SummaryStatistics(BaseModel):
    total_tables_analyzed: int = 0
    total_anomalies: int = 0
    overall_data_quality_score: float = 0.0
    anomaly_categories: Dict[str, int] = Field(default_factory=dict)
    severity_distribution: SeverityDistribution = SeverityDistribution()
 
 
class DataAnomalyAnalysisToolResponse(BaseModel):
    status: str = ""
    sensitivity_level: str = ""
    analysis_timestamp: int = 0
    processing_mode: str = ""
    tables_analyzed: int = 0
    processing_stats: ProcessingStats = ProcessingStats()
    summary_statistics: SummaryStatistics = SummaryStatistics()
    table_anomaly_reports: Dict[str, TableAnomalyReport] = Field(default_factory=dict)

# -----------------------------
# Column Analysis
# -----------------------------
class ColumnAnalysisItem(BaseModel):
    data_type: str
    total_count: int
    unique_count: int
    uniqueness_percentage: float
    distinct_values_sample: List[Any]
    avg_length: float
    blank_count: int
    blank_percentage: float
    min_value: Optional[float]
    max_value: Optional[float]
    avg_value: Optional[float]
    null_count: int
    null_percentage: float
    primary_key_candidate: bool
    foreign_key_candidate: bool


# -----------------------------
# Default Value Analysis
# -----------------------------
class DefaultValueItem(BaseModel):
    total_rows: int
    default_value: str
    default_count: int
    default_pct: float


# -----------------------------
# Data Quality Score
# -----------------------------

class DataQualityDimensionScores(BaseModel):
    completeness: float
    uniqueness: float
    distribution: float
    validity: float

class ColumnQualityScore(BaseModel):
    overall_score: float
    dimension_scores: DataQualityDimensionScores

class DataQualityScore(BaseModel):
    overall_score: float
    dimension_scores: DataQualityDimensionScores
    per_column_scores: Dict[str, ColumnQualityScore]


# -----------------------------
# Table Summary
# -----------------------------
class TableSummary(BaseModel):
    total_rows: int
    total_columns: int


# -----------------------------
# Enhanced Analysis Subcomponents
# -----------------------------
class TableContext(BaseModel):
    detected_level: str
    confidence: float
    primary_entity: str
    business_context: str
    reasoning: str


class PrimaryKeyRecommendation(BaseModel):
    column: str
    rank: int
    confidence: str  # HIGH | MEDIUM | LOW
    uniqueness_percentage: float
    null_percentage: float
    data_type: str
    reasoning: str


class CompositeKeyRecommendation(BaseModel):
    columns: List[str]
    uniqueness_percentage: float
    is_candidate: bool
    business_meaning: str
    composite_score: float


class ValidationResult(BaseModel):
    columns: List[str]
    distinct_count: int
    total_rows: int
    uniqueness_percentage: float
    is_unique: bool


class CompositeKeyRecommendations(BaseModel):
    two_column: List[CompositeKeyRecommendation]
    three_column: List[CompositeKeyRecommendation]
    four_column: List[CompositeKeyRecommendation]


class LLMSuggestedCombos(BaseModel):
    two_column: List[List[str]]
    three_column: List[List[str]]
    four_column: List[List[str]]


class ValidationResults(BaseModel):
    two_column: List[ValidationResult]


# -----------------------------
# Enhanced Analysis
# -----------------------------
class EnhancedAnalysis(BaseModel):
    available: bool
    version: str
    table_context: TableContext
    primary_key_recommendations: List[PrimaryKeyRecommendation]
    composite_key_recommendations: CompositeKeyRecommendations
    llm_suggested_combos: LLMSuggestedCombos
    validation_results: ValidationResults
    enhanced_recommendations: List[str]


# -----------------------------
# Full Result Item
# -----------------------------
class ToolResultItem(BaseModel):
    table_reference: str
    analysis_type: str
    processing_mode: str
    status: str
    data_quality_score: DataQualityScore
    recommendations: List[str]
    table_summary: TableSummary
    column_analysis: Dict[str, ColumnAnalysisItem]
    default_value_analysis: Dict[str, DefaultValueItem]
    enhanced_analysis: EnhancedAnalysis


class ToolResponse(BaseModel):
    result: List[ToolResultItem]




