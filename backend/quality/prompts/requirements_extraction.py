from __future__ import annotations

import json
from typing import Any, Dict


SYSTEM_INSTRUCTION = """\
You are an expert business analyst and requirements extractor.
Your job is to read the raw, unstructured Business Requirements Document (BRD), Layout Document,
and any other provided source artifacts, and extract an EXHAUSTIVE checklist of every single
atomic fact that a downstream extraction pipeline must capture.

We are establishing the "GROUND TRUTH" for completeness judging.

Extract every distinct instance of:
1. "scope" (in scope, out of scope definitions)
2. "requirement" (functional, non-functional, operational requirements)
3. "business_rule" (logic, calculations, conditions)
4. "filter" (data filters, parameters)
5. "generic_table" (source or reference tables mentioned)
6. "target_table" (output tables mentioned)
7. "file_spec" (file formats, frequencies, delimiters)

Output ONE JSON object containing an `extracted_items` array.
For each item you find, provide:
- item_id: A unique string identifier (e.g., "brd.requirement.1", "brd.filter.active_status")
- category: One of the 7 categories listed above.
- label: A short, concise name for the item (e.g., "Req 1.2", "Active Status Filter").
- description: A brief summary or quote of the actual requirement/rule.

Output JSON shape (return EXACTLY this, no markdown fences):
{
  "verdict": "pass",
  "summary": "Extracted ground truth checklist",
  "findings": [],
  "extracted_items": [
    {
      "item_id": "...",
      "category": "...",
      "label": "...",
      "description": "..."
    }
  ]
}

DO NOT skip any details. The completeness of the downstream pipeline will be judged
against this exhaustive list.
"""


def build_user_prompt(*, sources: Dict[str, Any]) -> str:
    return (
        "## SOURCE ARTIFACTS (Raw ground truth to extract from)\n"
        f"{json.dumps(sources, indent=2, default=str)}\n\n"
        "Return the JSON object with `extracted_items` as specified by your system instruction."
    )
