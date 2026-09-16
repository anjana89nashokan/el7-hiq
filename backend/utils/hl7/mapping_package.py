"""Mapping package persistence, review decisions, audit trail and versioning.

Covers BRD FR-019 to FR-024 and business rules RULE-005 and RULE-007.

The working set of mappings is mutable and lives in ``app.db`` (``hl7_sessions``
table). Publishing snapshots the approved subset into ``hl7_published_packages``,
which is never rewritten — RULE-005 makes a published package immutable, so a
correction is a new version rather than an edit.

Every state change appends to ``hl7_audit_events``. NFR-008 requires mapping and
approval actions to be immutable and searchable, and FR-037 requires the
evidence to be exportable for the Workato review, so the audit log records the
before and after state of each decision rather than just the fact that it
changed.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from db.engine import app_db_session
from db.hl7_repository import Hl7Repository

from . import canonical_model as cm
from .mapping_engine import (
    APPROVED,
    DEFERRED,
    PROPOSED,
    REJECTED,
    UNRESOLVED,
    FieldMapping,
)

REVIEW_ACTIONS = {"approve", "reject", "defer", "edit", "reset"}

ACTION_STATUS = {
    "approve": APPROVED,
    "reject": REJECTED,
    "defer": DEFERRED,
    "edit": APPROVED,
}


class ReviewError(Exception):
    """A review or publish action that violates a governance rule."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Storage (app.db via Hl7Repository)
# ---------------------------------------------------------------------------

def save_mappings(session_id: str, mappings: list[FieldMapping]) -> None:
    with app_db_session() as db:
        Hl7Repository(db).save_mappings(session_id, [asdict(m) for m in mappings])


def load_mappings(session_id: str) -> list[dict]:
    with app_db_session() as db:
        return Hl7Repository(db).load_mappings(session_id)


def append_audit(session_id: str, event: dict) -> None:
    with app_db_session() as db:
        Hl7Repository(db).append_audit(session_id, event)


def load_audit(session_id: str) -> list[dict]:
    with app_db_session() as db:
        return Hl7Repository(db).load_audit(session_id)


def load_custom_targets(session_id: str) -> list[dict]:
    with app_db_session() as db:
        return Hl7Repository(db).load_custom_targets(session_id)


def register_custom_target(
    session_id: str,
    target_path: str,
    *,
    actor: str,
    description: str = "",
    source_path: str = "",
) -> dict:
    with app_db_session() as db:
        return Hl7Repository(db).register_custom_target(
            session_id,
            target_path,
            actor=actor,
            description=description,
            source_path=source_path,
        )


def sync_custom_targets_from_mappings(session_id: str) -> list[dict]:
    with app_db_session() as db:
        return Hl7Repository(db).sync_custom_targets_from_mappings(session_id)


# ---------------------------------------------------------------------------
# Review actions (FR-019, FR-020)
# ---------------------------------------------------------------------------

def apply_review(
    session_id: str,
    mapping_id: str,
    action: str,
    *,
    actor: str,
    target_path: str | None = None,
    transformation: str | None = None,
    comment: str | None = None,
) -> dict:
    """Record one reviewer decision against one mapping."""
    if action not in REVIEW_ACTIONS:
        raise ReviewError(f"unknown review action {action!r}")

    with app_db_session() as db:
        repo = Hl7Repository(db)
        mappings = repo.load_mappings(session_id)
        if not mappings:
            raise ReviewError("no mapping package exists for this session")

        index = next((i for i, m in enumerate(mappings) if m["id"] == mapping_id), None)
        if index is None:
            raise ReviewError(f"mapping {mapping_id!r} not found")

        before = dict(mappings[index])
        mapping = dict(before)

        is_override = (
            action == "edit"
            and target_path is not None
            and target_path != before.get("target_path")
        )

        if (action == "reject" or is_override) and not (comment or "").strip():
            raise ReviewError(
                "A reviewer comment is required when rejecting or manually "
                "overriding a proposed mapping (FR-020)."
            )

        if action == "reset":
            mapping["status"] = UNRESOLVED if not mapping.get("target_path") else PROPOSED
            mapping["reviewer"] = None
            mapping["reviewer_comment"] = None
            mapping["reviewed_at"] = None
            if mapping.get("original_target_path"):
                mapping["target_path"] = mapping["original_target_path"]
                mapping["target_entity"] = mapping["target_path"].split(".")[0]
                mapping["original_target_path"] = None
        else:
            if action == "edit":
                if not target_path:
                    raise ReviewError("an edit must supply a target path")
                if not cm.is_governed(target_path):
                    raise ReviewError(
                        f"{target_path!r} is not an attribute of the governed canonical "
                        "model. Choose an existing target or use a CustomExtension "
                        "property."
                    )
                if mapping.get("original_target_path") is None:
                    mapping["original_target_path"] = before.get("target_path") or ""
                mapping["target_path"] = target_path
                mapping["target_entity"] = target_path.split(".")[0]
                attribute = cm.resolve(target_path)
                mapping["target_required"] = bool(attribute and attribute.required)
                mapping["target_terminology"] = attribute.terminology if attribute else None
                if transformation:
                    mapping["transformation"] = transformation

            if action == "approve" and not mapping.get("target_path"):
                raise ReviewError(
                    "This mapping has no target. Assign a canonical target before "
                    "approving, or mark it intentionally unused by rejecting it."
                )

            mapping["status"] = ACTION_STATUS[action]
            mapping["reviewer"] = actor
            mapping["reviewer_comment"] = comment
            mapping["reviewed_at"] = _now()

            if action == "edit" and target_path and target_path.startswith("CustomExtension."):
                repo.register_custom_target(
                    session_id,
                    target_path,
                    actor=actor,
                    description=comment or "",
                    source_path=mapping.get("source_path") or "",
                )

        mappings[index] = mapping
        repo.save_mappings(session_id, mappings)

        repo.append_audit(session_id, {
            "event_id": uuid.uuid4().hex,
            "timestamp": _now(),
            "actor": actor,
            "action": f"mapping.{action}",
            "mapping_id": mapping_id,
            "source_path": before.get("source_path"),
            "before": {
                "target_path": before.get("target_path"),
                "status": before.get("status"),
                "transformation": before.get("transformation"),
            },
            "after": {
                "target_path": mapping.get("target_path"),
                "status": mapping.get("status"),
                "transformation": mapping.get("transformation"),
            },
            "comment": comment,
        })

        return mapping


def sync_standard_auto_approval(session_id: str) -> int:
    """Approve any standard mappings still proposed (hidden from Z-segment review)."""
    with app_db_session() as db:
        repo = Hl7Repository(db)
        mappings = repo.load_mappings(session_id)
        approved = 0
        for mapping in mappings:
            if mapping.get("category") != "standard":
                continue
            if mapping.get("status") != PROPOSED:
                continue
            if not mapping.get("target_path"):
                continue
            mapping["status"] = APPROVED
            mapping["reviewer"] = "RULE-001"
            mapping["reviewer_comment"] = (
                "Auto-approved: standard HL7 field (review is limited to Z-segments)."
            )
            mapping["reviewed_at"] = _now()
            approved += 1

        if approved:
            repo.save_mappings(session_id, mappings)
            repo.append_audit(session_id, {
                "event_id": uuid.uuid4().hex,
                "timestamp": _now(),
                "actor": "RULE-001",
                "action": "mapping.sync_standard_auto_approve",
                "count": approved,
            })
        return approved


def bulk_approve_auto(session_id: str, *, actor: str) -> int:
    sync_standard_auto_approval(session_id)
    with app_db_session() as db:
        repo = Hl7Repository(db)
        mappings = repo.load_mappings(session_id)
        approved = 0
        for mapping in mappings:
            if mapping.get("category") != "standard":
                continue
            if mapping.get("status") != PROPOSED:
                continue
            if not mapping.get("target_path"):
                continue
            mapping["status"] = APPROVED
            mapping["reviewer"] = actor
            mapping["reviewer_comment"] = (
                "Bulk-approved: standard HL7 field (review is limited to Z-segments)."
            )
            mapping["reviewed_at"] = _now()
            approved += 1

        if approved:
            repo.save_mappings(session_id, mappings)
            repo.append_audit(session_id, {
                "event_id": uuid.uuid4().hex,
                "timestamp": _now(),
                "actor": actor,
                "action": "mapping.bulk_approve",
                "count": approved,
                "basis": "RULE-001 standard auto-approve",
            })
        return approved


def bulk_approve_mappings(
    session_id: str,
    mapping_ids: list[str],
    *,
    actor: str,
    comment: str | None = None,
) -> dict:
    """Approve a selected set of mappings that have a target and are not terminal."""
    if not mapping_ids:
        return {"approved": 0, "skipped": 0}

    id_set = set(mapping_ids)
    note = (comment or "").strip() or "Bulk-approved from mapping review workbench."

    with app_db_session() as db:
        repo = Hl7Repository(db)
        mappings = repo.load_mappings(session_id)
        approved = 0
        skipped = 0

        for mapping in mappings:
            if mapping["id"] not in id_set:
                continue
            if mapping.get("status") in {APPROVED, REJECTED, DEFERRED}:
                skipped += 1
                continue
            if not mapping.get("target_path"):
                skipped += 1
                continue
            mapping["status"] = APPROVED
            mapping["reviewer"] = actor
            mapping["reviewer_comment"] = note
            mapping["reviewed_at"] = _now()
            approved += 1

        if approved:
            repo.save_mappings(session_id, mappings)
            repo.append_audit(session_id, {
                "event_id": uuid.uuid4().hex,
                "timestamp": _now(),
                "actor": actor,
                "action": "mapping.bulk_approve_selected",
                "count": approved,
                "skipped": skipped,
                "mapping_ids": sorted(id_set),
            })

    return {"approved": approved, "skipped": skipped}


# ---------------------------------------------------------------------------
# Publication gate (FR-021, RULE-007)
# ---------------------------------------------------------------------------

def readiness(session_id: str, entities: set[str]) -> dict:
    sync_standard_auto_approval(session_id)
    mappings = load_mappings(session_id)
    approved = [m for m in mappings if m["status"] == APPROVED]
    approved_targets = {m["target_path"] for m in approved if m.get("target_path")}
    mapped_targets = {m["target_path"] for m in mappings if m.get("target_path")}

    blockers: list[dict] = []
    for entity_name, attribute in cm.required_attributes(entities):
        target = f"{entity_name}.{attribute.name}"
        # Only gate required attributes the corpus actually maps to.
        if target not in mapped_targets:
            continue
        if target not in approved_targets:
            blockers.append({
                "target_path": target,
                "reason": (
                    f"{attribute.cardinality} is required on {entity_name} but no "
                    "approved mapping populates it."
                ),
                "description": attribute.description,
            })

    warnings: list[dict] = []
    for mapping in mappings:
        if mapping["status"] == PROPOSED:
            warnings.append({
                "mapping_id": mapping["id"],
                "source_path": mapping["source_path"],
                "reason": "Proposed but not yet reviewed; it will not be included.",
            })
        elif mapping["status"] == UNRESOLVED:
            warnings.append({
                "mapping_id": mapping["id"],
                "source_path": mapping["source_path"],
                "reason": "Unresolved; the source value will not reach the output.",
            })
        elif mapping["status"] == DEFERRED:
            warnings.append({
                "mapping_id": mapping["id"],
                "source_path": mapping["source_path"],
                "reason": "Deferred to a later version.",
            })

    return {
        "can_publish": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "approved_count": len(approved),
        "total_count": len(mappings),
    }


def _validation_rules(approved: list[dict]) -> list[dict]:
    rules: list[dict] = []
    for mapping in approved:
        target = mapping.get("target_path") or ""
        attribute = cm.resolve(target)
        if attribute is None:
            continue
        if attribute.required:
            rules.append({
                "rule": "required",
                "target_path": target,
                "source_path": mapping["source_path"],
                "detail": f"{target} must be present and non-empty.",
            })
        if attribute.terminology:
            rules.append({
                "rule": "terminology",
                "target_path": target,
                "source_path": mapping["source_path"],
                "detail": f"{target} must validate against {attribute.terminology}.",
            })
        if attribute.datatype in {"integer", "decimal", "dateTime", "date", "boolean"}:
            rules.append({
                "rule": "datatype",
                "target_path": target,
                "source_path": mapping["source_path"],
                "detail": f"{target} must parse as {attribute.datatype}.",
            })
    return rules


def next_version(session_id: str) -> int:
    with app_db_session() as db:
        return Hl7Repository(db).next_version(session_id)


def publish(
    session_id: str,
    *,
    actor: str,
    entities: set[str],
    source_signature: dict,
    note: str | None = None,
) -> dict:
    state = readiness(session_id, entities)
    if not state["can_publish"]:
        raise ReviewError(
            "Publication is blocked: "
            + "; ".join(b["target_path"] for b in state["blockers"])
        )

    mappings = load_mappings(session_id)
    approved = [m for m in mappings if m["status"] == APPROVED]
    if not approved:
        raise ReviewError("nothing to publish — no mappings have been approved")

    version = next_version(session_id)
    populated = entities | {
        m["target_path"].split(".")[0] for m in approved if m.get("target_path")
    }
    package = {
        "version": version,
        "published_at": _now(),
        "published_by": actor,
        "note": note,
        "source_signature": source_signature,
        "canonical_entities": sorted(populated),
        "mapping_count": len(approved),
        "mappings": [
            {
                "source_path": m["source_path"],
                "target_path": m["target_path"],
                "transformation": m["transformation"],
                "category": m["category"],
                "confidence": m["confidence"],
                "reviewer": m.get("reviewer"),
                "reviewer_comment": m.get("reviewer_comment"),
                "reviewed_at": m.get("reviewed_at"),
                "overridden_from": m.get("original_target_path"),
            }
            for m in approved
        ],
        "validations": _validation_rules(approved),
        "warnings_at_publish": state["warnings"],
        "custom_targets": sync_custom_targets_from_mappings(session_id),
    }

    with app_db_session() as db:
        repo = Hl7Repository(db)
        try:
            repo.save_package(session_id, package)
        except ValueError as exc:
            raise ReviewError(str(exc)) from exc
        repo.append_audit(session_id, {
            "event_id": uuid.uuid4().hex,
            "timestamp": _now(),
            "actor": actor,
            "action": "package.publish",
            "version": version,
            "mapping_count": len(approved),
            "note": note,
        })

    return package


def list_versions(session_id: str) -> list[dict]:
    with app_db_session() as db:
        return Hl7Repository(db).list_versions(session_id)


def get_version(session_id: str, version: int) -> dict | None:
    with app_db_session() as db:
        return Hl7Repository(db).get_package(session_id, version)
