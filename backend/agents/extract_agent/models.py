from pydantic import BaseModel, Field
from typing import Optional

# --- BRD Parser ---
class FieldInstruction(BaseModel):
    field_name: str
    instruction: str

class DateCriteria(BaseModel):
    description: str
    field_type: Optional[str] = None

class ParsedBrd(BaseModel):
    in_scope_items: list[str] = Field(default_factory=list)
    out_of_scope_items: list[str] = Field(default_factory=list)
    date_criteria: list[DateCriteria] = Field(default_factory=list)
    eligibility_criteria: list[str] = Field(default_factory=list)
    field_level_instructions: list[FieldInstruction] = Field(default_factory=list)
    skipped_tbd_items: list[str] = Field(default_factory=list)

# --- Layout Parser ---
class LayoutField(BaseModel):
    sequence: int
    attribute_name: str
    normalized_name: str
    data_type: Optional[str] = None
    length: Optional[str] = None
    format: Optional[str] = None
    nullability: Optional[str] = None
    is_key: bool = False

class ParsedLayout(BaseModel):
    source_file_name: str
    field_count: int
    fields: list[LayoutField] = Field(default_factory=list)

# --- Transcript ---
class TranscriptDecision(BaseModel):
    decision_text: str
    category: str  # e.g. "frequency", "scope", "format"
    source_session: Optional[str] = None

class ParsedTranscript(BaseModel):
    decisions: list[TranscriptDecision] = Field(default_factory=list)
    vendor_context: Optional[str] = None
    frequency_notes: Optional[str] = None

# --- Domain Classifier ---
class TaggedField(BaseModel):
    attribute_name: str
    domain: str  # member | provider | claim | eligibility | group | unknown
    domain_confidence: float

class DomainTaggedFields(BaseModel):
    tagged_fields: list[TaggedField] = Field(default_factory=list)
    domain_summary: dict[str, int] = Field(default_factory=dict)
    primary_domain: str = "unknown"

# --- Ambiguity Detector ---
class AmbiguityItem(BaseModel):
    severity: str  # HIGH | MEDIUM | LOW
    item_type: str  # missing | field_mismatch | conflict | other
    description: str
    recommended_action: Optional[str] = None
    resolved: bool = False
    bsa_resolution_note: Optional[str] = None

class AmbiguityReport(BaseModel):
    ambiguities: list[AmbiguityItem] = Field(default_factory=list)
    total_conflicts: int = 0
    total_missing: int = 0
    fields_in_layout_not_brd: list[str] = Field(default_factory=list)
    fields_in_brd_not_layout: list[str] = Field(default_factory=list)
    can_proceed: bool = True
