"""
Native Gemini Document Understanding for Vendor Data Dictionary Extraction

Uses Google Gemini's native file processing capabilities to extract and map
vendor data dictionaries to standard format. Supports PDF, CSV, Excel files
without manual parsing.

Part of Plan 2 - Improved Native Approach
Zero impact on existing normal flow (datadict_functions.py)
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, AsyncGenerator, Optional
from google import genai
from google.genai import types as genai_types
from google.adk.tools import ToolContext
from config.settings import config

# Reuse existing batching functions (DRY principle)
from utils.datadict_batched import (
    create_column_batches,
    generate_datadict_markdown,
    generate_datadict_summary
)

logger = logging.getLogger(__name__)


# ============================================================================
# HELPER FUNCTIONS (From Legacy datadict_functions.py - Production-Tested)
# ============================================================================

def _calculate_buffered_length(data_type: str, max_length: int = 0) -> int:
    """
    Calculates a buffered string length based on the actual max length.
    Production logic from Legacy system.

    Args:
        data_type: The data type (STRING, INTEGER, etc.)
        max_length: Maximum length observed/specified

    Returns:
        Buffered length for STRING types, 0 for others
    """
    if data_type == 'STRING':
        # Apply production business rules
        if not max_length or max_length == 0:
            return 50
        if max_length < 20:
            return 50
        if max_length < 100:
            return 255
        return int(max_length * 1.5) + 50
    # For non-string types, length is not applicable
    return 0


def _extract_length_from_type(vendor_type: str) -> int:
    """
    Extract length from vendor type specification (e.g., VARCHAR(255) -> 255).

    Args:
        vendor_type: Vendor's type specification

    Returns:
        Length value if specified, 0 otherwise
    """
    import re
    # Match patterns like VARCHAR(255), CHAR(10), STRING(50)
    match = re.search(r'\((\d+)\)', vendor_type)
    if match:
        return int(match.group(1))  # Return the length (first number)
    return 0


def _extract_precision_from_type(vendor_type: str) -> int:
    """
    Extract precision from vendor type specification (e.g., DECIMAL(10,2) -> 2).

    Args:
        vendor_type: Vendor's type specification

    Returns:
        Precision value if specified, 0 otherwise
    """
    import re
    # Match patterns like DECIMAL(10,2) or NUMERIC(18,4)
    match = re.search(r'\((\d+),(\d+)\)', vendor_type)
    if match:
        return int(match.group(2))  # Return the precision (second number)
    return 0


def extract_and_map_vendor_dd_streaming(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Extract vendor data dictionary using Gemini's native document understanding.
    Reads file path from session state via tool_context.

    **Features:**
    - Native PDF/CSV/Excel parsing (no manual code)
    - Gemini handles OCR, rotated text, merged cells
    - Schema-enforced output via structured prompts
    - Batched processing (50 fields/batch)
    - Reads file_path from session state (NO dependency on profiling)

    **Args:**
        tool_context: ADK tool context (automatically injected, provides access to session state)

    **Returns:**
        Dict with text_response and tool_response (same format as batched version)
    """

    # Read file_path directly from session state via tool_context
    logger.info("[datadict_native_streaming] Tool called - checking session state...")
    logger.info(f"[datadict_native_streaming] tool_context type: {type(tool_context)}")
    logger.info(f"[datadict_native_streaming] has session attr: {hasattr(tool_context, 'session')}")

    session = tool_context.session
    logger.info(f"[datadict_native_streaming] session type: {type(session)}")
    logger.info(f"[datadict_native_streaming] session.state keys: {list(session.state.keys())}")

    file_path = session.state.get("data_dict_file_path", "")
    logger.info(f"[datadict_native_streaming] data_dict_file_path value: '{file_path}'")

    if not file_path:
        logger.error("[datadict_native_streaming] No data_dict_file_path in session state")
        logger.error(f"[datadict_native_streaming] Available keys: {list(session.state.keys())}")
        return {
            "text_response": "Error: No vendor DD file path found in session state",
            "tool_response": {"error": "data_dict_file_path not found in session state"}
        }

    logger.info(f"[datadict_native_streaming] Starting vendor DD extraction: {file_path}")

    try:
        # ============================================
        # PHASE 1: UPLOAD FILE TO GEMINI
        # ============================================
        logger.info("[datadict_native_streaming] Uploading vendor DD file to Gemini...")

        # Initialize Gemini client
        client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )

        # Detect MIME type
        file_path_obj = Path(file_path)
        mime_map = {
            '.pdf': 'application/pdf',
            '.csv': 'text/csv',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.xls': 'application/vnd.ms-excel',
            '.txt': 'text/plain'
        }
        mime_type = mime_map.get(file_path_obj.suffix.lower(), 'application/octet-stream')

        # Determine if file is local or GCS
        is_gcs = file_path.startswith('gs://')

        if is_gcs:
            # GCS files - download and process
            logger.info(f"[datadict_native_streaming] Downloading GCS file: {file_path}")

            try:
                from google.cloud import storage
                import io

                # Parse GCS path: gs://bucket/path/to/file
                gcs_path = file_path.replace('gs://', '')
                bucket_name, blob_path = gcs_path.split('/', 1)

                # Download file to memory
                storage_client = storage.Client(project=config.GOOGLE_CLOUD_PROJECT)
                bucket = storage_client.bucket(bucket_name)
                blob = bucket.blob(blob_path)

                file_bytes = blob.download_as_bytes()
                logger.info(f"[datadict_native_streaming] Downloaded {len(file_bytes)} bytes from GCS")

                # Handle Excel/CSV - convert to PDF
                if file_path_obj.suffix.lower() in ['.xlsx', '.xls', '.csv']:
                    logger.info(f"[datadict_native_streaming] Converting GCS {file_path_obj.suffix} to PDF...")

                    import pandas as pd
                    from reportlab.lib.pagesizes import landscape, A4
                    from reportlab.lib import colors
                    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
                    from reportlab.lib.styles import getSampleStyleSheet
                    from reportlab.lib.units import inch

                    # Read into DataFrame
                    if file_path_obj.suffix.lower() == '.csv':
                        df = pd.read_csv(io.BytesIO(file_bytes))
                    else:  # Excel
                        df = pd.read_excel(io.BytesIO(file_bytes))

                    logger.info(f"[datadict_native_streaming] Loaded {len(df)} rows, {len(df.columns)} columns")

                    # Create PDF in memory
                    pdf_buffer = io.BytesIO()
                    doc = SimpleDocTemplate(pdf_buffer, pagesize=landscape(A4))
                    elements = []

                    # Add title
                    styles = getSampleStyleSheet()
                    title = Paragraph(f"<b>Data Dictionary: {file_path_obj.name}</b>", styles['Title'])
                    elements.append(title)
                    elements.append(Spacer(1, 0.2*inch))

                    # Convert to table
                    table_data = [df.columns.tolist()] + df.fillna('-').astype(str).values.tolist()
                    num_cols = len(df.columns)
                    page_width = landscape(A4)[0] - 2*inch
                    col_width = page_width / num_cols

                    table = Table(table_data, colWidths=[col_width] * num_cols)
                    table.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 10),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                        ('FONTSIZE', (0, 1), (-1, -1), 8),
                        ('GRID', (0, 0), (-1, -1), 1, colors.black),
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ]))

                    elements.append(table)
                    doc.build(elements)

                    pdf_bytes = pdf_buffer.getvalue()
                    logger.info(f"[datadict_native_streaming] GCS file converted to PDF: {len(pdf_bytes)} bytes")

                    file_part = genai_types.Part.from_bytes(data=pdf_bytes, mime_type='application/pdf')

                elif file_path_obj.suffix.lower() == '.pdf':
                    # PDF - use as-is
                    file_part = genai_types.Part.from_bytes(data=file_bytes, mime_type='application/pdf')
                    logger.info(f"[datadict_native_streaming] GCS PDF loaded: {len(file_bytes)} bytes")

                else:
                    logger.error(f"[datadict_native_streaming] Unsupported GCS file format: {file_path_obj.suffix}")
                    return {
                        "text_response": f"Error: Unsupported file format {file_path_obj.suffix}",
                        "tool_response": {"error": f"Unsupported format: {file_path_obj.suffix}"}
                    }

            except Exception as gcs_error:
                logger.error(f"[datadict_native_streaming] GCS download/conversion error: {gcs_error}")
                import traceback
                traceback.print_exc()
                return {
                    "text_response": f"Error downloading from GCS: {str(gcs_error)}",
                    "tool_response": {"error": f"GCS error: {str(gcs_error)}"}
                }
        else:
            # Local files - handle conversion to PDF for Excel/CSV
            logger.info(f"[datadict_native_streaming] Reading local file: {file_path_obj.name}, MIME: {mime_type}")

            # Check if file needs PDF conversion
            if file_path_obj.suffix.lower() in ['.xlsx', '.xls', '.csv']:
                logger.info(f"[datadict_native_streaming] Converting {file_path_obj.suffix} to PDF for Gemini processing...")

                try:
                    import pandas as pd
                    from reportlab.lib.pagesizes import letter, A4, landscape
                    from reportlab.lib import colors
                    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
                    from reportlab.lib.styles import getSampleStyleSheet
                    from reportlab.lib.units import inch
                    import io

                    # Read file into DataFrame
                    if file_path_obj.suffix.lower() == '.csv':
                        df = pd.read_csv(file_path)
                    else:  # .xlsx or .xls
                        df = pd.read_excel(file_path)

                    logger.info(f"[datadict_native_streaming] Loaded {len(df)} rows, {len(df.columns)} columns")

                    # Create PDF in memory
                    pdf_buffer = io.BytesIO()

                    # Use landscape A4 for better table display
                    doc = SimpleDocTemplate(pdf_buffer, pagesize=landscape(A4))
                    elements = []

                    # Add title
                    styles = getSampleStyleSheet()
                    title = Paragraph(f"<b>Data Dictionary: {file_path_obj.name}</b>", styles['Title'])
                    elements.append(title)
                    elements.append(Spacer(1, 0.2*inch))

                    # Convert DataFrame to table data (all values as strings)
                    table_data = [df.columns.tolist()] + df.fillna('-').astype(str).values.tolist()

                    # Create table with adjusted column widths
                    num_cols = len(df.columns)
                    page_width = landscape(A4)[0] - 2*inch  # Account for margins
                    col_width = page_width / num_cols

                    table = Table(table_data, colWidths=[col_width] * num_cols)

                    # Style the table
                    table.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 10),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                        ('FONTSIZE', (0, 1), (-1, -1), 8),
                        ('GRID', (0, 0), (-1, -1), 1, colors.black),
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ]))

                    elements.append(table)

                    # Build PDF
                    doc.build(elements)

                    # Get PDF bytes
                    pdf_bytes = pdf_buffer.getvalue()
                    logger.info(f"[datadict_native_streaming] PDF generated: {len(pdf_bytes)} bytes")

                    # Create Part from PDF bytes
                    file_part = genai_types.Part.from_bytes(data=pdf_bytes, mime_type='application/pdf')

                except Exception as conv_error:
                    logger.error(f"[datadict_native_streaming] PDF conversion failed: {conv_error}")
                    import traceback
                    traceback.print_exc()
                    return {
                        "text_response": f"Error converting file to PDF: {str(conv_error)}",
                        "tool_response": {"error": f"PDF conversion failed: {str(conv_error)}"}
                    }

            elif file_path_obj.suffix.lower() == '.pdf':
                # PDF files - pass as-is
                with open(file_path, 'rb') as f:
                    file_bytes = f.read()
                file_part = genai_types.Part.from_bytes(data=file_bytes, mime_type='application/pdf')
                logger.info(f"[datadict_native_streaming] PDF read successfully: {len(file_bytes)} bytes")

            else:
                # Unsupported format
                logger.error(f"[datadict_native_streaming] Unsupported file format: {file_path_obj.suffix}")
                return {
                    "text_response": f"Error: Unsupported file format {file_path_obj.suffix}. Supported: PDF, Excel (.xlsx, .xls), CSV",
                    "tool_response": {"error": f"Unsupported format: {file_path_obj.suffix}"}
                }

        # ============================================
        # PHASE 2: EXTRACT RAW FIELDS
        # ============================================
        logger.info("[datadict_native_streaming] Extracting field definitions from vendor document...")

        # Extraction prompt (declarative, not hardcoded parsing)
        extraction_prompt = """Extract ALL field definitions from this data dictionary document.

**For EACH field in the document, extract:**
- Table/File name
- Column/Field name
- Data type (keep vendor's original type name exactly as written, e.g., VARCHAR(255), INT, DECIMAL(10,2))
- Max length or size (if specified)
- Nullable/Required indicator (Yes/No/Required/Optional/NULL/NOT NULL)
- Default value (if specified)
- Primary Key indicator (PK/Primary/Key)
- Foreign Key indicator (FK/Foreign/Reference)
- Description or comments

**Output Format:**
Return as JSON array with this exact structure:
```json
[
  {
    "table": "table_or_file_name",
    "field": "column_name",
    "vendor_type": "VARCHAR(255)",
    "length_specified": "255",
    "nullable": "Yes",
    "default_value": "N/A",
    "primary_key": "No",
    "foreign_key": "No",
    "description": "Field description from document"
  }
]
```

**Important:**
- Extract EVERY field from the document (do not skip any)
- Keep vendor's original type names (do NOT convert to standard types yet)
- If a field is missing in the document, use "N/A" or "-"
- Maintain the order as they appear in the document
- Handle multi-page documents (extract all pages)

Process the uploaded document now:
"""

        logger.info("[datadict_native_streaming] Calling Gemini for extraction...")

        # Gemini extracts natively (handles PDF OCR, table detection, etc.)
        response = client.models.generate_content(
            model=config.AGENT_MODEL,
            contents=[
                genai_types.Part(text=extraction_prompt),
                file_part  # Either from_uri (GCS) or from_bytes (local)
            ],
            config=genai_types.GenerateContentConfig(
                temperature=0.1,  # Low temperature for accuracy
                response_mime_type="application/json"
            )
        )

        raw_fields = json.loads(response.text)
        total_fields = len(raw_fields)

        logger.info(f"[datadict_native_streaming] Extracted {total_fields} raw fields")

        # ============================================
        # PHASE 3: MAP TO STANDARD SCHEMA
        # ============================================
        logger.info("[datadict_native_streaming] Starting batch mapping to standard schema...")

        # Create batches (reuse existing function)
        batches = create_column_batches(raw_fields, batch_size=50)
        total_batches = len(batches)

        all_mapped_fields = []

        for batch_idx, batch in enumerate(batches):
            batch_num = batch_idx + 1
            logger.info(f"[datadict_native_streaming] Processing batch {batch_num}/{total_batches} ({len(batch)} fields)...")

            # Build mapping prompt (declarative type mapping rules)
            mapping_prompt = f"""Map these {len(batch)} vendor fields to standard data dictionary format.

**Vendor Fields:**
{json.dumps(batch, indent=2)}

**Standard Type Mapping Rules:**
- VARCHAR/CHAR/TEXT/STRING → STRING
- INT/INTEGER/BIGINT/SMALLINT/TINYINT → INTEGER
- DECIMAL/NUMERIC/NUMBER → NUMERIC (preserve precision if specified)
- FLOAT/REAL/DOUBLE/DOUBLE PRECISION → FLOAT
- DATE → DATE
- DATETIME/TIMESTAMP → DATETIME
- TIME → TIMESTAMP
- BOOLEAN/BOOL/BIT → BOOLEAN

**Length Calculation for STRING Types:**
If vendor specifies length (e.g., VARCHAR(255)):
- Use vendor's length exactly

If vendor does NOT specify length or uses MAX:
- If likely identifier (id, key, code): use 50
- If likely name/description (name, title): use 255
- If likely long text (comments, notes): use 1000

**Business Name Transformation:**
- Remove underscores and special characters
- Convert to Title Case
- Examples:
  - "customer_id" → "Customer ID"
  - "member_first_name" → "Member First Name"
  - "claim_amount_usd" → "Claim Amount USD"

**Output Format (MUST match exactly):**
Return JSON with this structure:
{{
  "result": [
    {{
      "file_name": "table_name",
      "field_name": "column_name_as_is",
      "business_name": "Column Name",
      "data_type": "STRING",
      "length": 255,
      "precision": 0,
      "format": "-",
      "nullable": "Yes",
      "default_value": "-",
      "primary_key": "No",
      "foreign_key": "No",
      "field_description": "Description from vendor DD"
    }}
  ]
}}

**CRITICAL RULES FOR field_description:**
- **ALWAYS preserve the vendor's original description/comments EXACTLY as provided**
- **DO NOT rephrase, summarize, or rewrite vendor descriptions**
- **DO NOT add new information or interpretations**
- If vendor provided "Comments" or "Description" → use it verbatim
- If vendor didn't provide description → use "-" (do NOT generate one)
- The vendor description is authoritative - your job is to map it, not change it

**Other Important Rules:**
- Preserve ALL {len(batch)} fields in exact order
- Use "Yes" or "No" for nullable, primary_key, foreign_key (not null/true/false)
- Use "-" for default_value if not specified
- For DATE/DATETIME types, use appropriate format pattern (YYYY-MM-DD, etc.)
- Preserve vendor's data type names when mapping (e.g., varchar(16) → STRING, length: 16)

Generate the standardized output now:
"""

            # Call LLM for mapping (non-streaming)
            logger.info(f"[datadict_native_streaming] Calling Gemini for batch {batch_num}/{total_batches}...")

            batch_response = client.models.generate_content(
                model=config.AGENT_MODEL,
                contents=mapping_prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.3,
                    response_mime_type="application/json"
                )
            )

            # Parse batch result
            try:
                batch_result = json.loads(batch_response.text)
                mapped_fields = batch_result.get("result", [])

                if len(mapped_fields) != len(batch):
                    logger.warning(
                        f"[datadict_native_streaming] Batch {batch_num}: Expected {len(batch)} fields, got {len(mapped_fields)}"
                    )

                # ENFORCE ALL REQUIRED FIELDS (even if LLM omits them)
                # Ensure every field has the complete structure
                for field in mapped_fields:
                    # Enforce optional fields with defaults
                    if "default_value" not in field:
                        field["default_value"] = "-"
                    if "format" not in field:
                        field["format"] = "-"
                    if "precision" not in field:
                        field["precision"] = 0

                    # Log if we had to add missing fields (for debugging)
                    missing_fields = []
                    if "default_value" not in field:
                        missing_fields.append("default_value")
                    if "format" not in field:
                        missing_fields.append("format")
                    if missing_fields:
                        logger.debug(f"[datadict_native_streaming] Added missing fields {missing_fields} to field '{field.get('field_name', 'unknown')}'")

                # ============================================
                # APPLY LEGACY LENGTH & PRECISION CALCULATIONS
                # ============================================
                # Recalculate length and precision using production-tested Legacy logic
                # This ensures consistency with the standard flow
                for idx, field in enumerate(mapped_fields):
                    # Match this field with its corresponding raw vendor field to get vendor_type
                    raw_field = batch[idx] if idx < len(batch) else {}
                    vendor_type = raw_field.get("vendor_type", "")
                    data_type = field.get("data_type", "STRING")

                    # CALCULATE LENGTH (for STRING types)
                    if data_type == "STRING":
                        # Extract length from vendor type specification (e.g., VARCHAR(255) -> 255)
                        extracted_length = _extract_length_from_type(vendor_type)

                        # Apply Legacy's buffering logic
                        buffered_length = _calculate_buffered_length(data_type, extracted_length)
                        field["length"] = buffered_length

                        logger.debug(
                            f"[datadict_native_streaming] Field '{field.get('field_name')}': "
                            f"vendor_type={vendor_type}, extracted_length={extracted_length}, "
                            f"buffered_length={buffered_length}"
                        )
                    else:
                        # Non-STRING types: length is not applicable
                        field["length"] = 0

                    # CALCULATE PRECISION (for NUMERIC/DECIMAL/FLOAT types)
                    if data_type in ["NUMERIC", "DECIMAL", "FLOAT"]:
                        # Extract precision from vendor type specification (e.g., DECIMAL(10,2) -> 2)
                        extracted_precision = _extract_precision_from_type(vendor_type)
                        field["precision"] = extracted_precision

                        logger.debug(
                            f"[datadict_native_streaming] Field '{field.get('field_name')}': "
                            f"vendor_type={vendor_type}, precision={extracted_precision}"
                        )
                    else:
                        # Non-numeric types: precision is 0
                        field["precision"] = 0

                all_mapped_fields.extend(mapped_fields)
                logger.info(f"[datadict_native_streaming] Batch {batch_num}/{total_batches} complete: {len(mapped_fields)} fields mapped")

            except json.JSONDecodeError as e:
                logger.error(f"[datadict_native_streaming] Failed to parse batch {batch_num} response: {e}")
                logger.error(f"Response (first 500 chars): {batch_response.text[:500]}")
                # Continue with other batches even if one fails

        # ============================================
        # PHASE 4: GENERATE OUTPUT
        # ============================================
        logger.info("[datadict_native_streaming] Generating final output...")

        # Generate markdown and summary (reuse existing functions)
        markdown = generate_datadict_markdown(all_mapped_fields)
        summary = generate_datadict_summary(all_mapped_fields, total_batches, 0)

        logger.info(f"[datadict_native_streaming] Extraction complete: {len(all_mapped_fields)} total fields")

        # Return final result (same format as batched_data_dictionary_tool)
        return {
            "text_response": summary + "\n\n" + markdown,
            "tool_response": {
                "result": all_mapped_fields,  # Use "result" to match batched tool
                "source": "vendor_upload",
                "total_fields": len(all_mapped_fields),
                "source_file": file_path
            }
        }

    except Exception as e:
        logger.error(f"[datadict_native_streaming] Error: {e}")
        import traceback
        traceback.print_exc()

        return {
            "text_response": f"Error processing vendor data dictionary: {str(e)}",
            "tool_response": {
                "error": str(e),
                "error_details": traceback.format_exc()
            }
        }
