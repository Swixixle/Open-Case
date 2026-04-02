import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from database import init_db
from routes.cases import router as cases_router
from routes.evidence_disambig import router as evidence_disambig_router
from routes.investigate import router as investigate_router
from routes.reporting import router as reporting_router
from routes.subjects import router as subjects_router
from signing import bootstrap_env_keys

_ROOT = Path(__file__).resolve().parent

load_dotenv(_ROOT / ".env")
bootstrap_env_keys(_ROOT)

logger = logging.getLogger(__name__)


def check_config_warnings() -> None:
    """Log warnings for common misconfiguration (non-fatal)."""
    base_url = os.getenv("BASE_URL", "")
    if not base_url or "localhost" in base_url.lower():
        logger.warning(
            "BASE_URL is not set or points to localhost. "
            "Receipt card OG tags will use localhost URLs. "
            "Set BASE_URL=https://your-domain.com in .env for public deployments."
        )

    if not os.getenv("CONGRESS_API_KEY"):
        logger.warning(
            "CONGRESS_API_KEY is not set. "
            "Congress.gov vote records will not be fetched. "
            "Get a free key at https://api.data.gov/signup/"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_config_warnings()
    init_db()
    yield


app = FastAPI(title="OPEN CASE", version="0.2.0", lifespan=lifespan)
app.include_router(cases_router)
app.include_router(investigate_router)
app.include_router(evidence_disambig_router)
app.include_router(reporting_router)
app.include_router(subjects_router)
