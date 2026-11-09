"""FastAPI application entry point for FlowForge."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db
from app.routers import flows, logs, runs
from app.services.runs import run_manager


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables on startup; cancel in-flight runs on shutdown."""
    init_db()
    yield
    await run_manager.shutdown()


app = FastAPI(title="FlowForge", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(flows.router)
app.include_router(runs.router)
app.include_router(logs.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by docker-compose and the frontend."""
    return {"status": "ok"}
