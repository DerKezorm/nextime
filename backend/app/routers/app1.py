"""The own app for iPhone and iPad: ``/api/app/1`` (TIME-29; the wording in ``docs/vertrag-app1.md``, ``api`` 1).

**A promise to the own app.** The app in the App Store and the servers out there are never updated at the same time.
As long as ``app/1`` stands in the address nothing here breaks: new fields may come, none is renamed or taken away. A
change that must break goes to ``/api/app/2`` beside it, and ``hello`` says which ones the server speaks (EC-2).

Walls: a device's token in ``Authorization: Bearer ntd_…`` on every call but ``hello`` and the two pickups of a
pairing (a cookie opens nothing here). A disconnected device hears ``401 device_disconnected`` (EC-1), one of a blocked
or deleted account ``401 account_blocked`` (EC-5); neither takes anything. The pickups stand behind the sign-in brake
(EC-3).

What changed since a cursor comes from ``changes`` and ``wait``, changes made on the device go through ``ops``
(``services/appsync.py``). Errors are ``{"detail": {"code", "message"}}`` as everywhere in nextime.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import __version__
from ..db import SessionLocal
from ..deps import client_ip
from ..errors import detail, error
from ..models import Account as AccountRow
from ..models import Device, utcnow
from ..security import brake as sign_in_brake
from ..services import appdevices, apppush, appsync, display, events, live, logs, settings_service
from ..services.appdevices import Caller, PairingError, Refusal
from ..services.oidc_store import SqlStore
from ..vendor.nexoidc import providers

logger = logging.getLogger("nextime.app")

router = APIRouter(prefix="/api/app/1", tags=["app"])
# Every change the account hears of nudges its app devices (follow-up 3).
apppush.register()

#: The interface this server speaks, and the oldest one it still answers.
API = 1
MIN_API = 1
#: How long ``wait`` holds a request open; how often it looks again meanwhile.
WAIT_SECONDS = 25.0
WAIT_RECHECK = 5.0
#: The parts of the interface this server has (``hello``): an app tells an older server by their absence.
FEATURES = ("pairing", "changes", "ops", "conflicts", "calendar_account", "push_ticket", "nudge")


def _refused(code: str, text: str, status: int, retry: int | None = None) -> HTTPException:
    headers = {"Retry-After": str(retry)} if retry is not None else None
    return HTTPException(status_code=status, detail=detail(code, text), headers=headers)


_REFUSALS = {
    "device_disconnected": "This device was disconnected. Connect it again.",
    "account_blocked": "This account is blocked or deleted.",
    "second_factor_required": "Set up your second factor in the browser first, then connect again.",
}


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else None


def _caller(token: str | None) -> Caller:
    with SessionLocal() as db:
        try:
            return appdevices.authenticate(db, token)
        except Refusal as refusal:
            raise _refused(refusal.code, _REFUSALS[refusal.code], 401) from refusal


def device(request: Request) -> Caller:
    found = _caller(_bearer(request))
    logs.set_actor(found.account.name)
    return found


Device_ = Annotated[Caller, Depends(device)]


# --- Server version (EC-2) ------------------------------------------------------------------------------------------


@router.get("/hello", summary="Which interface this server speaks (no sign-in)")
def hello() -> dict[str, Any]:
    with SessionLocal() as db:
        ways = []
        if settings_service.get(db, "password_login"):
            ways.append("password")
        if providers.public_list(SqlStore(db)):
            ways.append("oidc")
    return {"app": "nextime", "api": API, "min_api": MIN_API, "version": __version__, "sign_in": ways,
            "features": list(FEATURES)}


# --- Pairing (no token; behind the sign-in brake) ------------------------------------------------------------------


class DeviceIn(BaseModel):
    device_name: str = Field(default="", max_length=200)
    device_kind: str = Field(default="", max_length=16)


class KeyIn(DeviceIn):
    secret: str = Field(default="", max_length=200)


class CodeIn(DeviceIn):
    code: str = Field(default="", max_length=200)


def _paired(token: str, account: AccountRow, row: Any, status: int = 200) -> JSONResponse:
    body = {"token": token, "account": appsync.account_out(account), "device": appdevices.device_out(row)}
    # A token must not stay behind in any cache.
    return JSONResponse(status_code=status, content=body, headers={"Cache-Control": "no-store"})


def _pickup(request: Request, take: Any) -> JSONResponse:
    """The brake counts per sender: there is no account to count at before the code is known (EC-3)."""
    key = "app-pairing:" + client_ip(request)
    wait = sign_in_brake.wait_seconds(key)
    if wait:
        raise _refused("too_many_attempts", "Too many attempts. Try again later.", 429, wait)
    with SessionLocal() as db:
        try:
            token, account, row = take(db)
        except PairingError as exc:
            if exc.code == "pairing_gone":
                sign_in_brake.failed(key)
                logger.warning("Pairing refused")
            raise error(exc.code, exc.text, exc.status, **exc.values) from exc
        db.expunge(account)
        db.expunge(row)
    sign_in_brake.succeeded(key)
    return _paired(token, account, row)


@router.post("/pairing/{pairing_id}/key", summary="The device's token for the QR code")
def pickup_by_secret(pairing_id: str, body: KeyIn, request: Request) -> JSONResponse:
    return _pickup(request, lambda db: appdevices.by_secret(db, pairing_id, body.secret, body.device_name,
                                                            body.device_kind))


@router.post("/pairing/code", summary="The device's token for the code of the sign-in page")
def pickup_by_code(body: CodeIn, request: Request) -> JSONResponse:
    return _pickup(request, lambda db: appdevices.by_code(db, body.code, body.device_name, body.device_kind))


@router.post("/devices", status_code=201, summary="A token of its own for another device of this account")
def bring_in(found: Device_, body: DeviceIn) -> JSONResponse:
    """The connection travels over iCloud to the account's other devices; each comes in under its own name."""
    with SessionLocal() as db:
        try:
            token, row = appdevices.sibling(db, found.account, found.device, body.device_name, body.device_kind)
        except PairingError as exc:
            retry = exc.values.pop("retry_after", None)
            raise _refused(exc.code, exc.text, exc.status, retry) from exc
        db.expunge(row)
    return _paired(token, found.account, row, status=201)


# --- The own device -----------------------------------------------------------------------------------------------


def _calendar_account(db: Any, device_id: int) -> dict[str, Any] | None:
    row = db.scalar(select(Device).where(Device.app_device_id == device_id).order_by(Device.id.desc()))
    if row is None:
        return None
    return {"id": row.id, "name": row.name, "set_up": row.last_used_at is not None,
            "last_seen_at": appdevices.iso(row.last_used_at)}


@router.get("/me", summary="The account, this device, what the server offers")
def me(found: Device_) -> dict[str, Any]:
    with SessionLocal() as db:
        account = db.get(AccountRow, found.account.id)
        assert account is not None
        return {
            "account": appsync.account_out(account),
            "device": appdevices.device_out(found.device),
            "display": display.view(account),
            "language": account.language or "",
            "limits": {"ops": appsync.OPS_MAX, "changes_page": appsync.PAGE, "wait_seconds": int(WAIT_SECONDS),
                       "title_chars": events.TITLE_MAX, "notes_chars": events.NOTES_MAX,
                       "location_chars": events.LOCATION_MAX, "upload_mb": settings_service.upload_max_mb(db),
                       "trash_days": settings_service.trash_days(db)},
            # The server nudges devices with a ticket through the relay; empty relay: nothing goes out.
            "offers": {"push": bool(apppush.relay_url())},
            "server_id": appdevices.server_id(),
            "calendar_account": _calendar_account(db, found.device.id),
        }


@router.delete("/device", status_code=204, summary="This device disconnects itself")
def forget_device(found: Device_) -> Response:
    with SessionLocal() as db:
        appdevices.disconnect(db, found.account.id, found.device.id)
    return Response(status_code=204)


@router.get("/devices", summary="The account's paired iPhones and iPads")
def list_devices(found: Device_) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        return [{**appdevices.device_out(row), "push": bool(row.push_ticket), "this": row.id == found.device.id}
                for row in appdevices.of_account(db, found.account.id)]


@router.delete("/devices/{device_id}", status_code=204, summary="Disconnect one of the account's devices")
def drop_device(device_id: int, found: Device_) -> Response:
    with SessionLocal() as db:
        if not appdevices.disconnect(db, found.account.id, device_id):
            raise error("not_found", "Not found.", 404)
    return Response(status_code=204)


class PushIn(BaseModel):
    ticket: str = Field(min_length=1, max_length=appdevices.TICKET_MAX)
    key: str = Field(min_length=1, max_length=100)


@router.put("/push", status_code=204, summary="Notifications for this device: the relay's ticket and the key")
def push_on(found: Device_, body: PushIn) -> Response:
    """Kept as nexnotes keeps it; the device gets the silent nudge on every change of the account."""
    with SessionLocal() as db:
        try:
            appdevices.remember_push(db, found.device.id, body.ticket, body.key)
        except ValueError as exc:
            raise error("push_ticket_invalid", "The ticket or the key is not usable.", 422) from exc
    return Response(status_code=204)


@router.delete("/push", status_code=204, summary="No more notifications for this device")
def push_off(found: Device_) -> Response:
    with SessionLocal() as db:
        appdevices.forget_push(db, found.device.id)
    return Response(status_code=204)


# --- The calendar account on this device (TIMEIOS-4 AC-6, AC-7, EC-14, EC-15) -------------------------------------


class CalendarAccountIn(BaseModel):
    device_name: str = Field(default="", max_length=400)


@router.post("/calendar-account", status_code=201, summary="The calendar account for this iPhone or iPad")
def calendar_account(found: Device_, body: CalendarAccountIn, request: Request) -> dict[str, Any]:
    """A device of the calendar account linked to this app device, and its one-time setup link (ten minutes, once).
    Such a device gets no alarms: only the app reminds (AC-7)."""
    from ..services import devices as dav_devices
    from .devices import base_url, https

    with SessionLocal() as db:
        if not https(request, db):
            raise error("https_required", "Calendar programs connect only over HTTPS.", 409)
        account = db.get(AccountRow, found.account.id)
        assert account is not None
        # A device of before that never signed in goes; one that is set up stays and is replaced only by the browser.
        for old in db.scalars(select(Device).where(Device.app_device_id == found.device.id,
                                                   Device.last_used_at.is_(None))):
            db.delete(old)
        db.flush()
        row, _password, token = dav_devices.create(db, account, body.device_name or found.device.name)
        row.app_device_id = found.device.id
        db.commit()
        link = f"{base_url(request, db)}/setup/{token}"
        until = utcnow() + timedelta(minutes=dav_devices.SETUP_MINUTES)
        logger.info("Calendar account made for an app device device_id=%s", found.device.id)
        return {"link": link, "expires_at": appdevices.iso(until),
                "device": {"id": row.id, "name": row.name, "set_up": False}}


@router.delete("/calendar-account", status_code=204, summary="Lock out the calendar account of this device")
def drop_calendar_account(found: Device_) -> Response:
    from ..services import devices as dav_devices

    with SessionLocal() as db:
        account = db.get(AccountRow, found.account.id)
        assert account is not None
        for row in db.scalars(select(Device).where(Device.app_device_id == found.device.id)).all():
            dav_devices.lock_out(db, account, row.id)
    return Response(status_code=204)


# --- What changed (AC-6, AC-8, EC-4) ------------------------------------------------------------------------------


CursorText = Annotated[str | None, Query(max_length=200)]


def _only(calendars: str | None) -> set[int] | None:
    if calendars is None:
        return None
    try:
        return {int(part) for part in calendars.split(",") if part.strip()}
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=["calendars"]) from None


@router.get("/changes", summary="Everything the account sees that changed after the cursor")
def changes(found: Device_, cursor: CursorText = None,
            calendars: str | None = Query(default=None, max_length=4000)) -> dict[str, Any]:
    with SessionLocal() as db:
        return appsync.changes(db, found.account.id, cursor, _only(calendars))


def _changed_since(token: str | None, cursor: str) -> str | None:
    found = _caller(token)
    with SessionLocal() as db:
        return appsync.changed_since(db, found.account.id, cursor)


_waiting_lock = threading.Lock()
_waiting: dict[int, int] = {}
_turns = itertools.count(1)


@router.get("/wait", summary="Answers as soon as something changed after the cursor, at the latest after 25 s")
async def wait(found: Device_, request: Request, cursor: CursorText = None) -> dict[str, str]:
    """Nothing is held while waiting: neither a thread nor a database connection. A change the account hears of wakes
    the request (``services/live.py``); every few seconds it looks again anyway (and a device disconnected meanwhile
    hears so). A device waits once: asking again ends its waiting request before."""
    token = _bearer(request)
    given = cursor or ""
    turn = next(_turns)
    with _waiting_lock:
        _waiting[found.device.id] = turn
    deadline = time.monotonic() + WAIT_SECONDS
    entry = live.subscribe(found.account.id)
    try:
        while True:
            newer = await asyncio.to_thread(_changed_since, token, given)
            if newer is not None:
                return {"cursor": newer}
            left = deadline - time.monotonic()
            with _waiting_lock:
                replaced = _waiting.get(found.device.id) != turn
            if left <= 0 or replaced or await request.is_disconnected():
                return {"cursor": given}
            try:
                await asyncio.wait_for(entry[1].get(), timeout=min(left, WAIT_RECHECK))
            except TimeoutError:
                pass
    finally:
        live.unsubscribe(found.account.id, entry)
        with _waiting_lock:
            if _waiting.get(found.device.id) == turn:
                _waiting.pop(found.device.id, None)


# --- Writing (AC-2, EC-6, EC-7) -----------------------------------------------------------------------------------


class OpsIn(BaseModel):
    ops: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/ops", summary="Changes made on the device, each once, in order")
def ops(found: Device_, body: OpsIn) -> dict[str, Any]:
    if len(body.ops) > appsync.OPS_MAX:
        raise error("too_many_ops", f"At most {appsync.OPS_MAX} changes at once.", 422, max=appsync.OPS_MAX)
    answers = appsync.run_ops(SessionLocal, found.account.id, body.ops)
    with SessionLocal() as db:
        cursor = appsync.current(db, found.account.id)
    return {"ops": answers, "cursor": cursor}
