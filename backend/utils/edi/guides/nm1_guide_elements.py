"""NM1 elements documented in 837 companion guide loops (hide unused NM106/NM107 etc.)."""

from __future__ import annotations

# LOOP 2310+ claim-attached provider NM1 rows (guide tables with HI / service lines).
_CLAIM_LOOP_NM1: dict[str, frozenset[str]] = {
    "DN": frozenset({"NM101", "NM102", "NM103", "NM104", "NM105", "NM108", "NM109"}),
    "82": frozenset({"NM101", "NM102", "NM103", "NM104", "NM105", "NM108", "NM109"}),
    "DQ": frozenset({"NM101", "NM102", "NM103", "NM104", "NM105", "NM108", "NM109"}),
    "P3": frozenset({"NM101", "NM102", "NM103", "NM104", "NM105", "NM108", "NM109"}),
    "77": frozenset({"NM101", "NM102", "NM103", "NM108", "NM109"}),
    "QB": frozenset({"NM101", "NM102", "NM108", "NM109"}),
    "PW": frozenset({"NM101", "NM102", "NM103", "NM108", "NM109"}),
    "45": frozenset({"NM101", "NM102", "NM103", "NM108", "NM109"}),
}

# Header / billing party NM1 (loops 1000, 2010).
_HEADER_NM1: dict[str, frozenset[str]] = {
    "41": frozenset({"NM101", "NM102", "NM103", "NM108", "NM109"}),
    "40": frozenset({"NM101", "NM102", "NM103", "NM108", "NM109"}),
    "85": frozenset({"NM101", "NM102", "NM103", "NM108", "NM109"}),
    "87": frozenset({"NM101", "NM102", "NM103"}),
    "PE": frozenset({"NM101", "NM102", "NM103", "NM108", "NM109"}),
    "IL": frozenset({"NM101", "NM102", "NM103", "NM104", "NM108", "NM109"}),
    "PR": frozenset({"NM101", "NM102", "NM103", "NM108", "NM109"}),
    "QC": frozenset({"NM101", "NM102", "NM103", "NM104"}),
}


def allowed_nm1_element_ids(party_code: str, *, in_claim_loop: bool) -> frozenset[str] | None:
    code = (party_code or "").strip().upper()
    if in_claim_loop:
        return _CLAIM_LOOP_NM1.get(code)
    return _HEADER_NM1.get(code)
