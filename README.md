# Open Case

A cryptographically signed government accountability investigation engine.

Open Case cross-references public records — campaign finance, lobbying filings, legislative votes, judicial appointments, financial disclosures — to surface proximity patterns between money and decisions. Every finding is epistemically tagged, source-linked, and signed with an Ed25519 receipt.

**Philosophy:** Receipts, not verdicts. This is a mirror of public records, not a verdict machine. No inference of guilt or wrongdoing is made or implied.

---

## Strongest live finding

**Tom Cotton — SOFT_BUNDLE_V1 — score 0.921**

FEC records document a cluster of financial services and defense sector donations to Cotton's principal committee on February 9–11, 2026, within the proximity window of a Senate vote on S.J.Res. 95. Score reflects donation timing, sector concentration, and committee jurisdiction alignment.

![React client — federal legislative directory and featured finding](docs/assets/ui/01-home-directory-federal-senate.png)

---

## What it does

1. Create a case for any public official — senator, judge, mayor, sheriff, zoning board member
2. The investigation pipeline ingests public records from relevant sources
3. The pattern engine scores proximity between financial relationships and public decisions (**corruption-adjacent pattern detection** — proximity and timing, not legal findings)
4. Every finding is classified by epistemic level
5. A cryptographically signed receipt is generated — shareable, verifiable, tamper-evident
6. **Optional:** Tiered **LLM assist** for reporter-facing story angles (`POST /api/v1/assist/story-angles`) and routed **Perplexity / Gemini / Claude** calls for senator deep-research enrichment — core detection stays deterministic without these keys

---

## Epistemic levels

Every finding is tagged at ingest. Classification is source-driven, not sentiment-driven.

| Level | Meaning |
|-------|---------|
| `VERIFIED` | Official record — court document, regulatory finding, government disclosure |
| `REPORTED` | Credible named-source journalism or official statement |
| `ALLEGED` | Formal complaint or legal allegation — not yet adjudicated |
| `DISPUTED` | Formal rebuttal or contrary finding on record |
| `CONTEXTUAL` | Unverified public discourse — hidden from public responses by default |

A court filing is VERIFIED as a document. The accusation inside it is ALLEGED until adjudicated. Historical records are never deleted — disputes update claim status without erasing the trail.

---

## Pattern engine

The engine ships **18 pattern rules** (see `RULE_*` and `PATTERN_RULE_IDS` in `engines/pattern_engine.py`):

| Rule | Signal |
|------|--------|
| `COMMITTEE_SWEEP_V1` | Donations from industries under direct committee oversight |
| `FINGERPRINT_BLOOM_V1` | Cross-case donor fingerprints |
| `SOFT_BUNDLE_V1` | Donor clustering around legislative events |
| `SOFT_BUNDLE_V2` | Donor clustering (v2 weights: sector, baseline, hearings) |
| `SECTOR_CONVERGENCE_V1` | Sector donation concentration vs committee jurisdiction |
| `GEO_MISMATCH_V1` | Geographic donor anomalies |
| `DISBURSEMENT_LOOP_V1` | PAC disbursement patterns |
| `JOINT_FUNDRAISING_V1` | Joint fundraising committee signals |
| `BASELINE_ANOMALY_V1` | Deviation from historical baseline |
| `ALIGNMENT_ANOMALY_V1` | Vote/donor alignment anomalies |
| `AMENDMENT_TELL_V1` | Amendment timing vs donor activity |
| `HEARING_TESTIMONY_V1` | Testimony/donor overlap |
| `REVOLVING_DOOR_V1` | LDA / employment transition overlap with donors |
| `LEGISLATIVE_RELATED_ENTITY_DONOR_V1` | Curated PAC/affiliate vs donor-of-record near roll-call votes (federal legislative) |
| `LOCAL_CONTRACTOR_DONOR_LOOP_V1` | Local procurement vendor ↔ donor (direct / curated alias) |
| `LOCAL_CONTRACT_DONATION_TIMING_V1` | Donation timing vs contract award (local, award-only) |
| `LOCAL_VENDOR_CONCENTRATION_V1` | Top vendor vs top donor overlap (local) |
| `LOCAL_RELATED_ENTITY_DONOR_V1` | Curated related-entity donor vs vendor (local) |

---

## Subject coverage

All branches and levels of American government:

**Federal:** Senators, House members, President/VP, federal judges (SCOTUS through magistrate and bankruptcy), administrative law judges

**State:** Governors, legislators, attorneys general, secretaries of state, treasurers, state judges

**Local elected:** Mayors, city council, district attorneys, sheriffs, prosecutors, school boards, comptrollers

**Local appointed:** Police commissioners and chiefs, zoning and planning boards, utility and water boards, transit authorities, port and airport authorities, parole boards, corrections commissioners, inspectors general, gaming and liquor commissions

---

## Data sources

**Implemented:**

| Source | Coverage |
|--------|----------|
| FEC | Schedule A/B, historical cycles, JFC |
| Congress.gov | Votes, amendments, committee assignments |
| LDA | Lobbying filings |
| CourtListener | Judicial opinions, dockets, financial disclosures |
| FJC Biographical Database | Article III judge biographies and appointments |
| USASpending | Federal contracts and grants |
| GovInfo | Hearings, legislative documents |
| Regulations.gov | Regulatory comments and filings |
| Indiana Campaign Finance | State-level campaign records |

**Planned:** PACER, eJudiciary disclosures, local campaign finance, city contracts, local news, bar complaints, FollowTheMoney, property records, use of force records, DOJ pattern and practice findings

---

## Judicial pilot

**Indianapolis (S.D. Indiana) + Chicago (N.D. Illinois)**

First diagnostic run on Judge James R. Sweeney II (S.D. Indiana, appointed 2017). FJC returned full biographical record: Naval Academy, Notre Dame Law, Marine Corps, Tinder clerkship, commission date 2018-09-13. CourtListener returned person ID and financial disclosure index. Pattern engine returned zero alerts — expected, no FEC or vote data available for judicial subjects under current adapters.

---

## Verified senator corpus

Sullivan (R-AK) · Cotton (R-AR) · Ernst (R-IA) · Wyden (D-OR) · Crapo (R-ID) · Grassley (R-IA) · Cantwell (D-WA)

---

## Project structure

```
adapters/       FEC, CourtListener, FJC, LDA, Congress, USASpending, and more
alembic/        Database migrations (14 phases)
client/         React/Vite frontend (build to client/dist for /app static mount)
core/           Subject taxonomy, credentials, admin gate
data/           Source registry, entity aliases, industry maps
engines/        Pattern engine, signal scorer, entity resolution, temporal proximity
routes/         API endpoints (incl. optional assist)
scripts/        CI floor, epistemic classifier, calendar calibration, pilot seed
services/       Dossier, report stream, epistemic classifier, LLM/research routers, human review
tests/          311 passing (PYTHONPATH=. pytest tests/)
main.py         FastAPI entry point
models.py       SQLAlchemy models
payloads.py     Receipt signing and sealing
```

---

## API

Case file CRUD, evidence, and snapshots use the `/cases` prefix; reports and the investigation pipeline use `/api/v1`.

**OpenAPI:** `GET /openapi.json` — currently **~43 paths / 44 HTTP operations** (full surface includes admin, auth, patterns, reporting, etc.).

```
POST   /cases                                      Create a case (Bearer)
GET    /cases/{case_id}                            Case with evidence
GET    /api/v1/cases                               List cases — filter by government_level, branch, subject_type, pilot
POST   /api/v1/cases/{case_id}/investigate         Run investigation pipeline (Bearer)
GET    /api/v1/cases/{case_id}/report              Signed report (JSON)
GET    /api/v1/cases/{case_id}/report/view         HTML report
GET    /api/v1/cases/{case_id}/report/pattern-events  SSE stream for async pattern alerts
POST   /api/v1/findings/{finding_id}/dispute       Submit dispute or correction (Bearer)
POST   /api/v1/assist/story-angles               Optional narrative story angles from dossier (Bearer; tiered Gemini → Claude)
GET    /api/v1/subjects/search                     Search subject profiles
GET    /api/v1/methodology                         Methodology and legal liability text
```

Admin routes require `X-Admin-Secret` (and API key issuance / cache flush require `ADMIN_SECRET` to be set).

**Smart routing (committed in `services/`):**

- **`llm_router.py`** — classifies dossier complexity and routes story-angle generation (e.g. Gemini for lighter tiers, Claude for heavy vote–money patterns).
- **`perplexity_router.py`** — research-phase routing for senator enrichment (Perplexity Sonar / deep research vs Gemini-first by category; phase-2 narrative prefers Claude when configured).

---

## Setup

```bash
git clone https://github.com/Swixixle/Open-Case.git
cd Open-Case
pip install -r requirements.txt

# Configure
cp .env.example .env
# Required: DATABASE_URL, FEC_API_KEY, CONGRESS_API_KEY
# Required: ADMIN_SECRET (privileged HTTP routes)
# Signing: OPEN_CASE_PRIVATE_KEY / OPEN_CASE_PUBLIC_KEY (Ed25519; auto-generated on first boot if missing — set explicitly in production)
# Optional: COURTLISTENER_API_KEY, PERPLEXITY_API_KEY

# Database
alembic upgrade head

# Run
uvicorn main:app --reload

# Test
PYTHONPATH=. pytest tests/
```

**Deployment:** see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (Render-friendly checklist, static client, env vars).

---

## License

See LICENSE. All findings link directly to primary sources. This system documents public records and labels them by evidentiary status. No inference of guilt or wrongdoing is made or implied.
