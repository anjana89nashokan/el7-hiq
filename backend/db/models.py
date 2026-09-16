from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Declarative base for app-session persistence models."""


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class AppSession(Base):
    __tablename__ = "app_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    current_profiling_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_mapping_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_extract_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_hl7_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_vertex_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active_vertex_app_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    active_vertex_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    profiling_runs: Mapped[list["ProfilingRun"]] = relationship(back_populates="session")
    mapping_runs: Mapped[list["MappingRun"]] = relationship(back_populates="session")
    extract_runs: Mapped[list["ExtractRun"]] = relationship(back_populates="session")


class ProfilingRun(Base):
    __tablename__ = "profiling_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("app_sessions.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="IDLE")
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    profiling_context_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_vertex_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active_vertex_app_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_by_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped["AppSession"] = relationship(back_populates="profiling_runs")


class MappingRun(Base):
    __tablename__ = "mapping_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("app_sessions.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="IDLE")
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    mapping_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    step1_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    step2_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    step3_review_package_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    step3_capture_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    step4_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_by_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped["AppSession"] = relationship(back_populates="mapping_runs")
    review_draft: Mapped["MappingReviewDraft | None"] = relationship(back_populates="mapping_run", uselist=False)


class ExtractRun(Base):
    __tablename__ = "extract_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("app_sessions.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="IDLE")
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Persisted artifact URIs produced as the extract pipeline progresses.
    upload_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brd_gcs_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    layout_gcs_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_gcs_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    driver_gcs_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_vertex_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active_vertex_app_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_by_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped["AppSession"] = relationship(back_populates="extract_runs")


class MappingReviewDraft(Base):
    __tablename__ = "mapping_review_drafts"

    mapping_run_id: Mapped[str] = mapped_column(ForeignKey("mapping_runs.id"), primary_key=True)
    answers_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    feedbacks_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    changed_rows_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    active_tab: Mapped[str | None] = mapped_column(String(32), nullable=True)
    selected_row_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_saved_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    mapping_run: Mapped["MappingRun"] = relationship(back_populates="review_draft")


Index("ix_app_sessions_user_updated", AppSession.user_key, AppSession.updated_at)
Index("ix_profiling_runs_session_started", ProfilingRun.session_id, ProfilingRun.started_at)
Index("ix_mapping_runs_session_started", MappingRun.session_id, MappingRun.started_at)
Index("ix_extract_runs_session_started", ExtractRun.session_id, ExtractRun.started_at)


class Hl7Session(Base):
    __tablename__ = "hl7_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_sessions.id"), nullable=True, index=True
    )
    user_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    mappings_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    fhir_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    custom_targets: Mapped[list["Hl7CustomTarget"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list["Hl7AuditEvent"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    packages: Mapped[list["Hl7PublishedPackage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Hl7CustomTarget(Base):
    __tablename__ = "hl7_custom_targets"
    __table_args__ = (UniqueConstraint("session_id", "target_path", name="uq_hl7_custom_target"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("hl7_sessions.id"), nullable=False, index=True)
    target_path: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_from_source: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    session: Mapped["Hl7Session"] = relationship(back_populates="custom_targets")


class Hl7AuditEvent(Base):
    __tablename__ = "hl7_audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("hl7_sessions.id"), nullable=False, index=True)
    event_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    session: Mapped["Hl7Session"] = relationship(back_populates="audit_events")


class Hl7PublishedPackage(Base):
    __tablename__ = "hl7_published_packages"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("hl7_sessions.id"), primary_key=True, nullable=False
    )
    version: Mapped[int] = mapped_column(primary_key=True, nullable=False)
    package_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    published_by: Mapped[str] = mapped_column(String(320), nullable=False, default="")

    session: Mapped["Hl7Session"] = relationship(back_populates="packages")


Index("ix_hl7_sessions_updated", Hl7Session.updated_at)
