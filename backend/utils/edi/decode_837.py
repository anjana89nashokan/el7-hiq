"""Decode X12 837 interchanges into ordered segment tables (companion guide semantics)."""

from __future__ import annotations

from typing import Any

from .format_meanings import semantic_meaning
from .guides import SEGMENT_NAMES, lookup_code as _lookup_code
from .guides.elements_837 import resolve_element_def
from .guide_sections import assign_guide_sections
from .parser import Delimiters, InterchangeMessage, Segment
from .target_keys import target_for_element

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
    if segment in ("SV1", "SV2", "SV3", "SV5", "SVD") and position == 1:
        return _decode_procedure_composite(value)
    return ""


_HI_ELEMENT_ROLE: dict[int, str] = {
    1: "Principal Diagnosis",
}


def _hi_element_role(position: int) -> str:
    return _HI_ELEMENT_ROLE.get(position, "Additional Diagnosis")


def _hi_industry_meaning(qualifier: str, code: str) -> str:
    if not code.strip():
        return ""
    q = qualifier.strip().upper()
    parts = [
        "Diagnosis code as defined by the coding system (no decimal point in transmission)",
    ]
    if q in ("ABK", "ABF"):
        parts.append(f"ICD-10-CM code: {code.strip()}")
    elif q in ("BK", "BF"):
        parts.append(f"ICD-9-CM code: {code.strip()}")
    else:
        parts.append(f"Industry code: {code.strip()}")
    return "; ".join(parts)


def _split_composite(value: str, component: str) -> tuple[str, str]:
    if not value.strip():
        return "", ""
    sep = component or ":"
    if sep in value:
        qual, code = value.split(sep, 1)
        return qual.strip(), code.strip()
    return value.strip(), ""


def _decode_hi_element(
    position: int,
    value: str,
    name: str,
    delimiters: Delimiters,
) -> dict[str, Any]:
    """HI01…HI12 composites per companion guide (HI0n-1 qualifier, HI0n-2 industry code)."""
    elem_id = _element_id("HI", position)
    qual, code = _split_composite(value, delimiters.component)
    role = _hi_element_role(position)
    children: list[dict[str, Any]] = []
    if value.strip():
        children.append({
            "element_id": f"{elem_id}-1",
            "element_name": "Code List Qualifier Code",
            "value": qual,
            "meaning": _lookup_code("HI01", qual) or "",
        })
        children.append({
            "element_id": f"{elem_id}-2",
            "element_name": "Industry Code",
            "value": code,
            "meaning": _hi_industry_meaning(qual, code),
        })
    summary = role
    if qual:
        qual_label = _lookup_code("HI01", qual)
        summary = f"{role}; {qual_label or qual}"
        if code:
            summary = f"{summary} — {code}"
    row = {
        "element_id": elem_id,
        "element_name": name,
        "value": value,
        "meaning": summary,
        "target": target_for_element(elem_id, name),
        "children": children,
    }
    for child in row["children"]:
        child["target"] = target_for_element(child["element_id"], child["element_name"])
    return row


def _finalize_nm1_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nest NM109 under NM108 (qualifier + identification) like HI composites."""
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(elements):
        el = elements[i]
        if (
            el.get("element_id") == "NM108"
            and i + 1 < len(elements)
            and elements[i + 1].get("element_id") == "NM109"
        ):
            n109 = elements[i + 1]
            qual = (el.get("value") or "").strip()
            qual_meaning = _lookup_code("NM108", qual) or el.get("meaning") or ""
            id_val = (n109.get("value") or "").strip()
            id_meaning = n109.get("meaning") or ""
            if id_val and qual_meaning:
                id_meaning = id_meaning or f"{qual_meaning}: {id_val}"
            merged = dict(el)
            merged["children"] = [
                {
                    "element_id": "NM109",
                    "element_name": "Identification Code",
                    "value": n109.get("value", ""),
                    "meaning": id_meaning,
                    "target": target_for_element("NM109", "Identification Code"),
                }
            ]
            merged["target"] = target_for_element("NM108", el.get("element_name", "Identification Code Qualifier"))
            if qual_meaning and not merged.get("meaning"):
                merged["meaning"] = qual_meaning
            out.append(merged)
            i += 2
            continue
        out.append(el)
        i += 1
    return out


def _nm1_segment_label(elements: list[dict[str, Any]]) -> tuple[str, str]:
    ent = ""
    for el in elements:
        if el.get("element_id") == "NM101":
            ent = (el.get("value") or "").strip()
            break
    if not ent:
        return "", ""
    label = _lookup_code("NM101", ent) or ""
    return ent, label


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
) -> dict[str, Any]:
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
        "target": target_for_element(elem_id, name),
    }


def decode_segment(seg: Segment, delimiters: Delimiters) -> dict[str, Any]:
    segment_title = SEGMENT_NAMES.get(seg.name, seg.name)
    elements: list[dict[str, Any]] = []
    count = seg.field_count()
    for i in range(count):
        raw = seg.fields[i]
        if seg.name == "NM1" and not (raw or "").strip():
            continue
        if seg.name == "HI" and not (raw or "").strip():
            continue
        name, code_set = resolve_element_def(seg.name, i + 1)
        if seg.name == "HI":
            elements.append(_decode_hi_element(i + 1, raw, name, delimiters))
            continue
        elements.append(
            _decode_element(seg.name, i + 1, raw, name, code_set, list(seg.fields))
        )

    party_code = ""
    segment_label = ""
    if seg.name == "NM1":
        elements = _finalize_nm1_elements(elements)
        party_code, segment_label = _nm1_segment_label(elements)

    payload: dict[str, Any] = {
        "sequence": seg.index + 1,
        "segment_id": seg.name,
        "segment_name": segment_title,
        "elements": elements,
    }
    if segment_label:
        payload["segment_label"] = segment_label
        payload["party_code"] = party_code
    return payload


def decode_message(message: InterchangeMessage) -> dict[str, Any]:
    segments = [decode_segment(s, message.delimiters) for s in message.segments]
    sections = assign_guide_sections(segments)
    return {
        "source_file": message.source_file,
        "transaction_set": message.transaction_set,
        "implementation_guide": message.implementation_guide,
        "guide_reference": _GUIDE_REF,
        "version": message.version,
        "control_id": message.control_id,
        "segments": segments,
        "sections": sections,
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
