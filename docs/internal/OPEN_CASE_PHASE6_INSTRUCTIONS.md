# OPEN CASE — Phase 6 Cursor Instructions
## Synthesized from Five-AI Roundtable — Rounds 5 through 8
## Contributors: Claude Sonnet 4.6, GPT-5.3, Grok, Gemini 3 Flash, Perplexity

---

## What Phase 6 Is

Phase 6 is a closure and hardening phase. Five AI systems in two roundtable
rounds reached the same consensus with no material contradictions:

"You don't have a Phase 6 feature problem. You have a Phase 5 closure problem."

Phase 6 has four parts, in strict order. Do not start Part 2 until Part 1 is
done. Do not start Part 3 until Part 2 is done.

```
Part 1: Run the test. Get PASS. Write the closure artifact.
Part 2: Automate the idempotency check.
Part 3: Add per-investigator API key authentication.
Part 4: Harden BASE_URL for production environments.
```

No new adapters. No BullMQ. No social layer. No Photo Tap. No contract
proximity expansion. No Indiana legislature adapter. Everything else
goes to Phase 7.

---

## Part 1 — Run the Test and Write the Closure Artifact

### Step 1A — Run the test

```bash
export CONGRESS_API_KEY=your_key_here
export FEC_API_KEY=DEMO_KEY
export BASE_URL=http://localhost:8000
python -m scripts.test_todd_young
```

Read the full diagnostic output. Every contributor in the roundtable ranked
Category 3 (evidence_ids intersection / typing mismatch) as the most likely
failure mode, with Category 2 (Congress votes response shape) second most likely.
Category 1 (FEC) is now low-risk because the committee_id path is used.

The diagnostic output from Phase 5 will tell you exactly which category failed
and which source_name/entry_type is wrong. Read it before changing anything.

### Step 1B — Most likely fixes based on diagnostic output

**If Category 3 fails (has_financial_type_in_signal: False or has_decision_type_in_signal: False):**

The signal exists and has the right pattern detected, but the assertion is not
matching the actual entry_type values stored on the EvidenceEntry rows in
the signal's evidence_ids.

Check what the diagnostic output shows for each evidence entry's source_name
and entry_type. Then check the adapter that produced it:

- `adapters/fec.py` — should produce `entry_type="financial_connection"`,
  `source_name="FEC"`. If it says "FEC Campaign Finance" or "fec" or
  "financial_connection_fec" — fix it to exactly "FEC" and "financial_connection".

- `adapters/congress_votes.py` — should produce `entry_type="vote_record"`,
  `source_name="Congress.gov"`. If it says "congressional_vote" or "vote" or
  "decision_event" — fix it to exactly "vote_record".

The assertion uses a set: `DECISION_TYPES = {"vote_record", "decision_event",
"congressional_vote"}`. If the actual stored value is in that set, the
assertion will pass. If it's something else entirely, fix the adapter.

**If Category 2 fails (vote evidence count: 0 or parse_warning):**

The Congress.gov adapter fetched data but could not parse votes, or the API
returned an unexpected shape. Check for parse_warning entries:

```python
# In scripts/test_todd_young.py or a quick db check:
from sqlalchemy import select
from models import EvidenceEntry
stmt = select(EvidenceEntry).where(
    EvidenceEntry.case_file_id == case_id,
    EvidenceEntry.body.ilike("%parse%warning%")
)
```

If parse_warnings exist, the adapter got data but couldn't parse it. Print
the raw API response from the adapter to see the actual shape:

```python
# Temporary debug — add to adapters/congress_votes.py
import json
raw = response.json()
print("CONGRESS RAW RESPONSE KEYS:", list(raw.keys()))
# Find the votes array and update the key mapper
```

Common shapes from Congress.gov for member votes:
- `raw["votes"]["vote"]` — list of vote objects
- `raw["results"]["votes"]` — under a results wrapper
- `raw["memberVotes"]` — for member-specific endpoint

Update the adapter's key mapper to handle whichever shape comes back.

**If Category 1 fails (FEC evidence count: 0):**

Check that the FEC adapter is being called with committee mode when
`fec_committee_id` is set in the fixture:

```python
# In tests/fixtures/todd_young.json — should contain:
{
  "fec_committee_id": "C00459255",
  "proximity_days": 365,
  ...
}
```

Verify the FEC API returns data for this committee:
```bash
curl "https://api.open.fec.gov/v1/schedules/schedule_a/?committee_id=C00459255&api_key=DEMO_KEY&per_page=3"
```

If the API returns data but the adapter is returning empty, add a print
statement to the adapter showing the raw response shape, find where parsing
stops, and fix it.

### Step 1C — Update scripts/test_todd_young.py to write the closure artifact

After the test achieves PASS, the script should auto-generate PHASE5_CLOSURE.md.
This is Perplexity and Gemini's recommendation and it prevents the
"proof scattered across logs and checklists" problem that GPT named.

In `scripts/test_todd_young.py`, add this function and call it after
the RESULT: PASS line:

```python
import subprocess
import datetime
import os

def write_closure_artifact(
    case_id: str,
    category_results: dict,
    signal_count: int,
    evidence_count: int
):
    """
    Write PHASE5_CLOSURE.md to the repo root on successful test pass.
    Called only after RESULT: PASS is confirmed.
    """
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        commit = "unknown"

    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

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
        f"Proximity signals detected: {signal_count}",
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
        "Status: CLOSED",
        f"Signed off: [your investigator handle]",
        f"Date: [confirm date when checklist is complete]",
    ]

    output_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "PHASE5_CLOSURE.md"
    )

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"\nPHASE5_CLOSURE.md written to {output_path}")
    print("Fill in the idempotency section after running scripts/test_idempotency.py")
    print("Check the ten boxes manually and sign off before declaring Phase 5 closed.")
```

Call it from the main test flow, after all categories pass:

```python
# At the bottom of the main test function, after RESULT: PASS:
if all_pass:
    print("\nRESULT: PASS")
    write_closure_artifact(
        case_id=str(case.id),
        category_results={
            1: "PASS",
            2: "PASS",
            3: "PASS",
            4: "PASS",
        },
        signal_count=signal_count,
        evidence_count=evidence_count,
    )
    sys.exit(0)
else:
    print("\nRESULT: FAIL")
    sys.exit(1)
```

Also update `run_assertions()` to return the category results as a dict so
`write_closure_artifact` can receive them.

---

## Part 2 — Automate the Idempotency Check

Create `scripts/test_idempotency.py`. This is a new file, not a modification
to the existing test. It runs separately and addresses the unautomated checklist
item that every roundtable contributor flagged as a regression risk.

```python
# scripts/test_idempotency.py
"""
Idempotency test for the Open Case investigation pipeline.

Verifies that running the same investigation N times produces the same
signal count and evidence count as running it once. This is the signal
deduplication guarantee. If this test fails, the identity hash upsert
logic has broken.

Usage:
    python -m scripts.test_idempotency

Expects:
    - CONGRESS_API_KEY in environment
    - App database accessible (SQLite at default path)

Exit codes:
    0 = PASS (all counts stable after repeated investigate)
    1 = FAIL (counts changed between runs)
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select, func, create_engine
from sqlalchemy.orm import Session
import httpx

from database import DATABASE_URL
from models import CaseFile, EvidenceEntry, Signal

BASE = os.getenv("BASE_URL", "http://localhost:8000")
HANDLE = "idempotency_test"

SUBJECT = {
    "subject_name": "Todd Young",
    "subject_type": "public_official",
    "jurisdiction": "Indiana, USA",
    "bioguide_id": "Y000064",
    "fec_committee_id": "C00459255",
    "proximity_days": 90,
    "investigator_handle": HANDLE,
}


def create_case(client: httpx.Client) -> str:
    r = client.post("/api/v1/cases", json={
        "title": "Idempotency Test Case",
        "subject_name": "Todd Young",
        "subject_type": "public_official",
        "jurisdiction": "Indiana, USA",
        "created_by": HANDLE,
        "summary": "Created by test_idempotency.py — safe to delete."
    })
    assert r.status_code == 200, f"Case creation failed: {r.text}"
    case_id = r.json()["id"]
    print(f"Created case: {case_id}")
    return case_id


def run_investigate(client: httpx.Client, case_id: str) -> dict:
    r = client.post(
        f"/api/v1/cases/{case_id}/investigate",
        json={**SUBJECT, "investigator_handle": HANDLE},
        timeout=120.0,
    )
    assert r.status_code == 200, f"Investigate failed: {r.text}"
    return r.json()


def count_rows(db: Session, case_id: str) -> tuple[int, int]:
    ev_count = db.execute(
        select(func.count(EvidenceEntry.id)).where(
            EvidenceEntry.case_file_id == case_id
        )
    ).scalar()

    sig_count = db.execute(
        select(func.count(Signal.id)).where(
            Signal.case_file_id == case_id,
            Signal.dismissed.is_(False)
        )
    ).scalar()

    return ev_count, sig_count


def main():
    engine = create_engine(DATABASE_URL)

    with httpx.Client(base_url=BASE) as client:
        case_id = create_case(client)

        print("\nRun 1...")
        run_investigate(client, case_id)
        with Session(engine) as db:
            ev1, sig1 = count_rows(db, case_id)
        print(f"  After run 1: evidence={ev1}, signals={sig1}")

        print("Run 2...")
        run_investigate(client, case_id)
        with Session(engine) as db:
            ev2, sig2 = count_rows(db, case_id)
        print(f"  After run 2: evidence={ev2}, signals={sig2}")

        print("Run 3...")
        run_investigate(client, case_id)
        with Session(engine) as db:
            ev3, sig3 = count_rows(db, case_id)
        print(f"  After run 3: evidence={ev3}, signals={sig3}")

    print()

    ev_stable = ev1 == ev2 == ev3
    sig_stable = sig1 == sig2 == sig3

    print(f"Evidence stable (run 1 = 2 = 3): {ev_stable}")
    print(f"Signals stable (run 1 = 2 = 3): {sig_stable}")
    print()

    if ev_stable and sig_stable:
        print("IDEMPOTENCY: PASS")
        print(f"\nPaste into PHASE5_CLOSURE.md:")
        print(f"  Evidence count after 3x: {ev3}")
        print(f"  Signal count after 3x: {sig3}")
        print(f"  Count stable: YES")
        sys.exit(0)
    else:
        print("IDEMPOTENCY: FAIL")
        if not ev_stable:
            print(f"  Evidence counts changed: {ev1} → {ev2} → {ev3}")
            print("  The evidence deduplication hash is not working correctly.")
        if not sig_stable:
            print(f"  Signal counts changed: {sig1} → {sig2} → {sig3}")
            print("  The signal identity hash upsert is not working correctly.")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

Run it after Part 1 passes:

```bash
# App must be running in another terminal
uvicorn main:app --reload &
python -m scripts.test_idempotency
```

Take the output numbers and paste them into the PHASE5_CLOSURE.md idempotency
section.

---

## Part 3 — Per-Investigator API Key Authentication

All five roundtable contributors agreed: Option B (API key per investigator
handle) is the correct Phase 6 authentication architecture. It maps to the
existing Investigator model and handle-based design without JWT complexity
that is not yet needed.

### Step 3A — Add hashed_api_key to Investigator model

In `models.py`, add one column to the Investigator model:

```python
class Investigator(Base):
    # ... all existing columns unchanged ...

    # Added in Phase 6 — authentication
    hashed_api_key: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    api_key_created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

### Step 3B — Create Alembic migration

```bash
alembic revision --autogenerate -m "add hashed api key to investigator"
alembic upgrade head
```

Verify the migration file in `alembic/versions/` looks correct before running it.
It should add `hashed_api_key VARCHAR` and `api_key_created_at DATETIME` to
the investigators table, both nullable.

### Step 3C — Create auth dependency

Create `auth.py` in the project root (next to main.py):

```python
# auth.py
"""
Authentication dependency for Open Case write routes.

GET routes are always unauthenticated — receipts and case files are public.
POST / PATCH / DELETE routes require a valid API key in the Authorization header.

Usage on a route:
    from auth import require_api_key
    @router.post("/cases")
    def create_case(
        request: CaseCreateRequest,
        db: Session = Depends(get_db),
        investigator: Investigator = Depends(require_api_key),
    ):
        ...

Key format: open_case_[64 hex chars]
Keys are stored as SHA-256 hash (not plaintext). Plaintext is returned once on
generation and never retrievable again.
"""

import hashlib
import secrets
import os
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from database import get_db
from models import Investigator


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_raw_key() -> str:
    return f"open_case_{secrets.token_hex(32)}"


async def require_api_key(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
) -> Investigator:
    """
    FastAPI dependency. Validates the Authorization header.
    Returns the authenticated Investigator on success.
    Raises 401 on missing or invalid key.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be: Bearer open_case_...",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_key = authorization.removeprefix("Bearer ").strip()

    if not raw_key.startswith("open_case_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid key format. Expected: open_case_[64 hex chars]",
            headers={"WWW-Authenticate": "Bearer"},
        )

    hashed = hash_key(raw_key)

    investigator = db.execute(
        select(Investigator).where(Investigator.hashed_api_key == hashed)
    ).scalar_one_or_none()

    if investigator is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return investigator
```

### Step 3D — Create the key issuance endpoint

Create `routes/auth.py`:

```python
# routes/auth.py
"""
API key management endpoint.

POST /api/v1/auth/keys?handle=your_handle
- If the handle does not exist, creates the Investigator row.
- Generates a new key, hashes it, stores the hash.
- Returns the plaintext key ONCE. It is not stored.
- Subsequent calls generate a new key (old key revoked).

This endpoint is intentionally unauthenticated so new investigators
can get their first key.

Security note: anyone who knows a handle can request a new key,
which revokes the old one. This is acceptable for V0 (pseudonymous
single-operator usage). Multi-user key management belongs in Phase 7.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone

from database import get_db
from models import Investigator
from auth import generate_raw_key, hash_key

router = APIRouter()


@router.post("/api/v1/auth/keys")
def generate_api_key(
    handle: str = Query(..., description="Investigator handle to generate key for"),
    db: Session = Depends(get_db),
):
    """
    Generate a new API key for an investigator handle.
    
    The plaintext key is returned ONCE and not stored. If lost,
    a new key must be generated (which revokes the old one).
    
    Call this endpoint to get your first key:
        curl -X POST "http://localhost:8000/api/v1/auth/keys?handle=your_handle"
    
    Use the key on write endpoints:
        curl -X POST http://localhost:8000/api/v1/cases \\
          -H "Authorization: Bearer open_case_..." \\
          -H "Content-Type: application/json" \\
          -d '{...}'
    """
    investigator = db.execute(
        select(Investigator).where(Investigator.handle == handle)
    ).scalar_one_or_none()

    if not investigator:
        investigator = Investigator(
            handle=handle,
            credibility_score=0.0,
            cases_opened=0,
            entries_contributed=0,
        )
        db.add(investigator)

    raw_key = generate_raw_key()
    investigator.hashed_api_key = hash_key(raw_key)
    investigator.api_key_created_at = datetime.now(timezone.utc)

    db.commit()

    return {
        "handle": handle,
        "api_key": raw_key,
        "format": "open_case_[64 hex chars]",
        "warning": (
            "This key will not be shown again. Store it securely. "
            "Calling this endpoint again will revoke this key."
        ),
        "usage": f"Authorization: Bearer {raw_key}",
    }
```

### Step 3E — Register the auth router in main.py

In `main.py`, add the import and include the router:

```python
from routes.auth import router as auth_router

# In the app setup, alongside existing router includes:
app.include_router(auth_router)
```

### Step 3F — Add the auth dependency to write routes

Import the dependency in each write-route file:

```python
from auth import require_api_key
from models import Investigator
```

Then add it to POST, PATCH, and DELETE endpoints. Example in `routes/cases.py`:

```python
@router.post("/api/v1/cases")
def create_case(
    request: CaseCreateRequest,
    db: Session = Depends(get_db),
    _investigator: Investigator = Depends(require_api_key),
):
    # ... existing implementation unchanged ...
```

The underscore prefix on `_investigator` signals it's used for auth only,
not for its data. If you need the handle from the key, use `investigator.handle`
instead of the request body's `created_by` field.

Apply `Depends(require_api_key)` to these routes:
- `POST /cases` — create case
- `PATCH /cases/{id}/status` — update status
- `POST /cases/{id}/pickup` — take ownership
- `POST /cases/{id}/subject` — set subject profile
- `POST /cases/{id}/evidence` — add evidence
- `PATCH /evidence/{id}/disambiguate` — resolve collision
- `POST /cases/{id}/investigate` — run investigation
- `PATCH /signals/{id}/confirm` — confirm signal
- `PATCH /signals/{id}/dismiss` — dismiss signal
- `PATCH /signals/{id}/expose` — expose to receipt
- `POST /cases/{id}/snapshot` — generate snapshot

Do NOT add auth to:
- `GET /cases/{id}` — case files are public
- `GET /cases/{id}/report` — reports are public
- `GET /cases/{id}/report/view` — HTML view is public
- `GET /cases/{id}/report/card` — receipt card is public
- `GET /cases/{id}/signals` — signal list is public
- `GET /signals/{id}/history` — audit history is public
- `GET /subjects/search` — search is public
- `GET /subjects/bioguide/{id}` — lookup is public
- `GET /verify/public-key` — key verification is public
- `POST /api/v1/auth/keys` — must be unauthenticated (first key issuance)

### Step 3G — Test authentication manually

```bash
# Start the app
uvicorn main:app --reload

# Get a key for your handle
curl -X POST "http://localhost:8000/api/v1/auth/keys?handle=my_handle"
# Returns: {"api_key": "open_case_abc123..."}

# Try creating a case without a key (should fail with 401)
curl -X POST http://localhost:8000/api/v1/cases \
  -H "Content-Type: application/json" \
  -d '{"title": "test", "subject_name": "test", ...}'
# Expected: 401 Unauthorized

# Try with the key (should succeed)
curl -X POST http://localhost:8000/api/v1/cases \
  -H "Authorization: Bearer open_case_abc123..." \
  -H "Content-Type: application/json" \
  -d '{"title": "test", "subject_name": "test", ...}'
# Expected: 200

# Verify GET routes still work without auth
curl http://localhost:8000/api/v1/cases/{any_case_id}/report
# Expected: 200 (public)
```

---

## Part 4 — BASE_URL Production Hardening

Grok and Perplexity both recommended making BASE_URL a hard error in
production while keeping the warning behavior in development.

In `main.py`, update `check_config_warnings()`:

```python
import sys

def check_config_warnings():
    """
    Validate configuration at startup.
    
    In production (ENV=production): missing or localhost BASE_URL is a hard error.
    In development: it is a warning.
    
    CONGRESS_API_KEY missing is always a warning (app still starts,
    but vote records will not be fetched).
    """
    env = os.getenv("ENV", "development").lower()
    base_url = os.getenv("BASE_URL", "")
    is_localhost = not base_url or "localhost" in base_url.lower()

    if is_localhost:
        if env == "production":
            logger.error(
                "BASE_URL is not set to a production URL. "
                "Cannot start in production mode with localhost BASE_URL. "
                "Receipt card OG tags would point to localhost, breaking all shares. "
                "Set BASE_URL=https://your-domain.com or set ENV=development to override."
            )
            sys.exit(1)
        else:
            logger.warning(
                "BASE_URL is not set or points to localhost. "
                "Receipt card OG tags will use localhost URLs. "
                "Set BASE_URL=https://your-domain.com before public deployment."
            )

    if not os.getenv("CONGRESS_API_KEY"):
        logger.warning(
            "CONGRESS_API_KEY is not set. "
            "Congress.gov vote records will not be fetched. "
            "Get a free key at https://api.data.gov/signup/"
        )
```

In `.env.example` (or update the existing one), add:

```
# Set to "production" on Render to enable strict validation
ENV=development
```

In the Render environment settings, add `ENV=production` so the hard error
applies in deployment.

---

## Definition of Done for Phase 6

Phase 6 is complete when:

```
[ ] Part 1: scripts/test_todd_young exits 0
             All four categories PASS
             PHASE5_CLOSURE.md has been written with real data

[ ] Part 1: scripts/test_idempotency exits 0
             Signal count is stable after 3x investigate
             Evidence count is stable after 3x investigate
             PHASE5_CLOSURE.md idempotency section filled in

[ ] Part 1: Ten-box checklist confirmed manually
             All ten boxes checked
             PHASE5_CLOSURE.md checklist section filled in and signed

[ ] Part 3: POST /api/v1/auth/keys generates a valid key
             POST /cases without a key returns 401
             POST /cases with a valid key returns 200
             GET /cases/{id}/report without a key returns 200

[ ] Part 4: APP started with ENV=production and localhost BASE_URL fails to start
             APP started with ENV=development and localhost BASE_URL logs warning only
```

---

## What Phase 7 Looks Like

The roundtable reached consensus on Phase 7 priorities. They are not Phase 6's
problem. For reference:

**First Phase 7 priority: BullMQ async queuing**
The investigation pipeline is synchronous. It will time out under production load.
The DEBRIEF project's BullMQ/Redis implementation is the template.

**Second: Contract proximity real-data validation**
The contract proximity engine has never been confirmed to produce a signal on
real USASpending data.

**Third: Indiana state legislature adapter**
Extends temporal proximity to state-level officials.

**After those: Photo Tap, social layer, network graph, Forward to Authority.**

None of these are Phase 6. Phase 6 ends with a system that is verified,
idempotent, authenticated, and safe to deploy to a public URL.

---

## Files Changed in Phase 6

```
New files:
  auth.py                          # API key dependency and key management
  routes/auth.py                   # Key issuance endpoint
  scripts/test_idempotency.py      # Idempotency automation test
  PHASE5_CLOSURE.md                # Auto-generated by test_todd_young on PASS

Modified files:
  models.py                        # +hashed_api_key, +api_key_created_at to Investigator
  main.py                          # +auth router, +ENV-aware BASE_URL check
  scripts/test_todd_young.py       # +write_closure_artifact() called on PASS
  routes/cases.py                  # +require_api_key on write endpoints
  routes/evidence.py               # +require_api_key on write endpoints
  routes/evidence_disambig.py      # +require_api_key on write endpoints
  routes/investigate.py            # +require_api_key on POST/PATCH endpoints
  routes/reporting.py              # +require_api_key on snapshot/expose endpoints

New migration:
  alembic/versions/XXX_add_hashed_api_key_to_investigator.py
```

---

*OPEN CASE — Phase 6: Close, Harden, Authenticate*
*Roundtable consensus: five systems, no contradictions on core scope*
*Phase 6 ends with a system that is proven to work and safe to expose*
