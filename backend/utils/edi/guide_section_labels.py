"""Guide section titles → JSON/UI context labels (e.g. Transaction header)."""

from __future__ import annotations

import re

_PAREN_SUFFIX = re.compile(r"^(.+?)\s*\([^)]+\)\s*$")


def normalize_section_title_for_label(title: str) -> str:
    """``Transaction header (BHT)`` → ``Transaction header``; keep LOOP titles intact."""
    text = (title or "").strip()
    if not text or text.lower() == "all segments":
        return ""
    m = _PAREN_SUFFIX.match(text)
    if m:
        return m.group(1).strip()
    return text


def json_segment_label(segment: dict, section_title: str | None) -> str:
    guide = normalize_section_title_for_label(section_title or "")
    if guide:
        return guide
    party = (segment.get("segment_label") or "").strip()
    if party:
        return party
    return (segment.get("segment_name") or segment.get("segment_id") or "").strip()
