"""The trash of the signed-in account (TIME-23, ``services/trash.py``).

| Method | Path | What |
|---|---|---|
| GET | ``/api/trash?offset=&limit=`` | the deadline and the entries, newest first, a page at a time |
| POST | ``/api/trash/events/{id}/restore`` | the event back; ``{"calendar_id"}`` when its calendar is gone |
| POST | ``/api/trash/calendars/{id}/restore`` | the calendar back with its events |
| POST | ``/api/trash/parts/{id}/restore`` | an occurrence or the following ones back into their series |
| POST | ``/api/trash/empty`` | ``{"until"}``: everything deleted until then goes for good |
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import APIRouter, FastAPI, Query
from pydantic import BaseModel, Field

from ..deps import Account, DbSession
from ..errors import error
from ..services import trash


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # The deadline runs out in the cleanup round (services/cleanup.py), once an hour.
    trash.register()
    yield


router = APIRouter(prefix="/api/trash", tags=["trash"], lifespan=lifespan)


class RestoreIn(BaseModel):
    #: The calendar to restore into when the event's own is gone (EC-1, EC-9).
    calendar_id: int | None = None


class EmptyIn(BaseModel):
    #: When the list was loaded: what was deleted later is not taken unseen.
    until: str | None = Field(default=None, max_length=40)


@router.get("", summary="The trash: deadline and entries")
def listing(account: Account, db: DbSession, offset: int = Query(default=0, ge=0, le=10_000_000),
            limit: int = Query(default=trash.PAGE, ge=1, le=trash.PAGE_MAX)) -> dict[str, Any]:
    return trash.listing(db, account, offset=offset, limit=limit)


@router.post("/events/{event_id}/restore", summary="Restore an event")
def restore_event(event_id: int, account: Account, db: DbSession, payload: RestoreIn | None = None) -> dict[str, Any]:
    row = trash.restore_event(db, account, event_id, payload.calendar_id if payload else None)
    return {"id": row.id, "calendar_id": row.calendar_id}


@router.post("/parts/{part_id}/restore", summary="Put an occurrence, or the following ones, back into the series")
def restore_part(part_id: int, account: Account, db: DbSession) -> dict[str, Any]:
    row = trash.restore_part(db, account, part_id)
    return {"id": row.id, "calendar_id": row.calendar_id}


@router.post("/calendars/{calendar_id}/restore", summary="Restore a calendar with its events")
def restore_calendar(calendar_id: int, account: Account, db: DbSession) -> dict[str, Any]:
    row = trash.restore_calendar(db, account, calendar_id)
    return {"id": row.id}


@router.post("/empty", summary="Empty the trash")
def empty(account: Account, db: DbSession, payload: EmptyIn | None = None) -> dict[str, int]:
    before = None
    if payload is not None and payload.until:
        try:
            before = datetime.fromisoformat(payload.until)
        except ValueError:
            raise error("invalid_input", "The input is not valid.", 422, fields=["until"]) from None
        if before.tzinfo is None:
            raise error("invalid_input", "The input is not valid.", 422, fields=["until"])
    return trash.empty(db, account, before)
