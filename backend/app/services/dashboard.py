"""The card on a nexdeck dashboard (TIME-27): today and tomorrow of the key's calendars, as the overview "Heute" has
them in small (TIME-11), in the zone of the key's account.

What the card gets: the running or next event of today (never a whole day, never background), the rest of today, the
events beginning tomorrow, background titles of both days, "nothing more today" with the first one tomorrow. Busy-only
events come as "Belegt" without place, id or link (EC-7). Titles and places are text; nexdeck draws them as such.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from . import apiv1, calendars, events, settings_service

#: Events per list; above it the card says "+n weitere" (EC-9).
MAX_EVENTS = 20
MAX_TEXT = 200
#: The card of more than one calendar: nextime's slate blue.
ACCENT = "#4A6491"
BUSY = {"de": "Belegt", "en": "Busy"}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_TEXT]


def _hex(color: str) -> str:
    from ..routers.dav import hex_of

    return hex_of(color)


def _name(db: Session, caller: apiv1.Caller, reach: dict[int, calendars.Access]) -> tuple[str, str]:
    """The card's name and color: the one calendar's when the key has exactly one, else the key's name (AC-7)."""
    if caller.calendar_ids is not None and len(reach) == 1:
        only = next(iter(reach.values())).calendar
        return only.name, _hex(only.color)
    return caller.key_name, ACCENT


def card(db: Session, caller: apiv1.Caller) -> dict[str, Any]:
    account = caller.account
    zone_name = apiv1.zone_name(account)
    zone = ZoneInfo(zone_name)
    now = apiv1.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    today = now.astimezone(zone).date()
    tomorrow = today + timedelta(days=1)
    start = datetime.combine(today, datetime.min.time(), zone)
    end = datetime.combine(today + timedelta(days=2), datetime.min.time(), zone)
    reach = caller.reach(db)
    language = events.language_of(account)
    name, color = _name(db, caller, reach)
    base = settings_service.public_url(db)
    entries = (events.occurrence_list(db, account, start.astimezone(UTC), end.astimezone(UTC), include_hidden=True,
                                      only=set(reach)) if reach else [])

    def wall(moment: datetime) -> datetime:
        return moment.astimezone(zone)

    def out(entry: dict[str, Any], begin: datetime, finish: datetime) -> dict[str, Any]:
        calendar = reach[entry["calendar_id"]].calendar
        busy_only = bool(entry.get("busy_only"))
        first_day = begin.astimezone(zone).date() if not entry["all_day"] else date.fromisoformat(entry["start"])
        item: dict[str, Any] = {
            "id": None if busy_only else entry["id"],
            "occurrence": None if busy_only else entry.get("occurrence"),
            "title": BUSY[language] if busy_only else _text(entry.get("title")),
            "location": "" if busy_only else _text(entry.get("location")),
            "busy_only": busy_only,
            "all_day": entry["all_day"],
            "start": apiv1.iso(begin),
            "end": apiv1.iso(finish),
            "start_time": None if entry["all_day"] else wall(begin).strftime("%H:%M"),
            "end_time": None if entry["all_day"] else wall(finish).strftime("%H:%M"),
            "calendar_id": entry["calendar_id"],
            "color": _hex(calendar.color),
            "link": "",
        }
        if base and not busy_only:
            query = {"day": max(first_day, today).isoformat(), "event": entry["id"]}
            if entry.get("occurrence"):
                query["occurrence"] = entry["occurrence"]
            item["link"] = f"{base}/day?{urlencode(query)}"
        return item

    background: dict[str, list[str]] = {"today": [], "tomorrow": []}
    timed_today: list[tuple[datetime, datetime, dict[str, Any]]] = []
    long_today: list[tuple[datetime, datetime, dict[str, Any]]] = []
    rest_tomorrow: list[tuple[datetime, datetime, dict[str, Any]]] = []
    for entry in entries:
        begin, finish = apiv1._bounds(entry, zone)
        first = wall(begin).date()
        last = wall(finish - timedelta(microseconds=1)).date()
        if reach[entry["calendar_id"]].calendar.background:
            # A background entry shared "busy only" has no title to show: it is left out (review, 2026-11-23).
            if entry.get("busy_only") or not _text(entry.get("title")):
                continue
            for key, day in (("today", today), ("tomorrow", tomorrow)):
                if first <= day <= last:
                    background[key].append(_text(entry.get("title")))
            continue
        many = entry["all_day"] or (finish - begin) >= timedelta(hours=24)
        if first <= today <= last:
            (long_today if many else timed_today).append((begin, finish, entry))
        elif first == tomorrow:
            rest_tomorrow.append((begin, finish, entry))

    order = sorted(timed_today, key=lambda found: (found[0], _text(found[2].get("title"))))
    coming = [found for found in order if found[1] > now]
    running = [found for found in coming if found[0] <= now]
    chosen = running[0] if running else (coming[0] if coming else None)
    next_item = None
    if chosen is not None:
        begin, finish, entry = chosen
        state = "running" if begin <= now else "coming"
        minutes = max(1, round(((finish if state == "running" else begin) - now).total_seconds() / 60))
        next_item = {**out(entry, begin, finish), "state": state, "minutes": minutes}
    # A long event that runs today stands once, as running, with its day (EC-4).
    lines = []
    for begin, finish, entry in sorted(long_today, key=lambda found: found[0]):
        first = wall(begin).date() if not entry["all_day"] else date.fromisoformat(entry["start"])
        last = wall(finish - timedelta(microseconds=1)).date() if not entry["all_day"] else date.fromisoformat(
            entry["end"])
        if entry["all_day"] and first == last:
            # A whole day of today alone is no running event: its line says "ganztägig" (TIME-11 EC-14).
            lines.append({**out(entry, begin, finish), "state": "all_day"})
            continue
        lines.append({**out(entry, begin, finish), "state": "running", "day": (today - first).days + 1,
                      "days": (last - first).days + 1})
    rest = [out(entry, begin, finish) for begin, finish, entry in coming if (begin, finish, entry) != chosen]
    today_list = lines + rest
    tomorrow_list = [out(entry, begin, finish)
                     for begin, finish, entry in sorted(rest_tomorrow, key=lambda found: (not found[2]["all_day"],
                                                                                          found[0]))]
    # "Heute nichts mehr": no event with a time still to come; whole days of today do not hold it off (decided after
    # the review, 2026-11-23, as Today's "Heute nichts mehr" beside its all-day lines).
    nothing_more = next_item is None and not rest
    first_tomorrow = next((item for item in tomorrow_list if not item["all_day"]), None)
    return {
        "name": name,
        "color": color,
        "language": language,
        "time_zone": zone_name,
        "today": today.isoformat(),
        "tomorrow": tomorrow.isoformat(),
        "as_of": apiv1.iso(now),
        "link": base,
        "next": next_item,
        "today_events": today_list[:MAX_EVENTS],
        "today_more": max(0, len(today_list) - MAX_EVENTS),
        "tomorrow_events": tomorrow_list[:MAX_EVENTS],
        "tomorrow_more": max(0, len(tomorrow_list) - MAX_EVENTS),
        "background": background,
        "nothing_more": nothing_more,
        "next_tomorrow": first_tomorrow if nothing_more else None,
    }
