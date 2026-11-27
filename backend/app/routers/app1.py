"""The own app for iPhone and iPad: ``/api/app/1`` (TIME-29; the wording in ``docs/vertrag-app1.md``, ``api`` 1).

**A promise to the own app.** The app in the App Store and the servers out there are never updated at the same time.
As long as ``app/1`` stands in the address nothing here breaks: new fields may come, none is renamed or taken away. A
change that must break goes to ``/api/app/2`` beside it, and ``hello`` says which ones the server speaks (EC-2).

Walls: a device's token in ``Authorization: Bearer ntd_…`` on every call but ``hello`` and the two pickups of a
pairing (a cookie opens nothing here). A disconnected device hears ``401 device_disconnected`` (EC-1), one of a blocked
or deleted account ``401 account_blocked`` (EC-5); neither takes anything. The pickups stand behind the sign-in brake
(EC-3).

Since the shared module geraete 0.1.0 pairing, the devices, ``PUT``/``DELETE push`` and the kinds of notification come
from the module (``module_router``, mounted after this one; ``services/geraete.py``), and every route here checks the
device through it (``geraete_api.device``, G12: a blocked account's device rests, G26). Two old routes stay here in
their old form, ahead of the module's: ``GET /devices`` (the list in the order of before) and ``PUT /push`` (``alerts``
of the published app becomes what it understands, G16).

What changed since a cursor comes from ``changes`` and ``wait``, changes made on the device go through ``ops``
(``services/appsync.py``). Errors are ``{"detail": {"code", "message"}}`` as everywhere in nextime.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import __version__
from ..db import SessionLocal
from ..deps import client_ip
from ..errors import error
from ..models import Account as AccountRow
from ..models import AppDevice, Device, utcnow
from ..services import appdevices, apppush, appsync, display, events, geraete, live, logs, settings_service
from ..services.appdevices import Caller, Refusal
from ..services.oidc_store import SqlStore
from ..vendor import nexgeraete
from ..vendor.nexgeraete import api as geraete_api
from ..vendor.nexgeraete import forms as geraete_forms
from ..vendor.nexkonten.brake import BRAKE
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
FEATURES = ("pairing", "changes", "ops", "conflicts", "calendar_account", "push_ticket", "nudge", "notify",
            "collections_write", "tasks", "deadlines", "invitations", "people", "year", "reminders",
            "attachments", "trash")


def _refusal(refusal: Refusal, request: Request | None) -> HTTPException:
    form = geraete_forms.form_of(geraete.CONFIG, request.headers if request is not None else {})
    status, body, headers = geraete_forms.refusal(form, refusal.refused)
    return HTTPException(status_code=status, detail=body["detail"], headers=headers)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else None


def _caller(token: str | None, request: Request | None = None) -> Caller:
    client = nexgeraete.client_of(request.headers.get(nexgeraete.CLIENT_HEADER)) if request is not None else None
    with SessionLocal() as db:
        try:
            return appdevices.authenticate(db, token, client)
        except Refusal as refusal:
            raise _refusal(refusal, request) from refusal


_module_device = geraete_api.device(config=geraete.CONFIG, store=geraete.store, clock=geraete.now)


def device(found: Annotated[nexgeraete.Caller, Depends(_module_device)]) -> Caller:
    """The device of the request, checked by the module (G12), with nextime's rows of its account and itself."""
    with SessionLocal() as db:
        account = db.get(AccountRow, int(found.holder.id))
        row = db.get(AppDevice, int(found.device.id))
        if account is None or row is None:
            raise _refusal(Refusal(nexgeraete.DeviceRefused("device_disconnected")), None)
        db.expunge(account)
        db.expunge(row)
    logs.set_actor(account.name)
    return Caller(account=account, device=row, found=found)


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
            "features": list(FEATURES), **geraete_api.hello_fields(geraete.CONFIG)}


# --- Pairing, devices, notifications: the module's routes (G1–G30) ---------------------------------------------

#: ``POST pairing/{id}/key``, ``POST pairing/code``, ``POST devices``, ``DELETE devices/{id}``, ``PATCH device``,
#: ``DELETE device``, ``DELETE push``, ``GET``/``PUT push/kinds``; the pickups behind the sign-in brake of konten
#: (pot ``app-pairing:<sender>``, EC-3), after each new device the mail "Neue Anmeldung" (G29) and ``security`` to the
#: account's other devices. Mounted after ``router``: its ``GET devices`` and ``PUT push`` keep the old form.
module_router = geraete_api.app_router(
    config=geraete.CONFIG,
    store=geraete.store,
    brake=BRAKE,
    on_paired=lambda holder, device: geraete.on_paired(holder, device),
    send=geraete.send,
    sender=client_ip,
    clock=geraete.now,
)


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
        fields = geraete_api.me_fields(geraete.CONFIG, found.found)
        return {
            **fields,
            "account": appsync.account_out(account),
            "device": fields["device"],
            "display": display.view(account),
            "language": account.language or "",
            "limits": {"ops": appsync.OPS_MAX, "changes_page": appsync.PAGE, "wait_seconds": int(WAIT_SECONDS),
                       "title_chars": events.TITLE_MAX, "notes_chars": events.NOTES_MAX,
                       "location_chars": events.LOCATION_MAX, "upload_mb": settings_service.upload_max_mb(db),
                       "trash_days": settings_service.trash_days(db)},
            # The server nudges devices with a ticket through the relay; empty relay: nothing goes out.
            "offers": fields["offers"],
            "server_id": fields["server_id"],
            "calendar_account": _calendar_account(db, found.device.id),
        }


@router.get("/devices", summary="The account's paired iPhones and iPads")
def list_devices(found: Device_) -> list[dict[str, Any]]:
    """In the order of before (the oldest first), each as the module shows it, ``this`` marked."""
    with SessionLocal() as db:
        return [nexgeraete.devices.entry(geraete.device_of(row), found.device.id)
                for row in appdevices.of_account(db, found.account.id)]


class PushIn(BaseModel):
    ticket: str = Field(min_length=1, max_length=appdevices.TICKET_MAX)
    key: str = Field(min_length=1, max_length=100)
    #: The published app (contract 0.3): it shows notifications with text. Becomes ``understands`` ``changed`` and
    #: ``changes``; left out, only the silent nudge (``changed``), so that a build without the notification service
    #: extension never shows an empty notification.
    alerts: bool = False
    #: The kinds the device understands (G16); wins over ``alerts``.
    understands: list[str] | None = Field(default=None, max_length=100)


@router.put("/push", status_code=204, summary="Notifications for this device: the relay's ticket, the key, the kinds")
def push_on(found: Device_, body: PushIn, request: Request) -> Response:
    understands = body.understands if body.understands is not None else (list(geraete.ALERTS) if body.alerts else None)
    with SessionLocal() as db:
        data = geraete.Store(db)
        try:
            nexgeraete.push.register(data, geraete.CONFIG, found.found, body.ticket, body.key, understands)
        except nexgeraete.DeviceRefused as refused:
            data.rollback()
            raise _refusal(Refusal(refused), request) from None
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


# --- What the browser shows beside the events (0.4) ---------------------------------------------------------------
#
# The browser's own answers, with the device's account: the same rights, the same limits (``routers/nextasks.py``,
# ``routers/nexpaper.py``, ``routers/avatars.py``, ``routers/events.py``).


Day = Annotated[date, Query()]


@router.get("/tasks", summary="The tasks of nextasks due in those days, as the browser gets them")
def tasks(found: Device_, to: Day, start: Annotated[date | None, Query(alias="from")] = None,
          previews: bool = False) -> dict[str, Any]:
    """TIMEIOS-17: ``GET /api/tasks`` with the device's account (no ``from``: overdue ones too)."""
    from . import nextasks as nextasks_router

    with SessionLocal() as db:
        return nextasks_router.tasks(found.account, db, to, start, previews)


@router.get("/deadlines", summary="The open deadlines of nexpaper in those days, as the browser gets them")
def deadlines(found: Device_, to: Day, start: Annotated[date | None, Query(alias="from")] = None) -> dict[str, Any]:
    """TIMEIOS-6, TIMEIOS-7 (G9): ``GET /api/deadlines`` with the device's account."""
    from . import nexpaper as nexpaper_router

    with SessionLocal() as db:
        return nexpaper_router.deadlines(found.account, db, to, start)


@router.get("/avatars/{account_id}", summary="A profile picture, for those who may see it")
def avatar(account_id: int, found: Device_) -> Response:
    """TIMEIOS-9 (people): the browser's rules (``avatars.may_see``; a blocked or deleted person only for itself and
    the operator). The address carries ``?v=<avatar>``, so a new picture is a new address."""
    from .avatars import get_avatar

    with SessionLocal() as db:
        return get_avatar(account_id, found.account, db)


@router.get("/year", summary="How many events each day of a year holds, as the browser counts")
def year(found: Device_, year: int = Query(ge=1900, le=2200), zone: str | None = Query(default=None, max_length=64),
         collection: int | None = None, only: int | None = None) -> dict[str, Any]:
    """TIMEIOS-8 AC-16: the browser's count (``yearmap.year_map``) for comparing; ``collection`` or ``only`` (one
    calendar, "Nur diesen", hidden or not) narrow it as in the browser."""
    from ..services import calendars as calendar_service
    from ..services import collections, yearmap

    if collection is not None and only is not None:
        raise error("invalid_input", "The input is not valid.", 422, fields=["collection", "only"])
    if not yearmap.FIRST_YEAR <= year <= yearmap.LAST_YEAR:
        raise error("invalid_input", "The input is not valid.", 422, fields=["year"])
    with SessionLocal() as db:
        account = db.get(AccountRow, found.account.id)
        assert account is not None
        chosen = zone or str(getattr(display.zone_of(account), "key", "") or "UTC")
        if not display.valid_zone(chosen):
            raise error("invalid_input", "The input is not valid.", 422, fields=["zone"])
        picked: set[int] | None = None
        hidden = False
        if collection is not None:
            entry = next((item for item in collections.view(db, account.id) if item["id"] == collection), None)
            if entry is None:
                raise error("not_found", "Not found.", 404)
            picked = set(entry["calendar_ids"])
        elif only is not None:
            if calendar_service.access(db, account.id, only) is None:
                raise error("not_found", "Not found.", 404)
            picked, hidden = {only}, True
        return yearmap.year_map(db, account, year, chosen, include_hidden=hidden, only=picked)


# --- Attachments (0.5, TIME-9; as nexnotes' app1_files) -------------------------------------------------------------


def _attachment_id(account_id: int, ref: str) -> str:
    """The attachment's id from the app's ``ref``: the same upload sent again is the same attachment."""
    import base64
    import hashlib

    digest = hashlib.sha256(f"nextime-app-upload:{account_id}:{ref}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")[:22]


@router.post("/attachments", status_code=201, summary="A file at an event (the file as the body)")
async def upload(found: Device_, request: Request, response: Response,
                 event_id: Annotated[int | None, Query()] = None,
                 event_ref: Annotated[str | None, Query(max_length=64)] = None,
                 ref: Annotated[str | None, Query(max_length=64)] = None,
                 name: Annotated[str, Query(max_length=2000)] = "") -> dict[str, Any]:
    """``ref`` (or the header ``Idempotency-Key``) names the upload: sent again, nothing doubles. ``event_ref``: the
    ``ref`` of the op that made the event offline; ``404 event_unknown`` while that op has not arrived."""
    from ..models import Attachment, Event
    from ..services import attachments

    key = (ref or request.headers.get("idempotency-key") or "").strip()
    if not appsync.REF.match(key):
        raise error("invalid_input", "The input is not valid.", 422, fields=["ref"])
    if (event_id is None) == (event_ref is None):
        raise error("invalid_input", "The input is not valid.", 422, fields=["event_id", "event_ref"])
    if event_ref is not None:
        with SessionLocal() as db:
            made = db.scalar(select(Event).where(Event.created_by == found.account.id,
                                                 Event.client_ref == event_ref))
            if made is None:
                raise error("event_unknown", "The event is not there yet.", 404)
            event_id = made.id
    declared_text = request.headers.get("content-length")
    declared = int(declared_text) if declared_text and declared_text.isdigit() else None
    try:
        with SessionLocal() as db:
            started = attachments.begin(db, found.account, event_id=event_id, pending_ref=None,
                                        attachment_id=_attachment_id(found.account.id, key), name=name,
                                        declared=declared)
        if isinstance(started, Attachment):
            response.status_code = 200
            return attachments.out(started)
        work = await attachments.receive(started, request.stream())
        row, new = await asyncio.to_thread(attachments.finish, started, work)
    except attachments.AttachmentError as exc:
        raise exc.http() from exc
    if not new:
        response.status_code = 200
    return attachments.out(row)


def _browser_attachments() -> Any:
    from . import attachments as attachments_router

    return attachments_router


@router.get("/attachments/{attachment_id}/file", summary="The file of an attachment")
def attachment_file(attachment_id: str, found: Device_, download: bool = False) -> Any:
    with SessionLocal() as db:
        return _browser_attachments().file(attachment_id, found.account, db, download)


@router.get("/attachments/{attachment_id}/view", summary="A picture as every device shows it (WebP)")
def attachment_view(attachment_id: str, found: Device_) -> Any:
    with SessionLocal() as db:
        return _browser_attachments().view(attachment_id, found.account, db)


@router.get("/attachments/{attachment_id}/thumb", summary="The preview of an attachment")
def attachment_thumb(attachment_id: str, found: Device_) -> Any:
    with SessionLocal() as db:
        return _browser_attachments().thumb(attachment_id, found.account, db)


@router.delete("/attachments/{attachment_id}", status_code=204, summary="Remove an attachment for good")
def attachment_remove(attachment_id: str, found: Device_) -> Response:
    """With the right to change the event; one gone already (or never seen) is gone: ``204``."""
    from ..services import attachments

    with SessionLocal() as db:
        try:
            attachments.remove(db, found.account, attachment_id)
        except HTTPException as exc:
            if not (isinstance(exc.detail, dict) and exc.detail.get("code") == "not_found"):
                raise
    return Response(status_code=204)


@router.get("/events/{event_id}/remote-attachments/{index}", summary="An attachment of an event of a source")
def remote_attachment(event_id: int, index: int, found: Device_) -> Any:
    with SessionLocal() as db:
        return _browser_attachments().remote(event_id, index, found.account, db)


# --- Trash (0.5, TIME-23) -------------------------------------------------------------------------------------------


class TrashRestoreIn(BaseModel):
    calendar_id: int | None = None


class TrashEmptyIn(BaseModel):
    until: str | None = Field(default=None, max_length=40)


@router.get("/trash", summary="The trash as the browser shows it")
def trash_listing(found: Device_, offset: int = Query(default=0, ge=0, le=10_000_000),
                  limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    from ..services import trash

    with SessionLocal() as db:
        return trash.listing(db, found.account, offset=offset, limit=min(limit, trash.PAGE_MAX))


@router.post("/trash/events/{event_id}/restore", summary="Restore an event")
def trash_restore_event(event_id: int, found: Device_, body: TrashRestoreIn | None = None) -> dict[str, Any]:
    from . import trash as trash_router

    with SessionLocal() as db:
        return trash_router.restore_event(event_id, found.account, db,
                                          trash_router.RestoreIn(calendar_id=body.calendar_id if body else None))


@router.post("/trash/parts/{part_id}/restore", summary="Put an occurrence, or the following ones, back")
def trash_restore_part(part_id: int, found: Device_) -> dict[str, Any]:
    from . import trash as trash_router

    with SessionLocal() as db:
        return trash_router.restore_part(part_id, found.account, db)


@router.post("/trash/calendars/{calendar_id}/restore", summary="Restore a calendar with its events")
def trash_restore_calendar(calendar_id: int, found: Device_) -> dict[str, Any]:
    from . import trash as trash_router

    with SessionLocal() as db:
        return trash_router.restore_calendar(calendar_id, found.account, db)


@router.post("/trash/empty", summary="Empty the trash up to what the list showed")
def trash_empty(found: Device_, body: TrashEmptyIn | None = None) -> dict[str, int]:
    from . import trash as trash_router

    with SessionLocal() as db:
        gone = trash_router.empty(found.account, db, trash_router.EmptyIn(until=body.until if body else None))
    return {"count": gone["events"] + gone["calendars"], **gone}
