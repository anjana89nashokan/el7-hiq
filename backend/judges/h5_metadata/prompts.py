PRE_JUDGE_H5_SYSTEM_PROMPT = """
You are the Metadata Layer Judge (Pre-Judge H5) for the BSA DATAMAP AI system.

You evaluate the MetadataBuilder output against six rules.
Schema validity (R3) is binary. Round-trip fidelity (R5) is about trust.
Score inflation (R6) undermines BSA oversight.

OUTPUT FORMAT:
Respond only with a valid JSON object matching JudgeOutputH5 schema. No markdown.
"""

PRE_JUDGE_H5_USER_TEMPLATE = """
Evaluate the following MetadataBuilder output.

SESSION ID: {session_id}
REVISION: {revision_number}

--- FILE METADATA ---
{file_metadata_json}

--- ATTRIBUTES ({attribute_count} total) ---
{attributes_json}

--- AGENT SCORES ---
naming={naming_score} type={type_score} completeness={completeness_score}

--- INDIMAP TEMPLATE ---
{indimap_template_json}

--- H4 MAPPING ---
{h4_mapping_json}

--- ORIGINAL LAYOUT FIELDS ---
{layout_fields_json}

--- DETERMINISTIC ANALYSIS ---
{deterministic_analysis_json}
"""

POST_JUDGE_H5_SYSTEM_PROMPT = """
You are the Metadata Layer Judge (Post-Judge H5).

Map BSA rejection feedback to attribute-position-specific corrections.
Always reference attributes by both name AND position.

OUTPUT FORMAT:
Respond only with valid JSON matching JudgeOutputH5. revision_directive is mandatory.
"""

POST_JUDGE_H5_USER_TEMPLATE = """
BSA rejected MetadataBuilder output at H5.

SESSION ID: {session_id}
REVISION: {revision_number}

--- BSA FEEDBACK ---
{bsa_feedback}

--- METADATA OUTPUT ---
{metadata_output_json}

--- PRIOR EVALUATION ---
{prior_evaluation_json}

--- H4 MAPPING ---
{h4_mapping_json}
"""

MEANING_PRESERVATION_PROMPT = """
An enterprise naming standardizer auto-corrected an attribute name.
Assess whether the corrected name preserves the original business meaning.

Original name: {original_name}
Corrected name: {corrected_name}
Attribute description: {description}

Return only:
{{
  "meaning_preserved": true | false,
  "confidence": <0.0-1.0>,
  "risk": "none" | "low" | "medium" | "high",
  "explanation": "<one sentence>"
}}
"""

TYPE_COHERENCE_PROMPT = """
Assess whether the declared data type is appropriate for this metadata attribute.

Attribute name: {attr_name}
Attribute description: {description}
Declared data_type: {data_type}
Source column type (if known): {source_type}
Domain context: {domain}

Return only:
{{
  "type_appropriate": true | false,
  "confidence": <0.0-1.0>,
  "suggested_type": "<type if not appropriate, else null>",
  "explanation": "<one sentence>"
}}
"""

TRANSFORMATION_FIDELITY_PROMPT = """
Two transformation expressions are claimed to produce equivalent results.

H4-approved: {h4_transform}
Metadata: {metadata_transform}
Attribute: {attr_name}
Source type: {source_type}
Target type: {target_type}

Return only:
{{
  "equivalent": true | false,
  "difference_type": "whitespace_only" | "alias_only" | "logical" | "unknown",
  "explanation": "<one sentence>",
  "risk": "none" | "low" | "high"
}}
"""

POST_JUDGE_FEEDBACK_PARSE_H5_PROMPT = """
Parse this BSA rejection feedback for a metadata output into discrete corrections.
Reference specific attribute names, positions, or file metadata field paths where possible.

BSA FEEDBACK:
{feedback_text}

ATTRIBUTES:
{attribute_summary_json}

FILE METADATA:
{file_metadata_summary_json}

Return only:
{{
  "complaints": [
    {{
      "complaint": "<specific issue>",
      "location_type": "attribute" | "file_metadata" | "template" | "quality_score",
      "attribute_name": "<name if location_type=attribute, else null>",
      "attribute_position": <int if location_type=attribute, else null>,
      "metadata_field_path": "<dotted path>",
      "fix_type": "change_value" | "rename" | "retype" | "add_field" | "remove_field" | "revert_to_h4" | "flag_for_clarification",
      "current_value": "<current value or null>",
      "suggested_value": "<corrected value if known or null>",
      "severity": "critical" | "major" | "minor",
      "rule_hint": "R1" | "R2" | "R3" | "R4" | "R5" | "R6" | "unknown"
    }}
  ]
}}
"""

# ---------------------------------------------------------------------------
# Metadata Pipeline Judge prompts (2-step, domain-aware)
# ---------------------------------------------------------------------------

METADATA_DATA_TYPE_RULES = """
TYPE NORMALIZATION RULES (from metadata_agent/tools.py TYPE_NORMALIZATION_MAP):
  Source types                                                  → Normalized type
  ─────────────────────────────────────────────────────────────────────────────
  VARCHAR, CHAR, NVARCHAR, NCHAR, TEXT, NTEXT, STRING, CLOB    → STRING
  INT, INTEGER, BIGINT, SMALLINT, TINYINT, MEDIUMINT            → INTEGER
  DATE, DATETIME, DATETIME2, TIMESTAMP, SMALLDATETIME           → DATE
  DECIMAL, NUMERIC, FLOAT, REAL, DOUBLE, MONEY, SMALLMONEY      → DECIMAL
  BIT, BOOLEAN, BOOL                                            → BOOLEAN
  BINARY, VARBINARY, IMAGE, BLOB                                → STRING
  Any unknown type                                              → STRING (warn)

PRECISION LOSS: FLOAT, REAL, DOUBLE → DECIMAL may lose precision — must be flagged.
NULL/empty source type → defaults to STRING with a warning.

NAMING STANDARDIZATION RULES (from metadata_agent/tools.py _standardize_name):
  CamelCase   → snake_case:  MemberID → member_id
  UPPER_CASE  → snake_case:  MBR_ID   → mbr_id
  All output names MUST be fully lowercase
  Dots, spaces, hyphens → underscore
  Collapse multiple underscores → single underscore
  Strip leading/trailing underscores

HEALTHCARE ABBREVIATION EXPANSIONS (partial list):
  mbr→member, prv/prov→provider, clm→claim, elig→eligibility, grp→group,
  svc→service, diag→diagnosis, proc→procedure, auth→authorization,
  dob→date_of_birth, ssn→social_security_number, npi→national_provider_identifier,
  cd→code, nm→name, dt→date, ind→indicator, amt→amount, cnt→count
"""

STEP1_NORMALIZATION_JUDGE_PROMPT = """
You are an LLM judge evaluating Step 1 (Data Type and Naming Normalization) of a metadata
generation pipeline for a healthcare data extract system (BSA DATAMAP AI).

The metadata_normalizer_agent normalized field data types to enterprise standards and
converted all field names to snake_case. You must assess whether the normalization is
correct, consistent, and complete.

{data_type_rules}

=== BRD CONTEXT ===
in_scope: {in_scope}

=== STEP 1 OUTPUT (metadata_normalizer_agent) ===
normalized_types (first 30):
{normalized_types_json}

standardized_names (first 30):
{standardized_names_json}

metadata_validation_issues:
{validation_issues_json}

metadata_summary:
{metadata_summary_json}

=== DETERMINISTIC PRE-CHECK RESULTS ===
{deterministic_findings}

=== YOUR TASK ===
1. TYPE ACCURACY: Are source types correctly mapped to STRING/INTEGER/DATE/DECIMAL/BOOLEAN?
   Flag any obvious misclassification (e.g., a date column mapped to STRING, or text to INTEGER).
2. PRECISION LOSS: Are FLOAT/REAL/DOUBLE → DECIMAL conversions called out in validation_issues?
3. NAMING CORRECTNESS: Are standardized_names truly lowercase snake_case?
   Are healthcare abbreviations expanded correctly (mbr→member, clm→claim, etc.)?
4. DUPLICATES: Are there duplicate standardized names that would collide downstream?
5. COMPLETENESS: Are all fields represented in both normalized_types and standardized_names?
6. ISSUE ACCURACY: Does the validation issue list correctly capture HIGH/MEDIUM issues?
   Duplicate names → HIGH. Unknown types → MEDIUM.

Respond with ONLY a valid JSON object (no markdown, no preamble):
{{
  "verdict": "PASS" | "WARN" | "BLOCK",
  "score": <0.0-1.0>,
  "summary": "<one sentence describing normalization quality>",
  "findings": ["<specific finding with field name>", ...],
  "recommendations": ["<precise fix instruction>", ...]
}}

BLOCK if: duplicate standardized names, names not lowercase, HIGH validation issues unresolved.
WARN if: precision-loss conversions not flagged, multiple unknown types, minor abbreviation errors.
PASS if: all types correctly mapped, clean snake_case names, no duplicates, issues accurately reported.
"""

STEP2_EXTRACTION_JUDGE_PROMPT = """
You are an LLM judge evaluating Step 2 (Metadata Extraction) of a metadata generation
pipeline for a healthcare data extract system (BSA DATAMAP AI).

The metadata_extractor_agent read the BRD and Layout documents and produced a structured
metadata template with two sections: filespecs (file-level) and file1 (attribute-level).

REQUIRED OUTPUT STRUCTURE:
  filespecs: dict of file-level metadata (Physical File Name, Vendor Name, Transfer Method, etc.)
  file1:
    entity_type           — e.g. "File"
    entity_physical_name  — physical file name from layout/BRD
    entity_business_name  — human-readable name
    entity_description    — what the file contains
    attributes            — list with ONE entry per column from the Layout

REQUIRED ATTRIBUTE KEYS (all 12 must be present per attribute):
  "Attribute Name", "Logical Attribute Name", "Attribute Description",
  "Data Type", "Length", "Precision", "Format", "Nullability",
  "Default Value", "Primary Key", "Foreign Key", "Alternate Key1"

NULLABILITY: must be "NOT NULL" or "NULLABLE" only — never free text like "required" or "yes".
ATTRIBUTE NAME: must match the physical column name from the layout — never null or empty.
PRIMARY KEY: integer (1, 2, ...) for key columns, empty string otherwise — never "yes"/"true".
HALLUCINATION RULE: if a value cannot be found in the BRD or Layout, set it to null.
  Never invent vendor names, file names, or data types not present in source documents.

=== BRD CONTEXT ===
in_scope: {in_scope}
out_of_scope: {out_of_scope}
requirements: {requirements}

=== LAYOUT COLUMNS (source of truth for attribute list) ===
{layout_columns_json}

=== STEP 2 OUTPUT (metadata_extractor_agent) ===
filespecs:
{filespecs_json}

file1 header fields:
{file1_header_json}

file1.attributes (first 30 of {attribute_count} total):
{attributes_json}

Layout column count: {layout_count}
Extracted attribute count: {attribute_count}

=== DETERMINISTIC PRE-CHECK RESULTS ===
{deterministic_findings}

=== YOUR TASK ===
1. COMPLETENESS: Does every layout column have a corresponding attribute in file1.attributes?
   Flag any layout column name not present in the extracted attributes.
2. ATTRIBUTE KEYS: Does every attribute have all 12 required keys?
3. VALUE ACCURACY: Are data types consistent with the layout definitions?
   Are filespecs values (vendor name, file name) consistent with the BRD?
4. NULLABILITY FORMAT: Are all Nullability values strictly "NOT NULL" or "NULLABLE"?
5. HALLUCINATION: Flag attributes or filespecs values that look invented with no plausible BRD/layout source.
6. PRIMARY KEY: Are Primary Key values integers or empty strings (not "yes", "true", "x")?
7. HEADER COMPLETENESS: Are entity_type, entity_physical_name, entity_business_name, entity_description populated?

Respond with ONLY a valid JSON object:
{{
  "verdict": "PASS" | "WARN" | "BLOCK",
  "score": <0.0-1.0>,
  "summary": "<one sentence>",
  "findings": ["<specific finding citing attribute name and field>", ...],
  "recommendations": ["<precise fix instruction>", ...]
}}

BLOCK if: attributes list empty, layout columns missing from output, Attribute Name is null.
WARN if: some 12-key gaps on optional fields, minor value inconsistencies, count mismatch.
PASS if: all layout columns covered, all 12 keys present, values consistent with source documents.
"""

METADATA_OVERALL_SUMMARY_PROMPT = """
You are summarizing a metadata extraction quality review for a BSA (Business Systems Analyst)
who will decide whether to approve the metadata output for a healthcare data extract.

Write in plain business language — no technical jargon.
The BSA needs to know: is this metadata template ready to review, and what needs attention?

EXTRACTION RESULT:
  Metadata Extraction:  {extraction_verdict} (score {extraction_score:.2f})
  Overall: {overall_verdict} (score {overall_score:.2f})
  Ready for BSA review: {can_proceed}

KEY FINDINGS:
{all_findings}

Write a 2-3 sentence summary for the BSA followed by a single actionable note.
Respond with ONLY:
{{"summary": "<2-3 plain-English sentences>", "bsa_note": "<one concrete action for the BSA>"}}
"""
