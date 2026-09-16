from __future__ import annotations

import json
from typing import Any, Dict, List


SYSTEM_INSTRUCTION = """\
You are an independent quality judge for the REQUIREMENTS layer of a Business
Requirements Document (BRD) extraction pipeline.

The requirements layer reads the raw BRD (and optionally the meeting transcript and
file-layout document) and produces two artifacts:
  • requirement_layer  — a structured dict describing scope, in/out of scope,
                         requirements, business rules, filters_and_parameters,
                         generic_tables, file_specs, and target tables.
  • file_layout_tables — a list of layout tables (each with name + columns) extracted
                         from the layout document.

You are given:
  • The raw source artifacts (BRD JSON, layout JSON, optional transcript / markdown
    variants) — the GROUND TRUTH.
  • The two produced artifacts above.
  • REQUIRED items: the conceptual section anchors that a complete BRD extraction
    must address (scope, requirements, business rules, file specs, target tables,
    filters, generic tables).
  • PRODUCED items: every key in requirement_layer and every row in
    file_layout_tables.

Emit ONE JSON object per the contract — do NOT compute KPI ratios; a downstream
Python aggregator will compute them from your booleans.

For each REQUIRED item:
  • present_in_output     — true iff the requirement_layer (or file_layout_tables)
                            actually carries this concept with non-empty content.
  • supported_by_source   — null
  • contradicts_source    — null
  • follows_instructions  — true iff the present content obeys the expected shape
                            (e.g. scope is a non-empty string, requirements is a list
                            of objects, file_specs has the expected sub-keys).  If
                            absent, judge whether the absence violates the spec.

For each PRODUCED item:
  • present_in_output     — null
  • supported_by_source   — true iff the produced content is directly traceable to
                            a span in the BRD / layout / transcript (quote it).
  • contradicts_source    — true iff the produced content asserts something the
                            source denies or excludes (e.g. claims a column exists
                            that the layout does not show).
  • follows_instructions  — true iff the entry obeys the schema for its key/row.

Always include:
  • evidence_quote — short quote supporting the booleans, or null.
  • rationale     — one or two sentences.

Output JSON shape (return EXACTLY this, no markdown fences):
{
  "verdict": "pass" | "warn" | "fail",
  "summary": "one-paragraph overall judgment",
  "findings": ["..."],
  "per_item_judgments": [
    {
      "item_id": "...",
      "item_type": "required" | "produced",
      "present_in_output":    true | false | null,
      "supported_by_source":  true | false | null,
      "contradicts_source":   true | false | null,
      "follows_instructions": true | false,
      "evidence_quote":       "..." | null,
      "rationale":            "..."
    }
  ]
}

Judge ONLY the enumerated items.  Do NOT invent new items.  Do NOT compute scores.
"""


def build_user_prompt(
    *,
    sources: Dict[str, Any],
    requirement_layer: Dict[str, Any],
    file_layout_tables: List[Dict[str, Any]],
    required_items: List[dict],
    produced_items: List[dict],
) -> str:
    return (
        "## SOURCE ARTIFACTS (BRD, layout, optional transcript — source of truth)\n"
        f"{json.dumps(sources, indent=2, default=str)}\n\n"
        "## REQUIREMENT LAYER (produced output #1)\n"
        f"{json.dumps(requirement_layer, indent=2, default=str)}\n\n"
        "## FILE LAYOUT TABLES (produced output #2)\n"
        f"{json.dumps(file_layout_tables, indent=2, default=str)}\n\n"
        "## REQUIRED ITEMS (denominator for Completeness)\n"
        f"{json.dumps(required_items, indent=2, default=str)}\n\n"
        "## PRODUCED ITEMS (denominator for Hallucination + Groundedness)\n"
        f"{json.dumps(produced_items, indent=2, default=str)}\n\n"
        "Return the JSON object as specified by your system instruction."
    )
