'''
BRD extraction using multi pass generation, to avoid consistent output for 400-500 rows DD
'''
import io
import json
import time
from typing import List, Dict, Any, Optional, Tuple
from pypdf import PdfReader, PdfWriter
import os
import uuid
import re
import logging
import sys
from docx2pdf import convert
from google import genai
from google.genai.types import Part
from fastapi.concurrency import run_in_threadpool
import pandas as pd
from pathlib import Path
from config.settings import config
from utils.profiling_artifact_store import materialize_profiling_artifact

if sys.platform == "win32":
    import pythoncom

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Configs (tune these for your environment)
DEFAULT_CHUNK_SIZE = 5          # pages per chunk (increase for fewer LLM calls)
DEFAULT_PAGE_CALL_MAX_TOKENS = 32768
DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 1.0
DATA_DIR = "data"

# -------------------------
# Utility helpers
# -------------------------
def get_genai_client(project: str, location: str):
    return genai.Client(vertexai=True, project=project, location=location)

def split_pdf_into_chunks(pdf_path: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> List[bytes]:
    """
    Splits PDF into chunks of N pages and returns list of PDF bytes.
    """
    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    chunks = []
    for start in range(0, total_pages, chunk_size):
        writer = PdfWriter()
        end = min(start + chunk_size, total_pages)
        for i in range(start, end):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())
    return chunks

def dedupe_columns(columns):
    seen = {}
    new_cols = []
    for col in columns:
        if col not in seen:
            seen[col] = 0
            new_cols.append(col)
        else:
            seen[col] += 1
            new_cols.append(f"{col}_{seen[col]}")
    return new_cols

def _safe_json_load(txt: str) -> Any:
    """
    Robust JSON loader for LLM output.
    """
    if not txt or not txt.strip():
        raise ValueError("Empty model response")
    original = txt
    txt = txt.strip()
    # remove fences if present
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.I)
    txt = re.sub(r"\s*```$", "", txt, flags=re.I)
    # remove trailing commas
    txt = re.sub(r",\s*}", "}", txt)
    txt = re.sub(r",\s*\]", "]", txt)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        # try to extract first top-level JSON object
        start = txt.find("{")
        end = txt.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = txt[start:end+1]
            candidate = re.sub(r",\s*}", "}", candidate)
            candidate = re.sub(r",\s*\]", "]", candidate)
            return json.loads(candidate)
        logger.error("Failed to parse JSON. Raw model output:\n%s", original)
        raise

# -------------------------
# Improved chunk prompt
# -------------------------
def build_chunk_prompt(chunk_index: int, page_range: str, context_open_table: Optional[Dict[str, Any]] = None) -> str:
    """
    Build a deterministic, strict JSON prompt for a document chunk.
    Key rules:
    - If an earlier chunk had an open table, model must decide whether this chunk continues it
    - Only mark table_closed=true when the model is confident that that table ends here.
    - When continuing a table: DO NOT repeat headers.
    - For complete tables: return headers + rows and include a markdown_snippet for the whole table.
    """
    ctx = ""
    if context_open_table:
        ctx = f"""
PREVIOUS_CHUNK_OPEN_TABLE_CONTEXT:
- heading: {json.dumps(context_open_table.get("heading"))}
- headers: {json.dumps(context_open_table.get("headers"))}
- last_row: {json.dumps(context_open_table.get("rows")[-1] if context_open_table.get("rows") else [])}

USE THE ABOVE CONTEXT to determine whether the current chunk CONTINUES that table or STARTS A NEW TABLE.
"""
    # explicit rules and small example
    rules = f"""
You are a STRICT machine-readable JSON API that extracts ALL tables found in the provided PDF chunk.
You will be given one PDF chunk that corresponds to pages {page_range}.

RESPONSE SCHEMA (MUST FOLLOW EXACTLY):
{{
  "chunk_index": {chunk_index},
  "page_range": "{page_range}",
  "continues_previous_table": true|false,   // only true if the FIRST table in 'tables' is a continuation of an open table from the previous chunk
  "table_closed": true|false,               // only true if the last table in this chunk definitely ends within this chunk
  "tables": [
    {{
      "heading": "string or null",           // use existing heading if present, otherwise null
      "headers": ["string","..."],           // header row (omit if this table is only a continuation)
      "rows": [["cell","cell"], ...],       // rows found in this chunk for this table (if continuing a previous table, RETURN ONLY NEW ROWS)
      "markdown_snippet": "string"          // REQUIRED if this table is complete within this chunk (i.e., doesn't continue); use standard Markdown table format
    }}
  ]
}}

IMPORTANT RULES:
1) NEVER invent columns/headers. If headers are split across pages, attempt to reconstruct header in full. If uncertain, return headers as they appear.
2) If a table spans pages and you are not certain the table ends in this chunk, set table_closed=false and DO NOT include markdown_snippet for that open table (only include rows).
3) Only set continues_previous_table=true when the FIRST table returned actually continues the exact previous table (headers and/or last rows match).
4) Do NOT drop or summarize rows — return all rows exactly as text.
5) Do NOT repeat header row when continuing a previous table.
6) For any table you deem COMPLETE in this chunk, include markdown_snippet that is a valid Markdown table with the exact headers and rows you've returned.
7) If no tables are found, return tables: [].
8) Be conservative: prefer marking a table as OPEN (not closed) unless you can confidently see the table footer or no table-rows follow on the next page.

EXAMPLES (valid minimal responses):
- chunk with continuation:
{{"chunk_index":0,"page_range":"1-2","continues_previous_table":true,"table_closed":false,"tables":[{{"heading":null,"headers":[],"rows":[["123","abc"]],"markdown_snippet":null}}]}}
- chunk with complete table:
{{"chunk_index":1,"page_range":"3-4","continues_previous_table":false,"table_closed":true,"tables":[{{"heading":"Customers","headers":["ID","Name"],"rows":[["1","Alice"],["2","Bob"]],"markdown_snippet":"| ID | Name |\\n|----|------|\\n| 1 | Alice |\\n| 2 | Bob |"}}]}}
"""

    prompt = f"You are an expert Data Engineer extracting tables from a PDF chunk.\n{ctx}\n{rules}"
    return prompt

# -------------------------
# Core extractor (improved)
# -------------------------
def extract_tables_with_gemini_v2(
    file_path: str,
    project: str,
    location: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    page_call_max_tokens: int = DEFAULT_PAGE_CALL_MAX_TOKENS,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY
) -> List[Dict[str, Any]]:

    if sys.platform == "win32":
        pythoncom.CoInitialize()
    try:
        current_chunk_size = chunk_size
        current_max_tokens = page_call_max_tokens
        adaptive_used = False

        while True:
            try:
                logger.info(
                    "Starting extraction with chunk_size=%d, max_output_tokens=%d",
                    current_chunk_size,
                    current_max_tokens
                )

                return _extract_tables_with_gemini_v2_impl(
                    file_path,
                    project,
                    location,
                    current_chunk_size,
                    current_max_tokens,
                    retries,
                    retry_delay
                )

            except Exception as e:
                logger.warning("Extraction attempt failed: %s", str(e))

                # If adaptive already used → re-raise
                if adaptive_used:
                    logger.error("Adaptive retry already used. Raising error.")
                    raise

                # ---- ADAPTIVE REASSESSMENT ----
                adaptive_used = True
                current_chunk_size = max(1, current_chunk_size // 2)
                current_max_tokens = 65000

                logger.warning(
                    "Adaptive retry triggered. "
                    "New chunk_size=%d, max_output_tokens=%d",
                    current_chunk_size,
                    current_max_tokens
                )

                # Loop will retry with new parameters

    finally:
        if sys.platform == "win32":
            pythoncom.CoUninitialize()

def _extract_tables_with_gemini_v2_impl(
    file_path: str,
    project: str,
    location: str,
    chunk_size: int,
    page_call_max_tokens: int,
    retries: int,
    retry_delay: float
) -> List[Dict[str, Any]]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    ext = os.path.splitext(file_path)[1].lower()
    working_file = file_path
    temp_pdf_created = False

    if ext == ".docx":
        pdf_path = file_path.replace(".docx", ".pdf")
        logger.info("Converting DOCX to PDF...")
        convert(file_path, pdf_path)
        working_file = pdf_path
        temp_pdf_created = True

    try:
        chunks = split_pdf_into_chunks(working_file, chunk_size=chunk_size)
        logger.info("Total chunks created: %d", len(chunks))

        genai_client = get_genai_client(project, location)
        stitched_tables: List[Dict[str, Any]] = []
        open_table: Optional[Dict[str, Any]] = None

        total_pages = None
        # we'll attempt to estimate page ranges for debug/readability
        # NOTE: PdfReader used earlier; re-open to get page count
        try:
            reader = PdfReader(working_file)
            total_pages = len(reader.pages)
        except Exception:
            total_pages = None

        # process each chunk sequentially
        for idx, chunk_bytes in enumerate(chunks):
            start_page = idx * chunk_size + 1
            end_page = min((idx + 1) * chunk_size, total_pages) if total_pages else (idx + 1) * chunk_size
            page_range = f"{start_page}-{end_page}"

            context_open_table = open_table if open_table else None
            prompt = build_chunk_prompt(idx, page_range, context_open_table)

            part = Part.from_bytes(data=chunk_bytes, mime_type="application/pdf")

            success = False
            last_exception = None
            for attempt in range(retries + 1):
                try:
                    logger.info("Calling Gemini for chunk %d (attempt %d)", idx, attempt + 1)
                    resp = genai_client.models.generate_content(
                        model="gemini-2.5-pro",
                        contents=[part, prompt],
                        config={
                            "temperature": 0.0,
                            "max_output_tokens": page_call_max_tokens,
                            "response_mime_type": "application/json"
                        },
                    )
                    raw = (resp.text or "").strip()
                    logger.debug("Raw model output (chunk %d): %s", idx, raw[:1000])
                    data = _safe_json_load(raw)
                    # Basic validation
                    if data.get("chunk_index") != idx:
                        logger.warning("Model chunk_index mismatch (expected %d got %s). Using returned value anyway.", idx, data.get("chunk_index"))
                    tables = data.get("tables", []) or []

                    # If no tables, just continue (may still be continuing previous open_table if indicated)
                    if not tables and not data.get("continues_previous_table", False):
                        logger.info("No tables in chunk %d", idx)
                        success = True
                        # If model says table_closed True with no tables, close open table
                        if data.get("table_closed") and open_table:
                            stitched_tables.append(open_table)
                            open_table = None
                        break

                    # iterate through tables in this chunk and merge with state
                    for t_i, tbl in enumerate(tables):
                        heading = tbl.get("heading")
                        headers = tbl.get("headers") or []
                        rows = tbl.get("rows") or []
                        markdown_snippet = tbl.get("markdown_snippet")

                        # continuation applies only to FIRST returned table in chunk per prompt contract
                        if t_i == 0 and data.get("continues_previous_table", False) and open_table:
                            # ensure header compatibility (best-effort)
                            # Do NOT repeat header. Append rows only.
                            logger.info("Chunk %d: continuing previous table (append %d rows) with heading='%s'", idx, len(rows), open_table.get("heading"))
                            # Avoid possible duplicates: if first appended row equals last row, drop it
                            if open_table["rows"] and rows:
                                if rows[0] == open_table["rows"][-1]:
                                    rows = rows[1:]
                            open_table["rows"].extend(rows)
                            # If model says table_closed, finalize it
                            if data.get("table_closed", False):
                                logger.info("Chunk %d: previous table closed here with heading='%s'", idx, open_table.get("heading"))
                                stitched_tables.append(open_table)
                                open_table = None
                            continue

                        # If there is an open table but the first returned table is NOT a continuation,
                        # finalize the open table first (we assume model recognized end in earlier chunk)
                        if open_table:
                            logger.info("Chunk %d: finalizing previously open table before starting a new one", idx)
                            stitched_tables.append(open_table)
                            open_table = None

                        # New table begins in this chunk
                        new_table = {
                            "heading": heading,
                            "headers": headers,
                            "rows": rows
                        }
                        
                        # Log heading information for debugging
                        logger.info(f"Chunk {idx}: Processing table with heading='{heading}', headers={len(headers)}, rows={len(rows)}")

                        # If this table is complete (model set table_closed true OR markdown present),
                        # finalize it immediately.
                        # Note: model may return multiple tables per chunk.
                        if data.get("table_closed", False) or t_i < len(tables) - 1 or markdown_snippet:
                            # final table
                            logger.info("Chunk %d: found complete table with heading=%s rows=%d", idx, heading, len(rows))
                            stitched_tables.append(new_table)
                            open_table = None
                        else:
                            # Table is open and likely continues to next chunk
                            logger.info("Chunk %d: starting open table with heading=%s rows=%d", idx, heading, len(rows))
                            open_table = new_table

                    success = True
                    break

                except Exception as e:
                    last_exception = e
                    logger.warning("Chunk %d failed attempt %d: %s", idx, attempt + 1, str(e))
                    time.sleep(retry_delay)

            if not success:
                logger.error("Chunk %d failed permanently. Last exception: %s", idx, str(last_exception))
                # best-effort: continue to next chunk but note failure
                continue

        # after all chunks, if any open table remains, close it
        if open_table:
            logger.info("Final open table auto-closed at document end")
            stitched_tables.append(open_table)
            open_table = None

        # Post-processing: cleanup repeated header-rows and duplicates
        processed = []
        seen_signatures = set()
        for tbl in stitched_tables:
            headers = tbl.get("headers") or []
            rows = tbl.get("rows") or []
            # drop rows that equal headers (pagination artifacts)
            cleaned_rows = [r for r in rows if r != headers]
            # drop empty rows
            cleaned_rows = [r for r in cleaned_rows if any(cell not in (None, "", "nan", "None") for cell in r)]
            # dedupe sequential duplicate rows (common when overlapping)
            deduped_rows = []
            for r in cleaned_rows:
                if not deduped_rows or r != deduped_rows[-1]:
                    deduped_rows.append(r)
            tbl["rows"] = deduped_rows

            # create a signature to dedupe identical tables across document
            sig = (tuple(headers), tuple(tuple(r) for r in deduped_rows[:3]))  # headers + first 3 rows
            if sig in seen_signatures:
                logger.info("Skipping duplicate table with headers=%s", headers)
                continue
            seen_signatures.add(sig)
            processed.append(tbl)

        stitched_tables = processed

        # build a final consistent markdown for all stitched tables
        markdown_pieces = []
        for tbl in stitched_tables:
            heading = tbl.get("heading") or "UNKNOWN_TABLE"
            headers = tbl.get("headers") or []
            rows = tbl.get("rows") or []
            # build markdown table safely — escape pipes in cells
            def esc(cell):
                if cell is None:
                    return ""
                return str(cell).replace("|", "\\|")
            if not headers and rows:
                # try to infer header count from first row
                header_count = len(rows[0])
                headers = [f"col_{i+1}" for i in range(header_count)]
            # header line
            header_line = "| " + " | ".join(esc(h) for h in headers) + " |"
            sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
            row_lines = []
            for r in rows:
                # align row length
                if len(r) < len(headers):
                    r = r + [""] * (len(headers) - len(r))
                elif len(r) > len(headers):
                    r = r[:len(headers)]
                row_lines.append("| " + " | ".join(esc(c) for c in r) + " |")
            md = "\n".join([
                "===================================",
                f"### {heading}",
                "",
                header_line,
                sep_line,
                *row_lines
            ])
            markdown_pieces.append(md)

        full_markdown = "\n\n".join(markdown_pieces)

        # Debug prints (optional)
        logger.info("STATEFUL EXTRACTION COMPLETE — %d tables stitched", len(stitched_tables))

        return {
            "stitched_tables": stitched_tables,
            "markdown": full_markdown
        }

    finally:
        if temp_pdf_created and os.path.exists(working_file):
            os.remove(working_file)
            logger.info("Temporary PDF removed")

# -------------------------
# Classification (unchanged but tightened prompt)
# -------------------------
def identify_all_data_dictionary_tables_v3(
    stitched_tables: List[Dict[str, Any]],
    project: str,
    location: str
) -> List[Dict[str, Any]]:
    if not stitched_tables:
        raise ValueError("No tables available to classify")

    descriptors = []
    for i, t in enumerate(stitched_tables):
        headers = t.get("headers") or []
        rows = t.get("rows") or []
        sample_rows = rows[:]
        descriptors.append({
            "table_id": f"table_id_{i}",
            "index": i,
            "heading": t.get("heading"),
            "headers": headers,
            "sample_rows": sample_rows
        })

    prompt = f"""
You are a highly precise DATA DICTIONARY classifier.

You will be given multiple extracted tables with sample rows (top 5). Your job is to return only those tables which are TRUE DATA DICTIONARIES.

A TRUE DATA DICTIONARY:
- contains column-level metadata: data type, length/size, nullable/required, description, constraints, positions for fixed-width, or field numbers.
- is NOT sample transactional data (IDs, dates, amounts), KPI summaries, or revision history.

Return EXACT JSON only:
{{ "found_any": true|false, "table_ids": ["table_id_0", ...] }}

Tables:
{json.dumps(descriptors, indent=2)}
"""

    genai_client = get_genai_client(project, location)
    resp = genai_client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[prompt],
        config={
            "temperature": 0.0,
            "max_output_tokens": 4000,
            "response_mime_type": "application/json"
        }
    )
    payload = _safe_json_load(resp.text or "")
    if not payload.get("found_any"):
        raise ValueError("No data dictionary tables found")
    table_ids = payload.get("table_ids", [])
    valid = []
    for tid in table_ids:
        try:
            idx = int(tid.replace("table_id_", ""))
            valid.append(stitched_tables[idx])
        except Exception:
            continue
    if not valid:
        raise ValueError("Classifier returned invalid table IDs")
    return valid

from typing import List

async def extract_dd_from_brd_v2(
    brd_file_path: str,
    project=config.GOOGLE_CLOUD_PROJECT,
    location=config.GOOGLE_CLOUD_LOCATION
) -> List[Dict[str, Any]]:

    res = await run_in_threadpool(
        extract_tables_with_gemini_v2,
        brd_file_path,
        project,
        location
    )

    stitched = res["stitched_tables"]
    markdown = res["markdown"]

    # classify DD tables
    dd_tables = await run_in_threadpool(
        identify_all_data_dictionary_tables_v3,
        stitched,
        project,
        location
    )

    dd_paths = []

    brd_name = Path(brd_file_path).stem
    brd_name = re.sub(r"[^A-Za-z0-9_\-]+", "_", brd_name)

    for i, table in enumerate(dd_tables):
        headers = table.get("headers", [])
        rows = table.get("rows", [])
        heading = table.get("heading")  # Extract the heading

        if not headers or not rows:
            continue

        df = pd.DataFrame(rows, columns=headers)
        df.columns = dedupe_columns(df.columns)

        # Ensure heading is properly captured and passed
        table_heading = heading if heading else f"Table {i+1}"
        logger.info(f"Extracted DD table {i} with heading: '{table_heading}'")
        
        # Add Section column as the first column
        df.insert(0, 'Section', table_heading)
        
        # Add extraction order column to preserve table sequence (for internal use only)
        df.insert(1, 'extraction_order', i)

        # sanitization (skip Section and extraction_order columns)
        for col in df.columns:
            if col not in ['Section', 'extraction_order']:
                df[col] = (
                    df[col]
                    .astype(str)
                    .str.replace(r"[\x00-\x1f]", "", regex=True)
                    .str.strip()
                    .replace({"": None, "nan": None, "None": None})
                )

        dd_path = Path(DATA_DIR) / f"{brd_name}_dd_table_{i}_{uuid.uuid4().hex[:8]}.xlsx"
        df.to_excel(dd_path, index=False)

        # Create display columns list (exclude extraction_order for UI)
        display_columns = [col for col in df.columns if col != 'extraction_order']

        dd_paths.append({
            "table_index": i,
            "heading": table_heading,  # Ensure heading is always included
            "extraction_order": i,  # Add extraction order to metadata
            "row_count": len(df),
            "columns": display_columns,  # Use filtered columns for UI display
            "file_path": str(dd_path),
            "sample_rows": rows[:]
        })

    if not dd_paths:
        raise ValueError("No valid data dictionary rows extracted")

    # Save markdown for debugging
    md_path = Path(DATA_DIR) / f"{brd_name}_extracted_tables_{uuid.uuid4().hex}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    logger.info("Saved extracted markdown to: %s", md_path)

    return dd_paths

async def resolve_metadata_path(
    *,
    uploaded_info: dict,
    brd_file_path: Optional[str],
    session_id: Optional[str] = None,
) -> tuple[Optional[str], Dict[str, Any]]:
    """
    Priority:
    1. Uploaded Data Dictionary
    2. Extract from BRD (best effort)
    3. None (model-generated downstream)
    
    Returns: (metadata_path, brd_status_dict)
    """
    brd_status = {
        "brd_exists": bool(brd_file_path),
        "extraction_attempted": False,
        "extraction_success": False,
        "error_description": None
    }

    # ---------------------------
    # CASE 1 — UPLOADED DD
    # ---------------------------
    if uploaded_info.get("data_dict_file_path"):
        logger.info("DD RESOLUTION: Using uploaded data dictionary")
        return uploaded_info["data_dict_file_path"], brd_status

    # ---------------------------
    # CASE 2 — BRD EXTRACTION
    # ---------------------------
    if brd_file_path:
        logger.info("DD RESOLUTION: Attempting DD extraction from BRD")
        brd_status["extraction_attempted"] = True

        try:
            materialized_brd_path = materialize_profiling_artifact(brd_file_path)
            dd_candidates = await extract_dd_from_brd_v2(str(materialized_brd_path))
            brd_status["extraction_success"] = True
            brd_status["dd_candidates"] = dd_candidates
            return dd_candidates, brd_status
        except Exception as e:
            error_msg = str(e)
            logger.warning(
                "DD RESOLUTION: BRD extraction failed → fallback | reason=%s",
                error_msg,
            )
            brd_status["error_description"] = error_msg
            return None, brd_status

    # ---------------------------
    # CASE 3 — FALLBACK
    # ---------------------------
    logger.info("DD RESOLUTION: No DD available, proceeding without metadata")
    return None, brd_status
