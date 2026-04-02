from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CaseFile(Base):
    __tablename__ = "case_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(1024))
    subject_name: Mapped[str] = mapped_column(String(512))
    subject_type: Mapped[str] = mapped_column(String(64))
    jurisdiction: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_by: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text)
    pickup_note: Mapped[str] = mapped_column(Text, default="")
    signed_hash: Mapped[str] = mapped_column(Text, default="")
    last_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)

    evidence_entries: Mapped[list["EvidenceEntry"]] = relationship(
        "EvidenceEntry",
        back_populates="case_file",
        order_by="EvidenceEntry.entered_at",
    )
    snapshots: Mapped[list["CaseSnapshot"]] = relationship(
        "CaseSnapshot",
        back_populates="case_file",
        order_by="CaseSnapshot.snapshot_number",
    )


class EvidenceEntry(Base):
    __tablename__ = "evidence_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_files.id", ondelete="CASCADE"),
        index=True,
    )
    entry_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(1024))
    body: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, default="")
    source_name: Mapped[str] = mapped_column(String(512), default="")
    date_of_event: Mapped[date | None] = mapped_column(Date, nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    entered_by: Mapped[str] = mapped_column(String(256))
    signed_hash: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(32))
    is_absence: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged_for_review: Mapped[bool] = mapped_column(Boolean, default=False)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_data_json: Mapped[str] = mapped_column(Text, default="")
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    disambiguation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    disambiguation_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    disambiguation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    adapter_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    case_file: Mapped["CaseFile"] = relationship("CaseFile", back_populates="evidence_entries")


class Investigator(Base):
    __tablename__ = "investigators"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handle: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    public_key: Mapped[str] = mapped_column(Text, default="")
    credibility_score: Mapped[int] = mapped_column(Integer, default=0)
    cases_opened: Mapped[int] = mapped_column(Integer, default=0)
    entries_contributed: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    is_anchor: Mapped[bool] = mapped_column(Boolean, default=False)


class CaseContributor(Base):
    __tablename__ = "case_contributors"
    __table_args__ = (UniqueConstraint("case_file_id", "investigator_handle", name="uq_case_investigator"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_files.id", ondelete="CASCADE"),
        index=True,
    )
    investigator_handle: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(64))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    entry_count: Mapped[int] = mapped_column(Integer, default=0)


class SourceCheckLog(Base):
    __tablename__ = "source_check_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_files.id", ondelete="CASCADE"),
        index=True,
    )
    source_name: Mapped[str] = mapped_column(String(512))
    query_string: Mapped[str] = mapped_column(Text)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    checked_by: Mapped[str] = mapped_column(String(256))
    result_hash: Mapped[str] = mapped_column(String(128), default="")


class CaseSnapshot(Base):
    __tablename__ = "case_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_files.id", ondelete="CASCADE"),
        index=True,
    )
    snapshot_number: Mapped[int] = mapped_column(Integer)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    taken_by: Mapped[str] = mapped_column(String(256))
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    signed_hash: Mapped[str] = mapped_column(Text)
    share_url: Mapped[str] = mapped_column(Text, default="")
    label: Mapped[str] = mapped_column(Text, default="")

    case_file: Mapped["CaseFile"] = relationship("CaseFile", back_populates="snapshots")


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint(
            "case_file_id",
            "signal_identity_hash",
            name="uq_signal_identity_per_case",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_files.id", ondelete="CASCADE"),
        index=True,
    )
    signal_identity_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    signal_type: Mapped[str] = mapped_column(String(64))
    weight: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[str] = mapped_column(Text, default="[]")
    actor_a: Mapped[str | None] = mapped_column(String(512), nullable=True)
    actor_b: Mapped[str | None] = mapped_column(String(512), nullable=True)
    event_date_a: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_date_b: Mapped[str | None] = mapped_column(String(32), nullable=True)
    days_between: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dismissed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dismissed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    exposure_state: Mapped[str] = mapped_column(String(32), default="internal")
    routing_log: Mapped[str] = mapped_column(Text, default="[]")
    repeat_count: Mapped[int] = mapped_column(Integer, default=1)
    proximity_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_warning: Mapped[str | None] = mapped_column(Text, nullable=True)


class SignalAuditLog(Base):
    __tablename__ = "signal_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("signals.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64))
    performed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    old_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AdapterCache(Base):
    __tablename__ = "adapter_cache"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    adapter_name: Mapped[str] = mapped_column(String(128), index=True)
    query_hash: Mapped[str] = mapped_column(String(64), index=True)
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ttl_hours: Mapped[int] = mapped_column(Integer, default=4)
    query_string: Mapped[str] = mapped_column(Text)


class SubjectProfile(Base):
    __tablename__ = "subject_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_files.id", ondelete="CASCADE"),
        index=True,
    )
    subject_name: Mapped[str] = mapped_column(String(512))
    subject_type: Mapped[str] = mapped_column(String(64))
    bioguide_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[str | None] = mapped_column(String(8), nullable=True)
    district: Mapped[str | None] = mapped_column(String(16), nullable=True)
    office: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
