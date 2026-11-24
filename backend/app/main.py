"""FastAPI application entry point (``uvicorn app.main:app``)."""

from __future__ import annotations

from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

# Swagger UI and ReDoc load their JS/CSS from a CDN, which would break the
# no-external-requests rule, so both are off. /openapi.json stays available.
app = FastAPI(title="Permit Guardian", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Does not contact Ollama."""
    return {"status": "ok", "ollama_model": settings.ollama_model}
