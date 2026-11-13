"""FastAPI application entry point for FlowForge."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings

log = logging.getLogger(__name__)
from app.db import init_db
from app.routers import (
    admin,
    auth,
    automations,
    billing,
    biz,
    connectors,
    flows,
    kb,
    keys,
    logs,
    models,
    rules,
    runs,
    shared,
    skills,
    stats,
    templates,
    tools,
    triggers,
    workspaces,
)
from app.services.runs import run_manager


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables on startup; cancel in-flight runs on shutdown."""
    init_db()
    s = get_settings()
    if not s.jwt_secret:
        log.warning(
            "JWT_SECRET is empty: sessions and stored API keys will not survive a restart (dev only)"
        )
    if "*" in s.cors_origins:
        log.warning("CORS_ORIGINS contains '*' which is ignored (credentials are enabled)")
    if s.registration == "open":
        log.info("registration is open; set REGISTRATION=first_user or closed on shared hosts")
    from app.db import SessionLocal
    from app.services.admin import ensure_admin
    from app.skills_builtin import seed_builtin_skills
    from app.templates import seed_builtin_templates

    with SessionLocal() as db:
        seed_builtin_templates(db)
        seed_builtin_skills(db)
        ensure_admin(db)
    from app.services.triggers import TriggerScheduler

    scheduler = TriggerScheduler(SessionLocal, get_settings(), run_manager.launch)
    scheduler.start()
    app.state.scheduler = scheduler
    from app.biz.scheduler import BizScheduler

    biz_scheduler = BizScheduler(SessionLocal, run_manager.launch, get_settings().biz_tick_seconds)
    biz_scheduler.start()
    app.state.biz_scheduler = biz_scheduler
    yield
    await biz_scheduler.stop()
    await scheduler.stop()
    await run_manager.shutdown()


app = FastAPI(title="FlowForge", version="0.1.0", lifespan=lifespan)

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
MAX_BODY_BYTES = 110 * 1024 * 1024  # uploads are capped at 100 MB by the upload service


@app.middleware("http")
async def security_middleware(request: Request, call_next: Callable[[Request], Any]) -> Response:
    """CSRF guard (Origin must be ours on mutating requests) + response hardening headers."""
    origin = request.headers.get("origin")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "request body too large"}, status_code=413)
    if request.method in MUTATING and origin and not request.url.path.startswith("/hooks/"):
        own = f"{request.url.scheme}://{request.url.netloc}"
        allowed = set(get_settings().cors_origin_list) | {own}
        forwarded = request.headers.get("x-forwarded-host")
        if forwarded:
            proto = request.headers.get("x-forwarded-proto", request.url.scheme)
            allowed.add(f"{proto}://{forwarded}")
        if origin not in allowed:
            return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(flows.router)
app.include_router(runs.router)
app.include_router(logs.router)
app.include_router(keys.router)
app.include_router(stats.router)
app.include_router(templates.router)
app.include_router(skills.router)
app.include_router(rules.router)
app.include_router(shared.router)
app.include_router(models.router)
app.include_router(tools.router)
app.include_router(triggers.router)
app.include_router(workspaces.router)
app.include_router(biz.router)
app.include_router(connectors.router)
app.include_router(kb.router)
app.include_router(automations.router)
app.include_router(billing.router)
app.include_router(admin.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by docker-compose and the frontend."""
    return {"status": "ok"}
