"""X12 EDI upload and profiling — same session store and review flow as HL7."""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api.dependencies.auth import CurrentUser, resolve_current_user
from db.engine import app_db_session
from db.hl7_repository import Hl7Repository
from db.repositories import AppSessionRepository
from utils.edi import (
    build_mappings,
    canonical_model,
    entities_in_play,
    parse,
    profile,
    summarise,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _source_signature(messages: list) -> dict:
    senders = sorted({m.get("ISA-6") for m in messages if m.get("ISA-6")})
    receivers = sorted({m.get("ISA-8") for m in messages if m.get("ISA-8")})
    segments = sorted({name for m in messages for name in m.segment_names()})
    guides = sorted({m.implementation_guide for m in messages if m.implementation_guide})
    return {
        "interchange_senders": senders,
        "interchange_receivers": receivers,
        "transaction_sets": sorted({m.transaction_set for m in messages}),
        "implementation_guides": guides,
        "x12_versions": sorted({m.version for m in messages if m.version}),
        "segments": segments,
    }


@router.get("/canonical-model")
async def get_edi_canonical_model() -> dict:
    return {"entities": canonical_model.as_dict()}


@router.post("/upload")
async def upload_edi(
    files: list[UploadFile] = File(...),
    app_session_id: str | None = Form(default=None),
    current_user: CurrentUser = Depends(resolve_current_user),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="no files supplied")

    messages = []
    file_results: list[dict] = []

    for upload in files:
        raw = await upload.read()
        name = upload.filename or "unnamed.edi"
        try:
            msg = parse(_decode(raw), source_file=name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("EDI parse failed for %s: %s", name, exc)
            file_results.append({
                "filename": name,
                "status": "failed",
                "error": str(exc),
            })
            continue

        messages.append(msg)
        file_prof = profile([msg])
        file_results.append({
            "filename": name,
            "status": "parsed",
            "message_type": msg.message_type,
            "version": msg.version,
            "control_id": msg.control_id,
            "segment_count": len(msg.segments),
            "segments": msg.segment_names(),
            "z_segments": [],
            "profile": {
                "message_count": file_prof.message_count,
                "per_type_structure": {
                    k: dict(v) for k, v in file_prof.per_type_structure.items()
                },
                "segments": [asdict(s) for s in file_prof.segments],
            },
            "inference": {},
        })

    if not messages:
        raise HTTPException(
            status_code=400,
            detail="no X12 interchanges could be parsed. "
                   "Check that each file begins with an ISA segment.",
        )

    prof = profile(messages)
    mappings = build_mappings(messages)
    entities = entities_in_play(messages)

    session_id = uuid.uuid4().hex
    payload = {
        "format": "x12",
        "hl7_session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "files_received": len(files),
            "messages_parsed": len(messages),
            "messages_failed": len(files) - len(messages),
            "message_types": dict(prof.message_types),
            "versions": dict(prof.versions),
            "z_segment_names": [],
            "z_field_count": 0,
            "fhir_documents": 0,
            "implementation_guides": sorted(
                {m.implementation_guide for m in messages if m.implementation_guide}
            ),
        },
        "routing_summary": {},
        "files": file_results,
        "profile": {
            "message_count": prof.message_count,
            "per_type_structure": {k: dict(v) for k, v in prof.per_type_structure.items()},
            "segments": [asdict(s) for s in prof.segments],
        },
        "inference": {},
        "canonical_entities": sorted(entities),
        "source_signature": _source_signature(messages),
        "mapping_summary": summarise(mappings),
    }

    try:
        with app_db_session() as db:
            repo = Hl7Repository(db)
            linked_app_session_id: str | None = None
            if app_session_id:
                app_repo = AppSessionRepository(db)
                app_session = app_repo.get_session(
                    session_id=app_session_id,
                    user_key=current_user.user_key,
                )
                if app_session:
                    linked_app_session_id = app_session.id

            repo.create(
                session_id,
                payload,
                [asdict(m) for m in mappings],
                {},
                app_session_id=linked_app_session_id,
                user_key=current_user.user_key,
            )
            repo.sync_custom_targets_from_mappings(session_id)
            if linked_app_session_id:
                app_repo.link_hl7_session(
                    session=app_session,
                    hl7_session_id=session_id,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist EDI session %s: %s", session_id, exc)

    payload["fhir_available"] = []
    return payload
