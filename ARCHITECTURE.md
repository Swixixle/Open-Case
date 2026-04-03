# Architecture

How Open Case is built and why the pieces are shaped the way they are.

---

## The Five Layers

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 5: GAME / REPUTATION / LEADERBOARD                   │
│  Credibility scores, rank progression, impact attribution   │
├─────────────────────────────────────────────────────────────┤
│  LAYER 4: SOCIAL / COLLABORATION                            │
│  Case threads, pickup/handoff, contributor tracking         │
├─────────────────────────────────────────────────────────────┤
│  LAYER 3: PHOTO TAP / PHYSICAL INGEST         [PLANNED]     │
│  Camera → address → public record → investigation thread    │
├─────────────────────────────────────────────────────────────┤
│  LAYER 2: INVESTIGATION TOOLS                               │
│  Adapters, detection engines, signing, reporting            │
├─────────────────────────────────────────────────────────────┤
│  LAYER 1: DATABASE AGGREGATION                              │
│  FEC, USASpending, Congress.gov, Marion, IndyGIS, Indiana CF │
└─────────────────────────────────────────────────────────────┘
```

Each layer is independently useful. Layers 1 and 2 form the core that
everything else depends on. Layers 4 and 5 are social features that sit
on top of a working detection layer.

---

## The Data Flow

```
HTTP Request → FastAPI Route
                    ↓
         InvestigateRequest validated
                    ↓
      SubjectProfile loaded (bioguide_id etc.)
                    ↓
    ┌──────────────────────────────────┐
    │   Adapter Layer (concurrent)     │
    │  ┌────────┐  ┌──────────────┐   │
    │  │  FEC   │  │ USASpending  │   │
    │  └────────┘  └──────────────┘   │
    │  ┌────────────────┐  ┌───────┐  │
    │  │ Congress Votes │  │IndyGIS│  │
    │  └────────────────┘  └───────┘  │
    └──────────────────────────────────┘
                    ↓
    For each result:
      - compute evidence_hash
      - check for duplicate (skip if exists)
      - sign entry (Ed25519)
      - store EvidenceEntry
      - store SourceCheckLog
      - store gap_documented if empty
                    ↓
    Engine Layer (all case evidence):
      detect_proximity(all_evidence)
      detect_contract_anomalies(all_evidence)
      detect_contract_proximity(all_evidence)
                    ↓
    For each signal candidate:
      - compute signal_identity_hash
      - upsert (create or update weight)
      - store SignalAuditLog entry
                    ↓
    Re-sign the CaseFile (updated evidence)
                    ↓
    Single db.commit() — atomic
    (rollback on any failure above)
                    ↓
    Return investigation response
```

---

## The Receipt Chain

Every piece of evidence is signed. The case file is re-signed when evidence
is added. Snapshots are fully self-contained signed documents.

**Evidence entry signing:**
```python
# JCS normalization ensures key ordering doesn't affect the signature
payload = {
    "title": entry.title,
    "body": entry.body,
    "source_url": entry.source_url,
    "entered_at": entry.entered_at
}
content_hash = sha256(jcs_serialize(payload))
signature = ed25519_private_key.sign(content_hash.encode())
signed_hash = json.dumps({
    "content_hash": content_hash,
    "signature": base64(signature)
})
```

**Snapshot signing:**
The snapshot serializes the full case (case metadata + all evidence + snapshot metadata),
signs the whole thing, and stores the payload embedded in the signature record.
This means a snapshot can be verified years later without access to the database —
the signed document is self-contained.

---

## Signal Deduplication

The most subtle but important data integrity guarantee.

Every time the investigation pipeline runs on a case, the detection engines
analyze all evidence and produce signal candidates. Without deduplication,
running the investigation five times would produce five copies of every signal.

The solution: a signal identity hash that uniquely identifies a correlation
regardless of when it was detected.

```python
def make_signal_identity_hash(
    case_file_id: str,
    signal_type: str,
    actor_a: str,
    actor_b: str,
    event_date_a: str,
    event_date_b: str
) -> str:
    payload = ":".join([
        case_file_id, signal_type,
        actor_a or "", actor_b or "",
        event_date_a or "", event_date_b or ""
    ])
    return sha256(payload.encode()).hexdigest()
```

The upsert behavior:
- If no signal with this hash exists for this case: INSERT
- If a signal exists: update weight if new weight is higher, merge evidence_ids,
  increment repeat_count, update proximity_summary

The unique constraint on `(case_file_id, signal_identity_hash)` enforces this
at the database level. A bug in the upsert logic surfaces as a constraint
violation rather than silent data corruption.

---

## The Adapter Interface

Every adapter implements `BaseAdapter.search()` and returns `AdapterResponse`.

```python
@dataclass
class AdapterResult:
    source_name: str
    source_url: str        # direct link to primary source
    entry_type: str        # financial_connection, vote_record, etc.
    title: str             # short label
    body: str              # plain English description
    date_of_event: Optional[str]
    amount: Optional[float]
    confidence: str        # "confirmed" | "probable" | "unverified"
    matched_name: Optional[str]
    collision_count: int   # > 1 means fuzzy match had multiple candidates
    collision_set: List[str]
    raw_data: dict         # full API response for audit

@dataclass
class AdapterResponse:
    source_name: str
    query: str
    results: List[AdapterResult]
    found: bool            # True = search ran; False = error
    error: Optional[str]
    result_hash: str       # SHA-256 of raw response
    parse_warning: Optional[str]  # set when API returned data but parser found nothing
```

The `found=True, results=[]` case is important: it means "we searched and
found nothing." This is different from `found=False` which means "the search
itself failed." The pipeline creates a `gap_documented` entry for empty results
and a regular error log for failed searches.

---

## The Cache Layer

Adapter responses are cached in SQLite with configurable TTL (default: 4 hours).

Cache key: `sha256(adapter_name + ":" + query_string)`

The cache serves two purposes:
1. Prevents exhausting API rate limits during active development
2. Makes repeated investigations fast

The cache does not suppress source check logging — every adapter run creates
a SourceCheckLog entry regardless of whether the result came from cache.

Cache invalidation happens explicitly when:
- An investigator disambiguates a collision (the original query result may
  be stale if the match was wrong)

---

## The Signing Infrastructure

Open Case uses the same Ed25519 signing pattern across all Nikodemus Systems
projects. The pattern:

1. Serialize the payload using JSON Canonical Serialization (JCS) — keys sorted,
   whitespace removed, deterministic output regardless of insertion order
2. SHA-256 hash the serialized bytes
3. Sign the hash digest (as UTF-8) with the Ed25519 private key
4. Store the signature as a JSON object: `{"content_hash": "...", "signature": "..."}`

Keys are stored as base64-encoded DER format in environment variables.
On first boot, keys are auto-generated and written to `.env`.

Verification: anyone with the public key can verify any receipt.
The public key is included in the API response at `GET /api/v1/verify/public-key`.

---

## Database Schema

### Core Models

```
CaseFile
├── id (UUID)
├── slug (human-readable URL segment)
├── title, subject_name, subject_type
├── jurisdiction, status
├── summary, pickup_note
├── created_by, created_at
├── signed_hash (Ed25519 signature of full file)
└── is_public, view_count

EvidenceEntry
├── id (UUID)
├── case_file_id (FK → CaseFile)
├── entry_type (financial_connection | vote_record | ...)
├── title, body, source_url, source_name
├── date_of_event, amount
├── entered_by, entered_at
├── confidence (confirmed | probable | unverified)
├── is_absence (True = documented gap)
├── flagged_for_review (collision warning)
├── adapter_name (which adapter produced this)
├── matched_name, raw_data_json
├── evidence_hash (SHA-256 of semantic payload, dedup key)
├── disambiguation_note, disambiguation_by, disambiguation_at
└── signed_hash

Signal
├── id (UUID)
├── case_file_id (FK → CaseFile)
├── signal_type (temporal_proximity | contract_anomaly | contract_proximity)
├── weight (0.0 – 1.0)
├── description (plain English)
├── weight_explanation (plain English, why this weight)
├── weight_breakdown (JSON, component scores)
├── evidence_ids (JSON list of UUIDs)
├── actor_a, actor_b
├── event_date_a, event_date_b, days_between
├── amount
├── signal_identity_hash (dedup key, unique per case)
├── repeat_count (how many runs found this same pattern)
├── proximity_summary (human-readable aggregation)
├── parse_warning (set when source returned data but parser found nothing)
├── confirmed, confirmed_by
├── dismissed, dismissed_by, dismissed_reason
├── exposure_state (internal | released)
└── routing_log (JSON, for future signal routing features)

SignalAuditLog
├── id (UUID)
├── signal_id (FK → Signal)
├── action (created | weight_updated | confirmed | dismissed | exposed)
├── performed_by, performed_at
├── old_weight, new_weight
└── note

SourceCheckLog
├── id (UUID)
├── case_file_id (FK → CaseFile)
├── source_name, query_string
├── result_count (0 = documented absence)
├── checked_at, checked_by
└── result_hash

AdapterCache
├── id (UUID)
├── adapter_name, query_hash (dedup key)
├── response_json (serialized AdapterResponse)
├── query_string, created_at, expires_at, ttl_hours

SubjectProfile
├── id (UUID)
├── case_file_id (FK → CaseFile)
├── subject_name, subject_type
├── bioguide_id (for Congress.gov voter records)
├── state, district, office
└── updated_by

CaseContributor
├── id (UUID)
├── case_file_id (FK → CaseFile)
├── investigator_handle
├── role (originator | field | analyst | reviewer | pickup)
├── joined_at, last_active_at
└── entry_count

CaseSnapshot
├── id (UUID)
├── case_file_id (FK → CaseFile)
├── snapshot_number
├── taken_at, taken_by
├── entry_count
├── signed_hash (self-contained signature with embedded payload)
├── share_url
└── label

Investigator
├── id (UUID)
├── handle (public identifier)
├── public_key (Ed25519)
├── credibility_score
├── cases_opened, entries_contributed
├── joined_at
└── is_anchor (bootstrap reviewer status)
```

---

## The Detection Engines

### Temporal Proximity

Input: all EvidenceEntry rows for a case
Operation: find financial events (FEC donations, financial_connection type)
that precede decision events (vote_record type) within the proximity window

Weight calculation:
```
proximity_score = 1.0 if days ≤ 30 else 0.6 if days ≤ 90 else 0.3
amount_score = log scale, $10k → 0.3, $1M+ → 1.0
cooling = 1.0 if event_age ≤ 365 days else 0.75 if ≤ 730 else 0.5
repeat_multiplier = 1.5 if same actor in 3+ signals else 1.25 if 2 else 1.0
final = (proximity * 0.6 + amount * 0.4) * cooling * repeat_multiplier
```

### Contract Anomaly

Input: EvidenceEntry rows with source_name in USASpending adapters
Operation: check each contract for structural problems

Patterns detected:
- No-bid: Number of offers received ≤ 1
- Threshold avoidance: Value within 8% below a procurement threshold
- Repeat vendor: Same vendor in 3+ contracts in the case evidence
- Value balloon: [planned] modification that multiplies original value significantly

### Contract Proximity

Input: all EvidenceEntry rows
Operation: find FEC donations that precede USASpending contract awards
involving the same or related actors within the proximity window

This engine works for any subject type (local official, corporation, etc.)
because it uses contract award dates rather than vote dates for the decision event.

### Pattern engine (read-side)

The pattern engine scans the global **donor fingerprint** ledger joined to **signals**
whose `weight_breakdown.kind` is `donor_cluster`. Rules emit **`PatternAlert`**
objects (e.g. for the report UI and `GET /api/v1/patterns`); they do not mutate
signal weights or case state.

**`COMMITTEE_SWEEP_V1`** — one donor linked (via bioguide committee assignments)
to appearances for **≥3** distinct officials who share a **Senate committee** name,
with dated donations inside a **14-day** span.

**`FINGERPRINT_BLOOM_V1`** — one donor appears in **≥4** investigations (cases)
with relevance **≥ 0.3** on the linked signals.

**`SOFT_BUNDLE_V1`** — **≥3** distinct donors (**`normalized_donor_key` /
canonical id**) with `donor_cluster` signals sharing the same
**`committee_label`** (recipient committee) in the breakdown, donation dates
(from `event_date_a` / `exemplar_financial_date` in the breakdown) within a
**7-day** span, and **aggregate** `total_amount` from those rows **≥ $1,000**.
Amount diversification `(1 − HHI)` over per-donor shares is included in the alert
payload for reporting; employer concentration is reserved for when employer
fields exist on clustered evidence.

---

## Known Limitations

**Entity resolution is hard.** "Smith Holdings LLC" on an FEC filing and
"Smith Holdings, LLC" on a USASpending contract may or may not be the same entity.
The collision detection system flags these ambiguities but cannot resolve them.
Human disambiguation is required.

**Local data is patchy.** The national federal databases (FEC, USASpending,
Congress.gov) are well-covered. State and local data varies enormously by
jurisdiction. The Indianapolis V0 scope has good local data. Most other
cities do not yet.

**Vote relevance is not filtered.** The temporal proximity engine finds
donations that precede votes without checking whether the vote topic is
related to the donor's industry. A pharmaceutical company donation 20 days
before an agricultural vote will still generate a signal, albeit with lower
contextual weight. Domain tagging for votes is planned.

**The system is single-user.** Authentication and multi-user collaboration
are planned but not yet built. The credibility score system exists but is
not enforced by any access control.

**Investigation is synchronous.** Running all adapters in a single HTTP
request works for development but will time out under load. BullMQ async
queuing is planned.

---

## Deployment

Open Case is deployed on Render. The deployment pattern follows the same
approach as other Nikodemus Systems projects.

**Environment variables required for production:**
```
CONGRESS_API_KEY=...
FEC_API_KEY=...
BASE_URL=https://your-render-url.onrender.com
OPEN_CASE_PRIVATE_KEY=...  (auto-generated on first boot)
OPEN_CASE_PUBLIC_KEY=...   (auto-generated on first boot)
```

**Database:** SQLite (file-based, Render persistent disk).
Alembic migrations run automatically on startup via `database.init_db()`.

**Note on multi-instance deployments:** Running migrations in the startup
lifecycle creates a race condition when multiple instances restart simultaneously.
For single-instance deployments (current target), this is acceptable.
For multi-instance production, migrations should be run as a pre-deploy step.

---

*Questions about the architecture? Open a Discussion on GitHub.*
