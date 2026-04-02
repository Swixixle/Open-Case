# CONTEXT — OPEN CASE
Updated: 2026-04-01

## CURRENT STATE
**Phase 6 (in progress):** **Write routes** require `Authorization: Bearer open_case_<hex>` minted at **`POST /api/v1/auth/keys?handle=...`**. Body handles (`created_by`, `investigator_handle`, etc.) must match the authenticated handle. **GET** (reports, signals list, subjects search, case read) stays **public**. Investigator row gains **`hashed_api_key`** / **`api_key_created_at`** (Alembic `c901d4e2f8ab`).

**BASE_URL hardening:** If **`ENV=production`**, startup **exits** when **`BASE_URL`** is empty or contains `localhost`. Otherwise only **warns**. **`CONGRESS_API_KEY`** missing is always a warning.

**Closure automation:** On Todd Young **PASS**, **`scripts/test_todd_young`** writes **`PHASE5_CLOSURE.md`** (fill idempotency + checklist after **`python -m scripts.test_idempotency`** and manual boxes). Category 3 stderr groups evidence **by adapter/source** for FEC vs Congress triage.

**Phase 5 product deltas** (unchanged): Category 3 type sets, no **`og:image`** on receipt, **`is_featured`**, report HTML split, no **`/static`**.

**Definition of done** remains the **ten-box checklist** + **PHASE5_CLOSURE.md** signed off. **Box 1**: `python -m scripts.test_todd_young` exit 0 — mints a key for `gate-runner` automatically.

Evidence: *(paste Todd Young Category 1–4 PASS output here after you run the test.)*

## NEXT ACTION
1. Run **`OPEN_CASE_PHASE6_INSTRUCTIONS.md`** Part 1 (Todd Young PASS → closure stub).  
2. With **`uvicorn`** running, **`python -m scripts.test_idempotency`** → paste counts into **`PHASE5_CLOSURE.md`**.  
3. Manual ten-box + **ENV=production** smoke on BASE_URL.

## BLOCKED BY
Live verification: **CONGRESS_API_KEY** (+ network); app + idempotency script must share the same **`open_case.db`** path.

## PARKING LOT
- BullMQ async queuing (after auth)
- Indiana state legislature adapter
- Photo Tap / physical ingest
- Network graph analysis
- Social layer
- Forward to Authority packet
- Contract proximity real-data validation
- Full authentication system (Phase 6 **start**)
