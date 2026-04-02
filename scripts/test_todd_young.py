"""
Todd Young gate (Phase 4): structured four-category diagnostics (Step 1A).
Requires network (Senate.gov vote XML, FEC, etc.). Optional CONGRESS_API_KEY. Uses TestClient + DB assertions.

On full PASS, writes PHASE5_CLOSURE.md (Phase 6 closure artifact; fill
idempotency + checklist sections after follow-up runs).
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import func, select

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def write_closure_artifact(
    case_id: str,
    category_results: dict[int, str],
    signal_count: int,
    evidence_count: int,
) -> None:
    """Write PHASE5_CLOSURE.md to repo root on successful Todd Young PASS."""
    try:
        commit = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parent.parent,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        commit = "unknown"

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        "# PHASE 5 CLOSURE — CONFIRMED",
        "",
        "## Test Execution",
        f"Generated: {timestamp}",
        f"Commit: {commit}",
        f"Test subject: Todd Young (Y000064, committee C00459255)",
        f"Case ID: {case_id}",
        "",
        "## Category Results",
        f"Category 1 (FEC data path): {category_results.get(1, 'UNKNOWN')}",
        f"Category 2 (Congress votes): {category_results.get(2, 'UNKNOWN')}",
        f"Category 3 (Evidence intersection): {category_results.get(3, 'UNKNOWN')}",
        f"Category 4 (Readable narrative): {category_results.get(4, 'UNKNOWN')}",
        "",
        "**RESULT: PASS**",
        "",
        "## Signal and Evidence Counts (after single investigate)",
        f"Evidence entries: {evidence_count}",
        f"Signals detected (all types, non-dismissed): {signal_count}",
        "",
        "## Idempotency (after 3x investigate)",
        "Evidence count after 3x: [FILL IN after running scripts/test_idempotency.py]",
        "Signal count after 3x: [FILL IN after running scripts/test_idempotency.py]",
        "Count stable: [YES/NO]",
        "",
        "## Ten-Box Checklist — Manual Confirmation Required",
        "[ ] 1. python -m scripts.test_todd_young exits 0, all 4 categories PASS",
        "[ ] 2. 3x investigate → stable signal count (idempotency)",
        "[ ] 3. Report HTML view renders, signals visible",
        "[ ] 4. PATCH /expose returns 400 for unconfirmed signal",
        "[ ] 5. PATCH /confirm succeeds (200)",
        "[ ] 6. PATCH /expose on confirmed signal succeeds (200)",
        "[ ] 7. Receipt card HTML renders, no og:image tag in source",
        "[ ] 8. GET /signals/{id}/history returns audit trail",
        "[ ] 9. GET /subjects/search?name=Todd+Young returns result",
        "[ ] 10. Startup warning appears when CONGRESS_API_KEY is unset",
        "",
        "## Phase 5 Status",
        "Status: CLOSED (pending manual checklist + idempotency fill-in)",
        "Signed off: [your investigator handle]",
        "Date: [confirm date when checklist is complete]",
    ]

    output_path = Path(__file__).resolve().parent.parent / "PHASE5_CLOSURE.md"

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nPHASE5_CLOSURE.md written to {output_path}")
    print("Fill in the idempotency section after running scripts/test_idempotency.py")
    print("Check the ten boxes manually and sign off before declaring Phase 5 closed.")

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
    from database import SessionLocal
    from main import app
    from models import EvidenceEntry, Signal
    from scripts.todd_young_assertions import run_assertions

    client = TestClient(app)
    key_r = client.post("/api/v1/auth/keys", params={"handle": "gate-runner"})
    if key_r.status_code != 200:
        print("Auth key mint failed:", key_r.status_code, key_r.text, file=sys.stderr)
        return 1
    auth_headers = {"Authorization": f"Bearer {key_r.json()['api_key']}"}

    slug = f"todd-young-gate-{uuid.uuid4().hex[:10]}"
    create = client.post(
        "/cases",
        headers=auth_headers,
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
        headers=auth_headers,
        json=_investigate_body(),
    )
    if inv.status_code != 200:
        print("Investigate failed:", inv.status_code, inv.text, file=sys.stderr)
        return 1

    case_uuid = uuid.UUID(str(case_id))
    db = SessionLocal()
    try:
        ok, _diag, category_results = run_assertions(case_uuid, db)
        evidence_count = (
            db.scalar(
                select(func.count())
                .select_from(EvidenceEntry)
                .where(EvidenceEntry.case_file_id == case_uuid)
            )
            or 0
        )
        signal_count = (
            db.scalar(
                select(func.count())
                .select_from(Signal)
                .where(
                    Signal.case_file_id == case_uuid,
                    Signal.dismissed.is_(False),
                )
            )
            or 0
        )
    finally:
        db.close()

    if ok:
        print("\nOVERALL: PASS (all four categories)")
        write_closure_artifact(
            case_id=str(case_id),
            category_results=category_results,
            signal_count=int(signal_count),
            evidence_count=int(evidence_count),
        )
        return 0
    print(
        "\nOVERALL: FAIL — fix the first failing category per Phase 6 Step 1B.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
