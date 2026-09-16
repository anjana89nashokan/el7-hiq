from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import datetime


from enum import Enum
from pydantic import BaseModel
 
class AgentType(str, Enum):
    DATA_DICTIONARY_UPDATE = "data_dictionary"
    METADATA_FILL_UPDATE = "metadata_fill"
    DATA_PROFILING= "profiling"
    DATA_ANOMALY_ANALYSIS ="anomaly"


class AgentTypeLarge(str, Enum):
    PROFILING_HITL = "profiling"

    
class HumanInLoopLargeRequest(BaseModel):
    user_id: str
    session_id: str
    app_name: str
    agent_type: AgentTypeLarge
    user_message: str


class ProfilingChatHITLRequest(BaseModel):
    user_id: str
    session_id: str
    app_name: str
    user_message: str
    is_edit: bool = False


class SimilarityChatHITLRequest(BaseModel):
    user_id: str
    session_id: str
    app_name: str
    user_message: str
    apply_changes: bool = False
    text_response: Optional[str] = None
    tool_response: Optional[Dict[str, Any]] = None
 
class HumanInLoopRequest(BaseModel):
    user_id: str
    session_id: str
    app_name: str
    agent_type: AgentType
    user_message: str
 
class HumanInLoopResponse(BaseModel):
    session_id: str
    response: str


# Response models
class FileUploadResponse(BaseModel):
    sessionID: Optional[str|None]
    user: str
    createdDate: str
    lastUpdateDate: str
    file_id: str
    filename: str
    table_name: str
    dataset_id: str
    project_id: str
    rows_uploaded: int
    upload_timestamp: str
    access_info: Dict[str, Any]
    initial_profiling_report: str
    profiling_report_url: str
    data_quality_score: Optional[Dict[str, Any]] = None

class BatchUploadResponse(BaseModel):
    total_files: int
    successful_uploads: List[FileUploadResponse]
    failed_uploads: List[Dict[str, str]]
    summary: Dict[str, int]
    brd_extraction_status: Optional[Dict[str, Any]] = None

class MessagePart(BaseModel):
    text: str

class Message(BaseModel):
    role: str
    parts: List[MessagePart]



class MessageRequest(BaseModel):
    appName: str
    sessionId: str
    userId: str
    newMessage: Message
    streaming: bool = False
    stateDelta: dict | None = None
    additional_data: dict | None = None
    dart_database_name: str | None = None
    filters: list | None = None


class QARequest(BaseModel):
    appName: str
    sessionId: str
    userId: str
    newMessage: str
    streaming: bool = False
    stateDelta: dict | None = None



class SessionCreateRequest(BaseModel):
    app_name: str
    user_id: str
    initial_state: dict = {}


class SessionModule(str, Enum):
    extract = "extract"
    sourcing = "sess"


class AppSessionCreateRequest(BaseModel):
    title: str | None = None


class AppSessionRenameRequest(BaseModel):
    title: str


class ProfilingRunStartRequest(BaseModel):
    force_new: bool = True


class ProfilingResumeStateRequest(BaseModel):
    status: str | None = None
    current_step: str | None = None
    resume_state: Dict[str, Any] = Field(default_factory=dict)
    profiling_context_uri: str | None = None


class MappingResumeStateRequest(BaseModel):
    status: str | None = None
    current_step: str | None = None
    resume_state: Dict[str, Any] = Field(default_factory=dict)


class ExtractResumeStateRequest(BaseModel):
    status: str | None = None
    current_step: str | None = None
    resume_state: Dict[str, Any] = Field(default_factory=dict)
    upload_session_id: str | None = None
    brd_gcs_uri: str | None = None
    layout_gcs_uri: str | None = None
    metadata_gcs_uri: str | None = None
    driver_gcs_uri: str | None = None


class MappingReviewDraftRequest(BaseModel):
    answers: Dict[str, Any] = Field(default_factory=dict)
    feedbacks: Dict[str, Any] = Field(default_factory=dict)
    changed_rows: List[Dict[str, Any]] = Field(default_factory=list)
    active_tab: str | None = None
    selected_row_id: str | None = None

    
class DocExtractionResponse(BaseModel):
    success: bool
    session_id: Optional[str] = None
    message: str
    gcs_prefix: Optional[str] = None
    artifacts_uploaded: List[str] = Field(default_factory=list)


class BrdExtractionResponse(BaseModel):
    success: bool
    session_id: str
    message: str
    artifacts_found: List[str] = Field(default_factory=list)
    brd_filename: Optional[str] = None
    file_layout_filename: Optional[str] = None
    transcript_filename: Optional[str] = None
    bsa_notes: Optional[str] = None
    markdown_uploads: List[str] = Field(default_factory=list)
    requirement_layer: dict
    gcs_output_uri: str

class RequirementLayerOutput(BaseModel):
    scope_in: List[Dict[str, Any]] = Field(default_factory=list)
    scope_out: List[Dict[str, Any]] = Field(default_factory=list)
    bsa_inputs: List[Dict[str, Any]] = Field(default_factory=list)
    requirements: List[Dict[str, Any]] = Field(default_factory=list)
    file_specs: Dict[str, Any] = Field(default_factory=dict)
    common_rules: Dict[str, Any] = Field(default_factory=dict)
    unresolved_fields: List[str] = Field(default_factory=list)
    extraction_confidence: Dict[str, Any] = Field(default_factory=dict)


class BrdRequirementLayerResponse(BaseModel):
    success: bool
    session_id: str
    message: str
    gcs_output_uri: Optional[str] = None
    chunks_processed: int = 0
    requirement_layer: Optional[RequirementLayerOutput] = None


class ValidationResultResponse(BaseModel):
    success: bool
    session_id: str
    validation_status: str  # "completed" | "corrected" | "failed"
    corrections_made: bool
    gcs_output_uri: Optional[str] = None
    validated_requirement_layer: Optional[Dict[str, Any]] = None
    message: str


class FieldFeedback(BaseModel):
    field_path: str
    current_value: Any
    instruction: str
    comment: Optional[str] = Field(
        default=None,
        description="Human-readable note explaining why this field was rejected"
    )

class FieldCorrectionResult(BaseModel):
    field_path: str
    original_value: Any
    corrected_value: Any
    instruction: str
    comment: Optional[str] = None
    status: str  # "corrected" | "unchanged" | "failed"

class BrdAcceptRequest(BaseModel):
    accepted_edits: Dict[str, Any]

class BrdRejectFreeformRequest(BaseModel):
    instruction: str

class BrdCheckpointResponse(BaseModel):
    success: bool
    session_id: str
    message: str
    validated_requirement_layer: Dict[str, Any]
    gcs_output_uri: str
    # New: full audit trail
    accepted_fields_applied: List[str] = Field(default_factory=list)
    rejected_field_results: List[FieldCorrectionResult] = Field(default_factory=list)
    unchanged_fields: List[str] = Field(default_factory=list)



class FileLayoutCheckpointRequest(BaseModel):
    """Human checkpoint payload for file layout tables — direct edits only, no LLM re-run."""
    # Full or partial replacement of file_layout_tables keys
    edited_tables: Dict[str, Any] = Field(
        default_factory=dict,
        description="Table keys → updated row arrays to merge into the persisted file_layout_tables",
    )
    additional_instructions: Optional[str] = Field(
        default=None,
        description="Reserved for future use",
    )


class FileLayoutCheckpointResponse(BaseModel):
    success: bool
    session_id: str
    message: str
    file_layout_tables: Dict[str, Any]
    gcs_output_uri: str


class MappingApproverRequest(BaseModel):
    """Payload for the mapping approver — carries UI-edited mapping data."""
    common_rules: List[Dict[str, str]] = Field(default_factory=list, description="List of {Field, Value} dicts")
    transformation_rules: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains target_entity, driver_table_required, history_data_pull, common_filter, and rows array"
    )


class MappingApproverResponse(BaseModel):
    success: bool
    session_id: str
    message: str
    gcs_output_uri: str
    common_rules: List[Dict[str, str]] = Field(default_factory=list)
    transformation_rules: Dict[str, Any] = Field(default_factory=dict)


class MappingFieldCheckpointRequest(BaseModel):
    """Payload to re-run mapping for one field using BSA feedback."""
    appName: str
    sessionId: str
    user_id: str
    target_attribute: str
    current_row: Dict[str, Any] = Field(
        default_factory=dict,
        description="The existing transformation_rules.rows item for this target attribute.",
    )
    bsa_instruction: str = Field(
        ...,
        description="BSA correction/guidance for this field.",
    )


class MappingFieldCheckpointResponse(BaseModel):
    success: bool
    session_id: str
    row: Dict[str, Any]


class FileLayoutExtractionResponse(BaseModel):
    success: bool
    session_id: str
    message: str
    file_layout_filename: str
    total_pages: int
    tables_extracted: int
    file_layout_tables: dict
    gcs_output_uri: str


class SmartColumnMatch(BaseModel):
    """Column match using agent reasoning"""
    rank: int = Field(description="Match ranking")
    source_file_name: str = Field(description="Original file name")
    source_table_name: str = Field(description="BigQuery table name")
    source_column_name: str = Field(description="Source column name")
    dart_table_name: str = Field(description="DART reference table")
    dart_column_name: str = Field(description="DART column name")
    similarity_score: float = Field(description="Similarity score 0-100")
    match_reasoning: str = Field(description="Why this is a match")
    data_sample_comparison: str = Field(description="Sample data comparison")
    confidence: str = Field(description="HIGH MEDIUM LOW")
    null_blank_percent: float = Field(description="Percent NULL blank")
    data_overlap_percent: float = Field(description="Percent data overlap")
    total_rows: int = Field(description="Total rows in source")
    overlap_count: int = Field(description="Matching values count")
    source_distinct_count: int = Field(description="Distinct source values")


class SmartSimilarityOutput(BaseModel):
    """Agent-driven similarity analysis output"""
    status: str = Field(description="Success or error")
    dart_references_analyzed: int = Field(description="Number of DART refs")
    source_tables_analyzed: int = Field(description="Number of source tables")
    matches: List[SmartColumnMatch] = Field(default=[], description="All matches")
    summary: Dict[str, Any] = Field(default={}, description="Summary stats")
    markdown_report: str = Field(default="", description="Formatted report")

