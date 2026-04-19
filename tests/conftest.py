from __future__ import annotations

# Set test env before any imports that could load `main` (scheduler gate reads DISABLE_SCHEDULER at lifespan runtime).
import os

os.environ.setdefault("SKIP_EXTERNAL_PROPORTIONALITY", "1")
# APScheduler + Starlette TestClient: add_job/start can raise when the event loop is torn down.
os.environ.setdefault("DISABLE_SCHEDULER", "1")

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import generate_raw_key, hash_key
from adapters.dedup import make_evidence_hash
from models import Base, CaseContributor, CaseFile, EvidenceEntry, Investigator, Signal
from payloads import sign_evidence_entry


@pytest.fixture
def test_engine():
    # StaticPool: single connection so all sessions share one in-memory SQLite DB
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db_session(test_engine):
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(test_engine):
    import database
    from fastapi.testclient import TestClient

    import main

    old_engine = database.engine
    old_factory = database.SessionLocal
    database.engine = test_engine
    database.SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    try:
        with patch.object(main, "init_db", lambda: None):
            with TestClient(main.app) as c:
                yield c
    finally:
        database.engine = old_engine
        database.SessionLocal = old_factory


@pytest.fixture
def seeded_case_with_signals(test_engine):
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    handle = "testinv"
    inv = Investigator(
        handle=handle,
        hashed_api_key=hash_key(raw_key),
        public_key="",
    )
    db.add(inv)
    case = CaseFile(
        slug=f"slug-{uuid.uuid4().hex[:12]}",
        title="Test case",
        subject_name="Test Subject",
        subject_type="organization",
        jurisdiction="US",
        status="open",
        created_by=handle,
        summary="",
    )
    db.add(case)
    db.flush()
    db.add(
        CaseContributor(
            case_file_id=case.id,
            investigator_handle=handle,
            role="field",
        )
    )
    sig = Signal(
        case_file_id=case.id,
        signal_identity_hash="a" * 64,
        signal_type="temporal_proximity",
        weight=0.75,
        description="Prior signal",
        evidence_ids="[]",
        exposure_state="internal",
    )
    db.add(sig)
    db.commit()
    case_id = case.id
    db.close()
    return {
        "case_id": str(case_id),
        "signal_count": 1,
        "api_key": raw_key,
        "handle": handle,
        "engine": test_engine,
    }


@pytest.fixture
def seeded_public_official_case(test_engine):
    """Minimal public-official case so Congress adapter runs during investigate."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    handle = "testinv"
    inv = Investigator(
        handle=handle,
        hashed_api_key=hash_key(raw_key),
        public_key="",
    )
    db.add(inv)
    case = CaseFile(
        slug=f"slug-{uuid.uuid4().hex[:12]}",
        title="Test official case",
        subject_name="Test Subject",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by=handle,
        summary="",
    )
    db.add(case)
    db.flush()
    db.add(
        CaseContributor(
            case_file_id=case.id,
            investigator_handle=handle,
            role="field",
        )
    )
    db.commit()
    case_id = case.id
    db.close()
    return {
        "case_id": str(case_id),
        "api_key": raw_key,
        "handle": handle,
        "engine": test_engine,
    }


@pytest.fixture
def seeded_case_with_evidence(test_engine):
    """Public-official case with two committed evidence rows (Phase 8.4 durability)."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    handle = "testinv"
    inv = Investigator(
        handle=handle,
        hashed_api_key=hash_key(raw_key),
        public_key="",
    )
    db.add(inv)
    case = CaseFile(
        slug=f"slug-{uuid.uuid4().hex[:12]}",
        title="Test case with evidence",
        subject_name="Test Subject",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by=handle,
        summary="",
    )
    db.add(case)
    db.flush()
    db.add(
        CaseContributor(
            case_file_id=case.id,
            investigator_handle=handle,
            role="field",
        )
    )
    for i in range(2):
        eh = make_evidence_hash(
            case.id,
            "FEC",
            f"https://example.test/{i}",
            None,
            None,
            f"donor{i}",
        )
        ent = EvidenceEntry(
            case_file_id=case.id,
            entry_type="financial_connection",
            title=f"Prior receipt {i}",
            body="prior",
            source_name="FEC",
            source_url=f"https://example.test/{i}",
            entered_by=handle,
            confidence="confirmed",
            is_absence=False,
            evidence_hash=eh,
        )
        db.add(ent)
        db.flush()
        sign_evidence_entry(ent)
    db.commit()
    case_id = case.id
    db.close()
    db2 = Session()
    try:
        ct = (
            db2.scalar(
                select(func.count()).select_from(EvidenceEntry).where(
                    EvidenceEntry.case_file_id == case_id
                )
            )
            or 0
        )
    finally:
        db2.close()
    return {
        "case_id": str(case_id),
        "evidence_count": int(ct),
        "api_key": raw_key,
        "handle": handle,
        "engine": test_engine,
    }
