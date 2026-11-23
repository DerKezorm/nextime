"""Export, import and moving house (TIME-24, ``services/transfer.py``).

| Method | Path | What |
|---|---|---|
| GET | ``/api/calendars/{id}/export.ics`` | one calendar as an ICS file, streamed |
| GET | ``/api/export.zip`` | every calendar of the account, with the attachments beside them |
| POST | ``/api/imports?name=`` | an ICS file as the body (at most 50 MB, 50,000 events): its preview |
| GET | ``/api/imports/{id}?calendar_id=`` | the preview for a calendar, and the state of its job |
| POST | ``/api/imports/{id}/apply`` | ``{"calendar_id"}``: starts the job (202), all or none, once |
| GET | ``/api/moves`` | the calendars from iCloud and Google the member may move |
| GET | ``/api/moves/{client_ref}`` | the state of a move |
| GET | ``/api/calendars/{id}/move`` | what moving a calendar from iCloud or Google would do |
| POST | ``/api/calendars/{id}/move`` | ``{"client_ref"}``: starts moving it into an own calendar (202) |
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from .. import middleware
from ..db import SessionLocal
from ..deps import Account, DbSession
from ..errors import error
from ..models import Account as AccountRow
from ..services import cleanup, transfer

logger = logging.getLogger("nextime.transfer")

# The file of an import comes as the body; its own limit is checked while it streams in.
middleware.LARGE_BODIES["/api/imports"] = transfer.IMPORT_MAX_BYTES + 1024 * 1024


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Files waiting for "übernehmen" and ZIPs left behind go in the cleanup round, once an hour.
    if not any(name == "imports" for name, _job in cleanup._jobs):
        cleanup.register("imports", transfer.sweep, every=3600)
    # Jobs a stop cut off: their staging calendars go, they can start again (TIME-24 EC-9).
    try:
        await run_in_threadpool(transfer.recover)
    except Exception:
        logger.exception("A step at the start was left out step=recover_jobs")
    yield


router = APIRouter(tags=["transfer"], lifespan=lifespan)


class ApplyIn(BaseModel):
    calendar_id: int


class MoveIn(BaseModel):
    client_ref: str = Field(min_length=1, max_length=64)


@router.get("/api/calendars/{calendar_id}/export.ics", summary="One calendar as an ICS file")
def export_one(calendar_id: int, account: Account, db: DbSession) -> StreamingResponse:
    name, chunks = transfer.export_calendar(db, account, calendar_id)
    return StreamingResponse(chunks, media_type="text/calendar; charset=utf-8",
                             headers={"Content-Disposition": transfer.disposition(name), "Cache-Control": "no-store"})


@router.get("/api/export.zip", summary="Every calendar of the account as a ZIP, streamed")
def export_all(account: Account) -> StreamingResponse:
    return StreamingResponse(transfer.export_zip(account.id), media_type="application/zip",
                             headers={"Content-Disposition": transfer.disposition("nextime.zip"),
                                      "Cache-Control": "no-store"})


def _take(account_id: int, import_id: str, name: str) -> dict[str, Any]:
    with SessionLocal() as db:
        person = db.get(AccountRow, account_id)
        if person is None:
            raise error("not_found", "Not found.", 404)
        return transfer.take(db, person, import_id, name)


@router.post("/api/imports", status_code=201, summary="An ICS file (the body): its preview")
async def take_file(request: Request, account: Account,
                    name: Annotated[str, Query(max_length=2000)] = "") -> dict[str, Any]:
    try:
        import_id = await transfer.receive(request.stream())
    except ClientDisconnect:
        raise error("upload_broken", "The upload broke off.", 400) from None
    return await run_in_threadpool(_take, account.id, import_id, name)


@router.get("/api/imports/{import_id}", summary="The preview of a file for a calendar")
def preview(import_id: str, account: Account, db: DbSession,
            calendar_id: Annotated[int | None, Query()] = None) -> dict[str, Any]:
    return transfer.preview(db, account, import_id, calendar_id)


@router.post("/api/imports/{import_id}/apply", summary="Starts taking the events of the file into the calendar")
def apply(import_id: str, payload: ApplyIn, account: Account, db: DbSession, response: Response) -> dict[str, Any]:
    state = transfer.apply_import(db, account, import_id, payload.calendar_id)
    if state["state"] == "running":
        response.status_code = 202
    return state


@router.get("/api/moves", summary="The calendars from iCloud and Google the member may move")
def movable(account: Account, db: DbSession) -> dict[str, Any]:
    return {"calendars": transfer.movable(db, account)}


@router.get("/api/calendars/{calendar_id}/move", summary="What moving a calendar would do")
def move_preview(calendar_id: int, account: Account, db: DbSession) -> dict[str, Any]:
    return transfer.move_preview(db, account, calendar_id)


@router.post("/api/calendars/{calendar_id}/move", summary="Starts moving a calendar from iCloud or Google")
def move(calendar_id: int, payload: MoveIn, account: Account, db: DbSession, response: Response) -> dict[str, Any]:
    state = transfer.move(db, account, calendar_id, payload.client_ref)
    if state["state"] == "running":
        response.status_code = 202
    return state


@router.get("/api/moves/{client_ref}", summary="The state of a move")
def move_state(client_ref: str, account: Account, db: DbSession) -> dict[str, Any]:
    return transfer.move_status(db, account, client_ref)
