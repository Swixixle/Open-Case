import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

logger.info("Python version: %s", sys.version)
_db_preview = os.getenv("DATABASE_URL", "NOT SET")
logger.info(
    "DATABASE_URL prefix: %s",
    _db_preview[:20] if _db_preview != "NOT SET" else "NOT SET",
)

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import init_db
from routes.admin import router as admin_router
from routes.assist import router as assist_router
from routes.auth import router as auth_router
from routes.cases import router as cases_router
from routes.demo import router as demo_router
from routes.entity_resolution import router as entity_resolution_router
from routes.evidence_disambig import router as evidence_disambig_router
from routes.findings import router as findings_router
from routes.investigate import router as investigate_router
from routes.narrative import router as narrative_router
from routes.patterns import router as patterns_router
from routes.proportionality_view import router as proportionality_view_router
from routes.reporting import router as reporting_router
from routes.subjects import router as subjects_router
from routes.system import router as system_router
from core.credentials import CredentialRegistry
from signing import bootstrap_env_keys

bootstrap_env_keys(_ROOT)

scheduler = AsyncIOScheduler()

# Set True by tests/conftest.py before TestClient (explicit; does not depend on import order).
_testing_mode_skip_scheduler: bool = False


def set_testing_mode(enabled: bool) -> None:
    """
    When True, lifespan skips all APScheduler setup. Call from tests immediately before
    TestClient(main.app) (see tests/conftest.py).
    """
    global _testing_mode_skip_scheduler
    _testing_mode_skip_scheduler = enabled


def _env_open_case_testing() -> bool:
    """OPEN_CASE_TESTING=1 from conftest / CI before pytest imports main."""
    v = os.getenv("OPEN_CASE_TESTING", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _env_scheduler_disabled() -> bool:
    """True when DISABLE_SCHEDULER requests skipping the scheduler (tests/CI)."""
    v = os.getenv("DISABLE_SCHEDULER", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _scheduler_disabled() -> bool:
    """
    Skip APScheduler when any test/CI signal is present:

    - explicit ``set_testing_mode(True)`` (before TestClient)
    - ``OPEN_CASE_TESTING`` or ``DISABLE_SCHEDULER`` env (conftest / ci_pytest_floor / Actions)
    - ``pytest`` in ``sys.modules`` (fallback when env is missing)
    """
    if _testing_mode_skip_scheduler:
        return True
    if _env_open_case_testing():
        return True
    if _env_scheduler_disabled():
        return True
    return "pytest" in sys.modules


def _scheduled_enrichment_refresh() -> None:
    try:
        from services.enrichment_service import enqueue_stale_enrichment

        enqueue_stale_enrichment()
    except Exception:
        logger.exception("Scheduled enrichment refresh failed.")


def check_config_warnings() -> None:
    """
    BASE_URL: hard fail in production if missing or localhost; warn in development.
    CONGRESS_API_KEY: non-fatal warning when missing (Congress.gov member search / better vote matching).
    """
    env = os.getenv("ENV", "development").lower()
    base_url = os.getenv("BASE_URL", "")
    bad_base = not base_url or "localhost" in base_url.lower()

    if bad_base:
        if env == "production":
            logger.error(
                "BASE_URL is not set to a production URL. "
                "Cannot start in production mode with localhost or empty BASE_URL. "
                "Receipt card OG tags would break public shares. "
                "Set BASE_URL=https://your-domain.com or ENV=development."
            )
            sys.exit(1)
        logger.warning(
            "BASE_URL is not set or points to localhost. "
            "Receipt card OG tags will use localhost URLs. "
            "Set BASE_URL=https://your-domain.com before public deployment."
        )

    if not CredentialRegistry.get_credential("congress"):
        logger.warning(
            "CONGRESS_API_KEY is not set. "
            "Congress.gov member search and name-based vote matching will be limited. "
            "Get a free key at https://api.data.gov/signup/"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        check_config_warnings()
        logger.info("Running database migrations and startup hooks.")
        init_db()
        scheduler_disabled = _scheduler_disabled()
        logger.info(
            "Scheduler gate: testing_mode=%s OPEN_CASE_TESTING=%r DISABLE_SCHEDULER=%r "
            "env_testing=%s env_sched_off=%s pytest_loaded=%s disabled=%s",
            _testing_mode_skip_scheduler,
            os.getenv("OPEN_CASE_TESTING"),
            os.getenv("DISABLE_SCHEDULER"),
            _env_open_case_testing(),
            _env_scheduler_disabled(),
            "pytest" in sys.modules,
            scheduler_disabled,
        )
        if scheduler_disabled:
            logger.info(
                "Scheduler disabled (testing mode / env / pytest); skipping background jobs."
            )
        else:
            scheduler.add_job(
                _scheduled_enrichment_refresh,
                "interval",
                hours=24,
                id="enrichment_refresh",
                replace_existing=True,
            )
            try:
                scheduler.start()
                logger.info("Scheduler started")
            except Exception as e:
                logger.warning(
                    "Scheduler failed to start, continuing without it: %s",
                    e,
                )
        logger.info("Application startup complete.")
    except Exception:
        logger.exception("Application startup failed")
        raise
    try:
        yield
    finally:
        if not _scheduler_disabled():
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                logger.debug("Scheduler shutdown skipped or failed", exc_info=True)


app = FastAPI(title="OPEN CASE", version="0.2.0", lifespan=lifespan)

_cors_origins = ["http://localhost:5173"]
_extra_cors = os.getenv("OPEN_CASE_CORS_ORIGINS", "").strip()
if _extra_cors:
    _cors_origins.extend(
        o.strip() for o in _extra_cors.split(",") if o.strip() and o.strip() not in _cors_origins
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(admin_router)
app.include_router(assist_router)
app.include_router(auth_router)
app.include_router(cases_router)
app.include_router(demo_router)
app.include_router(entity_resolution_router)
app.include_router(findings_router)
app.include_router(investigate_router)
app.include_router(narrative_router)
app.include_router(patterns_router)
app.include_router(proportionality_view_router)
app.include_router(evidence_disambig_router)
app.include_router(reporting_router)
app.include_router(subjects_router)
app.include_router(system_router)

_client_dist = _ROOT / "client" / "dist"
if _client_dist.is_dir():
    app.mount(
        "/app",
        StaticFiles(directory=str(_client_dist), html=True),
        name="client",
    )
