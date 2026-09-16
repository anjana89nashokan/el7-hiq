"""HL7 session persistence in app.db (same store as profiling/mapping runs)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import Hl7AuditEvent, Hl7CustomTarget, Hl7PublishedPackage, Hl7Session

LEGACY_SESSIONS_DIR = Path(__file__).resolve().parents[1] / "data" / "hl7_sessions"


def summarize_hl7_row(row: Hl7Session, *, versions: list[dict] | None = None) -> dict:
    """Compact HL7 progress summary for app session list / dashboard."""
    mappings = list(row.mappings_json or [])
    approved = sum(1 for m in mappings if m.get("status") == "approved")
    pending = sum(
        1 for m in mappings if m.get("status") in ("proposed", "unresolved")
    )
    result = row.result_json or {}
    summary = result.get("summary", {})
    version_rows = versions if versions is not None else []
    latest_version = version_rows[-1]["version"] if version_rows else None
    if latest_version:
        status = f"published v{latest_version}"
        mapping_complete = True
    elif mappings and pending == 0 and approved == len(mappings):
        status = "mapping complete"
        mapping_complete = True
    elif mappings and approved > 0:
        status = "in review"
        mapping_complete = False
    elif mappings:
        status = "profiled"
        mapping_complete = False
    else:
        status = "profiled"
        mapping_complete = False

    return {
        "hl7_session_id": result.get("hl7_session_id", row.id),
        "status": status,
        "messages_parsed": summary.get("messages_parsed", 0),
        "mappings_total": len(mappings),
        "mappings_approved": approved,
        "mappings_pending": pending,
        "latest_version": latest_version,
        "profiling_complete": True,
        "mapping_complete": mapping_complete,
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Hl7Repository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, session_id: str) -> Hl7Session | None:
        return self.db.get(Hl7Session, session_id)

    def require(self, session_id: str) -> Hl7Session:
        row = self.get(session_id)
        if row is not None:
            return row
        imported = self.import_legacy_session(session_id)
        if imported is not None:
            return imported
        raise LookupError(f"HL7 session {session_id!r} not found")

    def create(
        self,
        session_id: str,
        result: dict,
        mappings: list[dict],
        fhir_documents: dict[str, dict] | None = None,
        *,
        app_session_id: str | None = None,
        user_key: str | None = None,
    ) -> Hl7Session:
        now = _utcnow()
        row = Hl7Session(
            id=session_id,
            app_session_id=app_session_id,
            user_key=user_key,
            result_json=result,
            mappings_json=mappings,
            fhir_json=fhir_documents or {},
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def save_result(self, session_id: str, result: dict) -> None:
        row = self.require(session_id)
        row.result_json = result
        row.updated_at = _utcnow()

    def load_result(self, session_id: str) -> dict:
        return dict(self.require(session_id).result_json)

    def save_mappings(self, session_id: str, mappings: list[dict]) -> None:
        row = self.require(session_id)
        row.mappings_json = mappings
        row.updated_at = _utcnow()

    def load_mappings(self, session_id: str) -> list[dict]:
        row = self.require(session_id)
        return list(row.mappings_json or [])

    def save_fhir(self, session_id: str, name: str, document: dict) -> None:
        row = self.require(session_id)
        docs = dict(row.fhir_json or {})
        docs[name] = document
        row.fhir_json = docs
        row.updated_at = _utcnow()

    def load_fhir(self, session_id: str, name: str) -> dict | None:
        row = self.require(session_id)
        docs = row.fhir_json or {}
        return docs.get(name)

    def list_fhir_names(self, session_id: str) -> list[str]:
        row = self.require(session_id)
        return sorted((row.fhir_json or {}).keys())

    def list_sessions(self) -> list[Hl7Session]:
        self._import_all_legacy_sessions()
        stmt = select(Hl7Session).order_by(Hl7Session.updated_at.desc())
        return list(self.db.scalars(stmt))

    def delete_session(self, session_id: str) -> bool:
        row = self.get(session_id)
        if row is None:
            legacy = LEGACY_SESSIONS_DIR / session_id
            if legacy.exists():
                import shutil
                shutil.rmtree(legacy)
                return True
            return False
        self.db.execute(delete(Hl7AuditEvent).where(Hl7AuditEvent.session_id == session_id))
        self.db.execute(delete(Hl7CustomTarget).where(Hl7CustomTarget.session_id == session_id))
        self.db.execute(delete(Hl7PublishedPackage).where(Hl7PublishedPackage.session_id == session_id))
        self.db.delete(row)
        legacy = LEGACY_SESSIONS_DIR / session_id
        if legacy.exists():
            import shutil
            shutil.rmtree(legacy)
        return True

    def load_custom_targets(self, session_id: str) -> list[dict]:
        self.require(session_id)
        stmt = (
            select(Hl7CustomTarget)
            .where(Hl7CustomTarget.session_id == session_id)
            .order_by(Hl7CustomTarget.target_path)
        )
        rows = self.db.scalars(stmt).all()
        return [
            {
                "target_path": r.target_path,
                "slug": r.slug,
                "description": r.description,
                "created_from_source": r.created_from_source,
                "created_by": r.created_by,
                "created_at": r.created_at.replace(tzinfo=timezone.utc).isoformat()
                if r.created_at
                else "",
            }
            for r in rows
        ]

    def register_custom_target(
        self,
        session_id: str,
        target_path: str,
        *,
        actor: str,
        description: str = "",
        source_path: str = "",
    ) -> dict:
        prefix = "CustomExtension."
        if not target_path.startswith(prefix):
            return {}
        slug = target_path[len(prefix):].strip()
        if not slug:
            return {}

        self.require(session_id)
        stmt = select(Hl7CustomTarget).where(
            Hl7CustomTarget.session_id == session_id,
            Hl7CustomTarget.target_path == target_path,
        )
        existing = self.db.scalar(stmt)
        if existing:
            if description.strip() and not (existing.description or "").strip():
                existing.description = description.strip()
            if source_path and not existing.created_from_source:
                existing.created_from_source = source_path
            self.db.flush()
            return self._target_dict(existing)

        now = _utcnow()
        row = Hl7CustomTarget(
            id=_new_id("htgt"),
            session_id=session_id,
            target_path=target_path,
            slug=slug,
            description=description.strip(),
            created_from_source=source_path,
            created_by=actor,
            created_at=now,
        )
        self.db.add(row)
        self.db.flush()
        return self._target_dict(row)

    def sync_custom_targets_from_mappings(self, session_id: str) -> list[dict]:
        mappings = self.load_mappings(session_id)
        known = {t["target_path"] for t in self.load_custom_targets(session_id)}
        for mapping in mappings:
            target_path = mapping.get("target_path") or ""
            if not target_path.startswith("CustomExtension.") or target_path in known:
                continue
            self.register_custom_target(
                session_id,
                target_path,
                actor=mapping.get("reviewer") or "system",
                description=(mapping.get("reviewer_comment") or "").strip(),
                source_path=mapping.get("source_path") or "",
            )
            known.add(target_path)
        return self.load_custom_targets(session_id)

    def append_audit(self, session_id: str, event: dict) -> None:
        self.require(session_id)
        ts_raw = event.get("timestamp")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            ts = _utcnow()
        row = Hl7AuditEvent(
            id=event.get("event_id") or _new_id("haud"),
            session_id=session_id,
            event_json=event,
            timestamp=ts,
        )
        self.db.add(row)

    def load_audit(self, session_id: str) -> list[dict]:
        self.require(session_id)
        stmt = (
            select(Hl7AuditEvent)
            .where(Hl7AuditEvent.session_id == session_id)
            .order_by(Hl7AuditEvent.timestamp)
        )
        return [dict(r.event_json) for r in self.db.scalars(stmt)]

    def next_version(self, session_id: str) -> int:
        stmt = (
            select(Hl7PublishedPackage.version)
            .where(Hl7PublishedPackage.session_id == session_id)
            .order_by(Hl7PublishedPackage.version.desc())
        )
        current = self.db.scalar(stmt)
        return (current or 0) + 1

    def save_package(self, session_id: str, package: dict) -> None:
        version = int(package["version"])
        stmt = select(Hl7PublishedPackage).where(
            Hl7PublishedPackage.session_id == session_id,
            Hl7PublishedPackage.version == version,
        )
        if self.db.scalar(stmt) is not None:
            raise ValueError(f"version {version} already exists")
        ts_raw = package.get("published_at")
        try:
            published_at = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except (TypeError, ValueError):
            published_at = _utcnow()
        row = Hl7PublishedPackage(
            session_id=session_id,
            version=version,
            package_json=package,
            published_at=published_at,
            published_by=str(package.get("published_by") or ""),
        )
        self.db.add(row)

    def list_versions(self, session_id: str) -> list[dict]:
        self.require(session_id)
        stmt = (
            select(Hl7PublishedPackage)
            .where(Hl7PublishedPackage.session_id == session_id)
            .order_by(Hl7PublishedPackage.version)
        )
        out = []
        for row in self.db.scalars(stmt):
            pkg = row.package_json
            out.append({
                "version": pkg.get("version", row.version),
                "published_at": pkg.get("published_at"),
                "published_by": pkg.get("published_by"),
                "mapping_count": pkg.get("mapping_count"),
                "note": pkg.get("note"),
            })
        return out

    def get_package(self, session_id: str, version: int) -> dict | None:
        row = self.db.get(Hl7PublishedPackage, (session_id, version))
        return dict(row.package_json) if row else None

    def import_legacy_session(self, session_id: str) -> Hl7Session | None:
        if self.get(session_id) is not None:
            return self.get(session_id)
        session_dir = LEGACY_SESSIONS_DIR / session_id
        result_file = session_dir / "result.json"
        if not result_file.exists():
            return None

        result = json.loads(result_file.read_text(encoding="utf-8"))
        mappings_file = session_dir / "mappings.json"
        mappings = (
            json.loads(mappings_file.read_text(encoding="utf-8"))
            if mappings_file.exists()
            else []
        )
        fhir_docs: dict[str, dict] = {}
        fhir_dir = session_dir / "fhir"
        if fhir_dir.exists():
            for path in fhir_dir.glob("*.json"):
                try:
                    fhir_docs[path.stem] = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue

        created_raw = result.get("created_at")
        try:
            created_at = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except (TypeError, ValueError):
            created_at = _utcnow()

        row = Hl7Session(
            id=session_id,
            result_json=result,
            mappings_json=mappings,
            fhir_json=fhir_docs,
            created_at=created_at,
            updated_at=created_at,
        )
        self.db.add(row)
        self.db.flush()

        targets_file = session_dir / "custom_targets.json"
        if targets_file.exists():
            try:
                for target in json.loads(targets_file.read_text(encoding="utf-8")):
                    self.register_custom_target(
                        session_id,
                        target["target_path"],
                        actor=target.get("created_by") or "import",
                        description=target.get("description") or "",
                        source_path=target.get("created_from_source") or "",
                    )
            except json.JSONDecodeError:
                pass

        audit_file = session_dir / "audit.json"
        if audit_file.exists():
            try:
                for event in json.loads(audit_file.read_text(encoding="utf-8")):
                    self.append_audit(session_id, event)
            except json.JSONDecodeError:
                pass

        versions_dir = session_dir / "versions"
        if versions_dir.exists():
            for path in sorted(versions_dir.glob("v*.json")):
                try:
                    package = json.loads(path.read_text(encoding="utf-8"))
                    self.save_package(session_id, package)
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue

        self.sync_custom_targets_from_mappings(session_id)
        return row

    def _import_all_legacy_sessions(self) -> None:
        if not LEGACY_SESSIONS_DIR.exists():
            return
        for session_dir in LEGACY_SESSIONS_DIR.iterdir():
            if not session_dir.is_dir():
                continue
            if (session_dir / "result.json").exists():
                self.import_legacy_session(session_dir.name)

    @staticmethod
    def _target_dict(row: Hl7CustomTarget) -> dict:
        return {
            "target_path": row.target_path,
            "slug": row.slug,
            "description": row.description,
            "created_from_source": row.created_from_source,
            "created_by": row.created_by,
            "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat()
            if row.created_at
            else "",
        }
