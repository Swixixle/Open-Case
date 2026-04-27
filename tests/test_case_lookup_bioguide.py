"""GET /api/v1/cases/lookup-by-bioguide/{id} resolves investigate CaseFile rows for /official/{bioguide} UX."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import sessionmaker

from models import CaseFile, SubjectProfile


def _seed_case(test_engine, *, bioguide: str, slug: str) -> uuid.UUID:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        cid = uuid.uuid4()
        case = CaseFile(
            id=cid,
            slug=slug,
            title="Lookup test",
            subject_name="Test Subject",
            subject_type="senator",
            jurisdiction="US",
            status="open",
            created_by="test_lookup",
            summary="",
            government_level="federal",
            branch="legislative",
        )
        db.add(case)
        db.flush()
        db.add(
            SubjectProfile(
                case_file_id=cid,
                subject_name="Test Subject",
                subject_type="senator",
                government_level="federal",
                branch="legislative",
                bioguide_id=bioguide,
                updated_by="test_lookup",
            )
        )
        db.commit()
        return cid
    finally:
        db.close()


def test_lookup_by_bioguide_returns_case(client, test_engine):
    bg = "Z000099"
    cid = _seed_case(test_engine, bioguide=bg, slug=f"lookup-test-{bg.lower()}")
    r = client.get(f"/api/v1/cases/lookup-by-bioguide/{bg}")
    assert r.status_code == 200
    data = r.json()
    assert data["case_id"] == str(cid)
    assert data["slug"] == f"lookup-test-{bg.lower()}"


def test_lookup_unknown_bioguide_404(client):
    r = client.get("/api/v1/cases/lookup-by-bioguide/ZZZZZZ")
    assert r.status_code == 404
