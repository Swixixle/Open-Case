# OPEN CASE — Engineering Report
## Technical Handoff Document — Phase 5 State
### Nikodemus Systems / Swixixle

**The system's core detection loop is unverified. Run the smoke test.**

---

## ⚠ VERIFICATION STATUS — READ THIS BEFORE ANYTHING ELSE

This section describes what has been implemented versus what has been confirmed
to work on real data. **Implemented** and **verified** are different columns on purpose.
A new engineer who does not read this section first will inherit false confidence
about the system state.

When the Todd Young gate passes, paste the full `RESULT: PASS` log into the block
below and update the table rows from UNKNOWN to CONFIRMED. The same placeholder
exists in `CONTEXT.md` until that log exists — the document is not a complete
operational handoff until it does.

### Recorded Todd Young output (paste when confirmed)

```text
_(Paste full console output from `python -m scripts.test_todd_young` showing Category 1–4 PASS and `RESULT: PASS`.)_
```

### Verification matrix (Phase 5 — code complete, live gate open)

| Capability | Implemented | Verified | Notes |
| --- | --- | --- | --- |
| Todd Young end-to-end (`scripts.test_todd_young`) | Yes | **UNKNOWN** | Requires live APIs + keys; no embedded PASS log yet |
| Category 1 — FEC / data path alive | Yes | **UNKNOWN** | Assertions in `todd_young_assertions.py` |
| Category 2 — vote / signal path | Yes | **UNKNOWN** | Depends on Congress.gov response shape |
| Category 3 — financial ∩ decision evidence | Yes | **UNKNOWN** | Highest-risk failure; diagnostics print per-entry types |
| Category 4 — readable narrative (`weight_explanation`) | Yes | **UNKNOWN** | Depends on 1–3 |
| Ten-box checklist (Phase 5 instructions) | Yes | **MANUAL** | Not automated; walk each box explicitly |
| Idempotency (repeated investigate) | Yes | **UNKNOWN** | Dedup code present; not exercised on record |
| Signal identity hash deduplication | Yes | **UNKNOWN** | `signals/dedup.py` — never proven end-to-end |
| Atomic investigate transaction | Yes | **UNKNOWN** | Single `commit()` after adapters + engines |
| Confirm-before-expose (HTTP 400 if unconfirmed) | Yes | **UNKNOWN** | `routes/reporting.py` — needs live confirm/expose attempt |
| `check_config_warnings()` (BASE_URL, Congress key) | Yes | **UNKNOWN** | Logs only; behavior not acceptance-tested |
| `is_featured` (`weight >= 0.5` in API/HTML) | Yes | **UNKNOWN** | Not a DB column; computed in responses |
| `og:image` removed from receipt card | Yes | **Yes (visual)** | Template/CSS inspection; no social image tag |
| Category 3 diagnostic logging | Yes | **Yes (code review)** | Set-based assertions; deterministic in review |
| `/static` StaticFiles mount + bundled receipt PNG | **Removed** | **Yes (code)** | Phase 5: no lazy-loaded social image; card HTML only |
| Per-handle API key on write routes | Yes | **UNKNOWN** | Mint at `POST /api/v1/auth/keys`; needs security review in prod |
| `ENV=production` + invalid `BASE_URL` exits process | Yes | **UNKNOWN** | Manual smoke: start with `ENV=production` localhost |
| `PHASE5_CLOSURE.md` on Todd Young PASS | Yes | **UNKNOWN** | Written only after full category PASS |
| HTTP idempotency harness (`scripts/test_idempotency`) | Yes | **UNKNOWN** | Requires running server + shared DB |

### What "unverified" means here

Unverified means the code was written to be correct, the logic is sound, and
peer review across five AI systems found no obvious bugs — but the code has
never executed against real external data (FEC API, Congress.gov) in a
controlled test with recorded output.

The distinction matters for a new engineer because: a system can have correct
logic and still fail at the API boundary. Date format mismatches, unexpected
JSON keys, rate limit errors, and authentication failures are not visible in
code review. They are visible in test output.

### The smoke test you must run before doing anything else

```bash
cd Open-Case
source .venv/bin/activate
export CONGRESS_API_KEY=your_key_here
export FEC_API_KEY=DEMO_KEY   # or your key
export BASE_URL=http://localhost:8000

python -m scripts.test_todd_young
```

Expected output when passing:

```
Category 1 (Data path alive): PASS — X FEC entries, Y vote entries
Category 2 (Signal exists): PASS — Z proximity signals detected
Category 3 (Evidence intersection): PASS — financial + vote both present
Category 4 (Readable narrative): PASS — weight_explanation present

RESULT: PASS
```

If any category fails, the diagnostic JSON output will tell you exactly which
entry_type or source_name is wrong. Read that output before changing anything.

The most likely failure mode based on five-roundtable analysis: Category 1 or 2
fails first (external API did not return parseable data), before Category 3
(evidence intersection logic) gets a chance to run. Do not assume Category 3
is the problem until Categories 1 and 2 are confirmed to pass.

---

## THE SYSTEM IN ONE PARAGRAPH

Open Case is a FastAPI + SQLite civic investigation platform. It takes a
public official's name and ID, queries public record databases (FEC donations,
USASpending contracts, Congress.gov votes, Indiana property records), detects
patterns (donation → vote timing, contract anomalies), stores findings as
cryptographically signed evidence entries, scores detected patterns as signals
with plain-English explanations, requires human confirmation before signals
can be published, and generates a shareable receipt card from confirmed signals.

The philosophy: receipts, not verdicts. The system documents what public
records show. It does not reach conclusions.

---

## PHASE 5 DELIVERY (THIS SNAPSHOT)

What shipped in code for the Phase 5 closure gate (see also `CONTEXT.md` and,
when available in your tree, `OPEN_CASE_PHASE5_INSTRUCTIONS.md` for the **manual
ten-box** definition of done):

1. **Todd Young Category 3 — forward-compatible types.** `scripts/todd_young_assertions.py`
   defines `FINANCIAL_TYPES` and `DECISION_TYPES`. Category 3 requires at least one
   evidence row linked to the top temporal-proximity signal whose `entry_type` falls
   in each set. On failure, the script prints a **Category 3 Evidence Debug** block
   (signal id, evidence ids, per-row `source_name` / `entry_type` / title snippet) so
   the next engineer sees the mismatch without guessing. **Note:** Category 1’s SQL
   count for “votes” still filters `entry_type == "vote_record"` only; Congress
   adapters today emit `vote_record`. If you add `decision_event` / `congressional_vote`
   to live data, ensure Category 1’s query stays aligned or the gate will disagree
   with Category 3.

2. **Receipt card — text OG only.** `GET /api/v1/cases/{id}/report/card` emits
   `og:url`, `og:type`, `og:title`, `og:description`. **`og:image` is intentionally
   absent** (no hot-linked or static preview asset). The old placeholder
   `static/receipt-card-preview.png` and **`StaticFiles` mount were removed** from
   `main.py`. Social crawlers get a text summary only. *(The template still sets
   `twitter:card` to `summary_large_image` without an image — expect inconsistent
   Twitter cards until that card type is revisited.)*

3. **Startup configuration.** `check_config_warnings()` runs at the start of app
   lifespan (before `init_db()`). **`ENV=production`:** missing or localhost
   **`BASE_URL` causes `sys.exit(1)`**. Otherwise **`BASE_URL`** issues log a **warning** only.
   Missing **`CONGRESS_API_KEY`** is always a **warning** (app still starts).

4. **`is_featured` everywhere signals surface.** Threshold **`weight >= 0.5`**, hardcoded
   in **`routes/investigate.py`** (investigate response + `GET .../signals`) and
   **`routes/reporting.py`** (report JSON/HTML payload). **Not** a database column.

5. **HTML report — notable vs other.** `templates/report.html` splits **“Notable signals
   (weight ≥ 0.5)”** from a muted **“All other signals”** section using Jinja2
   `selectattr('is_featured')` / `rejectattr('is_featured')`.

6. **App wiring.** `main.py` mounts six routers: `auth`, `cases`, `investigate`,
   `evidence_disambig`, `reporting`, `subjects` — no global static directory.

---

## STACK

| Component | Technology | Version |
|---|---|---|
| Web framework | FastAPI | 0.104+ |
| Database | SQLite via SQLAlchemy | 2.0 |
| Migrations | Alembic | latest |
| Signing | Ed25519 (cryptography library) | latest |
| Templates | Jinja2 | latest |
| Python | 3.11+ | required |
| Deployment | Render | free/paid tier |
| Async queuing | NOT BUILT | BullMQ/Redis planned Phase 6 |
| Write-path authentication | API key per handle | Phase 6: Bearer `open_case_*`, `POST /api/v1/auth/keys` |
| Multi-user UI | NOT BUILT | planned Phase 7+ |
| Global static assets (`/static`) | NOT MOUNTED | Receipt card is standalone HTML; Phase 5 removed preview PNG |

---

## ENVIRONMENT VARIABLES

### Required for any real data

```
CONGRESS_API_KEY=your_key_here
  Required for Congress.gov vote records.
  Get free key: https://api.data.gov/signup/
  Without this: votes adapter returns empty results silently.
  Warning logged at startup if missing.
```

### Optional but important

```
FEC_API_KEY=DEMO_KEY
  DEMO_KEY is the default; rate-limited to 1,000 req/day.
  For development: DEMO_KEY is fine.
  Get your own key: https://api.open.fec.gov/

BASE_URL=https://your-domain.onrender.com
  Used for absolute URLs in receipt card OG tags.
  Default: http://localhost:8000
  Warning logged at startup if unset or contains "localhost".
  Not setting this in production means og:url points to localhost.
  This causes broken social previews when the card is shared.
```

### Auto-generated on first boot

```
OPEN_CASE_PRIVATE_KEY=
  Ed25519 private key, base64-encoded PKCS#8 DER.
  Generated automatically if absent. Written to .env.
  Do not regenerate after first use — all prior signatures
  become unverifiable if the private key changes.

OPEN_CASE_PUBLIC_KEY=
  Corresponding public key. Auto-generated with private key.
  Stored in `.env` alongside the private key. There is no `GET /verify/public-key`
  HTTP route in this codebase today — verify signatures offline using `signing.py`
  (or embedded `signature_check` fields on evidence/case responses).
```

### Startup behavior

At startup (lifespan), `check_config_warnings()` runs before `init_db()`.
If **`ENV=production`** and **`BASE_URL`** is empty or contains **`localhost`**, the
process **exits**. In **development**, the same condition is a **warning** only.
Missing **`CONGRESS_API_KEY`** is always a **warning**. Then `init_db()` runs
Alembic migrations automatically.

---

## API ENDPOINT MAP

Routers use **two prefixes**. Case CRUD, evidence, and snapshots live under **`/cases`**
(no `/api/v1`). Investigation, reporting, signal actions, and subjects use **`/api/v1`**.

### Cases (`/cases`)

```
POST   /cases                         Create case file
GET    /cases/{id}                    Get case metadata + evidence entries (+ signature_check)
GET    /cases/browse/available        List cases by status/jurisdiction
PATCH  /cases/{id}/status             Update status, set pickup_note
POST   /cases/{id}/pickup             Take ownership of stalled case
POST   /cases/{id}/evidence          Add manual evidence entry
POST   /cases/{id}/snapshot          Generate signed point-in-time snapshot
```

Signals are **not** embedded on `GET /cases/{id}` — list them with `GET /api/v1/cases/{id}/signals`.

### Investigation & signals (`/api/v1`)

```
POST   /api/v1/cases/{id}/investigate Run full pipeline (adapters + engines)
GET    /api/v1/cases/{id}/signals     All signals for a case (JSON)
PATCH  /api/v1/signals/{id}/confirm   Confirm a signal (required before expose)
PATCH  /api/v1/signals/{id}/dismiss   Dismiss with reason
PATCH  /api/v1/signals/{id}/expose    Publish to receipt (requires confirmed=True)
GET    /api/v1/signals/{id}/history   Audit log for a signal
```

### Evidence disambiguation (`/api/v1/evidence`)

There is no `GET /api/v1/evidence/{id}` route — read evidence via `GET /cases/{id}`
(`evidence_entries` on the case payload) or from a report response.

```
PATCH  /api/v1/evidence/{id}/disambiguate  Resolve collision (confirm entity match)
```

### Reporting (`/api/v1`)

```
GET    /api/v1/cases/{id}/report             Full JSON report
GET    /api/v1/cases/{id}/report/view        HTML police-report format (Jinja2)
GET    /api/v1/cases/{id}/report/card        Shareable receipt card (OG text tags)
GET    /api/v1/investigators/{handle}/score Credibility score
```

### Subjects (`/api/v1/subjects`)

```
GET    /api/v1/subjects/search?name=   Search by name (Indiana roster stub + Congress.gov)
GET    /api/v1/subjects/bioguide/{id} Look up roster row by bioguide_id
```

`SubjectProfile` rows for a case are created/updated inside **`POST /api/v1/cases/{id}/investigate`**
(when `subject_type == public_official`), not via a separate “subject” REST route.

### Auth (`/api/v1/auth`)

```
POST   /api/v1/auth/keys?handle=... Mint API key (Bearer token); plaintext once
```

Unauthenticated by design for first-key bootstrap. **Subsequent write calls** use
`Authorization: Bearer open_case_<64 hex>`.

### Utility

There is **no** dedicated HTTP endpoint for publishing the Ed25519 public key. Use
`OPEN_CASE_PUBLIC_KEY` from the environment or the signing helpers in `signing.py`.

---

## DATA MODELS

### CaseFile

The top-level investigation object.

| Column | Type | Notes |
|---|---|---|
| id | UUID | primary key |
| slug | string | human-readable URL segment |
| title | string | |
| subject_name | string | |
| subject_type | string | public_official / corporation / organization |
| jurisdiction | string | |
| status | string | open / active / needs_pickup / stalled / closed / referred |
| summary | string | |
| pickup_note | string | note left when marking needs_pickup |
| created_by | string | investigator handle |
| created_at | datetime | |
| signed_hash | text | Ed25519 signature, updated when evidence added |
| is_public | bool | |
| view_count | int | |

### EvidenceEntry

Every piece of evidence. Signed individually.

| Column | Type | Notes |
|---|---|---|
| id | UUID | primary key |
| case_file_id | UUID | FK → CaseFile |
| entry_type | string | **must be one of the canonical types below** |
| title | string | short label |
| body | string | plain English description |
| source_url | string | direct link to primary source |
| source_name | string | adapter name (e.g. "FEC", "USASpending") |
| date_of_event | string | ISO format date of underlying event |
| amount | float | if financial |
| entered_by | string | investigator handle or adapter name |
| entered_at | datetime | |
| confidence | string | confirmed / probable / unverified |
| is_absence | bool | True = documented gap ("we looked, found nothing") |
| flagged_for_review | bool | True = collision warning, needs disambiguation |
| evidence_hash | string | SHA-256 of semantic payload, deduplication key |
| adapter_name | string | which adapter produced this |
| matched_name | string | name as it appears in source |
| raw_data_json | text | full API response for audit |
| disambiguation_note | string | resolution note after collision cleared |
| disambiguation_by | string | handle who resolved |
| disambiguation_at | datetime | |
| signed_hash | text | Ed25519 signature |

**Canonical entry_type values (do not use other values — tests check these):**

```python
FINANCIAL_TYPES = {"financial_connection"}
DECISION_TYPES = {"vote_record", "decision_event", "congressional_vote"}
OTHER_TYPES = {"property_record", "court_record", "disclosure",
               "timeline_event", "gap_documented"}
```

Type naming drift is the most likely Category 3 failure mode. If you add a
new adapter, use exactly one of these strings. Do not invent new entry_types
without also updating the assertion sets in `scripts/todd_young_assertions.py`.

### Signal

A detected pattern. Has a complete lifecycle from created → confirmed → exposed.

| Column | Type | Notes |
|---|---|---|
| id | UUID | primary key |
| case_file_id | UUID | FK → CaseFile |
| signal_type | string | temporal_proximity / contract_anomaly / contract_proximity |
| weight | float | 0.0 – 1.0 |
| description | string | plain English description |
| weight_explanation | string | why this weight was assigned |
| weight_breakdown | JSON | component scores |
| evidence_ids | JSON | list of EvidenceEntry UUIDs that support this signal |
| actor_a | string | first actor in the correlation |
| actor_b | string | second actor |
| event_date_a | string | date of first event |
| event_date_b | string | date of second event |
| days_between | int | days between event_a and event_b |
| amount | float | dollar amount if financial |
| signal_identity_hash | string | SHA-256 dedup key — UNIQUE per case |
| repeat_count | int | how many investigate runs detected this same pattern |
| proximity_summary | string | human-readable aggregation |
| parse_warning | string | set when adapter fetched data but parser found nothing |
| confirmed | bool | human confirmed this signal |
| confirmed_by | string | handle (no separate `confirmed_at` column) |
| dismissed | bool | |
| dismissed_by | string | handle (no separate `dismissed_at` column) |
| dismissed_reason | string | required on dismiss |
| exposure_state | string | internal / released |
| routing_log | JSON | audit trail for signal routing |
| *(computed in API)* | — | **`is_featured`** = `(weight >= 0.5)` on investigate, list-signals, and report payloads — not stored on `Signal` |

**The confirm-before-expose rule:**
`PATCH /api/v1/signals/{id}/expose` returns 400 UNCONFIRMED_SIGNAL if `confirmed != True`.
This is enforced in `routes/reporting.py`. It is architectural, not optional.
Do not remove or bypass this check.

### SignalAuditLog

Every state change to a signal. Append-only.

| Column | Type | Notes |
|---|---|---|
| id | UUID | primary key |
| signal_id | UUID | FK → Signal |
| action | string | created / weight_updated / confirmed / dismissed / exposed |
| performed_by | string | handle |
| performed_at | datetime | |
| old_weight | float | |
| new_weight | float | |
| note | string | |

### SourceCheckLog

Every adapter query, including empty results. Documented absence is real evidence.

| Column | Type | Notes |
|---|---|---|
| id | UUID | primary key |
| case_file_id | UUID | FK → CaseFile |
| source_name | string | adapter that ran |
| query_string | string | what was searched |
| result_count | int | 0 = documented absence |
| checked_at | datetime | |
| checked_by | string | handle or "pipeline" |
| result_hash | string | SHA-256 of response (for cache verification) |

### AdapterCache

Cached adapter responses. Prevents API exhaustion during development.

| Column | Type | Notes |
|---|---|---|
| id | UUID | primary key |
| adapter_name | string | |
| query_hash | string | SHA-256(adapter_name + ":" + query), dedup key |
| response_json | text | serialized AdapterResponse |
| query_string | string | |
| created_at | datetime | |
| expires_at | datetime | created_at + TTL |
| ttl_hours | int | default 4 |

### SubjectProfile

Public official or entity being investigated.

| Column | Type | Notes |
|---|---|---|
| id | UUID | primary key |
| case_file_id | UUID | FK → CaseFile |
| subject_name | string | |
| subject_type | string | public_official / corporation / organization |
| bioguide_id | string | required for Congress.gov vote records |
| state | string | |
| district | string | |
| office | string | senate / house / other |
| updated_by | string | handle |

### Investigator

Investigator handle and credibility score.

| Column | Type | Notes |
|---|---|---|
| id | UUID | primary key |
| handle | string | public identifier, unique |
| public_key | string | Ed25519 public key (for future per-investigator signing) |
| credibility_score | int | starts at 0, increments on actions |
| cases_opened | int | |
| entries_contributed | int | |
| joined_at | datetime | |
| is_anchor | bool | bootstrap reviewer status |

**Phase 6 — API keys:** `Investigator` includes **`hashed_api_key`** (SHA-256 hex of
the plaintext key) and **`api_key_created_at`**. Plaintext keys are returned **once**
from **`POST /api/v1/auth/keys`**. All **write** routes require **`Authorization: Bearer ...`**
and the body’s investigator handle must **match** the authenticated handle.

---

## THE ATOMIC INVESTIGATE FLOW

This is what happens when `POST /api/v1/cases/{id}/investigate` is called.
Everything in this flow is inside a single database transaction.
If anything fails after evidence inserts have started, the whole run rolls back.

```
1. Load CaseFile. Verify it exists.

2. Load SubjectProfile from case (or use InvestigateRequest subject fields).

3. Determine adapter list (always: FEC, USASpending, Indiana CF; optional address → IndyGIS + Marion):
   - If `request.address` is set: IndyGIS (address), then up to 3 Marion Assessor pins from parcel IDs.
   - FEC: committee mode if `fec_committee_id` else name mode (`request.subject_name`).
   - USASpending + Indiana CF always run.
   - **Congress.gov votes** run only when `case.subject_type == "public_official"` (bioguide from request or SubjectProfile).
   - **corporation** / **organization**: same financial/local stack; **no** Congress votes adapter in current code.
   - There is **no** CourtListener adapter in this repository.

4. For FEC adapter:
   - If fec_committee_id is set on the request → use committee mode
     (query by committee_id on schedule_a, bypasses name collision)
   - If not set → use name search (collision-prone)

5. Run all adapters (currently sequential, not concurrent):
   - Each adapter catches its own exceptions. Adapter failure ≠ pipeline failure.
   - Each adapter returns AdapterResponse (found=True/False, results=[...])

6. For each AdapterResult:
   a. Compute evidence_hash per `adapters/dedup.make_evidence_hash`:
      SHA-256 of `case_id:source_name:source_url:date_of_event:rounded_amount:matched_name`
   b. Check if evidence_hash already exists in EvidenceEntry for this case
   c. If duplicate: skip (idempotency)
   d. If new: create EvidenceEntry, sign it, set confidence based on collision_count
   e. Create SourceCheckLog entry (even if result is empty)
   f. If results=[] and found=True: create gap_documented EvidenceEntry

7. Load all EvidenceEntry rows for this case (including new ones just created)

8. Run detection engines against the full evidence set:
   - temporal_proximity.detect_proximity(entries, max_days=proximity_days)
   - contract_anomaly.detect_anomalies(entries)
   - contract_proximity.detect_contract_proximity(entries)

9. For each detected signal candidate:
   a. Compute `signal_identity_hash` via `signals/dedup.make_signal_identity_hash`:
      SHA-256 of pipe-joined normalized fields
      `(case_id, signal_type, evidence_id, donor_name, vote_id, contractor_name?, anomaly_subtype?)`
      — not a simple concat of actor names and dates.
   b. Look up existing signal with this hash for this case
   c. If no existing signal: INSERT Signal row
   d. If existing signal: UPDATE weight (if new weight > old weight),
      merge evidence_ids, increment repeat_count, update proximity_summary
   e. Insert SignalAuditLog entry ("created" or "weight_updated")

10. Re-sign the CaseFile (signed_hash now reflects new evidence)

11. db.commit() — single commit, everything above or nothing

12. Return investigation response with:
    - evidence_entries_created, signals_detected, signals array (`is_featured` bool per signal)
    - collision_warnings (any entries flagged for disambiguation; action points to disambiguate URL)
    - errors (adapter-level strings), cache_hits, sources_checked
```

If the transaction rolls back, the response returns a 500 with the error.
No partial state is committed.

---

## ADAPTER DETAILS

All adapters implement `BaseAdapter.search(query, query_type)` and return
`AdapterResponse`. All adapters catch their own exceptions and return
`found=False` on error. None raise.

### FEC (adapters/fec.py)

Queries FEC API for campaign donations.

Modes:
- Name search (`query_type="person"` or `"organization"`): hits `/candidates/search`
  then `/schedules/schedule_a` by candidate_id. Collision-prone for common names.
- Committee mode (`query_type="committee"`): hits `/schedules/schedule_a`
  with `committee_id` parameter directly. No name collision.

For Todd Young test: always use committee mode with `fec_committee_id: C00459255`.

Returns: `entry_type="financial_connection"`, `source_name="FEC"`.

**Important:** If source_name is stored as anything other than exactly "FEC",
the Category 3 assertion will fail. Check this if Category 3 fails.

### USASpending (adapters/usa_spending.py)

Queries USASpending.gov for federal contracts.

Returns: `entry_type="financial_connection"`, `source_name="USASpending"`.
Flags: no-bid (offers_received <= 1), threshold avoidance (value within 8%
of oversight thresholds at $25k, $150k, $750k, $10M).

### Congress Votes (adapters/congress_votes.py)

Queries Congress.gov for vote records by bioguide_id.

Returns: `entry_type="vote_record"`, `source_name="Congress.gov"`.

**Important:** This adapter tries multiple JSON keys to find the votes array
in the API response. If the Congress.gov API changes its response shape,
this adapter will silently return empty results and log a parse_warning.
Check for parse_warnings in Category 2 if it fails.

### IndyGIS (adapters/indy_gis.py)

Resolves street addresses to parcel records via Indianapolis GIS.

Returns: `entry_type="property_record"`, `source_name="IndyGIS"`.

### Marion Assessor (adapters/marion_assessor.py)

Queries Marion County property assessment database.

Returns: `entry_type="property_record"`, `source_name="Marion Assessor"`.

### Indiana CF (adapters/indiana_cf.py)

Indiana campaign finance — no public API exists.

Always returns: `found=True, results=[], is_absence=True`.
Creates a `gap_documented` entry explaining there is no automated access.

### Court records (CourtListener) — **not implemented**

No `adapters/courtlistener.py` (or equivalent) exists in this repository. Do not
document or assume court docket ingestion until an adapter is added and covered
by the verification matrix.

---

## DEDUPLICATION — TWO SEPARATE SYSTEMS

These solve different problems. Do not confuse them.

### Evidence deduplication (adapters/dedup.py)

**Problem:** Running investigate twice would create two copies of every
evidence entry if not deduplicated.

**Solution:** `evidence_hash` = SHA-256 of a colon-delimited payload built in
`make_evidence_hash` (case id, source name, source URL, event date, rounded amount,
lower-cased matched name). Gap and parse-warning synthetic rows use special
payload fragments so they dedupe safely. Before inserting, the pipeline skips if
this hash already exists for the case.

### Signal deduplication (signals/dedup.py)

**Problem:** Running investigate twice would create two copies of every
detected signal if not deduplicated.

**Solution:** `signal_identity_hash` = SHA-256 of normalized, pipe-separated
fields from `make_signal_identity_hash` (case id, signal type, evidence id,
donor name, vote id, optional contractor name, optional anomaly subtype). The
scorer supplies the correct tuple per signal kind. Before inserting, `upsert_signal`
merges into an existing row with the same hash for this case.
- If none: INSERT.
- If exists: UPDATE weight/evidence_ids/repeat_count, do not create a second row.

A UNIQUE constraint on `(case_file_id, signal_identity_hash)` enforces this
at the database level. A bug in the upsert logic will surface as a constraint
violation, not silent data corruption.

---

## THE SIGNING SYSTEM

Every evidence entry is signed. The case file is re-signed when evidence
is added. Snapshots are self-contained signed documents.

### Pattern (consistent across all Nikodemus Systems projects)

```python
import json
from hashlib import sha256
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# 1. Serialize with JSON Canonical Serialization (keys sorted, no whitespace)
def jcs_serialize(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))

# 2. SHA-256 hash the serialized payload
content_hash = sha256(jcs_serialize(payload).encode()).hexdigest()

# 3. Sign the hash digest as UTF-8
signature = private_key.sign(content_hash.encode())

# 4. Store as JSON
signed_hash = json.dumps({
    "content_hash": content_hash,
    "signature": base64.b64encode(signature).decode()
})
```

### Key storage

Keys are stored as base64-encoded PKCS#8 DER in environment variables.
Auto-generated on first boot. Written to `.env`. Never printed in logs.
Distribute `OPEN_CASE_PUBLIC_KEY` out-of-band (or read it from `.env` on a trusted
host). Consumers verify using `signing.py`, not a live HTTP key endpoint.

**Do not regenerate keys after the first boot.** All prior signatures
become unverifiable if the private key changes. If you need to rotate keys,
you must re-sign all existing signed records.

### Snapshot integrity

A case snapshot serializes the full case (metadata + all evidence entries +
snapshot metadata), signs the serialized JSON, and stores the payload embedded
in the signature record. A snapshot can be verified years later with no access
to the live database — the signed document is self-contained.

---

## CACHE BEHAVIOR

Adapter results are cached in SQLite with TTL (default: 4 hours).

Cache key: `SHA-256(adapter_name + ":" + query_string)`

Behavior:
- Cache hit: return stored AdapterResponse, do not call external API
- Cache miss: call API, store response, return result
- Source check logging still happens on cache hits (the search is still a search)
- Cache does NOT suppress gap_documented entries on empty results

Invalidation: explicit only. Running `flush_adapter_cache(adapter_name, query)`
clears a specific entry. This happens when an investigator disambiguates a
collision — the original query result may contain the wrong entity.

There is no automatic background expiry. TTL is enforced at read time
(check expires_at before returning cached result).

---

## SIGNAL WEIGHT CALCULATION

### Temporal Proximity

```
proximity_score:
  days_between ≤ 30  → 1.0
  days_between ≤ 90  → 0.6
  days_between ≤ 180 → 0.3
  days_between > 180 → 0.1

amount_score (log scale):
  $0        → 0.0
  $1,000    → 0.15
  $10,000   → 0.30
  $100,000  → 0.60
  $1,000,000 → 1.0

cooling_factor (by event age):
  ≤ 365 days ago  → 1.0
  ≤ 730 days ago  → 0.75
  > 730 days ago  → 0.50

repeat_multiplier (same actor in multiple signals):
  1 signal  → 1.0
  2 signals → 1.25
  3+ signals → 1.5

final_weight = (proximity_score * 0.6 + amount_score * 0.4)
               * cooling_factor * repeat_multiplier

cap: weight never exceeds 1.0
```

### is_featured

`is_featured = (weight >= 0.5)`

This threshold is arbitrary and based on no real data. It was chosen as a
reasonable starting point. If real investigations consistently produce signals
where the featured/non-featured cutoff feels wrong, change the comparisons in code.
The value **0.5** is not in a config file — it is hardcoded in:

- `routes/reporting.py` — each signal dict in `_collect_report_payload()` (JSON + HTML report)
- `routes/investigate.py` — investigate response `signals` list and `GET /api/v1/cases/{id}/signals`

The HTML report template (`templates/report.html`) does not recompute the threshold;
it filters on the boolean `is_featured` from the report payload.

---

## KNOWN LIMITS — HONEST ACCOUNTING

### Structural limits

**Single-writer SQLite.** SQLite allows one writer at a time. Concurrent
`POST /api/v1/cases/{id}/investigate` calls from different users will serialize.
Under any meaningful concurrent load, this will become a bottleneck.
Migration to Postgres is an environment variable change (SQLAlchemy handles it)
but requires switching to a Postgres deployment on Render.

**Synchronous investigation pipeline.** The investigate endpoint runs all
adapters and engines in the HTTP request lifecycle. With five adapters
and network calls to three external APIs, response times routinely exceed
15-30 seconds on a cold request. This will timeout in production under
any real load. BullMQ async queuing is the fix (planned Phase 6).

**Write authentication (Phase 6).** POST/PATCH write paths require a valid **Bearer**
API key; the handle in the JSON body must match the key holder. **GET remains public.**
Public deployment still assumes you trust read access to cases/reports and rate-limit
at the edge — same as pre-Phase-6 for read paths.

**No `og:image` on the receipt card.** Phase 5 removed the image tag and static
asset by design (text-only Open Graph). Crawlers that require an image for rich
previews will show text-only or degraded previews.

### Data limits

**Local data is patchy outside Indianapolis.** FEC, USASpending, and Congress.gov
are national and well-covered. Property records and local contracts depend on
each jurisdiction's data availability. Marion County is specifically integrated.
Most other municipalities are not.

**Entity resolution is not solved.** The fuzzy name matching collision rule
(confidence="unverified" when multiple entities match a name) prevents false
positives but does not resolve ambiguity. Human disambiguation is required
for any colliding match before the evidence can be used in a signal.

**Vote relevance is not filtered.** Temporal proximity detection finds any
financial event within the proximity window of any vote. A pharmaceutical
company donation before an agricultural policy vote will generate a signal.
Domain tagging for vote topics is not implemented.

**The committee-mode FEC approach solves the Todd Young test specifically.**
For other subjects without a known committee ID, name-based FEC search is
still collision-prone. This is a data quality problem, not a code problem.

### Verification limits

**The Todd Young test has never been confirmed to pass.** This is not a
temporary state — it is the current state as of Phase 5. The diagnostic
infrastructure to understand failures is complete, but the test has not
been run with real API keys and recorded output.

**The ten-box checklist is entirely manual.** The idempotency check, the
expose-400 enforcement, the signal history endpoint, and the subjects search
all require manual verification. None have automated coverage beyond the
four-category Todd Young test.

---

## MIGRATION MANAGEMENT

Alembic manages schema. Migrations run automatically on startup via
`init_db()` in `database.py`.

```bash
# Create a new migration after model changes
alembic revision --autogenerate -m "plain english description"

# Apply manually (also happens automatically on startup)
alembic upgrade head

# Check current revision
alembic current
```

**Do not manually edit the SQLite database.** Use Alembic migrations for
all schema changes. The migration history is the record of how the schema
evolved and is required for clean upgrades on deployed instances.

---

## REBUILD FROM ZERO CHECKLIST

If you are setting up a fresh instance from the repository:

```bash
# 1. Clone and enter
git clone https://github.com/Swixixle/Open-Case.git
cd Open-Case

# 2. Python environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env: add CONGRESS_API_KEY, optionally FEC_API_KEY
# Set BASE_URL if deploying publicly

# 4. Start the app
uvicorn main:app --reload
# First boot auto-generates Ed25519 keys and writes them to .env
# First boot runs Alembic migrations
# Startup warnings appear if CONGRESS_API_KEY or BASE_URL are missing

# 5. Run the smoke test (REQUIRED before doing anything else)
export CONGRESS_API_KEY=your_key
python -m scripts.test_todd_young
# Do not proceed if this does not exit 0

# 6. Create a test case
curl -X POST http://localhost:8000/cases \
  -H "Content-Type: application/json" \
  -d '{"slug":"test-todd-young","title":"Test: Todd Young","subject_name":"Todd Young","subject_type":"public_official","jurisdiction":"Indiana","created_by":"test_handle","summary":"Smoke case for Todd Young gate"}'

# 7. Walk the ten-box checklist (see CONTEXT.md / Phase 5 instructions doc, manual)
# Do not use the system in production until all ten boxes are confirmed
```

---

## DIRECTORY STRUCTURE

```
Open-Case/
├── main.py                  # FastAPI app: lifespan, ENV-aware BASE_URL, 6 routers (no /static)
├── models.py                # All SQLAlchemy models
├── database.py              # SQLite engine, Alembic upgrade on startup
├── auth.py                  # require_api_key, hash_key, generate_raw_key
├── signing.py               # Ed25519 keypair, JCS → SHA-256 → sign
├── scoring.py               # Credibility score increment logic
│
├── adapters/
│   ├── base.py              # AdapterResult, AdapterResponse, BaseAdapter
│   ├── cache.py             # SQLite cache with TTL
│   ├── dedup.py             # Evidence hash deduplication
│   ├── fec.py               # FEC (name mode + committee mode)
│   ├── usa_spending.py      # USASpending federal contracts
│   ├── congress_votes.py    # Congress.gov vote records by bioguide_id
│   ├── indy_gis.py          # Indianapolis GIS parcel resolution
│   ├── marion_assessor.py   # Marion County property records
│   └── indiana_cf.py        # Indiana campaign finance (documented absence)
│
├── engines/
│   ├── temporal_proximity.py  # detect_proximity(entries, max_days)
│   ├── contract_anomaly.py    # no-bid, threshold avoidance, repeat vendor
│   ├── contract_proximity.py  # donation → contract award timing
│   └── signal_scorer.py       # converts engine output to Signal rows
│
├── signals/
│   └── dedup.py             # signal_identity_hash + upsert logic
│
├── routes/
│   ├── auth.py              # POST /api/v1/auth/keys
│   ├── cases.py             # Case CRUD and status management
│   ├── evidence.py          # Evidence management
│   ├── evidence_disambig.py # Collision resolution
│   ├── investigate.py       # Investigation pipeline, confirm, dismiss
│   ├── reporting.py         # JSON report, HTML view, card, expose, history
│   └── subjects.py          # Subject profile, search, bioguide
│
├── templates/
│   └── report.html          # Jinja2 police-report format
│
├── scripts/
│   ├── test_todd_young.py       # Gate + writes PHASE5_CLOSURE.md on PASS
│   ├── test_idempotency.py     # 3× investigate count stability (needs uvicorn)
│   └── todd_young_assertions.py # Four-category assertions with diagnostics
│
├── tests/
│   └── fixtures/
│       └── todd_young.json      # proximity_days: 365, fec_committee_id: C00459255
│
└── alembic/
    └── versions/            # Migration files
```

---

## PHASE HISTORY SUMMARY

Every phase lists what shipped in code versus what was **proven on live external data**.
There is no “partial” here: if the Todd Young gate has not recorded PASS, the
answer is **No**.

| Phase | What was built | Verified (live Todd Young / APIs) |
| --- | --- | --- |
| 0 | Models, DB, keypair, base `/cases` CRUD | **No** |
| 1 | Adapters, detection engines, `/api/v1/.../investigate` | **No** |
| 2 | Signal dedup, atomic transaction, Todd Young test harness started | **No** |
| 3 | Signal integrity fields, weight copy, HTML report | **No** |
| 4 | Committee FEC mode, long-window fixture, confirm-before-expose, signing plumbing | **No** |
| 5 | Cat3 debug + type sets, `og:image` removed + static mount dropped, `check_config_warnings`, `is_featured` + report HTML split | **No — test unrun** |

**The core detection loop has never been confirmed to produce a signal on real data.**

---

## RULES THAT MUST NOT BE VIOLATED

These are architectural constraints, not style preferences. Future changes
that violate these rules will break the integrity guarantees of the system.
Each rule states **why it exists** — not only what is forbidden.

1. **Confirm-before-expose.** A signal must have `confirmed=True` before
   `PATCH /api/v1/signals/{id}/expose` succeeds. The HTTP 400 `UNCONFIRMED_SIGNAL`
   response is mandatory server-side enforcement. A receipt that can include
   unreviewed signals is legally and journalistically indefensible, and defeats
   the entire “human gate” design.

2. **The collision rule.** When an adapter reports `collision_count > 1`, the
   pipeline stores `confidence="unverified"` and `flagged_for_review=True`.
   The investigator resolves
   collisions via `PATCH /api/v1/evidence/{id}/disambiguate`. **Reason:**
   ambiguous name matches are the fastest path to false positives; the system
   must record uncertainty instead of silently picking a winner.

3. **Adapters never raise.** Every adapter catches its own exceptions and
   returns `AdapterResponse(found=False, error=str(e))`. Adapter failure must
   not abort the whole investigation transaction. **Reason:** a flaky vendor
   API must degrade to “no data / logged error”, not corrupt half-written evidence.

4. **Documented absence is real evidence.** When an adapter returns empty
   results (`found=True, results=[]`), the pipeline must create a
   `gap_documented` EvidenceEntry and a `SourceCheckLog` row. **Reason:**
   transparency requires proving a source was queried; omitting empty runs
   reads like concealment in a public-records context.

5. **The receipt format has no guilt field.** Signal descriptions describe
   what the record shows, not juridical conclusions. **Reason:** receipts are
   documentary artifacts; adding verdict language invites misuse and misreads
   the product’s ethical stance.

6. **Private individuals are out of scope.** Subject profiles are for public
   officials, corporations, and organizations only. **Reason:** the platform
   is not built for doxxing or neighborhood surveillance; adapters must not
   expand scope without legal/product review.

7. **Ed25519 keys are never regenerated after first use.** Rotating
   `OPEN_CASE_PRIVATE_KEY` invalidates every historical signature. **Reason:**
   cryptographic provenance is the audit trail; key churn breaks third-party
   verification of archived cases unless a full re-signing migration is run.

---

*OPEN CASE — Engineering Report*
*Nikodemus Systems / Swixixle*
*Last updated: Phase 5 complete*
*The system's core detection loop is unverified. Run the smoke test.*
