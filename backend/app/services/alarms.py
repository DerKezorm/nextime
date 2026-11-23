"""Reminders as iCalendar alarms (VALARM) for the calendar account (TIME-7 AC-7, AC-9).

A reminder belongs to one person, an iCalendar object to everybody who sees the event. So nextime keeps the alarms out
of the stored object of its own calendars and hands every device the alarms of the person it belongs to: the person's
own choice for the event, or the default of the calendar (marked ``X-NEXTIME-DEFAULT``, so that a device sending it back
unchanged does not turn the default into a choice). Apple Calendar then reminds on its own on that phone, and nextime
can stay quiet there.

**Lead times** are minutes before the start. Whole days count on the wall clock ("1 day before" is the same time the
day before, also over the change of the clocks); the rest counts exactly. For an all-day event the minutes count back
from midnight of its day in the member's zone, so "the day before at 18:00" is 360 and "on the day at 09:00" is -540,
as Apple writes them (``TRIGGER:-PT6H``, ``TRIGGER:PT9H``).

Everything here works on the text where it can (stripping and inserting whole ``VALARM`` blocks), so that the rest of
an object stays byte for byte as it was.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

import icalendar

from . import ical

#: The longest lead time: four weeks.
MAX_MINUTES = 4 * 7 * 1440
#: An all-day event may remind on its own day, up to a minute before midnight.
EARLIEST_ON_THE_DAY = -1439
#: The mark of an alarm made from a calendar's default.
DEFAULT_MARK = "X-NEXTIME-DEFAULT"

#: A whole VALARM block, from a line that begins it to a line that ends it: never from the middle of a folded text line
#: (review of TIME-7, finding 8).
_BLOCK = re.compile(r"^BEGIN:VALARM\r?\n.*?^END:VALARM\r?\n?", re.DOTALL | re.IGNORECASE | re.MULTILINE)
_END_EVENT = re.compile(r"^END:VEVENT\r?$", re.MULTILINE | re.IGNORECASE)


def present(text: str) -> bool:
    return "BEGIN:VALARM" in text.upper() and _BLOCK.search(text) is not None


def strip(text: str) -> str:
    """The object without any alarm."""
    return _BLOCK.sub("", text) if present(text) else text


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def inject(text: str, blocks: str) -> str:
    """``blocks`` (whole VALARM or other lines, CRLF) into every event of the object, before its end."""
    if not blocks:
        return text
    newline = _newline(text)
    body = blocks.replace("\r\n", "\n").replace("\n", newline)
    if not body.endswith(newline):
        body += newline
    return _END_EVENT.sub(lambda found: body + found.group(0), text)


def trigger(minutes: int, all_day: bool) -> str:
    """The TRIGGER value for a lead time: ``-PT15M``, ``-P1D``, ``-P1DT2H``; for a whole day hours from midnight."""
    sign = "-" if minutes > 0 else ""
    rest = abs(minutes)
    if all_day:
        days = 0
    else:
        days, rest = divmod(rest, 1440)
    hours, mins = divmod(rest, 60)
    out = f"{sign}P"
    if days:
        out += f"{days}D"
    if hours or mins or not days:
        out += "T"
        if hours:
            out += f"{hours}H"
        if mins or not hours:
            out += f"{mins}M"
    return out


def generated(minutes: list[int], all_day: bool, *, default: bool) -> str:
    """VALARM blocks for the lead times, as nextime makes them."""
    lines = []
    for value in minutes:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", f"TRIGGER:{trigger(value, all_day)}"]
        if default:
            lines.append(f"{DEFAULT_MARK}:TRUE")
        lines.append("END:VALARM")
    return "\r\n".join(lines) + "\r\n" if lines else ""


def signature(blocks: str) -> str:
    return hashlib.sha256(blocks.encode("utf-8")).hexdigest()[:16] if blocks else ""


def valid(minutes: int, all_day: bool) -> bool:
    low = EARLIEST_ON_THE_DAY if all_day else 0
    return low <= minutes <= MAX_MINUTES


# --- Reading what a device or a source wrote ----------------------------------------------------------------------


def _lead(alarm: icalendar.cal.Component, start: date | datetime, length: timedelta, series: bool) -> int | None:
    """The lead time of one alarm in minutes, or ``None`` where it is none nextime can keep (no action, after the
    start of a timed event, an absolute time in a series, beyond four weeks)."""
    if str(alarm.get("ACTION", "")).upper() not in ("DISPLAY", "AUDIO", "EMAIL"):
        return None
    if alarm.get("TRIGGER") is None:
        return None
    try:
        value = alarm.decoded("TRIGGER")
    except Exception:  # noqa: BLE001 - a broken trigger is no reminder
        return None
    all_day = not isinstance(start, datetime)
    if isinstance(value, timedelta):
        related = str(alarm["TRIGGER"].params.get("RELATED", "START")).upper()
        offset = value + length if related == "END" else value
        minutes = -round(offset.total_seconds() / 60)
    elif isinstance(value, datetime) and not series:
        minutes = round((ical._as_utc(start) - ical._as_utc(value)).total_seconds() / 60)
    else:
        return None
    return minutes if valid(minutes, all_day) else None


def _master(parsed: icalendar.Calendar) -> icalendar.Event:
    return ical.master_of(parsed)


def _component_leads(component: icalendar.cal.Component, series: bool) -> list[int]:
    start = component.decoded("DTSTART")
    found = {_lead(sub, start, ical.duration_of(component), series) for sub in component.subcomponents
             if sub.name == "VALARM"}
    return sorted(value for value in found if value is not None)


def leads(parsed: icalendar.Calendar) -> list[int]:
    """The lead times of the event's own alarms (its master), each once, in order."""
    master = _master(parsed)
    series = master.get("RRULE") is not None or master.get("RDATE") is not None
    return _component_leads(master, series)


@lru_cache(maxsize=4096)
def _source_leads(etag: str, text: str) -> tuple[int, ...]:
    try:
        return tuple(leads(ical.parse(text)))
    except Exception:  # noqa: BLE001 - an object the source broke reminds of nothing
        return ()


def source_leads(etag: str, text: str) -> list[int]:
    """The alarms an event brought from iCloud, Google or a CalDAV server (TIME-7 AC-5), read once per version."""
    if not present(text):
        return []
    return list(_source_leads(etag, text))


class FromDevice:
    """What a device's object says about its alarms."""

    def __init__(self, minutes: list[int], raw: str, any_alarm: bool, only_defaults: bool,
                 occurrences: dict[str, dict[str, Any]] | None = None) -> None:
        self.minutes = minutes
        #: Single occurrences of a series with alarms of their own, other than the series' (review of TIME-7, finding
        #: 5): by the occurrence's name in the API, its RECURRENCE-ID line and its lead times.
        self.occurrences = occurrences or {}
        #: The VALARM blocks as the device sent them, per event of the object (JSON: its RECURRENCE-ID line, "" for the
        #: master), handed back to it as they were (Apple keeps fields of its own there).
        self.raw = raw
        self.any = any_alarm
        #: Every alarm carries nextime's default mark: the device sent the default back unchanged.
        self.only_defaults = only_defaults


_SECTION = re.compile(r"^BEGIN:VEVENT\r?\n.*?^END:VEVENT", re.DOTALL | re.IGNORECASE | re.MULTILINE)
_RECURRENCE = re.compile(r"^RECURRENCE-ID[;:][^\r\n]*", re.MULTILINE | re.IGNORECASE)


def _sections(text: str) -> list[tuple[int, int, str]]:
    """Every event of an object as (begin, end, its RECURRENCE-ID line or "" for the master)."""
    found = []
    for match in _SECTION.finditer(text):
        outside = _BLOCK.sub("", match.group(0))
        line = _RECURRENCE.search(outside)
        found.append((match.start(), match.end(), line.group(0).strip() if line else ""))
    return found


def _blocks(text: str) -> str:
    return "".join(_BLOCK.findall(text))


def take(parsed: icalendar.Calendar, text: str) -> FromDevice:
    """The alarms of an object a device stores: its lead times (``parsed``), the master's blocks as sent (``text``).
    The object itself is stored with ``strip`` (the rest of it byte for byte as the device sent it)."""
    if not present(text):
        return FromDevice([], "", False, False)
    sections = _sections(text)
    sent = {recurrence: _blocks(text[begin:end]) for begin, end, recurrence in sections}
    every = _BLOCK.findall(text)
    only_defaults = all(f"{DEFAULT_MARK}:TRUE" in block.upper() for block in every)
    series_leads = leads(parsed)
    occurrences: dict[str, dict[str, Any]] = {}
    master = _master(parsed)
    series = master.get("RRULE") is not None or master.get("RDATE") is not None
    # The events of the text and of the parsed object stand in the same order.
    for (_begin, _end, recurrence), component in zip(sections, ical.events_of(parsed), strict=False):
        if not recurrence or component.get("RECURRENCE-ID") is None:
            continue
        own = _component_leads(component, series)
        if own != series_leads:
            key = ical.recurrence_key(component.decoded("RECURRENCE-ID"))
            occurrences[key] = {"line": recurrence, "minutes": own}
    # An occurrence without alarms under a series with them is kept too, as an empty entry.
    lines = {entry["line"] for entry in occurrences.values()}
    raw = json.dumps({key: value for key, value in sent.items() if value or key in lines}, ensure_ascii=False)
    return FromDevice(series_leads, raw, True, only_defaults, occurrences)


def covers_series(raw: str) -> bool:
    """Whether the blocks a device sent hold the series' own (its master) as well."""
    return "" in _mapping(raw)


def keep_occurrences(raw: str, occurrences: dict[str, Any]) -> str:
    """Of the blocks a device sent, those of the single occurrences with alarms of their own: a change of the series in
    the interface leaves them (review of TIME-7, finding 5)."""
    lines = {str(entry.get("line", "")) for entry in (occurrences or {}).values() if isinstance(entry, dict)}
    kept = {key: value for key, value in _mapping(raw).items() if key and key in lines}
    return json.dumps(kept, ensure_ascii=False) if kept else ""


def _mapping(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        found = json.loads(raw)
    except ValueError:
        return {"": raw}
    return {str(key): str(value) for key, value in found.items()} if isinstance(found, dict) else {}


def place(text: str, raw: str, made: str, files: str) -> str:
    """What one person's device gets of an object without alarms: the alarms that device sent, each in its event of
    the object as it was (``raw``), and into every other event the alarms nextime made (``made``); nextime's
    attachments (``files``) into every event, before the alarms."""
    mapping = _mapping(raw)
    if not mapping and not made and not files:
        return text
    out, at = [], 0
    for begin, end, recurrence in _sections(text):
        section = text[begin:end]
        blocks = files + mapping.get(recurrence, made)
        out.append(text[at:begin])
        out.append(inject(section, blocks) if blocks else section)
        at = end
    out.append(text[at:])
    return "".join(out)


def restore(text: str, previous: str) -> str:
    """The alarms of the stored object (a source's, TIME-7 AC-5) into an object a device sent, which carries none any
    more: what a member's phone set never goes to the source of a shared iCloud, Google or CalDAV calendar."""
    if not previous or not present(previous):
        return text
    kept = {recurrence: _blocks(previous[begin:end]) for begin, end, recurrence in _sections(previous)}
    out, at = [], 0
    for begin, end, recurrence in _sections(text):
        section = text[begin:end]
        blocks = kept.get(recurrence, "")
        out.append(text[at:begin])
        out.append(inject(section, blocks) if blocks else section)
        at = end
    out.append(text[at:])
    return "".join(out)
