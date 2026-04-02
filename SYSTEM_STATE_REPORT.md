# Open Case — system state report (Phase 7)

Generated as part of Phase 7: credential pipeline, LDA lobbying, donor fingerprints, self-baseline, and five-leads reporting.

---

## 1. Project files

| Path | Role | Status |
|------|------|--------|
| `main.py` | FastAPI app, lifespan, config warnings, routers | **Complete** |
| `database.py` | SQLAlchemy engine, sessions, `init_db` | **Complete** |
| `auth.py` | API key validation, handle checks | **Complete** |
| `models.py` | ORM: cases, evidence, signals, profiles, fingerprints, investigation runs, etc. | **Complete** |
| `payloads.py` | JCS-shaped semantic dicts for signing; includes `last_source_statuses` on case when set | **Complete** |
| `signing.py` | Ed25519 sign/verify; private key via `CredentialRegistry` | **Complete** |
| `scoring.py` | Investigator credibility helpers | **Complete** |
| `jobs.py` | Background/async job hooks (if used) | **Partial / thin** |
| `core/credentials.py` | `CredentialRegistry`, `CredentialUnavailable`, status helpers | **Complete** |
| `core/__init__.py` | Package marker / re-exports | **Complete** |
| `adapters/base.py` | `AdapterResponse`, `BaseAdapter`, `credential_mode` | **Complete** |
| `adapters/cache.py` | Adapter HTTP response cache | **Complete** |
| `adapters/dedup.py` | Evidence dedupe helpers | **Complete** |
| `adapters/fec.py` | FEC Schedule A search | **Complete** (uses registry + `credential_mode`) |
| `adapters/congress_votes.py` | Congress.gov votes | **Complete** (registry) |
| `adapters/senate_committees.py` | Senate committee cache (HTML/API) | **Complete** (no direct env read for API key in adapter) |
| `adapters/lda.py` | Senate LDA filings HTTP client | **Complete** |
| `adapters/indiana_cf.py` | Indiana campaign finance | **Stub / partial** (project-specific) |
| `adapters/indy_gis.py` | Local GIS | **Stub / partial** |
| `adapters/marion_assessor.py` | Assessor | **Stub / partial** |
| `adapters/usa_spending.py` | USASpending | **Stub / partial** |
| `adapters/__init__.py` | Exports | **Minimal** |
| `routes/system.py` | `GET /api/v1/system/credentials` | **Complete** |
| `routes/cases.py` | Case CRUD | **Complete** |
| `routes/evidence.py` | Evidence ingest | **Complete** |
| `routes/evidence_disambig.py` | Disambiguation | **Complete** |
| `routes/investigate.py` | POST investigate: adapters, proximity, LDA, fingerprints, baseline, signing | **Complete** |
| `routes/reporting.py` | Report JSON + HTML, signal expose, investigator score | **Complete** (`top_leads` + `signals`) |
| `routes/subjects.py` | Subject lookup; Congress.gov uses `CredentialRegistry` | **Complete** |
| `routes/auth.py` | Registration / keys | **Complete** |
| `routes/admin.py` | Admin | **Complete** |
| `routes/snapshots.py` | Snapshots | **Complete** |
| `engines/temporal_proximity.py` | Donor–vote proximity clustering; `has_lda_filing` | **Complete** |
| `engines/relevance.py` | Relevance scoring (+0.3 LDA) | **Complete** |
| `engines/signal_scorer.py` | Signals from clusters (breakdown includes LDA, sponsorship) | **Complete** |
| `engines/contract_proximity.py` | Contract proximity | **Complete** |
| `engines/contract_anomaly.py` | Contract anomalies | **Complete** |
| `signals/dedup.py` | Signal upsert / merge | **Complete** |
| `data/industry_jurisdiction_map.py` | Jurisdiction hints | **Complete** |
| `templates/report.html` | **Top leads** + **All signals** sections | **Complete** |
| `alembic/env.py` | Migrations env | **Complete** |
| `alembic/versions/*.py` | Schema revisions through Phase 9 | **Complete** |
| `tests/test_temporal_clusters.py` | Cluster / signal tests | **Complete** (run with `PYTHONPATH=.`) |
| `scripts/test_*.py` | Manual integration scripts | **Ad hoc** |
| `PHILOSOPHY.md` | Product notes | **Doc** |

---

## 2. Data flow: POST investigate → signed receipt

1. Client calls **POST** `/api/v1/cases/{id}/investigate` with investigator handle and optional bioguide/FEC hints.
2. **Clear prior run artifacts** for this case (signals/evidence from automated pipeline per implementation), while retaining historical `investigation_runs` for baseline comparison before overwrite logic runs (order as implemented in `investigate.py`).
3. **`source_statuses`** list is built while **adapters** run (FEC, Congress votes, local adapters, etc.). Each adapter uses **`CredentialRegistry`** where applicable; responses carry **`credential_mode`** (`ok`, `fallback`, `credential_unavailable`). Cached hits are marked `cached`.
4. Evidence entries are created/updated; **FEC** and **vote** records feed **temporal proximity** (`detect_proximity`) with evidence entries for LDA matching.
5. **`_ingest_lda_for_unique_donors`** queries **LDA** for donor/org names; adds **`lobbying_filing`** evidence; new filings extend evidence used when rebuilding clusters if called after LDA (sequence as in route).
6. **Signals** are built (`build_signals_from_proximity`, contract engines, etc.), **deduplicated**, saved.
7. **Cross-case fingerprints**: for top signals, **`donor_fingerprints`** rows are written; queries populate **`cross_case_appearances`** / **`cross_case_officials`** on signals.
8. **Self-baseline**: previous **`investigation_runs`** row for this case supplies prior top-donor weights; **`weight_delta`**, **`new_top_signal`**, **`first_appearance`** set on signals; a new **`InvestigationRun`** row is inserted.
9. **`case.last_source_statuses`** is set to JSON **`source_statuses`**.
10. **Signing**: `apply_case_file_signature` builds canonical payload via **`case_semantic_dict`** (includes **`last_source_statuses`** when present) + ordered evidence; **`sign_payload`** produces **`case.signed_hash`** / timestamp.

**Receipt honesty:** the signed case semantic blob includes **which data sources were clean, fallback, cached, or credential-skipped**, so the receipt reflects coverage limits.

---

## 3. Adapters

| Adapter | Source | Auth | Failure modes | Production notes |
|---------|--------|------|---------------|------------------|
| **FEC** (`fec.py`) | api.open.fec.gov | `FEC_API_KEY` or **DEMO_KEY** fallback | Rate limits, API errors → empty/partial results; `credential_unavailable` if required key missing (FEC is optional) | DEMO_KEY is fine for demos; real keys for scale |
| **Congress votes** (`congress_votes.py`) | api.congress.gov | Optional `CONGRESS_API_KEY` | Without key: limited/quota behavior per API; adapter should surface `credential_unavailable` or degraded matching | Register key for reliability |
| **LDA** (`lda.py`) | lda.senate.gov | None (public) | HTTP failures, pagination limits, shape changes | Suitable for production with monitoring |
| **Senate committees** (`senate_committees.py`) | Senate / Congress data | No API key in-module | HTML/selectors drift | Cache mitigates load; fragile to site changes |
| **Indiana CF / GIS / Marion / USASpending** | Various | Mixed | Network, schema drift | Expect partial/stub depending on deployment |

---

## 4. Environment variables

| Variable | Required? | If missing |
|----------|-----------|------------|
| `FEC_API_KEY` | No | `get_credential` returns **`DEMO_KEY`**; status **`fallback`** |
| `CONGRESS_API_KEY` | No | Congress adapter/subject search limited; status **`unavailable`** |
| `REGULATIONS_GOV_API_KEY` | No | Reserved for future regulations adapter |
| `LDA_API_KEY` | No | LDA is public; field reserved |
| `GOVINFO_API_KEY` | No | Reserved |
| `OPEN_CASE_PRIVATE_KEY` | Yes (auto-bootstrapped) | **`CredentialUnavailable`** if signing attempted without bootstrap file/env resolution |
| `OPEN_CASE_PUBLIC_KEY` | Recommended for verification displays | Signing still produces hash/signature if private present; verify needs public |
| `DATABASE_URL` | No | Defaults to **`sqlite:///./open_case.db`** |
| `BASE_URL` | Strongly recommended in production | Startup warning or exit in production mode (`main.check_config_warnings`) |
| `ENV` | No | Default **development** |
| `BUST_CACHE` | No | Cache bypass flag in adapter cache |

---

## 5. Database schema (conceptual)

- **`case_files`**: case metadata, **`last_source_statuses`** (JSON text of adapter statuses).
- **`evidence_entries`**: all evidence types including **`lobbying_filing`**, FEC/vote rows, etc.
- **`signals`**: weights, breakdown JSON, **`relevance_score`**, **`cross_case_*`**, **`weight_delta`**, **`new_top_signal`**, **`first_appearance`**, exposure, confirmation fields.
- **`donor_fingerprints`**: normalized donor key, case, signal, weight, official name, bioguide, timestamps — populated after each investigate for top signals.
- **`investigation_runs`**: per-run snapshot: signal count, **`top_donors`** JSON — populated after each run.
- **Other tables**: `investigators`, `case_contributors`, `source_check_logs`, `case_snapshots`, `signal_audit_log`, `adapter_cache`, `subject_profiles`, `senator_committees`, etc.

**Empty vs filled:** new installs have empty fingerprints/runs until investigations run. `last_source_statuses` is null until first POST investigate completes.

---

## 6. Test coverage

- **Present:** `tests/test_temporal_clusters.py` (3 tests) — clustering/signal shaping; requires **`PYTHONPATH=.`**.
- **Missing / untested:** HTTP adapters (FEC, Congress, LDA), full **`investigate`** route, migration upgrades, signing round-trips, reporting HTML, cross-case and baseline math edge cases, concurrent runs.

---

## 7. Gaps vs. roundtable-style spec (honest)

- Not all global “regulations.gov” / GovInfo adapters are wired into investigate.
- Cross-case graph is **opportunistic** (only cases run in this deployment), not a national donor-official graph.
- **Baseline** is **intra-case over time**, not cross-member.
- **Top leads** cap at **5** and use **confirmed OR relevance ≥ 0.5** — thresholds are heuristic, not legal standards.
- FEC **DEMO_KEY** rate limits can silently reduce donor coverage on busy demos.

---

## 8. If a journalist ran this on a new senator today

They would get an automated **case report** that pulls **public FEC receipts** and **Congressional vote** data (quality improves markedly with a free Congress API key), clusters **donors whose contributions fall near votes** in time, scores **committee/jurisdiction overlap** and **sponsorship**, optionally attaches **Senate LDA lobbying filings** when names match, and shows up to **five “top leads”** with badges for jurisdiction, LDA, and sponsorship, plus **cross-donor hints** once multiple officials have been investigated in the same system. The **second** investigation on the same senator would start showing **weight deltas** and “new top signal” flags. Nothing here is proof of corruption—outputs are **leads for human review**, and the **signed receipt** documents which sources actually succeeded.
