"""Build hierarchical JSON from decoded 837 messages (targets + file order)."""

from __future__ import annotations

from typing import Any

from .guide_section_labels import json_segment_label


def _element_value(el: dict[str, Any]) -> Any:
    children = el.get("children") or []
    if children:
        nested: dict[str, Any] = {}
        for child in children:
            nested[child["target"]] = child.get("value", "")
        raw = (el.get("value") or "").strip()
        if raw:
            nested["_raw"] = raw
        return nested
    return el.get("value", "")


def _segment_fields(segment: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for el in segment.get("elements") or []:
        key = el.get("target") or el.get("element_id", "field").lower()
        n = counts.get(key, 0)
        counts[key] = n + 1
        out_key = key if n == 0 else f"{key}_{n + 1}"
        fields[out_key] = _element_value(el)
    return fields


def _section_to_dict(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_id": section.get("section_id"),
        "title": section.get("title"),
        "segments": [
            {
                "segment_id": seg.get("segment_id"),
                "sequence": seg.get("sequence"),
                "segment_name": seg.get("segment_name"),
                "segment_label": json_segment_label(
                    seg,
                    seg.get("guide_section_title") or section.get("title"),
                ),
                "party_code": seg.get("party_code"),
                "fields": _segment_fields(seg),
            }
            for seg in section.get("segments") or []
        ],
    }


def message_to_export_dict(message: dict[str, Any]) -> dict[str, Any]:
    sections = message.get("sections")
    if not sections:
        sections = [
            {
                "section_id": "all",
                "title": "All segments",
                "segments": message.get("segments") or [],
            }
        ]
    return {
        "source_file": message.get("source_file"),
        "transaction_set": message.get("transaction_set"),
        "implementation_guide": message.get("implementation_guide"),
        "control_id": message.get("control_id"),
        "sections": [_section_to_dict(s) for s in sections],
    }


def decoded_corpus_to_json(decoded: dict[str, Any]) -> dict[str, Any]:
    files_out = []
    for file_entry in decoded.get("files") or []:
        files_out.append({
            "filename": file_entry.get("filename"),
            "messages": [message_to_export_dict(m) for m in file_entry.get("messages") or []],
        })
    return {
        "format": "x12_837_decode",
        "guide_reference": decoded.get("guide_reference"),
        "files": files_out,
    }
