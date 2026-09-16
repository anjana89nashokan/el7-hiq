"""
Core data models for the metadata-driven mapping POC.

This file defines:

1) SharedState
   - The "envelope" object passed from Step 1 (metadata ingestion)
     into Step 2 (mapping logic). It contains all parsed metadata
     and instructions for a single run_id / interface_code.

2) SourceSchema
   - A normalized, machine-friendly representation of source file
     metadata (IndeMap exports, profiling outputs, etc.).

3) TargetSchema
   - A normalized representation of target table metadata (IBX / DART
     style Excel files, e.g., PRV_DATA / PRV_MAP).

4) DataModelGraph
   - A lightweight graph abstraction of relationships between
     source files and target tables. In the PoC, this is "excel_only"
     and mostly nodes, with minimal or no edges. Later, ERwin can be
     used to populate true PK/FK relationships.

5) MappingContext
   - A structured representation of business rules, BRD instructions,
     overrides, and global filters that steer the mapping engine.

All of these are Pydantic models so they can be validated, logged,
serialized as JSON, and passed between agents (main agent / subagents).

NOTE:
- Step 1 (MetadataIngestionMainAgent + subagents) is responsible for
  building these models.
- Step 2 (MappingLogicAgent + JoinAndFilterAgent + others) is the
  consumer, using this metadata to generate the "green columns"
  of the mapping template.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# -------------------------------------------------------------------
# 0. Shared RuleType enum (used from Step 1 & Step 2)
# -------------------------------------------------------------------


class RuleType(str, Enum):
    """
    High-level classification of how a target column is populated.

    Values:
        DIRECT_MOVE
            Target column is populated directly from a source column
            (possibly after joining multiple source files), without
            using a DART lookup table.

        SK_CREATION
            Target column is a surrogate key (typically name ends with
            "_SK"). A new key is generated based on a combination of
            natural keys (alternate key groups) from the source.

        LOOKUP
            Target column is populated by joining to an existing DART
            table / reference table and retrieving the value from there.

        DEFAULT_HARDCODE
            Target column is populated using a constant / hardcoded
            value for all rows (or all rows that meet a condition).

        SYSTEM_GENERATED
            Target column is populated by the ETL framework itself at
            runtime (e.g., timestamps, load audit fields), not by
            explicit mapping logic.
    """

    DIRECT_MOVE = "DIRECT_MOVE"
    SK_CREATION = "SK_CREATION"
    LOOKUP = "LOOKUP"
    DEFAULT_HARDCODE = "DEFAULT_HARDCODE"
    SYSTEM_GENERATED = "SYSTEM_GENERATED"


# -------------------------------------------------------------------
# 1. SharedState – what Step 1 outputs and Step 2 consumes
# -------------------------------------------------------------------


class SharedState(BaseModel):
    """
    Top-level object representing the state for a single mapping run.

    This is the main "contract" between:
      - Step 1: Metadata ingestion / parsing
      - Step 2: Mapping logic and join/filter/transform generation

    It bundles all normalized metadata and instructions that the
    mapping agents need to work with.
    """

    # Unique identifier for this run across the system.
    run_id: str

    # The business interface code (e.g., "PRV_MAP_001") that identifies
    # the mapping / interface being processed.
    interface_code: str

    # Parsed source metadata (files, columns, datatypes, AKs).
    source_schema: "SourceSchema"

    # Parsed target metadata (tables, columns, PK/AKs, SCD hints).
    target_schema: "TargetSchema"

    # Graph structure of entities (source files, target tables, reference
    # tables) and relationships (PK/FK, lookup, lineage). For the PoC,
    # this is mostly nodes and empty edges, with ERwin support later.
    data_model_graph: "DataModelGraph"

    # Parsed instructions from BRD / prompts / mapping docs: selected
    # sources & targets, global filters, overrides, unresolved references.
    mapping_context: "MappingContext"

    # Optional selected subject area when Step 1 loads an ERwin subject-area graph artifact.
    # This supports auditability for downstream steps (which graph snapshot was used).
    graph_subject_area: Optional[str] = None

    # Selected subject areas for this run when one or more ERwin subject-area graphs are used.
    graph_subject_areas: List[str] = Field(default_factory=list)

    # Optional filesystem path to the graph artifact used in this run (if any).
    # This is persisted for traceability/replay and does not alter mapping behavior by itself.
    graph_artifact_path: Optional[str] = None

    # When this SharedState was created (e.g., when Step 1 completed).
    created_at: datetime

    # Who/what created this SharedState (e.g., main ingestion agent).
    created_by: str = "metadata_ingestion_agent"


# -------------------------------------------------------------------
# 2. Source Schema
# -------------------------------------------------------------------


class SourceColumnProfiling(BaseModel):
    """
    Optional profiling statistics for a single source column.

    This is derived from data profiling tools / scripts and can be used
    by Step 2 for additional confidence or rule hints (e.g., pattern
    checks, value ranges). All fields are optional.
    """

    # Number of distinct values observed in the column.
    distinct_count: Optional[int] = None

    # Fraction of rows where value is NULL (0.0 = no nulls, 1.0 = all null).
    null_fraction: Optional[float] = None

    # Minimum observed value (string representation).
    min_value: Optional[str] = None

    # Maximum observed value (string representation).
    max_value: Optional[str] = None

    # Example sample values from the data (string representations).
    sample_values: Optional[List[str]] = None

    # Pattern describing the data (optional), e.g.:
    #   - '^[0-9]{9}$' for a 9-digit ID
    #   - 'YYYY-MM-DD' for a date format
    pattern: Optional[str] = None


class SourceColumn(BaseModel):
    """
    Represents a single column/field in a source file.

    This is derived from the IndeMap source metadata template
    (column metadata tabs) and normalized into a consistent format.
    """

    # Physical column name in the file (e.g., "Id", "TaxId", "SRC_PRV_ID").
    physical_name: str

    # Optional logical/business name if provided in metadata.
    logical_name: Optional[str] = None

    # Human-readable description from the metadata, if any.
    description: Optional[str] = None

    # Normalized data type (e.g., "STRING", "INTEGER", "DECIMAL", etc.).
    # The parser is responsible for mapping IndeMap types to this enum.
    data_type: str

    # Maximum allowed length for character types (e.g., VARCHAR length).
    # For numeric types, this may be unused.
    length: Optional[int] = None

    # Total number of digits allowed (precision) for DECIMAL/NUMERIC types.
    # For non-numeric types, this may be None.
    precision: Optional[int] = None

    # Whether the column is nullable according to the metadata.
    nullable: bool = True

    # Default value at the source, if any is specified in metadata.
    # This is not the mapping default; it describes the raw file.
    default_value: Optional[str] = None

    # True if this column is part of the primary key (from IndeMap flags).
    is_primary_key: bool = False

    # Names of alternate key groups this column belongs to, e.g.:
    #   ["AK1"] or ["AK1", "AK2"] if it participates in multiple AKs.
    # The AlternateKeyGroup definitions live on SourceFile.
    alternate_key_groups: List[str] = []

    # Optional profiling statistics for this column (if profiling was run).
    profiling: Optional[SourceColumnProfiling] = None


class AlternateKeyGroup(BaseModel):
    """
    Logical grouping of columns that form an alternate/natural key.

    Example:
        name = "AK1"
        column_names = ["SRC_PRV_ID", "PRV_DSGNTN_CD"]

    These groups are very important for SK Creation rules, as they define
    which combination of columns identifies a natural key in the source.
    """

    # Short label, e.g. "AK1", "AK2".
    name: str

    # The physical column names that make up this alternate key.
    column_names: List[str]

    # Optional description or notes, possibly from BRD or IndeMap.
    description: Optional[str] = None


class SourceFile(BaseModel):
    """
    Represents a single source file (e.g., one IndeMap interface file).

    A SourceFile groups together:
      - file-level properties (name, type, encoding)
      - the list of its columns
      - primary key and alternate key definitions
    """

    # Stable internal ID for this file, used throughout the system
    # (e.g., "ACCOUNT_IDENTIFIER").
    file_id: str

    # Physical file name, e.g., "Account_Identifier.txt" or the
    # configured name used by ETL.
    file_name: str

    # Optional logical name for the file (e.g., "Account Identifier File").
    logical_name: Optional[str] = None

    # Human-readable description of the file's content/purpose.
    description: Optional[str] = None

    # Interface code to which this file belongs (e.g., "PRV_MAP_001").
    interface_code: str

    # File type for ingestion/metadata purposes:
    #   - DELIMITED: typical CSV/pipe-delimited files
    #   - FIXED_WIDTH: fixed-length record files
    #   - JSON / XML: hierarchical formats
    #   - OTHER: any other format
    file_type: Literal["DELIMITED", "FIXED_WIDTH", "JSON", "XML", "OTHER"] = "DELIMITED"

    # Delimiter used for DELIMITED files (e.g., ",", "|", "\t").
    delimiter: Optional[str] = None

    # Text encoding, e.g., "UTF-8", "ISO-8859-1".
    encoding: Optional[str] = None

    # List of columns parsed from IndeMap column metadata tabs.
    columns: List[SourceColumn]

    # Primary key definition at the file level:
    # list of column physical_names that form the PK.
    primary_key: List[str] = []

    # Alternate key groups for this file, referencing the same
    # AlternateKeyGroup model used by source/target schemas.
    alternate_keys: List[AlternateKeyGroup] = []

    # Optional name/identifier of the upstream source system
    # (e.g., "OPTUM", "EPIC"), if known.
    source_system: Optional[str] = None

    # Optional domain classification (e.g., "PROVIDER", "MEMBER").
    domain: Optional[str] = None


class SourceSchema(BaseModel):
    """
    Top-level collection of all source files for a given interface.

    This is the normalized structure that Step 2 uses when trying to
    find candidate source fields, evaluate type compatibility, and
    generate mapping rules.
    """

    # Business interface code this source schema belongs to.
    interface_code: str

    # All files that are in scope for this interface.
    files: List[SourceFile]

    # Convenience map from file_id → SourceFile, to avoid scanning `files`
    # every time. This can be populated explicitly by the parser or
    # lazily at runtime.
    by_file_id: Dict[str, SourceFile] = Field(default_factory=dict, exclude=True)


# -------------------------------------------------------------------
# 3. Target Schema
# -------------------------------------------------------------------


class SCDHints(BaseModel):
    """
    Slowly Changing Dimension (SCD) / CDC hints for a target table.

    These are derived from target metadata and/or BRD instructions,
    not from ERwin (for now).

    They help Step 2 decide:
      - whether the table behaves like a Type 1 or Type 2 dimension
      - which columns are technical SCD fields (e.g., ROW_EFF_DT).
    """

    scd_type_candidate: Literal["NONE", "TYPE_1", "TYPE_2"] = "NONE"
    eff_dt_column: Optional[str] = None
    exp_dt_column: Optional[str] = None
    current_flag_column: Optional[str] = None
    cdc_indicator: Optional[str] = None
    system_generated_columns: List[str] = []


class TargetColumn(BaseModel):
    """
    Represents a single column in a target table (e.g. PRV_DATA, PRV_MAP).

    This is parsed from the target metadata Excel and must reflect the
    IBX / DART standards exactly (no extra/hallucinated columns).
    """

    attribute_name: str  # Physical name, e.g., "AEDW_PRV_SK".
    logical_attribute_name: Optional[str] = None
    attribute_description: Optional[str] = None
    data_type: str
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    format: Optional[str] = None
    nullability: bool = True
    default_value: Optional[str] = None
    order_no: Optional[int] = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    alternate_key_groups: List[str] = []  # e.g., ["AK1", "AK2"]
    fk_reference_table: Optional[str] = None
    fk_reference_column: Optional[str] = None
    is_surrogate_key: bool = False  # name ends with "_SK"
    is_code_column: bool = False  # name ends with "_CD"
    is_technical: bool = False  # audit/SCD/system column (ETL-managed)


class TargetTable(BaseModel):
    """
    Represents a single target table.

    Example: PRV_DATA, PRV_MAP, or other DART/IBX tables.
    """

    table_id: str  # internal identifier, often the physical table name

    # "Database" for review display (from target metadata "Entity Data Set"/"Entity Database/Data Set"),
    # e.g., DB_AEDWP1. This is distinct from `database_name`, which is typically a project/server label.
    server_name: Optional[str] = None
    database: Optional[str] = None
    database_name: Optional[str] = None
    schema_name: Optional[str] = None
    table_name: str
    logical_name: Optional[str] = None
    business_name: Optional[str] = None
    description: Optional[str] = None
    workstream: Optional[str] = None
    table_type: Optional[str] = None  # e.g., DIMENSION/FACT/REF
    columns: List[TargetColumn]
    primary_key: List[str] = []
    alternate_keys: List[AlternateKeyGroup] = []
    scd_hints: Optional[SCDHints] = None


class TargetSchema(BaseModel):
    """
    Top-level collection of all target tables for a given interface.

    This is the main reference for Step 2 when iterating over target
    columns and generating their mapping rules and green columns.
    """

    interface_code: str
    tables: List[TargetTable]
    by_table_id: Dict[str, TargetTable] = Field(default_factory=dict, exclude=True)


# -------------------------------------------------------------------
# 4. Data Model Graph (Excel-only for PoC, ERwin-capable later)
# -------------------------------------------------------------------


class GraphNode(BaseModel):
    """
    A node in the data model graph.

    For the PoC:
      - Nodes are typically source files and target tables.
      - Later, reference tables or other entities can be added.
    """

    node_id: str  # e.g., "SRC:ACCOUNT_IDENTIFIER", "TGT:PRV_DATA"
    label: str  # human-readable label
    node_type: Literal["SOURCE_FILE", "TARGET_TABLE", "REF_TABLE"]
    # Optional ERwin extraction fields (kept optional for backward compatibility).
    database_name: Optional[str] = None
    table_name: Optional[str] = None
    # Lightweight table-column context for subject-area graph use.
    columns: List[str] = Field(default_factory=list)
    # True when the node was synthesized because an FK parent was referenced but
    # not present in the subject-area extract.
    is_stub: bool = False
    # Subject areas that contributed this node in a merged graph.
    provenance_subject_areas: List[str] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """
    A directed edge between two nodes in the data model graph.

    Represents relationships (FK, lookup, lineage, etc.). For the PoC,
    edges may be empty or minimal (EXCEL_ONLY_POC mode).
    """

    edge_id: str
    from_node_id: str
    to_node_id: str
    relationship_type: Literal["FK", "DERIVED_FROM", "LOOKUP", "OTHER"] = "FK"
    from_columns: List[str] = []
    to_columns: List[str] = []
    cardinality: Optional[Literal["1-1", "1-N", "N-1", "N-N"]] = None
    source: Literal["EXCEL_ONLY_POC", "ERWIN", "MANUAL_OVERRIDE"] = "EXCEL_ONLY_POC"
    comment: Optional[str] = None


class GraphMetadata(BaseModel):
    """
    Metadata describing the entire DataModelGraph.
    """

    graph_mode: Literal["excel_only_poc", "erwin_full", "erwin_subject_area_extract"] = "excel_only_poc"
    has_erwin: bool = False
    interface_code: str
    created_at: datetime
    # Optional runtime metadata for subject-area graph artifacts.
    run_id: Optional[str] = None
    subject_area: Optional[str] = None
    selected_subject_areas: List[str] = Field(default_factory=list)
    source_graph_artifact_paths: List[str] = Field(default_factory=list)
    merge_warnings: List[Dict[str, object]] = Field(default_factory=list)
    source_files: List[Dict[str, str]] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)


class DataModelGraph(BaseModel):
    """
    The data model graph tying together sources, targets, and references.

    For the PoC:
      - nodes: all SourceFile and TargetTable entities
      - edges: empty or minimal, unless explicit FK info exists
    """

    nodes: List[GraphNode]
    edges: List[GraphEdge]
    metadata: GraphMetadata
    # Optional extensions for ERwin subject-area artifacts (safe defaults so
    # existing Step 1/Step 2 paths remain compatible).
    sk_generators: Dict[str, Dict[str, List[str]]] = Field(default_factory=dict)
    sk_generator_origins: Dict[str, Dict[str, Dict[str, object]]] = Field(default_factory=dict)
    warnings: List[Dict[str, object]] = Field(default_factory=list)
    missing_refs: List[Dict[str, object]] = Field(default_factory=list)


# -------------------------------------------------------------------
# 5. Mapping Context (bridge between BRD / prompts and mapping logic)
# -------------------------------------------------------------------


class EntityRef(BaseModel):
    """
    Reference to a logical entity in the mapping context.

    entity_type:
      - "SOURCE_FILE": refers to SourceFile.file_id
      - "TARGET_TABLE": refers to TargetTable.table_id
    """

    entity_type: Literal["SOURCE_FILE", "TARGET_TABLE"]
    entity_id: str  # e.g. "ACCOUNT_IDENTIFIER", "PRV_DATA"


class ColumnRef(BaseModel):
    """
    Reference to a specific column within a given entity.

    Used for overrides (ignore fields, lookups, defaults, etc.).
    """

    entity_type: Literal["SOURCE_FILE", "TARGET_TABLE"]
    entity_id: str
    column_name: str


class ExplicitMapping(BaseModel):
    """
    High-level hint that a given source entity feeds a given target entity.
    """

    source: EntityRef
    target: EntityRef
    description: Optional[str] = None
    priority: Optional[int] = None


class LookupRule(BaseModel):
    """
    Direct instruction that a target column is populated via lookup
    against a specific table, with known join columns.
    """

    target_column: ColumnRef
    lookup_table: EntityRef
    source_join_columns: List[str]
    lookup_join_columns: List[str]
    description: Optional[str] = None


class DefaultRule(BaseModel):
    """
    Instruction that a target column should be populated with a
    constant (default/hardcoded) value, possibly under a condition.
    """

    target_column: ColumnRef
    default_value: str
    condition_text: Optional[str] = None


class CompositeKeyRule(BaseModel):
    """
    Instruction defining a composite / natural key based on multiple columns.

    Closely related to SK Creation rules (SK derived from natural key).
    """

    entity: EntityRef
    key_name: str
    column_names: List[str]
    description: Optional[str] = None


class RuleTypeOverride(BaseModel):
    """
    Explicit override of the rule type for a specific target column.
    """

    target_column: ColumnRef
    forced_rule_type: RuleType
    reason: Optional[str] = None
    source: Literal["BRD", "PROMPT", "MANUAL"] = "BRD"


class Overrides(BaseModel):
    """
    Container for all override-style instructions extracted from BRD and prompts.
    """

    ignore_fields: List[ColumnRef] = []
    lookup_rules: List[LookupRule] = []
    default_rules: List[DefaultRule] = []
    composite_key_rules: List[CompositeKeyRule] = []
    rule_type_overrides: List[RuleTypeOverride] = []


class GlobalFilter(BaseModel):
    """
    A filter condition that applies at mapping, table, or column scope.
    """

    scope: Literal["MAPPING", "TABLE", "COLUMN"] = "MAPPING"
    target_table_id: Optional[str] = None
    target_column_name: Optional[str] = None
    description: Optional[str] = None
    expression_text: str
    source: Literal["BRD", "PROMPT", "MANUAL"] = "BRD"


class SCDOverride(BaseModel):
    """
    When the BRD explicitly states SCD behavior that conflicts with heuristics.
    """

    target_table_id: str
    scd_type: Literal["NONE", "TYPE_1", "TYPE_2"]
    notes: Optional[str] = None


class UnresolvedReference(BaseModel):
    """
    Represents a reference found in BRD/prompts that could not be resolved.
    """

    raw_text: str
    reason: Literal["UNKNOWN_TABLE", "UNKNOWN_COLUMN", "AMBIGUOUS_ENTITY"]
    severity: Literal["INFO", "WARN", "ERROR"] = "WARN"
    suggested_action: Optional[str] = None


class MappingContext(BaseModel):
    """
    Top-level structure representing all business-side mapping
    instructions and overrides for a given interface.
    """

    interface_code: str
    selected_sources: List[str]  # SourceFile.file_id
    selected_targets: List[str]  # TargetTable.table_id
    explicit_mappings: List[ExplicitMapping] = []
    overrides: Overrides = Overrides()
    global_filters: List[GlobalFilter] = []
    scd_overrides: List[SCDOverride] = []
    unresolved_references: List[UnresolvedReference] = []
    notes: Optional[str] = None
