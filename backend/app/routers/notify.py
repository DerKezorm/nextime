"""Notifications over HTTP (TIME-21, ``services/notify.py``): the switch of a calendar with the member's devices,
whether notifications can arrive and a preview (AC-1, AC-5, AC-6, EC-8, EC-14), and the browser's own
sign-up for Web Push.

| Method | Path | What |
|---|---|---|
| GET | ``/api/calendars/{id}/notify`` | switch, devices, HTTPS and internet, preview |
| PUT | ``/api/calendars/{id}/notify`` | ``{"on"}`` |
| GET | ``/api/notify/webpush`` | the server's public key, and whether this browser is signed up |
| PUT | ``/api/notify/webpush`` | this browser allowed notifications: ``{"endpoint", "keys", "installed"}`` |
| DELETE | ``/api/notify/webpush`` | this browser no longer |

A browser's sign-up belongs to the session it is made in, so these ways take the session cookie only.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..config import get_settings
from ..deps import Account, DbSession
from ..errors import error
from ..models import AuthSession
from ..security import SESSION_COOKIE, hash_token
from ..services import notify, webpush
from .devices import https


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    notify.register()
    stop = asyncio.Event()
    ticker = None if get_settings().disable_background else asyncio.create_task(notify.run_forever(stop))
    try:
        yield
    finally:
        stop.set()
        if ticker is not None:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
        # What waits in ``notify_changes`` and ``notify_outbox`` goes out at the first tick after the next start.


router = APIRouter(tags=["notifications"], lifespan=lifespan)


def _session_id(request: Request, db: DbSession, *, required: bool = False) -> int | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    found = db.scalar(select(AuthSession.id).where(AuthSession.token_hash == hash_token(cookie))) if cookie else None
    if found is None and required:
        raise error("sign_in_required", "Sign in first.", 401)
    return found


class SwitchIn(BaseModel):
    on: bool


class KeysIn(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=8, max_length=webpush.MAX_ENDPOINT)
    keys: KeysIn
    installed: bool = False
    #: "Remind me on this device" as the page kept it (TIME-7 AC-6), and what it runs on (AC-7).
    reminders: bool = True
    platform: str = Field(default="", max_length=8)


@router.get("/api/calendars/{calendar_id}/notify", summary="Notifications of a calendar")
def calendar_settings(calendar_id: int, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    return notify.calendar_settings(db, account, calendar_id, _session_id(request, db), https(request, db))


@router.put("/api/calendars/{calendar_id}/notify", summary="Switch notifications of a calendar on or off")
def set_switch(calendar_id: int, body: SwitchIn, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    notify.set_switch(db, account, calendar_id, body.on)
    return notify.calendar_settings(db, account, calendar_id, _session_id(request, db), https(request, db))


@router.get("/api/notify/webpush", summary="The key for signing up, and whether this browser is")
def webpush_state(request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    session_id = _session_id(request, db)
    row = webpush.of_session(db, session_id) if session_id is not None else None
    mine = row is not None and row.account_id == account.id
    return {"key": webpush.public_key(db), "subscribed": mine,
            "reminders": bool(row.reminders) if mine and row is not None else True}


@router.put("/api/notify/webpush", status_code=204, summary="This browser allowed notifications")
def subscribe(body: SubscriptionIn, request: Request, account: Account, db: DbSession) -> None:
    session_id = _session_id(request, db, required=True)
    assert session_id is not None
    endpoint = body.endpoint.strip()
    if not webpush.endpoint_valid(endpoint):
        raise error("webpush_endpoint", "This is not the address of a push service.", 422, fields=["endpoint"])
    if not webpush.valid_keys(body.keys.p256dh, body.keys.auth):
        raise error("webpush_keys", "The browser's keys are not usable.", 422, fields=["keys"])
    webpush.subscribe(db, account.id, session_id, endpoint, body.keys.p256dh, body.keys.auth,
                      request.headers.get("user-agent", ""), body.installed, body.reminders, body.platform)


@router.delete("/api/notify/webpush", status_code=204, summary="This browser no longer")
def unsubscribe(request: Request, account: Account, db: DbSession) -> None:
    session_id = _session_id(request, db, required=True)
    assert session_id is not None
    webpush.unsubscribe(db, account.id, session_id)


@router.post("/api/notify/webpush/probe", status_code=204, summary="One notification to this browser, at once")
def probe(request: Request, account: Account, db: DbSession) -> None:
    """"Probemeldung schicken" under Notifications › Which way (plan "Einstellungen"): this browser only, signed up by
    this account; nothing waits in the outbox."""
    from ..services.events import language_of

    session_id = _session_id(request, db, required=True)
    row = webpush.of_session(db, session_id) if session_id is not None else None
    if row is None or row.account_id != account.id:
        raise error("webpush_not_here", "This browser has no notifications from nextime.", 409)
    try:
        webpush.probe(db, row, language_of(account))
    except webpush.Gone as exc:
        raise error("webpush_not_here", "This browser has no notifications from nextime.", 409) from exc
    except (webpush.Refused, webpush.Failed) as exc:
        raise error("webpush_failed", "The push service did not take the notification.", 502) from exc


@router.delete("/api/notify/webpush/{subscription_id}", status_code=204, summary="One of the own browsers no longer")
def remove_browser(subscription_id: int, account: Account, db: DbSession) -> None:
    """"Entfernen" in the list "Browser mit Mitteilungen": only a browser of the own account (another's: 404)."""
    if not webpush.remove(db, account.id, subscription_id):
        raise error("not_found", "Not found.", 404)
