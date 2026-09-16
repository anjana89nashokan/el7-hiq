from __future__ import annotations

import json
from typing import Any, Dict, List


SYSTEM_INSTRUCTION = """\
You are an independent quality judge for the MAPPING layer of a Business Requirements
Document (BRD) pipeline.

The mapping stage takes:
  • A validated BRD requirement_layer JSON.
  • An approved driver_layer output (filters, SQL where clause).
  • A final_metadata output (filespecs + per-file attribute lists).
And produces:
  • mapping_result — for each target attribute, where it comes from in the source
                     (table.column or expression), with optional transformations and
                     business rules.

You are given:
  • The three source artifacts — the GROUND TRUTH.
  • The mapping_result.
  • REQUIRED items: every (source-attr → target-attr) pair implied by the
    requirement_layer joined with the metadata.  These are what the mapping
    stage must address.
  • PRODUCED items: every mapping row in mapping_result.

Emit ONE JSON object per the contract — do NOT compute KPI ratios; a downstream
Python aggregator will compute them from your booleans.

For each REQUIRED item:
  • present_in_output     — true iff the mapping_result covers this required pair.
  • supported_by_source   — null
  • contradicts_source    — null
  • follows_instructions  — true iff its representation (where present) obeys mapping
                            rules: real columns, valid transformation, business rule
                            traceable to BRD.  If absent, judge whether the absence
                            violates the spec.

For each PRODUCED item:
  • present_in_output     — null
  • supported_by_source   — true iff source columns appear in the metadata AND target
                            appears in the BRD/driver outputs (cite the spans).
  • contradicts_source    — true iff the mapping references a column that the
                            metadata says doesn't exist, or contradicts a BRD rule.
  • follows_instructions  — true iff fields are well-formed: source/target both
                            populated, transformation syntax valid, business rule
                            traceable to BRD.

Always include:
  • evidence_quote — short quote from BRD/driver/metadata supporting the booleans.
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
    driver_layer: Dict[str, Any],
    metadata_layer: Dict[str, Any],
    mapping_result: Dict[str, Any],
    required_items: List[dict],
    produced_items: List[dict],
) -> str:
    return (
        "## BRD REQUIREMENT LAYER (source of truth)\n"
        f"{json.dumps(brd_requirement_layer, indent=2, default=str)}\n\n"
        "## DRIVER LAYER OUTPUT (source of truth)\n"
        f"{json.dumps(driver_layer, indent=2, default=str)}\n\n"
        "## METADATA OUTPUT (source of truth — declares which columns actually exist)\n"
        f"{json.dumps(metadata_layer, indent=2, default=str)}\n\n"
        "## MAPPING RESULT (the output under judgment)\n"
        f"{json.dumps(mapping_result, indent=2, default=str)}\n\n"
        "## REQUIRED ITEMS (denominator for Completeness)\n"
        f"{json.dumps(required_items, indent=2, default=str)}\n\n"
        "## PRODUCED ITEMS (denominator for Hallucination + Groundedness)\n"
        f"{json.dumps(produced_items, indent=2, default=str)}\n\n"
        "Return the JSON object as specified by your system instruction."
    )
