"""Z-segment inference — deterministic baseline with explicit reasoning.

Site-defined Z-segments carry no published schema, and the trading partners in
this engagement do not supply data dictionaries for them. Meaning therefore has
to be inferred from the values themselves.

This module scores two things separately, because they answer different
questions and have different consequences:

  semantic_confidence  How sure are we what this field *means*?
                       Drives whether a human must confirm the mapping.

  stability            How consistently does the field *appear* across messages
                       carrying this segment? Drives whether the field can be
                       required or must be optional. Unstable cardinality is
                       what causes the customer's current pipeline to reject
                       whole messages.

A field can be perfectly understood and still unstable, which is precisely the
case the demo needs to show. Every score carries the reasons that produced it,
so a reviewer can audit the judgement instead of trusting a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
import re

# ---------------------------------------------------------------------------
# Healthcare token lexicon
# ---------------------------------------------------------------------------
# Tokens observed in site-defined segment labels, grouped by the FHIR element
# they most plausibly relate to. Token-level matching (rather than whole-label
# matching) is what lets an unseen label like SPECIMEN_COND resolve.

LEXICON: dict[str, str] = {
    "FASTING": "specimen collection state",
    "SPECIMEN": "specimen",
    "COND": "condition/quality qualifier",
    "CONDITION": "condition/quality qualifier",
    "COLLECTED": "collection event",
    "COLLECT": "collection event",
    "BY": "responsible actor",
    "INSTRUMENT": "measuring device",
    "DEVICE": "measuring device",
    "ANALYZER": "measuring device",
    "LANG": "language",
    "LANGUAGE": "language",
    "PREFERRED": "preference qualifier",
    "INTERPRETER": "interpreter service",
    "NEEDED": "requirement flag",
    "PORTAL": "patient portal",
    "ENROLLED": "enrolment flag",
    "CARE": "care delivery",
    "TEAM": "organisational unit",
    "ROUTE": "routing destination",
    "TO": "routing destination",
    "PRIORITY": "priority",
    "SPECIALTY": "clinical specialty",
    "RETENTION": "records retention",
    "YRS": "duration in years",
    "YEARS": "duration in years",
    "MODALITY": "imaging modality",
    "ACCESSION": "accession identifier",
    "BODY": "anatomical site",
    "SITE": "anatomical site",
    "TEACHING": "teaching/education flag",
    "FILE": "record classification",
    "PHYSICIAN": "practitioner",
    "NURSE": "practitioner",
    "ORDER": "order",
    "STATUS": "status",
    "REASON": "reason code",
    "COMMENT": "free-text note",
}

# Imaging modality codes (DICOM). Used to corroborate a MODALITY inference.
DICOM_MODALITIES = {"CR", "CT", "MR", "US", "XA", "NM", "PT", "DX", "MG", "RF", "OT"}


# ---------------------------------------------------------------------------
# Datatype detection
# ---------------------------------------------------------------------------

@dataclass
class DataType:
    name: str
    determinate: bool
    detail: str = ""


def detect_datatype(values: list[str]) -> DataType:
    """Classify a set of observed values for one field position."""
    vals = [v for v in values if v]
    if not vals:
        return DataType("empty", False, "no populated values observed")

    uniq = set(vals)

    if uniq <= {"Y", "N"}:
        return DataType("boolean", True, "values confined to Y/N")
    if uniq <= {"Y", "N", "U", ""}:
        return DataType("boolean", True, "values confined to Y/N/U")
    if all(re.fullmatch(r"\d+", v) for v in vals):
        return DataType("integer", True, "all values are integers")
    if all(re.fullmatch(r"\d+\.\d+", v) for v in vals):
        return DataType("decimal", True, "all values are decimals")
    if all(re.fullmatch(r"\d{8}(\d{4,6})?", v) for v in vals):
        return DataType("datetime", True, "all values match HL7 TS format")
    if all(re.fullmatch(r"[A-Z]{2,4}", v) for v in vals):
        kind = "DICOM modality code" if uniq <= DICOM_MODALITIES else "short uppercase code"
        return DataType("code", True, kind)
    if all(re.fullmatch(r"[A-Z0-9][A-Z0-9_\-]*", v) for v in vals):
        # Distinguish an enumerable code set from an opaque identifier by
        # whether the values look like words or like serial numbers.
        if all(re.search(r"\d{3,}", v) for v in vals):
            return DataType("identifier", True, "token containing a numeric run")
        return DataType("code", True, "uppercase token code")
    if all(len(v) < 64 for v in vals):
        return DataType("string", False, "short free text")
    return DataType("text", False, "long free text")


# ---------------------------------------------------------------------------
# Field observation and inference
# ---------------------------------------------------------------------------

@dataclass
class FieldObservation:
    """Everything the corpus tells us about one Z-segment field position."""

    segment: str
    position: int
    labels: list[str] = dc_field(default_factory=list)
    values: list[str] = dc_field(default_factory=list)
    raw_values: list[str] = dc_field(default_factory=list)
    present_in: int = 0
    total_occurrences: int = 0
    source_files: list[str] = dc_field(default_factory=list)


@dataclass
class Inference:
    segment: str
    path: str
    label: str | None
    inferred_meaning: str
    datatype: str
    semantic_confidence: float
    stability: float
    routing: str
    reasoning: list[str]
    fhir_target: str
    observed_values: list[str]
    present_in: int
    total_occurrences: int
    source_files: list[str] = dc_field(default_factory=list)


def _split_label(raw: str, component_sep: str = "^") -> tuple[str | None, str]:
    """Split a ``LABEL^VALUE`` field into its parts.

    A label is only recognised when the first component looks like a name
    (uppercase word tokens) and a second component exists to be its value.
    """
    parts = raw.split(component_sep)
    if len(parts) >= 2 and parts[0] and re.fullmatch(r"[A-Z][A-Z0-9_]*", parts[0]):
        if not re.fullmatch(r"\d+", parts[0]):
            return parts[0], component_sep.join(parts[1:])
    return None, raw


def _humanise(label: str) -> str:
    return label.replace("_", " ").lower()


def _to_camel(label: str) -> str:
    tokens = [t for t in label.split("_") if t]
    if not tokens:
        return "unknown"
    head, *tail = tokens
    return head.lower() + "".join(t.capitalize() for t in tail)


def _lexicon_hits(label: str) -> list[tuple[str, str]]:
    return [(t, LEXICON[t]) for t in label.split("_") if t in LEXICON]


def infer_field(obs: FieldObservation) -> Inference:
    """Score one field position and record why."""
    reasoning: list[str] = []
    semantic = 0.0

    label = obs.labels[0] if obs.labels else None
    labels_consistent = len(set(obs.labels)) <= 1

    # --- Field 1 of a Z-segment is conventionally a Set ID ------------------
    if obs.position == 1 and all(re.fullmatch(r"\d+", v) for v in obs.raw_values if v):
        return Inference(
            segment=obs.segment,
            path=f"{obs.segment}-1",
            label=None,
            inferred_meaning="Set ID (segment instance counter)",
            datatype="integer",
            semantic_confidence=0.95,
            stability=1.0,
            routing="AUTO_MAP",
            reasoning=[
                "Position 1 holds a small integer in every occurrence.",
                "HL7 convention reserves field 1 of a repeating segment for the Set ID.",
                "Structural field — carries no clinical payload, so it is not mapped to FHIR.",
            ],
            fhir_target="(structural — not mapped)",
            observed_values=sorted(set(obs.raw_values)),
            present_in=obs.present_in,
            total_occurrences=obs.total_occurrences,
            source_files=sorted(set(obs.source_files)),
        )

    # --- Signal 1: is the field self-describing? ----------------------------
    if label:
        semantic += 0.40
        reasoning.append(
            f"Field is self-describing: carries an inline label '{label}' in "
            f"component 1, with the value in component 2."
        )
        if labels_consistent:
            reasoning.append(f"Label is identical in all {obs.present_in} occurrence(s).")
        else:
            semantic -= 0.15
            reasoning.append(
                f"WARNING: label differs across occurrences ({sorted(set(obs.labels))}) — "
                "the position may not be stable."
            )
    else:
        reasoning.append(
            "Field carries a bare value with no inline label — nothing in the "
            "message declares what it means."
        )

    # --- Signal 2: does the label use known healthcare vocabulary? ----------
    if label:
        hits = _lexicon_hits(label)
        if hits:
            coverage = len(hits) / max(len(label.split("_")), 1)
            gain = 0.30 * coverage
            semantic += gain
            terms = ", ".join(f"'{t}' ({m})" for t, m in hits)
            reasoning.append(
                f"Label tokens resolve against the healthcare lexicon: {terms}. "
                f"Token coverage {coverage:.0%}."
            )
        else:
            reasoning.append(
                "Label tokens are not in the healthcare lexicon — meaning is "
                "guessed from surface form only."
            )

    # --- Signal 3: is the value's datatype determinate? ---------------------
    dt = detect_datatype(obs.values)
    if dt.determinate:
        semantic += 0.20
        reasoning.append(f"Value shape is determinate: {dt.name} ({dt.detail}).")
    else:
        reasoning.append(f"Value shape is indeterminate: {dt.name} ({dt.detail}).")

    # --- Signal 4: is the value set small and closed? -----------------------
    uniq = sorted(set(obs.values))
    if uniq and len(uniq) <= 3 and dt.determinate and obs.present_in > 1:
        semantic += 0.10
        reasoning.append(
            f"Value set is small and closed ({uniq}) — consistent with a coded field."
        )

    # --- Corroboration: DICOM modality --------------------------------------
    if label and "MODALITY" in label and set(obs.values) <= DICOM_MODALITIES:
        semantic = min(1.0, semantic + 0.10)
        reasoning.append(
            f"Values {uniq} are valid DICOM modality codes, corroborating the label."
        )

    # --- Evidence weight -----------------------------------------------------
    if obs.total_occurrences == 1:
        semantic -= 0.10
        reasoning.append(
            "Only one message in the corpus contains this segment — evidence is thin "
            "and the inference should be re-scored as volume grows."
        )

    semantic = max(0.0, min(1.0, semantic))

    # --- Stability (independent of meaning) ---------------------------------
    stability = obs.present_in / obs.total_occurrences if obs.total_occurrences else 0.0
    if stability < 1.0:
        reasoning.append(
            f"CARDINALITY VARIES: present in {obs.present_in} of "
            f"{obs.total_occurrences} occurrences of {obs.segment}. Must be modelled "
            "as optional; a pipeline that treats it as required would reject the "
            "messages that omit it."
        )
    elif obs.total_occurrences > 1:
        reasoning.append(
            f"Present in all {obs.total_occurrences} occurrences — cardinality is stable."
        )

    # --- Routing decision ----------------------------------------------------
    if semantic >= 0.85 and stability == 1.0:
        routing = "AUTO_MAP"
    elif semantic >= 0.85:
        routing = "AUTO_MAP_OPTIONAL"
    elif semantic >= 0.50:
        routing = "REVIEW"
    else:
        routing = "HUMAN_REQUIRED"

    meaning = _humanise(label) if label else "unknown — no label, opaque value"
    slug = _to_camel(label) if label else f"{obs.segment.lower()}Field{obs.position}"

    return Inference(
        segment=obs.segment,
        path=f"{obs.segment}-{obs.position}",
        label=label,
        inferred_meaning=meaning,
        datatype=dt.name,
        semantic_confidence=round(semantic, 2),
        stability=round(stability, 2),
        routing=routing,
        reasoning=reasoning,
        # Resource-agnostic: the mapper attaches this to whichever resource the
        # message type produces (Observation for ORU, DocumentReference for MDM).
        fhir_target=f"extension[{slug}]",
        observed_values=sorted(set(obs.raw_values)),
        present_in=obs.present_in,
        total_occurrences=obs.total_occurrences,
        source_files=sorted(set(obs.source_files)),
    )


def collect_observations(messages: list) -> dict[str, dict[int, FieldObservation]]:
    """Gather per-position evidence for every Z-segment across the corpus."""
    obs: dict[str, dict[int, FieldObservation]] = {}
    seg_counts: dict[str, int] = {}

    for msg in messages:
        for seg in msg.z_segments():
            seg_counts[seg.name] = seg_counts.get(seg.name, 0) + 1
            bucket = obs.setdefault(seg.name, {})
            for pos in range(1, seg.field_count() + 1):
                raw = seg.fields[pos - 1].strip() if pos - 1 < len(seg.fields) else ""
                if not raw:
                    continue
                fo = bucket.setdefault(pos, FieldObservation(seg.name, pos))
                label, value = _split_label(raw, msg.delimiters.component)
                if label:
                    fo.labels.append(label)
                fo.values.append(value)
                fo.raw_values.append(raw)
                fo.present_in += 1
                fo.source_files.append(msg.source_file)

    for name, bucket in obs.items():
        for fo in bucket.values():
            fo.total_occurrences = seg_counts[name]
    return obs


def infer_corpus(messages: list) -> dict[str, list[Inference]]:
    observations = collect_observations(messages)
    return {
        name: [infer_field(bucket[pos]) for pos in sorted(bucket)]
        for name, bucket in sorted(observations.items())
    }
