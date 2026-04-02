# PHASE 11 — VISION DOCUMENT
**Date:** April 2, 2026  
**Status:** Planned — do not implement until PatternAlert fires on real data  
**Author:** Alex Maksimovich / Claude (Anthropic)

---

## The core reframe

The receipt is not the product. It is the notary stamp on the back of something
else. Infrastructure, not output. It should be invisible when working and only
noticed when it breaks.

The product is the pattern that emerges from many investigators, many subjects,
many receipts, correlated by entity and committee and time.

The tools are how you earn the right to see it clearly.

---

## Human-bound receipts (Phase 11A)

### What exists today

Platform Ed25519 key signs case and evidence payloads. Receipt proves "this is
what the service produced at this time." The `Investigator` model has a
`public_key` column that is not yet wired into receipt sealing.

### What needs to change

Separate investigator signing keypairs from API auth keys. API keys are secrets
(hashed). Signing keys are public (ed25519: format, stored plaintext). Wire the
investigator public key into a second signature on every sealed receipt.

### Implementation order (when ready)

1. Key model — add `identity_method` and `identity_commitment` columns to
   Investigator. Migration.
2. `GET /api/v1/investigators/{handle}/pubkey` — returns public key
3. Receipt payload — define `open-case-full-4` with `investigator` block
   and dual-signature fields
4. `POST /api/v1/cases/{id}/sign` — investigator signs content_hash,
   server verifies against registered pubkey, stores final receipt
5. `POST /api/v1/receipts/verify` — pure verify, no DB required if
   payload is self-contained

### Three identity levels

**Level 1 — Self-attested (ship first)**  
Handle + public key + self-declaration. No external verification. Costs nothing.
Works immediately. Investigator cannot deny signed receipts.

**Level 2 — Email-attested**  
Verified email required to register keypair. Minimum bar for journalist-facing
outreach. Press office cannot claim "anyone could have run this."

**Level 3 — Proof of humanity**  
For adversarial contexts. Verified real-world identity bound to keypair. Receipts
are legally non-repudiable in the strong sense.

### Receipt schema addition (open-case-full-4)

```json
"investigator": {
  "handle": "alex",
  "public_key": "ed25519:base64...",
  "identity_commitment": "hash of verified identity record",
  "identity_method": "self_attested | email_verified | document_verified",
  "key_registered_at": "2026-04-02T00:00:00Z"
},
"investigator_signature": "ed25519:base64...",
"platform_signature": "ed25519:base64..."
```

Two signatures. Platform signs what it computed. Investigator signs that
they reviewed and submitted it. Both required for a receipt to be considered
fully sealed.

---

## Tool upgrade model (Phase 11B)

### The key is a capability credential, not an identity credential

Not "Alex signed this." But "this receipt was produced by a key that has earned
these capabilities, verified against this many accurate prior receipts."

### Personal tools

**The Adapter Deck**  
Unlocked data sources. Everyone starts with FEC + Senate votes. Regulations.gov,
GovInfo, CourtListener, SEC EDGAR, state CF — earned by demonstrating accuracy.
The deck is visible on the profile. It is the flex.

**The Proximity Lens**  
Default window is wide. As reputation builds, earn the right to run tighter
windows. A 7-day lens on an experienced investigator means something a 90-day
lens on a new one does not.

**The Committee Badge**  
Earned by producing accurate receipts on subjects within a committee's
jurisdiction. Armed Services. Finance. Judiciary. The badge means receipts on
those subjects carry extra weight.

**The Receipt Seal**  
Front-facing summary visible on every published receipt:  
"Produced by [handle], Tier [N], [Committee] specialist, [N] verified receipts."

**The Case File**  
Linked chain of investigations. Todd Young → Armed Services sweep → PatternAlert
→ journalist citation. Public, navigable, grows over time.

### Key type schema

```
KEY_TYPE: investigator_armed_services_tier2
CAPABILITIES:
  fec_schedule_a: enabled
  senate_votes: enabled
  lda_lobbying: enabled
  regulations_gov: enabled      ← earned
  govinfo_hearings: enabled     ← earned
  proximity_window: 14_days     ← tightened from default 90
  pattern_alerts: enabled       ← unlocked at tier 2
EARNED_AT: 2026-03-15
VERIFIED_RECEIPTS: 23
CHALLENGED_RECEIPTS: 1
CHALLENGE_OUTCOME: upheld_by_investigator
```

---

## Social and gamified layer (Phase 11C)

### The core insight

Don't build a civic tool. Build a discovery engine that produces civic
accountability as a side effect.

Duolingo doesn't teach Spanish. It makes keeping a streak feel good. The Spanish
is the side effect. Open Case doesn't teach campaign finance. It makes finding
connections feel good. The accountability is the side effect.

### Three social loops

**Creation:** "I found something. I'm posting it."  
90 seconds. Phone points at a document. App extracts structured data. User
confirms signal. Posted to profile with receipt seal.

**Validation:** "47 people verified my find. 3 investigators built on it."  
Receipt gets views. Investigators verify it. Gets cited in a story. User gets
notified three years later: "Your 2023 find was cited in a story about Senator X."
More satisfying than a like. Permanent. Compounding.

**Discovery:** "This donor also appears in these 12 other cases."  
The entity thread is the discovery engine. Pull one thread. Find yourself three
investigations deep into something you never knew existed.

### Social mechanics

**Profile as portfolio**  
Not follower count. Verified record of finds, confirmations, contributions.
Tool deck. Committee badges. Entity threads. Contribution receipts going back
to the first thing ever added. Portable. Journalist cites it like clips.
Researcher cites it like publications.

**Feed as signal stream**  
Not content from people you follow. Signals from subjects being watched. New
evidence added to contributed cases. PatternAlerts that fired overnight.
Entity threads with new data points.

**Duet as collaborative investigation**  
Two investigators, same subject, different angles. One has FEC data. One has
LDA filings. They build on each other's receipts. Combination produces
corroborated signal neither could produce alone. Platform shows the
collaboration — who contributed what, when, in what order.

**Challenge as dispute mechanism**  
Someone posts a receipt. Another investigator thinks the entity resolution is
wrong. They post a challenge receipt — same subject, different methodology,
different finding. Platform shows both linked. Audience can verify independently.
Resolution is public and permanent. Disagreement must be grounded in methodology.
You can't just say "you're wrong." You have to produce a signed receipt showing why.

### The TikTok integration

**60-second receipt video**  
Screen recording of an investigation run. The moment the signal appears.
Voice over explaining the finding. Receipt URL in the video. Anyone watching
can pull the URL and verify it. The video is content. The receipt is evidence.
Same act.

**PatternAlert notification**  
When COMMITTEE_SWEEP fires, push notification to everyone watching any of
those cases. The notification brings them back. The investigation keeps them.

### The target population

Not civic-minded people. Curious people. They don't come for civic
accountability. They come because something caught their attention. The platform
makes the next step easy. Six months later they understand campaign finance
better than most journalists. They didn't study it. They played it.

---

## No-penalty contribution model (Phase 11D)

### Core principles

**The investigation belongs to the subject, not the investigator.**  
It exists forever. Anyone can pick it up, add to it, put it down. The record
accumulates regardless of who holds it.

**An investigation with one signal is better than zero. Always.**  
The person who added one FEC receipt and never came back contributed something
real. It doesn't expire. It doesn't get erased when they leave.

**No penalties for stopping. Ever.**  
Contribution without commitment. The platform should make it feel good to add
one thing and walk away. The XP model and tool unlocks are incentives to return,
not punishments for leaving.

**Newbies go after anyone.**  
No gatekeeping by subject rank. A newbie investigates a senator the same way
they investigate a city council member. The infrastructure is the same. The
engine finds what's there regardless of who's looking.

### The contribution receipt

Separate from the investigation receipt. When someone adds one piece of evidence
to an existing case and walks away, they get a contribution receipt — a small
signed artifact that says "on this date, this investigator added this evidence
to this case."

The newbie who added one FEC record in 2026 should be able to show that receipt
in 2034 when the investigation becomes a story.

### Case states

```
open        → active investigation, accepting evidence
historical  → subject no longer in office, still accepting evidence
archived    → subject deceased or fully retired, read-only
disputed    → active challenge receipt attached, under review
```

Cases never close. They change state. Evidence can always be added to
`open` and `historical` cases. `archived` cases are read-only monuments.

---

## Historical adapter (Phase 11E)

### The feather days

The dream is investigations that reach back as far as records exist.

**Realistic structured data starting points:**
- FEC bulk data: 1979 forward (full electronic records)
- Machine-readable Senate votes: 1973 forward (93rd Congress)
- LDA lobbying: 1996 forward (current system)
- Pre-LDA lobbying: paper records, partially digitized, requires OCR

**The feather days floor:** 1979 for campaign finance. 1973 for votes.  
Before that: document extraction adapters, newspaper archives, congressional
investigation records. Different tools, same evidence layer.

### Data era flags

Every evidence entry carries `data_era`:

```
modern        → 2000-present    (clean APIs, reliable)
digital       → 1979-1999       (bulk data, some schema variation)
transitional  → 1971-1978       (partial digital, requires validation)
pre_disclosure → before 1971    (document extraction, human review required)
```

The era flag tells the signal engine what matching is reliable and what
requires human review. All eras are documented. All eras are labeled honestly.

### What historical investigations show

A long-serving senator case shows:

**Career arc** — donation patterns across every election cycle. How donor
composition shifted. Which industries grew or shrunk as contributors.

**Historical thread** — entity threads showing a donor — or their predecessors
through mergers and acquisitions — appearing across decades. Structural
presence, not individual signals.

**Generational pattern** — some industries donate to whoever holds a seat,
not who the person is. Only visible across enough time to show the pattern
is positional, not personal.

**Before and after** — what changed after key votes? Did donation patterns
shift? Did new donor entities appear? The temporal span makes these questions
answerable.

---

## Pipe decisions — implement now, use later

These are cheap to add now and expensive to retrofit later.

**Time-agnostic evidence schema**  
Every evidence entry carries `data_era`, `source_reliability`, `record_type`
from day one.

**Adapter manifest in every receipt**  
Record not just what adapter ran but what version. Adapter logic changes.
Historical receipts need to be verifiable against the methodology that existed
when they were generated.

**Entity thread as first-class object**  
Canonical IDs are currently keys in a lookup table. Eventually the entity
thread — every appearance across all cases, all time, all investigators —
becomes the primary navigable object. Name it now. Give it an ID.

**Case inheritance model**  
When subject leaves office, case enters `historical` state, not closed.
One more node in the state machine.

**Contribution receipt**  
When someone adds evidence and walks away, they get a small signed artifact.
Costs nothing to generate. Essential for attribution and incentive.

**The shareable receipt URL**  
`open-case.onrender.com/receipts/[receipt_id]`  
Shows: what was found, who found it, when, what it connects to, verification
button. Loads fast. Works in any social platform link preview.  
The receipt is the post. The investigation is the feed. The entity thread
is the algorithm.

---

## The one-line version

The investigation is the subject, not the investigator. It lives as long as
the public record exists. Anyone can add to it. No one has to finish it. It
reaches back as far as records go. The social layer makes contribution feel
like creation. The game mechanics make accuracy feel like winning.

10,000 curious people with 90 seconds each, aggregated and cryptographically
sealed, is more investigative capacity than every newsroom in the country combined.

That's not a fantasy. That's arithmetic.

---

## Prerequisites before any Phase 11 implementation

1. PatternAlert fires on real data with resolution_method ≠ unresolved
2. At least one journalist has received and reviewed an Open Case receipt
3. At least 6 working investigations in the portfolio
4. Receipt export as self-contained offline-verifiable JSON

**Do not build Phase 11 until these are met.**

---

*All Phase 11 work is contingent on Phase 10 completion.*  
*Session resume point: Fix Sullivan bioguide → find 3 FEC committee IDs →*  
*run 4 Armed Services investigations → check GET /api/v1/patterns.*
