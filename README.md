# Open Case

**Public record investigation infrastructure.**

Open Case cross-references campaign finance, Senate votes, lobbying filings, and
regulatory records to produce cryptographically-signed receipts documenting what
public records show about the relationship between money and political decisions.

It does not assert conclusions. It produces receipts.

**Live:** https://open-case.onrender.com  
**API docs:** https://open-case.onrender.com/docs

---

## What it does

Point it at a public official. It queries:

| Source | What it finds |
|--------|---------------|
| FEC schedule_a | Donations to the official's campaign committee |
| Senate LIS XML | Roll call votes |
| Senate LDA | Lobbying registrations by donor entities |
| Regulations.gov | Regulatory comments by donor entities *(requires key)* |
| GovInfo | Congressional hearing witnesses *(requires key)* |

It detects temporal proximity between donations and votes, scores signals by
relevance, and produces a signed receipt documenting every connection found —
and every source checked that found nothing.

---

## What a receipt looks like

Each investigation produces:

- **Signals** ranked by weight — each documenting a donation, a vote, the gap
  between them, and any corroborating public records
- **Evidence tiers** — Documented / Corroborated / Multi-source
- **Source disclosure** — which adapters ran, which were unavailable, why
- **Cross-case appearances** — donor entities tracked across multiple investigations
- **Pattern alerts** — when a donor appears across multiple officials on the same
  committee within a tight window
- **Ed25519 cryptographic signature** — the receipt is sealed at generation time
  and cannot be altered

Live report example: https://open-case.onrender.com/api/v1/cases/f1213145-0494-45a6-8254-8f963927741f/report/view

---

## What it does not do

- It does not have a web frontend. It is a FastAPI backend.
- It does not assert causation or legal conclusions.
- It does not automatically confirm wrongdoing.
- It does not scrape or access non-public records.

Everything it finds is in a public government database. The receipt documents
what is there. Interpretation is left to the investigator.

---

## Current investigations

| Official | Committee | Signals | Status |
|----------|-----------|---------|--------|
| Todd Young (R-IN) | Armed Services, Foreign Relations | 76 | ✅ Live |
| Jim Banks (R-IN) | Armed Services | 61 | ✅ Live |

---

## Stack

- **API:** FastAPI + Python 3
- **Database:** SQLite (local) / Postgres (production via `DATABASE_URL`)
- **Signing:** Ed25519 via `cryptography` library
- **Migrations:** Alembic
- **Templates:** Jinja2
- **Hosting:** Render

---

## Setup

```bash
git clone https://github.com/Swixixle/Open-Case
cd Open-Case
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
uvicorn main:app --reload
```

### Environment variables

Copy `.env.example` to `.env` for the full template. Minimum useful values:

```
BASE_URL=http://localhost:8000   # Use your public URL in production (required for OG/receipt links)

FEC_API_KEY=                       # Optional; DEMO_KEY is rate-limited — register at https://api.open.fec.gov/

OPEN_CASE_PRIVATE_KEY=            # Ed25519 signing; auto-generated on first boot if empty
OPEN_CASE_PUBLIC_KEY=

ADMIN_SECRET=                      # Protects admin endpoints (e.g. credential file registration, entity alias POST)
```

**Optional (unlocks enrichment and better vote matching):**

```
CONGRESS_API_KEY=                  # api.congress.gov — sign up via https://api.data.gov/signup/
REGULATIONS_GOV_API_KEY=
GOVINFO_API_KEY=
```

---

## Running an investigation

### 1. Get an API key

```bash
# Via /docs UI or:
curl -sS -X POST "http://localhost:8000/api/v1/auth/keys" \
  -H "Content-Type: application/json" \
  -d '{"handle":"your_handle"}'
```

### 2. Open a case

```bash
curl -sS -X POST "http://localhost:8000/cases" \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "senator-name",
    "title": "Senator Name",
    "subject_name": "Senator Name",
    "subject_type": "public_official",
    "jurisdiction": "State, USA",
    "created_by": "your_handle",
    "summary": "Brief description"
  }'
```

### 3. Run investigation

```bash
curl -sS -X POST "http://localhost:8000/api/v1/cases/CASE_ID/investigate" \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "subject_name": "Senator Name",
    "investigator_handle": "your_handle",
    "bioguide_id": "BIOGUIDE_ID",
    "fec_committee_id": "C00000000"
  }'
```

### 4. View report

```
http://localhost:8000/api/v1/cases/CASE_ID/report/view
```

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full layer diagram.

```
Adapters          →  pull public records
Evidence layer    →  store facts with source URLs and dates
Signal engine     →  detect proximity patterns, score by relevance
Pattern engine    →  cross-case donor pattern detection
Entity resolution →  normalize donor names across cases
Signing layer     →  seal receipts with Ed25519
Report layer      →  journalist-facing HTML output
```

---

## Tests

```bash
PYTHONPATH=. pytest tests/
```

72 tests covering temporal edge cases, entity resolution, confirmation
standard, credential rotation, cross-case fingerprints, and pattern engine rules.

---

## Philosophy

The system is built around one idea: **documented absence is as meaningful as
documented presence.**

A receipt that says "we checked FEC, Senate votes, and LDA — and found these
connections" is useful. A receipt that says "we checked Regulations.gov and found
nothing" is also useful. Both are honest. Both are verifiable. Neither is a verdict.

See [PHILOSOPHY.md](PHILOSOPHY.md) for the full statement.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT. See [LICENSE](LICENSE).

---

## Security

See [SECURITY.md](SECURITY.md) for responsible disclosure policy.

---

*Built by Alex Maksimovich / Nikodemus Systems.*  
*All findings are grounded in public government records.*  
*The platform does not produce legal conclusions.*
