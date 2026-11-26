"""Events (TIME-5) and series (TIME-6) as the interface and the calendar account change them.

Every event is one iCalendar object (``services/ical.py``). The interface sends only what the member changed; it is
put into the object as stored now, so that whatever a device or another person changed meanwhile in other fields or
other occurrences stays (TIME-6 EC-10). When the member edited an older version than the one stored, the later save
wins, and the one it overwrote is told so (TIME-5 EC-2).

Every change is numbered in ``event_changes`` for the calendar account's ``sync-collection`` (TIME-20).
"""

from __future__ import annotations

import copy
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import icalendar
from sqlalchemy import and_, false, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import RIGHT_BUSY, Account, Calendar, Event, EventChange, EventConflict, Source, utcnow
from . import calendars, display, ical, live, rules

TITLE_MAX = 200
#: The name of the API or MCP key a change comes through (TIME-26 AC-10), "" for the web, a device or a source. The
#: routes of programs set it around their call (``acting_via``); ``write`` and ``trash`` take it into the event.
via: ContextVar[str] = ContextVar("nextime_event_via", default="")
NOTES_MAX = 10_000
#: The longest place (TIME-8 EC-6): long ones from Apple Calendar or the clipboard stay whole up to here.
LOCATION_MAX = 2_000
#: The longest window one question about occurrences may cover.
MAX_RANGE_DAYS = 400
#: Properties of a series' master that a single occurrence never copies.
_SERIES_ONLY = {"RRULE", "RDATE", "EXDATE", "EXRULE", "RECURRENCE-ID", "DTSTART", "DTEND", "DURATION"}

SCOPE_THIS = "this"
SCOPE_FOLLOWING = "following"
SCOPE_ALL = "all"
SCOPES = (SCOPE_THIS, SCOPE_FOLLOWING, SCOPE_ALL)


# --- Times as the interface sends them ------------------------------------------------------------------------------


@dataclass
class Times:
    all_day: bool
    #: An aware datetime, or a date for a whole day.
    start: date | datetime
    #: Exclusive: the day after the last for a whole day (as RFC 5545 has it).
    end: date | datetime
    zone: str = ""


def _date(text: str, field_name: str) -> date:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise error("invalid_date", "This date does not exist.", 422, field=field_name) from None


def _wall(text: str, field_name: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        raise error("invalid_date", "This date does not exist.", 422, field=field_name) from None
    if value.tzinfo is not None or len(text) < 16:
        raise error("invalid_date", "This date does not exist.", 422, field=field_name)
    return value.replace(second=0, microsecond=0)


def parse_times(all_day: bool, start: str, end: str | None, zone: str) -> Times:
    """Start and end as the page sends them: ``YYYY-MM-DD`` for a whole day (the end is the last day), otherwise
    ``YYYY-MM-DDTHH:MM`` on the clock of ``zone``. Without an end an event lasts an hour, a whole day one day
    (AC-10); an end before the start is refused (EC-1), and so is a date that does not exist (EC-13)."""
    if all_day:
        first = _date(start, "start")
        last = _date(end, "end") if end else first
        if last < first:
            raise error("end_before_start", "The end lies before the start.", 422, field="end")
        return Times(True, first, last + timedelta(days=1))
    try:
        ical.zone(zone)
    except ValueError:
        raise error("invalid_time_zone", "This time zone is not known.", 422, field="time_zone") from None
    begin = ical.at_wall(_wall(start, "start"), zone)
    finish = ical.at_wall(_wall(end, "end"), zone) if end else begin + timedelta(hours=1)
    if finish < begin:
        raise error("end_before_start", "The end lies before the start.", 422, field="end")
    return Times(False, begin, finish, zone)


def ui_times(start: date | datetime, end: date | datetime, zone: str) -> dict[str, Any]:
    """Start and end for the page: dates with the last day for a whole day, the wall clock in the event's zone."""
    if not isinstance(start, datetime):
        last = (end if not isinstance(end, datetime) else end.date()) - timedelta(days=1)
        return {"all_day": True, "start": start.isoformat(), "end": max(last, start).isoformat(), "time_zone": ""}
    shown = zone or ical.zone_name(start)
    return {
        "all_day": False,
        "start": ical.local_wall(start, shown).strftime("%Y-%m-%dT%H:%M") if shown else start.strftime(
            "%Y-%m-%dT%H:%M"),
        "end": ical.local_wall(end, shown).strftime("%Y-%m-%dT%H:%M") if shown else end.strftime("%Y-%m-%dT%H:%M"),
        "time_zone": shown,
    }


# --- What the page changed -------------------------------------------------------------------------------------------


@dataclass
class Change:
    title: str | None = None
    notes: str | None = None
    busy: bool | None = None
    times: Times | None = None
    #: ``rule_set`` False: the rule stays as it is; True with ``rule`` None: no longer a series.
    rule_set: bool = False
    rule: rules.Rule | None = None
    calendar_id: int | None = None
    touched: set[str] = field(default_factory=set)
    #: The member's own reminders for a new event (TIME-7 AC-2); ``None``: it follows the calendar's default.
    reminders: list[Any] | None = None
    #: The place (TIME-8); ``None``: unchanged. ``geo_set`` with ``geo`` (latitude, longitude) or ``None``: the point of
    #: a chosen suggestion, or none.
    location: str | None = None
    geo_set: bool = False
    geo: tuple[float, float] | None = None
    #: The people invited (TIME-18 AC-12, plan "Apps verbinden" V30): ``None`` unchanged; a list of (address, name)
    #: the whole list, empty for none. Only in a calendar of iCloud or Google, which send the invitations themselves;
    #: ``organizer`` is the source's account (address, name), set by ``create``/``update``.
    attendees: list[tuple[str, str]] | None = None
    organizer: tuple[str, str] | None = None


def clean_title(raw: str) -> str:
    title = (raw or "").strip()
    if not title:
        raise error("title_missing", "The event needs a title.", 422, field="title")
    if len(title) > TITLE_MAX:
        raise error("title_too_long", f"At most {TITLE_MAX} characters.", 422, field="title", max=TITLE_MAX)
    return title


def clean_location(raw: str) -> str:
    """Blanks at the edges go; umlauts, emoji and line breaks stay (TIME-8 EC-6)."""
    location = (raw or "").strip()
    if len(location) > LOCATION_MAX:
        raise error("location_too_long", f"At most {LOCATION_MAX} characters.", 422, field="location",
                    max=LOCATION_MAX)
    return location


def clean_geo(latitude: float, longitude: float) -> tuple[float, float]:
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise error("invalid_input", "The input is not valid.", 422, fields=["geo"])
    return round(latitude, 6), round(longitude, 6)


def clean_notes(raw: str) -> str:
    if len(raw) > NOTES_MAX:
        raise error("notes_too_long", f"At most {NOTES_MAX} characters.", 422, field="notes", max=NOTES_MAX)
    return raw


def checked_rule(rule: rules.Rule | None, first: date | datetime) -> rules.Rule | None:
    if rule is None:
        return None
    day = first.date() if isinstance(first, datetime) else first
    try:
        return rules.check(rule, day)
    except rules.RuleError as exc:
        raise error(exc.code, "The rule of the series is not valid.", 422, field=f"rule.{exc.field}") from None


# --- Storing --------------------------------------------------------------------------------------------------------


def record_change(db: Session, calendar_id: int, href: str, deleted: bool = False, *, remote: bool = True,
                  row: Event | None = None, by: int | None = None) -> None:
    """Numbers the change for the calendar account, tells the open pages, and, for a calendar from iCloud, Google or
    another CalDAV server, notes it to go there (TIME-15 AC-3); a change that came from the source does not
    (``remote=False``). A change of a member to an event (``row``) waits for the notifications of the others who
    asked for them (TIME-21); ``by`` is who made it (else the event's last writer or deleter)."""
    db.add(EventChange(calendar_id=calendar_id, href=href, deleted=deleted))
    live.touch_calendar(db, calendar_id)
    from . import reminders

    # Only this event is planned anew, for the accounts that see its calendar (review of TIME-7, finding 4).
    reminders.touch_on_commit(db, calendar_id=calendar_id, href=href)
    if row is not None:
        from . import notify

        actor = by if by is not None else (row.deleted_by if deleted else row.updated_by)
        notify.note(db, calendar_id, row, deleted=deleted, by=actor)
    if remote:
        from . import sync

        sync.note_change(db, calendar_id, href, deleted, row)


def write(db: Session, row: Event, text: str, parsed: icalendar.Calendar, by: int | None) -> None:
    """The object as the event's new version, with its derived columns; the one before is kept for EC-2."""
    derived = ical.derive(parsed)
    # Where it began before this change: a notification says "moved to 11:30" (TIME-21 AC-3).
    row.nx_before_start = row.starts_at if row.etag else None  # type: ignore[attr-defined]
    if row.etag:
        row.prev_etag = row.etag
        row.prev_ical = row.ical
    row.ical = text
    row.etag = ical.etag_of(text)
    row.size = len(text.encode("utf-8"))
    row.title = derived.title
    row.starts_at = derived.starts_at
    row.ends_at = derived.ends_at
    row.all_day = derived.all_day
    row.time_zone = derived.time_zone
    row.recurring = derived.recurring
    row.busy = derived.busy
    row.location = derived.location
    # What the search looks in, written with every version (TIME-22): the page, the calendar account and the sources
    # all write through here.
    row.search_text = ical.search_text(text)
    # The alarms of the text (a source's): read once here, not at every planning of reminders (review of TIME-7,
    # finding 4).
    from . import alarms

    row.alarm_leads = alarms.leads(parsed) if alarms.present(text) else []
    row.alarm_lead_max = max(row.alarm_leads) if row.alarm_leads else None
    row.updated_by = by
    row.updated_via = via.get()
    row.updated_at = utcnow()


def _store(db: Session, row: Event, parsed: icalendar.Calendar, by: int | None) -> None:
    ical.add_timezones(parsed)
    write(db, row, ical.serialize(parsed), parsed, by)
    record_change(db, row.calendar_id, row.href, row=row)


def find(db: Session, account: Account, event_id: int, *, for_writing: bool) -> tuple[Event, calendars.Access]:
    """The event and the account's right to its calendar. Gone in the meantime: who deleted it (EC-9); a calendar
    deleted meanwhile says so (TIME-4 EC-7)."""
    row = db.get(Event, event_id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    calendar = db.get(Calendar, row.calendar_id)
    found = calendars.access(db, account.id, row.calendar_id)
    if found is None:
        if calendar is not None and calendar.deleted_at is not None and _knew(db, account, calendar):
            raise error("calendar_gone", "This calendar does not exist any more.", 409, field="calendar_id")
        raise error("not_found", "Not found.", 404)
    if row.deleted_at is not None:
        by = db.get(Account, row.deleted_by) if row.deleted_by else None
        raise error("event_deleted", "This event was deleted.", 409, by=by.shown_name if by else "")
    if for_writing and not found.writable:
        raise error("calendar_read_only", "This calendar may only be read.", 403, owner=_owner_name(db, found))
    return row, found


def _knew(db: Session, account: Account, calendar: Calendar) -> bool:
    """The account saw this calendar before it went into the trash: its own, or shared with it (TIME-4 EC-7)."""
    from ..models import CalendarShare

    return calendar.owner_id == account.id or db.get(CalendarShare, (calendar.id, account.id)) is not None


def _writable_calendar(db: Session, account: Account, calendar_id: int) -> calendars.Access:
    found = calendars.access(db, account.id, calendar_id)
    if found is None:
        gone = db.get(Calendar, calendar_id)
        if gone is not None and gone.deleted_at is not None and _knew(db, account, gone):
            raise error("calendar_gone", "This calendar does not exist any more.", 409, field="calendar_id")
        raise error("not_found", "Not found.", 404)
    if not found.writable:
        raise error("calendar_read_only", "This calendar may only be read.", 403, field="calendar_id",
                    owner=_owner_name(db, found))
    return found


def _owner_name(db: Session, found: calendars.Access) -> str:
    """Who decides about a shared calendar: the one who took the right to edit away (TIME-5 EC-9)."""
    owner = db.get(Account, found.calendar.owner_id)
    return owner.shown_name if owner is not None else ""


class acting_via:
    """Changes inside come through the API or MCP key ``name`` (TIME-26 AC-10)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> None:
        self.token = via.set(self.name)

    def __exit__(self, *_exc: object) -> None:
        via.reset(self.token)


# --- Making -----------------------------------------------------------------------------------------------------------


def _apply_fields(component: icalendar.cal.Component, change: Change) -> None:
    if change.title is not None:
        ical.set_text(component, "SUMMARY", change.title)
    if change.notes is not None:
        ical.set_text(component, "DESCRIPTION", change.notes)
    if change.busy is not None:
        component.pop("TRANSP", None)
        component.add("TRANSP", "OPAQUE" if change.busy else "TRANSPARENT")
    if change.location is not None:
        before = str(component.get("LOCATION", ""))
        ical.set_text(component, "LOCATION", change.location)
        if change.location != before or change.geo_set:
            # The point of the place before is no longer the place's (TIME-8): only a chosen suggestion brings one.
            component.pop("GEO", None)
            component.pop("X-APPLE-STRUCTURED-LOCATION", None)
    if change.geo_set and change.geo is not None and change.location:
        component.add("GEO", change.geo)
    if change.attendees is not None and change.organizer is not None:
        _apply_attendees(component, change.attendees, change.organizer)


#: The sources that take attendees and send the invitations themselves (TIME-18 AC-12, V30).
ATTENDEE_SOURCES = ("icloud", "google")


def _address_of(value: Any) -> str:
    text = str(value or "").strip()
    return (text[7:] if text.lower().startswith("mailto:") else text).casefold()


def _apply_attendees(component: icalendar.cal.Component, wanted: list[tuple[str, str]],
                     organizer: tuple[str, str]) -> None:
    """The attendees of an event the member organizes: the organizer (the source's account) and the people wanted.
    Answers of people who stay are kept; someone else's invitation is not changed."""
    current = component.get("ORGANIZER")
    if current is not None and _address_of(current) != organizer[0].casefold():
        raise error("attendees_not_yours", "Only the one who invites changes who is invited.", 409, field="attendees")
    found = component.get("ATTENDEE")
    before = {_address_of(item): item for item in (found if isinstance(found, list) else [found] if found else [])}
    component.pop("ATTENDEE", None)
    component.pop("ORGANIZER", None)
    if not wanted:
        return
    boss = icalendar.vCalAddress(f"mailto:{organizer[0]}")
    if organizer[1]:
        boss.params["CN"] = icalendar.vText(organizer[1])
    component.add("ORGANIZER", boss)
    me = before.get(organizer[0].casefold())
    if me is None:
        me = icalendar.vCalAddress(f"mailto:{organizer[0]}")
        me.params.update({"PARTSTAT": "ACCEPTED", "ROLE": "CHAIR", "CUTYPE": "INDIVIDUAL"})
        if organizer[1]:
            me.params["CN"] = icalendar.vText(organizer[1])
    component.add("ATTENDEE", me)
    for address, name in wanted:
        if address.casefold() == organizer[0].casefold():
            continue
        kept = before.get(address.casefold())
        if kept is None:
            kept = icalendar.vCalAddress(f"mailto:{address}")
            kept.params.update({"PARTSTAT": "NEEDS-ACTION", "RSVP": "TRUE", "ROLE": "REQ-PARTICIPANT",
                                "CUTYPE": "INDIVIDUAL"})
            if name:
                kept.params["CN"] = icalendar.vText(name)
        component.add("ATTENDEE", kept)


def organizer_for(db: Session, account: Account, calendar_id: int) -> tuple[str, str]:
    """Who invites in ``calendar_id``: the account of its source, where the source takes attendees (iCloud, Google);
    anywhere else 422 ``attendees_not_here`` (nextime's own calendars send no invitations, V30)."""
    calendar = db.get(Calendar, calendar_id)
    source = db.get(Source, calendar.source_id) if calendar is not None and calendar.source_id else None
    if source is None or source.kind not in ATTENDEE_SOURCES or not source.username:
        raise error("attendees_not_here", "Only events of iCloud and Google calendars invite people.", 422,
                    field="attendees")
    return source.username, account.shown_name


def create(db: Session, account: Account, change: Change, client_ref: str | None) -> tuple[Event, bool]:
    """A new event (AC-1). ``client_ref`` again gives the event made the first time (EC-8). True when new."""
    if client_ref:
        known = db.scalar(select(Event).where(Event.created_by == account.id, Event.client_ref == client_ref))
        if known is not None:
            return known, False
    calendars.ensure_seeded(db, account)
    calendar_id = change.calendar_id
    if calendar_id is None:
        chosen = calendars.default_of(db, account.id).calendar
        if chosen is None:
            raise error("no_writable_calendar", "There is no calendar to write into.", 409)
        calendar_id = chosen.id
    target = _writable_calendar(db, account, calendar_id)
    assert change.times is not None and change.title is not None
    uid = str(uuid.uuid4())
    parsed = ical.new_calendar()
    component = icalendar.Event()
    component.add("UID", uid)
    ical.set_times(component, change.times.start, change.times.end)
    busy = change.busy if change.busy is not None else not change.times.all_day
    if change.attendees:
        change.organizer = organizer_for(db, account, target.calendar.id)
    _apply_fields(component, Change(title=change.title, notes=change.notes or "", busy=busy,
                                    location=change.location, geo_set=change.geo_set, geo=change.geo,
                                    attendees=change.attendees if change.attendees else None,
                                    organizer=change.organizer))
    rule = checked_rule(change.rule, change.times.start)
    if rule is not None:
        component.add("RRULE", rules.to_rrule(rule, change.times.start))
    ical.stamp(component)
    parsed.add_component(component)
    row = Event(calendar_id=target.calendar.id, uid=uid, href=f"{uid}.ics", ical="", etag="", created_by=account.id,
                client_ref=client_ref, starts_at=utcnow())
    db.add(row)
    _store(db, row, parsed, account.id)
    try:
        if change.reminders is not None:
            from . import reminders

            reminders.choose(db, account, row, reminders.clean(change.reminders, change.times.all_day))
        if client_ref:
            from . import attachments

            # Files the member attached while making the event (TIME-9 AC-10).
            db.flush()
            attachments.attach_pending(db, account.id, client_ref, row)
        db.commit()
    except IntegrityError:
        db.rollback()
        known = db.scalar(select(Event).where(Event.created_by == account.id, Event.client_ref == client_ref))
        if known is None:
            raise
        return known, False
    return row, True


# --- Changing ---------------------------------------------------------------------------------------------------------


def _occurrence_start(master: icalendar.Event, key: str) -> date | datetime:
    """The original start of the occurrence ``key`` names, in the master's kind and zone."""
    start = master.decoded("DTSTART")
    try:
        if isinstance(start, datetime):
            value = datetime.strptime(key.replace("Z", "+0000"), "%Y%m%dT%H%M%S%z")
            return value.astimezone(start.tzinfo) if start.tzinfo else value.replace(tzinfo=None)
        if len(key) != 8 or not key.isdigit():
            raise ValueError(key)
        return date(int(key[0:4]), int(key[4:6]), int(key[6:8]))
    except (TypeError, ValueError):
        raise error("occurrence_unknown", "This occurrence is not part of the series.", 422) from None


def _occurs(parsed: icalendar.Calendar, original: date | datetime) -> bool:
    begin = ical._as_utc(original)
    for occurrence in ical.occurrences(parsed, begin - timedelta(days=1), begin + timedelta(days=2)):
        if occurrence.recurrence_id is not None and ical.same_recurrence(occurrence.recurrence_id, original):
            return True
    return False


def _override(parsed: icalendar.Calendar, original: date | datetime) -> icalendar.Event | None:
    for component in ical.overrides_of(parsed):
        if ical.same_recurrence(component.decoded("RECURRENCE-ID"), original):
            return component
    return None


def _new_override(parsed: icalendar.Calendar, master: icalendar.Event, original: date | datetime) -> icalendar.Event:
    """A single occurrence of its own: everything of the master but the rule, at its original time."""
    component = icalendar.Event()
    for name, value in master.property_items(recursive=False):
        if name in ("BEGIN", "END") or name in _SERIES_ONLY:
            continue
        component.add(name, copy.deepcopy(value), encode=False)
    for sub in master.subcomponents:
        component.add_component(copy.deepcopy(sub))
    component.add("RECURRENCE-ID", original)
    ical.set_times(component, original, original + ical.duration_of(master))
    parsed.add_component(component)
    return component


def _snapshot(parsed: icalendar.Calendar, original: date | datetime | None) -> tuple:
    component = ical.master_of(parsed) if original is None else (_override(parsed, original) or ical.master_of(
        parsed))
    return tuple(str(component.get(name, "")) for name in ("SUMMARY", "DESCRIPTION", "TRANSP", "LOCATION")) + (
        component.get("DTSTART").to_ical() if original is None or _override(parsed, original) is not None else b"",
    )


def _note_conflict(db: Session, row: Event, account: Account, base_etag: str | None,
                   original: date | datetime | None) -> str | None:
    """Saving over a version the member did not see: the account that made the version overwritten hears of it when
    it next opens the event (EC-2). Returns the overwritten object, to keep as theirs."""
    if not base_etag or base_etag == row.etag:
        return None
    if original is not None:
        # One occurrence: a conflict only when that very occurrence changed since the member's version (TIME-6 EC-10).
        if row.prev_etag != base_etag:
            return None
        before = ical.parse(db.scalar(select(Event.prev_ical).where(Event.id == row.id)) or row.ical)
        if _snapshot(before, original) == _snapshot(ical.parse(row.ical), original):
            return None
    return row.ical


def _keep_conflict(db: Session, row: Event, overwritten: str | None, account: Account) -> None:
    if overwritten is None or row.updated_by is None:
        return
    db.add(EventConflict(event_id=row.id, account_id=row.updated_by, by_id=account.id, mine_ical=overwritten,
                         theirs_etag=""))


def _shift(value: date | datetime, wall_delta: timedelta, zone: str) -> date | datetime:
    """``value`` moved by a span on the wall clock (09:00 stays 09:00 over the change of the clocks, EC-3)."""
    if not isinstance(value, datetime):
        return value + timedelta(days=wall_delta.days)
    if value.tzinfo is None:
        return value + wall_delta
    zone_name = zone or ical.zone_name(value)
    if not zone_name:
        return value + wall_delta
    return ical.at_wall(ical.local_wall(value, zone_name) + wall_delta, zone_name)


def _wall_delta(old: date | datetime, new: date | datetime) -> timedelta:
    if isinstance(old, datetime) and isinstance(new, datetime):
        old_zone, new_zone = ical.zone_name(old), ical.zone_name(new)
        return ical.local_wall(new, new_zone or old_zone) - ical.local_wall(old, old_zone or new_zone)
    old_day = old.date() if isinstance(old, datetime) else old
    new_day = new.date() if isinstance(new, datetime) else new
    return timedelta(days=(new_day - old_day).days)


def _move_exceptions(master: icalendar.Event, overrides: list[icalendar.Event], delta: timedelta,
                     zone: str, to_date: bool | None = None) -> None:
    """EXDATEs and the RECURRENCE-IDs of single occurrences follow the series when it moves, so that cancelled
    occurrences stay cancelled and changed ones stay changed, as they are (EC-16)."""
    if not delta and to_date is None:
        return

    def moved(value: date | datetime) -> date | datetime:
        shifted = _shift(value, delta, zone)
        if to_date is True and isinstance(shifted, datetime):
            return shifted.date()
        return shifted

    exdates = master.get("EXDATE")
    if exdates is not None:
        values = [item.dt for entry in (exdates if isinstance(exdates, list) else [exdates]) for item in entry.dts]
        master.pop("EXDATE")
        for value in values:
            master.add("EXDATE", moved(value))
    for component in overrides:
        original = component.decoded("RECURRENCE-ID")
        component.pop("RECURRENCE-ID")
        component.add("RECURRENCE-ID", moved(original))


def _apply_times_to_series(master: icalendar.Event, overrides: list[icalendar.Event], original: date | datetime,
                           times: Times, rule_changed: bool) -> None:
    """The series moves as the occurrence moved: by the same span from its first start; its length is the new one."""
    old_start = master.decoded("DTSTART")
    delta = _wall_delta(original, times.start)
    if times.all_day:
        first = (old_start.date() if isinstance(old_start, datetime) else old_start) + timedelta(days=delta.days)
        new_start: date | datetime = first
        new_end: date | datetime = first + (times.end - times.start)
    elif isinstance(old_start, datetime):
        moved = _shift(old_start, delta, ical.zone_name(old_start))
        new_start = ical.at_wall(ical.local_wall(moved, ical.zone_name(old_start) or times.zone), times.zone)
        new_end = new_start + (times.end - times.start)
    else:
        day = old_start + timedelta(days=delta.days)
        new_start = ical.at_wall(datetime.combine(day, ical.local_wall(times.start, times.zone).time()), times.zone)
        new_end = new_start + (times.end - times.start)
    was_date = not isinstance(old_start, datetime)
    to_date = True if times.all_day and not was_date else None
    if not times.all_day and was_date:
        # From whole days to times: cancelled and changed days keep their day at the new time.
        for component in overrides:
            component.pop("RECURRENCE-ID")
        master.pop("EXDATE", None)
    else:
        _move_exceptions(master, overrides, delta, ical.zone_name(old_start), to_date)
    ical.set_times(master, new_start, new_end)
    if not rule_changed and master.get("RRULE") is not None and delta.days:
        recur = master["RRULE"]
        current = rules.read(recur, old_start)
        if current is not None and current.freq == "weekly":
            master.pop("RRULE")
            master.add("RRULE", rules.to_rrule(rules.shift_weekdays(current, delta.days), new_start))


def update(db: Session, account: Account, event_id: int, change: Change, *, scope: str, occurrence: str | None,
           base_etag: str | None) -> Event:
    """Changes what the member changed: of a single event, of one occurrence, of this and the following, or of the
    whole series (AC-2, EC-10, EC-11, EC-16). All or nothing (EC-15)."""
    row, _found = find(db, account, event_id, for_writing=True)
    if change.calendar_id is not None and change.calendar_id != row.calendar_id:
        if occurrence and scope == SCOPE_THIS:
            raise error("scope_calendar", "A single occurrence cannot move to another calendar.", 422)
        _writable_calendar(db, account, change.calendar_id)
    if change.attendees is not None:
        # Who invites: the account of the source the event stays in or moves to (TIME-18 AC-12, V30).
        change.organizer = organizer_for(db, account, change.calendar_id or row.calendar_id)
    parsed = ical.parse(row.ical)
    master = ical.master_of(parsed)
    series = master.get("RRULE") is not None or master.get("RDATE") is not None
    original = _occurrence_start(master, occurrence) if (series and occurrence) else None
    if original is not None and not _occurs(parsed, original):
        raise error("occurrence_unknown", "This occurrence is not part of the series.", 422)
    if original is not None and scope == SCOPE_FOLLOWING and ical.same_recurrence(original,
                                                                                    master.decoded("DTSTART")):
        scope = SCOPE_ALL  # the first occurrence: "this and the following" is the whole series (EC-11)
    if original is not None and scope == SCOPE_FOLLOWING:
        return _split(db, account, row, parsed, master, original, change, base_etag)
    overwritten = _note_conflict(db, row, account, base_etag, original if scope == SCOPE_THIS else None)
    if original is not None and scope == SCOPE_THIS:
        target = _override(parsed, original) or _new_override(parsed, master, original)
        if change.times is not None:
            ical.set_times(target, change.times.start, change.times.end)
        _apply_fields(target, change)
        ical.stamp(target)
    else:
        if change.times is not None:
            if series and original is not None:
                _apply_times_to_series(master, ical.overrides_of(parsed), original, change.times, change.rule_set)
            elif series:
                _apply_times_to_series(master, ical.overrides_of(parsed), master.decoded("DTSTART"), change.times,
                                       change.rule_set)
            else:
                ical.set_times(master, change.times.start, change.times.end)
        if change.rule_set:
            master.pop("RRULE", None)
            rule = checked_rule(change.rule, master.decoded("DTSTART"))
            if rule is not None:
                master.add("RRULE", rules.to_rrule(rule, master.decoded("DTSTART")))
            else:
                master.pop("EXDATE", None)
                master.pop("RDATE", None)
                for component in ical.overrides_of(parsed):
                    parsed.subcomponents.remove(component)
        _apply_fields(master, change)
        ical.stamp(master)
    _keep_conflict(db, row, overwritten, account)
    if change.calendar_id is not None and change.calendar_id != row.calendar_id:
        from . import attachments

        attachments.event_moved(db, row, change.calendar_id)
        _move(db, row, change.calendar_id, account.id)
    _store(db, row, parsed, account.id)
    db.commit()
    return row


def _move(db: Session, row: Event, calendar_id: int, by: int) -> None:
    clash = db.scalar(select(Event.id).where(Event.calendar_id == calendar_id, Event.deleted_at.is_(None),
                                             (Event.uid == row.uid) | (Event.href == row.href)))
    if clash is not None:
        raise error("event_exists", "The other calendar holds this event already.", 409, field="calendar_id")
    record_change(db, row.calendar_id, row.href, deleted=True, row=row, by=by)
    row.calendar_id = calendar_id
    # In the other calendar it is new: its notification says "added" (TIME-21).
    row.nx_arrived = True  # type: ignore[attr-defined]


def _count_before(master: icalendar.Event, original: date | datetime) -> int:
    begin = master.decoded("DTSTART")
    count = 0
    for value in ical._rule_of(master):
        moment = value
        if isinstance(begin, datetime) and begin.tzinfo is not None and value.tzinfo is None:
            moment = value.replace(tzinfo=begin.tzinfo)
        compare = moment if isinstance(begin, datetime) else moment.date()
        if ical._as_utc(compare) >= ical._as_utc(original):
            break
        count += 1
    return count


def _split(db: Session, account: Account, row: Event, parsed: icalendar.Calendar, master: icalendar.Event,
           original: date | datetime, change: Change, base_etag: str | None) -> Event:
    """"This and all following" from a later occurrence: the old series ends the day before, a new one begins with
    this occurrence and takes along the later exceptions (EC-11). Both in one transaction (EC-15)."""
    overwritten = _note_conflict(db, row, account, base_etag, None)
    recur = master["RRULE"]
    old_start = master.decoded("DTSTART")
    old_count = int(rules._first(recur["COUNT"])) if "COUNT" in recur else None
    before = _count_before(master, original) if old_count else 0
    fresh = ical.new_calendar()
    new_master = icalendar.Event()
    for name, value in master.property_items(recursive=False):
        if name in ("BEGIN", "END") or name in _SERIES_ONLY or name in ("UID", "CREATED", "SEQUENCE"):
            continue
        new_master.add(name, copy.deepcopy(value), encode=False)
    for sub in master.subcomponents:
        new_master.add_component(copy.deepcopy(sub))
    uid = str(uuid.uuid4())
    new_master.add("UID", uid)
    length = ical.duration_of(master)
    ical.set_times(new_master, original, original + length)
    rule = rules.read(recur, old_start)
    if change.rule_set:
        rule = checked_rule(change.rule, change.times.start if change.times else original)
        new_recur = rules.to_rrule(rule, change.times.start if change.times else original) if rule else None
    elif rule is not None:
        if old_count:
            rule.count = max(1, old_count - before)
        new_recur = rules.to_rrule(rule, original)
    else:
        parts = dict(recur)
        if old_count:
            parts["COUNT"] = [max(1, old_count - before)]
        new_recur = icalendar.vRecur(parts)
    if new_recur is not None:
        new_master.add("RRULE", new_recur)
    fresh.add_component(new_master)
    # Later exceptions go with the new series.
    cut = ical._as_utc(original)
    exdates = master.get("EXDATE")
    if exdates is not None:
        values = [item.dt for entry in (exdates if isinstance(exdates, list) else [exdates]) for item in entry.dts]
        master.pop("EXDATE")
        for value in values:
            (new_master if ical._as_utc(value) >= cut else master).add("EXDATE", value)
    for component in ical.overrides_of(parsed):
        if ical._as_utc(component.decoded("RECURRENCE-ID")) >= cut:
            parsed.subcomponents.remove(component)
            component.pop("UID")
            component.add("UID", uid)
            fresh.add_component(component)
    master.pop("RRULE")
    master.add("RRULE", rules.end_before(recur, original, old_start))
    ical.stamp(master)
    if change.times is not None:
        _apply_times_to_series(new_master, ical.overrides_of(fresh), original, change.times, change.rule_set)
    _apply_fields(new_master, change)
    ical.stamp(new_master)
    _keep_conflict(db, row, overwritten, account)
    _store(db, row, parsed, account.id)
    calendar_id = change.calendar_id or row.calendar_id
    successor = Event(calendar_id=calendar_id, uid=uid, href=f"{uid}.ics", ical="", etag="", created_by=account.id,
                      starts_at=utcnow())
    db.add(successor)
    _store(db, successor, fresh, account.id)
    db.commit()
    return successor


def tail_of(parsed: icalendar.Calendar, original: date | datetime, uid: str) -> icalendar.Calendar:
    """The occurrences of a series from ``original`` on as a series of its own under ``uid``, with the later
    cancelled and changed occurrences: what "this and the following" cut off, made whole again (TIME-23, review
    finding 2). ``parsed`` is used up."""
    master = ical.master_of(parsed)
    recur = master["RRULE"]
    old_start = master.decoded("DTSTART")
    old_count = int(rules._first(recur["COUNT"])) if "COUNT" in recur else None
    before = _count_before(master, original) if old_count else 0
    fresh = ical.new_calendar()
    new_master = icalendar.Event()
    for name, value in master.property_items(recursive=False):
        if name in ("BEGIN", "END") or name in _SERIES_ONLY or name in ("UID", "CREATED", "SEQUENCE"):
            continue
        new_master.add(name, copy.deepcopy(value), encode=False)
    for sub in master.subcomponents:
        new_master.add_component(copy.deepcopy(sub))
    new_master.add("UID", uid)
    ical.set_times(new_master, original, original + ical.duration_of(master))
    rule = rules.read(recur, old_start)
    if rule is not None:
        if old_count:
            rule.count = max(1, old_count - before)
        new_master.add("RRULE", rules.to_rrule(rule, original))
    else:
        parts = dict(recur)
        if old_count:
            parts["COUNT"] = [max(1, old_count - before)]
        new_master.add("RRULE", icalendar.vRecur(parts))
    cut = ical._as_utc(original)
    exdates = master.get("EXDATE")
    if exdates is not None:
        for entry in exdates if isinstance(exdates, list) else [exdates]:
            for item in entry.dts:
                if ical._as_utc(item.dt) >= cut:
                    new_master.add("EXDATE", item.dt)
    ical.stamp(new_master)
    fresh.add_component(new_master)
    for component in ical.overrides_of(parsed):
        if ical._as_utc(component.decoded("RECURRENCE-ID")) >= cut:
            component.pop("UID")
            component.add("UID", uid)
            fresh.add_component(component)
    return fresh


def single_from(component: icalendar.Event, start: date | datetime, end: date | datetime,
                uid: str) -> icalendar.Calendar:
    """One occurrence as an event of its own under ``uid``: its fields, without the series' rule and name."""
    fresh = ical.new_calendar()
    single = icalendar.Event()
    for name, value in component.property_items(recursive=False):
        if name in ("BEGIN", "END") or name in _SERIES_ONLY or name in ("UID", "CREATED", "SEQUENCE"):
            continue
        single.add(name, copy.deepcopy(value), encode=False)
    for sub in component.subcomponents:
        single.add_component(copy.deepcopy(sub))
    single.add("UID", uid)
    ical.set_times(single, start, end)
    ical.stamp(single)
    fresh.add_component(single)
    return fresh


def add_new(db: Session, calendar_id: int, parsed: icalendar.Calendar, uid: str, by: int) -> Event:
    """A new event of an object made here (a part of a series restored as its own)."""
    row = Event(calendar_id=calendar_id, uid=uid, href=f"{uid}.ics", ical="", etag="", created_by=by,
                starts_at=utcnow())
    db.add(row)
    _store(db, row, parsed, by)
    return row


# --- Deleting ---------------------------------------------------------------------------------------------------------


def delete(db: Session, account: Account, event_id: int, *, scope: str, occurrence: str | None) -> dict[str, Any]:
    """Into the trash (AC-6); of a series one occurrence (EXDATE), this and the following, or all. Answers which entry
    of the trash it became (``kind`` "event" or "part" and its ``id``), so that "Undo" can bring it back from there."""
    row, _found = find(db, account, event_id, for_writing=True)
    parsed = ical.parse(row.ical)
    master = ical.master_of(parsed)
    series = master.get("RRULE") is not None or master.get("RDATE") is not None
    original = _occurrence_start(master, occurrence) if (series and occurrence) else None
    if original is not None and scope == SCOPE_FOLLOWING and ical.same_recurrence(original,
                                                                                    master.decoded("DTSTART")):
        scope = SCOPE_ALL
    if original is None or scope == SCOPE_ALL:
        trash(db, row, account.id)
        db.commit()
        return {"kind": "event", "id": row.id}
    if not _occurs(parsed, original):
        raise error("occurrence_unknown", "This occurrence is not part of the series.", 422)
    before = row.ical
    taken: list[icalendar.Event] = []
    if scope == SCOPE_THIS:
        changed = _override(parsed, original)
        if changed is not None:
            parsed.subcomponents.remove(changed)
            taken.append(changed)
        shown = changed if changed is not None else master
        start = changed.decoded("DTSTART") if changed is not None else original
        master.add("EXDATE", original)
        exceptions = False
    else:
        cut = ical._as_utc(original)
        recur = master["RRULE"]
        master.pop("RRULE")
        master.add("RRULE", rules.end_before(recur, original, master.decoded("DTSTART")))
        exdates = master.get("EXDATE")
        exceptions = False
        if exdates is not None:
            values = [item.dt for entry in (exdates if isinstance(exdates, list) else [exdates]) for item in entry.dts]
            master.pop("EXDATE")
            for value in values:
                if ical._as_utc(value) < cut:
                    master.add("EXDATE", value)
                else:
                    exceptions = True
        for component in ical.overrides_of(parsed):
            if ical._as_utc(component.decoded("RECURRENCE-ID")) >= cut:
                parsed.subcomponents.remove(component)
                taken.append(component)
                exceptions = True
        shown, start = master, original
    ical.stamp(master)
    _store(db, row, parsed, account.id)
    from . import trash as trash_service

    # The part goes into the trash of the calendar's owner (TIME-5 AC-6, TIME-23; review of TIME-23, finding 2).
    part = trash_service.part_deleted(
        db, row, kind="occurrence" if scope == SCOPE_THIS else "following", occurrence=ical.recurrence_key(original),
        title=summary_in(shown, language_of(account)), start=start, removed=ical.components_text(taken),
        before=before if scope != SCOPE_THIS else "", exceptions=exceptions, by=account.id)
    db.flush()
    entry = {"kind": "part", "id": part.id}
    db.commit()
    return entry


def trash(db: Session, row: Event, by: int | None) -> None:
    """Into the trash of the calendar's owner (TIME-23 EC-10), for the deadline that holds now (EC-13), with the
    name of who deleted it as it is now (EC-12)."""
    from . import trash as trash_service

    row.deleted_at = trash_service.clock()
    row.deleted_by = by
    row.trash_until = trash_service.until(db, row.deleted_at)
    row.deleted_by_name = trash_service.name_of(db, by)
    row.deleted_via = via.get()
    record_change(db, row.calendar_id, row.href, deleted=True, row=row)


# --- The conflict (EC-2) ------------------------------------------------------------------------------------------


def conflict_of(db: Session, account: Account, row: Event) -> EventConflict | None:
    return db.scalar(select(EventConflict).where(EventConflict.event_id == row.id,
                                                 EventConflict.account_id == account.id)
                     .order_by(EventConflict.id.desc()))


def resolve_conflict(db: Session, account: Account, event_id: int, *, restore: bool) -> Event:
    """"Restore mine" makes the overwritten version the valid one again; "Leave it" keeps the later one."""
    row, _found = find(db, account, event_id, for_writing=restore)
    found = conflict_of(db, account, row)
    if found is None:
        raise error("not_found", "Not found.", 404)
    mine = found.mine_ical
    for item in db.scalars(select(EventConflict).where(EventConflict.event_id == row.id,
                                                       EventConflict.account_id == account.id)):
        db.delete(item)
    if restore:
        parsed = ical.parse(mine)
        ical.stamp(ical.master_of(parsed))
        _store(db, row, parsed, account.id)
    db.commit()
    return row


# --- What the page shows ----------------------------------------------------------------------------------------------


def busy_only(found: calendars.Access) -> bool:
    return found.right == RIGHT_BUSY


def language_of(account: Account) -> str:
    """The language names are shown in: holidays carry theirs in German and English (TIME-17 AC-7)."""
    return "de" if (account.language or account.browser_language or "").lower().startswith("de") else "en"


def summary_in(component: icalendar.cal.Component, language: str) -> str:
    translated = component.get(f"X-NEXTIME-SUMMARY-{language.upper()}")
    return str(translated if translated is not None else component.get("SUMMARY", ""))


def occurrence_list(db: Session, account: Account, start: datetime, end: datetime,
                    include_hidden: bool = False, only: set[int] | None = None,
                    include_declined: bool = False,
                    events_of: dict[int, set[str]] | None = None,
                    merge: bool | None = None) -> list[dict[str, Any]]:
    """Every occurrence in [start, end) in the calendars the account shows (hidden ones not, AC-3); series rolled
    out with their exceptions. "Busy only" calendars give blocks without title and leave out free events.

    ``only`` keeps the calendars of the collection chosen on the device (TIME-13 AC-3). Each occurrence brings what
    the views draw calmly (TIME-14): who entered it in a shared calendar (``person``, AC-2), its place and version;
    the same event in two of one person's calendars comes once with all its ``sources`` (AC-3), unless the account
    switched that off (AC-4); ``merge`` overrides that (the year counts duplicates once, TIME-12 AC-18)."""
    if end <= start or (end - start).days > MAX_RANGE_DAYS:
        raise error("invalid_range", "Ask for at most 400 days at once.", 422)
    listed = [(item, setting) for item, setting in calendars.ordered(db, account.id)
              if (include_hidden or not setting.hidden) and (only is None or item.calendar.id in only)]
    shown = {item.calendar.id: item for item, _setting in listed}
    if not shown:
        return []
    place = {calendar_id: index for index, calendar_id in enumerate(shown)}
    shared = _shared_calendars(db, shown)
    from . import invitations

    # The member's own addresses at each source it connected: what makes an event its invitation (TIME-18).
    own = {calendar_id: invitations.context(db, item)[0] for calendar_id, item in shown.items()}
    language = language_of(account)
    people = _People(db)
    margin = timedelta(days=1)
    statement = select(Event).where(
        Event.calendar_id.in_(shown), Event.deleted_at.is_(None), Event.starts_at < end + margin,
        (Event.ends_at.is_(None)) | (Event.ends_at > start - margin),
    )
    if events_of is not None:
        # Only these events (by calendar and address): the reminders plan a changed event anew.
        statement = statement.where(or_(false(), *[and_(Event.calendar_id == calendar_id, Event.href.in_(hrefs))
                                                   for calendar_id, hrefs in events_of.items() if hrefs]))
    rows = db.scalars(statement).all()
    found: list[tuple[dict[str, Any], int, str]] = []
    # The member's addresses for invitations entered from a mail (TIME-26), asked once when one is there.
    mail_base: set[str] | None = None
    for row in rows:
        item = shown[row.calendar_id]
        mine = own[row.calendar_id]
        if invitations.by_mail(row) and item.right == calendars.OWNER:
            if mail_base is None:
                mail_base = invitations.account_addresses(db, account.id)
            mine = invitations.mail_addresses(row, mine | mail_base)
        hide = busy_only(item)
        # A single event straight from its stored columns; the object itself is read only where the columns cannot
        # tell (a series, an invitation, a holiday named in two languages). Reading every event made a month of
        # 20 calendars take a second (TIME-12 EC-9).
        spans: list[tuple[Any, Any, Any, Any]]
        if QUICK and _plain_event(row, mine):
            if row.all_day:
                spans = [(None, row.starts_at.date(), (row.ends_at or row.starts_at).date(), None)]
            else:
                spans = [(None, row.starts_at, row.ends_at or row.starts_at, None)]
        else:
            parsed = ical.parse(row.ical)
            if not row.recurring:
                master = ical.master_of(parsed)
                begin = master.decoded("DTSTART")
                finish = begin + ical.duration_of(master)
                spans = [(None, begin, finish, master)]
            else:
                spans = [(occ.recurrence_id, occ.start, occ.end, occ.component)
                         for occ in ical.occurrences(parsed, start - margin, end + margin)]
        if hide:
            person = people.view(item.calendar.owner_id)
        elif row.calendar_id in shared and row.created_by is not None and row.created_by != account.id:
            person = people.view(row.created_by)
        else:
            person = None
        for recurrence, begin, finish, component in spans:
            # A whole day has no zone: it may touch the window seen from any zone; times count exactly.
            slack = margin if not isinstance(begin, datetime) else timedelta(0)
            if ical._as_utc(begin) >= end + slack or (ical._as_utc(finish) <= start - slack and not (
                    finish == begin and ical._as_utc(begin) >= start)):
                continue
            if component is None:
                free, title, location, invitation = not row.busy, row.title or "", row.location or "", None
                zone = row.time_zone
            else:
                free = str(component.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT"
                title, location = summary_in(component, language), str(component.get("LOCATION", ""))
                invitation = invitations.state_of(component, mine) if not hide else None
                zone = ical.zone_name(begin)
            if hide and free:
                continue
            if invitation is not None and invitation["state"] == "declined" and not include_declined:
                # Declined: gone from the views, still found by the search (TIME-18 AC-7).
                continue
            entry: dict[str, Any] = {
                "id": row.id,
                "occurrence": ical.recurrence_key(recurrence) or None,
                "calendar_id": row.calendar_id,
                "title": "" if hide else title,
                "location": "" if hide else location,
                "busy": not free,
                "busy_only": hide,
                "recurring": row.recurring,
                "writable": item.writable,
                "etag": row.etag,
                "person": person,
                "waiting": row.waiting and not hide,
                "invitation": {"state": invitation["state"], "by": invitation["by"]} if invitation else None,
                # For programs (TIME-26): the event's UID and whether it is an invitation entered from a mail.
                "uid": "" if hide else row.uid,
                "origin": "" if hide else (row.origin or ""),
            }
            if isinstance(begin, datetime):
                entry.update(all_day=False, start=ical._as_utc(begin).isoformat().replace("+00:00", "Z"),
                             end=ical._as_utc(finish).isoformat().replace("+00:00", "Z"), time_zone=zone)
            else:
                last = finish - timedelta(days=1) if finish > begin else begin
                entry.update(all_day=True, start=begin.isoformat(), end=last.isoformat(), time_zone="")
            found.append((entry, item.calendar.owner_id, row.uid))
    found.sort(key=lambda trio: (trio[0]["start"], place[trio[0]["calendar_id"]], trio[0]["title"]))
    if merge is None:
        merge = bool(display.view(account).get("merge_duplicates", True))
    result = _merged(found) if merge else [entry for entry, _owner, _uid in found]
    result.sort(key=lambda entry: (entry["start"], entry["title"]))
    return result


#: Single events come from their stored columns where they can (``_plain_event``); the tests switch it off to compare.
QUICK = True


def _plain_event(row: Event, own_addresses: set[str]) -> bool:
    """A single event whose stored columns say all a view needs: no series, no changed occurrence, no holiday in two
    languages, and no invitation for the member (only looked for where the member has addresses at the source)."""
    if row.recurring:
        return False
    text = row.ical
    if "X-NEXTIME-SUMMARY-" in text or "RECURRENCE-ID" in text:
        return False
    return not (own_addresses and "ATTENDEE" in text.upper())


def _source(entry: dict[str, Any]) -> dict[str, Any]:
    return {"id": entry["id"], "occurrence": entry["occurrence"], "calendar_id": entry["calendar_id"],
            "writable": entry["writable"], "etag": entry["etag"]}


def _merged(found: list[tuple[dict[str, Any], int, str]]) -> list[dict[str, Any]]:
    """The same event in two calendars of the same person once, the first in the order of the list in front
    (TIME-14 AC-3): the same UID (and occurrence), or the same title, upper and lower case and blanks at the edges
    alike, with the same start and end as instants (EC-6, EC-12). Two persons' events never merge (EC-1); "busy"
    blocks only with each other, by the same UID. The same UID at another start or end is two events again (EC-5):
    the times are part of every key (instants in UTC, so the same moment from two zones still counts as one)."""
    kept: list[dict[str, Any]] = []
    known: dict[tuple, dict[str, Any]] = {}
    for entry, owner, uid in found:
        times = (entry["all_day"], entry["start"], entry["end"])
        if entry["busy_only"]:
            # Only by the same UID: a block carries no title to compare, and never merges with a full event.
            keys = [("busy", owner, uid, entry["occurrence"] or "", *times)]
        else:
            keys = [("uid", owner, uid, entry["occurrence"] or "", *times),
                    ("title", owner, entry["title"].strip().casefold(), entry["all_day"], entry["start"],
                     entry["end"])]
        first = next((known[key] for key in keys if key in known), None)
        if first is not None and all(source["calendar_id"] != entry["calendar_id"] for source in first["sources"]):
            first["sources"].append(_source(entry))
            continue
        entry["sources"] = [_source(entry)]
        kept.append(entry)
        for key in keys:
            known.setdefault(key, entry)
    for entry in kept:
        if len(entry.get("sources", ())) < 2:
            entry.pop("sources", None)
    return kept


def _shared_calendars(db: Session, shown: dict[int, calendars.Access]) -> set[int]:
    """The calendars among ``shown`` that more than one person sees: shared with the account, or by it."""
    from ..models import CalendarShare

    own = [calendar_id for calendar_id, item in shown.items() if item.right == calendars.OWNER]
    by_me = set(db.scalars(select(CalendarShare.calendar_id).where(CalendarShare.calendar_id.in_(own)))) \
        if own else set()
    return by_me | {calendar_id for calendar_id, item in shown.items() if item.right != calendars.OWNER}


class _People:
    """Who stands at an event (TIME-14 AC-2): the name and the uploaded picture; a person deleted or blocked keeps
    only the initial (EC-8). Asked once per person and answer."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.known: dict[int, dict[str, Any] | None] = {}

    def view(self, account_id: int) -> dict[str, Any] | None:
        if account_id not in self.known:
            person = self.db.get(Account, account_id)
            if person is None:
                self.known[account_id] = None
            else:
                gone = person.deleted_at is not None or person.blocked_at is not None
                self.known[account_id] = {
                    "id": person.id, "name": person.shown_name, "color": person.color,
                    "avatar": None if gone or not person.avatar_at else person.avatar_at.isoformat()}
        return self.known[account_id]


def _person(db: Session, account_id: int | None) -> dict[str, Any] | None:
    if account_id is None:
        return None
    person = db.get(Account, account_id)
    if person is None:
        return None
    return {"id": person.id, "name": person.shown_name, "color": person.color,
            "avatar": person.avatar_at.isoformat() if person.avatar_at else None}


def _summary(parsed: icalendar.Calendar) -> dict[str, Any]:
    master = ical.master_of(parsed)
    start = master.decoded("DTSTART")
    return {"title": str(master.get("SUMMARY", "")),
            **ui_times(start, start + ical.duration_of(master), ical.zone_name(start))}


def detail(db: Session, account: Account, event_id: int, occurrence: str | None = None) -> dict[str, Any]:
    """The event as the side panel shows it; for an occurrence of a series its own times and fields."""
    row, found = find(db, account, event_id, for_writing=False)
    parsed = ical.parse(row.ical)
    master = ical.master_of(parsed)
    series = master.get("RRULE") is not None or master.get("RDATE") is not None
    component = master
    start = master.decoded("DTSTART")
    end = start + ical.duration_of(master)
    key = None
    if series and occurrence:
        original = _occurrence_start(master, occurrence)
        if not _occurs(parsed, original):
            raise error("occurrence_unknown", "This occurrence is not part of the series.", 422)
        key = ical.recurrence_key(original)
        changed = _override(parsed, original)
        if changed is not None:
            component = changed
            start = changed.decoded("DTSTART")
            end = start + ical.duration_of(changed)
        else:
            start = original
            end = original + ical.duration_of(master)
    hide = busy_only(found)
    if hide and str(component.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
        # "Busy only" leaves free events out altogether (TIME-19 AC-2): not even that there is one.
        raise error("not_found", "Not found.", 404)
    recur = master.get("RRULE")
    rule = rules.read(recur, master.decoded("DTSTART")) if recur is not None and not isinstance(recur, list) else None
    first = master.decoded("DTSTART")
    result: dict[str, Any] = {
        "id": row.id,
        "etag": row.etag,
        "calendar_id": row.calendar_id,
        "occurrence": key,
        "first": key is not None and ical.same_recurrence(_occurrence_start(master, key), first),
        "title": "" if hide else summary_in(component, language_of(account)),
        "notes": "" if hide else str(component.get("DESCRIPTION", "")),
        "location": "" if hide else str(component.get("LOCATION", "")),
        # Where a chosen place lies (TIME-8 AC-2, AC-4): from GEO, or Apple's structured place; never for "busy only".
        "place": None if hide else ical.point_of(component),
        "busy": str(component.get("TRANSP", "OPAQUE")).upper() != "TRANSPARENT",
        **ui_times(start, end, ical.zone_name(start)),
        "recurring": series,
        "rule": rule.as_dict() if rule else None,
        "rrule": recur.to_ical().decode() if recur is not None and not isinstance(recur, list) else "",
        "series_start": ui_times(first, first + ical.duration_of(master), ical.zone_name(first)),
        "right": found.right,
        "writable": found.writable,
        "busy_only": hide,
        "created_by": None if hide else _person(db, row.created_by),
        "updated_by": None if hide else _person(db, row.updated_by),
        # The key of a program the last change came through (TIME-26 AC-10).
        "updated_via": "" if hide else (row.updated_via or ""),
        "updated_at": row.updated_at.isoformat(),
        "shared": found.right != calendars.OWNER or calendars_shared(db, row.calendar_id),
        "conflict": None,
        "waiting": row.waiting and not hide,
        "source": found.kind or None,
        "attendees": [],
        "attendees_count": 0,
        "invitation": None,
    }
    from . import reminders

    # The member's own reminders, with any right, "busy only" too (TIME-7 AC-9, EC-12).
    result["reminders"] = reminders.of_event(db, account, row, found)
    from . import attachments

    # The files at the event (TIME-9): not for "busy only"; those of a source shown, never added to.
    result.update(attachments.detail(db, row, found))
    if not hide:
        from . import invitations

        result.update(invitations.detail(db, found, component, row))
    clash = conflict_of(db, account, row)
    if clash is not None and not hide:
        result["conflict"] = {
            "by": _person(db, clash.by_id),
            "self": clash.by_id == account.id,
            "mine": _summary(ical.parse(clash.mine_ical)),
            "theirs": _summary(parsed),
        }
    return result


def calendars_shared(db: Session, calendar_id: int) -> bool:
    from ..models import CalendarShare

    return db.scalar(select(CalendarShare.account_id).where(CalendarShare.calendar_id == calendar_id).limit(1)) \
        is not None
