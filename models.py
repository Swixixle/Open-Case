from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
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
    last_source_statuses: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    government_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pilot_cohort: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_epistemic_level: Mapped[str] = mapped_column(String(32), default="REPORTED")

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
    jurisdictional_match: Mapped[bool] = mapped_column(Boolean, default=False)
    matched_committees: Mapped[str] = mapped_column(Text, default="[]")
    donor_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    epistemic_level: Mapped[str] = mapped_column(String(32), default="REPORTED")
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("subject_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(64), default="other")
    source_title: Mapped[str] = mapped_column(String(1024), default="")
    source_publisher: Mapped[str] = mapped_column(String(512), default="")
    source_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_discovered: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_text: Mapped[str] = mapped_column(Text, default="")
    claim_summary: Mapped[str] = mapped_column(Text, default="")
    claim_status: Mapped[str] = mapped_column(String(32), default="active")
    review_status: Mapped[str] = mapped_column(String(32), default="pending")
    review_notes: Mapped[str] = mapped_column(Text, default="")
    is_publicly_renderable: Mapped[bool] = mapped_column(Boolean, default=False)
    display_label: Mapped[str] = mapped_column(String(256), default="")
    source_excerpt: Mapped[str] = mapped_column(Text, default="")
    source_hash: Mapped[str] = mapped_column(String(128), default="")
    linked_entities_json: Mapped[str] = mapped_column(Text, default="[]")
    jurisdiction: Mapped[str] = mapped_column(String(512), default="")
    case_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    court: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ingest_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_id: Mapped[str] = mapped_column(String(512), default="")
    classification_basis: Mapped[str] = mapped_column(String(64), default="")
    corroboration_count: Mapped[int] = mapped_column(Integer, default=0)
    contradiction_count: Mapped[int] = mapped_column(Integer, default=0)

    case_file: Mapped["CaseFile"] = relationship("CaseFile", back_populates="evidence_entries")
    disputes: Mapped[list["DisputeRecord"]] = relationship(
        "DisputeRecord",
        back_populates="finding",
    )
    audit_events: Mapped[list["FindingAuditLog"]] = relationship(
        "FindingAuditLog",
        back_populates="finding",
    )


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
    # Phase 6 — API key auth (SHA-256 of plaintext; plaintext never stored)
    hashed_api_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    api_key_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
    direction_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    temporal_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    confirmation_checks: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    cross_case_appearances: Mapped[int] = mapped_column(Integer, default=0)
    cross_case_officials: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_top_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    first_appearance: Mapped[bool] = mapped_column(Boolean, default=False)
    epistemic_level: Mapped[str] = mapped_column(String(32), default="REPORTED")
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)


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
    government_level: Mapped[str] = mapped_column(String(32), default="federal")
    branch: Mapped[str] = mapped_column(String(32), default="legislative")
    historical_depth: Mapped[str] = mapped_column(String(32), default="career")


class DisputeRecord(Base):
    """Formal dispute / correction / takedown request tied to a finding (evidence entry)."""

    __tablename__ = "dispute_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_entries.id", ondelete="CASCADE"),
        index=True,
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("subject_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    submitted_by: Mapped[str] = mapped_column(String(64))
    submission_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    dispute_type: Mapped[str] = mapped_column(String(64))
    dispute_text: Mapped[str] = mapped_column(Text)
    supporting_source_url: Mapped[str] = mapped_column(Text, default="")
    supporting_document_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution_status: Mapped[str] = mapped_column(String(32), default="pending")
    resolution_notes: Mapped[str] = mapped_column(Text, default="")
    resolution_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

    finding: Mapped["EvidenceEntry"] = relationship("EvidenceEntry", back_populates="disputes")


class FindingAuditLog(Base):
    """Audit trail for classification, render decisions, and disputes."""

    __tablename__ = "finding_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_entries.id", ondelete="CASCADE"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64))
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    finding: Mapped["EvidenceEntry"] = relationship("EvidenceEntry", back_populates="audit_events")


class PoliticalEvent(Base):
    """Dated political / fundraising calendar entries (FEC, elections, primaries)."""

    __tablename__ = "political_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_name: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(50))
    event_date: Mapped[date] = mapped_column(Date, index=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    buffer_days_pre: Mapped[int] = mapped_column(Integer, default=7)
    buffer_days_post: Mapped[int] = mapped_column(Integer, default=3)
    discount_factor: Mapped[float] = mapped_column(Float, default=0.4)
    congress: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SenatorCommittee(Base):
    """Senate.gov committee assignment rows (cached per senator)."""

    __tablename__ = "senator_committees"
    __table_args__ = (
        UniqueConstraint(
            "bioguide_id",
            "committee_code",
            name="uq_senator_committee_code",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bioguide_id: Mapped[str] = mapped_column(String(16), index=True)
    committee_name: Mapped[str] = mapped_column(String(512))
    committee_code: Mapped[str] = mapped_column(String(32))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DonorFingerprint(Base):
    __tablename__ = "donor_fingerprints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    normalized_donor_key: Mapped[str] = mapped_column(String(512), index=True)
    canonical_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    resolution_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    case_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_files.id", ondelete="CASCADE"),
        index=True,
    )
    signal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("signals.id", ondelete="CASCADE"),
    )
    weight: Mapped[float] = mapped_column(Float)
    official_name: Mapped[str] = mapped_column(String(512))
    bioguide_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InvestigationRun(Base):
    __tablename__ = "investigation_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_files.id", ondelete="CASCADE"),
        index=True,
    )
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    signals_detected: Mapped[int] = mapped_column(Integer, default=0)
    top_donors: Mapped[str] = mapped_column(Text, default="[]")


class EnrichmentReceipt(Base):
    """Signed Perplexity sonar enrichment run attached to a case (receipts, not verdicts)."""

    __tablename__ = "enrichment_receipts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_files.id", ondelete="CASCADE"),
        index=True,
    )
    subject_name: Mapped[str] = mapped_column(String(512))
    bioguide_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    queried_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    findings: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    new_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    is_delta: Mapped[bool] = mapped_column(Boolean, default=False)
    signed_receipt: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class PatternAlertRecord(Base):
    """Persisted snapshot of pattern engine output (global; refreshed when cases are sealed)."""

    __tablename__ = "pattern_alert_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    pattern_version: Mapped[str] = mapped_column(String(32))
    donor_entity: Mapped[str] = mapped_column(String(512))
    matched_officials: Mapped[str] = mapped_column(Text)
    matched_case_ids: Mapped[str] = mapped_column(Text)
    committee: Mapped[str | None] = mapped_column(String(512), nullable=True)
    window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_refs: Mapped[str] = mapped_column(Text)
    disclaimer: Mapped[str] = mapped_column(Text)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    diagnostics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    epistemic_level: Mapped[str] = mapped_column(String(32), default="REPORTED")
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)


class SenatorDossier(Base):
    __tablename__ = "senator_dossiers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bioguide_id: Mapped[str] = mapped_column(String(16), index=True)
    senator_name: Mapped[str] = mapped_column(String(256))
    dossier_json: Mapped[str] = mapped_column(Text)
    signature: Mapped[str] = mapped_column(Text)
    share_token: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="building")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
