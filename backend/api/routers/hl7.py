"""HL7 v2 ingestion and mapping-review endpoints.

The message-shaped counterpart to ``/files``. HL7 files bypass the tabular path
entirely: they are never converted to a DataFrame and never landed as a SQL
table, because an HL7 message is a tree rather than a row.

The flow follows the Phase-1 workflow in the Vituity BRD: upload and detect,
profile, propose canonical mappings with confidence and rationale, take those
through human review, then publish an immutable versioned mapping package.

Session data is persisted in ``app.db`` (``hl7_sessions`` and related tables),
consistent with profiling and mapping runs elsewhere in the app.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.dependencies.auth import CurrentUser, resolve_current_user
from db.engine import app_db_session
from db.hl7_repository import Hl7Repository
from db.repositories import AppSessionRepository
from utils.hl7 import (
    ReviewError,
    apply_review,
    build_mappings,
    bulk_approve_auto,
    bulk_approve_mappings,
    canonical_model,
    entities_in_play,
    get_version,
    infer_corpus,
    list_versions,
    load_audit,
    load_mappings,
    map_message,
    parse,
    profile,
    publish,
    readiness,
    save_mappings,
    summarise,
    sync_custom_targets_from_mappings,
)

logger = logging.getLogger(__name__)

router = APIRouter()

ROUTING_ORDER = ["AUTO_MAP", "AUTO_MAP_OPTIONAL", "REVIEW", "HUMAN_REQUIRED"]


class ReviewRequest(BaseModel):
    mapping_id: str
    action: str = Field(description="approve | reject | defer | edit | reset")
    target_path: str | None = None
    transformation: str | None = None
    comment: str | None = None


class PublishRequest(BaseModel):
    note: str | None = None


class BulkApproveRequest(BaseModel):
    mapping_ids: list[str] = Field(min_length=1)
    comment: str | None = None


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _validate_session_id(hl7_session_id: str) -> None:
    if not hl7_session_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid session id")



def _session_context(hl7_session_id: str) -> tuple[set[str], dict]:
    with app_db_session() as db:
        result = Hl7Repository(db).load_result(hl7_session_id)
    return set(result.get("canonical_entities", [])), result.get("source_signature", {})


def _actor(user: CurrentUser) -> str:
    return user.user_email or user.user_key


def _source_signature(messages: list) -> dict:
    senders = sorted({m.get("MSH-3") for m in messages if m.get("MSH-3")})
    facilities = sorted({m.get("MSH-4") for m in messages if m.get("MSH-4")})
    segments = sorted({name for m in messages for name in m.segment_names()})
    return {
        "sending_applications": senders,
        "sending_facilities": facilities,
        "message_types": sorted({m.message_type for m in messages}),
        "hl7_versions": sorted({m.version for m in messages if m.version}),
        "segments": segments,
    }


@router.get("/canonical-model")
async def get_canonical_model() -> dict:
    return {"entities": canonical_model.as_dict()}


@router.post("/upload")
async def upload_hl7(
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
        name = upload.filename or "unnamed.hl7"
        try:
            msg = parse(_decode(raw), source_file=name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HL7 parse failed for %s: %s", name, exc)
            file_results.append({
                "filename": name,
                "status": "failed",
                "error": str(exc),
            })
            continue

        messages.append(msg)
        file_prof = profile([msg])
        file_inf = infer_corpus([msg])
        file_results.append({
            "filename": name,
            "status": "parsed",
            "message_type": msg.message_type,
            "version": msg.version,
            "control_id": msg.control_id,
            "segment_count": len(msg.segments),
            "segments": msg.segment_names(),
            "z_segments": [s.name for s in msg.z_segments()],
            "profile": {
                "message_count": file_prof.message_count,
                "per_type_structure": {
                    k: dict(v) for k, v in file_prof.per_type_structure.items()
                },
                "segments": [asdict(s) for s in file_prof.segments],
            },
            "inference": {
                k: [asdict(i) for i in v] for k, v in file_inf.items()
            },
        })

    if not messages:
        raise HTTPException(
            status_code=400,
            detail="no HL7 messages could be parsed. "
                   "Check that each file begins with an MSH segment.",
        )

    prof = profile(messages)
    inferences = infer_corpus(messages)
    mappings = build_mappings(messages, inferences)
    entities = entities_in_play(messages)

    routing_counts: Counter = Counter()
    for results in inferences.values():
        for inf in results:
            routing_counts[inf.routing] += 1

    fhir_docs: dict[str, dict] = {}
    for msg in messages:
        try:
            fhir_docs[Path(msg.source_file).stem.replace("/", "_")] = map_message(
                msg, inferences
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("FHIR mapping failed for %s: %s", msg.source_file, exc)
            for entry in file_results:
                if entry["filename"] == msg.source_file:
                    entry["fhir_error"] = str(exc)

    hl7_session_id = uuid.uuid4().hex
    payload = {
        "hl7_session_id": hl7_session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "files_received": len(files),
            "messages_parsed": len(messages),
            "messages_failed": len(files) - len(messages),
            "message_types": dict(prof.message_types),
            "versions": dict(prof.versions),
            "z_segment_names": prof.z_segment_names,
            "z_field_count": sum(routing_counts.values()),
            "fhir_documents": len(fhir_docs),
        },
        "routing_summary": {k: routing_counts.get(k, 0) for k in ROUTING_ORDER},
        "files": file_results,
        "profile": {
            "message_count": prof.message_count,
            "per_type_structure": {k: dict(v) for k, v in prof.per_type_structure.items()},
            "segments": [asdict(s) for s in prof.segments],
        },
        "inference": {k: [asdict(i) for i in v] for k, v in inferences.items()},
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
                hl7_session_id,
                payload,
                [asdict(m) for m in mappings],
                fhir_docs,
                app_session_id=linked_app_session_id,
                user_key=current_user.user_key,
            )
            repo.sync_custom_targets_from_mappings(hl7_session_id)
            if linked_app_session_id:
                app_repo.link_hl7_session(
                    session=app_session,
                    hl7_session_id=hl7_session_id,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist HL7 session %s: %s", hl7_session_id, exc)

    payload["fhir_available"] = sorted(fhir_docs.keys())
    return payload


@router.get("/sessions")
async def list_hl7_sessions(
    current_user: CurrentUser = Depends(resolve_current_user),
) -> dict:
    sessions: list[dict] = []
    with app_db_session() as db:
        repo = Hl7Repository(db)
        for row in repo.list_sessions():
            if row.user_key and row.user_key != current_user.user_key:
                continue
            mappings = list(row.mappings_json or [])
            reviewed = sum(1 for m in mappings if m.get("reviewed_at"))
            approved = sum(1 for m in mappings if m.get("status") == "approved")
            pending = sum(
                1 for m in mappings if m.get("status") in ("proposed", "unresolved")
            )
            versions = repo.list_versions(row.id)
            result = row.result_json or {}
            if versions:
                status = f"published v{versions[-1]['version']}"
            elif reviewed:
                status = "in review"
            elif mappings:
                status = "profiled"
            else:
                status = "profiled (legacy)"
            summary = result.get("summary", {})
            sessions.append({
                "hl7_session_id": result.get("hl7_session_id", row.id),
                "created_at": result.get("created_at"),
                "status": status,
                "messages_parsed": summary.get("messages_parsed", 0),
                "messages_failed": summary.get("messages_failed", 0),
                "message_types": summary.get("message_types", {}),
                "z_segment_names": summary.get("z_segment_names", []),
                "mappings_total": len(mappings),
                "mappings_approved": approved,
                "mappings_pending": pending,
                "latest_version": versions[-1]["version"] if versions else None,
                "published_at": versions[-1]["published_at"] if versions else None,
            })

    sessions.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return {"sessions": sessions}


@router.delete("/sessions/{hl7_session_id}")
async def delete_hl7_session(hl7_session_id: str) -> dict:
    _validate_session_id(hl7_session_id)
    with app_db_session() as db:
        deleted = Hl7Repository(db).delete_session(hl7_session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="HL7 session not found")
    return {"deleted": hl7_session_id}


@router.get("/sessions/{hl7_session_id}")
async def get_hl7_session(hl7_session_id: str) -> dict:
    _validate_session_id(hl7_session_id)
    with app_db_session() as db:
        repo = Hl7Repository(db)
        try:
            payload = repo.load_result(hl7_session_id)
            payload["fhir_available"] = repo.list_fhir_names(hl7_session_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="HL7 session not found") from exc
    return payload


@router.get("/sessions/{hl7_session_id}/fhir/{name}")
async def get_hl7_fhir(hl7_session_id: str, name: str) -> dict:
    _validate_session_id(hl7_session_id)
    safe = Path(name).stem.replace("/", "_")
    with app_db_session() as db:
        repo = Hl7Repository(db)
        try:
            doc = repo.load_fhir(hl7_session_id, safe)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="HL7 session not found") from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="FHIR document not found")
    return doc


@router.get("/sessions/{hl7_session_id}/mappings")
async def get_mappings(hl7_session_id: str) -> dict:
    _validate_session_id(hl7_session_id)
    entities, _ = _session_context(hl7_session_id)
    try:
        mappings = load_mappings(hl7_session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="HL7 session not found") from exc
    if not mappings:
        raise HTTPException(
            status_code=404,
            detail="no mapping package exists for this session. It was created "
                   "before mapping recommendation was added — re-upload the files.",
        )

    with app_db_session() as db:
        result = Hl7Repository(db).load_result(hl7_session_id)

    return {
        "hl7_session_id": hl7_session_id,
        "mappings": mappings,
        "files": result.get("files", []),
        "summary": {
            "total": len(mappings),
            "by_status": dict(Counter(m["status"] for m in mappings)),
            "standard": sum(1 for m in mappings if m["category"] == "standard"),
            "custom": sum(1 for m in mappings if m["category"] == "custom"),
            "auto_approvable": sum(
                1 for m in mappings
                if m["auto_approvable"] and m["status"] == "proposed"
            ),
        },
        "readiness": readiness(hl7_session_id, entities),
        "canonical_entities": sorted(entities),
        "versions": list_versions(hl7_session_id),
        "custom_targets": sync_custom_targets_from_mappings(hl7_session_id),
    }


@router.post("/sessions/{hl7_session_id}/review")
async def review_mapping(
    hl7_session_id: str,
    body: ReviewRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
) -> dict:
    _validate_session_id(hl7_session_id)
    entities, _ = _session_context(hl7_session_id)
    try:
        mapping = apply_review(
            hl7_session_id,
            body.mapping_id,
            body.action,
            actor=_actor(current_user),
            target_path=body.target_path,
            transformation=body.transformation,
            comment=body.comment,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="HL7 session not found") from exc

    return {
        "mapping": mapping,
        "readiness": readiness(hl7_session_id, entities),
        "custom_targets": sync_custom_targets_from_mappings(hl7_session_id),
    }


@router.post("/sessions/{hl7_session_id}/bulk-approve")
async def bulk_approve(
    hl7_session_id: str,
    current_user: CurrentUser = Depends(resolve_current_user),
) -> dict:
    _validate_session_id(hl7_session_id)
    entities, _ = _session_context(hl7_session_id)
    try:
        approved = bulk_approve_auto(hl7_session_id, actor=_actor(current_user))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="HL7 session not found") from exc
    return {
        "approved": approved,
        "skipped": 0,
        "readiness": readiness(hl7_session_id, entities),
        "mappings": load_mappings(hl7_session_id),
    }


@router.post("/sessions/{hl7_session_id}/bulk-approve-selected")
async def bulk_approve_selected(
    hl7_session_id: str,
    body: BulkApproveRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
) -> dict:
    """Approve an explicit list of mappings (view, file, or session scope from the UI)."""
    _validate_session_id(hl7_session_id)
    entities, _ = _session_context(hl7_session_id)
    try:
        result = bulk_approve_mappings(
            hl7_session_id,
            body.mapping_ids,
            actor=_actor(current_user),
            comment=body.comment,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="HL7 session not found") from exc
    return {
        **result,
        "readiness": readiness(hl7_session_id, entities),
        "mappings": load_mappings(hl7_session_id),
    }


@router.post("/sessions/{hl7_session_id}/publish")
async def publish_package(
    hl7_session_id: str,
    body: PublishRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
) -> dict:
    _validate_session_id(hl7_session_id)
    entities, signature = _session_context(hl7_session_id)
    try:
        package = publish(
            hl7_session_id,
            actor=_actor(current_user),
            entities=entities,
            source_signature=signature,
            note=body.note,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="HL7 session not found") from exc

    return {"package": package, "versions": list_versions(hl7_session_id)}


@router.get("/sessions/{hl7_session_id}/versions")
async def get_versions(hl7_session_id: str) -> dict:
    _validate_session_id(hl7_session_id)
    try:
        return {"versions": list_versions(hl7_session_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="HL7 session not found") from exc


@router.get("/sessions/{hl7_session_id}/versions/{version}")
async def get_package_version(hl7_session_id: str, version: int) -> dict:
    _validate_session_id(hl7_session_id)
    try:
        package = get_version(hl7_session_id, version)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="HL7 session not found") from exc
    if package is None:
        raise HTTPException(status_code=404, detail=f"version {version} not found")
    return package


@router.get("/sessions/{hl7_session_id}/audit")
async def get_audit(hl7_session_id: str) -> dict:
    _validate_session_id(hl7_session_id)
    try:
        return {"events": load_audit(hl7_session_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="HL7 session not found") from exc


@router.get("/sessions/{hl7_session_id}/export/mappings")
async def export_mappings(hl7_session_id: str) -> Response:
    _validate_session_id(hl7_session_id)
    try:
        mappings = load_mappings(hl7_session_id)
        with app_db_session() as db:
            result = Hl7Repository(db).load_result(hl7_session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="HL7 session not found") from exc
    if not mappings:
        raise HTTPException(
            status_code=404,
            detail="no mapping package exists for this session.",
        )

    payload = {
        "hl7_session_id": hl7_session_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(mappings),
            "by_status": dict(Counter(m["status"] for m in mappings)),
            "standard": sum(1 for m in mappings if m["category"] == "standard"),
            "custom": sum(1 for m in mappings if m["category"] == "custom"),
            "approved": sum(1 for m in mappings if m["status"] == "approved"),
        },
        "files": result.get("files", []),
        "canonical_entities": result.get("canonical_entities", []),
        "versions": list_versions(hl7_session_id),
        "custom_targets": sync_custom_targets_from_mappings(hl7_session_id),
        "mappings": mappings,
    }
    filename = f"hl7-mappings-{hl7_session_id[:8]}.json"
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
