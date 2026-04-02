from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import generate_raw_key, hash_key
from models import Base, CaseContributor, CaseFile, Investigator, Signal


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
