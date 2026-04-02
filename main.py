from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="OPEN CASE", version="0.2.0", lifespan=lifespan)
app.include_router(cases_router)
app.include_router(investigate_router)
app.include_router(evidence_disambig_router)
app.include_router(reporting_router)
app.include_router(subjects_router)

_static = _ROOT / "static"
if _static.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")
