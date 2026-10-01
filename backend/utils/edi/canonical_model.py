"""Governed canonical model for HIPAA X12 healthcare EDI.

Targets align with companion-guide concepts for eligibility (270/271, 005010X279),
claims (837), and remittance (835). Downstream JSON/CSV projections use these
entity names during mapping review and publication.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field


@dataclass(frozen=True)
class CanonicalAttribute:
    name: str
    datatype: str
    cardinality: str
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


INTERCHANGE_ENVELOPE = CanonicalEntity(
    name="InterchangeEnvelope",
    domain="X12 interchange",
    description="ISA/GS/ST envelope identifiers and control numbers.",
    attributes=(
        _attr("interchangeSenderId", "string", "1..1", "ISA06 interchange sender ID."),
        _attr("interchangeReceiverId", "string", "1..1", "ISA08 interchange receiver ID."),
        _attr("interchangeControlNumber", "string", "1..1", "ISA13 interchange control number."),
        _attr("functionalIdCode", "string", "0..1", "GS01 functional identifier code."),
        _attr("applicationSenderCode", "string", "0..1", "GS02 application sender code."),
        _attr("applicationReceiverCode", "string", "0..1", "GS03 application receiver code."),
        _attr("transactionSetId", "code", "1..1", "ST01 transaction set identifier.", "X12 TRN"),
        _attr("implementationGuide", "string", "0..1", "ST03 implementation convention reference."),
        _attr("transactionControlNumber", "string", "1..1", "ST02 transaction set control number."),
        _attr("x12Version", "string", "1..1", "ISA12 / GS08 version release."),
    ),
)

BATCH_HEADER = CanonicalEntity(
    name="BatchHeader",
    domain="270/271 batch",
    description="BHT batch header for eligibility and related transactions.",
    attributes=(
        _attr("structureCode", "code", "1..1", "BHT01 hierarchical structure code."),
        _attr("purposeCode", "code", "1..1", "BHT02 transaction set purpose code."),
        _attr("referenceId", "string", "1..1", "BHT03 originator application transaction ID."),
        _attr("transactionDate", "date", "0..1", "BHT04 transaction set creation date."),
        _attr("transactionTime", "time", "0..1", "BHT05 transaction set creation time."),
    ),
)

HIERARCHY = CanonicalEntity(
    name="Hierarchy",
    domain="HL loops",
    description="HL hierarchical level for payer / provider / subscriber / dependent.",
    attributes=(
        _attr("levelId", "string", "1..1", "HL01 hierarchical ID number."),
        _attr("parentId", "string", "0..1", "HL02 hierarchical parent ID."),
        _attr("levelCode", "code", "1..1", "HL03 hierarchical level code.", "X12 HL03"),
        _attr("childCode", "code", "0..1", "HL04 hierarchical child code."),
    ),
)

PARTY = CanonicalEntity(
    name="Party",
    domain="NM1 entities",
    description="Payer, provider, subscriber, or patient named entity (NM1).",
    attributes=(
        _attr("entityType", "code", "1..1", "NM101 entity identifier code.", "X12 NM101"),
        _attr("entityTypeQualifier", "code", "0..1", "NM102 entity type qualifier."),
        _attr("organizationName", "string", "0..1", "NM103 organization name."),
        _attr("lastName", "string", "0..1", "NM103 individual last name."),
        _attr("firstName", "string", "0..1", "NM104 individual first name."),
        _attr("identifierQualifier", "code", "0..1", "NM108 identification code qualifier."),
        _attr("identifier", "string", "0..1", "NM109 identification code."),
    ),
)

ELIGIBILITY_INQUIRY = CanonicalEntity(
    name="EligibilityInquiry",
    domain="270 eligibility",
    description="Service type inquiry (EQ) on a 270 request.",
    attributes=(
        _attr("serviceTypeCode", "code", "0..1", "EQ01 service type code.", "X12 EQ01"),
        _attr("inquiryDate", "date", "0..1", "DTP03 eligibility inquiry date."),
        _attr("traceNumber", "string", "0..1", "TRN02 trace number."),
        _attr("traceOriginator", "string", "0..1", "TRN03 trace originating company identifier."),
    ),
)

SUBSCRIBER = CanonicalEntity(
    name="Subscriber",
    domain="Subscriber demographics",
    description="Subscriber / patient demographics on eligibility transactions.",
    attributes=(
        _attr("birthDate", "date", "0..1", "DMG02 date of birth."),
        _attr("gender", "code", "0..1", "DMG03 gender code."),
    ),
)

ELIGIBILITY_RESPONSE = CanonicalEntity(
    name="EligibilityResponse",
    domain="271 eligibility",
    description="Eligibility or benefit response and validation outcomes.",
    attributes=(
        _attr("validRequestIndicator", "code", "0..1", "AAA01 valid request indicator."),
        _attr("rejectReasonCode", "code", "0..1", "AAA03 agency reject reason code."),
        _attr("followUpActionCode", "code", "0..1", "AAA04 follow-up action code."),
    ),
)

ENTITIES: tuple[CanonicalEntity, ...] = (
    INTERCHANGE_ENVELOPE,
    BATCH_HEADER,
    HIERARCHY,
    PARTY,
    ELIGIBILITY_INQUIRY,
    SUBSCRIBER,
    ELIGIBILITY_RESPONSE,
)

_BY_PATH: dict[str, CanonicalAttribute] = {}
for entity in ENTITIES:
    for attribute in entity.attributes:
        _BY_PATH[f"{entity.name}.{attribute.name}"] = attribute


def resolve(target_path: str) -> CanonicalAttribute | None:
    return _BY_PATH.get(target_path)


def is_governed(target_path: str) -> bool:
    if target_path.startswith("CustomExtension."):
        return True
    return target_path in _BY_PATH


def required_attributes(entities: set[str]) -> list[tuple[str, CanonicalAttribute]]:
    out: list[tuple[str, CanonicalAttribute]] = []
    for entity in ENTITIES:
        if entity.name not in entities:
            continue
        for attribute in entity.attributes:
            if attribute.required:
                out.append((entity.name, attribute))
    return out


def as_dict() -> list[dict]:
    return [
        {
            "name": e.name,
            "domain": e.domain,
            "description": e.description,
            "attributes": [
                {
                    "name": a.name,
                    "datatype": a.datatype,
                    "cardinality": a.cardinality,
                    "description": a.description,
                    "terminology": a.terminology,
                    "required": a.required,
                    "repeating": a.repeating,
                }
                for a in e.attributes
            ],
        }
        for e in ENTITIES
    ]
