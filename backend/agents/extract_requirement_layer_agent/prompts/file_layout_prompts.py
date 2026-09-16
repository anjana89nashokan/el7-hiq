# ---------------------------------------------------------------------------
# LLM extraction prompt
# ---------------------------------------------------------------------------

_LAYOUT_CHUNK_PROMPT = """\
You are a precise document extraction assistant specialising in file layout specifications.

Extract ALL tables from this PDF chunk (pages {page_range}) as structured JSON.

OUTPUT FORMAT — return a single JSON object where:
- Each key is the EXACT table header / section heading as it appears in the document
- Each value is an array of row objects, where each row object maps column headers to cell values
- Preserve ALL rows — do not skip or summarise any row
- Preserve exact column header text
- If a cell spans multiple columns, repeat the value under each column header
- If a section has no tabular data but has key-value pairs, represent as [{{"key": "...", "value": "..."}}]
- If a table continues from the previous chunk, use the same key and continue appending rows

Previous chunk ended with:
{handoff}

Return STRICT JSON only. No markdown fences, no commentary.
"""

_LAYOUT_VALIDATION_PROMPT = """
You are a precise document validation assistant for file layout specifications.

You are reviewing pages {page_range} of the original source document alongside
the currently extracted file layout tables.

Your tasks:
- Verify that every table visible in this page range is present in the extracted JSON.
- Check that all column headers and row values are accurate and complete.
- Correct any missing tables, missing rows, misaligned columns, truncated values, or OCR errors.
- Do NOT remove or modify tables that belong to pages outside this page range — you cannot
  see those pages, so preserve them exactly as they are in the current JSON.
- Do NOT invent data that is not present in the source document.
- If a table spans multiple pages and is partially visible, extract only the rows visible
  in this page range and merge them with whatever already exists in the current JSON.

Return the FULL corrected file_layout_tables JSON object (same top-level key structure).
Also include a "_validation_corrections" key whose value is a list of strings describing
every change you made. If no changes were needed, return an empty list for that key.

CURRENT EXTRACTED JSON:
{extracted}
"""