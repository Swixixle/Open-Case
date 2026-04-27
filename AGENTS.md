# Open Case — Agent Priming Doc

> **For AI coding assistants (Cursor, Claude, Cowork, Copilot, etc.):** Read **`AGENTS.md`** first. It is the authoritative **short-form** state of the project. Longer file-by-file history and architecture notes live in `docs/internal/PROJECT_STATE.md`, which may lag (see its header dates).

**Last updated:** 2026-04-26  
**Last verified:** 2026-04-26 (pytest `tests/test_public_demo.py` + engine v2.7; full-suite green not re-run this session)  
**Pattern engine version:** v2.7  
**Test count:** 395 collected (`PYTHONPATH=. pytest tests/ --co -q | tail -1`); CI floor still ≥201 passed (`server/scripts/ci_pytest_floor.py`)  
**CI floor:** ≥201 passed (`server/scripts/ci_pytest_floor.py`; invoked from `.github/workflows/ci.yml`)

---

## 60-second priming

Open Case is a government transparency investigation engine. It ingests public records, runs deterministic pattern rules against them, and produces cryptographically signed, epistemically labeled findings under the philosophy **"receipts, not verdicts."** The system surfaces patterns and flags concerns. It does not render judgment.

**Stack:** FastAPI, SQLAlchemy, **SQLite by default** (Postgres typical in production), React/Vite frontend, Ed25519 signing (JCS canonicalization, SHA-256 digest), deployed on Render.

**Vite dev:** `client/vite.config.js` proxies same-origin `/api` to `http://127.0.0.1:8000` so `fetch('/api/...')` and `curl http://127.0.0.1:8000/...` hit the same process when `VITE_OPEN_CASE_API_BASE` is unset. Set that env to a full URL to target a remote API.

**Public demo (optional):** When `OPEN_CASE_PUBLIC_DEMO=1`, unauthenticated routes under `/api/v1/demo/*` run a fixed senator cohort through the real `execute_investigation_for_case` pipeline (server must still hold FEC/Congress keys in env). React path: `/app/demo` in production (static `base`), `/demo` in Vite dev. See `routes/demo.py` and `.env.example`.

**Architecture shape:** Subject-type-driven. A `data/subject_type_sources.json` registry maps each subject type to ordered adapter lists in tiers (`primary`, `secondary`, `judicial`, `local`, `historical`, …). A `ResearchProfile` class in `services/research_profile.py` reads this registry. **The research algorithm is mostly uniform** — gather evidence, score proximity, sign receipt — but **the Indianapolis local pilot** also has explicit adapter choices in `routes/investigate.py` (e.g. required `idis` + `indy_*` for `government_level=local`); that path is not “registry-only,” so do not assume every row in the JSON is wired end-to-end yet.

**Coverage:** Federal legislator and executive `subject_type` rows in the JSON include many `implemented` adapter ids; **deepest** behavior is still the FEC + Senate vote + pattern stack. For **local** subjects, the registry is still largely `planned`; Marion County / IDIS work runs through the IDIS and Indianapolis contract/procurement modules (see **Adapter registry** below).

**Pattern engine:** 18 active rules at v2.7 (`PATTERN_RULE_IDS` in `engines/pattern_engine.py`). Tom Cotton / **SOFT_BUNDLE** score ~0.921 appears in docs as a calibration reference exemplar (production scores depend on live data and a completed investigate run).

**Epistemic layer:** Every finding carries a VERIFIED / REPORTED / ALLEGED / DISPUTED / CONTEXTUAL label, source-driven. Signed receipts include the epistemic distribution of contributing findings.

---

## Active focus

*This section is the most volatile part of this doc. If the `Last verified` date above is more than two weeks old, ask before assuming these are still current priorities.*

**Open Case backend investigation pipeline is functional.** With valid **FEC** and **Congress** API keys (and a working local DB + investigator API key), the system ingests public records, runs the pattern engine, and returns evidence plus signals end-to-end. A representative run: **Todd Young** — hundreds of evidence entries and dozens of signals in one investigate request (wall-clock and counts depend on keys, cache, and network).

**Current priority: frontend demo-readiness.** Confirm the React app (`client/`) correctly renders search, profile tabs, pattern alerts, epistemic labels, and signed receipts. Exercise a full read-only journalist path: landing → search → case/profile → pattern alert or signal detail → report / receipt view. Backend responses are the source of truth for what “should” appear.

**Standing architectural work (not blocking backend E2E):**

1. **Entity resolution refactor** — split “exact legal entity” vs a **family / affiliate** cluster id when the schema grows one (see `models.py` / `engines/entity_resolution.py`). Intended to unblock cross-actor pattern ideas (`DONOR_CONVERGENCE`-style) once stable joins exist.

2. **IDIS live hardening** — `adapters/indiana_campaign_finance.py` (adapter key `idis`): narrow the gap between fixture-validated local runs and always-publishable live bulk + refresh.

**Worth naming (out of band):**

- `JUDICIAL_RELATED_ENTITY_*` rules — spec’d / partial; depend on CourtListener, FJC, and keys.
- Deeper “capillary” influence than money-near-vote — `HEARING_TESTIMONY_V1` and `REVOLVING_DOOR_V1` only cover slices of that problem space.

---

## Subject taxonomy

Each subject has a `subject_type`, a `government_level` (`federal` | `state` | `local`), and a `branch` (`legislative` | `executive` | `judicial` | `administrative`).

**Strongest implemented paths in code today:** `senator`, `house_member`, and legacy `public_official` (FEC + Congress + LDA + many secondary slots); federal **executive** and **vp** JSON rows also list many `implemented` ids. **Federal judicial** types list **`fjc_biographical`** and **`courtlistener`** as `implemented` in the registry; behavior still depends on keys and data returns.

**Many state and local** `subject_type` keys are registered in the taxonomy with `status: "planned"` adapter ids — the roadmap is in the file; coverage is not uniform.

**Local pilot (Indianapolis / Marion):** In addition to registry rows, `routes/investigate.py` wires **IDIS** (`idis`) and Indianapolis modules **`indy_tax_abatement`** (contracts / tax abatement dataset hub in `adapters/indianapolis_contracts.py`, `source_name` **`INDY_TAX_ABATEMENT`**) and **`indy_procurement`** (`adapters/indianapolis_procurement.py`) when `government_level=local` (see `_temporal_core_required_adapters`).

Legacy fallback: `public_official` remains supported.

---

## Pattern rule inventory (engine v2.7)

Use `PATTERN_RULE_IDS` in `engines/pattern_engine.py` as the source of truth (18 rules). The README’s table is kept aligned.

**Local (Marion / IDIS + contracts/procurement), implemented** — `LOCAL_CONTRACTOR_DONOR_LOOP_V1`, `LOCAL_CONTRACT_DONATION_TIMING_V1`, `LOCAL_VENDOR_CONCENTRATION_V1`, and `LOCAL_RELATED_ENTITY_DONOR_V1` (see `engines/pattern_engine.py` when data supports them).

**Spec'd but not implemented** (examples; see engine file for names actually present):

- `DONOR_CONVERGENCE_V1` / `VOTE_EPISODE_ALIGNMENT_V1` — cross-actor; blocked on entity graph + resolution, not on wiring the JSON registry alone.
- `JUDICIAL_RELATED_ENTITY_*` / `EXECUTIVE_RELATED_ENTITY_DONOR_V1` — not in `PATTERN_RULE_IDS` as shipped.

The related-entity *family* of rules (where they exist) shares one idea: the donor and the beneficiary are not always the same literal entity — affiliates, PACs, parents, trade groups. Different subject types get different rule members over time.

---

## Epistemic classification

Source type drives label. Summary:

- Government primary sources (FEC, Senate votes, FJC, court records) → `VERIFIED`
- Vetted secondary reporting (major outlets, wire services) → `REPORTED`
- Complaints, indictments, allegations → `ALLEGED`
- Social media, anonymous, low-confidence → `CONTEXTUAL`
- Sources in active dispute with a response on file → `DISPUTED`

Retroactive re-classification: `python3 scripts/classify_epistemic_levels.py` (idempotent; supports `--dry-run`).

---

## Adapter registry — keys used in `subject_type_sources.json` vs `adapters/`

**Do not** use obsolete names like `indy_gis` or `marion_assessor` — they are **not** in this repository. The **local** pilot uses the IDs below.

**Implemented registry ids (union of `status: "implemented"` across the JSON) include** (verify before citing an edge case):  
`fec`, `congress`, `lda`, `regulations`, `govinfo`, `govinfo_hearings`, `congress_amendments`, `fec_jfc`, `fec_schedule_b`, `fec_historical`, `usaspending`, `indiana_cf`, `fjc_biographical`, `courtlistener`, and others depending on `subject_type` — **`opensecrets` / `followthemoney` are often `planned`** even when adjacent ids are `implemented` — **read the JSON** for the exact list per `subject_type`.

**Pipeline / legacy adapter keys** (see `routes/investigate.py` `_LEGACY_REGISTRY_ADAPTER_IDS` and `adapters/*.py`):

| Registry / pipeline id | Python module (typical) |
|------------------------|-------------------------|
| `fec` | `adapters/fec.py` |
| `congress` | `adapters/congress_votes.py` (Senate LIS; not House roll calls) |
| `usaspending` | `adapters/usa_spending.py` |
| `lda` | `adapters/lda.py` |
| `indiana_cf` | `adapters/indiana_cf.py` |
| `courtlistener` | `adapters/courtlistener.py` |
| `fjc_biographical` | `adapters/fjc_biographical.py` |
| `idis` | `adapters/indiana_campaign_finance.py` (IDIS bulk) |
| `indy_tax_abatement` | `adapters/indianapolis_contracts.py` |
| `indy_procurement` | `adapters/indianapolis_procurement.py` |
| `govinfo` / `govinfo_hearings` / `regulations` / `congress_amendments` / `fec_jfc` / `fec_schedule_b` | Matching modules under `adapters/` |

**Also present under `adapters/` (supporting, enrichments, tests):** e.g. `perplexity_enrichment.py`, `senate_committees.py`, `cache.py`, `dedup.py`, `stock_act_trades.py`, `ethics_travel.py`, `staff_network.py`, `committee_witnesses.py`, `amendment_fingerprint.py`, `dark_money.py`, `planned.py` — not every symbol is a top-level `subject_type_sources` row.

**Planned** — hundreds of `planned` rows in the JSON (e.g. `pacer`, `local_campaign_finance` for most state/local types, `city_contracts`, `opensecrets` in secondary tiers). **Always** confirm `id` + `status` in `data/subject_type_sources.json` before telling another developer an adapter is live.

---

## Invariants worth knowing before changing anything

- **Credentials never appear in chat or in committed files.** Database URLs, API keys, signing keys live in environment / Render. If a prompt or a script is about to surface a secret, stop and warn.
- **CI floor is a floor, not a target.** Do not delete or skip tests to pass CI.
- **Ed25519 signing is load-bearing.** If a change touches `signing.py`, JCS canonicalization, or receipt schema, re-sign or migrate deliberately — do not invalidate existing receipts silently.
- **Epistemic labels are source-driven, not author-driven.** Do not let pattern authors set the label from opinion; it comes from the classifier / source registry.
- **"Receipts, not verdicts."** Surface the pattern, show the evidence, label the epistemic level. The system does not say "this person is corrupt." Language in rule text, UI copy, and reports must hold this line.

---

## Repo layout (abbreviated)

```
Open-Case/
├── AGENTS.md                        ← this file (start here)
├── README.md
├── main.py                          FastAPI app, lifespan, router mounts
├── models.py                        SQLAlchemy models
├── signing.py                       Ed25519 + JCS + SHA-256
├── adapters/                        Per-source adapters (base.py, cache.py, fec.py, …)
├── engines/                         pattern_engine.py, temporal_proximity.py, …
├── routes/                          FastAPI routers
├── data/
│   ├── subject_type_sources.json   Registry: subject_type → ordered adapter list
│   └── entity_aliases.json         Curated entity aliases
├── services/research_profile.py     ResearchProfile: registry → adapter list
├── scripts/                         e.g. classify_epistemic_levels.py
├── tests/
├── server/scripts/ci_pytest_floor.py
├── alembic/versions/                Migrations
└── docs/internal/PROJECT_STATE.md  Longer-form architectural + historical record
```

---

## How to update this file

- Touch `AGENTS.md` in the same PR as any change to `engines/`, `adapters/`, `data/subject_type_sources.json`, `data/reference/federal_entity_aliases.json`, `alembic/versions/`, `signing.py`, or `models.py`. CI enforces this (see `.github/workflows/agents-md-check.yml`).
- **This workflow runs on pull requests that target `main` only.** If you routinely open PRs into another default branch, extend the `on: pull_request: branches:` list there.
- Update **`Last updated`** on any edit to this file.
- Update **`Last verified`** only after running the test suite and confirming test counts and the pattern engine version match this doc.
- Keep the 60-second priming to ~300 words. If a section grows past a screen, move detail to `docs/internal/PROJECT_STATE.md` and leave a pointer.

**Limitation:** CI can tell that `AGENTS.md` was **touched**, not that it is **semantically** accurate. Deliberate reviews still matter; the `skip-agents-md` label is for PRs that truly do not change anything this doc covers.
