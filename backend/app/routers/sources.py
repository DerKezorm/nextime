"""Sources of the signed-in member: iCloud, other CalDAV servers, Google, subscriptions, holidays (TIME-15 to
TIME-17), and the operator's Google set-up (TIME-16).

| Method | Path | What |
|---|---|---|
| GET | ``/api/sources`` | "Calendars and accounts": rows, notices about refused changes, Google ready |
| POST | ``/api/sources/dav`` | connect iCloud or another CalDAV server (checked at once) |
| GET, PUT | ``/api/sources/{id}/calendars`` | "Choose calendars": all calendars there, the ones shown |
| POST | ``/api/sources/{id}/reconnect`` | a new password for an expired access |
| POST | ``/api/sources/{id}/refresh`` | "Fetch again", "Try again" |
| DELETE | ``/api/sources/{id}/calendars/{calendar_id}`` | "Aus nextime entfernen": one calendar left out |
| DELETE | ``/api/sources/{id}`` | "Disconnect", "Remove" (``drop_waiting=true`` when changes still wait) |
| POST | ``/api/sources/subscriptions/preview`` | name, count and span of a subscription link |
| POST | ``/api/sources/subscriptions`` | subscribe (also Google's secret address) |
| PUT | ``/api/sources/{id}/link`` | a new link for a subscription |
| GET, PUT | ``/api/sources/holidays`` | the countries and regions; "Apply" for one country |
| POST | ``/api/sources/google/start`` | the address of "Continue to Google" |
| GET | ``/api/google/callback`` | where Google sends the member back |
| POST | ``/api/sources/notices/{id}`` | "Create as a new event" (``create``) or "Discard" |
| GET, PUT, DELETE | ``/api/server/google`` | the operator's Google Cloud client (operator only) |
| GET | ``/api/invitations`` | the open invitations from now on (TIME-18) |

No answer ever carries a password, a grant or a full subscription link (TIME-15 EC-21, TIME-17 EC-11).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..deps import Account, DbSession, OperatorAccount
from ..services import events, google, holidays, invitations, sources, subscriptions

router = APIRouter(prefix="/api/sources", tags=["sources"])
callback_router = APIRouter(tags=["sources"])
server_router = APIRouter(prefix="/api/server/google", tags=["server"])
invitations_router = APIRouter(prefix="/api/invitations", tags=["events"])


class DavIn(BaseModel):
    kind: Literal["icloud", "caldav"]
    server: str = Field(default="", max_length=1000)
    username: str = Field(max_length=320)
    password: str = Field(max_length=1000)
    client_ref: str | None = Field(default=None, max_length=64)


class ChoiceIn(BaseModel):
    urls: list[str] = Field(max_length=500)
    #: Calendars left out still have changes waiting; the member saw how many and goes on (TIME-15 EC-8).
    drop_waiting: bool = False


class PasswordIn(BaseModel):
    password: str = Field(max_length=1000)


class LinkIn(BaseModel):
    url: str = Field(max_length=4000)
    username: str = Field(default="", max_length=320)
    password: str = Field(default="", max_length=1000)
    #: The way "Google calendar, read only" (TIME-16 AC-4): only Google's secret addresses.
    google: bool = False


class SubscribeIn(LinkIn):
    name: str = Field(default="", max_length=400)
    background: bool = False
    client_ref: str | None = Field(default=None, max_length=64)


class HolidaysIn(BaseModel):
    country: str = Field(max_length=4)
    regions: list[str] = Field(default_factory=list, max_length=40)
    school: bool = False


class StartIn(BaseModel):
    source_id: int | None = None


class NoticeIn(BaseModel):
    create: bool


class GoogleSetupIn(BaseModel):
    client_id: str = Field(max_length=500)
    client_secret: str = Field(default="", max_length=500)
    #: Members are connected and the values change: they all have to connect again (TIME-16 EC-2).
    confirm: bool = False


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _rows(db: DbSession, account: Account) -> dict[str, Any]:
    result = sources.rows(db, account)
    # Not set up: the read-only way; set up and switched off: no way "Google" at all (TIME-16 AC-4, AC-5).
    result["google"] = {"ready": google.enabled(db), "configured": google.configured(db)}
    return result


@router.get("", summary="The member's sources, their state, and notices about refused changes")
def list_sources(account: Account, db: DbSession) -> dict[str, Any]:
    return _rows(db, account)


@router.post("/dav", status_code=201, summary="Connect iCloud or another CalDAV server")
def connect(payload: DavIn, response: Response, account: Account, db: DbSession) -> dict[str, Any]:
    row, new = sources.connect_dav(db, account, payload.kind, server=payload.server, username=payload.username,
                                   password=payload.password, client_ref=payload.client_ref)
    if not new:
        response.status_code = 200
    return sources.choices(db, account, row.id)


@router.get("/holidays", summary="Countries and regions for holidays and school holidays")
def holiday_regions(account: Account, db: DbSession) -> dict[str, Any]:
    return {"countries": holidays.regions(events.language_of(account))}


@router.put("/holidays", summary="Holidays and school holidays of one country")
def set_holidays(payload: HolidaysIn, account: Account, db: DbSession) -> dict[str, Any]:
    holidays.set_up(db, account, country=payload.country, chosen=payload.regions, school=payload.school)
    return _rows(db, account)


@router.post("/subscriptions/preview", summary="What a subscription link holds")
def preview(payload: LinkIn, account: Account, db: DbSession) -> dict[str, Any]:
    return subscriptions.preview(db, account, payload.url, username=payload.username, password=payload.password,
                                 google=payload.google)


@router.post("/subscriptions", status_code=201, summary="Subscribe to a calendar link")
def subscribe(payload: SubscribeIn, response: Response, account: Account, db: DbSession) -> dict[str, Any]:
    row, new = subscriptions.subscribe(db, account, payload.url, name=payload.name, background=payload.background,
                                       username=payload.username, password=payload.password, google=payload.google,
                                       client_ref=payload.client_ref)
    if not new:
        response.status_code = 200
    result = _rows(db, account)
    result["created"] = row.id
    return result


@router.post("/google/start", summary="The address of \"Continue to Google\"")
def google_start(payload: StartIn, request: Request, account: Account, db: DbSession) -> dict[str, str]:
    return {"url": google.start(db, account, _base(request), payload.source_id)}


@router.post("/notices/{notice_id}", summary="\"Create as a new event\" or \"Discard\"")
def settle(notice_id: int, payload: NoticeIn, account: Account, db: DbSession) -> dict[str, Any]:
    made = sources.settle_notice(db, account, notice_id, create=payload.create)
    result = _rows(db, account)
    result["created_event"] = made
    return result


@router.get("/{source_id}/calendars", summary="The calendars of a source, to choose from")
def source_calendars(source_id: int, account: Account, db: DbSession) -> dict[str, Any]:
    return sources.choices(db, account, source_id)


@router.put("/{source_id}/calendars", summary="The calendars of a source that show in nextime")
def choose(source_id: int, payload: ChoiceIn, account: Account, db: DbSession) -> dict[str, Any]:
    sources.choose(db, account, source_id, payload.urls, drop_waiting=payload.drop_waiting)
    return _rows(db, account)


@router.delete("/{source_id}/calendars/{calendar_id}", summary="One calendar of a source out of nextime")
def leave(source_id: int, calendar_id: int, account: Account, db: DbSession,
          drop_waiting: bool = False) -> dict[str, Any]:
    sources.leave(db, account, source_id, calendar_id, drop_waiting=drop_waiting)
    return _rows(db, account)


@router.post("/{source_id}/reconnect", summary="A new password for an expired access")
def reconnect(source_id: int, payload: PasswordIn, account: Account, db: DbSession) -> dict[str, Any]:
    sources.reconnect_dav(db, account, source_id, payload.password)
    return _rows(db, account)


@router.post("/{source_id}/refresh", summary="Fetch again now")
def refresh(source_id: int, account: Account, db: DbSession) -> dict[str, Any]:
    sources.refresh(db, account, source_id)
    db.expire_all()
    return _rows(db, account)


@router.put("/{source_id}/link", summary="A new link for a subscription")
def change_link(source_id: int, payload: LinkIn, account: Account, db: DbSession) -> dict[str, Any]:
    subscriptions.change_link(db, account, source_id, payload.url)
    db.expire_all()
    return _rows(db, account)


@router.delete("/{source_id}", summary="Disconnect a source; at the source everything stays")
def disconnect(source_id: int, account: Account, db: DbSession, drop_waiting: bool = False) -> dict[str, Any]:
    sources.disconnect(db, account, source_id, drop_waiting=drop_waiting)
    return _rows(db, account)


@callback_router.get("/api/google/callback", include_in_schema=False)
def google_callback(account: Account, db: DbSession, code: str = Query(default="", max_length=2000),
                    state: str = Query(default="", max_length=200),
                    error: str = Query(default="", max_length=200)) -> RedirectResponse:
    return RedirectResponse(google.callback(db, account, code=code, state=state, problem=error), status_code=303)


@server_router.get("", summary="The Google Cloud client of this server")
def google_setup(request: Request, _operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    return google.setup_view(db, _base(request))


@server_router.put("", summary="Set up Google: client ID and secret, checked at Google at once")
def save_google(payload: GoogleSetupIn, request: Request, _operator: OperatorAccount,
                db: DbSession) -> dict[str, Any]:
    return google.save_setup(db, _base(request), client_id=payload.client_id, client_secret=payload.client_secret,
                             confirm=payload.confirm)


@server_router.delete("", summary="Remove the Google set-up")
def remove_google(request: Request, _operator: OperatorAccount, db: DbSession, confirm: bool = False) -> dict[str, Any]:
    return google.remove_setup(db, _base(request), confirm=confirm)


@invitations_router.get("", summary="The open invitations from now on (TIME-18)")
def open_invitations(account: Account, db: DbSession) -> list[dict[str, Any]]:
    return invitations.open_ones(db, account)
