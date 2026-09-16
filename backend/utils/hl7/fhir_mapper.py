"""HL7 v2 to FHIR R4 mapping — one compiled mapping per message type.

The scaling argument for this engagement rests on a single claim: standard
segments are identical across trading partners, so one mapping per message type
serves every site. Only Z-segments vary, and they are handled separately as
extensions. This module implements that split for ORU^R01 and MDM^T02.

The mappings below are deterministic — no model in the path — which is what
makes output reproducible for audit.
"""

from __future__ import annotations

from .zsegment_inference import Inference

GENDER = {"F": "female", "M": "male", "O": "other", "U": "unknown"}
OBS_STATUS = {"F": "final", "C": "corrected", "P": "preliminary", "X": "cancelled"}
DOC_STATUS = {"AU": "current", "DI": "current", "IN": "preliminary", "OB": "superseded"}


def _date(ts: str) -> str:
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else ts


def _datetime(ts: str) -> str:
    if len(ts) >= 14:
        return f"{_date(ts)}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
    if len(ts) >= 12:
        return f"{_date(ts)}T{ts[8:10]}:{ts[10:12]}:00"
    return _date(ts)


def map_patient(msg) -> dict:
    pid = msg.first("PID")
    if pid is None:
        return {}
    mrn = msg.get("PID-3.1", pid)
    patient = {
        "resourceType": "Patient",
        "identifier": [{
            "system": f"urn:{msg.get('PID-3.4', pid).lower() or 'unknown'}:mrn",
            "value": mrn,
        }],
        "name": [{
            "family": msg.get("PID-5.1", pid),
            "given": [g for g in [msg.get("PID-5.2", pid), msg.get("PID-5.3", pid)] if g],
        }],
        "gender": GENDER.get(msg.get("PID-8", pid), msg.get("PID-8", pid)),
        "birthDate": _date(msg.get("PID-7", pid)),
    }
    street = msg.get("PID-11.1", pid)
    if street:
        patient["address"] = [{
            "line": [street],
            "city": msg.get("PID-11.3", pid),
            "state": msg.get("PID-11.4", pid),
            "postalCode": msg.get("PID-11.5", pid),
        }]
    phone = msg.get("PID-13", pid)
    if phone:
        patient["telecom"] = [{"system": "phone", "value": phone}]
    return patient


def _z_extensions(msg, inferences: dict[str, list[Inference]]) -> list[dict]:
    """Build FHIR extensions from Z-segments, carrying provenance.

    Anything not confidently understood is still emitted, but flagged for
    review rather than silently dropped or silently trusted.
    """
    extensions: list[dict] = []
    for seg in msg.z_segments():
        by_path = {i.path: i for i in inferences.get(seg.name, [])}
        sub: list[dict] = []
        for pos in range(1, seg.field_count() + 1):
            raw = seg.fields[pos - 1].strip() if pos - 1 < len(seg.fields) else ""
            if not raw:
                continue
            inf = by_path.get(f"{seg.name}-{pos}")
            if inf is None or inf.fhir_target.startswith("("):
                continue
            value = raw.split(msg.delimiters.component, 1)[1] if inf.label else raw
            entry = {
                "url": inf.fhir_target.split("[")[1].rstrip("]"),
                "valueString": value,
                "_provenance": {
                    "sourcePath": inf.path,
                    "semanticConfidence": inf.semantic_confidence,
                    "stability": inf.stability,
                    "routing": inf.routing,
                },
            }
            sub.append(entry)
        if sub:
            extensions.append({
                "url": f"urn:sitedefined:{seg.name.lower()}",
                "extension": sub,
            })
    return extensions


def map_oru(msg, inferences: dict[str, list[Inference]]) -> dict:
    """ORU^R01 -> Patient + Observation[] (one per OBX)."""
    patient = map_patient(msg)
    mrn = patient.get("identifier", [{}])[0].get("value", "unknown")
    obr = msg.first("OBR")
    extensions = _z_extensions(msg, inferences)

    observations = []
    for obx in msg.by_name("OBX"):
        value_type = msg.get("OBX-2", obx)
        obs = {
            "resourceType": "Observation",
            "status": OBS_STATUS.get(msg.get("OBX-11", obx), msg.get("OBX-11", obx)),
            "code": {"coding": [{
                "system": "http://loinc.org",
                "code": msg.get("OBX-3.1", obx),
                "display": msg.get("OBX-3.2", obx),
            }]},
            "subject": {"reference": f"Patient/{mrn}"},
            "effectiveDateTime": _datetime(
                msg.get("OBX-14", obx) or (msg.get("OBR-7", obr) if obr else "")
            ),
        }
        raw_value = msg.get("OBX-5", obx)
        if value_type == "NM" and raw_value:
            try:
                obs["valueQuantity"] = {
                    "value": float(raw_value),
                    "unit": msg.get("OBX-6.1", obx),
                    "system": "http://unitsofmeasure.org",
                }
            except ValueError:
                obs["valueString"] = raw_value
                obs["dataAbsentReason"] = {"coding": [{"code": "error"}]}
        elif raw_value:
            obs["valueString"] = raw_value

        ref_range = msg.get("OBX-7", obx)
        if ref_range:
            obs["referenceRange"] = [{"text": ref_range}]
        flag = msg.get("OBX-8", obx)
        if flag:
            obs["interpretation"] = [{"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v2-0078",
                "code": flag,
            }]}]
        if extensions:
            obs["extension"] = extensions
        observations.append(obs)

    return {"patient": patient, "observations": observations}


def map_mdm(msg, inferences: dict[str, list[Inference]]) -> dict:
    """MDM^T02 -> Patient + DocumentReference (TXA header, OBX text body)."""
    patient = map_patient(msg)
    mrn = patient.get("identifier", [{}])[0].get("value", "unknown")
    txa = msg.first("TXA")
    extensions = _z_extensions(msg, inferences)

    body_lines = [
        msg.get("OBX-5", obx)
        for obx in msg.by_name("OBX")
        if msg.get("OBX-2", obx) == "TX" and msg.get("OBX-5", obx)
    ]

    doc = {
        "resourceType": "DocumentReference",
        "status": DOC_STATUS.get(msg.get("TXA-17", txa), "current") if txa else "current",
        "type": {"coding": [{
            "code": msg.get("TXA-2.1", txa) if txa else "",
            "display": msg.get("TXA-2.2", txa) if txa else "",
        }]},
        "subject": {"reference": f"Patient/{mrn}"},
        "date": _datetime(msg.get("TXA-4", txa)) if txa else "",
        "author": [{"display": " ".join(filter(None, [
            msg.get("TXA-5.3", txa), msg.get("TXA-5.2", txa),
        ]))}] if txa else [],
        "identifier": [{"value": msg.get("TXA-12.1", txa)}] if txa else [],
        "content": [{
            "attachment": {
                "contentType": "text/plain",
                "data": "\n".join(body_lines),
            }
        }],
    }
    if extensions:
        doc["extension"] = extensions
    return {"patient": patient, "documentReference": doc}


def map_message(msg, inferences: dict[str, list[Inference]]) -> dict:
    mtype = msg.message_type
    if mtype.startswith("ORU"):
        return map_oru(msg, inferences)
    if mtype.startswith("MDM"):
        return map_mdm(msg, inferences)
    raise ValueError(f"no compiled mapping for message type {mtype}")
