"""FastAPI application entry point (``uvicorn app.main:app``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlmodel import Session

from app.config import get_settings
from app.db import init_db, make_engine
from app.procedure import sync_clauses

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = make_engine(settings.db_path)
    init_db(engine)
    with Session(engine) as session:
        sync_clauses(session)
    app.state.engine = engine
    yield


# Swagger UI and ReDoc load their JS/CSS from a CDN, which would break the
# no-external-requests rule, so both are off. /openapi.json stays available.
app = FastAPI(title="Permit Guardian", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Does not contact Ollama."""
    return {"status": "ok", "ollama_model": settings.ollama_model}
