"""
BRD extraction utilities.

Pipeline for extract-brd-information:
  1. Download all artifacts from GCS for a given session_id
  2. Convert BRD / transcript DOCX → PDF if needed (reuses splitter.convert_docx_to_pdf)
  3. Convert BRD PDF → markdown iteratively (chunk-by-chunk LLM, preserving all tables/text)
  4. Convert transcript PDF → markdown (same approach, lighter prompt)
  5. Return structured BrdArtifacts with markdown content + metadata
"""
from __future__ import annotations

import io
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from google import genai
from google.genai import types as genai_types
from google.genai.types import Part
from pypdf import PdfReader, PdfWriter

from config.settings import config
from utils.doc_chunker.splitter import convert_docx_to_pdf, split_pdf_to_chunks
from utils.gcs_artifact_utils import artifact_bucket_name, artifact_storage_client, list_blobs, download_bytes, upload_text, upload_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GCS artifact classification helpers
# ---------------------------------------------------------------------------

_PREFIX_MAP = {
    "brd_": "brd",
    "file_layout_": "file_layout",
    "transcript_": "transcript",
    "bsa_notes": "bsa_notes",
    "pipeline_result": "pipeline_result",
}

response_schema = {
    "type": "object",
    "properties": {

        "scope": {
            "type": "object",
            "properties": {
                "in_scope": {"type": "string"},
                "out_of_scope": {"type": "string"}
            },
            "required": ["in_scope", "out_of_scope"]
        },

        "bsa_input": {"type": "string"},

        "requirements": {"type": "string"},

        # 🔥 NEW — FILTERS + PARAMETERS
        "filters_and_parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "business": {"type": "string"},
                "state": {"type": "string"},
                "line_of_business": {"type": "string"},
                "financial_arrangement": {"type": "string"},
                "product_plan_type": {"type": "string"},
                "extended_product": {"type": "string"},
                "coverage_plan": {"type": "string"},
                "customer_id": {"type": "string"},
                "group_id": {"type": "string"},
                "claim_status": {"type": "string"},
                "blue_card_indicator": {"type": "string"},
                "excluded_companies": {"type": "string"},
                "excluded_lob": {"type": "string"},
                "sensitive_data_exclusion": {"type": "string"},
                "opt_out_groups": {"type": "string"},

                "date_parameters": {
                    "type": "object",
                    "properties": {
                        "member_active_enrollment": {"type": "string"},
                        "active_plan_group": {"type": "string"},
                        "start_date": {"type": "string"},
                        "history_lookback": {"type": "string"},
                        "rollover_period": {"type": "string"},
                        "claim_service_dates": {"type": "string"},
                        "claim_posted_dates": {"type": "string"},
                        "paid_dates": {"type": "string"},
                        "pharmacy_fill_dates": {"type": "string"},
                        "pharmacy_cut_dates": {"type": "string"}
                    },
                    "required": [
                        "member_active_enrollment",
                        "active_plan_group",
                        "start_date",
                        "history_lookback",
                        "rollover_period",
                        "claim_service_dates",
                        "claim_posted_dates",
                        "paid_dates",
                        "pharmacy_fill_dates",
                        "pharmacy_cut_dates"
                    ]
                }
            },
            "required": [
                "company",
                "business",
                "state",
                "line_of_business",
                "financial_arrangement",
                "product_plan_type",
                "extended_product",
                "coverage_plan",
                "customer_id",
                "group_id",
                "claim_status",
                "blue_card_indicator",
                "excluded_companies",
                "excluded_lob",
                "sensitive_data_exclusion",
                "opt_out_groups",
                "date_parameters"
            ]
        },

        # 🔥 NEW — FILE ATTRIBUTE + FIELD + DATA RULES
        "file_attributes_mapping": {
            "type": "object",
            "properties": {
                "file_count": {"type": "string"},
                "subject_areas": {"type": "string"},
                "file_frequency": {"type": "string"},
                "file_type": {"type": "string"},
                "file_delimiter": {"type": "string"},
                "file_naming_convention": {"type": "string"},
                "file_compression": {"type": "string"},
                "file_encryption": {"type": "string"},
                "control_file_required": {"type": "string"},
                "file_delivery_method": {"type": "string"},
                "field_headers": {"type": "string"},
                "trailer_required": {"type": "string"},
                "field_requirements": {"type": "string"},
                "default_values": {"type": "string"},
                "data_format_rules": {"type": "string"}
            },
            "required": [
                "file_count",
                "subject_areas",
                "file_frequency",
                "file_type",
                "file_delimiter",
                "file_naming_convention",
                "file_compression",
                "file_encryption",
                "control_file_required",
                "file_delivery_method",
                "field_headers",
                "trailer_required",
                "field_requirements",
                "default_values",
                "data_format_rules"
            ]
        },

        # EXISTING — FILE SPECS (FULLY EXPANDED)
        "file_specs": {
            "type": "object",
            "properties": {
                "physical_file_name": {"type": "string"},
                "vendor_name": {"type": "string"},
                "transfer_method": {"type": "string"},
                "vendor_contact_name": {"type": "string"},
                "frequency_mode": {"type": "string"},
                "vendor_phone_number": {"type": "string"},
                "dependencies": {"type": "string"},
                "vendor_email": {"type": "string"},
                "email_notification_dl": {"type": "string"},
                "file_delimiter": {"type": "string"},
                "file_extension": {"type": "string"},
                "date_timestamp_format": {"type": "string"},
                "header_record_number": {"type": "string"},
                "trailer_record_number": {"type": "string"},
                "quote_indicator": {"type": "string"},
                "file_population_type": {"type": "string"},
                "file_compression_type": {"type": "string"},
                "receive_files_when_no_data": {"type": "string"},
                "assumptions": {"type": "string"},
                "vendor_server_name": {"type": "string"},
                "vendor_file_drop_location": {"type": "string"},
                "control_file_name": {"type": "string"},
                "control_file_delimiter": {"type": "string"},
                "control_file_extension": {"type": "string"},
                "control_file_header_present": {"type": "string"},
                "control_record_number": {"type": "string"},
                "control_file_amount_column_count": {"type": "string"},
                "done_file_present": {"type": "string"},
                "file_arrival_schedule": {"type": "string"},
                "estimated_record_count_initial": {"type": "string"},
                "estimated_record_count_ongoing": {"type": "string"}
            },
            "required": [
                "physical_file_name",
                "vendor_name",
                "transfer_method",
                "vendor_contact_name",
                "frequency_mode",
                "vendor_phone_number",
                "dependencies",
                "vendor_email",
                "email_notification_dl",
                "file_delimiter",
                "file_extension",
                "date_timestamp_format",
                "header_record_number",
                "trailer_record_number",
                "quote_indicator",
                "file_population_type",
                "file_compression_type",
                "receive_files_when_no_data",
                "assumptions",
                "vendor_server_name",
                "vendor_file_drop_location",
                "control_file_name",
                "control_file_delimiter",
                "control_file_extension",
                "control_file_header_present",
                "control_record_number",
                "control_file_amount_column_count",
                "done_file_present",
                "file_arrival_schedule",
                "estimated_record_count_initial",
                "estimated_record_count_ongoing"
            ]
        },

        # EXISTING — COMMON RULES
        "common_rules": {
            "type": "object",
            "properties": {
                "interface_code": {"type": "string"},
                "history_required": {"type": "string"},
                "effective_dates_from": {"type": "string"},
                "effective_dates_to": {"type": "string"},
                "posted_dates_from": {"type": "string"},
                "posted_dates_to": {"type": "string"},
                "rolling_month_requirement": {"type": "string"},
                "driver_required": {"type": "string"},
                "incremental_history_required": {"type": "string"},
                "runout_required": {"type": "string"},
                "number_of_months": {"type": "string"},
                "sensitive_category_list": {"type": "string"},
                "deidentity_extract": {"type": "string"},
                "comments": {"type": "string"},
                "last_updated_date": {"type": "string"}
            },
            "required": [
                "interface_code",
                "history_required",
                "effective_dates_from",
                "effective_dates_to",
                "posted_dates_from",
                "posted_dates_to",
                "rolling_month_requirement",
                "driver_required",
                "incremental_history_required",
                "runout_required",
                "number_of_months",
                "sensitive_category_list",
                "deidentity_extract",
                "comments",
                "last_updated_date"
            ]
        }
    },

    "required": [
        "scope",
        "bsa_input",
        "requirements",
        "filters_and_parameters",
        "file_attributes_mapping",
        "file_specs",
        "common_rules"
    ]
}

def _classify_blob_name(name: str) -> Optional[str]:
    """Return the artifact type key for a GCS object name, or None if unknown."""
    basename = name.split("/")[-1]
    for prefix, kind in _PREFIX_MAP.items():
        if basename.startswith(prefix):
            return kind
    return None


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

def _get_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )

def _safe_json_load(text: str) -> dict:
    import json
    try:
        return json.loads(text)
    except:
        import re
        text = re.sub(r"```json|```", "", text).strip()
        try:
            return json.loads(text)
        except:
            return {}
# ---------------------------------------------------------------------------
# PDF → markdown (iterative, chunk-by-chunk)
# ---------------------------------------------------------------------------

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


def _pdf_bytes_to_chunks(pdf_bytes: bytes, chunk_size: int = 5) -> tuple[list[bytes], int]:
    """Split raw PDF bytes into page chunks without writing to disk."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    total = len(reader.pages)
    chunks: list[bytes] = []
    for start in range(0, total, chunk_size):
        writer = PdfWriter()
        for i in range(start, min(start + chunk_size, total)):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())
    return chunks, total


def _convert_to_markdown(
    pdf_bytes: bytes,
    prompt_template: str,
    chunk_size: int = config.MARKDOWN_CHUNK_PAGES,
    max_tokens: int = 30000,
    retries: int = 3,
    retry_delay: float = 1.5,
) -> str:

    if sys.platform == "win32":
        import pythoncom
        pythoncom.CoInitialize()

    try:
        client = _get_client()

        chunks, total_pages = _pdf_bytes_to_chunks(pdf_bytes, chunk_size)

        if total_pages == 0:
            raise ValueError("PDF has 0 pages — extraction cannot proceed")

        logger.info("PDF split into %d chunks | total_pages=%d", len(chunks), total_pages)

        markdown_parts: list[str] = []
        handoff = "(start of document)"

        failed_chunks = []

        for idx, chunk_bytes in enumerate(chunks):
            start_page = idx * chunk_size + 1
            end_page = min((idx + 1) * chunk_size, total_pages)
            page_range = f"{start_page}-{end_page}"

            prompt = prompt_template.format(page_range=page_range, handoff=handoff)
            pdf_part = Part.from_bytes(data=chunk_bytes, mime_type="application/pdf")

            success = False
            last_exc = None

            for attempt in range(retries):
                try:
                    resp = client.models.generate_content(
                        model=config.AGENT_MODEL,
                        contents=[pdf_part, prompt],
                        config={
                            "temperature": 0.0,
                            "max_output_tokens": max_tokens,
                        },
                    )

                    chunk_md = (resp.text or "").strip()

                    # 🚨 CRITICAL: detect empty / weak output
                    if not chunk_md or len(chunk_md) < 50:
                        raise ValueError("Chunk returned empty or too small")

                    markdown_parts.append(chunk_md)

                    handoff = chunk_md[-500:]  # stronger continuity

                    logger.info(
                        "Chunk SUCCESS | %d/%d pages=%s chars=%d",
                        idx + 1,
                        len(chunks),
                        page_range,
                        len(chunk_md),
                    )

                    success = True
                    break

                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "Chunk FAILED | %d attempt=%d pages=%s error=%s",
                        idx + 1,
                        attempt + 1,
                        page_range,
                        exc,
                    )
                    time.sleep(retry_delay * (attempt + 1))

            if not success:
                logger.error("Chunk PERMANENT FAILURE | pages=%s", page_range)
                failed_chunks.append((idx, chunk_bytes, page_range))

        #  HARD FAIL if any chunk failed
        if failed_chunks:
            raise RuntimeError(f"{len(failed_chunks)} chunks failed — extraction incomplete")

        full_markdown = "\n\n".join(markdown_parts)

        # FINAL VALIDATION
        if len(full_markdown) < 500:
            raise ValueError("Final markdown too small — extraction likely incomplete")

        logger.info("FULL MARKDOWN GENERATED | total_chars=%d", len(full_markdown))

        return full_markdown

    finally:
        if sys.platform == "win32":
            import pythoncom
            pythoncom.CoUninitialize()

# ---------------------------------------------------------------------------
# Ensure PDF bytes — convert DOCX bytes → PDF bytes if needed
# ---------------------------------------------------------------------------

def _ensure_pdf_bytes(raw_bytes: bytes, filename: str) -> bytes:
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return raw_bytes

    if ext != ".docx":
        raise ValueError(f"Cannot convert {ext!r} to PDF")

    tmp_dir = Path(config.DATA_DIR) / "tmp_brd_conversion"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    docx_path = tmp_dir / f"_conv_{os.urandom(4).hex()}.docx"
    pdf_path = docx_path.with_name(docx_path.stem + "_converted.pdf")

    try:
        docx_path.write_bytes(raw_bytes)

        if sys.platform == "win32":
            import pythoncom
            pythoncom.CoInitialize()

        try:
            convert_docx_to_pdf(str(docx_path))
        finally:
            if sys.platform == "win32":
                pythoncom.CoUninitialize()

        if not pdf_path.exists():
            raise FileNotFoundError(f"Converted PDF not found: {pdf_path}")

        logger.info("DOCX → PDF SUCCESS | file=%s", filename)

        return pdf_path.read_bytes()

    except Exception as e:
        logger.error("DOCX → PDF FAILED | file=%s error=%s", filename, e)

        try:
            time.sleep(1)

            if sys.platform == "win32":
                import pythoncom
                pythoncom.CoInitialize()

            convert_docx_to_pdf(str(docx_path))

            if sys.platform == "win32":
                pythoncom.CoUninitialize()

            if pdf_path.exists():
                logger.info("DOCX → PDF RECOVERY SUCCESS")
                return pdf_path.read_bytes()

        except Exception as e2:
            logger.error("DOCX → PDF RECOVERY FAILED: %s", e2)

        raise

    finally:
        for p in tmp_dir.glob(f"{docx_path.stem}*"):
            try:
                p.unlink()
            except:
                pass
# ---------------------------------------------------------------------------
# Stateful markdown extraction
# ---------------------------------------------------------------------------

EXTRACTION_CHUNK_PAGES = getattr(config, "EXTRACTION_CHUNK_PAGES", 10)

def _normalize_schema(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    return {
        "scope": payload.get("scope", {}),
        "bsa_input": payload.get("bsa_input", ""),
        "requirements": payload.get("requirements", ""),
        "filters_and_parameters": payload.get("filters_and_parameters", {}),
        "file_attributes_mapping": payload.get("file_attributes_mapping", {}),
        "file_specs": payload.get("file_specs", {}),
        "common_rules": payload.get("common_rules", {}),
    }

def _build_empty_requirement_schema() -> dict[str, Any]:
    return {
        "scope": {"in_scope": "", "out_of_scope": ""},
        "bsa_input": "",
        "requirements": "",

        "filters_and_parameters": {
            "company": "",
            "business": "",
            "state": "",
            "line_of_business": "",
            "financial_arrangement": "",
            "product_plan_type": "",
            "extended_product": "",
            "coverage_plan": "",
            "customer_id": "",
            "group_id": "",
            "claim_status": "",
            "blue_card_indicator": "",
            "excluded_companies": "",
            "excluded_lob": "",
            "sensitive_data_exclusion": "",
            "opt_out_groups": "",
            "date_parameters": {
                "member_active_enrollment": "",
                "active_plan_group": "",
                "start_date": "",
                "history_lookback": "",
                "rollover_period": "",
                "claim_service_dates": "",
                "claim_posted_dates": "",
                "paid_dates": "",
                "pharmacy_fill_dates": "",
                "pharmacy_cut_dates": ""
            }
        },

        "file_attributes_mapping": {
            "file_count": "",
            "subject_areas": "",
            "file_frequency": "",
            "file_type": "",
            "file_delimiter": "",
            "file_naming_convention": "",
            "file_compression": "",
            "file_encryption": "",
            "control_file_required": "",
            "file_delivery_method": "",
            "field_headers": "",
            "trailer_required": "",
            "field_requirements": "",
            "default_values": "",
            "data_format_rules": ""
        },

         "file_specs": {
            "physical_file_name": "",
            "vendor_name": "",
            "transfer_method": "",
            "vendor_contact_name": "",
            "frequency_mode": "",
            "vendor_phone_number": "",
            "dependencies": "",
            "vendor_email": "",
            "email_notification_dl": "",
            "file_delimiter": "",
            "file_extension": "",
            "date_timestamp_format": "",
            "header_record_number": "",
            "trailer_record_number": "",
            "quote_indicator": "",
            "file_population_type": "",
            "file_compression_type": "",
            "receive_files_when_no_data": "",
            "assumptions": "",
            "vendor_server_name": "",
            "vendor_file_drop_location": "",
            "control_file_name": "",
            "control_file_delimiter": "",
            "control_file_extension": "",
            "control_file_header_present": "",
            "control_record_number": "",
            "control_file_amount_column_count": "",
            "done_file_present": "",
            "file_arrival_schedule": "",
            "estimated_record_count_initial": "",
            "estimated_record_count_ongoing": "",
        },
        "common_rules": {
            "interface_code": "",
            "history_required": "",
            "effective_dates_from": "",
            "effective_dates_to": "",
            "posted_dates_from": "",
            "posted_dates_to": "",
            "rolling_month_requirement": "",
            "driver_required": "",
            "incremental_history_required": "",
            "runout_required": "",
            "number_of_months": "",
            "sensitive_category_list": "",
            "deidentity_extract": "",
            "comments": "",
            "last_updated_date": "",
        },
    }

def _split_markdown_semantic_chunks(markdown: str, approx_pages: int = EXTRACTION_CHUNK_PAGES) -> list[str]:
    """
    Split markdown into extraction-safe semantic chunks.
    Uses heading-aware splitting first, then size fallback.
    """
    if not markdown.strip():
        return []

    sections = re.split(r"(?m)^# ", markdown)
    rebuilt = []
    for idx, sec in enumerate(sections):
        sec = sec.strip()
        if not sec:
            continue
        rebuilt.append("# " + sec if idx > 0 else sec)

    # fallback chunking by char size
    max_chars = approx_pages * 4000
    chunks: list[str] = []
    current = ""

    for sec in rebuilt:
        if len(current) + len(sec) > max_chars and current:
            chunks.append(current)
            current = sec
        else:
            current += "\n\n" + sec

    if current.strip():
        chunks.append(current)

    return chunks


def _merge_nested_state(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    for key, value in incoming.items():
        if key not in base:
            base[key] = value
            continue
        if key == "bsa_input":
            if value:
                base[key] = (base.get(key, "") + "\n\n" + value).strip()
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_nested_state(base[key], value)
        else:
            if value not in (None, "", [], {}):
                base[key] = value
    return base


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
    if cache_name:
        bsa_section = "BSA INPUT REQUIREMENTS:\n(provided via cached context — apply with highest priority)"
    else:
        bsa_section = f"BSA INPUT REQUIREMENTS:\n{bsa_input or 'No explicit BSA input provided.'}"

    import json

    OUTPUT_SCHEMA_SNIPPET = json.dumps({
        "scope": {
            "in_scope": "",
            "out_of_scope": ""
        },
        "bsa_input": "",
        "requirements": "",
        "filters_and_parameters": {},
        "file_attributes_mapping": {},
        "file_specs": {},
        "common_rules": {}
    }, indent=2)

    prompt = f"""
You are a STRICT BRD + BSA REQUIREMENT EXTRACTION ENGINE.

Your PRIMARY objective:
→ Extract ALL structured information EXACTLY as present in the document
→ With SPECIAL PRIORITY on BSA INPUT DRIVEN EXTRACTION

-----------------------------------
BSA INPUT CONTEXT (ANCHOR SIGNAL — HIGHEST PRIORITY)
-----------------------------------
{bsa_section}

CRITICAL INSTRUCTION:

The BSA input is NOT just reference text.

You MUST:
- Use BSA input as a SEARCH ANCHOR
- Actively LOOK for:
    → matching terms
    → similar phrases
    → related sections
    → adjacent tables
    → nearby paragraphs

If ANY BSA term appears:
→ extract EVERYTHING around it:
    - full tables
    - full sections
    - surrounding paragraphs
    - field definitions
    - constraints

⚠️ DO NOT extract isolated lines — ALWAYS extract CONTEXT BLOCKS

-----------------------------------
CURRENT RUNNING STATE (DO NOT LOSE DATA)
-----------------------------------
{running_state}

-----------------------------------
EXTRACTION TASK (STRICT + COMPLETE)
-----------------------------------

You MUST extract into ALL sections below:

1. scope
   - in_scope
   - out_of_scope

2. bsa_input   (MOST IMPORTANT FIELD)
   MUST contain:
   - ALL tables (FULLY)
   - ALL field definitions
   - ALL file layouts
   - ALL business rules
   - ALL paragraphs near BSA-related content
   - ALL multi-line logic

   RULES:
   - NEVER summarize
   - ALWAYS preserve verbatim text
   - ALWAYS include full tables
   - ALWAYS include adjacent context

3. requirements
   - ONLY finalized business rules
   - Numbered or derived logic
   - No raw tables

4. filters_and_parameters   (STRICT FIELD MAPPING)

   Extract EXACT values:

   Company:
   Business:
   State:
   Line of Business:
   Financial Arrangement:
   Product Plan Type:
   Extended Product:
   Coverage Plan:
   Customer ID:
   Group ID:
   Claim Status:
   Blue Card Indicator:
   Excluded Companies:
   Excluded LOB:
   Sensitive Data Exclusion:
   Opt Out Groups:

   DATE PARAMETERS:
   - Member Active Enrollment
   - Active Plan Group
   - Start Date
   - History Lookback
   - Rollover Period
   - Claim Service Dates
   - Claim Posted Dates
   - Paid Dates
   - Pharmacy Fill Dates
   - Pharmacy Cut Dates

   RULES:
   - Extract EXACT wording (PA, NJ, FI, SF, etc.)
   - If multiple values → concatenate
   - DO NOT infer
   - DO NOT summarize

5. file_attributes_mapping   (STRICT EXTRACTION)

   Extract:

   File Count:
   Subject Areas:
   File Frequency:
   File Type:
   File Delimiter:
   File Naming Convention:
   File Compression:
   File Encryption:
   Control File Required:
   File Delivery Method:
   Field Headers:
   Trailer Required:
   Field Requirements:
   Default Values:
   Data Format Rules:

   RULES:
   - Preserve EXACT wording
   - Extract full definitions if available
   - Include table data if present

6. file_specs

   Extract EXACT metadata:

   - physical_file_name
   - vendor_name
   - transfer_method
   - vendor_contact_name
   - frequency_mode
   - vendor_phone_number
   - dependencies
   - vendor_email
   - email_notification_dl
   - file_delimiter
   - file_extension
   - date_timestamp_format
   - header_record_number
   - trailer_record_number
   - quote_indicator
   - file_population_type
   - file_compression_type
   - receive_files_when_no_data
   - assumptions
   - vendor_server_name
   - vendor_file_drop_location
   - control_file_name
   - control_file_delimiter
   - control_file_extension
   - control_file_header_present
   - control_record_number
   - control_file_amount_column_count
   - done_file_present
   - file_arrival_schedule
   - estimated_record_count_initial
   - estimated_record_count_ongoing

7. common_rules

   Extract:

   - interface_code
   - history_required
   - effective_dates_from
   - effective_dates_to
   - posted_dates_from
   - posted_dates_to
   - rolling_month_requirement
   - driver_required
   - incremental_history_required
   - runout_required
   - number_of_months
   - sensitive_category_list
   - deidentity_extract
   - comments
   - last_updated_date

-----------------------------------
CRITICAL EXTRACTION RULES
-----------------------------------

- NEVER summarize structured data
- ALWAYS preserve verbatim text
- ALWAYS extract full tables (no truncation)
- ALWAYS merge across chunks
- NEVER overwrite strong values with weak ones
- NEVER skip a section — return empty string if not found
- NEVER hallucinate values
- ALWAYS prefer BSA input over BRD content

-----------------------------------
OUTPUT FORMAT (STRICT JSON ONLY)
-----------------------------------

Return ONLY valid JSON matching the provided schema.

DO NOT:
- add explanations
- add comments
- add extra keys

-----------------------------------
MARKDOWN CHUNK
-----------------------------------
{chunk_markdown}

-----------------------------------
OUTPUT FORMAT (STRICT JSON)
-----------------------------------
{OUTPUT_SCHEMA_SNIPPET}
"""

    call_config: dict[str, Any] = {
    "temperature": 0.0,
    "response_mime_type": "application/json",
    "response_schema": response_schema, 
    "max_output_tokens": 16000,
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

def _create_cache(
    client: genai.Client,
    content: str,
    display_name: str,
) -> str | None:
    """
    Create a Vertex AI cached content entry for a large static text.
    Returns the cache name, or None if caching is disabled / content too small.
    """
    if not config.BRD_CONTEXT_CACHE_ENABLED:
        return None
    if len(content) < config.BRD_CONTEXT_CACHE_MIN_CHARS:
        logger.debug("Cache skipped — content too small (%d chars) for %s", len(content), display_name)
        return None
    try:
        cached = client.caches.create(
            model=config.AGENT_MODEL,
            config=genai_types.CreateCachedContentConfig(
                contents=[genai_types.Content(role="user", parts=[genai_types.Part(text=content)])],
                display_name=display_name,
                ttl=f"{config.BRD_CONTEXT_CACHE_TTL_SECONDS}s",
            ),
        )
        logger.info("Created prompt cache | name=%s display=%s", cached.name, display_name)
        return cached.name
    except Exception as exc:
        logger.warning("Prompt cache creation failed (%s) — falling back to inline context", exc)
        return None


def _delete_cache(client: genai.Client, cache_name: str) -> None:
    try:
        client.caches.delete(name=cache_name)
        logger.info("Deleted prompt cache | name=%s", cache_name)
    except Exception as exc:
        logger.warning("Could not delete prompt cache %s: %s", cache_name, exc)

def _final_cleanup(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": state.get("scope", {}),
        "bsa_input": state.get("bsa_input", "").strip(),
        "requirements": state.get("requirements", "").strip(),
        "filters_and_parameters": state.get("filters_and_parameters", {}),
        "file_attributes_mapping": state.get("file_attributes_mapping", {}),
        "file_specs": state.get("file_specs", {}),
        "common_rules": state.get("common_rules", {}),
    }

def _run_stateful_extraction(
    combined_markdown: str,
    session_id: str,
    bsa_input: str | None = None,
) -> dict[str, Any]:
    """
    Stateful chunk-wise markdown extraction.
    Caches BSA notes once (if large enough) so every chunk call reuses it
    instead of re-sending the full text each time.
    """
    client = _get_client()
    chunks = _split_markdown_semantic_chunks(combined_markdown)
    running_state = _build_empty_requirement_schema()

    logger.info("Starting stateful extraction | session=%s chunks=%d", session_id, len(chunks))

    # Cache BSA notes once — reused across all chunk calls
    bsa_cache_name: str | None = None
    cached_bsa_input = bsa_input  # fallback: inline
    if bsa_input:
        bsa_cache_name = _create_cache(
            client,
            content=bsa_input,
            display_name=f"bsa-notes-{session_id}",
        )
        if bsa_cache_name:
            cached_bsa_input = None  # will be injected via cache reference

    try:
        for idx, chunk in enumerate(chunks):
            logger.info("Processing extraction chunk %d/%d", idx + 1, len(chunks))
            chunk_result = _extract_chunk_structured(
                client=client,
                chunk_markdown=chunk,
                running_state=running_state,
                chunk_index=idx,
                bsa_input=cached_bsa_input,
                cache_name=bsa_cache_name,
            )
            running_state = _merge_nested_state(running_state, chunk_result)
    finally:
        if bsa_cache_name:
            _delete_cache(client, bsa_cache_name)

    return _final_cleanup(running_state)

# ---------------------------------------------------------------------------
# Main pipeline — called by the endpoint
# ---------------------------------------------------------------------------

def run_brd_extraction(session_id: str) -> dict[str, Any]:
    """
    1. List all blobs under bsa-extract-artifacts/{session_id}/
    2. Download and classify each artifact
    3. Convert BRD + transcript to PDF (if DOCX) then to markdown
    4. Return structured result dict
    """
    uploads_prefix = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/uploads/"
    blobs = list_blobs(prefix=uploads_prefix)

    if not blobs:
        raise FileNotFoundError(f"No artifacts found in GCS for session_id={session_id!r} (prefix={uploads_prefix})")

    # Download and classify
    artifacts: dict[str, dict[str, Any]] = {}
    for blob in blobs:
        kind = _classify_blob_name(blob.name)
        if kind is None:
            logger.warning("Skipping unrecognised artifact: %s", blob.name)
            continue
        raw = download_bytes(object_name=blob.name)
        artifacts[kind] = {"name": blob.name.split("/")[-1], "raw": raw}
        logger.info("Downloaded artifact kind=%s name=%s size=%d", kind, blob.name, len(raw))

    if "brd" not in artifacts:
        raise ValueError(f"BRD artifact not found for session_id={session_id!r}")
    if "file_layout" not in artifacts:
        raise ValueError(f"File layout artifact not found for session_id={session_id!r}")

    md_prefix = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/markdown_files"
    result: dict[str, Any] = {"session_id": session_id, "artifacts_found": list(artifacts.keys())}
    markdown_uploads: list[str] = []

    # ── BRD → PDF → markdown ────────────────────────────────────────────────
    brd_info = artifacts["brd"]
    brd_pdf_bytes = _ensure_pdf_bytes(brd_info["raw"], brd_info["name"])
    logger.info("Converting BRD to markdown | session=%s", session_id)
    brd_markdown = _convert_to_markdown(brd_pdf_bytes, _BRD_CHUNK_PROMPT)

    result["brd_filename"] = brd_info["name"]

    brd_md_stem = Path(brd_info["name"]).stem
    brd_md_object = f"{md_prefix}/brd_{brd_md_stem}.md"
    upload_text(object_name=brd_md_object, content=brd_markdown)
    markdown_uploads.append(brd_md_object)
    logger.info("Uploaded BRD markdown to GCS | object=%s", brd_md_object)

    # ── File layout → PDF → markdown ────────────────────────────────────────
    layout_info = artifacts["file_layout"]
    layout_pdf_bytes = _ensure_pdf_bytes(layout_info["raw"], layout_info["name"])
    logger.info("Converting file layout to markdown | session=%s", session_id)
    layout_markdown = _convert_to_markdown(layout_pdf_bytes, _BRD_CHUNK_PROMPT)

    result["file_layout_filename"] = layout_info["name"]

    layout_md_stem = Path(layout_info["name"]).stem
    layout_md_object = f"{md_prefix}/file_layout_{layout_md_stem}.md"
    upload_text(object_name=layout_md_object, content=layout_markdown)
    markdown_uploads.append(layout_md_object)
    logger.info("Uploaded file layout markdown to GCS | object=%s", layout_md_object)

    # ── Transcript → PDF → markdown (optional) ──────────────────────────────
    if "transcript" in artifacts:
        transcript_info = artifacts["transcript"]
        transcript_pdf_bytes = _ensure_pdf_bytes(transcript_info["raw"], transcript_info["name"])
        logger.info("Converting transcript to markdown | session=%s", session_id)
        transcript_markdown = _convert_to_markdown(transcript_pdf_bytes, _TRANSCRIPT_CHUNK_PROMPT)

        result["transcript_filename"] = transcript_info["name"]

        transcript_md_stem = Path(transcript_info["name"]).stem
        transcript_md_object = f"{md_prefix}/transcript_{transcript_md_stem}.md"
        upload_text(object_name=transcript_md_object, content=transcript_markdown)
        markdown_uploads.append(transcript_md_object)
        logger.info("Uploaded transcript markdown to GCS | object=%s", transcript_md_object)
    else:
        result["transcript_markdown"] = None
        result["transcript_filename"] = None

    # ── BSA notes (plain text, no conversion needed) ────────────────────────
    if "bsa_notes" in artifacts:
        result["bsa_notes"] = artifacts["bsa_notes"]["raw"].decode("utf-8", errors="replace")
    else:
        result["bsa_notes"] = None

    result["markdown_uploads"] = markdown_uploads

    # ── Stateful chunk-wise extraction ──────────────────────────────────────
    combined_markdown = "\n\n".join(filter(None, [
        brd_markdown,
        layout_markdown,
        result.get("transcript_markdown"),
        result.get("bsa_notes"),
    ]))
    extraction_result = _run_stateful_extraction(
        combined_markdown=combined_markdown,
        session_id=session_id,
        bsa_input=result.get("bsa_notes"),
    )
    result["requirement_layer"] = extraction_result

    # ── Persist final JSON to GCS ────────────────────────────────────────────
    final_object = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/extracted_data/final_requirement_layer.json"
    upload_json(object_name=final_object, payload=extraction_result)
    result["gcs_output_uri"] = f"gs://{config.MAPPING_ARTIFACT_BUCKET}/{final_object}"
    logger.info("Persisted final requirement layer | uri=%s", result["gcs_output_uri"])

    return result


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


def run_validation(session_id: str) -> dict[str, Any]:
    """
    1. Fetch final_requirement_layer.json from GCS (cached extraction)
    2. Fetch cached markdown files from GCS to use as context (no re-extraction)
    3. Cache the combined context via Vertex AI prompt cache (if large enough)
    4. Ask LLM to verify and correct all fields
    5. Persist validated JSON back to GCS
    6. Return validation result dict
    """
    import json

    base_prefix = f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}"
    uploads_prefix = f"{base_prefix}/uploads"
    final_object = f"{base_prefix}/extracted_data/final_requirement_layer.json"
    
    

    try:
        cached_bytes = download_bytes(object_name=final_object)
    except Exception as exc:
        raise FileNotFoundError(
            f"No extracted data found for session_id={session_id!r}. "
            f"Run /extract-brd-information first. Detail: {exc}"
        )

    extracted: dict[str, Any] = _safe_json_load(cached_bytes.decode("utf-8"))

    # Fetch cached markdown files to build context (token-efficient — no re-extraction)
    md_prefix = f"{base_prefix}/markdown_files/"
    md_blobs = list_blobs(prefix=md_prefix)
    context_parts: list[str] = []
    for blob in md_blobs:
        try:
            md_bytes = download_bytes(object_name=blob.name)
            context_parts.append(md_bytes.decode("utf-8", errors="replace"))
        except Exception:
            logger.warning("Could not fetch markdown blob: %s", blob.name)

    bsa_notes_object = f"{uploads_prefix}/bsa_notes.txt"
    try:
        notes_bytes = download_bytes(object_name=bsa_notes_object)
        context_parts.append(notes_bytes.decode("utf-8", errors="replace"))
    except Exception:
        pass

    combined_context = "\n\n".join(context_parts)
    truncated_context = combined_context 

    client = _get_client()

    # Cache the markdown context once for this validation call
    context_cache_name = _create_cache(
        client,
        content=truncated_context,
        display_name=f"validation-context-{session_id}",
    )

    try:
        if context_cache_name:
            # Context is cached — send only the extracted JSON in the prompt
            prompt = _VALIDATION_PROMPT.format(
                context="(see cached context)",
                extracted=json.dumps(extracted, indent=2),
            )
            call_config: dict[str, Any] = {
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "max_output_tokens": 8192,
                "response_schema": response_schema,
                "cached_content": context_cache_name,
            }
        else:
            prompt = _VALIDATION_PROMPT.format(
                context=truncated_context,
                extracted=json.dumps(extracted, indent=2),
            )
            call_config = {
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "max_output_tokens": 8192,
            }

        resp = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=[prompt],
            config=call_config,
        )
    finally:
        if context_cache_name:
            _delete_cache(client, context_cache_name)

    validated: dict[str, Any] = _safe_json_load((resp.text or "{}").strip())
    corrections: list[str] = validated.pop("_validation_corrections", [])
    corrections_made = bool(corrections)

    validated_object = f"{base_prefix}/extracted_data/validated_requirement_layer.json"
    upload_json(object_name=validated_object, payload=validated)
    gcs_uri = f"gs://{config.MAPPING_ARTIFACT_BUCKET}/{validated_object}"
    logger.info("Persisted validated requirement layer | uri=%s corrections=%s", gcs_uri, corrections)

    return {
        "validated_requirement_layer": validated,
        "corrections_made": corrections_made,
        "corrections": corrections,
        "gcs_output_uri": gcs_uri,
    }
