"""
Idempotency test: N sequential investigate runs must not grow evidence/signal counts.

Requires:
  - Running app (e.g. uvicorn main:app)
  - Network (Senate vote XML + FEC); optional CONGRESS_API_KEY
  - Shared SQLite DB as the running process (default ./open_case.db)

Usage:
  uvicorn main:app --reload   # terminal A
  python -m scripts.test_idempotency   # terminal B
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import func, select

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import SessionLocal  # noqa: E402
from models import EvidenceEntry, Signal  # noqa: E402

BASE = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
HANDLE = "idempotency_test"

INVESTIGATE_JSON = {
    "subject_name": "Todd Young",
    "subject_type": "public_official",
    "bioguide_id": "Y000064",
    "fec_committee_id": "C00459255",
    "proximity_days": 365,
    "investigator_handle": HANDLE,
}


def _mint_key(client: httpx.Client) -> dict[str, str]:
    r = client.post(f"{BASE}/api/v1/auth/keys", params={"handle": HANDLE})
    if r.status_code != 200:
        raise RuntimeError(f"Key mint failed {r.status_code}: {r.text}")
    token = r.json()["api_key"]
    return {"Authorization": f"Bearer {token}"}


def _create_case(client: httpx.Client, headers: dict[str, str]) -> str:
    slug = f"idempotency-{uuid.uuid4().hex[:12]}"
    r = client.post(
        f"{BASE}/cases",
        headers=headers,
        json={
            "slug": slug,
            "title": "Idempotency Test Case",
            "subject_name": "Todd Young",
            "subject_type": "public_official",
            "jurisdiction": "Indiana, USA",
            "created_by": HANDLE,
            "summary": "Created by scripts/test_idempotency.py — safe to delete.",
        },
    )
    if r.status_code != 200:
        raise RuntimeError(f"Case create failed {r.status_code}: {r.text}")
    return str(r.json()["id"])


def _investigate(client: httpx.Client, case_id: str, headers: dict[str, str]) -> None:
    r = client.post(
        f"{BASE}/api/v1/cases/{case_id}/investigate",
        headers=headers,
        json=INVESTIGATE_JSON,
        timeout=180.0,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Investigate failed {r.status_code}: {r.text}")


def _counts(case_id: str) -> tuple[int, int]:
    cid = uuid.UUID(case_id)
    with SessionLocal() as db:
        ev = (
            db.scalar(
                select(func.count())
                .select_from(EvidenceEntry)
                .where(EvidenceEntry.case_file_id == cid)
            )
            or 0
        )
        sig = (
            db.scalar(
                select(func.count())
                .select_from(Signal)
                .where(
                    Signal.case_file_id == cid,
                    Signal.dismissed.is_(False),
                )
            )
            or 0
        )
    return int(ev), int(sig)


def main() -> int:
    with httpx.Client() as client:
        headers = _mint_key(client)
        case_id = _create_case(client, headers)
        print(f"Created case: {case_id}")

        print("\nRun 1...")
        _investigate(client, case_id, headers)
        ev1, sig1 = _counts(case_id)
        print(f"  After run 1: evidence={ev1}, signals={sig1}")

        print("Run 2...")
        _investigate(client, case_id, headers)
        ev2, sig2 = _counts(case_id)
        print(f"  After run 2: evidence={ev2}, signals={sig2}")

        print("Run 3...")
        _investigate(client, case_id, headers)
        ev3, sig3 = _counts(case_id)
        print(f"  After run 3: evidence={ev3}, signals={sig3}")

    ev_stable = ev1 == ev2 == ev3
    sig_stable = sig1 == sig2 == sig3
    print()
    print(f"Evidence stable (run 1 = 2 = 3): {ev_stable}")
    print(f"Signals stable (run 1 = 2 = 3): {sig_stable}")
    print()

    if ev_stable and sig_stable:
        print("IDEMPOTENCY: PASS")
        print("\nPaste into PHASE5_CLOSURE.md idempotency section:")
        print(f"  Evidence count after 3x: {ev3}")
        print(f"  Signal count after 3x: {sig3}")
        print("  Count stable: YES")
        return 0

    print("IDEMPOTENCY: FAIL", file=sys.stderr)
    if not ev_stable:
        print(f"  Evidence counts: {ev1} → {ev2} → {ev3}", file=sys.stderr)
    if not sig_stable:
        print(f"  Signal counts: {sig1} → {sig2} → {sig3}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
