"""Snake_case target field names for EDI decode / JSON export."""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def to_snake_case(label: str) -> str:
    text = (label or "").strip().lower()
    text = _NON_ALNUM.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "field"


def target_for_element(element_id: str, element_name: str) -> str:
    """Companion-guide element name → snake_case target key."""
    base = to_snake_case(element_name)
    if base.startswith("element_"):
        return element_id.replace("-", "_").lower()
    # HI01-1, HI01-2 → hi01_1_code_list_qualifier_code style
    if "-" in element_id and re.match(r"^[A-Z]{2}\d+-\d+$", element_id):
        prefix = element_id.replace("-", "_").lower()
        return f"{prefix}_{base}" if base else prefix
    # Prefer stable id prefix when the name repeats (e.g. multiple health_care_code_information)
    eid = element_id.lower()
    if base in {
        "health_care_code_information",
        "reference_information",
        "date_time_period",
    }:
        return f"{eid}_{base}" if not base.startswith(eid) else base
    return base
