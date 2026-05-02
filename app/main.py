"""FastAPI entry point. Wires routers, lifespan, logging, and `/healthz`."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis_async
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.api import webhooks
from app.config import settings
from app.db.session import engine
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging()
    logger.info("polier-pilot starting (env=%s, version=%s)", settings.app_env, __version__)
    app.state.redis = redis_async.from_url(settings.redis_url, decode_responses=True)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await engine.dispose()
        logger.info("polier-pilot stopped")


app = FastAPI(
    title="Polier-Pilot",
    version=__version__,
    description="AI-first construction site reporting for German FTTH/Tiefbau.",
    lifespan=lifespan,
)

app.include_router(webhooks.router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"app": settings.app_name, "version": __version__}


@app.get("/healthz", tags=["meta"])
async def healthz() -> JSONResponse:
    """Liveness + dependency readiness check.

    Returns 200 only when both Postgres and Redis respond. Used by Docker
    healthchecks and (later) Kubernetes probes.
    """
    checks: dict[str, Any] = {"app": "ok"}
    overall_ok = True

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"
        overall_ok = False

    try:
        await app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc.__class__.__name__}"
        overall_ok = False

    status = 200 if overall_ok else 503
    return JSONResponse(
        status_code=status,
        content={"status": "ok" if overall_ok else "degraded", "checks": checks},
    )
