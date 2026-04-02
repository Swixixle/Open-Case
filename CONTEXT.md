# CONTEXT — OPEN CASE
Updated: 2026-04-01

## CURRENT STATE
Phase 5 closure gate (code): **Category 3** diagnostics print per-entry `source_name` / `entry_type` / title; assertions use **`FINANCIAL_TYPES`** / **`DECISION_TYPES`** sets for forward-compatible type names. **Receipt card** no longer emits **`og:image`** (text-only OG preview). **`check_config_warnings()`** in **`main` lifespan** logs non-fatal warnings when **`BASE_URL`** is missing or localhost, and when **`CONGRESS_API_KEY`** is missing. Signal JSON/HTML include **`is_featured`** (`weight >= 0.5`); **report view** splits **notable** vs **all other** signals. **`/static`** mount removed (placeholder image deleted).

**Definition of done** is the **ten-box checklist** in `OPEN_CASE_PHASE5_INSTRUCTIONS.md` (manual), not code alone. **Box 1**: `python -m scripts.test_todd_young` exit 0 with Category 1–4 PASS — run locally and paste PASS output below when confirmed.

Evidence: *(paste Todd Young Category 1–4 PASS output here after you run the test.)*

## NEXT ACTION
Walk the **ten-box checklist** (Phase 5 Step 4). Then Phase 6 starts with **authentication** on write paths before any public deployment.

## BLOCKED BY
Live verification: **CONGRESS_API_KEY** (+ network) to confirm the Todd Young gate; optional **FEC_API_KEY** beyond DEMO_KEY limits.

## PARKING LOT
- BullMQ async queuing (after auth)
- Indiana state legislature adapter
- Photo Tap / physical ingest
- Network graph analysis
- Social layer
- Forward to Authority packet
- Contract proximity real-data validation
- Full authentication system (Phase 6 **start**)
