# Open Case — project canon

Stable reference for what this repository **is** and **is not**, so future sessions do not drift. For moving technical inventory (test counts, adapter lists), see `AGENTS.md` at repo root and update it when those facts change.

---

## 1. What Open Case IS

- **Political accountability / government transparency investigation engine** — not a consumer app or game.
- **Ingests public records** through configurable adapters (e.g. FEC, Congress.gov / Senate votes, Senate LDA, USASpending, GovInfo paths, Indiana/Indianapolis pilots such as IDIS and local contracts/procurement). The exact set per subject type lives in `data/subject_type_sources.json`.
- **Runs deterministic pattern rules** (pattern engine v2.7 in code; enumerated in `PATTERN_RULE_IDS` in `engines/pattern_engine.py`). Rules surface structure, timing, and proximity in records — they are **not** LLM opinions.
- **Produces Ed25519-signed cryptographic receipts** (JCS canonicalization, SHA-256 digest) so third parties can verify frozen payloads.
- **Philosophy: “Receipts, not verdicts.”** The system documents what public records show together (including donation–vote timing/proximity where data exists); it does not judge intent or legal guilt.
- **Epistemic labeling** — every finding is tagged from **source type**, not from model speculation: `VERIFIED`, `REPORTED`, `ALLEGED`, `DISPUTED`, `CONTEXTUAL` (see `AGENTS.md` / `services/epistemic_classifier.py`). Adapter coverage is separately tracked as **implemented** vs **planned** in the subject-type registry.

---

## 2. What Open Case is NOT

- **NOT a corruption detector or verdict engine** — no automated “guilty” output.
- **NOT a source of AI-generated accusations** — narrative or copy must not substitute for cited records and labels.
- **NOT a replacement for investigative journalism** — it prepares defensible material; humans interpret and publish.
- **NOT making claims of wrongdoing** — proximity and timing are documented; causation and criminality are out of scope for the engine.
- **NOT for non–public / private investigations** — design center is public records and ethical civic use.
- **Core principle:** We document **proximity and timing** in public data. **Reporters and editors verify** before any public claim.

---

## 3. Technical stack

- **Backend:** Python, **FastAPI**, **SQLAlchemy** — **SQLite by default** locally; **PostgreSQL** typical in production (see `.env.example` / deployment notes).
- **Frontend:** **React**, **Vite**; styling includes **Tailwind** in the client tree.
- **Data layer:** Many adapter modules under `adapters/`; research routing via `services/research_profile.py` and the JSON registry — not a fixed “N adapters” forever; count grows with subject types.
- **Signing:** **Ed25519** + **JCS** (see `signing.py`).
- **Pattern engine:** **18** active rule IDs in `PATTERN_RULE_IDS` (v2.7); more rules may be spec’d or partial elsewhere — the set in `engines/pattern_engine.py` is authoritative.
- **Deployment:** Documented target includes **Render** (budget on the order of ~$200/month is a project constraint, not a guarantee).

---

## 4. Current state (as of 2026-04-26)

- **Automated tests:** **392** tests collected (`PYTHONPATH=. pytest tests/ --co -q`). CI enforces a **minimum pass floor** (`server/scripts/ci_pytest_floor.py`, currently ≥201 passed). A full local green run expects proper test env (e.g. `OPEN_CASE_TESTING=1`, valid signing/material where tests require it).
- **Search** — works against configured APIs (e.g. Congress-facing paths) when keys and network allow.
- **Investigation endpoint** — backend pipeline (ingest → evidence → signals → pattern engine → seal) is the documented happy path when credentials and DB are set.
- **Documentation exemplars:** **Tom Cotton** / **SOFT_BUNDLE** (~0.921 score appears in docs as a calibration reference; live scores depend on data and run completion). **Todd Young** is cited in `AGENTS.md` as a representative deep run (hundreds of evidence rows, dozens of signals — exact counts depend on keys, cache, and network).
- **Demo / product readiness:** Backend E2E has been verified in prior sessions; **frontend demo-readiness** (full journalist read-only path in `client/`) remains an active focus per `AGENTS.md`. Seeding **5–7 senators** (or comparable subjects) for a stable demo cohort is a practical gap vs one-off manual runs.
- **Operational friction:** Manual one-at-a-time investigation workflow and **API key / env configuration** confusion are known pain points for new setups.

---

## 5. Forbidden changes

- **Never remove or bypass Ed25519 signing** for load-bearing receipts without a deliberate migration plan.
- **Never turn the engine into an AI verdict layer** — no auto-accusations; epistemics stay source-driven.
- **Never imply court-proven corruption** without actual court / primary record support in the evidence and correct labels.
- **Never “simplify” by stripping epistemic rigor** — weakening labels, skipping provenance, or hiding uncertainty is out of scope.
- **Never auto-publish findings to the public** as if they were final — **human journalist / editorial review** is assumed for anything that reads like a publishable allegation.

---

## 6. Developer context

- **Maintainer:** Alex Maksimovich (background in respiratory therapy; civic-tech builder).
- **Tooling:** Much of the codebase was developed with **Cursor / Claude-style** assistance; treat AI suggestions as drafts until they match this canon and `AGENTS.md`.
- **Budget:** Roughly **~$200/month** operational ceiling is a design constraint for hosting and APIs.
- **Credentials:** **Never commit secrets.** Keys and `DATABASE_URL` live in **environment** / host config (start from `.env.example`). Some workflows use a **local-only** directory such as `~/.open_case_credentials/` for key material — that path is **not** part of the repo; standard onboarding is still `.env`.
- **Philosophy:** Treat Open Case as **procedural trust infrastructure** — reproducible steps, signed artifacts, and honest limits on what the system claims.
