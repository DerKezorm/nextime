"""How the account sees its times: first day of the week, week numbers, 12 or 24 hours, its time zone (TIME-3);
whether the same event in two of one person's calendars shows once (TIME-14 AC-4); and whether events in the month
stand with a dot or tinted in their calendar's color.

Stored in the account's preferences, so they travel to every device (EC-7). Light or dark is not here: it stays per
device, in the browser (AC-4). The home time zone is the device's zone at the first sign-in (AC-11); the page shows
"Times in New York" only while the zone it shows differs from it (AC-5).
"""

from __future__ import annotations

from datetime import tzinfo
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from sqlalchemy.orm import Session

from ..models import Account

#: 0 Sunday … 6 Saturday, as JavaScript's ``getDay``; Monday from the start (AC-2).
DEFAULT_WEEK_START = 1
CLOCKS = ("auto", "24", "12")
#: How an event stands in the month: with a dot in its calendar's color, or on a ground tinted with it.
MONTH_STYLES = ("dot", "tint")
DEFAULTS: dict[str, Any] = {
    "week_start": DEFAULT_WEEK_START,
    "week_numbers": True,
    "clock": "auto",
    "time_zone": "",
    "home_zone": "",
    #: The same event in two calendars of one person shows once (TIME-14 AC-3, AC-4); on from the start.
    "merge_duplicates": True,
    #: Events in the month with a dot (as from the start) or tinted in their calendar's color (asked for while testing,
    #: 2026-11-24).
    "month_style": "dot",
}


class DisplayError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


@lru_cache(maxsize=1)
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())


def valid_zone(name: str) -> bool:
    """An IANA name the server knows ("Europe/Berlin", "Asia/Kolkata", "UTC")."""
    if not name or len(name) > 64 or name not in _zones():
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def view(account: Account) -> dict[str, Any]:
    stored = account.preferences if isinstance(account.preferences, dict) else {}
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


def _check(changes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if value is None:
            continue
        if key == "week_start":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
                raise DisplayError(key, "The first day of the week is a number from 0 (Sunday) to 6 (Saturday).")
        elif key == "week_numbers":
            if not isinstance(value, bool):
                raise DisplayError(key, "Week numbers are on or off.")
        elif key == "merge_duplicates":
            if not isinstance(value, bool):
                raise DisplayError(key, "Merging duplicates is on or off.")
        elif key == "month_style":
            if value not in MONTH_STYLES:
                raise DisplayError(key, "Events in the month stand with a dot or tinted.")
        elif key == "clock":
            if value not in CLOCKS:
                raise DisplayError(key, "The clock is auto, 24 or 12.")
        elif key in ("time_zone", "home_zone"):
            if not isinstance(value, str) or (value and not valid_zone(value)):
                raise DisplayError(key, "Unknown time zone.")
            if key == "home_zone" and not value:
                continue
        else:
            continue
        clean[key] = value
    return clean


def save(db: Session, account: Account, changes: dict[str, Any]) -> dict[str, Any]:
    clean = _check(changes)
    preferences = dict(account.preferences) if isinstance(account.preferences, dict) else {}
    preferences.update(clean)
    account.preferences = preferences
    db.commit()
    from . import reminders

    # Another zone moves the reminders of whole days (TIME-7 EC-11).
    reminders.touch(account_id=account.id)
    return view(account)


def learn_home_zone(db: Session, account: Account, device_zone: str) -> bool:
    """The first zone a device reports becomes the home zone; later ones change nothing (AC-11)."""
    if not device_zone or view(account)["home_zone"] or not valid_zone(device_zone):
        return False
    preferences = dict(account.preferences) if isinstance(account.preferences, dict) else {}
    preferences["home_zone"] = device_zone
    account.preferences = preferences
    db.commit()
    return True


def zone_of(account: Account) -> tzinfo:
    """The zone the account's times are told in where no device asks (a notification, TIME-21 EC-13; the last day of
    a guest link, TIME-19 EC-19): the zone it chose, else its home zone, else the server's."""
    from ..config import server_zone

    chosen = view(account)
    name = chosen.get("time_zone") or chosen.get("home_zone") or ""
    if name and valid_zone(name):
        return ZoneInfo(name)
    return server_zone().zone


def twelve_hours(account: Account, language: str) -> bool:
    """12 or 24 hours (TIME-3 AC-6): as chosen, and on "auto" as the language has it."""
    chosen = view(account).get("clock", "auto")
    return chosen == "12" or (chosen == "auto" and language.startswith("en"))
