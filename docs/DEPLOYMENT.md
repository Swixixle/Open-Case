# Open Case — deployment checklist

Goal: run the **same** stack GitHub CI tests (`PYTHONPATH=. pytest tests/`) with a **built** React client so reporters hit **`/app`** in the browser while the API stays on the same origin (or behind your reverse proxy).

## 1. Environment

1. Copy `.env.example` → `.env` on the server.
2. **Production:** set `ENV=production` and a public **`BASE_URL`** (no localhost) — startup enforces this for OG/receipt links.
3. **Database:** set `DATABASE_URL` to PostgreSQL for multi-instance deploys (e.g. Render). Run migrations: `alembic upgrade head`.
4. **Signing:** set `OPEN_CASE_PRIVATE_KEY` / `OPEN_CASE_PUBLIC_KEY` (Ed25519) in production; do not rely on auto-generated keys across restarts.
5. **Admin / API keys:** `ADMIN_SECRET` for privileged routes; issue investigator API keys as documented in admin routes.
6. **Optional LLM / research keys** (see `.env.example`):
   - `PERPLEXITY_API_KEY` — enrichment / research routing
   - `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` — story angles + phase-2 narrative preference in routers

## 2. Backend

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn main:app --host 0.0.0.0 --port 8000
```

Health check: `GET /health` → `{"status":"ok"}`.

## 3. Frontend (reporter web UI)

The FastAPI app mounts the built client when `client/dist` exists:

```bash
cd client
npm ci
npm run build
cd ..
# restart uvicorn so StaticFiles sees client/dist
```

- Browsing **`/app`** serves the SPA (see `main.py`).
- Set `VITE_OPEN_CASE_API_KEY` in `client/.env` at **build time** for authenticated UI calls (see `client/.env.example`).

## 4. Verification

```bash
PYTHONPATH=. pytest tests/
```

Expect **311** passing (current floor). CI should use the same `PYTHONPATH`.

## 5. GitHub ↔ local

Before deploy, confirm:

- `git status` clean on `main`
- All of `routes/assist.py`, `services/llm_router.py`, `services/perplexity_router.py`, and their tests are **committed** so production matches the analyzed codebase.
