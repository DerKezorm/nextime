"""Sharing calendars (TIME-19, ``services/shares.py``) and what a guest with a read link sees (``services/guest.py``).

| Method | Path | What |
|---|---|---|
| GET | ``/api/shares`` | "Shares": what the account shares, what is shared with it, notices of calendars that went |
| GET | ``/api/shares/people?q=`` | members by name or address, for "Share …" |
| DELETE | ``/api/shares/notices/{id}`` | put a notice away ("Hide") |
| GET | ``/api/calendars/{id}/sharing`` | the dialog "Share …": people with their right, the guest link (owner only) |
| PUT | ``/api/calendars/{id}/sharing/{account_id}`` | ``{"right"}``: share with a member, or change the right |
| DELETE | ``/api/calendars/{id}/sharing/{account_id}`` | take a share back |
| PUT | ``/api/calendars/{id}/guest-link`` | ``{"expires_on"}``: switch the guest link on, or change its last day |
| POST | ``/api/calendars/{id}/guest-link/renew`` | a new address, when the stored one cannot be read any more |
| DELETE | ``/api/calendars/{id}/guest-link`` | revoke it |
| POST | ``/api/calendars/{id}/leave`` | leave a calendar shared with the account |
| GET | ``/api/guest/{token}?offset=`` | without sign-in: the guest page's calendar, owner and coming events |
| GET | ``/abo/{token}.ics`` | without sign-in: the subscription |
| GET | ``/abo/{token}`` | without sign-in: the guest page (the interface draws it) |
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import SessionLocal
from ..deps import Account, DbSession, client_ip
from ..errors import detail
from ..security import brake
from ..services import guest, shares
from .devices import base_url

router = APIRouter(tags=["shares"])
logger = logging.getLogger("nextime.shares")

#: Wrong link addresses from one sender that pass before the brake waits (EC-4).
FREE_TRIES = 20


class RightIn(BaseModel):
    right: str = Field(max_length=16)


class LinkIn(BaseModel):
    expires_on: str | None = Field(default=None, max_length=10)


@router.get("/api/shares", summary="What the account shares and what is shared with it")
def overview(account: Account, db: DbSession) -> dict[str, Any]:
    return shares.overview(db, account)


@router.get("/api/shares/people", summary="Members by name or address")
def people(account: Account, db: DbSession, q: str = Query(default="", max_length=200)) -> dict[str, Any]:
    return {"people": shares.people(db, account, q)}


@router.delete("/api/shares/notices/{notice_id}", status_code=204, summary="Put a notice away")
def dismiss(notice_id: int, account: Account, db: DbSession) -> None:
    shares.dismiss(db, account, notice_id)


@router.get("/api/calendars/{calendar_id}/sharing", summary="Who the calendar is shared with")
def sharing(calendar_id: int, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    return shares.sharing(db, account, calendar_id, base_url(request, db))


@router.put("/api/calendars/{calendar_id}/sharing/{person_id}", summary="Share with a member, or change the right")
def share(calendar_id: int, person_id: int, payload: RightIn, request: Request, account: Account,
          db: DbSession) -> dict[str, Any]:
    shares.share(db, account, calendar_id, person_id, payload.right)
    return shares.sharing(db, account, calendar_id, base_url(request, db))


@router.delete("/api/calendars/{calendar_id}/sharing/{person_id}", summary="Take a share back")
def unshare(calendar_id: int, person_id: int, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    shares.unshare(db, account, calendar_id, person_id)
    return shares.sharing(db, account, calendar_id, base_url(request, db))


@router.put("/api/calendars/{calendar_id}/guest-link", summary="Switch the guest link on, or change its last day")
def set_link(calendar_id: int, payload: LinkIn, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    shares.set_link(db, account, calendar_id, payload.expires_on)
    return shares.sharing(db, account, calendar_id, base_url(request, db))


@router.post("/api/calendars/{calendar_id}/guest-link/renew", summary="A new address for the guest link")
def renew_link(calendar_id: int, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    shares.renew_link(db, account, calendar_id)
    return shares.sharing(db, account, calendar_id, base_url(request, db))


@router.delete("/api/calendars/{calendar_id}/guest-link", summary="Revoke the guest link")
def revoke_link(calendar_id: int, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    shares.revoke_link(db, account, calendar_id)
    return shares.sharing(db, account, calendar_id, base_url(request, db))


@router.post("/api/calendars/{calendar_id}/leave", status_code=204, summary="Leave a calendar shared with you")
def leave(calendar_id: int, account: Account, db: DbSession) -> None:
    shares.leave(db, account, calendar_id)


# --- Guests (no sign-in) ------------------------------------------------------------------------------------------


class _Braked(Exception):
    """The sender tried too many wrong addresses: wait this many seconds (EC-4)."""

    def __init__(self, seconds: int) -> None:
        super().__init__(seconds)
        self.seconds = seconds


def _guest_link(request: Request, token: str) -> Any:
    """The link behind the token, or ``None``: unknown, revoked, run out, switched off all answer alike, without the
    calendar's name (EC-1). Wrong addresses from one sender slow down after a few (EC-4): ``_Braked``."""
    key = "guest:" + client_ip(request)
    wait = brake.wait_seconds(key, free=FREE_TRIES)
    if wait:
        raise _Braked(wait)
    with SessionLocal() as db:
        found = shares.find_link(db, token)
    if found is None:
        brake.failed(key)
    return found


@router.get("/api/guest/{token}", summary="What a guest with a read link sees", response_model=None)
def guest_page(token: str, request: Request,
               offset: int = Query(default=0, ge=0, le=1_000_000)) -> dict[str, Any] | Response:
    try:
        found = _guest_link(request, token)
    except _Braked as braked:
        return JSONResponse(status_code=429, headers={"Retry-After": str(braked.seconds), "Cache-Control": "no-store"},
                            content={"detail": detail("too_many_attempts", "Too many attempts. Try again later.",
                                                      retry_after=braked.seconds)})
    if found is None:
        return JSONResponse(status_code=404, headers={"Cache-Control": "no-store"},
                            content={"detail": detail("link_invalid", "This link is no longer valid.")})
    _link, calendar, owner = found
    with SessionLocal() as db:
        answer = guest.page(db, calendar, owner, offset)
        url = base_url(request, db)
    answer["feed"] = f"{url}{shares.guest_path(token)}.ics"
    return JSONResponse(content=answer, headers={"Cache-Control": "no-store"})


@router.get("/abo/{token}.ics", include_in_schema=False, response_model=None)
def guest_feed(token: str, request: Request) -> Response:
    """The subscription. A calendar program gets no text: a link that no longer holds is 404 (EC-1); a sender that
    tried too many wrong addresses is told to wait (429 with ``Retry-After``), so a program does not take a valid link
    for dead; anything else is 503, to be asked again later (review of TIME-19, finding 8)."""
    try:
        found = _guest_link(request, token)
    except _Braked as braked:
        return Response(status_code=429, headers={"Retry-After": str(braked.seconds), "Cache-Control": "no-store"})
    if found is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    link, calendar, _owner = found
    try:
        with SessionLocal() as db:
            body, etag = guest.feed(db, link, calendar)
    except Exception:
        logger.exception("A guest subscription could not be built calendar=%s", calendar.id)
        return Response(status_code=503, headers={"Retry-After": "60", "Cache-Control": "no-store"})
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match", "") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="text/calendar; charset=utf-8", headers=headers)


#: The page without the built interface (the tests, a server without it): nothing of the calendar in it, as in the
#: built one, where the page asks for its content itself (EC-11: a messenger's preview shows no events).
_BARE = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>nextime</title></head>'
         "<body></body></html>")


@router.get("/abo/{token}", include_in_schema=False, response_model=None)
def guest_shell(token: str) -> Response:
    index = get_settings().frontend_dist / "index.html"
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    if index.is_file():
        return FileResponse(index, headers=headers)
    return HTMLResponse(_BARE, headers=headers)
