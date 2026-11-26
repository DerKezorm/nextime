"""Entry point: the FastAPI app, routers, background tasks, the built frontend."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, private
from .config import get_settings
from .db import SessionLocal, init_db
from .errors import detail
from .middleware import ForwardedProtoMiddleware, GuardMiddleware, RequestContextMiddleware, unhandled_error
from .routers import about, auth, health, invites, oidc, server
from .routers import accounts as accounts_router
from .routers import api_v1 as api_v1_router
from .routers import app1 as app1_router
from .routers import app_pairing as app_pairing_router
from .routers import attachments as attachments_router
from .routers import avatars as avatars_router
from .routers import backups as backups_router
from .routers import calendars as calendars_router
from .routers import collections as collections_router
from .routers import dav as dav_router
from .routers import devices as devices_router
from .routers import events as events_router
from .routers import keys as keys_router
from .routers import live as live_router
from .routers import locales as locales_router
from .routers import logs as logs_router
from .routers import mcp as mcp_router
from .routers import nexfolk as nexfolk_router
from .routers import nexpaper as nexpaper_router
from .routers import nextasks as nextasks_router
from .routers import notify as notify_router
from .routers import offline as offline_router
from .routers import passkeys as passkeys_router
from .routers import places as places_router
from .routers import reminders as reminders_router
from .routers import search as search_router
from .routers import server_signin as server_signin_router
from .routers import settings as settings_router
from .routers import shares as shares_router
from .routers import sources as sources_router
from .routers import totp as totp_router
from .routers import transfer as transfer_router
from .routers import trash as trash_router
from .routers import verbinden as verbinden_router
from .security import HashingBusy, purge_sessions
from .services import (
    apppush,
    backups,
    cleanup,
    locales,
    logs,
    nextasks,
    oidc_store,
    post_store,
    settings_service,
    sync,
    totp,
    verbinden_store,
)
from .services.konten_store import KontenStore
from .vendor import nexoidc
from .vendor.nexkonten import factor as konten_factor
from .vendor.nexkonten import setup as konten_setup

logger = logging.getLogger("nextime")

#: The frame's routers. The calendar features add theirs here (``FEATURE_ROUTERS``), each a module with ``router``.
ROUTERS = [
    health, about, locales_router, logs_router, auth, totp_router, passkeys_router, oidc,
    accounts_router, invites, settings_router,
    backups_router, avatars_router, server, server_signin_router,
]
#: Routers of the calendar features (calendars, events, sharing ...), registered after the frame's.
FEATURE_ROUTERS: list = [calendars_router, collections_router, events_router, devices_router, live_router, dav_router,
                         sources_router, shares_router, notify_router, reminders_router,
                         places_router, attachments_router, search_router, trash_router, offline_router,
                         transfer_router, keys_router, api_v1_router, mcp_router, nextasks_router,
                         nexpaper_router, app_pairing_router, app1_router, verbinden_router, nexfolk_router]


def _read_log_mode() -> tuple[str, datetime | None]:
    with SessionLocal() as db:
        mode = settings_service.get(db, "log_mode")
        raw = settings_service.get(db, "log_mode_until")
    until = datetime.fromisoformat(raw).astimezone(UTC) if raw else None
    return str(mode or logs.DEFAULT_MODE), until


def _write_log_mode(mode: str, until: datetime | None) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"log_mode": mode, "log_mode_until": until.isoformat() if until else None})


async def _sweep_forever(stop: asyncio.Event) -> None:
    """Enrolments and sign-ins waiting for their second factor run out; what ran out goes from memory, ended
    sessions from the database once an hour (they stayed until someone showed them again)."""
    rounds = 0
    while not stop.is_set():
        totp.sweep()
        # System mail waiting in the outbox goes out (the shared post module), in a thread: SMTP blocks.
        try:
            await asyncio.to_thread(post_store.run_due)
        except Exception:
            logger.exception("Outbox round failed")
        if rounds % 60 == 0:
            with SessionLocal() as db:
                purge_sessions(db)
        rounds += 1
        try:
            await asyncio.wait_for(stop.wait(), 60)
        except TimeoutError:
            continue


def _backup_before_update() -> None:
    """A new version starting on data of an older one: a backup first, the way back if the new version goes wrong.
    Nothing to keep on a fresh data folder."""
    from sqlalchemy import inspect

    from .db import engine

    if not inspect(engine).has_table("settings"):
        return
    with SessionLocal() as db:
        known = str(settings_service.get(db, "running_version") or "")
    if known and known != __version__:
        logger.info("nextime %s starts on data of %s; a backup comes first", __version__, known)
        backups.create(kind=backups.UPDATE, note=f"before {__version__}")


def _remember_version() -> None:
    with SessionLocal() as db:
        if settings_service.get(db, "running_version") != __version__:
            settings_service.save(db, {"running_version": __version__})


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logs.setup()
    settings = get_settings()
    # The shared backup module on this data folder (a test may have moved it since the import).
    backups.configure()
    # What a stopped copy left (hidden parts, an empty file under a backup's name) goes at once.
    backups.tidy_at_start()
    # Said at every start while it lasts: a database on a share can lose events.
    backups.warn_at_start()
    backups.apply_pending()
    _backup_before_update()
    init_db()
    # A restore that failed at this start: the operators hear it once (the log; the page says it too).
    backups.report_failed_restore()
    with SessionLocal() as db:
        nextasks.move_choices(db)
    _remember_version()
    # The TLS context of the calls to providers and authentik, read once here outside the event loop (vendor/nexoidc).
    await nexoidc.warm()
    private.tighten_all()
    logs.attach_store(_read_log_mode, _write_log_mode)
    logs.apply_stored_mode()
    for directory in (settings.locales_dir, backups.media_root()):
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
    locales.at_start()
    added = locales.available()
    if added:
        logger.info("Languages added by the operator: %s", ", ".join(locale.code for locale in added))
    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    settings.resolved_secret_key()
    if not settings.disable_background:
        tasks.append(asyncio.create_task(logs.run_forever(stop)))
        tasks.append(asyncio.create_task(backups.run_forever(stop)))
        tasks.append(asyncio.create_task(cleanup.run_forever(stop)))
        tasks.append(asyncio.create_task(_sweep_forever(stop)))
        # The silent nudges to the iPhone app (TIME-29).
        tasks.append(asyncio.create_task(apppush.run_forever(stop)))
        # iCloud, Google, CalDAV servers, subscriptions and holidays (TIME-15 to TIME-17).
        tasks.append(asyncio.create_task(sync.run_forever(stop)))
        # Ticks of nextasks tasks that wait for nextasks (TIME-25 EC-12).
        tasks.append(asyncio.create_task(nextasks.run_forever(stop)))
        # Revocations another nex app has not heard yet (the shared module verbinden).
        tasks.append(asyncio.create_task(verbinden_store.run_forever(stop)))
    logger.info("nextime %s started sqlite=%s", __version__, sqlite3.sqlite_version)
    with SessionLocal() as db:
        konten_setup.announce_setup(KontenStore(db))
    # NEXTIME_SECOND_FACTOR_OFF: a warning in the log while the emergency hammer holds.
    konten_factor.announce_switched_off()
    try:
        yield
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("nextime stopped")


app = FastAPI(
    title="nextime",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs" if get_settings().api_docs else None,
    openapi_url="/api/openapi.json" if get_settings().api_docs else None,
    redoc_url=None,
)
# Added last, runs first: the request id and headers wrap everything, the guard sits inside it.
app.add_middleware(GuardMiddleware)
app.add_middleware(RequestContextMiddleware)
# Outermost: the scheme a trusted proxy reports is known before anything looks at the request.
app.add_middleware(ForwardedProtoMiddleware)


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [".".join(str(part) for part in error.get("loc", ()) if part != "body") for error in exc.errors()]
    return JSONResponse(
        status_code=422, content={"detail": detail("invalid_input", "The input is not valid.", fields=fields)}
    )


@app.exception_handler(OverflowError)
async def _too_large_a_number(_request: Request, _exc: OverflowError) -> JSONResponse:
    # A number past what SQLite holds (an id like 99999999999999999999 in the address): not a fault of the server.
    return JSONResponse(status_code=422, content={"detail": detail("invalid_input", "The input is not valid.")})


@app.exception_handler(HashingBusy)
async def _hashing_busy(_request: Request, _exc: HashingBusy) -> JSONResponse:
    # Every slot for checking a password stayed taken for 15 s: many sign-ins at once, as in a flood of guesses.
    return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                        content={"detail": detail("busy", "The server is busy. Try again in a moment.")})


@app.exception_handler(UnicodeEncodeError)
async def _not_unicode(_request: Request, _exc: UnicodeEncodeError) -> JSONResponse:
    # JSON may carry a lone surrogate ("\ud800"); no file name, path or database text can hold it.
    return JSONResponse(status_code=422, content={"detail": detail("invalid_input", "The input is not valid.")})


@app.exception_handler(StarletteHTTPException)
async def _plain_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # FastAPI's own 400 for a body that is not JSON carries only a text; give it a code like every other answer.
    if exc.status_code == 400 and isinstance(exc.detail, str):
        return JSONResponse(status_code=400, content={"detail": detail("invalid_input", "The input is not valid.")})
    # A file under /assets/ that is not there (an old page after an update), or a path no route knows: the same
    # answer as every other "not found", not Starlette's bare text.
    if exc.status_code == 404 and isinstance(exc.detail, str):
        return JSONResponse(status_code=404, content={"detail": detail("not_found", "Not found.")})
    return await http_exception_handler(request, exc)


app.add_exception_handler(Exception, unhandled_error)

for module in ROUTERS + FEATURE_ROUTERS:
    app.include_router(module.router)
# Google's way back after "Continue to Google", and the operator's Google set-up (TIME-16).
app.include_router(sources_router.callback_router)
app.include_router(sources_router.server_router)
app.include_router(settings_router.mail_router)
app.include_router(keys_router.connections_router)
app.include_router(sources_router.invitations_router)
# The description of the API for programs, for members signed in (TIME-26 AC-8).
app.include_router(api_v1_router.docs_router)

# The shared sign-in module learns who it runs in before the first request (vendor/nexoidc).
oidc_store.configure()


#: The service worker and the manifest are asked anew each time: a new build is a new worker (TIME-28).
_ALWAYS_ASKED = {"sw.js": "text/javascript", "manifest.webmanifest": "application/manifest+json"}
#: The mark of the start page: the one answer to a page the service worker may keep (``public/sw.js``). Pages the
#: server draws itself (a read link with a note in it) never carry it.
SHELL_HEADER = "X-Nextime-Shell"


def _mount_frontend(target: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.exists():
        return
    if (dist / "assets").is_dir():
        target.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    root = dist.resolve()
    start_page = index.resolve()

    @target.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa(path: str) -> FileResponse | JSONResponse:
        if path == "api" or path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": detail("not_found", "Not found.")})
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and root in candidate.parents and candidate != start_page:
            if path in _ALWAYS_ASKED:
                return FileResponse(candidate, media_type=_ALWAYS_ASKED[path], headers={"Cache-Control": "no-cache"})
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache", SHELL_HEADER: "1"})


_mount_frontend(app, get_settings().frontend_dist)
