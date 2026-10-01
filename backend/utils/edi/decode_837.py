"""Decode X12 837 interchanges into ordered segment tables (companion guide semantics)."""

from __future__ import annotations

from typing import Any

from .format_meanings import semantic_meaning
from .guides import ELEMENT_DEFS, SEGMENT_NAMES, lookup_code as _lookup_code
from .parser import Delimiters, InterchangeMessage, Segment

_GUIDE_REF = "837-health-care-claim-companion-guide (005010X222/X223/X224)"


def _element_id(segment: str, position_1based: int) -> str:
    return f"{segment}{position_1based:02d}"


def _meaning_for_value(
    code_set_id: str | None,
    value: str,
    segment: str,
    position: int,
) -> str:
    if not value.strip():
        return ""
    if code_set_id:
        label = _lookup_code(code_set_id, value)
        if label:
            return label
    if segment == "ST" and position == 1:
        guides = {
            "837": "Health Care Claim (837)",
            "835": "Health Care Claim Payment/Advice (835)",
            "270": "Eligibility Inquiry (270)",
            "271": "Eligibility Response (271)",
        }
        return guides.get(value.strip(), f"Transaction set {value}")
    if segment == "BHT" and position == 1 and value == "0019":
        return "Information Source, Subscriber, Dependent"
    if segment == "CLM" and position == 5 and ":" in value:
        parts = value.split(":")
        pos = _lookup_code("CLM05_POS", parts[0]) if parts else None
        bits = []
        if pos:
            bits.append(f"Facility type {parts[0]}: {pos}")
        if len(parts) > 1:
            bits.append(f"Facility code qualifier: {parts[1]}")
        if len(parts) > 2:
            bits.append(f"Claim frequency type: {parts[2]}")
        return "; ".join(bits) if bits else value
    if segment in ("HI",) and position == 1:
        return _decode_hi_value(value)
    if segment in ("SV1", "SV2", "SV3", "SV5", "SVD") and position == 1:
        return _decode_procedure_composite(value)
    return ""


def _decode_hi_value(value: str) -> str:
    parts = []
    for piece in value.split(":"):
        if not piece:
            continue
        qual = piece[:3] if len(piece) >= 3 else piece
        code = piece[3:] if len(piece) > 3 else ""
        qual_label = _lookup_code("HI01", qual) or qual
        if code:
            parts.append(f"{qual_label} ({qual}): diagnosis {code}")
        else:
            parts.append(qual_label)
    return "; ".join(parts)


def _decode_procedure_composite(value: str) -> str:
    if ":" not in value:
        label = _lookup_code("SV101", value)
        return label or value
    qual, code = value.split(":", 1)
    qual_label = _lookup_code("SV101", qual) or qual
    return f"{qual_label}; procedure/service code {code}"


def _decode_element(
    segment: str,
    position: int,
    value: str,
    name: str,
    code_set_id: str | None,
    sibling_fields: list[str],
) -> dict[str, str]:
    elem_id = _element_id(segment, position)
    meaning = _meaning_for_value(code_set_id, value, segment, position)
    if not meaning and code_set_id:
        meaning = _lookup_code(code_set_id, value) or ""
    if not meaning:
        meaning = semantic_meaning(
            segment,
            position,
            value,
            element_name=name,
            sibling_fields=sibling_fields,
        )
    return {
        "element_id": elem_id,
        "element_name": name,
        "value": value,
        "meaning": meaning,
    }


def decode_segment(seg: Segment, delimiters: Delimiters) -> dict[str, Any]:
    defs = ELEMENT_DEFS.get(seg.name, [])
    segment_title = SEGMENT_NAMES.get(seg.name, seg.name)
    elements: list[dict[str, str]] = []
    count = seg.field_count()
    for i in range(count):
        raw = seg.fields[i]
        if i < len(defs):
            name, code_set = defs[i]
        else:
            name = f"Element {i + 1}"
            code_set = None
        elements.append(
            _decode_element(seg.name, i + 1, raw, name, code_set, list(seg.fields))
        )
    return {
        "sequence": seg.index + 1,
        "segment_id": seg.name,
        "segment_name": segment_title,
        "elements": elements,
    }


def decode_message(message: InterchangeMessage) -> dict[str, Any]:
    segments = [decode_segment(s, message.delimiters) for s in message.segments]
    return {
        "source_file": message.source_file,
        "transaction_set": message.transaction_set,
        "implementation_guide": message.implementation_guide,
        "guide_reference": _GUIDE_REF,
        "version": message.version,
        "control_id": message.control_id,
        "segments": segments,
    }


def decode_from_stored(stored_messages: list[dict[str, Any]]) -> dict[str, Any]:
    messages = [message_from_stored(item) for item in stored_messages]
    return decode_messages(messages)


def decode_messages(messages: list[InterchangeMessage]) -> dict[str, Any]:
    by_file: dict[str, list[dict[str, Any]]] = {}
    for msg in messages:
        decoded = decode_message(msg)
        fname = msg.source_file or "unknown"
        by_file.setdefault(fname, []).append(decoded)
    return {
        "guide_reference": _GUIDE_REF,
        "files": [
            {
                "filename": filename,
                "messages": file_messages,
            }
            for filename, file_messages in by_file.items()
        ],
    }


def message_from_stored(payload: dict[str, Any]) -> InterchangeMessage:
    """Rebuild InterchangeMessage from JSON stored on the session."""
    delims = Delimiters(**payload.get("delimiters", {}))
    segments = [
        Segment(
            name=s["name"],
            fields=list(s.get("fields") or []),
            index=int(s.get("index", 0)),
        )
        for s in payload.get("segments") or []
    ]
    return InterchangeMessage(
        segments=segments,
        delimiters=delims,
        source_file=payload.get("source_file", ""),
    )


def serialize_messages(messages: list[InterchangeMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        out.append({
            "source_file": msg.source_file,
            "transaction_set": msg.transaction_set,
            "implementation_guide": msg.implementation_guide,
            "delimiters": {
                "element": msg.delimiters.element,
                "component": msg.delimiters.component,
                "repetition": msg.delimiters.repetition,
                "segment": msg.delimiters.segment,
            },
            "segments": [
                {"name": s.name, "fields": s.fields, "index": s.index}
                for s in msg.segments
            ],
        })
    return out
