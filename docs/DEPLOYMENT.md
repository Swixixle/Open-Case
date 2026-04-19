# Open Case — deployment checklist

**Goal:** Deploy the same codebase that GitHub CI tests, with a built React client so reporters hit **`/app`** in the browser while the API serves on the same origin.

**CI status:** Both Python backend and Vite client are tested on every push/PR (see `.github/workflows/ci.yml`).

---

## 1. Environment variables

Copy `.env.example` → `.env` on the server. Never commit real secrets.

### Required for production

| Variable | Purpose |
|----------|---------|
| `ENV=production` | Enables production-mode checks |
| `BASE_URL` | Public site URL (no trailing slash) — enforced non-localhost in production for OG tags and receipt links |
| `DATABASE_URL` | SQLite (`sqlite:///./open_case.db`) for single-instance; `postgres://...` for multi-instance (e.g., Render) |
| `OPEN_CASE_PRIVATE_KEY` | Ed25519 private key for receipt signing (PEM/base64 per `signing.py`) |
| `OPEN_CASE_PUBLIC_KEY` | Ed25519 public key for verification |
| `ADMIN_SECRET` | Credentials for privileged routes (admin, entity resolution, cache flush) |

**Note:** Keys are auto-generated on first boot if missing, but **you must set them explicitly in production** to avoid invalidation across restarts.

### Required for adapter functionality

| Variable | Purpose |
|----------|---------|
| `FEC_API_KEY` | Campaign finance data (DEMO_KEY works but is rate-limited) |
| `CONGRESS_API_KEY` | Congress.gov via api.data.gov (member search, vote matching) |

### Optional API keys

| Variable | Purpose |
|----------|---------|
| `PERPLEXITY_API_KEY` | Enrichment / research routing (`services/perplexity_router.py`) |
| `GEMINI_API_KEY` | Story angles assist (`routes/assist.py`) — tiered Gemini → Claude routing |
| `ANTHROPIC_API_KEY` | Phase-2 narrative preference in LLM routing |
| `REGULATIONS_GOV_API_KEY` | Regulations.gov adapter |
| `GOVINFO_API_KEY` | GovInfo collections/packages adapter |
| `LDA_API_KEY` | Lobbying Disclosure Act API (reserved; Senate LDA is public without key) |

### Optional runtime config

| Variable | Purpose |
|----------|---------|
| `PROPORTIONALITY_API_URL` | EthicalAlt proportionality API (signals work if unreachable) |
| `SKIP_EXTERNAL_PROPORTIONALITY=1` | Skip HTTP to EthicalAlt in CI/tests |
| `BUST_CACHE=1` | Skip reading adapter HTTP cache (still writes fresh responses) |

---

## 2. Database

### SQLite (single-instance / development)

Default when `DATABASE_URL` is unset:

```bash
sqlite:///./open_case.db
```

### PostgreSQL (multi-instance / production)

```bash
# Set in .env
DATABASE_URL=postgres://user:pass@host:5432/dbname

# Run migrations
alembic upgrade head
```

**Note on multi-instance deployments:** Migrations run in the startup lifecycle (`main.py:lifespan`), which creates a race condition when multiple instances restart simultaneously. For single-instance deployments, this is acceptable. For multi-instance production, run migrations as a pre-deploy step.

---

## 3. Backend

```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start server
uvicorn main:app --host 0.0.0.0 --port 8000
```

**Health check:** `GET /health` → `{"status":"ok"}`

**Startup checks:**
- Validates `BASE_URL` is not localhost in production (hard fail)
- Warns if `CONGRESS_API_KEY` is missing (non-fatal; limits member search / vote matching)
- Bootstraps signing keys if missing (but see note above about production)
- Starts APScheduler for daily enrichment refresh

---

## 4. Frontend (reporter web UI)

The FastAPI app mounts the built client at **`/app`** when `client/dist` exists (`main.py:147-153`):

```bash
cd client
npm ci
npm run build
cd ..
# restart uvicorn so StaticFiles sees the new client/dist
```

**Build config:**
- `client/vite.config.js`: `base: "/app/"` in production mode
- `client/.env`: Set `VITE_OPEN_CASE_API_KEY` at build time for authenticated UI calls
- Optional: `VITE_OPEN_CASE_API_BASE` to override API origin (default: same-origin)

**Dev mode:** `npm run dev` proxies `/api` to `https://open-case.onrender.com` (update for your setup).

---

## 5. Verification

### Test suite

```bash
PYTHONPATH=. pytest tests/
```

**Full suite:** Run `PYTHONPATH=. pytest tests/` locally — **344** tests as of last README verification (count changes as tests are added).

**CI regression floor:** `.github/workflows/ci.yml` runs `python server/scripts/ci_pytest_floor.py`, which requires **≥ 201** tests passed (see `REGRESSION_FLOOR` in that script). That floor is a **minimum bar**, not the full suite count.

### CI workflow (`.github/workflows/ci.yml`)

**Python job:**
```yaml
- python-version: "3.12"
- pip install -r requirements.txt
- python -m compileall -q .  # byte-compile syntax check
- python server/scripts/ci_pytest_floor.py  # enforces ≥201 passed
```

**Client job:**
```yaml
- node-version: "20"
- npm ci && npm run build  # Vite production bundle
```

---

## 6. Pre-deploy checklist

Before deploying:

```bash
# Confirm clean working tree
git status

# If ahead of origin, publish
git push origin main

# Verify CI passes on GitHub
# Ensure all new routes/services are committed:
#   routes/assist.py, services/llm_router.py, services/perplexity_router.py
#   and their tests
```

---

## 7. Deployment targets

### Render (single-instance)

Open Case is designed for Render deployment:
- **Web service:** `uvicorn main:app` with persistent disk for SQLite
- **Static files:** `client/dist` mounted at `/app`
- **Environment:** Set all required vars in Render dashboard
- **Disk:** Default credential storage at `/data/.credentials` (optional; set `CREDENTIAL_DATA_DIR`)

**Note:** No `render.yaml` in repo — configure via Render dashboard.

### Self-hosted (single instance)

```bash
# Systemd service example
[Unit]
Description=Open Case API
After=network.target

[Service]
User=open-case
WorkingDirectory=/opt/open-case
Environment="PATH=/opt/open-case/.venv/bin"
ExecStart=/opt/open-case/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

### Multi-instance (PostgreSQL)

1. Set `DATABASE_URL=postgres://...`
2. Run `alembic upgrade head` as pre-deploy step (not in app startup)
3. Ensure shared signing keys across instances
4. Use reverse proxy (nginx, Caddy) for load balancing

---

## 8. Post-deploy verification

```bash
# Health check
curl https://your-domain.com/health

# Verify client serves
curl https://your-domain.com/app

# Test authenticated route (if API key set)
curl -H "Authorization: Bearer YOUR_API_KEY" \
     https://your-domain.com/api/v1/subjects/search

# Verify receipt signing works
# Create a test case and check signed_hash is present
```

---

## 9. Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `BASE_URL` hard fail on startup | `ENV=production` with localhost/empty `BASE_URL` |
| No client at `/app` | `client/dist` missing — run `npm run build` |
| Receipt verification fails | Keys regenerated — restore from backup or re-sign all cases |
| Adapter returns empty results | API key missing/rate-limited — check logs for `found=False` |
| `CONGRESS_API_KEY` warning | Non-fatal; member search and vote matching limited |

---

## 10. Key rotation

Regenerating `OPEN_CASE_PRIVATE_KEY` invalidates all prior seals. To rotate:

1. Backup current keys
2. Generate new keypair
3. Run full re-signing migration (see `scripts/` for re-sign utilities)
4. Update `OPEN_CASE_PUBLIC_KEY` for verifiers

---

**Questions?** Open a Discussion on GitHub or check `ARCHITECTURE.md` for system design details.
