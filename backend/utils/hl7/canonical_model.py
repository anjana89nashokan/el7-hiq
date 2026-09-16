"""Governed canonical model for HL7 v2 ingestion.

BRD design principle DP-02 is "canonicalize once": partner messages are mapped
to a governed canonical model, and downstream formats (JSON/CSV for Workato,
optionally FHIR) are *projections* of that model. FHIR is therefore not the
target — it is one renderer of the target.

Entity and attribute names follow the proposed targets in BRD 14.3, which use
FHIR-flavoured naming (``Patient.identifier``, ``Observation.code``) without
committing to FHIR's structural rules. Domain coverage follows BRD 14.2; only
the Phase-1 domains are modelled here.

Attributes carry cardinality and terminology because the review workbench needs
them to enforce FR-021: a mapping cannot be published while a *required*
canonical attribute is still unresolved.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field


@dataclass(frozen=True)
class CanonicalAttribute:
    """One governed attribute on a canonical entity."""

    name: str
    datatype: str
    cardinality: str  # 0..1, 1..1, 0..*, 1..*
    description: str
    terminology: str | None = None

    @property
    def required(self) -> bool:
        return self.cardinality.startswith("1")

    @property
    def repeating(self) -> bool:
        return self.cardinality.endswith("*")


@dataclass(frozen=True)
class CanonicalEntity:
    name: str
    domain: str
    description: str
    attributes: tuple[CanonicalAttribute, ...] = dc_field(default_factory=tuple)

    def attribute(self, name: str) -> CanonicalAttribute | None:
        return next((a for a in self.attributes if a.name == name), None)


def _attr(name, datatype, cardinality, description, terminology=None):
    return CanonicalAttribute(name, datatype, cardinality, description, terminology)


# ---------------------------------------------------------------------------
# Integration metadata (BRD 14.2 "Integration metadata", DQ-05)
# ---------------------------------------------------------------------------

CANONICAL_MESSAGE = CanonicalEntity(
    name="CanonicalMessage",
    domain="Integration metadata",
    description=(
        "Provenance envelope retained for every message. DQ-05 requires source "
        "system, facility, control ID, event timestamp and mapping version to "
        "survive transformation so output can be traced back to its origin."
    ),
    attributes=(
        _attr("sourceControlId", "string", "1..1",
              "Message control ID; the correlation key for the whole pipeline."),
        _attr("sendingApplication", "string", "0..1", "Originating application."),
        _attr("sendingFacility", "string", "0..1", "Originating facility."),
        _attr("messageType", "code", "1..1", "Message type and trigger event.",
              "HL7 v2 table 0076/0003"),
        _attr("messageTimestamp", "dateTime", "1..1", "Message date and time."),
        _attr("hl7Version", "code", "1..1", "HL7 version identifier.", "HL7 v2 table 0104"),
        _attr("processingId", "code", "0..1", "Production, debug or training.",
              "HL7 v2 table 0103"),
        _attr("receivingApplication", "string", "0..1", "Receiving application."),
        _attr("receivingFacility", "string", "0..1", "Receiving facility."),
        _attr("eventTypeCode", "code", "0..1", "Event type code.", "HL7 v2 table 0003"),
        _attr("eventRecorded", "dateTime", "0..1", "When the event was recorded."),
        _attr("eventOccurred", "dateTime", "0..1", "When the event occurred."),
    ),
)

# ---------------------------------------------------------------------------
# Clinical identity (BRD 14.2)
# ---------------------------------------------------------------------------

PATIENT = CanonicalEntity(
    name="Patient",
    domain="Clinical identity",
    description=(
        "Subject of the message. DQ-06 forbids merging patients on name and "
        "date of birth alone, so identifier is required and carries its "
        "assigning authority."
    ),
    attributes=(
        _attr("identifier", "identifier", "1..*",
              "Patient identifier with assigning authority and type."),
        _attr("familyName", "string", "1..1", "Family name."),
        _attr("givenName", "string", "0..1", "Given name."),
        _attr("middleName", "string", "0..1", "Middle name or initial."),
        _attr("birthDate", "date", "0..1", "Date of birth."),
        _attr("gender", "code", "0..1", "Administrative sex.", "HL7 v2 table 0001"),
        _attr("addressLine", "string", "0..1", "Street address."),
        _attr("city", "string", "0..1", "City."),
        _attr("state", "string", "0..1", "State or province."),
        _attr("postalCode", "string", "0..1", "Postal code."),
        _attr("telecom", "string", "0..*", "Phone, email or other contact detail."),
        _attr("language", "code", "0..1", "Preferred language.", "BCP-47 / HL7 table 0296"),
        _attr("careTeam", "string", "0..*", "Care team or assigned unit."),
    ),
)

# ---------------------------------------------------------------------------
# Encounter (BRD 14.2)
# ---------------------------------------------------------------------------

ENCOUNTER = CanonicalEntity(
    name="Encounter",
    domain="Encounter",
    description="Visit context in which the observation or document was produced.",
    attributes=(
        _attr("class", "code", "0..1", "Patient class: inpatient, outpatient, emergency.",
              "HL7 v2 table 0004"),
        _attr("location", "string", "0..1", "Assigned point of care."),
        _attr("admitDateTime", "dateTime", "0..1", "Admission date and time."),
        _attr("dischargeDateTime", "dateTime", "0..1", "Discharge date and time."),
        _attr("attendingProvider", "string", "0..1", "Attending provider."),
        _attr("facility", "string", "0..1", "Servicing facility."),
    ),
)

# ---------------------------------------------------------------------------
# Observation / laboratory (BRD 14.2, Phase 1)
# ---------------------------------------------------------------------------

DIAGNOSTIC_REPORT = CanonicalEntity(
    name="DiagnosticReport",
    domain="Observation / laboratory",
    description="The ordered panel or study that groups a set of observations.",
    attributes=(
        _attr("code", "codeableConcept", "1..1", "Panel or service code.", "LOINC"),
        _attr("status", "code", "0..1", "Result status.", "HL7 v2 table 0123"),
        _attr("effectiveDateTime", "dateTime", "0..1", "Observation date and time."),
        _attr("placerOrderNumber", "identifier", "0..1", "Placer order number."),
        _attr("fillerOrderNumber", "identifier", "0..1", "Filler order number."),
        _attr("category", "code", "0..1", "Service section, e.g. laboratory.",
              "HL7 v2 table 0074"),
    ),
)

OBSERVATION = CanonicalEntity(
    name="Observation",
    domain="Observation / laboratory",
    description=(
        "A single result. FR-010 requires LOINC, UCUM units, reference ranges "
        "and abnormal flags to be preserved rather than flattened to a value."
    ),
    attributes=(
        _attr("code", "codeableConcept", "1..1", "Observation identifier.", "LOINC"),
        _attr("value", "polymorphic", "1..1",
              "Result value; concrete type is selected by the source value type."),
        _attr("valueType", "code", "0..1", "Source value type such as NM, ST, TX, CE.",
              "HL7 v2 table 0125"),
        _attr("unit", "code", "0..1", "Unit of measure.", "UCUM"),
        _attr("referenceRange", "string", "0..1", "Reference range as reported."),
        _attr("interpretation", "code", "0..1", "Abnormal flag.", "HL7 v2 table 0078"),
        _attr("status", "code", "0..1", "Observation result status.", "HL7 v2 table 0085"),
        _attr("effectiveDateTime", "dateTime", "0..1", "Observation date and time."),
        _attr("note", "string", "0..*", "Attached comment or note."),
    ),
)

# ---------------------------------------------------------------------------
# Clinical documents (BRD 14.2, Phase 1)
# ---------------------------------------------------------------------------

DOCUMENT_REFERENCE = CanonicalEntity(
    name="DocumentReference",
    domain="Clinical documents",
    description=(
        "A clinical document carried by MDM. RULE-013 keeps this separate from "
        "the observation transform because it is a different clinical artifact."
    ),
    attributes=(
        _attr("type", "codeableConcept", "1..1", "Document type."),
        _attr("masterIdentifier", "identifier", "1..1", "Unique document identifier."),
        _attr("docStatus", "code", "0..1", "Document completion status.",
              "HL7 v2 table 0271"),
        _attr("author", "string", "0..1", "Originating or authenticating provider."),
        _attr("activityDateTime", "dateTime", "0..1", "Activity date and time."),
        _attr("content", "text", "1..1",
              "Document body. Source line order is significant and must be preserved."),
        _attr("confidentiality", "code", "0..1", "Confidentiality status."),
    ),
)

# ---------------------------------------------------------------------------
# Imaging metadata (BRD 14.2)
# ---------------------------------------------------------------------------

IMAGING_METADATA = CanonicalEntity(
    name="ImagingMetadata",
    domain="Imaging metadata",
    description="Imaging descriptors attached to a report or document.",
    attributes=(
        _attr("modality", "code", "0..1", "Imaging modality.", "DICOM modality codes"),
        _attr("accessionNumber", "identifier", "0..1", "Accession number."),
        _attr("bodySite", "string", "0..1", "Anatomical site."),
        _attr("teachingFlag", "boolean", "0..1", "Approved for teaching use."),
    ),
)

# ---------------------------------------------------------------------------
# Routing metadata (BRD 14.3, ZDR)
# ---------------------------------------------------------------------------

ROUTING_METADATA = CanonicalEntity(
    name="RoutingMetadata",
    domain="Clinical documents",
    description="Delivery instructions that accompany a document.",
    attributes=(
        _attr("destination", "string", "0..1", "Destination queue or recipient."),
        _attr("priority", "code", "0..1", "Delivery priority."),
        _attr("specialty", "string", "0..1", "Clinical specialty."),
        _attr("retentionYears", "integer", "0..1", "Records retention period in years."),
    ),
)

# ---------------------------------------------------------------------------
# Extension container (BRD 14.3 "Custom", DQ-03, RULE-002)
# ---------------------------------------------------------------------------

CUSTOM_EXTENSION = CanonicalEntity(
    name="CustomExtension",
    domain="Integration metadata",
    description=(
        "Landing zone for site-defined content that has no governed attribute "
        "yet. DQ-03 forbids discarding unsupported fields, and RULE-002 forbids "
        "promoting them without human approval, so they are held here with their "
        "source path intact until a reviewer assigns a canonical target."
    ),
    attributes=(
        _attr("property", "any", "0..*",
              "Named custom property carrying its original source path."),
    ),
)


ENTITIES: tuple[CanonicalEntity, ...] = (
    CANONICAL_MESSAGE,
    PATIENT,
    ENCOUNTER,
    DIAGNOSTIC_REPORT,
    OBSERVATION,
    DOCUMENT_REFERENCE,
    IMAGING_METADATA,
    ROUTING_METADATA,
    CUSTOM_EXTENSION,
)

BY_NAME: dict[str, CanonicalEntity] = {e.name: e for e in ENTITIES}


def resolve(target_path: str) -> CanonicalAttribute | None:
    """Look up ``Entity.attribute``. Returns None for unknown or custom paths."""
    entity_name, _, attribute_name = target_path.partition(".")
    entity = BY_NAME.get(entity_name)
    if entity is None:
        return None
    # CustomExtension.<anything> is an open slot rather than a governed name.
    if entity is CUSTOM_EXTENSION:
        return CUSTOM_EXTENSION.attributes[0]
    return entity.attribute(attribute_name)


def is_governed(target_path: str) -> bool:
    """True when the target names a real attribute of a real entity."""
    return resolve(target_path) is not None


def required_attributes(entity_names: set[str]) -> list[tuple[str, CanonicalAttribute]]:
    """Required attributes across the given entities, as (entity, attribute).

    FR-021 uses this to decide which canonical slots must be filled before a
    mapping package may be published.
    """
    out: list[tuple[str, CanonicalAttribute]] = []
    for name in sorted(entity_names):
        entity = BY_NAME.get(name)
        if entity is None:
            continue
        out.extend((entity.name, a) for a in entity.attributes if a.required)
    return out


def as_dict() -> list[dict]:
    """Serialise the model for the frontend target picker."""
    return [
        {
            "name": e.name,
            "domain": e.domain,
            "description": e.description,
            "attributes": [
                {**asdict(a), "required": a.required, "repeating": a.repeating}
                for a in e.attributes
            ],
        }
        for e in ENTITIES
    ]
