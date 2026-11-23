"""What a key may see and do under ``/api/v1/`` and ``/api/mcp`` (TIME-26): the one place both ask.

A key acts as its account and never sees more (AC-2): the calendars the account sees right now (its own and those
shared with it, by the same rules as the interface, ``calendars.ordered``), and of those only the ones the key was made
for (EC-4). A calendar outside answers like one that is not there (``not_found``); a calendar made later belongs only to
keys with "all". Calendars the member hid in the interface stay in reach: hiding is a view of the interface, the key's
choice says what a program reads.

"Busy only" stays busy (EC-3): an occurrence there gives its times and nothing else, free events of such calendars do
not appear at all, and asking for one by its number answers with its times only.

Writing goes through the services of the interface (``services/events.py``): the limits of TIME-5 (EC-6), the right
to the calendar (EC-5), changes to a calendar of iCloud, Google or another CalDAV server go on to the source and wait
while it is away (AC-6), deleting goes into the trash (EC-10). Each change is marked as made through the key
(``events.acting_via``, AC-10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import error
from ..models import RIGHT_BUSY, Account, ApiKey, Event, Source, utcnow
from . import calendars, display, events, ical, invitations, symbols

LEVELS = ("read", "write")
#: What ``/me`` names: what this nextime offers under ``/api/v1/`` (AC-9). It only grows.
FEATURES = ["calendars", "events", "write", "search", "free", "dashboard", "client_ref", "series",
            # For nexmail (2026-11-23): calendar groups, events by UID, answers, invitations from mails, a version.
            "calendar_groups", "uid_lookup", "answer", "ical_import", "state"]
#: How the sidebar names a source's group (``source_name``); a CalDAV account and a subscription by their label.
SOURCE_NAMES = {"icloud": "iCloud", "google": "Google"}
#: The longest window one request reads, as the interface (TIME-12).
MAX_DAYS = events.MAX_RANGE_DAYS


@dataclass
class Caller:
    """The account behind a key, detached from the database, and what the key may."""

    account: Account
    key_id: int
    key_name: str
    level: str
    mcp: bool = False
    #: The calendars the key was made for; ``None`` for all.
    calendar_ids: list[int] | None = None
    expires_at: datetime | None = None
    _scope: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def writes(self) -> bool:
        return self.level == "write"

    def may(self, level: str) -> bool:
        return LEVELS.index(self.level) >= LEVELS.index(level)

    def reach(self, db: Session) -> dict[int, calendars.Access]:
        """The calendars in reach, in the order of the member's list: what the account sees, cut to the key's."""
        if "reach" not in self._scope:
            # "Persönlich" exists before anything asks for it (TIME-4 AC-10), as at the interface's first look.
            account = db.get(Account, self.account.id)
            if account is not None:
                calendars.ensure_seeded(db, account)
                db.commit()
            chosen = None if self.calendar_ids is None else set(self.calendar_ids)
            # A key for "all" reads what the member shows: a hidden calendar only when the key names it (decided
            # after the review, 2026-11-23); for API, MCP, the card and the search alike.
            listed = calendars.ordered(db, self.account.id)
            self._scope["reach"] = {item.calendar.id: item for item, setting in listed
                                    if (item.calendar.id in chosen if chosen is not None else not setting.hidden)}
            self._scope["hidden"] = {item.calendar.id for item, setting in listed if setting.hidden}
        return self._scope["reach"]

    def hidden(self, db: Session) -> set[int]:
        """The calendars the member hid in nextime (only those the key names reach it)."""
        self.reach(db)
        return self._scope["hidden"]


def caller_of(account: Account, row: ApiKey) -> Caller:
    chosen = [int(found) for found in row.calendar_ids] if row.calendar_ids is not None else None
    return Caller(account=account, key_id=row.id, key_name=row.name,
                  level=row.level if row.level in LEVELS else "read", mcp=bool(row.allow_mcp), calendar_ids=chosen,
                  expires_at=row.expires_at)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat().replace("+00:00", "Z")


def zone_name(account: Account) -> str:
    """The zone the account's "today" and wall clocks are in (TIME-27 EC-11)."""
    found = display.zone_of(account)
    return str(getattr(found, "key", "") or "UTC")


# --- Calendars ------------------------------------------------------------------------------------------------------


def source_name(db: Session, item: calendars.Access) -> str:
    """The name of the calendar's source as the sidebar heads its group: "iCloud", "Google", a CalDAV account or a
    subscription by the name of its connection; "" for nextime's own calendars and those shared with the member."""
    group = item.group
    if group in ("own", "shared"):
        return ""
    if item.kind in SOURCE_NAMES:
        return SOURCE_NAMES[item.kind]
    source = db.get(Source, item.calendar.source_id) if item.calendar.source_id is not None else None
    return source.label if source is not None and source.label else ("CalDAV" if group == "caldav" else "")


def calendar_out(db: Session, item: calendars.Access, default_id: int | None, hidden: bool = False) -> dict[str, Any]:
    calendar = item.calendar
    busy = item.right == RIGHT_BUSY
    owner = None
    if item.right != calendars.OWNER:
        person = db.get(Account, calendar.owner_id)
        owner = person.shown_name if person else ""
    return {
        "id": calendar.id,
        "name": calendar.name,
        "color": calendar.color,
        "icon": symbols.shown(calendar.icon),
        "background": bool(calendar.background),
        "right": item.right,
        "writable": item.writable,
        "busy_only": busy,
        "source": item.kind or "nextime",
        "owner": owner,
        "default": calendar.id == default_id,
        # Where it stands in the sidebar (``own``, ``icloud``, ``google``, ``caldav``, ``shared``, ``subscriptions``),
        # the name of that source, and whether the member hid it in nextime (TIME-26, for nexmail).
        "group": item.group,
        "source_name": source_name(db, item),
        "hidden": hidden,
    }


def calendar_list(db: Session, caller: Caller) -> list[dict[str, Any]]:
    default = calendars.default_of(db, caller.account.id).calendar
    default_id = default.id if default is not None else None
    hidden = caller.hidden(db)
    return [calendar_out(db, item, default_id, item.calendar.id in hidden) for item in caller.reach(db).values()]


def one_calendar(db: Session, caller: Caller, calendar_id: int) -> calendars.Access:
    found = caller.reach(db).get(calendar_id)
    if found is None:
        raise error("not_found", "Not found.", 404)
    return found


def checked_calendars(db: Session, caller: Caller, wanted: set[int] | None) -> set[int]:
    """The calendars a question is about: those asked for (each must be in reach, EC-4), or all in reach."""
    reach = caller.reach(db)
    if wanted is None:
        return set(reach)
    if any(found not in reach for found in wanted):
        raise error("not_found", "Not found.", 404)
    return wanted


def parse_ids(raw: str | None, name: str = "calendars") -> set[int] | None:
    if raw is None or not raw.strip():
        return None
    try:
        return {int(part) for part in raw.split(",") if part.strip()}
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=[name]) from None


def moment(text: str, name: str, zone: str = "UTC") -> datetime:
    """An ISO moment with its zone, or a day (``YYYY-MM-DD``: its midnight in ``zone``, the account's)."""
    try:
        if len(text) == 10:
            return ical.at_wall(datetime.combine(date.fromisoformat(text), datetime.min.time()), zone)
        value = datetime.fromisoformat(text)
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=[name]) from None
    if value.tzinfo is None:
        raise error("invalid_input", "The input is not valid.", 422, fields=[name])
    return value


# --- Events ---------------------------------------------------------------------------------------------------------


def web_url(base: str, event_id: int, occurrence: str | None, start: str, all_day: bool, zone: str) -> str | None:
    """Where the event opens in nextime: its day (in the account's zone) with the event, as notifications link it."""
    if not base:
        return None
    if all_day:
        day = start[:10]
    else:
        day = ical.local_wall(datetime.fromisoformat(start), zone).date().isoformat()
    url = f"{base}/day?day={day}&event={event_id}"
    return url + (f"&occurrence={occurrence}" if occurrence else "")


def occurrence_out(entry: dict[str, Any], reach: dict[int, calendars.Access], base: str = "",
                   zone: str = "UTC") -> dict[str, Any]:
    """An occurrence as programs read it. "Busy only": its times, nothing else (EC-3)."""
    if entry.get("busy_only"):
        return {"calendar_id": entry["calendar_id"], "busy_only": True, "busy": True, "all_day": entry["all_day"],
                "start": entry["start"], "end": entry["end"], "time_zone": entry.get("time_zone", "")}
    person = entry.get("person")
    out = {
        "id": entry["id"],
        "occurrence": entry["occurrence"],
        "calendar_id": entry["calendar_id"],
        "title": entry["title"],
        "location": entry["location"],
        "busy": entry["busy"],
        "busy_only": False,
        "all_day": entry["all_day"],
        "start": entry["start"],
        "end": entry["end"],
        "time_zone": entry.get("time_zone", ""),
        "recurring": entry["recurring"],
        "writable": entry["writable"],
        "etag": entry["etag"],
        "waiting": entry["waiting"],
        "background": bool(reach[entry["calendar_id"]].calendar.background),
        "person": person["name"] if person else None,
        "invitation": entry["invitation"]["state"] if entry.get("invitation") else None,
        "uid": entry.get("uid", ""),
        "origin": entry.get("origin") or None,
        "web_url": web_url(base, entry["id"], entry["occurrence"], entry["start"], entry["all_day"], zone),
    }
    if entry.get("sources"):
        out["also_in"] = [source["calendar_id"] for source in entry["sources"]
                          if source["calendar_id"] != entry["calendar_id"]
                          and source["calendar_id"] in reach and reach[source["calendar_id"]].right != RIGHT_BUSY]
    return out


def occurrences(db: Session, caller: Caller, start: datetime, end: datetime,
                wanted: set[int] | None = None, *, base: str = "", uid: str | None = None) -> list[dict[str, Any]]:
    """Every occurrence in [start, end) of the calendars in reach, series rolled out (AC-2); with ``uid`` only that
    event's, declined ones too."""
    if end <= start or (end - start).days > MAX_DAYS:
        raise error("invalid_range", f"Ask for at most {MAX_DAYS} days at once.", 422)
    only = checked_calendars(db, caller, wanted)
    if uid is not None:
        ids = _ids_of_uid(db, uid, only)
        only = {calendar_id for calendar_id, _id in ids}
    if not only:
        return []
    reach = caller.reach(db)
    zone = zone_name(caller.account)
    found = events.occurrence_list(db, caller.account, start, end, include_hidden=True, only=only,
                                   include_declined=uid is not None)
    if uid is not None:
        found = [entry for entry in found if not entry.get("busy_only") and entry.get("uid") == uid]
    return [occurrence_out(entry, reach, base, zone) for entry in found]


def _ids_of_uid(db: Session, uid: str, only: set[int]) -> list[tuple[int, int]]:
    if not uid or not only:
        return []
    return [(calendar_id, event_id) for calendar_id, event_id in db.execute(
        select(Event.calendar_id, Event.id).where(Event.uid == uid, Event.deleted_at.is_(None),
                                                  Event.calendar_id.in_(only)).order_by(Event.id)).all()]


def by_uid(db: Session, caller: Caller, uid: str, wanted: set[int] | None = None, *,
           base: str = "") -> list[dict[str, Any]]:
    """Each event with this UID in the key's calendars, once, as it begins (a series by its first occurrence), with
    the member's invitation state, declined or not; "busy only" calendars keep it to themselves."""
    reach = caller.reach(db)
    only = {calendar_id for calendar_id in checked_calendars(db, caller, wanted)
            if reach[calendar_id].right != RIGHT_BUSY}
    zone = zone_name(caller.account)
    out = []
    for _calendar_id, event_id in _ids_of_uid(db, uid, only):
        row = db.get(Event, event_id)
        if row is None:
            continue
        out.append(_first_entry(db, caller, row, reach[row.calendar_id], base, zone))
    return out


def _first_entry(db: Session, caller: Caller, row: Event, item: calendars.Access, base: str,
                 zone: str) -> dict[str, Any]:
    parsed = ical.parse(row.ical)
    master = ical.master_of(parsed)
    begin = master.decoded("DTSTART")
    finish = begin + ical.duration_of(master)
    mine, _source = invitations.context_of(db, item, row)
    invitation = invitations.state_of(master, mine)
    person = None
    if item.right != calendars.OWNER and row.created_by is not None and row.created_by != caller.account.id:
        author = db.get(Account, row.created_by)
        person = {"name": author.shown_name} if author is not None else None
    entry: dict[str, Any] = {
        "id": row.id, "occurrence": None, "calendar_id": row.calendar_id,
        "title": events.summary_in(master, events.language_of(caller.account)),
        "location": str(master.get("LOCATION", "")),
        "busy": str(master.get("TRANSP", "OPAQUE")).upper() != "TRANSPARENT", "busy_only": False,
        "recurring": row.recurring, "writable": item.writable, "etag": row.etag, "person": person,
        "waiting": row.waiting, "invitation": invitation, "uid": row.uid, "origin": row.origin or "",
    }
    if isinstance(begin, datetime):
        entry.update(all_day=False, start=iso(ical._as_utc(begin)), end=iso(ical._as_utc(finish)),
                     time_zone=ical.zone_name(begin))
    else:
        last = finish - timedelta(days=1) if finish > begin else begin
        entry.update(all_day=True, start=begin.isoformat(), end=last.isoformat(), time_zone="")
    return occurrence_out(entry, {row.calendar_id: item}, base, zone)


def _people_of(row: Event, occurrence: str | None) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    """The organizer and the attendees of the event (or of its occurrence), each with name and address; attendees
    with their state in the words of ``invitation``: ``open``, ``accepted``, ``tentative``, ``declined``."""
    parsed = ical.parse(row.ical)
    component = ical.master_of(parsed)
    if occurrence and row.recurring:
        try:
            changed = events._override(parsed, events._occurrence_start(component, occurrence))
        except Exception:  # noqa: BLE001 - an occurrence the detail already accepted; its master otherwise
            changed = None
        component = changed if changed is not None else component

    def name_of(value: Any) -> str:
        return str((getattr(value, "params", {}) or {}).get("CN", "") or "").strip().strip('"')

    found = component.get("ORGANIZER")
    organizer = {"name": name_of(found), "email": invitations.address(found)} if found is not None else None
    attendees = [{"name": name_of(item), "email": invitations.address(item),
                  "status": invitations.STATES.get(str(item.params.get("PARTSTAT", "NEEDS-ACTION")).upper(), "open")}
                 for item in invitations._attendees(component)]
    return organizer, attendees


def event_out(db: Session, caller: Caller, event_id: int, occurrence: str | None = None,
              base: str = "") -> dict[str, Any]:
    """One event or one occurrence. Outside the key's calendars it is not there (EC-4); "busy only" gives its times
    (EC-3)."""
    row = _event_row(db, caller, event_id)
    found = events.detail(db, caller.account, row.id, occurrence)
    times = {"all_day": found["all_day"], "start": found["start"], "end": found["end"],
             "time_zone": found["time_zone"]}
    if found["busy_only"]:
        return {"calendar_id": found["calendar_id"], "busy_only": True, "busy": True, **times}
    rule = found["rule"]
    return {
        "id": found["id"],
        "etag": found["etag"],
        "calendar_id": found["calendar_id"],
        "occurrence": found["occurrence"],
        "title": found["title"],
        "notes": found["notes"],
        "location": found["location"],
        "busy": found["busy"],
        "busy_only": False,
        **times,
        "recurring": found["recurring"],
        "rule": rule,
        "series_start": found["series_start"] if found["recurring"] else None,
        "writable": found["writable"],
        "waiting": found["waiting"],
        "source": found["source"] or "nextime",
        "created_by": (found["created_by"] or {}).get("name"),
        "updated_by": (found["updated_by"] or {}).get("name"),
        "updated_via": found.get("updated_via") or None,
        "updated_at": found["updated_at"],
        "invitation": (found.get("invitation") or {}).get("state") if found.get("invitation") else None,
        **_extra(caller, row, found, base),
    }


def _extra(caller: Caller, row: Event, found: dict[str, Any], base: str) -> dict[str, Any]:
    """What nexmail reads besides (TIME-26): the UID, where it opens in nextime, organizer and attendees, and
    whether it is an invitation entered from a mail (``origin`` ``imip``)."""
    zone = zone_name(caller.account)
    if found["all_day"]:
        start = found["start"]
    else:
        start = iso(ical.at_wall(datetime.fromisoformat(found["start"]), found["time_zone"] or zone)) or ""
    organizer, attendees = _people_of(row, found["occurrence"])
    return {"uid": row.uid, "origin": row.origin or None,
            "web_url": web_url(base, row.id, found["occurrence"], start, found["all_day"], zone),
            "organizer": organizer, "attendees": attendees}


def _event_row(db: Session, caller: Caller, event_id: int) -> Any:
    row = db.get(Event, event_id) if 0 < event_id < 2**62 else None
    if row is None or row.calendar_id not in caller.reach(db):
        raise error("not_found", "Not found.", 404)
    return row


def check_event(db: Session, caller: Caller, event_id: int) -> None:
    """The event lies in a calendar of the key (EC-4); anything else is not there."""
    _event_row(db, caller, event_id)


def target_calendar(db: Session, caller: Caller, calendar_id: int | None) -> int:
    """Where a new event goes: the one named (in reach, EC-4), else the member's default when the key reaches it,
    else the first calendar in reach the account may write."""
    reach = caller.reach(db)
    if calendar_id is not None:
        if calendar_id not in reach:
            raise error("not_found", "Not found.", 404)
        return calendar_id
    default = calendars.default_of(db, caller.account.id).calendar
    if default is not None and default.id in reach and reach[default.id].writable:
        return default.id
    for found in reach.values():
        if found.writable:
            return found.calendar.id
    raise error("no_writable_calendar", "There is no calendar to write into.", 409)


# --- Free times (EC-12) ---------------------------------------------------------------------------------------------


def _bounds(entry: dict[str, Any], zone: ZoneInfo) -> tuple[datetime, datetime]:
    if entry["all_day"]:
        first = date.fromisoformat(entry["start"])
        last = date.fromisoformat(entry["end"])
        return (datetime.combine(first, datetime.min.time(), zone).astimezone(UTC),
                datetime.combine(last + timedelta(days=1), datetime.min.time(), zone).astimezone(UTC))
    return (datetime.fromisoformat(entry["start"]),
            datetime.fromisoformat(entry["end"]))


def free_times(db: Session, caller: Caller, start: datetime, end: datetime, *, minutes: int = 30,
               day_start: str | None = None, day_end: str | None = None,
               wanted: set[int] | None = None) -> dict[str, Any]:
    """The gaps of at least ``minutes`` between the events marked busy (TIME-5 frei/belegt) of the key's calendars in
    [start, end); "busy only" calendars count with their blocks. ``day_start``/``day_end`` (``HH:MM`` in the account's
    zone) keep each day's gaps within those hours. Free events count as free (EC-12)."""
    name = zone_name(caller.account)
    zone = ZoneInfo(name)
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    busy = sorted(_bounds(entry, zone) for entry in occurrences(db, caller, start, end, wanted) if entry["busy"])
    merged: list[list[datetime]] = []
    for begin, finish in busy:
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], finish)
        else:
            merged.append([begin, finish])
    windows: list[tuple[datetime, datetime]] = []
    if day_start or day_end:
        open_at = _clock(day_start or "00:00", "day_start")
        close_at = _clock(day_end or "24:00", "day_end")
        if close_at <= open_at:
            raise error("invalid_input", "The input is not valid.", 422, fields=["day_end"])
        day = start.astimezone(zone).date()
        last_day = (end - timedelta(microseconds=1)).astimezone(zone).date()
        while day <= last_day:
            base = datetime.combine(day, datetime.min.time())
            low = ical.at_wall(base + open_at, name)
            high = ical.at_wall(base + min(close_at, timedelta(days=1)), name)
            windows.append((max(low.astimezone(UTC), start), min(high.astimezone(UTC), end)))
            day += timedelta(days=1)
    else:
        windows.append((start, end))
    gaps: list[dict[str, Any]] = []
    least = timedelta(minutes=minutes)
    for low, high in windows:
        cursor = low
        for begin, finish in merged:
            if finish <= cursor or begin >= high:
                continue
            if begin - cursor >= least:
                gaps.append(_gap(cursor, min(begin, high), name))
            cursor = max(cursor, finish)
            if cursor >= high:
                break
        if high - cursor >= least:
            gaps.append(_gap(cursor, high, name))
    return {"time_zone": name, "minutes": minutes, "free": gaps}


def _clock(text: str, name: str) -> timedelta:
    try:
        hours, mins = (int(part) for part in text.split(":"))
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=[name]) from None
    if not (0 <= hours <= 24 and 0 <= mins < 60) or (hours == 24 and mins):
        raise error("invalid_input", "The input is not valid.", 422, fields=[name])
    return timedelta(hours=hours, minutes=mins)


def _gap(begin: datetime, finish: datetime, name: str) -> dict[str, Any]:
    return {"start": iso(begin), "end": iso(finish),
            "local_start": ical.local_wall(begin, name).strftime("%Y-%m-%dT%H:%M"),
            "local_end": ical.local_wall(finish, name).strftime("%Y-%m-%dT%H:%M"),
            "minutes": int((finish - begin).total_seconds() // 60)}


def now() -> datetime:
    return utcnow()
