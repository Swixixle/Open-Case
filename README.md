# OPEN CASE

**Civic investigation infrastructure. Built like a game. Backed by public records.**

```
Point at a building. See who owns it. See who paid them. See what they voted for.
Build a signed receipt. Share it. Pick up where someone else left off.
```

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com)
[![Status: Active Development](https://img.shields.io/badge/status-active%20development-orange.svg)]()

---

## What This Is

Open Case is a civic investigation platform where people research public officials
and public money using real government records, then build documented, signed,
shareable case files from what they find.

It is not a fact-checking website. It is not a watchdog organization.
It is infrastructure — the tools and structure that make distributed,
verifiable, citizen-led accountability possible.

The interface looks like a game. The receipts are real.

---

## The Problem

Corruption and legal inconsistency aren't random. They follow patterns.
The data to find those patterns exists — FEC filings, USASpending contracts,
Senate LDA lobbying disclosures, congressional vote records, property databases,
court records. Almost all of it is legally public.

The problem is nobody has connected it in a way that a motivated, non-expert
person can actually use.

The other problem is social. Outrage travels fast. Evidence travels slow.
A screenshot of a tweet moves at the speed of emotion. A documented,
sourced, signed receipt chain moves at the speed of trust.

We built the infrastructure for the receipt.

---

## The Philosophy

**Receipts, not verdicts.**

Open Case never asserts guilt. It never reaches conclusions. Every case file
documents what the public record shows — donations received, votes cast,
contracts awarded, lobbying contacts logged — and leaves inference to
investigators, journalists, prosecutors, and courts.

The receipt format has no guilt field. It has a "what the record shows" field.

This is not a legal disclaimer. It is the design.

---

## How It Works

### The Investigation Loop

```
Subject selected (public official, corporation, organization)
        ↓
Adapters run automatically:
  FEC campaign donations
  USASpending federal contracts
  Congress.gov vote records
  Marion County property records
  Indiana campaign finance
        ↓
Detection engines analyze the evidence:
  Temporal proximity (donation → vote timing)
  Contract anomalies (no-bid, threshold avoidance, repeat vendors)
  Disclosure gaps (what's missing is evidence too)
        ↓
Signals ranked by weight (not by outrage)
        ↓
Human investigator reviews, confirms, or dismisses each signal
        ↓
Signed receipt generated — Ed25519 cryptographic signature,
timestamped, source-linked, tamper-evident
        ↓
Receipt card shared — the artifact that travels
        ↓
Another investigator picks up where you left off
```

### The Case File Format

Every investigation produces a Case File — a living document that:

- Looks like a police report
- Works like a wiki (anyone can add evidence, everyone gets credited)
- Is append-only (nothing gets deleted, everything has a chain of custody)
- Can be picked up by another investigator when you get stuck

The Case File has sections:
- **Incident summary** — what is being investigated and why
- **Subject record** — everything public record shows about the subject
- **Evidence log** — each piece of evidence, dated and attributed
- **Timeline** — the same evidence visualized chronologically
- **Financial connections** — the money trail
- **Signals** — algorithmically detected patterns, human-confirmed or dismissed
- **Gaps documented** — what we looked for and didn't find (this matters)
- **Chain of custody** — who worked on this, when, what they added

---

## Current Capabilities (V0)

Open Case is in active development. Here is what is actually built and working,
versus what is planned.

### ✅ Built and Working

**Data Pipeline**
- FEC campaign finance adapter (donor → recipient → date → amount)
- USASpending federal contracts adapter
- Congress.gov vote records adapter (bioguide_id → vote history)
- Marion County Assessor property records (Indianapolis V0 scope)
- IndyGIS parcel resolution (address → property record)
- Indiana campaign finance (documented absence — no public API)

**Detection Engines**
- Temporal proximity engine — finds donations that precede votes/decisions
- Contract anomaly engine — flags no-bid awards, threshold avoidance, repeat vendors
- Contract proximity engine — finds donations that precede contract awards
- Signal cooling — older events weighted less than recent ones

**Receipt Infrastructure**
- Ed25519 cryptographic signing (keypair auto-generated on first boot)
- Signal identity hash deduplication (running investigate twice doesn't multiply rows)
- Evidence hash deduplication (same evidence never stored twice)
- Case snapshot system — point-in-time signed versions of the full case file
- Adapter cache with TTL (prevents API exhaustion)
- Atomic investigation transactions (all-or-nothing, rollback on failure)

**Case File System**
- Case creation, evidence logging, source check logging
- Documented absence (what we looked for and didn't find)
- Investigation pickup/handoff (mark as needs_pickup, leave a note)
- Contributor tracking (who added what)
- Signal confirmation and dismissal with audit log (with audit trail)
- Signal exposure to public receipt (confirm-before-expose enforced)

**Reporting**
- JSON report endpoint — full structured case file
- HTML report view — police report format (Jinja2 template)
- Receipt card — shareable HTML with OG tags for social preview
- Signal weight explanations in plain English
- Evidence summaries inline on each signal (no UUID-hunting)
- Configurable temporal proximity window (`proximity_days` on investigate)
- Signal history endpoint (`GET /api/v1/signals/{id}/history`)

**Scoring**
- Investigator credibility score
- Increments on: case creation, evidence added, snapshot generated,
  signal confirmed, collision resolved, case pickup

### 🔄 In Progress

- Closing the **Todd Young gate** on live FEC + Congress.gov data (`python -m scripts.test_todd_young`; see diagnostic categories in output)
- Indianapolis / local contracts layer (planned data integration)

### 📋 Planned

- CourtListener (or similar) federal court records adapter
- Social layer (multi-investigator collaboration, feed algorithm)
- Photo Tap (point camera at building → property record → investigation thread)
- Forward to Authority (formatted packet → IG offices, DOJ, journalists)
- Basic authentication (before public deployment)
- Indiana state legislature adapter
- Network/graph analysis engine
- Statement vs. record divergence (what officials say vs. what records show)
- Mobile-responsive UI

---

## Architecture

```
Open-Case/
├── main.py                  # FastAPI app, lifespan, router registration
├── models.py                # SQLAlchemy models (all six core + audit)
├── database.py              # SQLite connection, Alembic upgrade on startup
├── signing.py               # Ed25519 keypair, JCS → SHA-256 → sign
├── scoring.py               # Credibility score increments
│
├── adapters/                # Data source connectors
│   ├── base.py              # AdapterResult / AdapterResponse / BaseAdapter
│   ├── cache.py             # SQLite cache with TTL
│   ├── dedup.py             # Evidence hash deduplication
│   ├── fec.py               # FEC campaign finance
│   ├── usa_spending.py      # USASpending federal contracts
│   ├── congress_votes.py    # Congress.gov vote records
│   ├── indy_gis.py          # IndyGIS parcel resolution
│   ├── marion_assessor.py   # Marion County property records
│   └── indiana_cf.py        # Indiana campaign finance (documented absence)
│
├── engines/                 # Pattern detection
│   ├── temporal_proximity.py  # Donation → vote/decision timing
│   ├── contract_anomaly.py    # No-bid, threshold avoidance, repeat vendors
│   ├── contract_proximity.py  # Donation → contract award timing
│   └── signal_scorer.py       # Combines engine outputs into Signal rows
│
├── signals/
│   └── dedup.py             # Signal identity hash + upsert logic
│
├── routes/
│   ├── cases.py             # Case CRUD, status updates, pickup/handoff
│   ├── evidence.py          # Evidence entry management
│   ├── evidence_disambig.py # Collision resolution (fuzzy match confirmation)
│   ├── investigate.py       # Main investigation pipeline endpoint
│   ├── reporting.py         # JSON/HTML report, receipt card, expose, signal history
│   └── subjects.py          # Bioguide lookup, Indiana + API subject search
│
├── templates/
│   └── report.html          # Jinja2 police-report-format HTML
│
├── scripts/
│   ├── test_todd_young.py   # End-to-end gate + four-category diagnostics
│   └── todd_young_assertions.py
│
├── tests/
│   └── fixtures/
│       └── todd_young.json  # Test configuration and pass conditions
│
└── alembic/                 # Database migrations
    └── versions/
```

### Key Data Models

**CaseFile** — The top-level investigation object. Has a subject, a status,
a pickup note for handoffs, and a signed hash that updates when evidence is added.

**EvidenceEntry** — Every piece of evidence. Signed individually. Includes
source URL, confidence level, date of the underlying event, whether it's a
documented absence, and a fuzzy-match flag for items needing human disambiguation.

**Signal** — A detected pattern. Has a weight (0.0–1.0), a type
(temporal_proximity, contract_anomaly, contract_proximity), a plain-English
explanation, a breakdown of how the weight was calculated, and an identity hash
for deduplication. Can be confirmed, dismissed, or exposed to the public receipt.

**SignalAuditLog** — Every change to a signal. Weight upgrades, confirmations,
dismissals, exposures. The history of how evidence accumulated.

**SourceCheckLog** — Every adapter query, including ones that returned nothing.
Documented absence is meaningful signal — it goes in the record.

**AdapterCache** — Cached adapter responses with TTL. Prevents exhausting
API rate limits during active development.

---

## Getting Started

### Prerequisites

- Python 3.11+
- API keys (free):
  - `CONGRESS_API_KEY` from [api.data.gov/signup](https://api.data.gov/signup/)
  - `FEC_API_KEY` from [api.open.fec.gov](https://api.open.fec.gov/) (optional, defaults to DEMO_KEY)

### Setup

```bash
git clone https://github.com/Swixixle/Open-Case.git
cd Open-Case

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
# Edit .env and add your API keys
```

Your `.env` should contain:
```
CONGRESS_API_KEY=your_key_here
FEC_API_KEY=DEMO_KEY          # optional, 1000 req/day on DEMO_KEY
BASE_URL=http://localhost:8000  # update to your public URL for deployment
OPEN_CASE_PRIVATE_KEY=         # auto-generated on first boot
OPEN_CASE_PUBLIC_KEY=          # auto-generated on first boot
```

### Run

```bash
uvicorn main:app --reload
```

The app starts at `http://localhost:8000`. Interactive API docs at
`http://localhost:8000/docs`.

On first boot:
- Database schema is created via Alembic migrations
- Ed25519 keypair is generated and written to `.env`
- No manual setup required

### Verify the Detection Loop Works

```bash
python -m scripts.test_todd_young
```

This creates a test case for Todd Young (Indiana senator, bioguide Y000064),
runs the full investigation pipeline, and confirms that at least one
temporal_proximity signal is detected from real FEC donation + Congress vote data.

If the test passes (exit 0), the core detection loop is working.
If it fails, the output includes a structured diagnostic showing exactly which
step failed.

---

## The Investigation Flow (Step by Step)

### 1. Create a Case

```bash
curl -X POST http://localhost:8000/api/v1/cases \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Investigation: Todd Young / Defense Contractor Donations",
    "subject_name": "Todd Young",
    "subject_type": "public_official",
    "jurisdiction": "Indiana, USA",
    "created_by": "your_handle",
    "summary": "Investigating proximity between defense industry donations and Armed Services Committee votes."
  }'
```

### 2. Set the Subject Profile

```bash
# Find the bioguide_id for your subject
curl "http://localhost:8000/api/v1/subjects/search?name=Todd+Young&state=IN"

# Set it on the case
curl -X POST http://localhost:8000/api/v1/cases/{case_id}/subject \
  -H "Content-Type: application/json" \
  -d '{
    "subject_name": "Todd Young",
    "subject_type": "public_official",
    "bioguide_id": "Y000064",
    "state": "IN",
    "office": "senate",
    "investigator_handle": "your_handle"
  }'
```

### 3. Run the Investigation

```bash
curl -X POST http://localhost:8000/api/v1/cases/{case_id}/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "subject_name": "Todd Young",
    "investigator_handle": "your_handle",
    "bioguide_id": "Y000064",
    "proximity_days": 90
  }'
```

This runs all adapters, detects patterns, creates signals, and returns
a ranked list of findings with plain-English explanations.

### 4. Review Signals

```bash
curl http://localhost:8000/api/v1/cases/{case_id}/signals
```

Each signal includes:
- `type` — what kind of pattern was detected
- `weight` — 0.0 to 1.0 strength score
- `description` — plain English description of the finding
- `explanation` — why the weight is what it is
- `supporting_evidence` — the specific records that support this signal
- `days_between` — days between the financial event and the decision event

### 5. Confirm or Dismiss

```bash
# Confirm a signal after reviewing the evidence
curl -X PATCH http://localhost:8000/api/v1/signals/{signal_id}/confirm \
  -H "Content-Type: application/json" \
  -d '{"investigator_handle": "your_handle"}'

# Dismiss with a reason
curl -X PATCH http://localhost:8000/api/v1/signals/{signal_id}/dismiss \
  -H "Content-Type: application/json" \
  -d '{
    "investigator_handle": "your_handle",
    "reason": "Donation is to general party committee, not directly to subject"
  }'
```

### 6. Read the Report

```bash
# Human-readable HTML
open http://localhost:8000/api/v1/cases/{case_id}/report/view

# Full JSON
curl http://localhost:8000/api/v1/cases/{case_id}/report
```

### 7. Generate the Shareable Receipt

```bash
# Expose a confirmed signal to the public receipt
curl -X PATCH http://localhost:8000/api/v1/signals/{signal_id}/expose \
  -H "Content-Type: application/json" \
  -d '{"investigator_handle": "your_handle"}'

# The receipt card (designed to be shared)
open http://localhost:8000/api/v1/cases/{case_id}/report/card
```

The receipt card generates correct Open Graph meta tags. When you paste
the link into Slack, Signal, or Twitter, it previews with the key finding.

### 8. Hand Off a Stalled Case

```bash
# Mark as needing pickup with a note about where you got stuck
curl -X PATCH http://localhost:8000/api/v1/cases/{case_id}/status \
  -H "Content-Type: application/json" \
  -d '{
    "status": "needs_pickup",
    "pickup_note": "Got the FEC donations and contract anomalies. Need someone who knows Indiana state procurement to check the local contract database. Suspicious clustering around Q4 2022.",
    "investigator_handle": "your_handle"
  }'

# Find cases waiting for pickup
curl "http://localhost:8000/api/v1/cases/browse/available?status=needs_pickup&jurisdiction=Indiana"

# Pick one up
curl -X POST http://localhost:8000/api/v1/cases/{case_id}/pickup \
  -H "Content-Type: application/json" \
  -d '{"investigator_handle": "your_handle"}'
```

---

## Signal Detection: How It Works

### Temporal Proximity Engine

The most powerful detection engine. Given a set of dated evidence entries,
it finds financial events that happened close in time to decision events.

The pattern: a donor gives money before an official votes in a way that
benefits the donor.

```
FEC donation: $45,000 from Pharma Corp → Sen. Young   [Jan 3, 2023]
Congress vote: Young votes for drug pricing bill       [Jan 21, 2023]
Days between: 18
Weight: 0.74 (high proximity, medium amount)
```

Weight factors:
- **Proximity score**: ≤30 days = 1.0, ≤90 days = 0.6, ≤180 days = 0.3
- **Amount score**: log scale, $10k = 0.3, $100k = 0.6, $1M+ = 1.0
- **Cooling factor**: events from 2+ years ago are weighted down
- **Repeat multiplier**: same actor appearing in 3+ signals = 1.5x

### Contract Anomaly Engine

Finds structural problems in government contracting data.

Detected patterns:
- **No-bid contracts** — awarded without competitive bidding
- **Threshold avoidance** — contract value suspiciously close to oversight thresholds ($25k, $150k, $750k, $10M)
- **Repeat vendor concentration** — same company winning >80% of awards in a category
- **Value balloon modifications** — contract awarded at $200k, modified to $2M

### Contract Proximity Engine

Like temporal proximity but for contracts instead of votes. Works for
any subject type — local officials, corporations, organizations — because
it uses USASpending (not vote records) for the decision-side events.

```
FEC donation: $25,000 from Construction LLC → County Commissioner  [March 2022]
USASpending: Construction LLC awarded $1.2M contract               [June 2022]
Days between: 94
Weight: 0.61 (medium proximity, large contract)
```

---

## The Fuzzy Match Problem (And How We Solve It)

Every adapter that searches by name faces the same problem:
"John Smith the donor" is not always "John Smith the contractor."

Open Case enforces a collision rule: if a name search returns multiple
possible matches, every result gets flagged as `unverified` with a
`collision_warning` in the investigation response. The investigator must
manually confirm which entity is which before the signal can be confirmed
or exposed.

This is not optional. A false positive link is a defamation bug.
The system is structurally incapable of creating verified signals
from ambiguous matches.

```json
{
  "collision_warnings": [
    {
      "entry_id": "...",
      "title": "FEC Donation: $45,000 from Smith Holdings LLC",
      "source": "FEC",
      "note": "Multiple entities matched this name — human confirmation required",
      "action": "PATCH /api/v1/evidence/{id}/disambiguate"
    }
  ]
}
```

---

## The Receipt Integrity Guarantee

Receipts are signed at multiple levels:
1. Each evidence entry is signed when created
2. The full case file is re-signed when new evidence is added
3. Case snapshots are signed point-in-time versions with the full payload embedded

What this means in practice:
- A receipt generated today can be verified in five years
- If any evidence was altered, the signature fails
- If the database is gone, the snapshot is self-contained
- Nobody can claim "we never found that" because the source check log
  documents what was searched, including searches that returned nothing

The signing pattern follows the Nikodemus Systems standard:
JCS normalization → SHA-256 hex → Ed25519 sign digest as UTF-8.
This is the same signing implementation used across DEBRIEF, PUBLIC EYE,
SPLIT, and RACK.

---

## V0 Scope: Indianapolis

The first version of Open Case targets Indianapolis and Indiana federal officials.
This is not arbitrary — it is a deliberate constraint that lets us prove the
concept with real, accessible data before expanding nationally.

**Federal layer (works now):**
- All Indiana federal officials accessible via Congress.gov
- FEC donation records for Indiana officials and Indiana entities
- USASpending contracts involving Indiana vendors and agencies

**Local layer (Indianapolis):**
- Marion County Assessor property records via indy.gov
- IndyGIS parcel resolution (address → parcel → owner)
- Indianapolis public contracts (planned)
- Indiana campaign finance (documented absence — no API, manual lookup required)

**Why Indianapolis is the right V0 target:**
Marion County property records are digitized and searchable.
Indianapolis has a public contracts database.
Indiana's federal officials have rich FEC and Congress.gov records.
The city has real local accountability issues worth documenting.

---

## Contributing

Open Case is in active development by one person working third shift,
building with AI-assisted development tools. Contributions are welcome
and the architecture is designed for it.

### Ways to Contribute

**Data coverage** — The local data layer is the hardest problem. If you know
how to access public records in your jurisdiction — especially municipal
contracts, local campaign finance, property records, and zoning databases
that don't have APIs — open an issue describing what's available and how to
reach it. You don't need to write code. A description of the data source,
the URL, and how to query it is enough to build an adapter.

**New adapters** — See `adapters/base.py` for the interface every adapter
implements. The pattern is: query takes a string, returns AdapterResponse
with a list of AdapterResult objects, and always documents absence when
nothing is found. Add your adapter to `adapters/` and register it in
`routes/investigate.py`. There are no gotchas if you follow the interface.

**Detection logic** — The engines in `engines/` are where the interesting
work happens. If you know of a pattern in public records that indicates
potential misconduct — a specific structure in FEC filings, a contracting
pattern, a disclosure anomaly — that's worth a discussion or a PR.

**Bug reports** — The most useful bug reports include: what you searched,
what you expected, what you got, and the diagnostic JSON output from the
investigation response.

**Jurisdiction documentation** — Open a PR that adds your jurisdiction's
public record sources to `docs/jurisdictions/`. Even if there's no code yet,
documenting what exists is valuable.

### Development Setup

```bash
git clone https://github.com/Swixixle/Open-Case.git
cd Open-Case
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add API keys to .env
uvicorn main:app --reload
```

Run the Todd Young test to confirm your environment is working:
```bash
python -m scripts.test_todd_young
```

### Code Style

- Commit messages in plain English, not conventional commits
- No authentication bypass in any adapter (if the source requires a key, document it)
- Every adapter must handle empty results as a documented absence, not an error
- Every adapter must handle exceptions internally and return found=False, never raise
- The fuzzy match collision rule is non-negotiable

### What We Don't Accept

- Adapters that scrape authenticated endpoints or require credentials
  that aren't freely available
- Any change that allows unconfirmed signals to appear in public receipts
- Adapters for non-public data sources
- Detection engines that reach conclusions — they surface patterns,
  humans confirm or dismiss

---

## Roadmap

### Phase 3 (Active — closing now)
- ✅ Temporal proximity engine
- ✅ Contract anomaly and contract proximity engines
- ✅ Signal identity hash deduplication
- ✅ Atomic investigation transactions
- ✅ HTML report view (police report format)
- ✅ Receipt card with OG tags
- 🔄 End-to-end proof on real Todd Young data
- 🔄 Confirm-before-expose hard enforcement

### Phase 4 (Next)
- Dynamic proximity window (configurable per investigation)
- Signal history endpoint from audit log
- Subjects search with Indiana officials hardcoded + Congress.gov fallback
- BASE_URL configuration for public deployments

### Phase 5
- Basic authentication (before public URL deployment)
- BullMQ async queuing for investigation pipeline
- Contract proximity real-data validation
- Forward to Authority — formatted submission packets for IG offices, DOJ

### Phase 6
- Photo Tap — point camera at physical location → property record → investigation
- Indiana state legislature adapter
- Indianapolis local contracts adapter

### Phase 7
- Social layer — multi-investigator threads, feed algorithm, pickup discovery
- Investigator profiles and credibility display

### Phase 8
- Mobile-responsive UI
- Network/graph analysis engine (entity clustering, shared addresses)
- Statement vs. record divergence engine (what officials say vs. what records show)

### Long Term
- National coverage expansion (state-by-state data layer)
- Global layer (ICIJ, OpenCorporates, OpenSanctions)
- Forward to Authority expanded (journalist routing, Congressional oversight staff)

---

## Technical Design Decisions

### Why SQLite?

SQLite is the right choice for a single-developer project in active
development. The SQLAlchemy layer means switching to Postgres is one
environment variable change. Alembic handles migrations for both.
When the project grows to a scale where SQLite is a bottleneck,
that's a good problem to have and the switch is straightforward.

### Why Ed25519 over RSA?

Fast. Small signatures. Well-implemented in Python's cryptography library.
The signing pattern (JCS → SHA-256 → Ed25519) is consistent across all
Nikodemus Systems projects, which means the verification code is shared
and battle-tested.

### Why FastAPI?

Async HTTP handling matters when you're running five adapter queries in
sequence against external APIs. The automatic OpenAPI docs at `/docs` make
the API self-documenting, which matters for a project where the receipts
are meant to be reproducible by anyone.

### Why the Adapter Interface?

Every adapter implements the same interface — same input shape, same output
shape, same empty-result handling. This means:
- Adding a new data source never requires touching the investigation pipeline
- The investigation response is consistent regardless of which adapters ran
- Documented absence (found=True, results=[]) is first-class in the API

### Why Detect Patterns Before Human Review?

The temporal proximity and contract anomaly engines find things that are
genuinely hard to see without algorithmic help. A human reading a list of
200 FEC donations and 150 congressional votes will miss the 18-day
proximity that a machine catches in milliseconds. The human's job is
confirming or dismissing what the machine surfaces, not finding needles
in haystacks.

---

## The Nikodemus Systems Context

Open Case is part of a broader project exploring cryptographically verified,
citizen-accessible accountability infrastructure.

**Related projects:**

- **PUBLIC EYE** — news framing analysis platform with signed receipts.
  The signing pipeline Open Case uses was developed and battle-tested here.

- **SPLIT** — actor accountability engine tracking gaps between stated values
  and documented records. Open Case's subject profile model builds on SPLIT's
  actor model.

- **RACK** — local-first epistemic receipt engine. The regulator dossier
  builder in RACK is the foundation for Open Case's planned Forward to
  Authority function.

- **DEBRIEF** — codebase analysis with plain-language signed briefs.
  Open Case's async document processing pipeline adapts DEBRIEF's BullMQ/Redis pattern.

These projects share a unified thesis: AI-influenced decisions and public
claims should be cryptographically verifiable and independently auditable.
Open Case applies that thesis to civic accountability specifically.

---

## License

MIT License. See [LICENSE](LICENSE).

The data that Open Case surfaces is public record. The platform's value
is in connecting and signing it — the connection and the signature are ours,
the underlying data belongs to everyone.

---

## A Note on What This Is Not

Open Case does not investigate private citizens.
Open Case does not reach conclusions about guilt.
Open Case does not publish accusations.
Open Case does not aggregate data that is not legally public.
Open Case does not persist personally identifying information about
investigators beyond what they choose to share.

The platform is pointed at institutions, public officials in their public
roles, and the money that moves between them. That is its entire scope.

When a receipt chain is strong enough that its implications seem obvious,
that's for journalists, prosecutors, and courts to act on. The receipt
documents what the record shows. The inference belongs to the reader.

---

*Built by [Nikodemus Systems](https://github.com/Swixixle)*
*Initiated in Indianapolis, Indiana*
*"Receipts, not verdicts."*
