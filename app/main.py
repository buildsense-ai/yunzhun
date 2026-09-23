"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import init_db
from .mail.imap import MailError
from .routers import accounts, folders, inbound, judgments, messages, objects, sending, stores

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yunzhun")

settings = get_settings()


async def _background_sync() -> None:
    if not settings.sync_enabled:
        log.info("background sync disabled")
        return
    from .services.sync import sync_all_accounts

    await asyncio.sleep(2)  # let the server finish booting first
    while True:
        try:
            stats = await asyncio.to_thread(sync_all_accounts)
            log.info("sync round done: %s", stats)
            # event-driven: new mail activates the pipeline immediately,
            # no waiting for the next pipeline tick
            if any(s.get("new_messages") for s in stats):
                from .services.pipeline import process_pending

                pstats = await asyncio.to_thread(process_pending)
                if pstats.get("pulled"):
                    log.info("sync-triggered pipeline: %s", pstats)
        except Exception:  # noqa: BLE001
            log.exception("background sync round failed")
        await asyncio.sleep(settings.sync_interval_seconds)


async def _background_pipeline() -> None:
    """Fetch body -> Jev judge -> confidence-gated pull, for every pending mail."""
    if not settings.pipeline_enabled:
        log.info("background pipeline disabled")
        return
    from .services.pipeline import process_pending

    await asyncio.sleep(5)
    while True:
        try:
            stats = await asyncio.to_thread(process_pending)
            if stats.get("pending") or stats.get("pulled"):
                log.info("pipeline round: %s", stats)
        except Exception:  # noqa: BLE001
            log.exception("background pipeline round failed")
        await asyncio.sleep(settings.pipeline_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_background_sync())
    pipeline_task = asyncio.create_task(_background_pipeline())
    idle_mgr = None
    if settings.idle_enabled:
        from .services.idle import IdleManager

        idle_mgr = IdleManager()
        idle_mgr.start()
    log.info("%s started", settings.app_name)
    yield
    task.cancel()
    pipeline_task.cancel()
    if idle_mgr:
        idle_mgr.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router)
app.include_router(inbound.router)
app.include_router(folders.router)
app.include_router(messages.router)
app.include_router(objects.router)
app.include_router(sending.router)
app.include_router(judgments.router)
app.include_router(stores.router)


@app.exception_handler(MailError)
async def mail_error_handler(request: Request, exc: MailError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"upstream mail error: {exc}"})


@app.exception_handler(LookupError)
async def not_found_handler(request: Request, exc: LookupError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
