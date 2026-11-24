"""The year as a heat map (TIME-12 AC-16 to AC-22, the same as TIMEIOS-8 in the app): how many events each day of a
year holds, for the calendars the views show now.

What counts (AC-17, AC-18): every occurrence the views draw, on every day it touches in the zone shown; a series on
each of its days, an event over midnight or several days on each of them. Duplicates count once (TIME-14), "busy"
blocks and open invitations count, declined ones do not. Background calendars (holidays, birthdays, school holidays,
waste collection) never count; tasks and deadlines are not events here at all. The days of a calendar with "Tage
hervorheben" come apart as ``holidays`` (AC-20), whatever else that day holds.

A year of 20 calendars holds some 10,000 events (EC-20): reading each as an object took a second. Single events come
from a few columns instead (``FAST``); series, changed occurrences, holidays in two languages and invitations go
through ``events.occurrence_list`` as in every view, so both ways count the same (the tests compare them).

Only counts leave the server: a calendar shared as "busy only" tells no more than how full a day is (EC-24).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ORIGIN_IMIP, Account, Event
from . import calendars, events, ical, invitations

#: The years one may ask for; far enough for any calendar, small enough that a wrong number is answered at once.
FIRST_YEAR, LAST_YEAR = 1900, 2200

#: Single events from their columns; the tests switch it off to compare with the way every view goes.
FAST = True


def _days_of(entry: dict[str, Any], zone: ZoneInfo) -> tuple[date, date]:
    """The first and the last day an occurrence touches on the clock of ``zone`` (EC-4, EC-8 of TIMEIOS-8)."""
    if entry["all_day"]:
        return date.fromisoformat(entry["start"]), date.fromisoformat(entry["end"])
    begin = datetime.fromisoformat(entry["start"]).astimezone(zone)
    finish = datetime.fromisoformat(entry["end"]).astimezone(zone)
    # An end at midnight belongs to the day before; an event without length to its start.
    last = (finish - timedelta(microseconds=1)) if finish > begin else begin
    return begin.date(), last.date()


def _stamp(value: datetime) -> str:
    return ical._as_utc(value).isoformat().replace("+00:00", "Z")


def _occurrences(db: Session, account: Account, start: datetime, end: datetime, include_hidden: bool,
                 only: set[int] | None) -> list[dict[str, Any]]:
    """What ``events.occurrence_list`` gives for the window, duplicates merged, with only what counting needs."""
    if not FAST:
        return events.occurrence_list(db, account, start, end, include_hidden=include_hidden, only=only, merge=True)
    listed = [(item, setting) for item, setting in calendars.ordered(db, account.id)
              if (include_hidden or not setting.hidden) and (only is None or item.calendar.id in only)]
    shown = {item.calendar.id: item for item, _setting in listed}
    if not shown:
        return []
    place = {calendar_id: index for index, calendar_id in enumerate(shown)}
    own = {calendar_id: bool(invitations.context(db, item)[0]) for calendar_id, item in shown.items()}
    margin = timedelta(days=1)
    upper = func.upper(Event.ical)
    rows = db.execute(select(
        Event.id, Event.calendar_id, Event.href, Event.uid, Event.title, Event.starts_at, Event.ends_at,
        Event.all_day, Event.busy, Event.recurring, Event.origin,
        # What makes an event more than its columns (as ``events._plain_event`` asks), without reading its text here.
        (func.instr(Event.ical, "X-NEXTIME-SUMMARY-") + func.instr(Event.ical, "RECURRENCE-ID")) > 0,
        func.instr(upper, "ATTENDEE") > 0,
    ).where(
        Event.calendar_id.in_(shown), Event.deleted_at.is_(None), Event.starts_at < end + margin,
        (Event.ends_at.is_(None)) | (Event.ends_at > start - margin),
    )).all()
    found: list[tuple[dict[str, Any], int, str]] = []
    others: dict[int, set[str]] = {}
    uids: dict[int, str] = {}
    for (event_id, calendar_id, href, uid, title, starts_at, ends_at, all_day, busy, recurring, origin, marked,
         attendees) in rows:
        item = shown[calendar_id]
        uids[event_id] = uid
        mail = origin == ORIGIN_IMIP and item.right == calendars.OWNER
        if recurring or marked or (attendees and (own[calendar_id] or mail)):
            others.setdefault(calendar_id, set()).add(href)
            continue
        hide = events.busy_only(item)
        if hide and not busy:
            continue
        if all_day:
            begin: date | datetime = starts_at.date()
            finish: date | datetime = (ends_at or starts_at).date()
        else:
            begin, finish = starts_at, ends_at or starts_at
        slack = margin if all_day else timedelta(0)
        if ical._as_utc(begin) >= end + slack or (ical._as_utc(finish) <= start - slack and not (
                finish == begin and ical._as_utc(begin) >= start)):
            continue
        if all_day:
            last = finish - timedelta(days=1) if finish > begin else begin
            first_text, last_text = begin.isoformat(), last.isoformat()
        else:
            first_text, last_text = _stamp(begin), _stamp(finish)
        entry = {"id": event_id, "occurrence": None, "calendar_id": calendar_id, "title": "" if hide else title or "",
                 "busy_only": hide, "all_day": bool(all_day), "start": first_text, "end": last_text,
                 "writable": False, "etag": ""}
        found.append((entry, item.calendar.owner_id, uid))
    if others:
        for entry in events.occurrence_list(db, account, start, end, include_hidden=include_hidden, only=only,
                                            events_of=others, merge=False):
            found.append((entry, shown[entry["calendar_id"]].calendar.owner_id, uids.get(entry["id"], "")))
    found.sort(key=lambda trio: (trio[0]["start"], place[trio[0]["calendar_id"]], trio[0]["title"]))
    return events._merged(found)


def year_map(db: Session, account: Account, year: int, zone_name: str, *, include_hidden: bool = False,
             only: set[int] | None = None) -> dict[str, Any]:
    """``{"year", "zone", "days": {"2026-03-03": 4, ...}, "holidays": ["2026-01-01", ...]}``; days without events are
    left out."""
    zone = ZoneInfo(zone_name)
    start = datetime(year, 1, 1, tzinfo=zone).astimezone(UTC)
    end = datetime(year + 1, 1, 1, tzinfo=zone).astimezone(UTC)
    first, last = date(year, 1, 1), date(year, 12, 31)
    background: set[int] = set()
    marked: set[int] = set()
    for item, setting in calendars.ordered(db, account.id):
        if item.calendar.background:
            background.add(item.calendar.id)
        if calendars.highlighted(item.calendar, setting):
            marked.add(item.calendar.id)
    counts: dict[date, int] = {}
    holidays: set[date] = set()
    for entry in _occurrences(db, account, start, end, include_hidden, only):
        sources = {source["calendar_id"] for source in entry.get("sources") or ()} or {entry["calendar_id"]}
        counted = bool(sources - background)
        holiday = bool(sources & marked)
        if not counted and not holiday:
            continue
        day, final = _days_of(entry, zone)
        day, final = max(day, first), min(final, last)
        while day <= final:
            if counted:
                counts[day] = counts.get(day, 0) + 1
            if holiday:
                holidays.add(day)
            day += timedelta(days=1)
    return {
        "year": year,
        "zone": zone_name,
        "days": {day.isoformat(): count for day, count in sorted(counts.items())},
        "holidays": sorted(day.isoformat() for day in holidays),
    }
