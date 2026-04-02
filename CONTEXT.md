# CONTEXT — OPEN CASE
Updated: 2026-04-01

## CURRENT STATE
Phase 4 verification layer is in the codebase: **Todd Young test** emits **four-category diagnostics** (`scripts/todd_young_assertions.py`); **PATCH `/api/v1/signals/{id}/expose`** lives in **reporting** and returns **400** with **`UNCONFIRMED_SIGNAL`** or **`DISMISSED_SIGNAL`** unless the signal is **confirmed** and not dismissed. **`BASE_URL`** env drives **absolute OG URLs** on the receipt card; **`/static/receipt-card-preview.png`** is mounted. Reports include **`supporting_evidence`** inline on each signal; **`GET /api/v1/signals/{id}/history`** reads **`SignalAuditLog`**. **Investigate** accepts **`proximity_days`** (default 90) and optional **`fec_committee_id`** (Todd Young fixture uses **365** + **C00459255**). **`GET /api/v1/subjects/search`** searches hardcoded Indiana officials then Congress.gov.

Run **`python -m scripts.test_todd_young`** with **`CONGRESS_API_KEY`** (+ optional **`FEC_API_KEY`**) to close Gate 1; use Step 1B in Phase 4 instructions if any category fails.

## NEXT ACTION
Run the Todd Young CLI and, if needed, follow the **Category 1–4 failure tree** in **`OPEN_CASE_PHASE4_INSTRUCTIONS.md`**. Set **`BASE_URL`** in `.env` for deployment (e.g. `https://your-app.onrender.com`).

## BLOCKED BY
Nothing in code — blocked only on **running** the live test and confirming all **ten checklist** boxes manually.

## PARKING LOT
- Social layer / multi-user
- BullMQ async queuing
- Indiana state legislature adapter
- Network graph analysis
- Game layer UI / leaderboard display
- Full authentication system
- Contract proximity real-data validation (Phase 5)
- Forward to Authority packet (Phase 5)
