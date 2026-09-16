"""
Prompt builders for the two per-chunk LLM calls.

Call 1 — extraction_prompt()    : typed section extraction into predefined JSON buckets.
                                   PDF bytes are sent; output tokens are bounded because
                                   we are converting PDF → structured JSON (same as
                                   PDF → markdown, just with instructions added).
Call 2 — domain_scoring_prompt(): text-only domain classification. Cheap, no PDF bytes.

Design principle: the two calls reason about completely different things so they
cannot contaminate each other and cause hallucination.
"""
from __future__ import annotations

import json
from typing import Optional

from .models import ChunkContext

_ALL_DOMAINS = [
    "healthcare", "insurance", "finance", "retail",
    "logistics", "hr", "legal",
    "generic_data_dictionary", "technical_specification", "other",
]

# ---------------------------------------------------------------------------
# Call-1 output schema (shown inline in the prompt as a concrete example)
# ---------------------------------------------------------------------------
_EXTRACTION_SCHEMA = """\
{
  "chunk_index": <int>,
  "page_range": "<string>",

  "requirements": [
    {
      "id": "<string | null>",
      "category": "<string | null>",
      "description": "<string>",
      "priority": "<string | null>",
      "source": "<string | null>"
    }
  ],

  "in_scope": [
    { "description": "<string>", "notes": "<string | null>" }
  ],

  "out_of_scope": [
    { "description": "<string>", "notes": "<string | null>" }
  ],

  "file_layout": [
    {
      "field_name": "<string>",
      "position_start": "<string | null>",
      "position_end":   "<string | null>",
      "length":         "<string | null>",
      "data_type":      "<string | null>",
      "format":         "<string | null>",
      "nullable":       "<string | null>",
      "default_value":  "<string | null>",
      "description":    "<string | null>",
      "constraints":    "<string | null>",
      "section":        "<string | null>",
      "extra":          { "<col>": "<value>" }
    }
  ],

  "generic_tables": [
    {
      "heading":         "<string | null>",
      "headers":         ["<col1>", "..."],
      "rows":            [["<cell>", "..."], ...],
      "is_continuation": <true|false>,
      "is_complete":     <true|false>
    }
  ],

  "open_section": {
    "section_type":          "<requirements|in_scope|out_of_scope|file_layout|generic_table|null>",
    "heading":               "<string | null>",
    "headers":               ["<col1>", "..."],
    "last_row":              ["<cell>", "..."],
    "last_item_description": "<string | null>"
  },

  "handoff_summary": "<1-3 sentences — section type, heading, last row/item>"
}"""

# ---------------------------------------------------------------------------
# Call-2 output schema
# ---------------------------------------------------------------------------
_DOMAIN_SCHEMA = """\
{
  "chunk_index": <int>,
  "scores": {
    "healthcare": <0-10>, "insurance": <0-10>, "finance": <0-10>,
    "retail": <0-10>, "logistics": <0-10>, "hr": <0-10>,
    "legal": <0-10>, "generic_data_dictionary": <0-10>,
    "technical_specification": <0-10>, "other": <0-10>
  },
  "top_domain": "<label>",
  "rationale": "<1-2 sentences>"
}"""


# ---------------------------------------------------------------------------
# Call 1 — extraction prompt
# ---------------------------------------------------------------------------

def extraction_prompt(
    chunk_index: int,
    page_range: str,
    context: ChunkContext,
) -> str:
    """
    Build the Call-1 extraction prompt.

    Injects the previous chunk's handoff summary and open-section state so the
    model can continue mid-section content without loss.

    Token cost note: we are asking the model to convert PDF content to structured
    JSON — equivalent to PDF→markdown conversion. The additional instructions add
    negligible output tokens because the content volume is the same either way.
    """
    context_block = ""

    if context.previous_handoff_summary:
        context_block += f"\nPREVIOUS CHUNK SUMMARY:\n{context.previous_handoff_summary}\n"

    if context.open_section:
        os_ = context.open_section
        open_info: dict = {"section_type": os_.section_type}
        if os_.heading:
            open_info["heading"] = os_.heading
        if os_.headers:
            open_info["headers"] = os_.headers
            open_info["last_row"] = os_.last_row
        if os_.last_item_description:
            open_info["last_item_description"] = os_.last_item_description
        context_block += (
            "\nOPEN SECTION FROM PREVIOUS CHUNK (may continue here):\n"
            + json.dumps(open_info, indent=2) + "\n"
        )

    return f"""\
You are a STRICT JSON extraction API. Extract ALL content from the PDF chunk \
covering pages {page_range} into the predefined output schema below.
{context_block}
OUTPUT SCHEMA — return ONLY valid JSON matching this structure exactly \
(no markdown fences, no extra keys, no extra text):
{_EXTRACTION_SCHEMA}

SECTION CLASSIFICATION RULES:
1. requirements[]
   - Any item labelled as a requirement, business rule, functional spec, \
acceptance criterion, or constraint expressed as a statement of need.
   - Capture id (e.g. "REQ-001"), category, priority, and source heading if present.
   - If a requirements list is continuing from the previous chunk \
(see OPEN SECTION above), append new items — do NOT repeat items already extracted.

2. in_scope[] / out_of_scope[]
   - Items explicitly listed under "In Scope", "Scope", "Inclusions", \
"Out of Scope", "Exclusions", or equivalent headings.
   - Each bullet or row is one ScopeItem.

3. file_layout[]
   - ANY table describing file/record structure: fixed-width layouts, \
delimited field specs, EDI segment maps, API payload schemas, \
database column definitions, data dictionary tables.
   - Capture ALL columns present. Map them to the closest named field \
(field_name, position_start, position_end, length, data_type, format, \
nullable, default_value, description, constraints, section).
   - Put any column that does not map to a named field into extra{{}}.
   - section field = record type label if the layout has header/detail/trailer rows \
(e.g. "Header", "Detail", "Trailer").
   - If a file layout table is continuing from the previous chunk, \
set is_continuation=true on the first record and DO NOT repeat rows \
already extracted. Continue from the row AFTER last_row in OPEN SECTION.

4. generic_tables[]
   - All other tables that do not fit the above categories.
   - Preserve heading, headers, and all rows exactly.
   - Set is_continuation=true if this table continues from OPEN SECTION.
   - Set is_complete=false if the table clearly continues beyond the last page.

5. open_section
   - If ANY section (requirements list, scope list, file layout, or generic table) \
is still in progress at the END of this chunk, populate open_section with:
     * section_type: one of requirements | in_scope | out_of_scope | file_layout | generic_table
     * heading: the section/table heading
     * headers + last_row (for table types)
     * last_item_description (for list types)
   - Set open_section to null if all sections are complete.

6. handoff_summary
   - 1-3 sentences. State: which section is open (if any), its heading, \
and the last row/item so the next chunk can continue seamlessly.
   - If nothing is open: "All sections complete in this chunk."

GENERAL RULES:
- DO NOT invent content. Extract only what is present in the PDF.
- DO NOT truncate or summarise rows/items — return everything.
- DO NOT repeat content already captured in a previous chunk \
(use OPEN SECTION context to determine the continuation point).
- chunk_index MUST equal {chunk_index}.
"""


# ---------------------------------------------------------------------------
# Call 2 — domain scoring prompt
# ---------------------------------------------------------------------------

def domain_scoring_prompt(
    chunk_index: int,
    page_range: str,
    extraction_summary: str,
) -> str:
    """
    Build the Call-2 domain scoring prompt.

    Receives only a compact text summary of what was extracted — no PDF bytes.
    This keeps the call cheap and prevents extraction reasoning from bleeding
    into domain classification.
    """
    domains_list = ", ".join(_ALL_DOMAINS)
    return f"""\
You are a document domain classifier. Based ONLY on the extraction summary below \
for pages {page_range}, assign a confidence score (0-10) to EACH domain:
{domains_list}

EXTRACTION SUMMARY:
{extraction_summary}

OUTPUT SCHEMA — return ONLY valid JSON (no markdown fences, no extra text):
{_DOMAIN_SCHEMA}

RULES:
1. ALL domain keys in "scores" MUST be present — use 0 if clearly not applicable.
2. Scores accumulate across chunks; use a consistent scale across all calls.
3. top_domain must be the key with the highest score in this chunk.
4. rationale must cite specific terminology from the summary (e.g. field names, \
section headings, domain-specific jargon).
5. chunk_index MUST equal {chunk_index}.
"""


# ---------------------------------------------------------------------------
# Compact summary builder (Call-1 output → Call-2 input)
# ---------------------------------------------------------------------------

def build_extraction_summary(
    chunk_index: int,
    page_range: str,
    n_requirements: int,
    n_in_scope: int,
    n_out_of_scope: int,
    n_file_layout: int,
    n_generic_tables: int,
    handoff: str,
    open_section_type: Optional[str],
) -> str:
    """
    Produce a compact text summary of Call-1 output to feed into Call-2.
    Avoids re-sending PDF bytes to the domain-scoring call.
    """
    parts = [f"Chunk {chunk_index} (pages {page_range}):"]
    if n_requirements:
        parts.append(f"{n_requirements} requirement(s)")
    if n_in_scope:
        parts.append(f"{n_in_scope} in-scope item(s)")
    if n_out_of_scope:
        parts.append(f"{n_out_of_scope} out-of-scope item(s)")
    if n_file_layout:
        parts.append(f"{n_file_layout} file-layout record(s)")
    if n_generic_tables:
        parts.append(f"{n_generic_tables} other table(s)")
    if open_section_type:
        parts.append(f"open section: {open_section_type}")
    parts.append(f"Handoff: {handoff}")
    return " | ".join(parts)
