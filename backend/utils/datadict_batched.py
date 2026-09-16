"""
Batched Data Dictionary Generation with Token-Level Streaming

Handles large datasets (1000+ columns) by processing in batches with real-time LLM streaming.
Each batch generates business descriptions for ~50 columns, keeping token usage under limits.
"""

import json
import logging
from typing import List, Dict, Any
from google import genai
from google.genai import types as genai_types
from config.settings import config

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
        max_length: Maximum length observed in the data

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


def _calculate_precision_from_samples(sample_values: List) -> int:
    """
    Calculate precision (decimal places) from sample values.
    Production logic from Legacy system.

    Args:
        sample_values: List of sample values from the column

    Returns:
        Maximum number of decimal places found, 0 if none
    """
    if not sample_values:
        return 0

    max_precision = 0
    for val in sample_values:
        try:
            s = str(val)
            if "." in s:
                decimals = len(s.split(".")[1].rstrip("0"))
                if decimals > max_precision:
                    max_precision = decimals
        except:
            continue

    return max_precision if max_precision > 0 else 0


def _detect_date_format(sample_values: List, data_type: str) -> str:
    """
    Detect date/time format from sample values using pattern matching.
    Production logic for format detection.

    Args:
        sample_values: List of sample date/time values
        data_type: The data type (DATE, DATETIME, TIMESTAMP, TIME)

    Returns:
        Format string (e.g., "YYYY-MM-DD", "YYYY-MM-DD HH:MM:SS")
    """
    if not sample_values or data_type not in ["DATE", "DATETIME", "TIMESTAMP", "TIME"]:
        return "-"

    # Take first non-null sample
    sample = None
    for val in sample_values:
        if val:
            sample = str(val)
            break

    if not sample:
        return "-"

    # Analyze pattern using regex
    import re

    # Common patterns for DATE
    if data_type == "DATE":
        # ISO format: YYYY-MM-DD
        if re.match(r'\d{4}-\d{2}-\d{2}', sample):
            return "YYYY-MM-DD"
        # US format: MM/DD/YYYY
        elif re.match(r'\d{2}/\d{2}/\d{4}', sample):
            return "MM/DD/YYYY"
        # Oracle format: DD-MON-YYYY
        elif re.match(r'\d{2}-[A-Z]{3}-\d{4}', sample, re.IGNORECASE):
            return "DD-MON-YYYY"
        else:
            return "YYYY-MM-DD"  # Default to ISO standard

    # Common patterns for DATETIME
    elif data_type == "DATETIME":
        # ISO 8601 with T separator
        if "T" in sample:
            return "YYYY-MM-DDTHH:MM:SS"
        # Standard format: YYYY-MM-DD HH:MM:SS
        elif re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', sample):
            return "YYYY-MM-DD HH:MM:SS"
        # US format: MM/DD/YYYY HH:MM:SS
        elif re.match(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}', sample):
            return "MM/DD/YYYY HH:MM:SS"
        else:
            return "YYYY-MM-DD HH:MM:SS"  # Default

    # Common patterns for TIMESTAMP
    elif data_type == "TIMESTAMP":
        # Check for microseconds/milliseconds
        if re.search(r'\.\d+', sample):
            # Check for timezone indicator
            if "UTC" in sample or "Z" in sample or re.search(r'[+-]\d{2}:\d{2}$', sample):
                return "YYYY-MM-DD HH:MM:SS.SSS UTC"
            else:
                return "YYYY-MM-DD HH:MM:SS.SSS"
        else:
            return "YYYY-MM-DD HH:MM:SS"

    # TIME format
    elif data_type == "TIME":
        return "HH:MM:SS"

    return "-"


def create_column_batches(technical_data: List[Dict], batch_size: int = 50) -> List[List[Dict]]:
    """
    Split columns into batches for processing.

    Args:
        technical_data: List of all columns with technical info (file_name, field_name, etc.)
        batch_size: Columns per batch (default 50, ~5K output tokens)

    Returns:
        List of batches: [[batch1_50_cols], [batch2_50_cols], ...]
    """
    if not technical_data:
        logger.warning("No technical data to batch")
        return []

    batches = []
    for i in range(0, len(technical_data), batch_size):
        batch = technical_data[i:i + batch_size]
        batches.append(batch)

    logger.info(f"Created {len(batches)} batches from {len(technical_data)} columns (batch_size={batch_size})")
    return batches


def build_datadict_batch_prompt(batch: List[Dict], batch_num: int, total_batches: int) -> str:
    """
    Build LLM prompt for a single batch of columns.

    Generates business-friendly descriptions, infers nullable/default values,
    and creates human-readable field names.

    Args:
        batch: List of ~50 columns to enrich
        batch_num: Current batch number (0-indexed)
        total_batches: Total number of batches

    Returns:
        LLM prompt string optimized for JSON output
    """

    num_columns = len(batch)

    # Convert batch to compact JSON string
    batch_json = json.dumps(batch, indent=2)

    prompt = f"""You are a data dictionary enrichment specialist. Your task is to analyze technical column metadata and generate business-friendly data dictionary entries.

**Batch {batch_num + 1}/{total_batches}** ({num_columns} columns to process)

**Input Technical Metadata:**
```json
{batch_json}
```

**Your Task:**
For EACH column in the input, generate enriched metadata with:

1. **business_name**: Clean, human-readable name (e.g., "livongo_id" → "Livongo Member ID")
2. **field_description**: Clear business description (1-2 sentences explaining purpose/usage)
3. **nullable**: "Yes" or "No" (infer from data if not explicit)
4. **default_value**: Preserve the input default_value as-is (do NOT change it)
5. **primary_key**: "Yes" or "No" (based on uniqueness/naming patterns)
6. **foreign_key**: "Yes" or "No" (based on naming patterns like "_id", "_key")

**Format Detection:**
Analyze the `sample_values` for each column to detect patterns.
- If sample_values show a consistent format pattern, identify it using standard notation (e.g., "YYYY-MM-DD", "MM/DD/YYYY", "HH:MM:SS")
- If no pattern detected, keep format as "-"
- This applies to ALL columns regardless of data_type

**Important:**
- Preserve original fields: file_name, field_name, data_type, length, precision, default_value, most_occurrences
- Set format based on sample_values analysis (replacing the "-" default if pattern detected)
- Do NOT include sample_values in the output JSON (it's for analysis only)
- Add enriched fields as listed above
- Output ONLY valid JSON array (no markdown, no explanations)
- Maintain exact order and count of input columns
- **CRITICAL:** Properly escape ALL special characters in JSON strings:
  * Use \\" for quotes inside strings
  * Use \\n for newlines
  * Use \\\\ for backslashes
  * Ensure all strings are properly terminated with closing quotes
- Keep descriptions concise (max 150 characters) to avoid formatting issues

**Output Format:**
```json
[
  {{
    "file_name": "...",
    "field_name": "...",
    "business_name": "...",
    "data_type": "...",
    "length": 0,
    "precision": 0,
    "format": "-",
    "nullable": "Yes",
    "default_value": "-",
    "most_occurrences": [],
    "primary_key": "No",
    "foreign_key": "No",
    "field_description": "..."
  }}
]
```

Generate the enriched JSON array now:"""

    return prompt


def generate_datadict_markdown(enriched_fields: List[Dict]) -> str:
    """
    Generate markdown table from enriched fields.

    Args:
        enriched_fields: List of enriched field dictionaries

    Returns:
        Markdown formatted table string
    """
    if not enriched_fields:
        return "_No fields to display_"

    markdown = "## Data Dictionary\n\n"
    markdown += "| File Name | Field Name | Field Business Name | Data Type | Length | Format | Nullable | Default Value | Most Occurrences | Primary Key | Foreign Key | Field Description |\n"
    markdown += "|-----------|------------|---------------------|-----------|--------|--------|----------|---------------|------------------|-------------|-------------|-------------------|\n"

    for field in enriched_fields:
        most_occ = field.get('most_occurrences', [])
        most_occ_str = ", ".join(str(v) for v in most_occ) if most_occ else "-"
        markdown += f"| {field.get('file_name', '-')} "
        markdown += f"| {field.get('field_name', '-')} "
        markdown += f"| {field.get('business_name', '-')} "
        markdown += f"| {field.get('data_type', '-')} "
        markdown += f"| {field.get('length', '-')} "
        markdown += f"| {field.get('format', '-')} "
        markdown += f"| {field.get('nullable', '-')} "
        markdown += f"| {field.get('default_value', '-')} "
        markdown += f"| {most_occ_str} "
        markdown += f"| {field.get('primary_key', '-')} "
        markdown += f"| {field.get('foreign_key', '-')} "
        markdown += f"| {field.get('field_description', '-')} |\n"

    return markdown


def generate_datadict_summary(enriched_fields: List[Dict], total_batches: int, total_time_seconds: float) -> str:
    """
    Generate summary statistics for the data dictionary.

    Args:
        enriched_fields: List of enriched field dictionaries
        total_batches: Number of batches processed
        total_time_seconds: Total processing time in seconds

    Returns:
        Summary string
    """
    total_fields = len(enriched_fields)

    # Count data types
    type_counts = {}
    for field in enriched_fields:
        dt = field.get('data_type', 'UNKNOWN')
        type_counts[dt] = type_counts.get(dt, 0) + 1

    summary = f"""# Data Dictionary Generation Complete

**Summary:**
- Total Fields: {total_fields}
- Batches Processed: {total_batches}
- Processing Time: {total_time_seconds:.2f}s

**Data Type Distribution:**
"""
    for dt, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        summary += f"- {dt}: {count} fields\n"

    return summary


# ============================================================================
# BATCHED DATA DICTIONARY GENERATION (Plan 2 - for streaming flow)
# ============================================================================

def batched_data_dictionary_tool(tool_context) -> Dict[str, Any]:
    """
    Generate data dictionary from profiling/relationship data using batched processing.

    This is the BATCHED version for large data flow (is_stream=True).
    Reads data directly from session state via tool_context.

    **Features:**
    - Batched processing (50 fields/batch)
    - Reads profiling/relationship data from session state
    - Compatible with existing datadict_functions.py output

    **Args:**
        tool_context: ADK tool context (provides access to session state)

    **Returns:**
        Dict containing data dictionary fields (same format as standard version)
    """

    logger.info("[batched_data_dictionary_tool] Starting batched DD generation from profiling...")

    try:
        # Read inputs directly from session state via tool_context
        session = tool_context.session

        # Check if vendor DD file exists (if so, agent should NOT have called this tool)
        vendor_dd_path = session.state.get("data_dict_file_path", "")
        if vendor_dd_path:
            logger.warning(f"[batched_data_dictionary_tool] ⚠️ WRONG TOOL CALLED - vendor DD exists: {vendor_dd_path}")
            logger.warning("[batched_data_dictionary_tool] Agent should have called extract_and_map_vendor_dd_streaming instead")
            return {
                "text_response": "Error: Vendor DD file exists. Use vendor extraction tool instead.",
                "tool_response": {
                    "error": "Wrong tool - vendor DD file exists, should use extract_and_map_vendor_dd_streaming",
                    "result": [],
                    "total_fields": 0
                }
            }

        profiling_output = session.state.get("profiling_full_results", [])
        relationships_output = session.state.get("final_relationship_response", {})
        validation_output = {}

        logger.info(f"[batched_data_dictionary_tool] Received profiling_output: {'Yes' if profiling_output else 'No'}")
        logger.info(f"[batched_data_dictionary_tool] Received relationships_output: {'Yes' if relationships_output else 'No'}")

        # Validate required data exists
        if not profiling_output:
            logger.error("[batched_data_dictionary_tool] No profiling_full_results in session state")
            return {
                "text_response": "Error: No profiling results found. Please run profiling analysis first.",
                "tool_response": {
                    "error": "profiling_full_results not found in session state",
                    "result": [],
                    "total_fields": 0
                }
            }

        # ============================================
        # STEP 1: Build technical data from profiling
        # ============================================
        technical_data = _build_technical_data_from_profiling(
            profiling_output,
            relationships_output,
            validation_output
        )

        total_fields = len(technical_data)
        logger.info(f"[batched_data_dictionary_tool] Built technical data: {total_fields} fields")

        # ============================================
        # STEP 2: Batch processing with LLM enrichment
        # ============================================
        batches = create_column_batches(technical_data, batch_size=50)
        total_batches = len(batches)
        logger.info(f"[batched_data_dictionary_tool] Created {total_batches} batches")

        all_enriched_fields = []

        client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )

        # Process each batch synchronously
        for batch_idx, batch in enumerate(batches):
            logger.info(f"[batched_data_dictionary_tool] Processing batch {batch_idx + 1}/{total_batches}")

            # DEBUG: Check if sample_values are present in batch
            if batch and len(batch) > 0:
                first_field = batch[0]
                has_samples = 'sample_values' in first_field
                sample_count = len(first_field.get('sample_values', [])) if has_samples else 0
                logger.info(f"[batched_data_dictionary_tool] DEBUG - First field '{first_field.get('field_name')}': has_sample_values={has_samples}, count={sample_count}, values={first_field.get('sample_values', [])}")

            # Build prompt for this batch
            prompt = build_datadict_batch_prompt(batch, batch_idx, total_batches)

            # Call LLM (synchronous, no streaming)
            response = client.models.generate_content(
                model=config.AGENT_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=16384,  # Increased to handle 50 fields (~200 tokens each = ~10K tokens needed)
                )
            )

            # Parse response with robust error handling
            response_text = response.text.strip()

            # Log raw response for debugging
            logger.info(f"[batched_data_dictionary_tool] Raw LLM response length: {len(response_text)} chars")
            logger.info(f"[batched_data_dictionary_tool] First 200 chars: {response_text[:200]}")

            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            response_text = response_text.strip()

            # Try to parse JSON with multiple fallback strategies
            enriched_batch = None
            parse_error = None

            try:
                # Strategy 1: Direct parse
                enriched_batch = json.loads(response_text)
                logger.info(f"[batched_data_dictionary_tool] ✓ JSON parsed successfully, got {len(enriched_batch)} fields")

                # Verify enrichment actually happened
                if enriched_batch and len(enriched_batch) > 0:
                    sample_field = enriched_batch[0]
                    has_business_name = 'business_name' in sample_field
                    has_description = 'field_description' in sample_field
                    logger.info(f"[batched_data_dictionary_tool] Enrichment check - business_name: {has_business_name}, description: {has_description}")

            except json.JSONDecodeError as e:
                parse_error = e
                logger.warning(f"[batched_data_dictionary_tool] JSON parse failed for batch {batch_idx + 1}: {e}")
                logger.warning(f"[batched_data_dictionary_tool] Response length: {len(response_text)} chars")

                # Save problematic response for debugging
                debug_file = f"datadict_batch_{batch_idx + 1}_error.json"
                try:
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(response_text)
                    logger.info(f"[batched_data_dictionary_tool] Saved problematic response to {debug_file}")
                except:
                    pass

                # Strategy 2: Try to extract valid JSON array
                try:
                    # Find the first [ and last ]
                    start_idx = response_text.find('[')
                    end_idx = response_text.rfind(']')

                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        json_candidate = response_text[start_idx:end_idx + 1]

                        # Try to fix common issues
                        # 1. Replace newlines in strings with escaped newlines
                        import re
                        # This is a basic fix - won't handle all cases but helps

                        enriched_batch = json.loads(json_candidate)
                        logger.info(f"[batched_data_dictionary_tool] ✓ Recovered JSON using array extraction")
                    else:
                        raise ValueError("Could not find JSON array boundaries")

                except Exception as e2:
                    logger.error(f"[batched_data_dictionary_tool] Array extraction also failed: {e2}")

                    # Strategy 3: Fallback - use original batch data without enrichment
                    logger.warning(f"[batched_data_dictionary_tool] Falling back to non-enriched data for batch {batch_idx + 1}")
                    enriched_batch = batch  # Use original technical data

            # Validate parsed data
            if enriched_batch is None:
                logger.error(f"[batched_data_dictionary_tool] All parsing strategies failed for batch {batch_idx + 1}")
                # Use original batch as last resort
                enriched_batch = batch
            elif not isinstance(enriched_batch, list):
                logger.error(f"[batched_data_dictionary_tool] Parsed data is not a list: {type(enriched_batch)}")
                enriched_batch = batch

            all_enriched_fields.extend(enriched_batch)
            logger.info(f"[batched_data_dictionary_tool] Batch {batch_idx + 1} complete: {len(enriched_batch)} fields")

        # ============================================
        # STEP 3: Generate output
        # ============================================
        logger.info("[batched_data_dictionary_tool] Generating summary...")

        markdown = generate_datadict_markdown(all_enriched_fields)
        summary = generate_datadict_summary(all_enriched_fields, total_batches, 0)

        logger.info(f"[batched_data_dictionary_tool] Generation complete: {len(all_enriched_fields)} fields")

        # Return final dict (same format as standard version)
        return {
            "text_response": summary + "\n\n" + markdown,
            "tool_response": {
                "result": all_enriched_fields,
                "source": "generated_from_profiling",
                "total_fields": len(all_enriched_fields)
            }
        }

    except Exception as e:
        logger.error(f"[batched_data_dictionary_tool] Error: {e}")
        import traceback
        traceback.print_exc()

        # Return error dict
        return {
            "text_response": f"Error generating data dictionary: {str(e)}",
            "tool_response": {
                "error": str(e),
                "result": [],
                "total_fields": 0
            }
        }


def _build_technical_data_from_profiling(
    profiling_output: List[Dict],
    relationships_output: Dict,
    validation_output: Dict
) -> List[Dict]:
    """
    Extract technical column metadata from profiling results.

    Converts profiling analysis into the standard field format expected by
    the data dictionary generation process.

    Args:
        profiling_output: List of profiling results per table
        relationships_output: Relationship analysis results
        validation_output: Validation results (if available)

    Returns:
        List of technical field dictionaries with structure:
        {
            "file_name": "table_name",
            "field_name": "column_name",
            "data_type": "STRING|INTEGER|FLOAT|DATE|...",
            "length": 0,
            "precision": 0,
            "format": "-"
        }
    """

    logger.info("[_build_technical_data_from_profiling] Extracting technical metadata from profiling...")

    technical_fields = []

    for table_result in profiling_output:
        if table_result.get("status") != "success":
            logger.warning(f"Skipping failed profiling result: {table_result.get('table_reference')}")
            continue

        table_name = table_result.get("table_reference", "unknown_table")
        column_analysis = table_result.get("column_analysis", {})
        default_value_analysis = table_result.get("default_value_analysis", {})

        for col_name, col_stats in column_analysis.items():
            # Extract data type
            data_type = col_stats.get("data_type", "STRING").upper()

            # Map BigQuery types to standard types
            type_mapping = {
                "INT64": "INTEGER",
                "FLOAT64": "FLOAT",
                "NUMERIC": "NUMERIC",
                "BIGNUMERIC": "NUMERIC",
                "BOOL": "BOOLEAN",
                "STRING": "STRING",
                "BYTES": "STRING",
                "DATE": "DATE",
                "DATETIME": "DATETIME",
                "TIME": "DATETIME",
                "TIMESTAMP": "TIMESTAMP"
            }

            standard_type = type_mapping.get(data_type, data_type)

            # Calculate length using Legacy's buffering logic
            max_length = int(col_stats.get("max_length", col_stats.get("avg_length", 0)))
            calculated_length = _calculate_buffered_length(standard_type, max_length)

            # Calculate precision from sample values for numeric types
            sample_values = col_stats.get("distinct_values_sample", [])
            if standard_type in ["FLOAT", "DECIMAL", "NUMERIC"]:
                calculated_precision = _calculate_precision_from_samples(sample_values)
            else:
                calculated_precision = 0

            # Format detection is handled by LLM (not function-based)
            # LLM will analyze sample_values to detect date/time formats regardless of data_type
            detected_format = "-"

            # Extract default value: only populate if it's the sole value across all rows
            default_val_info = default_value_analysis.get(col_name, {})
            total_rows_count = default_val_info.get("total_rows", 0)
            default_count = default_val_info.get("default_count", 0)
            raw_default = default_val_info.get("default_value")
            if raw_default is not None and total_rows_count > 0 and default_count >= total_rows_count:
                default_value = str(raw_default)
            else:
                default_value = "-"

            # most_occurrences: top-N distinct sample values for this column
            top_n = getattr(config, "DD_MOST_OCCURRENCES_TOP_N", 5)
            most_occurrences = [str(v) for v in (sample_values[:top_n] if sample_values else [])]

            # Build technical field entry
            field_entry = {
                "file_name": table_name.split(".")[-1],
                "field_name": col_name,
                "data_type": standard_type,
                "length": calculated_length,
                "precision": calculated_precision,
                "format": detected_format,
                "default_value": default_value,
                "most_occurrences": most_occurrences,
                "sample_values": sample_values[:5] if sample_values else []
            }

            technical_fields.append(field_entry)

    logger.info(f"[_build_technical_data_from_profiling] Extracted {len(technical_fields)} fields")

    return technical_fields
