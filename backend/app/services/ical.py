"""Events as iCalendar objects (RFC 5545): reading, checking, the columns derived from them, and rolling out series.

An event is stored as one VCALENDAR with its VEVENT and the exceptions of a series (further VEVENTs with a
RECURRENCE-ID), the way CalDAV hands it over. What a device brought along (``X-…`` fields, alarms, attendees) stays
in the text untouched: the interface changes only the properties it knows, in the object as stored.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar
import recurring_ical_events
from dateutil.rrule import rrulestr

PRODID = "-//nextime//nextime//EN"
#: The largest object a device may store (``max-resource-size``, TIME-20 EC-11).
MAX_OBJECT_BYTES = 2 * 1024 * 1024
#: Components nextime keeps; any other kind of entry (tasks, journal) is not an event (supported-calendar-component).
ALLOWED_COMPONENTS = {"VEVENT", "VTIMEZONE"}


class IcalError(Exception):
    """An object nextime does not take. ``code`` names the CalDAV precondition it breaks."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


def etag_of(text: str) -> str:
    return '"' + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32] + '"'


def parse(text: str) -> icalendar.Calendar:
    try:
        calendar = icalendar.Calendar.from_ical(text)
    except Exception as exc:  # icalendar raises ValueError, KeyError and others on broken input
        raise IcalError("valid-calendar-data", "The calendar data is not valid iCalendar.") from exc
    if not isinstance(calendar, icalendar.Calendar) or calendar.name != "VCALENDAR":
        raise IcalError("valid-calendar-data", "The data is not a VCALENDAR.")
    return calendar


def events_of(calendar: icalendar.Calendar) -> list[icalendar.Event]:
    return [component for component in calendar.subcomponents if component.name == "VEVENT"]


def master_of(calendar: icalendar.Calendar) -> icalendar.Event:
    """The event itself; for a series the one without RECURRENCE-ID. An object of exceptions only (an invitation to
    one occurrence) has the first as its master."""
    events = events_of(calendar)
    for component in events:
        if component.get("RECURRENCE-ID") is None:
            return component
    return events[0]


def overrides_of(calendar: icalendar.Calendar) -> list[icalendar.Event]:
    return [component for component in events_of(calendar) if component.get("RECURRENCE-ID") is not None]


def check(calendar: icalendar.Calendar) -> str:
    """The object's UID when nextime takes it; otherwise ``IcalError``. One event with its exceptions, each with a
    start, an end not before it, one UID for all."""
    for component in calendar.subcomponents:
        if component.name not in ALLOWED_COMPONENTS:
            raise IcalError("supported-calendar-component", "Only events (VEVENT) are kept here.")
    events = events_of(calendar)
    if not events:
        raise IcalError("supported-calendar-component", "The object holds no event.")
    uids = {str(component.get("UID", "")).strip() for component in events}
    if len(uids) != 1 or "" in uids:
        raise IcalError("valid-calendar-object-resource", "An object holds one event with one UID.")
    uid = uids.pop()
    if len(uid) > 255:
        raise IcalError("valid-calendar-object-resource", "The UID is too long.")
    masters = [component for component in events if component.get("RECURRENCE-ID") is None]
    if len(masters) > 1:
        raise IcalError("valid-calendar-object-resource", "An object holds one event with one UID.")
    for component in events:
        start = _decoded(component, "DTSTART")
        if start is None:
            raise IcalError("valid-calendar-object-resource", "An event needs a start.")
        end = _decoded(component, "DTEND")
        if end is not None:
            if isinstance(start, datetime) != isinstance(end, datetime):
                raise IcalError("valid-calendar-object-resource", "Start and end are of different kinds.")
            if _as_utc(end) < _as_utc(start):
                raise IcalError("valid-calendar-object-resource", "The end lies before the start.")
        if component.get("RRULE") is not None:
            try:
                _rule_of(component)
            except Exception as exc:
                raise IcalError("valid-calendar-object-resource", "The rule of the series is not valid.") from exc
    return uid


def _decoded(component: icalendar.cal.Component, name: str) -> Any:
    if component.get(name) is None:
        return None
    try:
        return component.decoded(name)
    except Exception as exc:
        raise IcalError("valid-calendar-object-resource", f"{name} is not valid.") from exc


def _as_utc(value: date | datetime) -> datetime:
    """A date counts as midnight UTC; a time without zone (floating) as UTC."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def zone_name(value: Any) -> str:
    """The IANA name of a datetime's zone, when it has one nextime knows; "" otherwise."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return ""
    key = getattr(value.tzinfo, "key", None) or getattr(value.tzinfo, "zone", None)
    if key:
        return str(key)
    return "UTC" if value.utcoffset() == timedelta(0) else ""


def duration_of(component: icalendar.cal.Component) -> timedelta:
    start = _decoded(component, "DTSTART")
    end = _decoded(component, "DTEND")
    if end is not None:
        return _as_utc(end) - _as_utc(start)
    length = _decoded(component, "DURATION")
    if isinstance(length, timedelta):
        return length
    return timedelta(days=1) if not isinstance(start, datetime) else timedelta(0)


def _rule_of(component: icalendar.cal.Component) -> Any:
    start = component.decoded("DTSTART")
    if not isinstance(start, datetime):
        start = datetime(start.year, start.month, start.day)  # noqa: DTZ001 - a whole day has no zone
    rule = component["RRULE"]
    if isinstance(rule, list):
        rule = rule[0]
    return rrulestr(rule.to_ical().decode(), dtstart=start, forceset=False)


@dataclass
class Derived:
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    time_zone: str
    recurring: bool
    busy: bool
    location: str


def derive(calendar: icalendar.Calendar) -> Derived:
    """The columns for quick questions: the first start, the last end (none for a series without end)."""
    master = master_of(calendar)
    start = master.decoded("DTSTART")
    length = duration_of(master)
    recurring = master.get("RRULE") is not None or master.get("RDATE") is not None
    starts_at = _as_utc(start)
    ends_at: datetime | None = starts_at + length
    if recurring:
        ends_at = _series_end(master, length)
    for override in overrides_of(calendar):
        override_end = _as_utc(override.decoded("DTSTART")) + duration_of(override)
        starts_at = min(starts_at, _as_utc(override.decoded("DTSTART")))
        if ends_at is not None:
            ends_at = max(ends_at, override_end)
    transparency = str(master.get("TRANSP", "OPAQUE")).upper()
    return Derived(
        title=str(master.get("SUMMARY", "")),
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=not isinstance(start, datetime),
        time_zone=zone_name(start),
        recurring=recurring,
        busy=transparency != "TRANSPARENT",
        location=str(master.get("LOCATION", "")),
    )


def _series_end(master: icalendar.Event, length: timedelta) -> datetime | None:
    """The end of the last occurrence of a bounded series; ``None`` when it runs without end (or has RDATEs, which
    may lie anywhere)."""
    if master.get("RRULE") is None or master.get("RDATE") is not None:
        return None
    rule = master["RRULE"]
    if isinstance(rule, list):
        return None
    if not rule.get("UNTIL") and not rule.get("COUNT"):
        return None
    try:
        last = None
        for last in _rule_of(master):  # noqa: B007 - bounded by UNTIL or COUNT
            pass
    except Exception:  # noqa: BLE001 - a rule dateutil cannot roll out counts as without end
        return None
    if last is None:
        return _as_utc(master.decoded("DTSTART")) + length
    start = master.decoded("DTSTART")
    if isinstance(start, datetime) and start.tzinfo is not None and last.tzinfo is None:
        last = last.replace(tzinfo=start.tzinfo)
    return _as_utc(last) + length


# --- Rolling out ------------------------------------------------------------------------------------------------------


@dataclass
class Occurrence:
    #: The original start of this occurrence (its RECURRENCE-ID) for a series; ``None`` for a single event.
    recurrence_id: datetime | date | None
    start: datetime | date
    end: datetime | date
    component: icalendar.Event


def occurrences(calendar: icalendar.Calendar, start: datetime, end: datetime) -> list[Occurrence]:
    """Every occurrence that touches [start, end), exceptions and the change of the clocks included."""
    found = []
    for component in recurring_ical_events.of(calendar, keep_recurrence_attributes=True).between(start, end):
        begin = component.decoded("DTSTART")
        finish = component.decoded("DTEND") if component.get("DTEND") is not None else None
        if finish is None:
            finish = begin + duration_of(component) if isinstance(begin, datetime) else begin + timedelta(days=1)
        recurrence = component.decoded("RECURRENCE-ID") if component.get("RECURRENCE-ID") is not None else None
        found.append(Occurrence(recurrence, begin, finish, component))
    master = master_of(calendar)
    if master.get("RRULE") is None and master.get("RDATE") is None:
        for occurrence in found:
            if occurrence.component.get("RECURRENCE-ID") is None:
                occurrence.recurrence_id = None
    return found


def recurrence_key(value: datetime | date | None) -> str:
    """How an occurrence is named in the API: its original start, in UTC for a time, as the date for a whole day."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return _as_utc(value).strftime("%Y%m%dT%H%M%SZ")
    return value.strftime("%Y%m%d")


def same_recurrence(a: datetime | date, b: datetime | date) -> bool:
    return recurrence_key(a) == recurrence_key(b)


# --- Writing ----------------------------------------------------------------------------------------------------------


def zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown time zone") from exc


def new_calendar() -> icalendar.Calendar:
    calendar = icalendar.Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    return calendar


def stamp(component: icalendar.cal.Component) -> None:
    """DTSTAMP and LAST-MODIFIED now, SEQUENCE one up: a device sees that the event changed."""
    now = datetime.now(UTC).replace(microsecond=0)
    for name in ("DTSTAMP", "LAST-MODIFIED"):
        component.pop(name, None)
        component.add(name, now)
    sequence = int(component.get("SEQUENCE", 0) or 0)
    component.pop("SEQUENCE", None)
    if component.get("CREATED") is None:
        component.add("CREATED", now)
        component.add("SEQUENCE", 0)
    else:
        component.add("SEQUENCE", sequence + 1)


def set_text(component: icalendar.cal.Component, name: str, value: str) -> None:
    component.pop(name, None)
    if value:
        component.add(name, value)


def set_times(component: icalendar.cal.Component, start: date | datetime, end: date | datetime) -> None:
    for name in ("DTSTART", "DTEND", "DURATION"):
        component.pop(name, None)
    component.add("DTSTART", start)
    component.add("DTEND", end)


def add_timezones(calendar: icalendar.Calendar) -> None:
    """VTIMEZONE for every zone the object names (Apple Calendar reads them), over the years the events need."""
    starts = [_as_utc(component.decoded("DTSTART")) for component in events_of(calendar)]
    first = min(starts).date() - timedelta(days=366) if starts else date(1970, 1, 1)
    last = max(max(starts).date() + timedelta(days=366 * 20), date(2038, 1, 1)) if starts else date(2038, 1, 1)
    first = max(first, date(1970, 1, 1))
    known = {str(component.get("TZID")) for component in calendar.subcomponents if component.name == "VTIMEZONE"}
    try:
        calendar.add_missing_timezones(first_date=first, last_date=last)
    except Exception:  # noqa: BLE001 - a zone icalendar cannot describe: devices read the TZID by its name
        for component in list(calendar.subcomponents):
            if component.name == "VTIMEZONE" and str(component.get("TZID")) not in known:
                calendar.subcomponents.remove(component)


def serialize(calendar: icalendar.Calendar) -> str:
    return calendar.to_ical().decode("utf-8")


def local_wall(value: datetime, zone_name_: str) -> datetime:
    """``value`` as the clock on the wall shows it in the zone (naive)."""
    return value.astimezone(zone(zone_name_)).replace(tzinfo=None) if zone_name_ else value.replace(tzinfo=None)


def at_wall(naive: datetime, zone_name_: str) -> datetime:
    """The wall time in the zone as an aware datetime; a time that falls into the gap of the spring change moves on."""
    if not zone_name_:
        return naive.replace(tzinfo=UTC)
    aware = naive.replace(tzinfo=zone(zone_name_))
    # Round trip through UTC: 02:30 on the night clocks spring forward becomes 03:30.
    return aware.astimezone(UTC).astimezone(zone(zone_name_))


def point_of(component: icalendar.cal.Component) -> dict[str, float] | None:
    """Where the event's place lies, when the event says so: ``GEO`` (RFC 5545), or the ``geo:`` of Apple's
    ``X-APPLE-STRUCTURED-LOCATION`` (iCloud, TIME-8 AC-4). ``None`` for a place without a point."""
    latitude = longitude = None
    geo = component.get("GEO")
    if geo is not None:
        latitude, longitude = getattr(geo, "latitude", None), getattr(geo, "longitude", None)
    structured = component.get("X-APPLE-STRUCTURED-LOCATION")
    if latitude is None and structured is not None:
        text = str(structured)
        if text.lower().startswith("geo:"):
            parts = text[4:].split(";")[0].split(",")
            if len(parts) >= 2:
                try:
                    latitude, longitude = float(parts[0]), float(parts[1])
                except ValueError:
                    latitude = longitude = None
    if latitude is None or longitude is None:
        return None
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return {"lat": round(latitude, 6), "lon": round(longitude, 6)}


def midnight(value: date) -> datetime:
    return datetime.combine(value, time(0), tzinfo=UTC)


def components_text(components: list[icalendar.cal.Component]) -> str:
    """Some VEVENTs (changed occurrences a deletion took along) as one VCALENDAR, to keep them as text; "" for none."""
    if not components:
        return ""
    holder = new_calendar()
    for component in components:
        holder.add_component(component)
    return holder.to_ical().decode("utf-8")


# --- What the search looks in (TIME-22) -------------------------------------------------------------------------------

#: The properties of an event the search looks in: title (with the names of holidays in both languages), place, notes.
_SEARCHED = ("SUMMARY", "LOCATION", "DESCRIPTION", "X-NEXTIME-SUMMARY-DE", "X-NEXTIME-SUMMARY-EN")
_ESCAPES = {"\\n": "\n", "\\N": "\n", "\\,": ",", "\\;": ";", "\\\\": "\\"}


def fold_text(value: str) -> str:
    """A text as the search compares it: one form of each character, upper and lower case alike (TIME-22 EC-2)."""
    return unicodedata.normalize("NFC", value).casefold()


def _unescape(value: str) -> str:
    out, index = [], 0
    while index < len(value):
        pair = value[index:index + 2]
        if pair in _ESCAPES:
            out.append(_ESCAPES[pair])
            index += 2
        else:
            out.append(value[index])
            index += 1
    return "".join(out)


def search_text(text: str) -> str:
    """Title, place and notes of every VEVENT of an object (a series with its changed occurrences), folded, one per
    line. Read line by line without the iCalendar library, so that filling in thousands of old rows at the start is
    quick; the alarms' own texts (``DESCRIPTION`` inside a VALARM) are no part of it."""
    unfolded = text.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")
    stack: list[str] = []
    found: list[str] = []
    for line in unfolded.splitlines():
        head, colon, value = line.partition(":")
        if not colon:
            continue
        name = head.split(";", 1)[0].strip().upper()
        if name == "BEGIN":
            stack.append(value.strip().upper())
        elif name == "END":
            if stack:
                stack.pop()
        elif stack and stack[-1] == "VEVENT" and name in _SEARCHED and value:
            found.append(_unescape(value))
    return fold_text("\n".join(found))
