"""
Todd Young gate (Phase 4): structured four-category diagnostics (Step 1A).
Requires CONGRESS_API_KEY (and network). Uses TestClient + DB assertions.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "todd_young.json"


def _investigate_body() -> dict:
    if _FIXTURE.is_file():
        data = json.loads(_FIXTURE.read_text())
        return dict(data.get("investigate") or {})
    return {
        "subject_name": "Todd Young",
        "investigator_handle": "gate-runner",
        "bioguide_id": "Y000064",
        "proximity_days": 365,
        "fec_committee_id": "C00459255",
    }


def main() -> int:
    if not os.getenv("CONGRESS_API_KEY"):
        print("CONGRESS_API_KEY is required in the environment.", file=sys.stderr)
        return 1

    from database import SessionLocal
    from main import app
    from scripts.todd_young_assertions import run_assertions

    client = TestClient(app)
    slug = f"todd-young-gate-{uuid.uuid4().hex[:10]}"
    create = client.post(
        "/cases",
        json={
            "slug": slug,
            "title": "Todd Young Phase 4 gate",
            "subject_name": "Todd Young",
            "subject_type": "public_official",
            "jurisdiction": "Indiana / Federal",
            "created_by": "gate-runner",
            "summary": "Automated Todd Young temporal proximity check.",
            "pickup_note": "",
            "is_public": True,
        },
    )
    if create.status_code != 200:
        print("Case create failed:", create.status_code, create.text, file=sys.stderr)
        return 1
    case_id = create.json().get("id")
    if not case_id:
        print("No case id in response:", create.json(), file=sys.stderr)
        return 1

    inv = client.post(
        f"/api/v1/cases/{case_id}/investigate",
        json=_investigate_body(),
    )
    if inv.status_code != 200:
        print("Investigate failed:", inv.status_code, inv.text, file=sys.stderr)
        return 1

    case_uuid = uuid.UUID(str(case_id))
    db = SessionLocal()
    try:
        ok, _diag = run_assertions(case_uuid, db)
    finally:
        db.close()

    if ok:
        print("\nOVERALL: PASS (all four categories)")
        return 0
    print("\nOVERALL: FAIL — fix the first failing category per Phase 4 Step 1B.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
