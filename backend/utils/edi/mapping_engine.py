"""X12 EDI → canonical mapping recommendations (270/271, 835/837 envelope)."""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone

from utils.hl7.mapping_engine import (
    APPROVED,
    AUTO_POPULATE_THRESHOLD,
    PROPOSED,
    UNRESOLVED_THRESHOLD,
    FieldMapping,
    _examples,
    _path_sort_key,
)

from . import canonical_model as cm

_X279 = "HIPAA 005010X279 eligibility companion guide."
_X835 = "HIPAA 005010X221 remittance advice companion guide (835)."
_X837 = "HIPAA 005010X222/X223/X224 health care claim companion guide (837)."
_X12 = "ANSI X12 segment definition."

STRUCTURAL_PATHS = {
    "ISA-1", "ISA-2", "ISA-3", "ISA-4", "ISA-5", "ISA-7", "ISA-9", "ISA-10",
    "ISA-11", "ISA-14", "ISA-15", "ISA-16",
    "GS-1", "GS-4", "GS-5", "GS-6", "ST-1", "SE-1", "SE-2",
    "HL-1", "GE-1", "GE-2", "IEA-1", "IEA-2",
}


@dataclass(frozen=True)
class StandardRule:
    source_path: str
    target_path: str
    transformation: str
    confidence: float
    basis: str
    families: tuple[str, ...] = ()

    def applies_to(self, family: str) -> bool:
        return not self.families or family in self.families


RULES: tuple[StandardRule, ...] = (
    # Envelope
    StandardRule("ISA-6", "InterchangeEnvelope.interchangeSenderId", "trim", 0.96, _X12),
    StandardRule("ISA-8", "InterchangeEnvelope.interchangeReceiverId", "trim", 0.96, _X12),
    StandardRule("ISA-12", "InterchangeEnvelope.x12Version", "direct", 0.95, _X12),
    StandardRule("ISA-13", "InterchangeEnvelope.interchangeControlNumber", "direct", 0.95, _X12),
    StandardRule("GS-1", "InterchangeEnvelope.functionalIdCode", "direct", 0.92, _X12),
    StandardRule("GS-2", "InterchangeEnvelope.applicationSenderCode", "direct", 0.92, _X12),
    StandardRule("GS-3", "InterchangeEnvelope.applicationReceiverCode", "direct", 0.92, _X12),
    StandardRule("GS-8", "InterchangeEnvelope.x12Version", "direct", 0.90, _X12),
    StandardRule("ST-1", "InterchangeEnvelope.transactionSetId", "direct", 0.98, _X12),
    StandardRule("ST-2", "InterchangeEnvelope.transactionControlNumber", "direct", 0.98, _X12),
    StandardRule("ST-3", "InterchangeEnvelope.implementationGuide", "direct", 0.95, _X279),
    # BHT
    StandardRule("BHT-1", "BatchHeader.structureCode", "direct", 0.94, _X279),
    StandardRule("BHT-2", "BatchHeader.purposeCode", "direct", 0.94, _X279),
    StandardRule("BHT-3", "BatchHeader.referenceId", "direct", 0.96, _X279),
    StandardRule("BHT-4", "BatchHeader.transactionDate", "x12_date", 0.92, _X279),
    StandardRule("BHT-5", "BatchHeader.transactionTime", "x12_time", 0.90, _X279),
    # HL
    StandardRule("HL-1", "Hierarchy.levelId", "direct", 0.90, _X279),
    StandardRule("HL-2", "Hierarchy.parentId", "direct", 0.88, _X279),
    StandardRule("HL-3", "Hierarchy.levelCode", "direct", 0.94, _X279),
    StandardRule("HL-4", "Hierarchy.childCode", "direct", 0.88, _X279),
    # NM1 (entity varies by NM101)
    StandardRule("NM1-1", "Party.entityType", "direct", 0.96, _X279),
    StandardRule("NM1-2", "Party.entityTypeQualifier", "direct", 0.90, _X279),
    StandardRule("NM1-3", "Party.organizationName", "direct", 0.92, _X279),
    StandardRule("NM1-4", "Party.lastName", "direct", 0.90, _X279, ("270", "271")),
    StandardRule("NM1-5", "Party.firstName", "direct", 0.90, _X279, ("270", "271")),
    StandardRule("NM1-8", "Party.identifierQualifier", "direct", 0.92, _X279),
    StandardRule("NM1-9", "Party.identifier", "direct", 0.95, _X279),
    # TRN / DMG / DTP / EQ
    StandardRule("TRN-2", "EligibilityInquiry.traceNumber", "direct", 0.93, _X279, ("270",)),
    StandardRule("TRN-3", "EligibilityInquiry.traceOriginator", "direct", 0.90, _X279, ("270",)),
    StandardRule("DMG-2", "Subscriber.birthDate", "x12_date", 0.95, _X279, ("270", "271")),
    StandardRule("DMG-3", "Subscriber.gender", "direct", 0.90, _X279, ("270", "271")),
    StandardRule("DTP-3", "EligibilityInquiry.inquiryDate", "x12_date", 0.92, _X279, ("270",)),
    StandardRule("EQ-1", "EligibilityInquiry.serviceTypeCode", "direct", 0.94, _X279, ("270",)),
    # AAA (271 errors / validation)
    StandardRule("AAA-1", "EligibilityResponse.validRequestIndicator", "direct", 0.93, _X279, ("271",)),
    StandardRule("AAA-3", "EligibilityResponse.rejectReasonCode", "direct", 0.95, _X279, ("271",)),
    StandardRule("AAA-4", "EligibilityResponse.followUpActionCode", "direct", 0.92, _X279, ("271",)),
    # 835 remittance (companion guide samples)
    StandardRule("BPR-1", "RemittancePayment.paymentMethod", "direct", 0.94, _X835, ("835",)),
    StandardRule("BPR-2", "RemittancePayment.paymentAmount", "decimal", 0.96, _X835, ("835",)),
    StandardRule("BPR-3", "RemittancePayment.paymentFormat", "direct", 0.90, _X835, ("835",)),
    StandardRule("BPR-16", "RemittancePayment.paymentDate", "x12_date", 0.92, _X835, ("835",)),
    StandardRule("TRN-2", "RemittancePayment.traceNumber", "direct", 0.93, _X835, ("835",)),
    StandardRule("DTM-2", "RemittancePayment.productionDate", "x12_date", 0.90, _X835, ("835",)),
    StandardRule("CLP-1", "ClaimPayment.patientControlNumber", "direct", 0.96, _X835, ("835",)),
    StandardRule("CLP-2", "ClaimPayment.claimStatus", "direct", 0.95, _X835, ("835",)),
    StandardRule("CLP-3", "ClaimPayment.chargeAmount", "decimal", 0.96, _X835, ("835",)),
    StandardRule("CLP-4", "ClaimPayment.paymentAmount", "decimal", 0.96, _X835, ("835",)),
    StandardRule("CLP-5", "ClaimPayment.patientResponsibility", "decimal", 0.92, _X835, ("835",)),
    StandardRule("CLP-7", "ClaimPayment.payerClaimNumber", "direct", 0.94, _X835, ("835",)),
    StandardRule("SVC-1", "ServicePayment.procedureCode", "direct", 0.94, _X835, ("835",)),
    StandardRule("SVC-2", "ServicePayment.chargeAmount", "decimal", 0.95, _X835, ("835",)),
    StandardRule("SVC-3", "ServicePayment.paymentAmount", "decimal", 0.95, _X835, ("835",)),
    StandardRule("N3-1", "Address.addressLine", "direct", 0.90, _X835, ("835", "837")),
    StandardRule("N4-1", "Address.city", "direct", 0.90, _X835, ("835", "837")),
    StandardRule("N4-2", "Address.state", "direct", 0.90, _X835, ("835", "837")),
    StandardRule("N4-3", "Address.postalCode", "direct", 0.90, _X835, ("835", "837")),
    # 837 claim
    StandardRule("CLM-1", "Claim.patientControlNumber", "direct", 0.98, _X837, ("837",)),
    StandardRule("CLM-2", "Claim.totalCharge", "decimal", 0.96, _X837, ("837",)),
    StandardRule("CLM-5", "Claim.placeOfService", "component[1]", 0.92, _X837, ("837",)),
    StandardRule("HI-1", "Claim.diagnosisCodes", "direct", 0.94, _X837, ("837",)),
    StandardRule("SV1-1", "ServiceLine.procedureCode", "direct", 0.95, _X837, ("837",)),
    StandardRule("SV1-2", "ServiceLine.chargeAmount", "decimal", 0.96, _X837, ("837",)),
    StandardRule("SV1-3", "ServiceLine.unitBasis", "direct", 0.90, _X837, ("837",)),
    StandardRule("SV1-4", "ServiceLine.serviceUnits", "decimal", 0.92, _X837, ("837",)),
    StandardRule("SV2-1", "ServiceLine.procedureCode", "direct", 0.93, _X837, ("837",)),
    StandardRule("SV2-2", "ServiceLine.chargeAmount", "decimal", 0.95, _X837, ("837",)),
    StandardRule("PRV-1", "Party.entityType", "direct", 0.88, _X837, ("837",)),
    StandardRule("REF-2", "Party.identifier", "direct", 0.85, _X837, ("835", "837")),
)

RULES_BY_PATH: dict[str, list[StandardRule]] = {}
for _rule in RULES:
    RULES_BY_PATH.setdefault(_rule.source_path, []).append(_rule)


def _family(message_type: str) -> str:
    return (message_type or "").split("^")[0].strip()


def _fallback_target(path: str) -> str:
    slug = path.lower().replace("-", "_").replace(".", "_")
    return f"CustomExtension.{slug}"


def _collect_standard(messages: list) -> dict[str, dict]:
    observed: dict[str, dict] = {}
    component_paths: dict[str, set[int]] = {}
    for rule in RULES:
        seg, _, rest = rule.source_path.partition("-")
        if "." in rest:
            fnum, _, cnum = rest.partition(".")
            component_paths.setdefault(f"{seg}-{fnum}", set()).add(int(cnum))

    for msg in messages:
        family = _family(msg.message_type)
        for seg in msg.segments:
            last = seg.field_count()
            for pos in range(1, last + 1):
                offset = pos - 1
                if offset >= len(seg.fields):
                    continue
                raw = seg.fields[offset].strip()
                if not raw:
                    continue
                base = f"{seg.name}-{pos}"
                targets = [base]
                for cnum in sorted(component_paths.get(base, ())):
                    targets.append(f"{base}.{cnum}")
                for path in targets:
                    if "." in path.split("-", 1)[1]:
                        cnum = int(path.rsplit(".", 1)[1])
                        parts = raw.split(msg.delimiters.component)
                        value = parts[cnum - 1].strip() if cnum - 1 < len(parts) else ""
                        if not value:
                            continue
                    else:
                        value = raw
                    entry = observed.setdefault(
                        path,
                        {
                            "segment": seg.name,
                            "families": set(),
                            "values": [],
                            "occurrences": 0,
                            "messages": set(),
                        },
                    )
                    entry["families"].add(family)
                    entry["values"].append(value)
                    entry["occurrences"] += 1
                    entry["messages"].add(msg.source_file)
    return observed


def _standard_mappings(observed: dict[str, dict]) -> list[FieldMapping]:
    mappings: list[FieldMapping] = []
    for path in sorted(observed, key=_path_sort_key):
        entry = observed[path]
        if any(p.startswith(path + ".") for p in observed):
            continue
        if path in STRUCTURAL_PATHS:
            continue

        candidates = [
            r for r in RULES_BY_PATH.get(path, [])
            if any(r.applies_to(f) for f in entry["families"])
        ]
        if not candidates:
            target = _fallback_target(path)
            attribute = cm.resolve(target)
            mappings.append(
                FieldMapping(
                    id=f"std::{path}::{target}",
                    source_path=path,
                    source_segment=entry["segment"],
                    category="standard",
                    origin="fallback",
                    target_path=target,
                    target_entity="CustomExtension",
                    transformation="direct",
                    confidence=0.60,
                    rationale=[
                        "No deterministic rule is defined yet for this X12 element.",
                        f"Value retained as {target} pending review.",
                    ],
                    status=PROPOSED,
                    auto_approvable=False,
                    applies_to_families=sorted(entry["families"]),
                    examples=_examples(entry["values"]),
                    occurrences=entry["occurrences"],
                    messages_present=len(entry["messages"]),
                    target_required=False,
                    target_terminology=attribute.terminology if attribute else None,
                    source_files=sorted(entry["messages"]),
                )
            )
            continue

        candidates.sort(key=lambda r: r.confidence, reverse=True)
        rule = candidates[0]
        attribute = cm.resolve(rule.target_path)
        rationale = [rule.basis]
        mappings.append(
            FieldMapping(
                id=f"std::{path}::{rule.target_path}",
                source_path=path,
                source_segment=entry["segment"],
                category="standard",
                origin="rule",
                target_path=rule.target_path,
                target_entity=rule.target_path.split(".")[0],
                transformation=rule.transformation,
                confidence=rule.confidence,
                rationale=rationale,
                status=PROPOSED,
                auto_approvable=rule.confidence >= AUTO_POPULATE_THRESHOLD,
                applies_to_families=sorted(entry["families"]),
                examples=_examples(entry["values"]),
                occurrences=entry["occurrences"],
                messages_present=len(entry["messages"]),
                target_required=bool(attribute and attribute.required),
                target_terminology=attribute.terminology if attribute else None,
                source_files=sorted(entry["messages"]),
            )
        )
    return mappings


def entities_in_play(messages: list) -> set[str]:
    entities = {"InterchangeEnvelope", "BatchHeader", "Hierarchy", "Party"}
    for msg in messages:
        txn = _family(msg.message_type)
        if txn == "270":
            entities |= {"EligibilityInquiry", "Subscriber"}
        elif txn == "271":
            entities |= {"EligibilityResponse", "Subscriber"}
        elif txn == "835":
            entities |= {"RemittancePayment", "ClaimPayment", "ServicePayment", "Address"}
        elif txn == "837":
            entities |= {"Claim", "ServiceLine", "Address"}
    return entities


def apply_rule001_auto_approval(mappings: list[FieldMapping]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    approved = 0
    for mapping in mappings:
        if mapping.status != PROPOSED or mapping.category != "standard":
            continue
        if not mapping.target_path or mapping.origin not in {"rule", "fallback"}:
            continue
        mapping.status = APPROVED
        mapping.reviewer = "RULE-001"
        mapping.reviewer_comment = "Auto-approved at ingest: X12 element mapped from the rule table."
        mapping.reviewed_at = now
        approved += 1
    return approved


def build_mappings(messages: list, inferences: dict | None = None) -> list[FieldMapping]:
    del inferences  # EDI has no Z-segments; reserved for future companion-specific inference
    observed = _collect_standard(messages)
    mappings = _standard_mappings(observed)
    apply_rule001_auto_approval(mappings)
    return mappings


def summarise(mappings: list[FieldMapping]) -> dict:
    counts: dict[str, int] = {}
    for m in mappings:
        counts[m.status] = counts.get(m.status, 0) + 1
    return {
        "total": len(mappings),
        "by_status": counts,
        "standard": sum(1 for m in mappings if m.category == "standard"),
        "custom": sum(1 for m in mappings if m.category == "custom"),
        "auto_approvable": sum(1 for m in mappings if m.auto_approvable),
        "low_confidence": sum(1 for m in mappings if m.confidence < UNRESOLVED_THRESHOLD),
    }
