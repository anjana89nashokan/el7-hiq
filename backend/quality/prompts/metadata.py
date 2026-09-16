from __future__ import annotations

import json
from typing import Any, Dict, List


SYSTEM_INSTRUCTION = """\
You are an independent quality judge for the METADATA extraction layer of a Business
Requirements Document (BRD) pipeline.

The metadata extractor takes:
  • A validated BRD requirement_layer JSON (scope, requirements, file_specs, target tables).
  • A file_layout JSON (tables + columns of the input files).
And produces:
  • extracted_filespecs — dict-of-fields describing each filespec (frequency, delimiter,
                          encoding, etc.).
  • extracted_file1     — one or more file-level records each containing an `attributes`
                          array with column name, data type, length, nullable, position,
                          and other per-column metadata.

You are given:
  • The two source artifacts (BRD requirement_layer + layout) — the GROUND TRUTH.
  • The extracted_metadata output.
  • An enumerated REQUIRED list (every attribute the BRD + layout demand) and a
    PRODUCED list (every entry actually emitted by the metadata extractor).

Emit ONE JSON object with per-item judgments — do NOT compute KPI ratios; a downstream
Python aggregator will do that from your booleans.

For each REQUIRED item:
  • present_in_output     — true iff the produced metadata covers this required field.
  • supported_by_source   — null  (only meaningful for produced items)
  • contradicts_source    — null
  • follows_instructions  — true iff its representation (where present) obeys the
                            metadata template: correct field names, data types, lengths,
                            nullable flag, and position respect the layout.  If absent,
                            judge whether the absence violates the template's required
                            fields.

For each PRODUCED item:
  • present_in_output     — null
  • supported_by_source   — true iff the BRD or layout directly attests to this
                            attribute (you can quote a span).
  • contradicts_source    — true iff the produced value disagrees with the source
                            (e.g. data type mismatch with layout, length differs).
  • follows_instructions  — true iff the entry obeys the template: required template
                            fields are populated, types match the allowed enum,
                            attribute position is consistent.

Always include:
  • evidence_quote — short quote from BRD/layout supporting the booleans, or null.
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
    brd_requirement_layer: Dict[str, Any],
    layout: Dict[str, Any],
    extracted_metadata: Dict[str, Any],
    required_items: List[dict],
    produced_items: List[dict],
) -> str:
    return (
        "## BRD REQUIREMENT LAYER (source of truth)\n"
        f"{json.dumps(brd_requirement_layer, indent=2, default=str)}\n\n"
        "## FILE LAYOUT (source of truth)\n"
        f"{json.dumps(layout, indent=2, default=str)}\n\n"
        "## EXTRACTED METADATA (output of the metadata extractor)\n"
        f"{json.dumps(extracted_metadata, indent=2, default=str)}\n\n"
        "## REQUIRED ITEMS (denominator for Completeness)\n"
        f"{json.dumps(required_items, indent=2, default=str)}\n\n"
        "## PRODUCED ITEMS (denominator for Hallucination + Groundedness)\n"
        f"{json.dumps(produced_items, indent=2, default=str)}\n\n"
        "Return the JSON object as specified by your system instruction."
    )
