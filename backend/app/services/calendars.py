"""Calendars of an account (TIME-4): who may do what, the list by source, making, changing, hiding, ordering,
deleting, the default for new events, and "Personal" for a new account.

Own calendars and calendars shared with the account (``CalendarShare``, made by TIME-19) are the ones it sees here.
What an account chooses for itself (hidden, position, default) never changes anything for the others (EC-8).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import (
    CALENDAR_COLORS,
    DEFAULT_COLOR,
    RIGHT_EDIT,
    Account,
    Calendar,
    CalendarPrefs,
    CalendarSetting,
    CalendarShare,
    Event,
    Source,
    utcnow,
)
from . import avatars, content, live

OWNER = "owner"
#: The groups of the calendar list in the order of the sidebar (AC-9); the sources after "own" come with later
#: features (iCloud TIME-15, Google TIME-16, subscriptions TIME-17, nextasks TIME-25).
GROUPS = ("own", "icloud", "google", "caldav", "shared", "subscriptions", "nextasks")
#: The group of a calendar from a source, in the account that connected it (each CalDAV account its own, TIME-15 AC-9).
SOURCE_GROUPS = {"icloud": "icloud", "google": "google", "caldav": "caldav", "subscription": "subscriptions",
                 "holidays": "subscriptions"}
NAME_MAX = 60
#: The choices for "Reminder for new events", in minutes; 0 is "None" (AC-7).
REMINDERS = (0, 5, 15, 30, 60, 1440)
#: Up to this many calendars nextime stays smooth for sure (AC-6).
SMOOTH_UP_TO = 20


@dataclass
class Access:
    calendar: Calendar
    #: ``owner``, or the right of a share: ``edit``, ``read``, ``busy``.
    right: str
    #: The kind of its source (``icloud``, ``subscription`` …); "" for nextime's own calendars.
    kind: str = ""

    @property
    def writable(self) -> bool:
        # Read only at the source, or its access expired or resting (TIME-15 AC-6, EC-1, TIME-16 EC-5).
        return self.right in (OWNER, RIGHT_EDIT) and not self.calendar.read_only and not self.calendar.locked

    @property
    def group(self) -> str:
        if self.right != OWNER:
            return "shared"
        return SOURCE_GROUPS.get(self.kind, "own")


def clean_name(raw: str) -> str:
    """Blanks at the edges go; umlauts and emoji stay; empty and longer than 60 are refused (EC-3)."""
    name = (raw or "").strip()
    if not name:
        raise error("name_missing", "Give the calendar a name.", 422, field="name")
    if len(name) > NAME_MAX:
        raise error("name_too_long", f"At most {NAME_MAX} characters.", 422, field="name", max=NAME_MAX)
    return name


#: An own color, as nexnotes' "Eigene Farbe": exactly six hex digits (decided while testing, 2026-11-23).
CUSTOM_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def clean_color(color: str) -> str:
    """One of the eleven colors by name, or an own color as ``#rrggbb`` (kept in small letters); a calendar always
    has a color."""
    if color in CALENDAR_COLORS:
        return color
    if isinstance(color, str) and CUSTOM_COLOR.fullmatch(color):
        return color.lower()
    raise error("invalid_color", "Choose one of the eleven calendar colors or an own color as #rrggbb.", 422,
                field="color")


def clean_icon(name: str | None) -> str:
    """A symbol of the list nexnotes' folders have, or "" for none; anything else is refused."""
    from .symbols import KNOWN

    if not name:
        return ""
    if name not in KNOWN:
        raise error("calendar_icon_unknown", "This symbol is not offered.", 422, field="icon")
    return name


#: The choices of "Neue Termine in diesem Kalender" (TIME-4, decided while testing, 2026-11-23).
DEFAULT_MINUTES = (15, 30, 45, 60, 90, 120, 180)
DEFAULT_RULES = ("", "daily", "weekly", "biweekly", "monthly", "yearly")


def clean_defaults(values: Any) -> dict[str, Any] | None:
    """What a new event of a calendar starts with, for one account: ``all_day``, ``minutes`` (the length of an event
    with a time), ``rule`` (none, daily, weekly, every two weeks, monthly, yearly, without end), ``reminders`` (lead
    with an event) and ``busy``. Keys left out are as from the start; nothing at all is ``None``. The reminders come
    per kind (decided after the review, 2026-11-23): ``reminders`` for events with a time, ``reminders_all_day`` for
    whole days ("am Vortag um 18:00" means nothing at an event with a time). Stored before that, ``reminders`` of a
    calendar whose new events are whole days were those of whole days and stay so."""
    from . import reminders

    if values is None:
        return None
    if not isinstance(values, dict) or set(values) - {"all_day", "minutes", "rule", "reminders", "reminders_all_day",
                                                       "busy"}:
        raise error("invalid_defaults", "These defaults are not valid.", 422, field="defaults")
    out: dict[str, Any] = {}
    for key in ("all_day", "busy"):
        if values.get(key) is not None:
            if not isinstance(values[key], bool):
                raise error("invalid_defaults", "These defaults are not valid.", 422, field="defaults")
            out[key] = values[key]
    if values.get("minutes") is not None:
        if values["minutes"] not in DEFAULT_MINUTES or isinstance(values["minutes"], bool):
            raise error("invalid_defaults", "These defaults are not valid.", 422, field="defaults")
        out["minutes"] = values["minutes"]
    if values.get("rule"):
        if values["rule"] not in DEFAULT_RULES:
            raise error("invalid_defaults", "These defaults are not valid.", 422, field="defaults")
        out["rule"] = values["rule"]
    if values.get("reminders") is not None:
        if not isinstance(values["reminders"], list):
            raise error("invalid_defaults", "These defaults are not valid.", 422, field="defaults")
        legacy = bool(out.get("all_day")) and "reminders_all_day" not in values
        out["reminders"] = reminders.clean(values["reminders"], legacy)
    if values.get("reminders_all_day") is not None:
        if not isinstance(values["reminders_all_day"], list):
            raise error("invalid_defaults", "These defaults are not valid.", 422, field="defaults")
        out["reminders_all_day"] = reminders.clean(values["reminders_all_day"], True)
    return out or None


def clean_reminder(minutes: int) -> int:
    if minutes not in REMINDERS:
        raise error("invalid_reminder", "This reminder is not offered.", 422, field="reminder")
    return minutes


def _live(account_id: int) -> object:
    return select(Account.id).where(Account.id == account_id, Account.blocked_at.is_(None),
                                    Account.deleted_at.is_(None)).exists()


def accesses(db: Session, account_id: int) -> list[Access]:
    """Every calendar the account sees: its own, then those shared with it whose owner is still there. A blocked
    owner's calendars stay as they were (TIME-2 EC-2, TIME-19 EC-17); a deleted owner's go for everybody."""
    own = db.scalars(select(Calendar).where(Calendar.owner_id == account_id, Calendar.deleted_at.is_(None))
                     .order_by(Calendar.id)).all()
    shared = db.execute(
        select(Calendar, CalendarShare.right)
        .join(CalendarShare, CalendarShare.calendar_id == Calendar.id)
        .join(Account, Account.id == Calendar.owner_id)
        .where(CalendarShare.account_id == account_id, Calendar.deleted_at.is_(None), Account.deleted_at.is_(None))
        .order_by(Calendar.id)
    ).all()
    kinds = _kinds(db, {row.source_id for row in own} | {row.source_id for row, _right in shared})
    return [Access(row, OWNER, kinds.get(row.source_id, "")) for row in own] + [
        Access(row, right, kinds.get(row.source_id, "")) for row, right in shared]


def _kinds(db: Session, source_ids: set[int | None]) -> dict[int | None, str]:
    wanted = [source_id for source_id in source_ids if source_id is not None]
    if not wanted:
        return {}
    return dict(db.execute(select(Source.id, Source.kind).where(Source.id.in_(wanted))).all())


def access(db: Session, account_id: int, calendar_id: int) -> Access | None:
    """What the account may do with this calendar; ``None`` when it does not see it (or it is in the trash)."""
    row = db.get(Calendar, calendar_id)
    if row is None or row.deleted_at is not None:
        return None
    kind = _kinds(db, {row.source_id}).get(row.source_id, "")
    if row.owner_id == account_id:
        return Access(row, OWNER, kind)
    share = db.get(CalendarShare, (calendar_id, account_id))
    if share is None:
        return None
    owner = db.get(Account, row.owner_id)
    if owner is None or owner.deleted_at is not None:
        return None
    return Access(row, share.right, kind)


def setting(db: Session, account_id: int, calendar_id: int) -> CalendarSetting:
    row = db.get(CalendarSetting, (account_id, calendar_id))
    if row is None:
        row = CalendarSetting(account_id=account_id, calendar_id=calendar_id, hidden=False,
                              position=_next_position(db, account_id))
        db.add(row)
        db.flush()
    return row


def public_holidays(calendar: Calendar) -> bool:
    """A calendar of the built-in public holidays (TIME-17), ``holidays:<country>:public``; school holidays are not."""
    url = calendar.remote_url or ""
    return url.startswith("holidays:") and url.endswith(":public")


def highlighted(calendar: Calendar, row: CalendarSetting) -> bool:
    """Whether the days of this calendar stand out for the account ("Tage hervorheben"): what it chose, else on for
    the built-in public holidays only."""
    return public_holidays(calendar) if row.highlight is None else row.highlight


def _next_position(db: Session, account_id: int) -> int:
    highest = db.scalar(select(func.max(CalendarSetting.position)).where(CalendarSetting.account_id == account_id))
    return 0 if highest is None else int(highest) + 1


def _settings(db: Session, account_id: int) -> dict[int, CalendarSetting]:
    return {row.calendar_id: row for row in db.scalars(
        select(CalendarSetting).where(CalendarSetting.account_id == account_id))}


def ordered(db: Session, account_id: int) -> list[tuple[Access, CalendarSetting]]:
    """The account's calendars in the order of the list: by group, then by its own order within the group."""
    known = _settings(db, account_id)
    rows = []
    for item in accesses(db, account_id):
        row = known.get(item.calendar.id) or setting(db, account_id, item.calendar.id)
        rows.append((item, row))
    rows.sort(key=lambda pair: (GROUPS.index(pair[0].group), pair[0].calendar.source_id or 0 if pair[0].right == OWNER
                                else 0, pair[1].position, pair[0].calendar.id))
    return rows


# --- "Personal" and the default (AC-10, AC-5, EC-1, EC-9) --------------------------------------------------------


def personal_name(account: Account) -> str:
    language = (account.language or account.browser_language or "").lower()
    return "Persönlich" if language.startswith("de") else "Personal"


def ensure_seeded(db: Session, account: Account) -> None:
    """A new account gets its own calendar "Personal" in slate as the default, once: two sign-ins at the same time
    still make one (the account's row in ``calendar_prefs`` decides who makes it)."""
    prefs = db.get(CalendarPrefs, account.id)
    if prefs is not None and prefs.seeded:
        return
    db.execute(insert(CalendarPrefs).values(account_id=account.id, seeded=False)
               .on_conflict_do_nothing(index_elements=["account_id"]))
    claimed = db.execute(update(CalendarPrefs).where(CalendarPrefs.account_id == account.id,
                                                     CalendarPrefs.seeded.is_(False)).values(seeded=True))
    if not claimed.rowcount:
        db.commit()
        return
    # Calendars mirrored from iCloud, Google or a subscription are no own calendar of nextime's.
    has_own = db.scalar(select(func.count()).select_from(Calendar).where(Calendar.owner_id == account.id,
                                                                        Calendar.source_id.is_(None)))
    if not has_own:
        made = _make(db, account.id, personal_name(account), DEFAULT_COLOR)
        db.execute(update(CalendarPrefs).where(CalendarPrefs.account_id == account.id,
                                               CalendarPrefs.default_calendar_id.is_(None))
                   .values(default_calendar_id=made.id))
    db.commit()


def _prefs(db: Session, account_id: int) -> CalendarPrefs:
    row = db.get(CalendarPrefs, account_id)
    if row is None:
        db.execute(insert(CalendarPrefs).values(account_id=account_id, seeded=False)
                   .on_conflict_do_nothing(index_elements=["account_id"]))
        row = db.get(CalendarPrefs, account_id)
        assert row is not None
    return row


@dataclass
class Default:
    calendar: Calendar | None
    #: Why the chosen default is not the one set: ``deleted``, ``hidden``, ``read_only``; "" when it is.
    moved: str = ""
    #: The name of the default that was set, when it moved and is still known.
    was: str = ""


def default_of(db: Session, account_id: int) -> Default:
    """The calendar new events land in: the one set, while it is writable, there and shown; otherwise the next
    writable one in the order of the list, and why (EC-1)."""
    prefs = db.get(CalendarPrefs, account_id)
    rows = ordered(db, account_id)
    chosen_id = prefs.default_calendar_id if prefs is not None else None
    moved, was = "", ""
    if chosen_id is not None:
        for item, row in rows:
            if item.calendar.id == chosen_id:
                if not item.writable:
                    moved = "read_only"
                elif row.hidden:
                    moved = "hidden"
                else:
                    return Default(item.calendar)
                was = item.calendar.name
                break
        else:
            moved = "deleted"
            gone = db.get(Calendar, chosen_id)
            was = gone.name if gone is not None and gone.owner_id == account_id else ""
    for item, row in rows:
        if item.writable and not row.hidden:
            return Default(item.calendar, moved, was)
    for item, _row in rows:
        if item.writable:
            return Default(item.calendar, moved, was)
    return Default(None, moved, was)


def set_default(db: Session, account_id: int, calendar_id: int) -> None:
    found = access(db, account_id, calendar_id)
    if found is None:
        raise error("not_found", "Not found.", 404)
    if not found.writable:
        raise error("calendar_read_only", "A calendar you may only read cannot be the default.", 409)
    _prefs(db, account_id).default_calendar_id = calendar_id


# --- Making, changing, deleting ----------------------------------------------------------------------------------


def _make(db: Session, owner_id: int, name: str, color: str, *, background: bool = False, reminder: int = 0,
          client_ref: str | None = None, dav_name: str | None = None, icon: str = "") -> Calendar:
    row = Calendar(owner_id=owner_id, dav_name=dav_name or uuid.uuid4().hex, name=name, color=color,
                   background=background, reminder=reminder, client_ref=client_ref, icon=icon)
    db.add(row)
    db.flush()
    db.add(CalendarSetting(account_id=owner_id, calendar_id=row.id, hidden=False,
                           position=_next_position(db, owner_id)))
    db.flush()
    live.touch_accounts(db, {owner_id})
    return row


def create(db: Session, account: Account, *, name: str, color: str, background: bool, reminder: int,
           make_default: bool, client_ref: str | None, dav_name: str | None = None,
           icon: str = "") -> tuple[Calendar, bool]:
    """A new own calendar, at once (AC-1). The same ``client_ref`` again (a second click, a retry after the
    connection broke) gives back the calendar made the first time (EC-6). True when it is new."""
    name, color, reminder = clean_name(name), clean_color(color), clean_reminder(reminder)
    icon = clean_icon(icon)
    ensure_seeded(db, account)
    if client_ref:
        known = db.scalar(select(Calendar).where(Calendar.owner_id == account.id, Calendar.client_ref == client_ref))
        if known is not None:
            return known, False
    try:
        row = _make(db, account.id, name, color, background=background, reminder=reminder, client_ref=client_ref,
                    dav_name=dav_name, icon=icon)
        if make_default:
            _prefs(db, account.id).default_calendar_id = row.id
        db.commit()
    except IntegrityError:
        db.rollback()
        known = db.scalar(select(Calendar).where(Calendar.owner_id == account.id, Calendar.client_ref == client_ref))
        if known is None:
            raise
        return known, False
    return row, True


def change(db: Session, account: Account, calendar_id: int, values: dict) -> Access:
    """Name, color, background and reminder change for everybody (the owner only); hidden, default, the defaults for
    new events and "Tage hervorheben" only for the account itself, with any right (EC-8). The later change wins
    (EC-11)."""
    ensure_seeded(db, account)
    found = access(db, account.id, calendar_id)
    if found is None:
        raise error("not_found", "Not found.", 404)
    calendar = found.calendar
    own_fields = {"name", "color", "background", "reminder", "icon"} & set(values)
    if own_fields and found.right != OWNER:
        raise error("calendar_not_own", "Only the owner changes this calendar.", 403)
    if "name" in values:
        calendar.name = clean_name(values["name"])
    if "color" in values:
        calendar.color = clean_color(values["color"])
    if "background" in values:
        calendar.background = bool(values["background"])
    if "icon" in values:
        # Only here: a calendar of iCloud, Google or another CalDAV server keeps its symbol in nextime, nothing
        # goes to the source; nor does it change what a device of the calendar account compares (``props_rev``).
        calendar.icon = clean_icon(values["icon"])
    if "reminder" in values:
        calendar.reminder = clean_reminder(int(values["reminder"]))
        from . import reminders

        reminders.owner_default_changed(db, calendar_id)
    if own_fields - {"icon"}:
        calendar.props_rev += 1
    if own_fields:
        calendar.updated_at = utcnow()
        live.touch_calendar(db, calendar_id)
    live.touch_accounts(db, {account.id})
    if "hidden" in values:
        setting(db, account.id, calendar_id).hidden = bool(values["hidden"])
    if "defaults" in values:
        # Each account its own, with any right, a source's calendar too: nothing goes to the source.
        setting(db, account.id, calendar_id).new_defaults = clean_defaults(values["defaults"])
    if "holiday" in values:
        # "Highlight its days": each account its own, with any right; ``None`` goes back to as from the start.
        chosen = values["holiday"]
        setting(db, account.id, calendar_id).highlight = None if chosen is None else bool(chosen)
    if values.get("default"):
        set_default(db, account.id, calendar_id)
    db.commit()
    return found


def reorder(db: Session, account: Account, ids: list[int]) -> None:
    """The account's own order within one group; the groups themselves stay where they are (AC-5)."""
    seen = {item.calendar.id: item for item in accesses(db, account.id)}
    if not ids or len(set(ids)) != len(ids) or any(calendar_id not in seen for calendar_id in ids):
        raise error("invalid_order", "List the calendars of one group, each once.", 422)
    groups = {seen[calendar_id].group for calendar_id in ids}
    if len(groups) != 1:
        raise error("invalid_order", "List the calendars of one group, each once.", 422)
    positions = sorted(setting(db, account.id, calendar_id).position for calendar_id in ids)
    for position, calendar_id in zip(positions, ids, strict=True):
        setting(db, account.id, calendar_id).position = position
    # Equal positions (calendars made in the same instant) would keep their old order: spread them.
    if len(set(positions)) != len(positions):
        base = positions[0]
        for offset, calendar_id in enumerate(ids):
            setting(db, account.id, calendar_id).position = base + offset
    live.touch_accounts(db, {account.id})
    db.commit()


def delete(db: Session, account: Account, calendar_id: int) -> int:
    """Into the trash with its events (AC-4); everybody, every device stops seeing it (EC-10). Only the owner."""
    found = access(db, account.id, calendar_id)
    if found is None:
        raise error("not_found", "Not found.", 404)
    if found.right != OWNER:
        raise error("calendar_not_own", "Only the owner deletes this calendar.", 403)
    if found.calendar.source_id is not None:
        # It lives at its source: "Disconnect" or "Remove" under "Calendars and accounts" (TIME-15 AC-5).
        raise error("calendar_from_source", "Remove it under Calendars and accounts.", 409)
    from . import trash

    found.calendar.deleted_at = trash.clock()
    found.calendar.deleted_by = account.id
    # The deadline of now and the name of now (TIME-23 EC-12, EC-13).
    found.calendar.trash_until = trash.until(db, found.calendar.deleted_at)
    found.calendar.deleted_by_name = account.shown_name
    from . import shares

    # Gone for everybody it is shared with, each with a notice; its guest link stops (TIME-19 EC-18).
    shares.calendar_going(db, found.calendar)
    live.touch_calendar(db, calendar_id)
    events = count_events(db, calendar_id)
    db.commit()
    return events


def count_events(db: Session, calendar_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(Event).where(
        Event.calendar_id == calendar_id, Event.deleted_at.is_(None))) or 0)


# --- What the accounts side asks (services/content.py) ----------------------------------------------------------


def _counts(db: Session, account_id: int) -> content.Counts:
    own = select(Calendar.id).where(Calendar.owner_id == account_id, Calendar.deleted_at.is_(None))
    calendars = int(db.scalar(select(func.count()).select_from(own.subquery())) or 0)
    events = int(db.scalar(select(func.count()).select_from(Event).where(
        Event.calendar_id.in_(own), Event.deleted_at.is_(None))) or 0)
    size = int(db.scalar(select(func.coalesce(func.sum(Event.size), 0)).where(Event.calendar_id.in_(own))) or 0)
    shared = int(db.scalar(select(func.count(func.distinct(CalendarShare.calendar_id))).where(
        CalendarShare.calendar_id.in_(own))) or 0)
    return content.Counts(calendars=calendars, events=events, shared=shared, bytes=size)


content.register_counter(_counts)


# --- Who sees whose profile picture (TIME-14 AC-2) ---------------------------------------------------------------


def _visible_ids(account_id: int) -> object:
    own = select(Calendar.id).where(Calendar.owner_id == account_id, Calendar.deleted_at.is_(None))
    # A calendar in the trash joins nobody any more.
    shared = select(CalendarShare.calendar_id).join(Calendar, Calendar.id == CalendarShare.calendar_id).where(
        CalendarShare.account_id == account_id, Calendar.deleted_at.is_(None))
    return own.union(shared)


def _share_a_calendar(db: Session, viewer: Account, owner_id: int) -> bool:
    """Two people who see the same calendar see each other's picture at its events (TIME-2 AC-5, TIME-14 AC-2)."""
    mine = set(db.scalars(_visible_ids(viewer.id)))  # type: ignore[arg-type]
    if not mine:
        return False
    theirs = set(db.scalars(_visible_ids(owner_id)))  # type: ignore[arg-type]
    return bool(mine & theirs)


avatars.SEE_RULES.append(_share_a_calendar)
