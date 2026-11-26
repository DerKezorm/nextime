"""The API for programs (TIME-26) under ``/api/v1/``, as the other apps of the family offer it: a key in
``Authorization: Bearer``, errors as ``{"detail": {"code", "message"}}``, a description at ``/api/v1/docs``.

Walls, in this order (``wall``):

1. A request that carries an ``Origin`` is refused (EC-14): the API is for programs, not web pages. The session cookie
   counts for nothing here.
2. The operator's switch (``api_enabled``, AC-5), read on every request: off, every route answers ``api_off`` before a
   key is looked at; the keys stay and work again once it is on.
3. Wrong keys count per sender like wrong passwords (``too_many_attempts``, EC-2).
4. A valid key of an account that may sign in (``token_invalid``, EC-1, EC-13).
5. The key's rate (``slow_down``, EC-2).
6. A route that writes needs a key that may write (``token_reads_only``, EC-5).

Which calendar and event the key may touch is ``services/apiv1.py``. What is here stays as it is: new fields and routes
may come, nothing is renamed or removed (AC-9).
"""

from __future__ import annotations

import html
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..db import SessionLocal
from ..deps import client_ip, require_account
from ..errors import detail, error
from ..models import Account as AccountRow
from ..models import Event as EventRow
from ..security import brake as sign_in_brake
from ..services import (
    apibrake,
    apikeys,
    apiv1,
    dashboard,
    events,
    imip,
    invitations,
    live,
    logs,
    search,
    verbinden_store,
)
from ..services.apiv1 import Caller
from ..vendor import nexverbinden
from . import devices
from .events import EventChangeIn, EventIn, _change

logger = logging.getLogger("nextime.api")

PREFIX = "/api/v1"
#: Wrong keys one sender may bring before it waits.
FREE_WRONG_KEYS = 20


class Problem(BaseModel):
    code: str = Field(description="What went wrong, for programs: e.g. token_invalid, not_found, slow_down.")
    message: str = Field(description="The same in English, for people.")


class ProblemOut(BaseModel):
    detail: Problem


ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemOut, "description": "`token_invalid`: no key, or one unknown, expired or revoked, or its "
                                             "account blocked."},
    403: {"model": ProblemOut, "description": "`api_off`, `origin_refused`, `token_reads_only`, "
                                             "`calendar_read_only`."},
    409: {"model": ProblemOut, "description": "A state that does not allow it: e.g. `not_an_invitation`, "
                                             "`answers_not_possible` (with `why`), `invitation_changed`."},
    404: {"model": ProblemOut, "description": "`not_found`: not there, or not in this key's calendars."},
    429: {"model": ProblemOut, "description": "`slow_down` (more than 600 requests a minute, see `Retry-After`) or "
                                             "`too_many_attempts` (too many wrong keys from this sender)."},
}

router = APIRouter(prefix=PREFIX, tags=["api"], responses=ERRORS)
#: The description itself: for members signed in to nextime, not for keys (AC-8).
docs_router = APIRouter(prefix=PREFIX, include_in_schema=False)


def refused(status: int, code: str, text: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status_code=status, detail=detail(code, text), headers=headers)


def bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else None


def authenticate(request: Request, *, where: str) -> Caller:
    """The walls both places share: ``where`` is ``api`` or ``mcp``."""
    if request.headers.get("origin"):
        raise refused(403, "origin_refused", "This API is for programs, not web pages.")
    sender = f"{where}:" + client_ip(request)
    token = bearer(request)
    if where == "api" and token and token.startswith(verbinden_store.CONFIG.access_prefix):
        return _connection(request, token, sender)
    with SessionLocal() as db:
        if where == "api" and not apikeys.api_on(db):
            raise refused(403, "api_off", "The operator has switched the API off.")
        if where == "mcp" and not apikeys.mcp_on(db):
            raise refused(403, "mcp_off", "The operator has switched MCP off.")
        wait = sign_in_brake.wait_seconds(sender, FREE_WRONG_KEYS)
        if wait:
            raise refused(429, "too_many_attempts", "Too many wrong keys. Try again later.",
                          {"Retry-After": str(wait)})
        found = apikeys.authenticate(db, token)
        if found is not None and where == "mcp" and not found[1].allow_mcp:
            # A key without "for MCP" is no key there (AC-3).
            found = None
        if found is not None:
            account, row = found
            caller = apiv1.caller_of(account, row)
            db.expunge(account)
    if found is None:
        sign_in_brake.failed(sender)
        raise refused(401, "token_invalid", "No valid key.")
    if not apibrake.allow(where, caller.key_id):
        raise refused(429, "slow_down", "Too many requests with this key. Wait a minute.", {"Retry-After": "60"})
    logs.set_actor(caller.account.name)
    return caller


def _connection(request: Request, token: str, sender: str) -> Caller:
    """A token of another nex app connected with "Verbinden" (the shared module verbinden): it acts as its account
    with the right of its purposes and the calendars given at the consent (stage 1: a purpose stands for the right a
    key had, nothing of the routes' own checks changes). Its own switch counts ("Verbindungen mit anderen nex-Apps"),
    not the one of API keys; the brake for wrong keys and the rate per key are those of the API."""
    wait = sign_in_brake.wait_seconds(sender, FREE_WRONG_KEYS)
    if wait:
        raise refused(429, "too_many_attempts", "Too many wrong keys. Try again later.", {"Retry-After": str(wait)})
    with SessionLocal() as db:
        try:
            found = nexverbinden.authenticate(verbinden_store.Store(db), verbinden_store.CONFIG, token, apikeys.clock())
        except nexverbinden.Refused as refusal:
            if refusal.code == "token_invalid":
                sign_in_brake.failed(sender)
            body = refusal.as_dict()
            raise refused(refusal.status, str(body["code"]), str(body.get("message") or "")) from None
        account = db.get(AccountRow, int(found.account_id))
        if account is None:
            raise refused(401, "token_invalid", "No valid key.")
        db.expunge(account)
    caller = Caller(account=account, key_id=f"connection-{found.connection_id}",  # type: ignore[arg-type]
                    key_name=found.partner, level=found.level or "read",
                    calendar_ids=[int(area) for area in found.areas] if found.areas is not None else None)
    if not apibrake.allow("api", caller.key_id):
        raise refused(429, "slow_down", "Too many requests with this key. Wait a minute.", {"Retry-After": "60"})
    logs.set_actor(account.name)
    return caller


def wall(request: Request, *, write: bool = False) -> Caller:
    caller = authenticate(request, where="api")
    if write and not caller.writes:
        raise refused(403, "token_reads_only", "This key may only read.")
    if request.method == "GET":
        # Reading only at the deep levels of the log: the route and the key's number, never a content (AC-10).
        logger.debug("API read path=%s key_id=%s", request.url.path, caller.key_id)
    return caller


def _reader(request: Request) -> Caller:
    return wall(request)


def _writer(request: Request) -> Caller:
    return wall(request, write=True)


Reader = Annotated[Caller, Depends(_reader)]
Writer = Annotated[Caller, Depends(_writer)]
EventId = Annotated[int, Path(ge=1, le=2**62)]


# --- Who the key is (AC-9) ------------------------------------------------------------------------------------------


@router.get("/me", summary="Who the key reads as, what it may, what this nextime offers")
def me(caller: Reader) -> dict[str, Any]:
    account = caller.account
    with SessionLocal() as db:
        reach = caller.reach(db)
        chosen = None if caller.calendar_ids is None else [
            {"id": found, "name": reach[found].calendar.name} for found in caller.calendar_ids if found in reach]
    return {"app": "nextime", "name": account.name, "display_name": account.shown_name, "level": caller.level,
            "mcp": caller.mcp, "calendars": chosen, "expires_at": apiv1.iso(caller.expires_at),
            "time_zone": apiv1.zone_name(account), "version": __version__, "features": apiv1.FEATURES,
            "key": {"id": caller.key_id, "name": caller.key_name}}


# --- Calendars ------------------------------------------------------------------------------------------------------


@router.get("/calendars", summary="The calendars the key sees, in the order of the member's list")
def calendar_list(caller: Reader) -> dict[str, Any]:
    """Each calendar: `id`, `name`, `color`, `icon`, `background`, `right`, `writable`, `busy_only`, `source`,
    `owner`, `default`, and where it stands in nextime's sidebar: `group` (`own`, `icloud`, `google`, `caldav`,
    `shared`, `subscriptions`; subscriptions and holidays share `subscriptions`), `source_name` (`iCloud`, `Google`,
    the name of a CalDAV account or subscription; "" for own and shared calendars) and `hidden` (the member hid it in
    nextime; a key for all calendars lists them too, and reads their events only when a question names them by
    `calendars`)."""
    with SessionLocal() as db:
        return {"calendars": apiv1.calendar_list(db, caller)}


@router.get("/calendars/{calendar_id}", summary="One calendar of the key")
def calendar_one(calendar_id: Annotated[int, Path(ge=1, le=2**62)], caller: Reader) -> dict[str, Any]:
    with SessionLocal() as db:
        found = apiv1.one_calendar(db, caller, calendar_id)
        default = apiv1.calendars.default_of(db, caller.account.id).calendar
        return apiv1.calendar_out(db, found, default.id if default else None, calendar_id in caller.hidden(db))


# --- Events (AC-2, EC-3 to EC-11) -----------------------------------------------------------------------------------


@router.get("/events", summary="Every occurrence in a window, series rolled out; or the events of one UID")
def event_list(
    caller: Reader,
    request: Request,
    start: Annotated[str | None, Query(max_length=40, description=(
        "ISO moment with zone, or a day (`2026-11-26`, from midnight in the account's zone). Needed without "
        "`uid`."))] = None,
    end: Annotated[str | None, Query(max_length=40, description=(
        "ISO moment with zone, or a day; at most 400 days later. Needed without `uid`."))] = None,
    calendars: Annotated[str | None, Query(max_length=4000, description="Only these calendars: `1,4`.")] = None,
    uid: Annotated[str | None, Query(min_length=1, max_length=255, description=(
        "Only the events with this iCalendar UID, in every calendar of the key, declined invitations too. Without "
        "`start`/`end` each event comes once, as it begins (a series with its first occurrence, `occurrence` "
        "null); with them, its occurrences in that window."))] = None,
) -> dict[str, Any]:
    """Each item: `id`, `occurrence` (null, or `20261126T173000Z`/`20261126` for one of a series), `calendar_id`,
    `title`, `location`, `busy`, `busy_only`, `all_day`, `start`, `end` (UTC with `Z`; a whole day as dates, `end` its
    last day), `time_zone`, `recurring`, `writable`, `etag`, `waiting`, `background`, `person`, `invitation` (the
    member's state in an invitation: `open`, `accepted`, `tentative`, `declined`, else null), `uid`, `origin`
    (`imip` for an invitation entered from a mail through `POST /events/ical`, else null), `web_url` (where it opens
    in nextime), and `also_in` where the same event stands in more calendars. "Busy only" items carry their times
    only."""
    with SessionLocal() as db:
        zone = apiv1.zone_name(caller.account)
        base = devices.base_url(request, db)
        wanted = apiv1.parse_ids(calendars)
        if uid is not None and start is None and end is None:
            return {"events": apiv1.by_uid(db, caller, uid, wanted, base=base)}
        if start is None or end is None:
            raise error("invalid_input", "The input is not valid.", 422, fields=["start", "end"])
        found = apiv1.occurrences(db, caller, apiv1.moment(start, "start", zone), apiv1.moment(end, "end", zone),
                                  wanted, base=base, uid=uid)
    return {"events": found}


@router.get("/events/{event_id}", summary="One event, or one occurrence of a series")
def event_one(event_id: EventId, caller: Reader, request: Request,
              occurrence: Annotated[str | None, Query(max_length=20)] = None) -> dict[str, Any]:
    """Besides the fields of the list (times here as wall clock `YYYY-MM-DDTHH:MM` of `time_zone`): `notes`, `rule`,
    `series_start`, `source`, `created_by`, `updated_by`, `updated_via`, `updated_at`, `organizer` (`{name, email}`
    or null) and `attendees` (`[{name, email, status}]`, status `open`, `accepted`, `tentative` or `declined`)."""
    with SessionLocal() as db:
        return apiv1.event_out(db, caller, event_id, occurrence, devices.base_url(request, db))


class NewEvent(EventIn):
    """``start``/``end``: `YYYY-MM-DDTHH:MM` on the clock of `time_zone`, or `YYYY-MM-DD` with `all_day` (the end is
    the last day). `client_ref`: your own key for this event; the same again gives the event made the first time."""


class EventChange(EventChangeIn):
    """Only what changes. `scope` `this`/`following`/`all` with `occurrence` for a series; `etag` (or `If-Match`)
    names the version you read."""


def _etag(request: Request, given: str | None) -> str | None:
    header = (request.headers.get("if-match") or "").strip().removeprefix("W/").strip().strip('"')
    return header or given or None


@router.post("/events", status_code=201, summary="Make an event (in a calendar of the key)")
def event_make(body: NewEvent, caller: Writer, response: Response) -> dict[str, Any]:
    with SessionLocal() as db:
        change = _change(body, creating=True)
        change.calendar_id = apiv1.target_calendar(db, caller, body.calendar_id)
        with events.acting_via(caller.key_name):
            row, new = events.create(db, caller.account, change, body.client_ref)
        if row.calendar_id not in caller.listed(db):
            # An earlier event of the same client_ref made by another way, outside this key (EC-4).
            raise error("not_found", "Not found.", 404)
        if new:
            logger.info("API event made event_id=%s key_id=%s key=%s", row.id, caller.key_id, caller.key_name)
        else:
            response.status_code = 200
        return apiv1.event_out(db, caller, row.id)


# --- Invitations (for nexmail) ----------------------------------------------------------------------------------------


class AnswerIn(BaseModel):
    answer: Literal["accepted", "tentative", "declined"]
    occurrence: str | None = Field(default=None, max_length=20, description=(
        "Only this occurrence of a series (`20261126T173000Z` or `20261126`); left out, the whole event."))


@router.post("/events/{event_id}/answer", summary="Answer an invitation: accepted, tentative or declined")
def event_answer(event_id: EventId, body: AnswerIn, caller: Writer, request: Request) -> dict[str, Any]:
    """The member's answer to an invitation in a calendar of the key, by the rules of nextime's own buttons.

    `{"event": <the event as GET /events/{id}>, "reply_by": "source" | "client", "changed": bool}`:

    - `reply_by: "source"`: iCloud, Google or the CalDAV server sends the answer to the organizer. **Send no reply of
      your own.**
    - `reply_by: "client"`: nextime changed only the member's state (an invitation entered from a mail, or a CalDAV
      server that sends no answers); nothing went to the organizer, **you send the reply** (iMIP REPLY).
    - `changed: false`: it was already so (the same answer twice changes and sends nothing, also when two requests
      come at once).

    409 `not_an_invitation`, `answers_not_possible` (`why`: `locked`, `read_only_source`), `invitation_changed`;
    403 `calendar_read_only`; 422 `occurrence_unknown`."""
    with imip.held("answer", caller.account.id, event_id), SessionLocal() as db:
        apiv1.check_event(db, caller, event_id)
        with events.acting_via(caller.key_name):
            row, reply_by, changed = invitations.answer_by_program(db, caller.account, event_id, body.answer,
                                                                   occurrence=body.occurrence)
        if changed:
            logger.info("API invitation answered event_id=%s key_id=%s reply_by=%s", row.id, caller.key_id,
                        reply_by)
        return {"event": apiv1.event_out(db, caller, row.id, body.occurrence, devices.base_url(request, db)),
                "reply_by": reply_by, "changed": changed}


class IcalIn(BaseModel):
    ical: str = Field(min_length=1, max_length=imip.MAX_CHARS, description=(
        "The VCALENDAR of the mail (its text/calendar part), METHOD REQUEST, PUBLISH or CANCEL."))
    calendar_id: int | None = Field(default=None, ge=1, le=2**62, description=(
        "Where a new event goes (a writable calendar of the key); left out, the member's default calendar."))
    answer: Literal["accepted", "tentative", "declined"] | None = Field(default=None, description=(
        "The member's answer, its PARTSTAT in the event; needed for REQUEST and PUBLISH."))
    attendee: str | None = Field(default=None, max_length=320, description=(
        "The member's address among the attendees (the mailbox it arrived in); left out, the account's address or "
        "one of its iCloud, Google or CalDAV accounts."))


@router.post("/events/ical", status_code=201, summary="Enter an invitation from a mail (iMIP), sending nothing")
def event_from_mail(body: IcalIn, caller: Writer, request: Request, response: Response) -> dict[str, Any]:
    """The invitation as the organizer sent it (UID, ORGANIZER, ATTENDEE, SEQUENCE kept), the member's own attendee
    line answered with `answer`. **nextime sends nothing**: the organizer gets `SCHEDULE-AGENT=CLIENT`, iCloud and
    CalDAV servers get the event with `Schedule-Reply: F`, Google through `events.import`. The reply to the organizer
    is yours to send (`reply_by: "client"`).

    `{"status", "event", "reply_by", "trash"}`, by the UID in the key's calendars:

    - `created` (201): new, in `calendar_id`.
    - `existing` (200): there already, nothing new (a second delivery, or two requests at once: one event). An event
      from iCloud, Google or a CalDAV server is never changed here; then `reply_by` says who answers it (`source`:
      answer through `POST /events/{id}/answer`, not by mail).
    - `updated` (200): a copy entered from a mail took a higher SEQUENCE.
    - `cancelled` (200): METHOD CANCEL put the copies entered from a mail into the trash (`trash`), `event` null; an
      event from a source stays (`existing`), the source tells of the cancellation itself.

    Once iCloud, Google or a CalDAV server of the member brings the same UID, the copy goes into the trash and the
    member's answer moves to the source's event, again without anything being sent.

    404 `not_found` (a CANCEL for nothing there); 413 `ical_too_large`; 422 `invalid_ical`, `ical_method_unsupported`,
    `invalid_answer`, `not_an_invitation`, `attendee_unknown`; 403 `calendar_read_only`."""
    invitation = imip.read(body.ical)
    with imip.held("uid", caller.account.id, invitation.uid), SessionLocal() as db:
        target = None
        if invitation.method != imip.CANCEL:
            target = apiv1.target_calendar(db, caller, body.calendar_id)
        with events.acting_via(caller.key_name):
            outcome = imip.take(db, caller.account, caller.listed(db), invitation, calendar_id=target,
                                answer=body.answer, attendee=body.attendee or "")
        base = devices.base_url(request, db)
        reply_by = invitations.REPLY_BY_CLIENT
        if outcome.row is not None and outcome.row.origin != imip.ORIGIN_IMIP:
            reply_by = _who_answers(db, caller, outcome.row.id)
        if outcome.status != imip.CREATED:
            response.status_code = 200
        else:
            logger.info("API invitation entered event_id=%s key_id=%s key=%s", outcome.row.id if outcome.row else 0,
                        caller.key_id, caller.key_name)
        return {"status": outcome.status, "reply_by": reply_by, "trash": outcome.trash,
                "event": apiv1.event_out(db, caller, outcome.row.id, None, base) if outcome.row is not None else None}


def _who_answers(db: Any, caller: Caller, event_id: int) -> str:
    """For an event of a source: the source when it answers invitations itself, else the program."""
    row, item = events.find(db, caller.account, event_id, for_writing=False)
    mine, source = invitations.context_of(db, item, row)
    allowed, _why = invitations.can_answer(source, item.calendar, row)
    return invitations.REPLY_BY_SOURCE if mine and allowed else invitations.REPLY_BY_CLIENT


# --- A version to compare (for nexmail) -------------------------------------------------------------------------------


@router.get("/state", summary="A version that changes with every change the key may see")
def state(caller: Reader) -> dict[str, str]:
    """`{"version": "<opaque>"}`: changes whenever a calendar or event of the account changes (made in nextime, on a
    device or at a source), also when the member hides or shows a calendar. Compare it to skip asking again; read
    nothing into its form. After a restart of nextime it is new once."""
    return {"version": live.version(caller.account.id)}


@router.patch("/events/{event_id}", summary="Change an event, one occurrence, the following or the whole series")
def event_change(event_id: EventId, body: EventChange, caller: Writer, request: Request) -> dict[str, Any]:
    if body.scope != "all" and not body.occurrence:
        raise error("occurrence_unknown", "This occurrence is not part of the series.", 422)
    with SessionLocal() as db:
        apiv1.check_event(db, caller, event_id)
        if body.start is not None and not body.all_day and not body.time_zone:
            # Times without a zone are in the zone the event has, else the account's (review, 2026-11-23); before,
            # this answered "time zone not known" for a zone nobody had named.
            found = db.get(EventRow, event_id)
            body = body.model_copy(update={"time_zone": (found.time_zone if found is not None and found.time_zone
                                                         else apiv1.zone_name(caller.account))})
        change = _change(body, creating=False)
        if body.calendar_id is not None:
            apiv1.one_calendar(db, caller, body.calendar_id)
        with events.acting_via(caller.key_name):
            row = events.update(db, caller.account, event_id, change, scope=body.scope, occurrence=body.occurrence,
                                base_etag=_etag(request, body.etag))
        logger.info("API event changed event_id=%s key_id=%s key=%s", row.id, caller.key_id, caller.key_name)
        return apiv1.event_out(db, caller, row.id, body.occurrence if body.scope == "this" else None,
                               devices.base_url(request, db))


@router.delete("/events/{event_id}", summary="Delete an event, one occurrence or the following: into the trash")
def event_delete(event_id: EventId, caller: Writer,
                 scope: Literal["this", "following", "all"] = "all",
                 occurrence: Annotated[str | None, Query(max_length=20)] = None) -> dict[str, Any]:
    """Never for good (EC-10): the entry of the trash it became, `{"trash": {"kind": "event" | "part", "id"}}`."""
    with SessionLocal() as db:
        apiv1.check_event(db, caller, event_id)
        with events.acting_via(caller.key_name):
            entry = events.delete(db, caller.account, event_id, scope=scope, occurrence=occurrence)
        logger.info("API event deleted event_id=%s key_id=%s key=%s", event_id, caller.key_id, caller.key_name)
        return {"trash": entry}


# --- Search and free times ------------------------------------------------------------------------------------------


@router.get("/search", summary="Events by title, place and notes, coming first, then past")
def find(caller: Reader, q: Annotated[str, Query(max_length=2000)] = "",
         limit: Annotated[int, Query(ge=1, le=search.PAGE_MAX)] = 50,
         offset: Annotated[int, Query(ge=0, le=10_000_000)] = 0) -> dict[str, Any]:
    with SessionLocal() as db:
        reach = caller.reach(db)
        found = search.search(db, caller.account, q, only=set(reach), now=apiv1.now(),
                              zone=apiv1.zone_name(caller.account), offset=offset, limit=limit,
                              include_hidden=True)
    items = [{key: hit[key] for key in ("id", "occurrence", "calendar_id", "title", "all_day", "start", "end",
                                        "time_zone", "recurring", "coming")} for hit in found["items"]]
    return {"total": found["total"], "events": items}


@router.get("/dashboard", summary="Today and tomorrow for a card on a nexdeck dashboard")
def dashboard_card(caller: Reader) -> dict[str, Any]:
    """The nexdeck card (TIME-27): the running or next event, the rest of today, tomorrow, background titles, in the
    zone of the key's account; only the key's calendars; "busy only" as "Busy" without place or link."""
    with SessionLocal() as db:
        return dashboard.card(db, caller)


@router.get("/free", summary="Free times between the events marked busy")
def free(
    caller: Reader,
    start: Annotated[str, Query(max_length=40)],
    end: Annotated[str, Query(max_length=40)],
    minutes: Annotated[int, Query(ge=5, le=24 * 60, description="The shortest gap that counts.")] = 30,
    day_start: Annotated[str | None, Query(max_length=5, description="`08:00`: each day from then on.")] = None,
    day_end: Annotated[str | None, Query(max_length=5, description="`18:00`: each day until then.")] = None,
    calendars: Annotated[str | None, Query(max_length=4000)] = None,
) -> dict[str, Any]:
    with SessionLocal() as db:
        zone = apiv1.zone_name(caller.account)
        return apiv1.free_times(db, caller, apiv1.moment(start, "start", zone), apiv1.moment(end, "end", zone),
                                minutes=minutes, day_start=day_start, day_end=day_end,
                                wanted=apiv1.parse_ids(calendars))


# --- The description (AC-8) ----------------------------------------------------------------------------------------

DOCS_TEXT = (
    "Programs read and write the calendars of one account with a key from My account › Connections › API keys: "
    "`Authorization: Bearer nt_api_...`. A key never sees more than its account and its calendars; a calendar "
    "shared \"busy only\" gives times only. Errors come as `{\"detail\": {\"code\", \"message\"}}`. "
    "Times are ISO 8601; `start`/`end` of an event you send are wall clock times of `time_zone`. "
    "Requests with an `Origin` header are refused, so this page cannot try them out: use `curl` as in the "
    "examples. AI tools use `/api/mcp` with a key made \"for MCP\". Invitations: `POST /events/{id}/answer` and "
    "`POST /events/ical` say in `reply_by` who sends the reply to the organizer (`source` or you, `client`); "
    "`GET /state` gives a version to compare before asking again. What stands under `/api/v1/` stays: fields "
    "and routes may be added, none renamed or removed."
)

#: A body for the examples of the routes that take one.
EXAMPLE_BODIES = {
    ("post", "/api/v1/events"): ('{"title": "Dentist", "start": "2026-11-27T15:00", "end": "2026-11-27T15:45", '
                                 '"time_zone": "Europe/Berlin", "client_ref": "my-script-0815"}'),
    ("patch", "/api/v1/events/{event_id}"): '{"title": "Dentist with Ben", "etag": "<etag>"}',
    ("post", "/api/v1/events/{event_id}/answer"): '{"answer": "accepted"}',
    ("post", "/api/v1/events/ical"): ('{"calendar_id": 3, "answer": "accepted", "attendee": "me@example.com", '
                                      '"ical": "BEGIN:VCALENDAR\\r\\nMETHOD:REQUEST\\r\\n...END:VCALENDAR\\r\\n"}'),
}


def _example(base: str, path: str, method: str) -> str:
    auth = '-H "Authorization: Bearer $NEXTIME_KEY"'
    if (method, path) in EXAMPLE_BODIES:
        line = (f'curl -X {method.upper()} {auth} -H "Content-Type: application/json" '
                f"-d '{EXAMPLE_BODIES[(method, path)]}' \"{base}{path}\"")
    elif method == "get" and path.endswith("/events"):
        line = (f'curl {auth} "{base}{path}?start=2026-11-23&end=2026-11-30"\n'
                f'curl {auth} "{base}{path}?uid=meeting-0815@example.com"')
    elif method == "get" and path.endswith("/free"):
        line = f'curl {auth} "{base}{path}?start=2026-11-23&end=2026-11-28&minutes=60&day_start=08:00&day_end=18:00"'
    elif method == "get" and path.endswith("/search"):
        line = f'curl {auth} "{base}{path}?q=dentist"'
    elif method == "get":
        line = f'curl {auth} "{base}{path}"'
    else:
        line = f'curl -X {method.upper()} {auth} "{base}{path}"'
    return f"\n\n```sh\n{line}\n```"


def openapi(base: str) -> dict[str, Any]:
    """The description of every route under ``/api/v1/`` and nothing else, with an example each."""
    schema = get_openapi(title="nextime API", version=__version__, description=DOCS_TEXT, routes=router.routes,
                         servers=[{"url": base}])
    schema.setdefault("components", {})["securitySchemes"] = {
        "bearerAuth": {"type": "http", "scheme": "bearer", "description": "A key (`nt_api_...`)."}}
    schema["security"] = [{"bearerAuth": []}]
    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            operation["description"] = (operation.get("description") or "") + _example(base, path, method)
    return schema


def _browser_member(request: Request) -> AccountRow:
    """A member signed in in the browser (AC-8): its session, never a key."""
    if request.headers.get("authorization"):
        raise error("sign_in_required", "Sign in first.", 401)
    return require_account(request)


BrowserMember = Annotated[AccountRow, Depends(_browser_member)]


@docs_router.get("/openapi.json")
def openapi_json(_account: BrowserMember, request: Request) -> JSONResponse:
    with SessionLocal() as db:
        base = devices.base_url(request, db)
    return JSONResponse(openapi(base), headers={"Cache-Control": "no-store"})


@docs_router.get("/docs")
def docs(_account: BrowserMember) -> HTMLResponse:
    with SessionLocal() as db:
        off = not apikeys.api_on(db)
    notice = ('<p class="nt-api-off">The operator has switched the API off. Existing keys work again once it is '
              "switched on.</p>") if off else ""
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape('nextime API')}</title>"
        '<link rel="stylesheet" href="/api-docs/swagger-ui.css"><link rel="stylesheet" href="/api-docs/docs.css">'
        f'</head><body>{notice}<div id="swagger-ui"></div>'
        '<script src="/api-docs/swagger-ui-bundle.js"></script><script src="/api-docs/init.js"></script>'
        "</body></html>"
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})
