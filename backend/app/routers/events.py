"""Events of the signed-in account (TIME-5) and their series (TIME-6).

| Method | Path | What |
|---|---|---|
| GET | ``/api/events?start=&end=&calendars=`` | every occurrence in the window, series rolled out (hidden ones not) |
| GET | ``/api/events/year?year=&zone=&calendars=&hidden=`` | how many events each day of a year holds (TIME-12 AC-16) |
| GET | ``/api/events/{id}?occurrence=`` | one event or one occurrence for the side panel |
| POST | ``/api/events`` | a new event (201; the same ``client_ref`` again: 200 with the first one) |
| PATCH | ``/api/events/{id}`` | what the member changed; ``scope`` this/following/all with ``occurrence`` |
| DELETE | ``/api/events/{id}?scope=&occurrence=`` | into the trash, or one/the following occurrences |
| POST | ``/api/events/{id}/conflict`` | ``{"restore": true}`` "Restore mine", ``false`` "Leave it" |
| POST | ``/api/events/{id}/answer`` | answer an invitation: ``accepted``, ``tentative``, ``declined`` (TIME-18) |
| PUT | ``/api/events/{id}/reminders`` | the member's own reminders (TIME-7), with any right |

Times: ``YYYY-MM-DD`` for a whole day (``end`` the last day), otherwise ``YYYY-MM-DDTHH:MM`` on the clock of
``time_zone``. An occurrence is named by its original start (``20261126T173000Z``, or ``20261126``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from ..deps import Account, DbSession
from ..errors import error
from ..services import display, events, rules, yearmap

router = APIRouter(prefix="/api/events", tags=["events"])


class RuleIn(BaseModel):
    freq: Literal["daily", "weekly", "monthly", "yearly"]
    interval: int = Field(default=1, ge=-100000, le=100000)
    weekdays: list[str] = Field(default_factory=list, max_length=7)
    monthly: Literal["date", "nth", "last"] = "date"
    end: Literal["never", "until", "count"] = "never"
    until: str | None = Field(default=None, max_length=10)
    count: int | None = Field(default=None, ge=-100000, le=100000)

    def model(self) -> rules.Rule:
        until = None
        if self.end == "until":
            try:
                until = date.fromisoformat(self.until or "")
            except ValueError:
                raise error("rule_until", "The rule of the series is not valid.", 422, field="rule.until") from None
        return rules.Rule(freq=self.freq, interval=self.interval, weekdays=tuple(day.upper() for day in self.weekdays),
                          monthly=self.monthly, end=self.end, until=until, count=self.count)


class GeoIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class EventIn(BaseModel):
    calendar_id: int | None = None
    title: str = Field(max_length=100_000)
    all_day: bool = False
    start: str = Field(max_length=20)
    end: str | None = Field(default=None, max_length=20)
    time_zone: str = Field(default="", max_length=64)
    busy: bool | None = None
    notes: str = Field(default="", max_length=1_000_000)
    location: str = Field(default="", max_length=100_000)
    #: The point of a chosen suggestion (TIME-8).
    geo: GeoIn | None = None
    rule: RuleIn | None = None
    client_ref: str | None = Field(default=None, max_length=64)
    #: The member's own reminders (TIME-7); left out: as the calendar's default.
    reminders: list[int] | None = Field(default=None, max_length=50)


class RemindersIn(BaseModel):
    """Minutes before the start, each once; ``null``: as the calendar's default again (TIME-7)."""

    minutes: list[int] | None = Field(default=None, max_length=50)


class EventChangeIn(BaseModel):
    """Only what the member changed. Times come together: ``all_day`` and ``start`` (``end``, ``time_zone``)."""

    calendar_id: int | None = None
    title: str | None = Field(default=None, max_length=100_000)
    all_day: bool | None = None
    start: str | None = Field(default=None, max_length=20)
    end: str | None = Field(default=None, max_length=20)
    time_zone: str | None = Field(default=None, max_length=64)
    busy: bool | None = None
    notes: str | None = Field(default=None, max_length=1_000_000)
    location: str | None = Field(default=None, max_length=100_000)
    #: Sent (even as null) with a changed place: the point of a chosen suggestion, or none (TIME-8).
    geo: GeoIn | None = None
    #: Sent (even as null) when the rule changed; left out, the rule stays as it is (EC-9).
    rule: RuleIn | None = None
    scope: Literal["this", "following", "all"] = "all"
    occurrence: str | None = Field(default=None, max_length=20)
    #: The version the member started from (its ETag): saving over a newer one is a conflict (EC-2).
    etag: str | None = Field(default=None, max_length=64)


class ConflictIn(BaseModel):
    restore: bool


class AnswerIn(BaseModel):
    answer: Literal["accepted", "tentative", "declined"]
    scope: Literal["this", "all"] = "all"
    occurrence: str | None = Field(default=None, max_length=20)
    #: The version the member saw: an invitation changed meanwhile shows its new state instead (TIME-18 EC-1).
    etag: str | None = Field(default=None, max_length=64)


def _change(payload: EventIn | EventChangeIn, *, creating: bool) -> events.Change:
    change = events.Change()
    given = payload.model_fields_set
    if creating or payload.title is not None:
        change.title = events.clean_title(payload.title or "")
    if creating or payload.notes is not None:
        change.notes = events.clean_notes(payload.notes or "")
    if creating or payload.location is not None:
        change.location = events.clean_location(payload.location or "")
    if creating or "geo" in given:
        change.geo_set = True
        change.geo = events.clean_geo(payload.geo.lat, payload.geo.lon) if payload.geo is not None else None
    if payload.busy is not None:
        change.busy = payload.busy
    if creating or payload.start is not None:
        if payload.start is None:
            raise error("invalid_date", "This date does not exist.", 422, field="start")
        change.times = events.parse_times(bool(payload.all_day), payload.start, payload.end, payload.time_zone or "")
    if creating or "rule" in given:
        change.rule_set = True
        change.rule = payload.rule.model() if payload.rule is not None else None
    change.calendar_id = payload.calendar_id
    if creating and isinstance(payload, EventIn):
        change.reminders = payload.reminders
    return change


def _moment(text: str, name: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=[name]) from None
    if value.tzinfo is None:
        raise error("invalid_input", "The input is not valid.", 422, fields=[name])
    return value


@router.get("", summary="Every occurrence in a window, series rolled out")
def list_events(account: Account, db: DbSession, start: str = Query(max_length=40), end: str = Query(max_length=40),
                hidden: bool = False, declined: bool = False,
                calendars: str | None = Query(default=None, max_length=4000)) -> list[dict[str, Any]]:
    """``calendars=1,4``: only those (the collection chosen on the device, TIME-13 AC-3)."""
    only = None
    if calendars is not None:
        try:
            only = {int(part) for part in calendars.split(",") if part.strip()}
        except ValueError:
            raise error("invalid_input", "The input is not valid.", 422, fields=["calendars"]) from None
    return events.occurrence_list(db, account, _moment(start, "start"), _moment(end, "end"), include_hidden=hidden,
                                  only=only, include_declined=declined)


def _only(calendars: str | None) -> set[int] | None:
    if calendars is None:
        return None
    try:
        return {int(part) for part in calendars.split(",") if part.strip()}
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=["calendars"]) from None


@router.get("/year", summary="How many events each day of a year holds (the year as a heat map)")
def year_map(account: Account, db: DbSession, year: int = Query(ge=yearmap.FIRST_YEAR, le=yearmap.LAST_YEAR),
             zone: str | None = Query(default=None, max_length=64), hidden: bool = False,
             calendars: str | None = Query(default=None, max_length=4000)) -> dict[str, Any]:
    """Counts only (TIME-12 AC-17, AC-18): ``days`` maps a date to its number of events, ``holidays`` lists the days
    of calendars with "Tage hervorheben". ``zone`` is the zone the views show (default: the account's);
    ``calendars`` and ``hidden`` as for the window above."""
    chosen = zone or str(getattr(display.zone_of(account), "key", "") or "UTC")
    if not display.valid_zone(chosen):
        raise error("invalid_input", "The input is not valid.", 422, fields=["zone"])
    return yearmap.year_map(db, account, year, chosen, include_hidden=hidden, only=_only(calendars))


@router.get("/{event_id}", summary="One event or one occurrence of a series")
def get_event(event_id: int, account: Account, db: DbSession,
              occurrence: str | None = Query(default=None, max_length=20)) -> dict[str, Any]:
    return events.detail(db, account, event_id, occurrence)


@router.post("", status_code=201, summary="A new event")
def create_event(payload: EventIn, response: Response, account: Account, db: DbSession) -> dict[str, Any]:
    row, new = events.create(db, account, _change(payload, creating=True), payload.client_ref)
    if not new:
        response.status_code = 200
    return events.detail(db, account, row.id)


@router.patch("/{event_id}", summary="Change an event, one occurrence, the following or the whole series")
def change_event(event_id: int, payload: EventChangeIn, account: Account, db: DbSession) -> dict[str, Any]:
    if payload.scope != "all" and not payload.occurrence:
        raise error("occurrence_unknown", "This occurrence is not part of the series.", 422)
    row = events.update(db, account, event_id, _change(payload, creating=False), scope=payload.scope,
                        occurrence=payload.occurrence, base_etag=payload.etag)
    # One occurrence keeps its name (its original start) after a change; a split series answers with the new one.
    return events.detail(db, account, row.id, payload.occurrence if payload.scope == "this" else None)


@router.delete("/{event_id}", summary="Delete an event, one occurrence or the following")
def delete_event(event_id: int, account: Account, db: DbSession,
                 scope: Literal["this", "following", "all"] = "all",
                 occurrence: str | None = Query(default=None, max_length=20)) -> dict[str, Any]:
    """Answers the entry of the trash it became, ``{"trash": {"kind": "event" | "part", "id": …}}``: the web brings it
    back from there with "Undo" (TIME-23 AC-2)."""
    return {"trash": events.delete(db, account, event_id, scope=scope, occurrence=occurrence)}


@router.put("/{event_id}/reminders", summary="The member's own reminders for an event (TIME-7)")
def set_reminders(event_id: int, payload: RemindersIn, account: Account, db: DbSession) -> dict[str, Any]:
    from ..services import reminders

    reminders.set_for_event(db, account, event_id, payload.minutes)
    return events.detail(db, account, event_id)


@router.post("/{event_id}/conflict", summary="\"Restore mine\" or \"Leave it\" after a conflict")
def resolve(event_id: int, payload: ConflictIn, account: Account, db: DbSession) -> dict[str, Any]:
    row = events.resolve_conflict(db, account, event_id, restore=payload.restore)
    return events.detail(db, account, row.id)


@router.post("/{event_id}/answer", summary="Answer an invitation from iCloud, Google or a CalDAV server")
def answer_invitation(event_id: int, payload: AnswerIn, account: Account, db: DbSession) -> dict[str, Any]:
    from ..services import invitations

    row = invitations.answer(db, account, event_id, payload.answer, scope=payload.scope,
                             occurrence=payload.occurrence, etag=payload.etag)
    return events.detail(db, account, row.id, payload.occurrence if payload.scope == "this" else None)
