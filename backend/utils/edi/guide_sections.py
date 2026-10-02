"""Group decoded segments into companion-guide loops (NM1 with N3/N4/HI with CLM, etc.)."""

from __future__ import annotations

from typing import Any

from .guides import lookup_code as _lookup_code

_HL_LEVEL_TITLES = {
    "20": "LOOP 2000A — Billing provider hierarchy",
    "22": "LOOP 2000B — Subscriber hierarchy",
    "23": "LOOP 2000C — Patient hierarchy",
}

_ENVELOPE_SEGS = frozenset({"ISA", "GS", "ST"})
_TRAILER_SEGS = frozenset({"SE", "GE", "IEA"})


def _element_value(seg: dict[str, Any], element_id: str) -> str:
    for el in seg.get("elements") or []:
        if el.get("element_id") == element_id:
            return (el.get("value") or "").strip()
    return ""


def assign_guide_sections(decoded_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve file order; sections follow companion guide loops (not raw segment ID)."""
    if not decoded_segments:
        return []

    groups: list[dict[str, Any]] = []
    bucket: list[dict[str, Any]] = []
    current_id = "envelope"
    current_title = "Interchange / functional group"

    def flush() -> None:
        nonlocal bucket
        if not bucket:
            return
        groups.append({
            "section_id": current_id,
            "title": current_title,
            "segments": bucket,
        })
        bucket = []

    for seg in decoded_segments:
        sid = seg.get("segment_id") or ""

        if sid in _ENVELOPE_SEGS:
            if sid == "ISA":
                flush()
                current_id = "envelope"
                current_title = "Interchange / functional group"
            bucket.append(seg)
            continue

        if sid in _TRAILER_SEGS:
            if not bucket or bucket[-1].get("segment_id") not in _TRAILER_SEGS:
                flush()
                current_id = "trailers"
                current_title = "Transaction / interchange trailers"
            bucket.append(seg)
            continue

        if sid == "BHT":
            flush()
            current_id = "transaction-header"
            current_title = "Transaction header (BHT)"
            bucket.append(seg)
            continue

        if sid == "HL":
            flush()
            level = _element_value(seg, "HL03")
            current_id = f"hl-{seg.get('sequence')}-{level}"
            current_title = _HL_LEVEL_TITLES.get(level, f"Hierarchical level {level or '?'}")
            bucket.append(seg)
            continue

        if sid == "CLM":
            flush()
            current_id = f"claim-{seg.get('sequence')}"
            current_title = "LOOP 2300 — Claim Information"
            bucket.append(seg)
            continue

        if sid == "LX":
            line = _element_value(seg, "LX01") or "?"
            flush()
            current_id = f"service-{seg.get('sequence')}"
            current_title = f"LOOP 2400 — Service line {line}"
            bucket.append(seg)
            continue

        if sid == "NM1":
            if current_id.startswith("claim-") or current_id.startswith("service-"):
                bucket.append(seg)
                continue

            ent = _element_value(seg, "NM101")
            ent_label = _lookup_code("NM101", ent) if ent else ""
            if ent_label and ent:
                party_title = f"{ent_label} (NM1*{ent})"
            elif ent:
                party_title = f"NM1 — entity {ent}"
            else:
                party_title = "Individual or Organizational Name"

            last_sid = bucket[-1].get("segment_id") if bucket else ""
            if last_sid in ("HL", "SBR"):
                current_id = f"party-{seg.get('sequence')}-{ent or 'nm1'}"
                current_title = party_title
                bucket.append(seg)
                continue

            flush()
            current_id = f"party-{seg.get('sequence')}-{ent or 'nm1'}"
            current_title = party_title
            bucket.append(seg)
            continue

        bucket.append(seg)

    flush()
    return _enrich_sections(groups)


def _filter_nm1_elements(
    elements: list[dict[str, Any]],
    allowed: frozenset[str] | None,
) -> list[dict[str, Any]]:
    def visible(el: dict[str, Any]) -> bool:
        eid = el.get("element_id") or ""
        if allowed is not None and eid not in allowed:
            return False
        if (el.get("value") or "").strip():
            return True
        return bool(el.get("children"))

    out: list[dict[str, Any]] = []
    for el in elements:
        row = dict(el)
        if row.get("children"):
            row["children"] = [c for c in row["children"] if visible(c)]
        if not visible(row):
            continue
        out.append(row)
    return out


def _enrich_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .guides.nm1_guide_elements import allowed_nm1_element_ids

    for section in sections:
        section_id = section.get("section_id") or ""
        in_claim = section_id.startswith("claim-")
        in_service = section_id.startswith("service-")
        in_loop = in_claim or in_service
        section["combined_table"] = in_claim
        section_title = section.get("title") or ""
        for seg in section.get("segments") or []:
            seg["guide_section_title"] = section_title
            if seg.get("segment_id") != "NM1":
                continue
            party = (seg.get("party_code") or "").strip()
            allowed = allowed_nm1_element_ids(party, in_claim_loop=in_loop)
            seg["elements"] = _filter_nm1_elements(seg.get("elements") or [], allowed)
    return sections
