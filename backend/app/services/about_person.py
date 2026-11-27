"""What nextime tells nexfolk about one person (verbinden 0.1.13, purpose ``events.about``, V49 to V53): nexfolk's
card "In den anderen Apps" shows the next event with the contact the account looks at.

**Which events concern the person.** An event concerns the person when the person takes part in it (one of its
attendees has one of the person's mail addresses, upper and lower case alike) or when its title names the person (the
whole name as nexfolk sends it, as whole words, upper and lower case alike); numbers are not looked at, events carry
none. Only events of the calendars the connection was given (as a key for them reads them: the ones named, for "all"
the ones the member shows, ``apiv1.Caller.reach``), never one in the trash, never an invitation the member declined.

**What goes out.** ``count``: how many of them are still to come or running now (a series once, as the search counts
it, TIME-22); ``next``: the first of them, ``last``: the latest that is over (for a series running on, its latest
occurrence before now); each with its title and its start (a moment in UTC, a date for a whole day). A calendar shared
"busy only" counts its busy events without a title, its free ones not at all (TIME-19 AC-2). ``open``: the day of the
next (else the last) in nextime, a path; never the place, the notes or the attendees. The module checks the question
and the connection, brakes it, and cuts the answer to its form (``persons.cleaned``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import icalendar
from sqlalchemy import func, or_, select

from ..models import RIGHT_BUSY, Account, Event
from . import apiv1, calendars, ical, invitations, search

NOTHING: dict[str, Any] = {"count": 0, "next": None, "last": None, "open": None}


@dataclass
class _Moment:
    """One occurrence of an event that concerns the person."""

    event_id: int
    occurrence: Any
    all_day: bool
    start: Any
    end: Any
    title: str
    owner: int
    uid: str
    busy_only: bool

    def key(self) -> tuple[str, str]:
        """By start (a whole day before the times of that day), as the search sorts."""
        return search._times_of((self.all_day, self.start, self.end))[0], self.title.casefold()


def name_pattern(name: str) -> re.Pattern[str] | None:
    """The whole name as whole words, upper and lower case alike (any blanks between its words)."""
    words = [re.escape(ical.fold_text(word)) for word in name.split()]
    if not words:
        return None
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)")


def _attendees(component: icalendar.cal.Component) -> list[Any]:
    value = component.get("ATTENDEE")
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def concerns(title: str, parsed: icalendar.Calendar | None, pattern: re.Pattern[str] | None,
             mails: set[str]) -> bool:
    """The rule of the module docstring: the person takes part, or the title names the person."""
    if pattern is not None and pattern.search(ical.fold_text(title or "")):
        return True
    if mails and parsed is not None:
        for component in ical.events_of(parsed):
            if any(invitations.address(item) in mails for item in _attendees(component)):
                return True
    return False


def _last_over(parsed: icalendar.Calendar, row: Event, now: datetime, today: date) -> tuple[Any, tuple] | None:
    """The latest occurrence of a series that is over by now (as ``search._occurrence_of`` looks back)."""
    first = ical.master_of(parsed).decoded("DTSTART")
    finish = (min(row.ends_at, now + timedelta(days=1)) if row.ends_at is not None
              else now + timedelta(days=1)) + timedelta(days=1)
    for reach in search._windows():
        begin = finish - reach
        found = [occ for occ in ical.occurrences(search._near(parsed, begin, first), begin, finish)
                 if not search._coming(search._span(occ.start, occ.end), now, today)]
        if found:
            last = max(found, key=lambda occ: ical._as_utc(occ.start))
            return last.recurrence_id, search._span(last.start, last.end)
        if begin < row.starts_at:
            break
        finish = begin
    return None


def _plain_span(row: Event) -> tuple[bool, Any, Any]:
    """A single event's span from its columns, as the search reads it."""
    if row.all_day:
        begin = row.starts_at.date()
        end = row.ends_at.date() if row.ends_at is not None else begin + timedelta(days=1)
        return True, begin, max(end, begin + timedelta(days=1))
    return False, row.starts_at, row.ends_at or row.starts_at


def _coming(span: tuple[bool, Any, Any], now: datetime, today: date) -> bool:
    return search._coming(span, now, today)


def _at(moment: _Moment) -> str:
    if moment.all_day:
        return moment.start.isoformat()
    return moment.start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open(moment: _Moment, zone: str) -> str:
    day = moment.start if moment.all_day else ical.local_wall(moment.start, zone).date()
    if moment.busy_only:
        # "Busy only": the day, not the event (the member sees it there without a title).
        return f"/day?day={day.isoformat()}"
    path = f"/day?day={day.isoformat()}&event={moment.event_id}"
    key = ical.recurrence_key(moment.occurrence)
    return path + (f"&occurrence={key}" if key else "")


def _once(moments: list[_Moment]) -> list[_Moment]:
    """The same event in two calendars of one person once (TIME-14 AC-3, as the search does)."""
    kept, known = [], set()
    for moment in moments:
        found = (moment.owner, moment.uid, moment.all_day, str(moment.start), str(moment.end))
        if found in known:
            continue
        known.add(found)
        kept.append(moment)
    return kept


def about(store: Any, caller: Any, purpose: str, person: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The hook ``router(about=…)`` of the module verbinden for ``events.about``."""
    db = store.db
    account = db.get(Account, int(caller.account_id))
    pattern = name_pattern(person.name or "")
    mails = {mail.strip().lower() for mail in person.mails if mail.strip()}
    if account is None or (pattern is None and not mails):
        return dict(NOTHING)
    key = apiv1.Caller(account=account, key_id=0, key_name=str(caller.partner), level="read",
                       calendar_ids=[int(area) for area in caller.areas] if caller.areas is not None else None)
    reach = key.reach(db)
    if not reach:
        return dict(NOTHING)
    now = now or apiv1.now()
    zone = apiv1.zone_name(account)
    today = ical.local_wall(now, zone).date()
    # A first sieve in the database, the rule itself below: an event with attendees, or one whose folded texts hold
    # the first word of the name (a row of before without folded texts too).
    sieve = []
    if mails:
        sieve.append(func.instr(Event.ical, "ATTENDEE") > 0)
    if pattern is not None:
        first_word = ical.fold_text(person.name.split()[0])
        sieve.append(or_(func.instr(Event.search_text, first_word) > 0, Event.search_text.is_(None)))
    rows = db.scalars(select(Event).where(Event.calendar_id.in_(reach), Event.deleted_at.is_(None), or_(*sieve))
                      .order_by(Event.id)).all()
    coming: list[_Moment] = []
    over: list[_Moment] = []
    for row in rows:
        item: calendars.Access = reach[row.calendar_id]
        busy_only = item.right == RIGHT_BUSY
        if busy_only and not row.busy:
            continue
        # The object only where its columns cannot tell: attendees (and the member's answer), a series.
        parsed = ical.parse(row.ical) if (row.recurring or "ATTENDEE" in row.ical) else None
        if not concerns(row.title or "", parsed, pattern, mails):
            continue
        if parsed is not None and "ATTENDEE" in row.ical:
            state = invitations.state_of(ical.master_of(parsed), invitations.context_of(db, item, row)[0])
            if state and state["state"] == "declined":
                continue

        def moment(occurrence: Any, span: tuple[bool, Any, Any], row: Event = row, busy_only: bool = busy_only,
                   item: calendars.Access = item) -> _Moment:
            return _Moment(row.id, occurrence, span[0], span[1], span[2], "" if busy_only else (row.title or ""),
                           item.calendar.owner_id, row.uid, busy_only)

        if row.recurring and parsed is not None:
            series = search._series_occurrence(row, row.ical, now, today)
            occurrence, span, is_coming = series
            if is_coming:
                coming.append(moment(occurrence, span))
                last = _last_over(ical.parse(row.ical), row, now, today)
                if last is not None:
                    over.append(moment(*last))
            else:
                over.append(moment(occurrence, span))
        else:
            span = _plain_span(row)
            (coming if _coming(span, now, today) else over).append(moment(None, span))
    coming = sorted(_once(coming), key=_Moment.key)
    over = sorted(_once(over), key=_Moment.key, reverse=True)
    first = coming[0] if coming else None
    latest = over[0] if over else None
    shown = first or latest
    return {
        "count": len(coming),
        "next": {"title": first.title, "at": _at(first)} if first else None,
        "last": {"title": latest.title, "at": _at(latest)} if latest else None,
        "open": _open(shown, zone) if shown else None,
    }
