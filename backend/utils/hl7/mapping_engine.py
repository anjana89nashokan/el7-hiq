"""Mapping recommendation engine — HL7 source paths to canonical targets.

Implements BRD FR-012 to FR-016. Every populated field in the corpus becomes a
``FieldMapping`` carrying a proposed target, a confidence, the evidence behind
it and a proposed transformation, so a reviewer can judge the recommendation
rather than the output.

Two sources of candidates, deliberately kept apart because the governance rules
differ:

  standard segments   Resolved from a deterministic rule table built on the
                      v2.5.1 definitions and the proposed targets in BRD 14.3.
                      RULE-001 auto-approves exact matches at ingest so they
                      do not require manual review.

  Z-segments          Resolved from value-based inference (zsegment_inference).
                      RULE-002 forbids auto-promotion, so these are never
                      auto-approvable no matter how confident the inference is.

A populated field with no rule and no inference is *not* dropped. DQ-03 and
FR-007 require it to survive as an unresolved mapping, which is also what
FR-016 means by refusing to invent a definitive target.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field
from datetime import datetime, timezone

from . import canonical_model as cm

# ---------------------------------------------------------------------------
# Review status vocabulary
# ---------------------------------------------------------------------------

PROPOSED = "proposed"
APPROVED = "approved"
REJECTED = "rejected"
DEFERRED = "deferred"
UNRESOLVED = "unresolved"

TERMINAL_STATUSES = {APPROVED, REJECTED}

# Below this, FR-016 requires the candidate to be presented as unresolved
# rather than as a definitive target.
UNRESOLVED_THRESHOLD = 0.50

# At or above this, RULE-001 permits a standard field to be auto-populated.
AUTO_POPULATE_THRESHOLD = 0.90


# ---------------------------------------------------------------------------
# Deterministic rule table for standard segments
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StandardRule:
    source_path: str
    target_path: str
    transformation: str
    confidence: float
    basis: str
    families: tuple[str, ...] = ()  # empty tuple = applies to every family

    def applies_to(self, family: str) -> bool:
        return not self.families or family in self.families


_BRD = "Proposed target is listed in BRD 14.3 (illustrative mapping examples)."
_STD = "Field meaning is fixed by the HL7 v2.5.1 standard segment definition."
_PROFILE = "BRD 14.3 records this mapping profile as still to be approved."

RULES: tuple[StandardRule, ...] = (
    # --- MSH: provenance envelope (DQ-05) ---------------------------------
    StandardRule("MSH-3", "CanonicalMessage.sendingApplication", "component[1]", 0.95, _STD),
    StandardRule("MSH-4", "CanonicalMessage.sendingFacility", "component[1]", 0.95, _STD),
    StandardRule("MSH-7", "CanonicalMessage.messageTimestamp", "hl7_datetime", 0.95, _STD),
    StandardRule("MSH-9", "CanonicalMessage.messageType", "direct", 0.95, _STD),
    StandardRule("MSH-10", "CanonicalMessage.sourceControlId", "direct", 0.98, _BRD),
    StandardRule("MSH-11", "CanonicalMessage.processingId", "component[1]", 0.95, _STD),
    StandardRule("MSH-12", "CanonicalMessage.hl7Version", "direct", 0.95, _STD),
    StandardRule("MSH-5", "CanonicalMessage.receivingApplication", "component[1]", 0.93, _STD),
    StandardRule("MSH-6", "CanonicalMessage.receivingFacility", "component[1]", 0.93, _STD),

    # --- EVN: event context ------------------------------------------------
    StandardRule("EVN-1", "CanonicalMessage.eventTypeCode", "direct", 0.92, _STD),
    StandardRule("EVN-2", "CanonicalMessage.eventRecorded", "hl7_datetime", 0.92, _STD),
    StandardRule("EVN-6", "CanonicalMessage.eventOccurred", "hl7_datetime", 0.90, _STD),

    # --- PID: clinical identity -------------------------------------------
    StandardRule("PID-3", "Patient.identifier", "identifier_with_authority", 0.98, _BRD),
    StandardRule("PID-5.1", "Patient.familyName", "direct", 0.96, _BRD),
    StandardRule("PID-5.2", "Patient.givenName", "direct", 0.96, _BRD),
    StandardRule("PID-5.3", "Patient.middleName", "direct", 0.92, _STD),
    StandardRule("PID-7", "Patient.birthDate", "hl7_date", 0.95, _STD),
    StandardRule("PID-8", "Patient.gender", "code_translate[HL70001]", 0.93, _STD),
    StandardRule("PID-11.1", "Patient.addressLine", "direct", 0.92, _STD),
    StandardRule("PID-11.3", "Patient.city", "direct", 0.92, _STD),
    StandardRule("PID-11.4", "Patient.state", "direct", 0.92, _STD),
    StandardRule("PID-11.5", "Patient.postalCode", "direct", 0.92, _STD),
    StandardRule("PID-13", "Patient.telecom", "telecom_composite", 0.93, _STD),

    # --- PV1: encounter ----------------------------------------------------
    StandardRule("PV1-2", "Encounter.class", "code_translate[HL70004]", 0.94, _BRD),
    StandardRule("PV1-3", "Encounter.location", "location_composite", 0.90, _BRD),
    StandardRule("PV1-7", "Encounter.attendingProvider", "provider_name", 0.92, _STD),
    StandardRule("PV1-44", "Encounter.admitDateTime", "hl7_datetime", 0.92, _STD),
    StandardRule("PV1-45", "Encounter.dischargeDateTime", "hl7_datetime", 0.92, _STD),
    StandardRule("PV1-19", "Encounter.facility", "component[1]", 0.88, _STD),

    # --- ORC / OBR: order and report (ORU only) ----------------------------
    StandardRule("ORC-2", "DiagnosticReport.placerOrderNumber", "component[1]", 0.92, _STD,
                 ("ORU",)),
    StandardRule("ORC-3", "DiagnosticReport.fillerOrderNumber", "component[1]", 0.92, _STD,
                 ("ORU",)),
    StandardRule("OBR-4", "DiagnosticReport.code", "coded_element[LOINC]", 0.96, _BRD, ("ORU",)),
    StandardRule("OBR-2", "DiagnosticReport.placerOrderNumber", "component[1]", 0.90, _STD,
                 ("ORU",)),
    StandardRule("OBR-3", "DiagnosticReport.fillerOrderNumber", "component[1]", 0.90, _STD,
                 ("ORU",)),
    StandardRule("OBR-7", "DiagnosticReport.effectiveDateTime", "hl7_datetime", 0.92, _STD,
                 ("ORU",)),
    StandardRule("OBR-24", "DiagnosticReport.category", "code_translate[HL70074]", 0.90, _STD,
                 ("ORU",)),
    StandardRule("OBR-25", "DiagnosticReport.status", "code_translate[HL70123]", 0.92, _STD,
                 ("ORU",)),

    # --- OBX in ORU: the result itself (FR-010) ----------------------------
    StandardRule("OBX-2", "Observation.valueType", "direct", 0.94, _STD, ("ORU",)),
    StandardRule("OBX-3", "Observation.code", "coded_element[LOINC]", 0.98, _BRD, ("ORU",)),
    StandardRule("OBX-5", "Observation.value", "typed_by[OBX-2]", 0.98, _BRD, ("ORU",)),
    StandardRule("OBX-6", "Observation.unit", "coded_element[UCUM]", 0.96, _BRD, ("ORU",)),
    StandardRule("OBX-7", "Observation.referenceRange", "direct", 0.96, _BRD, ("ORU",)),
    StandardRule("OBX-8", "Observation.interpretation", "code_translate[HL70078]", 0.96, _BRD,
                 ("ORU",)),
    StandardRule("OBX-11", "Observation.status", "code_translate[HL70085]", 0.92, _STD, ("ORU",)),
    StandardRule("OBX-14", "Observation.effectiveDateTime", "hl7_datetime", 0.92, _STD, ("ORU",)),
    StandardRule("NTE-3", "Observation.note", "direct", 0.88, _STD, ("ORU",)),

    # --- TXA / OBX in MDM: the document (RULE-013, FR-011) -----------------
    StandardRule("TXA-2", "DocumentReference.type", "coded_element", 0.85, _PROFILE, ("MDM",)),
    StandardRule("TXA-4", "DocumentReference.activityDateTime", "hl7_datetime", 0.90, _STD,
                 ("MDM",)),
    StandardRule("TXA-5", "DocumentReference.author", "provider_name", 0.88, _STD, ("MDM",)),
    StandardRule("TXA-12", "DocumentReference.masterIdentifier", "identifier_with_authority",
                 0.85, _PROFILE, ("MDM",)),
    StandardRule("TXA-17", "DocumentReference.docStatus", "code_translate[HL70271]", 0.85,
                 _PROFILE, ("MDM",)),
    StandardRule("TXA-18", "DocumentReference.confidentiality", "code_translate[HL70272]",
                 0.82, _STD, ("MDM",)),
    StandardRule("OBX-5", "DocumentReference.content", "concat_ordered[OBX-1]", 0.94, _BRD,
                 ("MDM",)),
    StandardRule("OBX-3", "DocumentReference.type", "coded_element[LOINC]", 0.70,
                 "OBX-3 in MDM names the document class, which may duplicate or "
                 "conflict with TXA-2. Needs a reviewer decision on precedence.",
                 ("MDM",)),
)

RULES_BY_PATH: dict[str, list[StandardRule]] = {}
for _rule in RULES:
    RULES_BY_PATH.setdefault(_rule.source_path, []).append(_rule)


# Segments whose fields are structural bookkeeping rather than payload.
STRUCTURAL_PATHS = {"OBX-1", "ORC-1", "OBR-1", "TXA-1", "PID-1", "PV1-1", "MSH-1", "MSH-2"}


# ---------------------------------------------------------------------------
# Field mapping record (BRD 14.1 "Field Mapping")
# ---------------------------------------------------------------------------

@dataclass
class FieldMapping:
    id: str
    source_path: str
    source_segment: str
    category: str            # "standard" | "custom"
    origin: str              # "rule" | "inference" | "unmatched"
    target_path: str         # "" when unresolved
    target_entity: str
    transformation: str
    confidence: float
    rationale: list[str]
    status: str
    auto_approvable: bool
    source_label: str | None = None
    applies_to_families: list[str] = dc_field(default_factory=list)
    examples: list[str] = dc_field(default_factory=list)
    occurrences: int = 0
    messages_present: int = 0
    stability: float | None = None
    target_required: bool = False
    target_terminology: str | None = None
    reviewer: str | None = None
    reviewer_comment: str | None = None
    reviewed_at: str | None = None
    original_target_path: str | None = None
    source_files: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _family(message_type: str) -> str:
    """ORU^R01 -> ORU."""
    return (message_type or "").split("^")[0].strip().upper()


def _examples(values: list[str], limit: int = 4) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
        if len(seen) >= limit:
            break
    return seen


def _status_for(confidence: float) -> str:
    return UNRESOLVED if confidence < UNRESOLVED_THRESHOLD else PROPOSED


def _extension_slug(path: str) -> str:
    return path.lower().replace("-", "_").replace(".", "_")


def _fallback_standard_target(path: str) -> str:
    return f"CustomExtension.{_extension_slug(path)}"


# ---------------------------------------------------------------------------
# Standard segment candidates
# ---------------------------------------------------------------------------

def _collect_standard(messages: list) -> dict[str, dict]:
    """Observe every populated standard field position across the corpus.

    Keyed by the path the rule table uses, so ``PID-5`` is expanded into its
    component paths when a rule addresses components directly.
    """
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
            if seg.is_z:
                continue
            # MSH field numbers run one ahead of the list index, because MSH-1
            # *is* the field separator and so is not itself an element.
            last = seg.field_count() + (1 if seg.name == "MSH" else 0)
            for pos in range(1, last + 1):
                offset = pos - 2 if seg.name == "MSH" else pos - 1
                if offset < 0 or offset >= len(seg.fields):
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

                    entry = observed.setdefault(path, {
                        "segment": seg.name,
                        "families": set(),
                        "values": [],
                        "occurrences": 0,
                        "messages": set(),
                    })
                    entry["families"].add(family)
                    entry["values"].append(value)
                    entry["occurrences"] += 1
                    entry["messages"].add(msg.source_file)

    return observed


def _standard_mappings(observed: dict[str, dict]) -> list[FieldMapping]:
    mappings: list[FieldMapping] = []

    for path in sorted(observed, key=_path_sort_key):
        entry = observed[path]
        base = path.split(".")[0]

        # A component path supersedes its parent when rules address components.
        if any(p.startswith(path + ".") for p in observed):
            continue
        if path in STRUCTURAL_PATHS:
            continue

        candidates = [
            r for r in RULES_BY_PATH.get(path, [])
            if any(r.applies_to(f) for f in entry["families"])
        ]

        if not candidates:
            target = _fallback_standard_target(path)
            attribute = cm.resolve(target)
            mappings.append(FieldMapping(
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
                    "No deterministic rule is defined yet for this standard field.",
                    f"Proposed as {target} so the value is retained pending review.",
                    "Reassign to a governed canonical attribute or approve as a "
                    "custom extension.",
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
            ))
            continue

        # The families each candidate actually governs within this corpus.
        scopes = {
            r.target_path: (set(r.families) & entry["families"]) or set(entry["families"])
            for r in candidates
        }

        # FR-013: rank candidates; the best becomes the proposal.
        candidates.sort(key=lambda r: r.confidence, reverse=True)
        rule = candidates[0]
        attribute = cm.resolve(rule.target_path)
        rationale = [rule.basis]
        confidence = rule.confidence
        scope = scopes[rule.target_path]

        rivals = [r for r in candidates[1:] if scopes[r.target_path] & scope]
        if rivals:
            others = ", ".join(r.target_path for r in rivals)
            rationale.append(
                f"Lower-ranked candidate target(s) for the same source: {others}. "
                "This proposal was selected automatically."
            )

        if len(entry["families"]) > 1:
            rationale.append(
                f"Applies to {'/'.join(sorted(scope))} messages only; other "
                "families in this corpus use this position differently."
            )

        rationale.append(
            f"Observed in {len(entry['messages'])} message(s), "
            f"{entry['occurrences']} occurrence(s)."
        )
        if attribute and attribute.terminology:
            rationale.append(
                f"Target attribute is bound to {attribute.terminology}; "
                "FR-029 will validate the code system on publish."
            )

        mappings.append(FieldMapping(
            id=f"std::{path}::{rule.target_path}",
            source_path=path,
            source_segment=entry["segment"],
            category="standard",
            origin="rule",
            target_path=rule.target_path,
            target_entity=rule.target_path.split(".")[0],
            transformation=rule.transformation,
            confidence=confidence,
            rationale=rationale,
            status=PROPOSED,
            auto_approvable=confidence >= AUTO_POPULATE_THRESHOLD,
            applies_to_families=sorted(scope),
            examples=_examples(entry["values"]),
            occurrences=entry["occurrences"],
            messages_present=len(entry["messages"]),
            target_required=bool(attribute and attribute.required),
            target_terminology=attribute.terminology if attribute else None,
            source_files=sorted(entry["messages"]),
        ))

    return mappings


def _path_sort_key(path: str) -> tuple:
    seg, _, rest = path.partition("-")
    parts = rest.split(".")
    return (seg, int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


# ---------------------------------------------------------------------------
# Z-segment candidates
# ---------------------------------------------------------------------------

# Where an inferred custom concept belongs when the lexicon recognises it.
# Anything absent from this table lands in CustomExtension, which is the honest
# answer rather than forcing it into a governed slot.
INFERRED_TARGETS: dict[str, str] = {
    "PREFERRED_LANG": "Patient.language",
    "CARE_TEAM": "Patient.careTeam",
    "MODALITY": "ImagingMetadata.modality",
    "ACCESSION": "ImagingMetadata.accessionNumber",
    "BODY_SITE": "ImagingMetadata.bodySite",
    "TEACHING_FILE": "ImagingMetadata.teachingFlag",
    "ROUTE_TO": "RoutingMetadata.destination",
    "PRIORITY": "RoutingMetadata.priority",
    "SPECIALTY": "RoutingMetadata.specialty",
    "RETENTION_YRS": "RoutingMetadata.retentionYears",
}

TRANSFORM_BY_DATATYPE = {
    "boolean": "boolean_yn",
    "integer": "to_integer",
    "decimal": "to_decimal",
    "datetime": "hl7_datetime",
    "code": "direct",
    "identifier": "direct",
    "string": "direct",
    "text": "direct",
}


def _custom_mappings(inferences: dict[str, list]) -> list[FieldMapping]:
    mappings: list[FieldMapping] = []

    for segment, results in sorted(inferences.items()):
        for inf in results:
            # Set ID is structural; it carries no payload to map.
            if inf.fhir_target.startswith("(structural"):
                continue

            label = inf.label
            governed = INFERRED_TARGETS.get(label or "")
            rationale = list(inf.reasoning)

            if governed:
                target = governed
                rationale.append(
                    f"Inferred meaning matches the governed canonical attribute "
                    f"{governed}; proposing it rather than a custom extension."
                )
            else:
                slug = inf.fhir_target[len("extension["):-1] if inf.fhir_target.startswith(
                    "extension[") else (label or inf.path).lower()
                target = f"CustomExtension.{slug}"
                rationale.append(
                    "No governed canonical attribute covers this concept, so it is "
                    "proposed as a custom property. DQ-03 keeps the value and its "
                    "source path rather than dropping it."
                )

            rationale.append(
                "RULE-002: site-defined content cannot be promoted without explicit "
                "human approval, regardless of inference confidence."
            )

            attribute = cm.resolve(target)
            confidence = inf.semantic_confidence

            mappings.append(FieldMapping(
                id=f"cust::{inf.path}",
                source_path=inf.path,
                source_segment=segment,
                category="custom",
                origin="inference",
                target_path=target if confidence >= UNRESOLVED_THRESHOLD else "",
                target_entity=target.split(".")[0] if confidence >= UNRESOLVED_THRESHOLD else "",
                transformation=TRANSFORM_BY_DATATYPE.get(inf.datatype, "direct"),
                confidence=confidence,
                rationale=rationale,
                status=_status_for(confidence),
                auto_approvable=False,  # RULE-002, without exception
                source_label=label,
                examples=inf.observed_values[:4],
                occurrences=inf.present_in,
                messages_present=inf.present_in,
                stability=inf.stability,
                target_required=bool(attribute and attribute.required),
                target_terminology=attribute.terminology if attribute else None,
                source_files=sorted(set(inf.source_files)),
            ))

    return mappings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def entities_in_play(messages: list) -> set[str]:
    """Canonical entities the corpus is expected to populate.

    Drives the FR-021 publication gate: only entities this corpus actually
    produces can be required to be complete.
    """
    entities = {"CanonicalMessage", "Patient"}
    for msg in messages:
        family = _family(msg.message_type)
        if family == "ORU":
            entities |= {"DiagnosticReport", "Observation"}
        elif family == "MDM":
            entities |= {"DocumentReference"}
        if msg.first("PV1"):
            entities |= {"Encounter"}
    return entities


def apply_rule001_auto_approval(mappings: list[FieldMapping]) -> int:
    """Promote every standard segment mapping to approved at ingest (RULE-001).

    Review is limited to Z-segments; all populated standard HL7 fields with a
    proposed target are approved automatically, regardless of confidence score.
    """
    now = datetime.now(timezone.utc).isoformat()
    approved = 0
    for mapping in mappings:
        if mapping.status != PROPOSED or mapping.category != "standard":
            continue
        if not mapping.target_path:
            continue
        if mapping.origin == "rule":
            comment = (
                "Auto-approved at ingest: standard HL7 field mapped from the "
                "v2.5.1 rule table."
            )
        elif mapping.origin == "fallback":
            comment = (
                "Auto-approved at ingest: standard field retained under "
                f"{mapping.target_path} pending governed-target assignment."
            )
        else:
            continue
        mapping.status = APPROVED
        mapping.reviewer = "RULE-001"
        mapping.reviewer_comment = comment
        mapping.reviewed_at = now
        approved += 1
    return approved


def build_mappings(messages: list, inferences: dict[str, list]) -> list[FieldMapping]:
    """Produce the full candidate mapping set for a corpus."""
    observed = _collect_standard(messages)
    mappings = _standard_mappings(observed) + _custom_mappings(inferences)
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
