import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI

from database import init_db
from routes.admin import router as admin_router
from routes.auth import router as auth_router
from routes.cases import router as cases_router
from routes.entity_resolution import router as entity_resolution_router
from routes.evidence_disambig import router as evidence_disambig_router
from routes.investigate import router as investigate_router
from routes.patterns import router as patterns_router
from routes.proportionality_view import router as proportionality_view_router
from routes.reporting import router as reporting_router
from routes.subjects import router as subjects_router
from routes.system import router as system_router
from core.credentials import CredentialRegistry
from signing import bootstrap_env_keys

_ROOT = Path(__file__).resolve().parent

load_dotenv(_ROOT / ".env")
bootstrap_env_keys(_ROOT)

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


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
    check_config_warnings()
    init_db()
    scheduler.add_job(
        _scheduled_enrichment_refresh,
        "interval",
        hours=24,
        id="enrichment_refresh",
        replace_existing=True,
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="OPEN CASE", version="0.2.0", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(cases_router)
app.include_router(entity_resolution_router)
app.include_router(investigate_router)
app.include_router(patterns_router)
app.include_router(proportionality_view_router)
app.include_router(evidence_disambig_router)
app.include_router(reporting_router)
app.include_router(subjects_router)
app.include_router(system_router)
