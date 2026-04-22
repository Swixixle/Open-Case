"""Case narrative endpoints (auth, wiring; LLM and pattern engine mocked)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from models import CaseNarrative


@patch("routes.narrative.run_phase2_narrative", return_value=("Test narrative body.", ["claude"]))
@patch("routes.narrative.run_pattern_engine", return_value=[])
def test_synthesize_persists_narrative(
    _pat_engine: object,
    _pat_llm: object,
    client,
    seeded_case_with_signals,
) -> None:
    cid = seeded_case_with_signals["case_id"]
    key = seeded_case_with_signals["api_key"]
    r = client.post(
        f"/api/v1/cases/{cid}/synthesize-narrative",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["narrative"] == "Test narrative body."
    assert "model_used" in data

    eng = seeded_case_with_signals["engine"]
    Session = sessionmaker(bind=eng)
    db = Session()
    try:
        row = db.scalar(
            select(CaseNarrative).where(CaseNarrative.case_file_id == uuid.UUID(cid))
        )
        assert row is not None
        assert "Test narrative" in row.narrative_text
    finally:
        db.close()

    g = client.get(f"/api/v1/cases/{cid}/narrative")
    assert g.status_code == 200
    assert g.json()["narrative"] == "Test narrative body."


def test_synthesize_requires_api_key(client, seeded_case_with_signals) -> None:
    cid = seeded_case_with_signals["case_id"]
    r = client.post(f"/api/v1/cases/{cid}/synthesize-narrative")
    assert r.status_code == 401


def test_get_narrative_404_when_missing(client) -> None:
    r = client.get(f"/api/v1/cases/{uuid.uuid4()}/narrative")
    assert r.status_code == 404
