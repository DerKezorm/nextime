"""What a guest with a read link sees (TIME-19 AC-5, AC-13, AC-17): the page with the coming events and the
subscription with all of them. Of each event only its title, its times and its place: no notes, no attachments, no
reminders, no people (AC-17); of the owner only the name, of the server nothing else (EC-12).

**The page** (``page``) lists the coming events, a page of ``PAGE`` at a time ("Show more"): single events straight
from the derived columns, series rolled out for the next ``HORIZON_DAYS``. Times go out as instants, whole days as
dates, so the guest's browser shows them in the guest's zone (EC-19).

**The subscription** (``feed``) is the calendar as one iCalendar file: every event, past and future, as stored, with
only the properties a guest may see kept line by line (folded lines stay folded, escaped text stays escaped, so a
title with ``END:VEVENT`` in it stays a title, EC-10). Built once per state of the calendar and kept while it lasts:
calendar programs ask often, and a calendar may hold 20,000 events (EC-24).
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

import icalendar
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, Calendar, Event, EventChange, GuestLink, utcnow
from . import display, ical

#: Events on one page of the guest page; "Show more" brings the next.
PAGE = 50
#: How far ahead series are rolled out on the page.
HORIZON_DAYS = 366
#: What a guest sees of an event (AC-17): times, rule and exceptions, title, place, free or busy.
_KEEP = frozenset({"UID", "DTSTAMP", "DTSTART", "DTEND", "DURATION", "RRULE", "RDATE", "EXDATE", "RECURRENCE-ID",
                   "SUMMARY", "LOCATION", "TRANSP", "STATUS", "SEQUENCE", "LAST-MODIFIED", "CREATED"})
#: Subscriptions kept built, by calendar.
_CACHE_SIZE = 32

_lock = threading.Lock()
_cache: OrderedDict[int, tuple[tuple, bytes, str]] = OrderedDict()


def _instant(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _entry(start: Any, end: Any, all_day: bool, title: str, location: str) -> dict[str, Any]:
    if all_day:
        first = start.date() if isinstance(start, datetime) else start
        after = end.date() if isinstance(end, datetime) else end
        last = after - timedelta(days=1) if after > first else first
        return {"all_day": True, "start": first.isoformat(), "end": last.isoformat(), "title": title,
                "location": location, "key": f"{first.isoformat()}T00:00:00Z"}
    return {"all_day": False, "start": _instant(ical._as_utc(start)), "end": _instant(ical._as_utc(end)),
            "title": title, "location": location, "key": _instant(ical._as_utc(start))}


def upcoming(db: Session, calendar: Calendar, now: datetime | None = None,
             zone: tzinfo | None = None) -> list[dict[str, Any]]:
    """Every coming occurrence (not yet over), in order. A whole day counts as over once its last day has passed in
    ``zone``, the calendar owner's (review of TIME-19, finding 6): the day a family calls "today" is the owner's, and
    the page shows the dates as they are, without a zone."""
    now = now or utcnow()
    today = now.astimezone(zone or UTC).date()
    found: list[dict[str, Any]] = []
    singles = db.execute(select(Event.starts_at, Event.ends_at, Event.all_day, Event.title, Event.location).where(
        Event.calendar_id == calendar.id, Event.deleted_at.is_(None), Event.recurring.is_(False),
        Event.ends_at > now - timedelta(days=2))).all()
    for starts_at, ends_at, all_day, title, location in singles:
        end = ends_at or starts_at
        if not all_day and end <= now:
            continue
        if all_day and end.date() <= today:
            continue
        found.append(_entry(starts_at, end, all_day, title or "", location or ""))
    horizon = now + timedelta(days=HORIZON_DAYS)
    series = db.scalars(select(Event.ical).where(
        Event.calendar_id == calendar.id, Event.deleted_at.is_(None), Event.recurring.is_(True),
        Event.starts_at < horizon, (Event.ends_at.is_(None)) | (Event.ends_at > now - timedelta(days=2)))).all()
    for text in series:
        try:
            parsed = ical.parse(text)
            spans = ical.occurrences(parsed, now - timedelta(days=2), horizon)
        except Exception:  # noqa: BLE001, S112 - one unreadable series must not take the page
            continue
        for occurrence in spans:
            all_day = not isinstance(occurrence.start, datetime)
            if not all_day and ical._as_utc(occurrence.end) <= now:
                continue
            if all_day and occurrence.end <= today:
                continue
            component = occurrence.component
            found.append(_entry(occurrence.start, occurrence.end, all_day, str(component.get("SUMMARY", "")),
                                str(component.get("LOCATION", ""))))
    found.sort(key=lambda entry: (entry["key"], entry["title"]))
    for entry in found:
        entry.pop("key")
    return found


def page(db: Session, calendar: Calendar, owner: Account, offset: int = 0) -> dict[str, Any]:
    """The guest page (AC-13): name and color, the owner's name only, a page of coming events."""
    events = upcoming(db, calendar, zone=display.zone_of(owner))
    offset = max(0, offset)
    shown = events[offset: offset + PAGE]
    return {"calendar": {"name": calendar.name, "color": calendar.color}, "owner": owner.shown_name,
            "events": shown, "more": offset + PAGE < len(events), "next": offset + len(shown)}


# --- The subscription -------------------------------------------------------------------------------------------------


def _logical_lines(text: str) -> list[list[str]]:
    """The content lines of an iCalendar text, each with its folded continuation lines."""
    lines: list[list[str]] = []
    for physical in text.replace("\r\n", "\n").split("\n"):
        if not physical:
            continue
        if physical[0] in " \t" and lines:
            lines[-1].append(physical)
        else:
            lines.append([physical])
    return lines


def _name(line: str) -> str:
    cut = len(line)
    for mark in (";", ":"):
        found = line.find(mark)
        if found != -1:
            cut = min(cut, found)
    return line[:cut].strip().upper()


def _value(line: str) -> str:
    return line.split(":", 1)[1].strip() if ":" in line else ""


def guest_parts(text: str, zones: dict[str, list[str]]) -> list[str]:
    """The events of one stored object as a guest may see them: its VEVENTs with the kept properties only, nested
    components (alarms) left out; its time zones go to ``zones`` once each."""
    out: list[str] = []
    depth: list[str] = []
    block: list[str] | None = None
    zone_id = ""
    for group in _logical_lines(text):
        name = _name(group[0])
        if name == "BEGIN":
            depth.append(_value(group[0]).upper())
            if depth[-1] == "VTIMEZONE" and len(depth) == 2:
                block, zone_id = [*group], ""
                continue
        inside = depth[1] if len(depth) > 1 else ""
        if inside == "VTIMEZONE" and block is not None:
            block.extend(group)
            if name == "TZID" and len(depth) == 2:
                zone_id = _value(group[0])
            if name == "END" and len(depth) == 2:
                if zone_id and zone_id not in zones:
                    zones[zone_id] = block
                block = None
        elif inside == "VEVENT":
            if len(depth) == 2 and (name in ("BEGIN", "END") or name in _KEEP):
                out.extend(group)
        if name == "END" and depth:
            depth.pop()
    return out


def _stamp(db: Session, calendar: Calendar) -> tuple:
    last = db.scalar(select(func.max(EventChange.id)).where(EventChange.calendar_id == calendar.id)) or 0
    count = db.scalar(select(func.count()).select_from(Event).where(Event.calendar_id == calendar.id,
                                                                     Event.deleted_at.is_(None))) or 0
    newest = db.scalar(select(func.max(Event.updated_at)).where(Event.calendar_id == calendar.id))
    return (last, count, newest, calendar.name, calendar.updated_at)


def _header(calendar: Calendar) -> list[str]:
    head = icalendar.Calendar()
    head.add("PRODID", ical.PRODID)
    head.add("VERSION", "2.0")
    head.add("CALSCALE", "GREGORIAN")
    head.add("METHOD", "PUBLISH")
    head.add("X-WR-CALNAME", calendar.name)
    head.add("X-PUBLISHED-TTL", "PT1H")
    text = head.to_ical().decode("utf-8").replace("\r\n", "\n")
    return [line for line in text.split("\n") if line and line != "END:VCALENDAR"]


def feed(db: Session, link: GuestLink, calendar: Calendar) -> tuple[bytes, str]:
    """The subscription (AC-5, AC-17): all events of the calendar; with its ETag."""
    stamp = _stamp(db, calendar)
    with _lock:
        known = _cache.get(calendar.id)
        if known is not None and known[0] == stamp:
            _cache.move_to_end(calendar.id)
            return known[1], known[2]
    zones: dict[str, list[str]] = {}
    events: list[str] = []
    for text in db.scalars(select(Event.ical).where(Event.calendar_id == calendar.id, Event.deleted_at.is_(None))
                           .order_by(Event.starts_at, Event.id)).yield_per(500):
        events.extend(guest_parts(text, zones))
    lines = [*_header(calendar), *(line for block in zones.values() for line in block), *events, "END:VCALENDAR"]
    body = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    with _lock:
        _cache[calendar.id] = (stamp, body, etag)
        _cache.move_to_end(calendar.id)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return body, etag


def forget() -> None:
    """For the tests."""
    with _lock:
        _cache.clear()
