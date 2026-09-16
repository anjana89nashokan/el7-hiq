from typing import Any
import logging
from google import genai

from config.settings import config

from agents.extract_requirement_layer_agent.tools.brd_utils import _safe_json_load, _normalize_schema
from agents.extract_requirement_layer_agent.schema.brd_transcript_agent_response_schema import response_schema

logger = logging.getLogger(__name__)
_BRD_CHUNK_PROMPT = """\
You are a precise document conversion assistant.
Convert the content of this PDF chunk (pages {page_range}) to clean Markdown.

Rules:
- Preserve ALL text, headings, bullet points, numbered lists exactly as they appear.
- Render every table as a proper GitHub-flavoured Markdown table (| col | col |).
  - Do NOT skip any table rows or columns.
  - If a table continues from the previous chunk, continue rendering rows — do NOT re-emit the header.
- Use # / ## / ### for headings based on visual hierarchy.
- Do NOT add commentary, summaries, or any text not present in the document.
- Do NOT wrap the output in code fences.
- If the chunk contains no content, return an empty string.

Previous chunk ended with:
{handoff}
"""

_TRANSCRIPT_CHUNK_PROMPT = """\
You are a precise document conversion assistant.
Convert the content of this PDF chunk (pages {page_range}) to clean Markdown.

Rules:
- Preserve ALL spoken text, speaker labels, timestamps, and section headings.
- Render any tables as GitHub-flavoured Markdown tables.
- Do NOT add commentary or text not present in the document.
- Do NOT wrap the output in code fences.

Previous chunk ended with:
{handoff}
"""


def _extract_chunk_structured(
    client: genai.Client,
    chunk_markdown: str,
    running_state: dict[str, Any],
    chunk_index: int,
    bsa_input: str | None = None,
    cache_name: str | None = None,
) -> dict[str, Any]:
    """
    Extract structured fields from one markdown chunk.
    If cache_name is provided the BSA notes are already cached — the prompt
    references the cache instead of inlining the full BSA text.
    """
    bsa_provided = bool(bsa_input and bsa_input.strip())

    if cache_name:
        bsa_section = "(provided via cached context — use as search anchor)"
    elif bsa_provided:
        bsa_section = bsa_input
    else:
        bsa_section = "<empty>"

    if bsa_provided or cache_name:
        bsa_field_instruction = """\
bsa_input:
- Read through the MARKDOWN CHUNK below paragraph by paragraph.
- Identify ONLY the specific paragraphs, bullets, or sections where the BSA INPUT terms (or closely related concepts) are explicitly mentioned.
- If a paragraph does NOT mention the BSA INPUT terms, SKIP it entirely — do NOT include it.
- For each matching paragraph/section: extract it VERBATIM, preserving its original markdown formatting (headings, bullets, tables, etc.).
- Include the nearest preceding heading/section title above the matching paragraph so the context is clear.
- Concatenate only the matching paragraphs in the order they appear, separated by a blank line.
- Do NOT copy the BSA INPUT text itself into this field.
- Do NOT summarize, paraphrase, or merge unrelated paragraphs.
- If no paragraph in this chunk mentions the BSA INPUT terms, return an empty string.
- Do NOT include table-of-contents lines or page number artifacts.

EXAMPLE:
  BSA INPUT: "legislation enquiry"
  Document has 5 paragraphs — only paragraphs 2 and 4 mention "legislation enquiry".
  → Extract ONLY paragraphs 2 and 4 verbatim. Skip paragraphs 1, 3, and 5."""
    else:
        bsa_field_instruction = """\
bsa_input:
- BSA input is empty. Set this field to an empty string. Do NOT populate it."""

    prompt = f"""You are a strict BRD requirement extraction engine.

Your job is to extract structured information from BRD markdown chunks with HIGH PRECISION and ZERO HALLUCINATION.

---
BSA INPUT (highest-priority search anchor)
- Do NOT include any tables (e.g. file layout, field mapping, data dictionaries) unless they are explicitly named or referenced in the BSA input.
- Only extract tables when the BSA input directly calls for them by name or context.
---
{bsa_section}

Definition:
- This is the Business System Analyst (BSA) target section.
- It defines the intent and context of extraction.
- ALWAYS prioritize matching content relevant to this section.
- Use this as the PRIMARY anchor when deciding relevance and field population.

---

RUNNING STATE (never overwrite a non-empty value with a weaker/empty one)
---
{running_state}

Definition:
- Previously extracted values from earlier chunks.
- If a field already has a strong value, DO NOT overwrite it with:
  - empty string
  - partial value
  - less precise value
- Always MERGE intelligently — never degrade quality.

---

EXTRACTION INSTRUCTIONS (BSA-DRIVEN — DO NOT IGNORE)
---
{bsa_field_instruction}

Definition:
- These are dynamic extraction constraints tied to the BSA section.
- They OVERRIDE generic heuristics when conflicts arise.
- Follow them STRICTLY for:
  - prioritization
  - field selection
  - interpretation boundaries

---

FIELD DEFINITIONS + EXTRACTION RULES
---

scope: extract verbatim in_scope and out_of_scope content only — NO headers.

Definition:
- Captures explicit inclusion/exclusion boundaries of the system.

What to look for:
- Sections introduced by headings like:
  - "In Scope", "Out of Scope"
  - "Scope Includes", "Scope Excludes"
- Paragraphs, bullets, AND tables that appear UNDER those headings

CRITICAL FORMATTING RULE:
- ALWAYS preserve original markdown EXACTLY
- If tables are present → RETURN FULL TABLE in markdown
- NEVER flatten tables
- ALWAYS include surrounding descriptive text
- DO NOT include the heading line itself (e.g. "## In Scope", "## Out of Scope") in the extracted value
- The extracted value must start with the first content line AFTER the heading

Continuation Rule:
- If scope content spans multiple chunks → append using "..." only if truly continuous
- Do NOT artificially truncate or summarize

---

requirements: finalized business rules and numbered logic only. No raw tables.

Definition:
- Final business requirements governing system behavior

What to look for:
- Numbered rules
- “System shall…”, “Must…”, “Business rule…”

Do NOT include:
- tables
- drafts
- exploratory notes

Continuation Rule:
- Append logically across chunks using "..." if continuation is detected

---

filters_and_parameters — extract exact values present in the chunk; leave empty if absent:

Definition:
- Explicit filtering constraints used for data extraction

Fields:
  Company → organization name(s)
  Business → business unit
  State → geographic state
  Line of Business → e.g., Commercial, Medicare
  Financial Arrangement → funding type
  Product Plan Type → HMO, PPO, etc.
  Extended Product → sub-product classification
  Coverage Plan → plan identifier
  Customer ID → identifier
  Group ID → identifier
  Claim Status → paid/denied/pending
  Blue Card Indicator → yes/no
  Excluded Companies → explicitly excluded entities
  Excluded LOB → excluded lines of business
  Sensitive Data Exclusion → PHI/PII exclusions
  Opt Out Groups → excluded groups
  Member Active Enrollment → enrollment condition
  Active Plan Group → active group condition
  Start Date → extraction start
  History Lookback → historical window
  Rollover Period → carry-forward duration
  Claim Service Dates → service range
  Claim Posted Dates → posting range
  Paid Dates → payment range
  Pharmacy Fill Dates → Rx fill range
  Pharmacy Cut Dates → Rx cutoff

Rule:
- Extract EXACT values only
- No inference
- Preserve formatting if structured

---

file_attributes_mapping — extract exact values present in the chunk; leave empty if absent:

Definition:
- Logical file design and formatting configuration

Fields:
  File Count, Subject Areas, File Frequency, File Type, File Delimiter,
  File Naming Convention, File Compression, File Encryption,
  Control File Required, File Delivery Method, Field Headers,
  Trailer Required, Field Requirements, Default Values, Data Format Rules

---

file_specs — extract exact metadata present in the chunk; leave empty if absent:

Definition:
- Physical file + vendor + transfer-level metadata

Fields:
  physical_file_name, vendor_name, transfer_method, vendor_contact_name,
  frequency_mode, vendor_phone_number, dependencies, vendor_email,
  email_notification_dl, file_delimiter, file_extension,
  date_timestamp_format, header_record_number, trailer_record_number,
  quote_indicator, file_population_type, file_compression_type,
  receive_files_when_no_data, assumptions, vendor_server_name,
  vendor_file_drop_location, control_file_name, control_file_delimiter,
  control_file_extension, control_file_header_present,
  control_record_number, control_file_amount_column_count,
  done_file_present, file_arrival_schedule,
  estimated_record_count_initial, estimated_record_count_ongoing

---

common_rules — extract exact values present in the chunk; leave empty if absent:

Definition:
- General system-wide constraints and rules

Fields:
  interface_code, history_required, effective_dates_from, effective_dates_to,
  posted_dates_from, posted_dates_to, rolling_month_requirement,
  driver_required, incremental_history_required, runout_required,
  number_of_months, sensitive_category_list, deidentity_extract,
  comments, last_updated_date

---

FEW-SHOT EXAMPLES WITH CHAIN-OF-THOUGHT (STRICT FORMAT ENFORCEMENT)
---

## SCOPE EXAMPLES

Example S1 — Scope with Table (headers excluded):

INPUT:
## In Scope
The following data will be included:

| Data Type | Description |
|----------|------------|
| Claims   | Medical claims data |
| Members  | Enrollment data |

## Out of Scope
- Dental claims
- Vision claims

Thought process:
- "## In Scope" is a heading → do NOT include it in in_scope value
- Content under it starts with "The following data will be included:" → include from here
- "## Out of Scope" is a heading → do NOT include it in out_of_scope value
- Content under it starts with "- Dental claims" → include from here

OUTPUT:
{{
  "scope": {{
    "in_scope": "The following data will be included:\n\n| Data Type | Description |\n|----------|------------|\n| Claims   | Medical claims data |\n| Members  | Enrollment data |",
    "out_of_scope": "- Dental claims\n- Vision claims"
  }}
}}

---

Example S2 — Continuation Across Chunks (headers excluded):

Chunk 1:
## In Scope
Includes medical claims...

Chunk 2:
...and pharmacy claims

Thought process:
- "## In Scope" is a heading → strip it; content starts with "Includes medical claims..."
- Chunk 2 continues the same in_scope content → append with "..."
- No out_of_scope heading or content found → empty string

OUTPUT:
{{
  "scope": {{
    "in_scope": "Includes medical claims...\n...and pharmacy claims",
    "out_of_scope": ""
  }}
}}

---

Example S3 — Mixed Table + Text (headers excluded):

INPUT:
## Scope Includes

| Field | Value |
|------|------|
| Status | Paid |

Also includes historical claims.

Thought process:
- "## Scope Includes" is a heading → strip it; content starts with the table
- Table and trailing text are both content → include both verbatim
- No out_of_scope section found → empty string

OUTPUT:
{{
  "scope": {{
    "in_scope": "| Field | Value |\n|------|------|\n| Status | Paid |\n\nAlso includes historical claims.",
    "out_of_scope": ""
  }}
}}

---

## REQUIREMENTS EXAMPLE

Example R1 — Numbered business rules:

INPUT:
## Business Requirements
1. The system shall extract only paid claims.
2. Members must have active enrollment as of the extract date.
3. Business rule: exclude sensitive categories defined in the PHI list.

Thought process:
- Look for numbered rules and "shall/must/business rule" language
- Each numbered line is a discrete requirement → extract all three verbatim
- The heading "## Business Requirements" is context, not a requirement itself → exclude it
- No tables or draft notes present

OUTPUT:
{{
  "requirements": "1. The system shall extract only paid claims.\n2. Members must have active enrollment as of the extract date.\n3. Business rule: exclude sensitive categories defined in the PHI list."
}}

---

## FILTERS AND PARAMETERS EXAMPLE

Example F1 — Explicit filter values:

INPUT:
Extract Configuration:
- Company: BCBS Illinois
- Line of Business: Commercial, Medicare
- Claim Status: Paid
- Start Date: 01/01/2023
- History Lookback: 24 months
- Sensitive Data Exclusion: PHI fields excluded per HIPAA
- Excluded LOB: Medicaid

Thought process:
- Each bullet maps directly to a filters_and_parameters field
- "BCBS Illinois" → Company
- "Commercial, Medicare" → Line of Business (multiple values, preserve as-is)
- "Paid" → Claim Status
- "01/01/2023" → Start Date
- "24 months" → History Lookback
- "PHI fields excluded per HIPAA" → Sensitive Data Exclusion
- "Medicaid" → Excluded LOB
- No inference — only extract what is explicitly stated

OUTPUT:
{{
  "filters_and_parameters": {{
    "Company": "BCBS Illinois",
    "Line of Business": "Commercial, Medicare",
    "Claim Status": "Paid",
    "Start Date": "01/01/2023",
    "History Lookback": "24 months",
    "Sensitive Data Exclusion": "PHI fields excluded per HIPAA",
    "Excluded LOB": "Medicaid"
  }}
}}

---

## FILE ATTRIBUTES MAPPING EXAMPLE

Example FA1 — File design configuration:

INPUT:
File Specifications:
- File Count: 3
- File Frequency: Monthly
- File Type: Fixed-width
- File Delimiter: Pipe (|)
- File Naming Convention: EXTRACT_YYYYMMDD.txt
- File Compression: GZIP
- File Encryption: PGP
- Field Headers: Yes
- Trailer Required: Yes
- Control File Required: Yes
- File Delivery Method: SFTP

Thought process:
- Each bullet maps to a file_attributes_mapping field
- Extract exact values only — "3", "Monthly", "Fixed-width", "Pipe (|)", etc.
- "EXTRACT_YYYYMMDD.txt" is the naming convention → preserve exactly
- Boolean fields ("Yes") → preserve as stated, do not convert to true/false

OUTPUT:
{{
  "file_attributes_mapping": {{
    "File Count": "3",
    "File Frequency": "Monthly",
    "File Type": "Fixed-width",
    "File Delimiter": "Pipe (|)",
    "File Naming Convention": "EXTRACT_YYYYMMDD.txt",
    "File Compression": "GZIP",
    "File Encryption": "PGP",
    "Field Headers": "Yes",
    "Trailer Required": "Yes",
    "Control File Required": "Yes",
    "File Delivery Method": "SFTP"
  }}
}}

---

## FILE SPECS EXAMPLE

Example FS1 — Vendor and transfer metadata:

INPUT:
Vendor Details:
- Vendor Name: Acme Health Data
- Vendor Contact: Jane Smith
- Vendor Email: jane.smith@acme.com
- Vendor Phone: 555-123-4567
- Transfer Method: SFTP
- Vendor Server: sftp.acme.com
- Vendor File Drop Location: /outbound/bcbs/
- File Extension: .txt
- File Delimiter: |
- Date/Timestamp Format: YYYYMMDD
- Header Record Number: 1
- Trailer Record Number: 1
- File Compression Type: GZIP
- Receive Files When No Data: No
- Done File Present: Yes
- File Arrival Schedule: 1st of each month by 6AM CT
- Estimated Record Count (Initial): 500000
- Estimated Record Count (Ongoing): 50000

Thought process:
- Each line maps to a file_specs field by name
- "Acme Health Data" → vendor_name
- "Jane Smith" → vendor_contact_name
- "jane.smith@acme.com" → vendor_email
- "555-123-4567" → vendor_phone_number
- "SFTP" → transfer_method
- "sftp.acme.com" → vendor_server_name
- "/outbound/bcbs/" → vendor_file_drop_location
- Preserve exact values — no reformatting

OUTPUT:
{{
  "file_specs": {{
    "vendor_name": "Acme Health Data",
    "vendor_contact_name": "Jane Smith",
    "vendor_email": "jane.smith@acme.com",
    "vendor_phone_number": "555-123-4567",
    "transfer_method": "SFTP",
    "vendor_server_name": "sftp.acme.com",
    "vendor_file_drop_location": "/outbound/bcbs/",
    "file_extension": ".txt",
    "file_delimiter": "|",
    "date_timestamp_format": "YYYYMMDD",
    "header_record_number": "1",
    "trailer_record_number": "1",
    "file_compression_type": "GZIP",
    "receive_files_when_no_data": "No",
    "done_file_present": "Yes",
    "file_arrival_schedule": "1st of each month by 6AM CT",
    "estimated_record_count_initial": "500000",
    "estimated_record_count_ongoing": "50000"
  }}
}}

---

## COMMON RULES EXAMPLE

Example CR1 — System-wide constraints:

INPUT:
Interface Details:
- Interface Code: IBX-MED-001
- History Required: Yes
- Effective Dates From: 01/01/2020
- Effective Dates To: 12/31/2023
- Posted Dates From: 01/01/2020
- Posted Dates To: 12/31/2023
- Rolling Month Requirement: 12
- Driver Required: Yes
- Incremental History Required: No
- Runout Required: Yes
- Number of Months: 24
- Sensitive Category List: Mental Health, Substance Abuse
- De-identify Extract: No
- Last Updated Date: 2024-03-15
- Comments: Initial load includes 4 years of history

Thought process:
- Each line maps to a common_rules field by name
- "IBX-MED-001" → interface_code
- "Yes" → history_required (preserve as stated)
- Date values → preserve exact format as written in document
- "Mental Health, Substance Abuse" → sensitive_category_list (comma-separated, preserve as-is)
- "No" → deidentity_extract
- Free-text comment → comments field verbatim

OUTPUT:
{{
  "common_rules": {{
    "interface_code": "IBX-MED-001",
    "history_required": "Yes",
    "effective_dates_from": "01/01/2020",
    "effective_dates_to": "12/31/2023",
    "posted_dates_from": "01/01/2020",
    "posted_dates_to": "12/31/2023",
    "rolling_month_requirement": "12",
    "driver_required": "Yes",
    "incremental_history_required": "No",
    "runout_required": "Yes",
    "number_of_months": "24",
    "sensitive_category_list": "Mental Health, Substance Abuse",
    "deidentity_extract": "No",
    "last_updated_date": "2024-03-15",
    "comments": "Initial load includes 4 years of history"
  }}
}}

---

GLOBAL RULES (STRICT)
---

- NEVER hallucinate
- DONOT return .... from the table of contents and uness
- ALWAYS return empty string if not found
- ALWAYS preserve markdown EXACTLY
- TABLES must remain tables
- DO NOT summarize
- DO NOT infer
- DO NOT drop surrounding context
- DO NOT overwrite strong values with weak ones
- FOLLOW BSA INSTRUCTIONS ABOVE ALL

---

OUTPUT REQUIREMENT
---

Return ONLY valid JSON matching schema.
NO explanations.
NO extra keys.
---
MARKDOWN CHUNK
---
{chunk_markdown}
"""

    call_config: dict[str, Any] = {
        "temperature": 0.2,
        "response_mime_type": "application/json",
        "response_schema": response_schema,
        "max_output_tokens": 60000,
    }
    if cache_name:
        call_config["cached_content"] = cache_name

    resp = client.models.generate_content(
        model=config.AGENT_MODEL,
        contents=[prompt],
        config=call_config,
    )

    text = (resp.text or "{}").strip()
    raw = _safe_json_load(text)

    try:
        return _normalize_schema(raw)
    except Exception:
        logger.warning("Chunk %s returned invalid JSON", chunk_index)
        return {}


# ---------------------------------------------------------------------------
# Validation — fetch cached JSON and verify/correct fields via LLM
# ---------------------------------------------------------------------------

_VALIDATION_PROMPT = """
You are a strict data quality validator for BRD requirement layer extractions.

You will be given:
1. The cached markdown context (BRD + file layout + optional transcript + BSA notes)
2. The previously extracted requirement layer JSON

Your task:
- Verify every non-empty field against the markdown context
- Correct any field that is wrong, hallucinated, or misaligned with the source
- Fill in any field that was missed but is clearly present in the context
- Do NOT remove values that are correct
- Return STRICT JSON only using the exact same schema as the input
- Add a top-level key "_validation_corrections" listing field paths that were changed (empty list if none)

CACHED MARKDOWN CONTEXT:
{context}

EXTRACTED REQUIREMENT LAYER:
{extracted}
"""


_CHECKPOINT_PROMPT = """
You are correcting a structured requirement layer JSON extracted from a BRD.

CURRENT JSON:
{current_json}

FIELDS TO RE-DERIVE:
{rejected_fields}

Each field entry contains:
- "field_path": dot-separated path to the field in the JSON
- "current_value": the value currently in the JSON (may be empty or wrong)
- "instruction": what the reviewer wants you to do for this field
- "comment": (optional) additional context or hint from the reviewer about this specific field

ADDITIONAL INSTRUCTIONS (apply to all fields):
{additional_instructions}

Rules:
- Only update the fields listed in FIELDS TO RE-DERIVE.
- All other fields must remain exactly as they appear in CURRENT JSON.
- Use the source documents to derive values — do not guess.
- If a value cannot be determined from the source documents, return an empty string.
- Return the full corrected JSON only, no explanation, no markdown.
"""
