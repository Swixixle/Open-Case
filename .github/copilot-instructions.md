# GitHub Copilot — repository context

This repo is **OPEN CASE**: FastAPI + SQLAlchemy civic investigation tooling. Public records in; signed case files and shareable **receipts** out.

## Before you change code
- Read **`PHILOSOPHY.md`** (receipts, not verdicts) and **`CONTRIBUTING.md`** → **The Non-Negotiables**.
- Read **`ARCHITECTURE.md`** for the investigation pipeline, adapters, engines, atomic `investigate`, and signal identity upsert.

## Conventions
- **Adapters** (`adapters/`): implement `BaseAdapter.search`; never let exceptions escape — return `AdapterResponse(found=False, error=...)`.
- **Investigation**: `routes/investigate.py` — single transaction commit; use `upsert_signal` from `signals/dedup.py` for signals.
- **Expose**: `PATCH /api/v1/signals/{id}/expose` lives in `routes/reporting.py` — requires **confirmed** signal (`UNCONFIRMED_SIGNAL` if not).
- **Collisions**: multiple entity matches → `unverified` + flag for disambiguation (defamation guard).

## Docs for humans
- **`README.md`**: setup, capabilities, troubleshooting.
- **`CONTEXT.md`**: current phase and next actions (maintainer snapshot).

When unsure, prefer documenting a data source in an issue over guessing adapter behavior.
