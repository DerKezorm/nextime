"""Reminders over HTTP (TIME-7, ``services/reminders.py``): the area "Reminders" under My account, a calendar's
default, this browser's "Remind me on this device", and "Again in 5 min" from a notification.

| Method | Path | What |
|---|---|---|
| GET | ``/api/reminders`` | "Not twice", the defaults per calendar, the browsers, HTTPS and internet, the preview |
| PUT | ``/api/reminders`` | ``{"not_double"}`` |
| PUT | ``/api/reminders/calendars/{id}`` | ``{"choice": "none" | "source" | "15"}``, the account's default there |
| PUT | ``/api/notify/webpush/reminders`` | ``{"on"}``: this browser reminds or not |
| POST | ``/api/reminders/snooze`` | ``{"mark"}`` from a notification: once more in five minutes, on that browser only |

The member's own reminders of an event go with the event (``PUT /api/events/{id}/reminders``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..deps import Account, DbSession
from ..services import reminders
from .devices import https
from .notify import _session_id


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Alarms stored in own calendars before this version become the reminders of those who saw them, and the lead
    # times of sources' events written before are filled in (once per start). Neither ever stops the start.
    for step in (reminders.adopt_stored_alarms, reminders.fill_alarm_leads):
        try:
            await asyncio.to_thread(step)
        except Exception:
            logger.exception("A step at the start was left out step=%s", step.__name__)
    stop = asyncio.Event()
    ticker = None if get_settings().disable_background else asyncio.create_task(reminders.run_forever(stop))
    try:
        yield
    finally:
        stop.set()
        if ticker is not None:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)


router = APIRouter(tags=["reminders"], lifespan=lifespan)
logger = logging.getLogger("nextime.reminders")


class SettingsIn(BaseModel):
    not_double: bool


class ChoiceIn(BaseModel):
    choice: str = Field(max_length=16)


class SwitchIn(BaseModel):
    on: bool


class MarkIn(BaseModel):
    mark: str = Field(min_length=10, max_length=2000)


@router.get("/api/reminders", summary="The settings of the reminders")
def overview(request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    return reminders.overview(db, account, _session_id(request, db), https(request, db))


@router.put("/api/reminders", summary="\"Not twice when Apple Calendar reminds already\"")
def set_settings(body: SettingsIn, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    reminders.set_not_double(db, account, body.not_double)
    return reminders.overview(db, account, _session_id(request, db), https(request, db))


@router.put("/api/reminders/calendars/{calendar_id}", summary="The account's default reminder of a calendar")
def set_default(calendar_id: int, body: ChoiceIn, request: Request, account: Account,
                db: DbSession) -> dict[str, Any]:
    reminders.set_default(db, account, calendar_id, body.choice)
    return reminders.overview(db, account, _session_id(request, db), https(request, db))


@router.put("/api/notify/webpush/reminders", summary="\"Remind me on this device\"")
def set_device(body: SwitchIn, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    session_id = _session_id(request, db, required=True)
    assert session_id is not None
    reminders.set_device(db, account.id, session_id, body.on)
    return reminders.overview(db, account, session_id, https(request, db))


@router.post("/api/reminders/snooze", status_code=204, summary="Once more in five minutes, on this browser only")
def snooze(body: MarkIn) -> None:
    """Sent by the service worker with the mark the notification carried; the mark says which browser and event."""
    reminders.snooze(body.mark)
