# Open Case

**Open Case** is a backend investigation pipeline that links campaign finance, votes, lobbying, and regulatory records, then surfaces **cross-case money patterns** and **cryptographically signed evidence receipts**. It finds coordinated donation timing and related public-record context; it does not prove intent or legal violation.

**Live:** https://open-case.onrender.com  
**API docs:** https://open-case.onrender.com/docs

It does not assert conclusions. It produces receipts.

---

## What it does

An investigator opens a case for a subject (typically a federal official), runs **investigate** to pull FEC receipts, Senate votes, optional LDA/regulations/GovInfo enrichment, and **temporal proximity signals** (donation ↔ vote). Separately, the **pattern engine** scans donor fingerprints across all cases for structural patterns (bundles, sector clusters, geography skew, revolving door, etc.). Every evidence row and case snapshot can be sealed with **Ed25519** so downstream readers can verify integrity.

---

## Detection methods (pattern engine)

These rules power `GET /api/v1/patterns` (and case-filtered variants). The engine version is exposed as `pattern_engine_version` in API responses.

| Rule | What it detects |
|------|-----------------|
| `SOFT_BUNDLE_V1` | 3+ donors to the same committee within 7 days; suspicion blends diversification, vote proximity, and quarterly deadline discount |
| `SOFT_BUNDLE_V2` | Same windowing as V1; **suspicion_score** is a weighted score from donor-type mix, occupation-sector similarity, optional baseline spike, and hearing proximity — see `diagnostics` on each alert and `GET /api/v1/patterns/diagnostics?case_id=` |
| `SECTOR_CONVERGENCE_V1` | Donors from the same industry sector clustering in time around votes |
| `GEO_MISMATCH_V1` | High share of **individual** (non-org) donors from outside the senator’s home state in a short window |
| `REVOLVING_DOOR_V1` | Donor tied to LDA lobbying registrant with filing year and issue codes near a relevant vote |

**Also present (same engine):** `COMMITTEE_SWEEP_V1`, `FINGERPRINT_BLOOM_V1`, `DISBURSEMENT_LOOP_V1` — see [ARCHITECTURE.md](ARCHITECTURE.md#pattern-engine-current-v16) for formulas and fields.

---

## Data sources

| Source | What it finds |
|--------|----------------|
| FEC Schedule A | Donations to a committee (by `committee_id` or contributor search) |
| FEC Schedule B | Disbursements (optional enrich path for pattern / linkage rules) |
| Senate LIS XML | Roll call votes |
| Senate LDA | Lobbying registrations linked to donor entities |
| Congress.gov | Member metadata; better vote matching when a key is set |
| Regulations.gov | Regulatory comments by donor entities *(optional key)* |
| GovInfo | Congressional hearing witnesses *(optional key)* |
| USASpending | Federal awards |
| Indiana Campaign Finance | State-level cross-check (Indiana subjects) |

---

## Quickstart

```bash
# Open case(s) (batch)
curl -X POST https://open-case.onrender.com/api/v1/cases/batch-open \
  -H "Authorization: Bearer $OPEN_CASE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"subjects": [{"subject_name": "Tom Cotton", "bioguide_id": "C001095", "fec_committee_id": "C00499988"}], "created_by": "alex"}'

# Investigate (use case id from batch-open response)
curl -X POST https://open-case.onrender.com/api/v1/cases/{case_id}/investigate \
  -H "Authorization: Bearer $OPEN_CASE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"subject_name": "Tom Cotton", "investigator_handle": "alex", "bioguide_id": "C001095", "fec_committee_id": "C00499988"}'

# Pattern alerts (all rules)
curl https://open-case.onrender.com/api/v1/patterns \
  -H "Authorization: Bearer $OPEN_CASE_API_KEY"

# SOFT_BUNDLE_V2 diagnostics for one case
curl "https://open-case.onrender.com/api/v1/patterns/diagnostics?case_id={case_id}" \
  -H "Authorization: Bearer $OPEN_CASE_API_KEY"
```

---

## Receipt philosophy

Documented absence matters as much as documented presence. A receipt that lists which adapters ran, what matched, and what returned empty is still a complete record. Interpretation stays with the investigator.

See [PHILOSOPHY.md](PHILOSOPHY.md) for the full statement.

---

## Required and optional environment variables

| Variable | Required | Role |
|----------|----------|------|
| `DATABASE_URL` | Recommended in production | Defaults to `sqlite:///./open_case.db` |
| `OPEN_CASE_PRIVATE_KEY` | Auto-generated if unset | Ed25519 signing (with public key) |
| `OPEN_CASE_PUBLIC_KEY` | Paired with private | Verify / seal receipts |
| `FEC_API_KEY` | No (demo key rate-limited) | FEC Open Data |
| `CONGRESS_API_KEY` | No | Congress.gov; name ↔ bioguide |
| `REGULATIONS_GOV_API_KEY` | No | Regulations.gov comments |
| `GOVINFO_API_KEY` | No | GovInfo hearing search |
| `BASE_URL` | Yes in production | Public links / OG tags |
| `ENV` | No | `development` vs `production` (strict BASE_URL in prod) |
| `ADMIN_SECRET` | For admin routes | Credential registration, entity aliases |
| `CREDENTIAL_DATA_DIR` | No (Render: `/data/.credentials`) | On-disk API key fallback |
| `BUST_CACHE` | No | Skip adapter cache reads when debugging |
| `LDA_API_KEY` | No | Reserved; Senate LDA is public today |

Copy **`.env.example`** to `.env` and fill in values.

---

## Local setup

```bash
git clone https://github.com/Swixixle/Open-Case
cd Open-Case
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for adapters, engines, and pattern-rule detail.

---

## Documentation index

Project narratives and phase notes live under **[docs/](docs/README.md)** (mostly internal).

---

## Tests

```bash
PYTHONPATH=. pytest tests/
```

---

## Contributing & license

- [CONTRIBUTING.md](CONTRIBUTING.md)  
- MIT — [LICENSE](LICENSE)  
- Security — [SECURITY.md](SECURITY.md)

---

*Built by Alex Maksimovich / Nikodemus Systems. Findings are grounded in public records; the platform does not produce legal conclusions.*
