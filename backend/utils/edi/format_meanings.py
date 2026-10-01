"""Human-readable meanings for common X12 element values (dates, times, IDs)."""

from __future__ import annotations

import re
from datetime import date


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value.strip())


def format_date_ccyymmdd(value: str) -> str | None:
    raw = _digits_only(value)
    if len(raw) != 8:
        return None
    try:
        d = date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None
    return f"{d.strftime('%B %d, %Y')} (CCYYMMDD {raw})"


def format_date_yymmdd(value: str) -> str | None:
    raw = _digits_only(value)
    if len(raw) != 6:
        return None
    try:
        yy = int(raw[0:2])
        century = 2000 if yy < 50 else 1900
        d = date(century + yy, int(raw[2:4]), int(raw[4:6]))
    except ValueError:
        return None
    return f"{d.strftime('%B %d, %Y')} (YYMMDD {raw})"


def format_time_hhmm(value: str) -> str | None:
    raw = _digits_only(value)
    if len(raw) not in (4, 6):
        return None
    try:
        hh, mm = int(raw[0:2]), int(raw[2:4])
        if hh > 23 or mm > 59:
            return None
        suffix = f" (HHMM {raw[:4]})"
        if len(raw) == 6:
            ss = int(raw[4:6])
            if ss > 59:
                return None
            return f"{hh:02d}:{mm:02d}:{ss:02d}{suffix.replace('HHMM', 'HHMMSS')}"
        # 12-hour friendly hint for common business hours display
        hour12 = hh % 12 or 12
        am_pm = "AM" if hh < 12 else "PM"
        return f"{hh:02d}:{mm:02d} ({hour12}:{mm:02d} {am_pm}){suffix}"
    except ValueError:
        return None


def format_date_range_rd8(value: str) -> str | None:
    if "-" not in value:
        return None
    start, end = value.split("-", 1)
    a = format_date_ccyymmdd(start)
    b = format_date_ccyymmdd(end)
    if a and b:
        return f"From {a.split(' (')[0]} through {b.split(' (')[0]} (RD8)"
    return None


def format_dtp_period(format_qual: str, value: str) -> str:
    fq = (format_qual or "").strip().upper()
    if not value.strip():
        return ""
    if fq == "D8":
        return format_date_ccyymmdd(value) or f"Date value {value!r} (expected CCYYMMDD)"
    if fq == "RD8":
        return format_date_range_rd8(value) or f"Date range {value!r} (expected CCYYMMDD-CCYYMMDD)"
    if fq == "TM":
        t = format_time_hhmm(value)
        return t or f"Time value {value!r} (expected HHMM)"
    return ""


def format_amount(value: str) -> str | None:
    v = value.strip()
    if not v:
        return None
    try:
        num = float(v)
    except ValueError:
        return None
    return f"${num:,.2f} (monetary amount)" if "." in v or num != int(num) else f"${num:,.0f} (monetary amount)"


def format_control_number(value: str, *, scope: str) -> str:
    v = value.strip()
    if not v:
        return ""
    return f"{scope} (must match corresponding trailer); assigned value {v!r}"


# (segment, element position 1-based) → static hint when value is present but not a coded value
_ELEMENT_HINTS: dict[tuple[str, int], str] = {
    ("GS", 2): "Application sender ID — identifies the system or entity that created this functional group",
    ("GS", 3): "Application receiver ID — identifies the intended recipient of this functional group",
    ("GS", 6): "Functional group control number",
    ("ISA", 6): "Interchange sender ID (padded to fixed width in ISA)",
    ("ISA", 8): "Interchange receiver ID (padded to fixed width in ISA)",
    ("ISA", 13): "Interchange control number",
    ("ST", 2): "Transaction set control number (paired with SE02)",
    ("SE", 1): "Count of segments in this transaction set (including ST and SE)",
    ("SE", 2): "Transaction set control number (must match ST02)",
    ("GE", 1): "Number of transaction sets in this functional group",
    ("GE", 2): "Functional group control number (must match GS06)",
    ("IEA", 1): "Number of functional groups in this interchange",
    ("IEA", 2): "Interchange control number (must match ISA13)",
    ("BHT", 3): "Document or batch reference number",
    ("NM1", 3): "Organization name or person last name",
    ("NM1", 4): "Person first name (when entity type is person)",
    ("NM1", 9): "Identifier value (qualifier in previous element)",
    ("N3", 1): "Street address line 1",
    ("N3", 2): "Street address line 2",
    ("N4", 1): "City",
    ("N4", 2): "State or province code",
    ("N4", 3): "Postal or ZIP code",
    ("REF", 2): "Reference number or ID (type in REF01)",
    ("CLM", 1): "Patient control number (claim identifier in provider system)",
    ("CLM", 2): "Total claim charge amount",
    ("HL", 1): "Hierarchical ID number for this level",
    ("HL", 2): "Parent hierarchical ID (blank at root)",
    ("LX", 1): "Service line number within the claim",
    ("SV1", 2): "Line item charge amount",
    ("SV1", 4): "Number of units of service",
    ("GS", 7): "Responsible agency code",
}

_GS07 = {"T": "Transportation Data Coordinating Committee", "X": "Accredited Standards Committee X12"}


def semantic_meaning(
    segment: str,
    position: int,
    value: str,
    *,
    element_name: str,
    sibling_fields: list[str] | None = None,
) -> str:
    if not value.strip():
        return ""

    seg = segment.upper()
    key = (seg, position)

    if seg == "GS" and position == 4:
        return format_date_ccyymmdd(value) or f"Group date {value!r} (CCYYMMDD)"
    if seg == "GS" and position == 5:
        return format_time_hhmm(value) or f"Group time {value!r} (HHMM)"
    if seg == "GS" and position == 7:
        label = _GS07.get(value.strip().upper())
        return label or f"Responsible agency code {value!r}"

    if seg == "ISA" and position == 9:
        return format_date_yymmdd(value) or f"Interchange date {value!r} (YYMMDD)"
    if seg == "ISA" and position == 10:
        return format_time_hhmm(value) or f"Interchange time {value!r} (HHMM)"

    if seg == "BHT" and position == 4:
        return format_date_ccyymmdd(value) or f"Transaction date {value!r} (CCYYMMDD)"
    if seg == "BHT" and position == 5:
        return format_time_hhmm(value) or f"Transaction time {value!r} (HHMM)"

    if seg == "DTP" and position == 3 and sibling_fields:
        fq = sibling_fields[1] if len(sibling_fields) > 1 else ""
        qual = sibling_fields[0] if sibling_fields else ""
        label = _lookup_dtp_qual_label(qual)
        period = format_dtp_period(fq, value)
        if label and period:
            return f"{label}; {period}"
        if period:
            return period
        if label:
            return label

    if seg == "DMG" and position == 2 and sibling_fields:
        fq = sibling_fields[0] if sibling_fields else "D8"
        base = format_dtp_period(fq, value)
        return base or f"Birth or demographic date {value!r}"

    if seg == "PAT" and position == 5 and sibling_fields:
        fq = sibling_fields[3] if len(sibling_fields) > 3 else "D8"
        return format_dtp_period(fq, value) or f"Patient date {value!r}"

    if position == 2 and seg in ("CLM", "SV1", "SV2", "AMT"):
        amt = format_amount(value)
        if amt:
            return amt

    if key == ("GS", 6):
        return format_control_number(value, scope="Functional group control number")
    if key == ("ISA", 13):
        return format_control_number(value, scope="Interchange control number")
    if key == ("ST", 2):
        return format_control_number(value, scope="Transaction set control number")

    hint = _ELEMENT_HINTS.get(key)
    if hint:
        return f"{hint}: {value.strip()}"

    return ""


def _lookup_dtp_qual_label(qual: str) -> str:
    # Avoid circular import: duplicate minimal labels or import from code_sets
    from .guides.code_sets_837 import DTP01_QUAL

    q = (qual or "").strip()
    return DTP01_QUAL.get(q, f"Date/time qualifier {q}" if q else "")
