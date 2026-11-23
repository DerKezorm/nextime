"""The search (TIME-22): events by title, place and notes, past and future, without a limit in time (AC-7).

**Where.** The calendars the account shows (no hidden ones, none in the trash, EC-6), only those of the collection
chosen on the device when one is (``only``, AC-1), and never a calendar shared "busy only" (AC-4): its titles, places
and notes are neither searched nor shown.

**How.** ``events.search_text`` holds the folded texts of every part of an event (``ical.search_text``, written with
every version in ``events.write``); each word of the question must be in it, upper and lower case alike (EC-2),
literally, with ``instr`` (``C++``, emoji, quotes, EC-4). A row of before without that column is folded here.

**What comes back.** A single event at its time; a series once (EC-1), with its next occurrence from now, or its last
one once it ended. "Now" is the device's (TIME-11 EC-11); an event running now counts as coming (EC-10). The same
event in two calendars of one person once (TIME-14 AC-3, EC-7). A declined invitation stays findable, marked (EC-15).
First "Coming" (the next on top), then "Past" (the latest on top), a page at a time; the count is always all of them
(EC-11). Sources that do not answer right now are named with their state (EC-8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ..errors import error
from ..models import RIGHT_BUSY, Account, Event, Source
from . import calendars, display, events, ical, invitations

#: Hits per page.
PAGE = 100
PAGE_MAX = 500
#: The most words a question takes, and the longest word.
WORDS_MAX = 20
WORD_MAX = 200
#: How far one look ahead for the next occurrence of a series reaches, and how many looks at most (30 years).
_STEP = timedelta(days=400)
_STEPS = 28


def words_of(query: str) -> list[str]:
    """The words of a question, folded; blanks at the edges and between are no part of it (EC-3)."""
    found = [ical.fold_text(word) for word in query.split()]
    if len(found) > WORDS_MAX:
        raise error("search_too_many_words", f"At most {WORDS_MAX} words.", 422, max=WORDS_MAX)
    if any(len(word) > WORD_MAX for word in found):
        raise error("search_word_too_long", f"A word has at most {WORD_MAX} characters.", 422, max=WORD_MAX)
    return found


def _span(begin: date | datetime, finish: date | datetime) -> tuple[bool, Any, Any]:
    """An occurrence as (all day, start, end): instants in UTC, or dates with the end exclusive."""
    if isinstance(begin, datetime):
        return False, ical._as_utc(begin), ical._as_utc(finish)  # type: ignore[arg-type]
    end = finish.date() if isinstance(finish, datetime) else finish
    return True, begin, max(end, begin + timedelta(days=1))


def _coming(span: tuple[bool, Any, Any], now: datetime, today: date) -> bool:
    """Still to come or running now (EC-10): by the instant for times, by the device's day for whole days."""
    all_day, _start, end = span
    return end > today if all_day else end > now


def _windows() -> list[timedelta]:
    """How far each look reaches: short first (a daily series has its next occurrence tomorrow, and rolling out a year
    of it costs a quarter of a second), then a year at a time, up to 30 years."""
    return [timedelta(days=3), timedelta(days=32)] + [_STEP] * _STEPS


#: Rules whose first start may move on by whole periods without changing a single occurrence.
_PERIODS = {"DAILY": 1, "WEEKLY": 7}


def _near(parsed: Any, begin: datetime, first: Any) -> Any:
    """The series with its first start moved on by whole periods to shortly before ``begin``, so that rolling out does
    not count every day since 2018 (review of TIME-22, finding 1). Only for daily and weekly rules without COUNT or
    BYSETPOS, where moving by whole periods (on the wall clock of the series' zone) keeps every occurrence, every
    cancelled and every changed one; anything else stays as it is. ``first`` is the series' own first start: the
    object is moved in place (it was parsed for this alone), always from there."""
    master = ical.master_of(parsed)
    recur = master.get("RRULE")
    if recur is None or isinstance(recur, list) or master.get("RDATE") is not None:
        return parsed
    freq = str((recur.get("FREQ") or [""])[0]).upper()
    if freq not in _PERIODS or recur.get("COUNT") or recur.get("BYSETPOS"):
        return parsed
    interval = int((recur.get("INTERVAL") or [1])[0] or 1)
    period = _PERIODS[freq] * interval
    length = ical.duration_of(master)
    steps = max(0, (begin - ical._as_utc(first)).days // period - 1)
    if steps < 2:
        new_start: date | datetime = first
    elif isinstance(first, datetime) and first.tzinfo is not None and ical.zone_name(first):
        zone = ical.zone_name(first)
        new_start = ical.at_wall(ical.local_wall(first, zone) + timedelta(days=steps * period), zone)
    else:
        new_start = first + timedelta(days=steps * period)
    if master.decoded("DTSTART") != new_start:
        ical.set_times(master, new_start, new_start + length)
    return parsed


def _occurrence_of(parsed: Any, row: Any, now: datetime, today: date) -> tuple[Any, tuple[bool, Any, Any], bool]:
    """The occurrence of a series a hit stands for: the next one from now (or the one running), else the last one.
    Returns (its name, its span, coming)."""
    ends_at = row.ends_at
    first = ical.master_of(parsed).decoded("DTSTART")
    if ends_at is None or ends_at > now - timedelta(days=1):
        begin = max(row.starts_at, now - timedelta(days=2))
        for reach in _windows():
            found = [occ for occ in ical.occurrences(_near(parsed, begin, first), begin, begin + reach)
                     if _coming(_span(occ.start, occ.end), now, today)]
            if found:
                first = min(found, key=lambda occ: ical._as_utc(occ.start))
                return first.recurrence_id, _span(first.start, first.end), True
            begin += reach
            if ends_at is not None and begin > ends_at:
                break
    last_end = min(ends_at, now + timedelta(days=1)) if ends_at is not None else now + timedelta(days=1)
    finish = last_end + timedelta(days=1)
    for reach in _windows():
        begin = finish - reach
        found = [occ for occ in ical.occurrences(_near(parsed, begin, first), begin, finish)
                 if not _coming(_span(occ.start, occ.end), now, today)]
        if found:
            last = max(found, key=lambda occ: ical._as_utc(occ.start))
            return last.recurrence_id, _span(last.start, last.end), False
        if begin < row.starts_at:
            break
        finish = begin
    master = ical.master_of(_near(parsed, row.starts_at, first))
    span = _span(first, first + ical.duration_of(master))
    return None, span, _coming(span, now, today)


#: The occurrence a series stands for, by event and version: kept while it holds (review of TIME-22, finding 1). A
#: coming one holds until it is over, as seen from a later "now" (nothing earlier can come up then); the last one of
#: an ended series holds for good. A "now" before the one it was found for asks anew.
_SERIES: dict[tuple[int, str], tuple[datetime, date, tuple[Any, tuple[bool, Any, Any], bool]]] = {}
_SERIES_MAX = 20_000


def forget() -> None:
    _SERIES.clear()


def _series_occurrence(row: Any, text: str, now: datetime, today: date) -> tuple[Any, tuple[bool, Any, Any], bool]:
    key = (row.id, row.etag)
    kept = _SERIES.get(key)
    if kept is not None:
        found_at, found_today, found = kept
        coming = found[2]
        if found_at <= now and found_today <= today and (
                (coming and _coming(found[1], now, today)) or (not coming and row.ends_at is not None
                                                               and row.ends_at <= found_at)):
            return found
    if len(_SERIES) >= _SERIES_MAX:
        _SERIES.clear()
    found = _occurrence_of(ical.parse(text), row, now, today)
    _SERIES[key] = (now, today, found)
    return found


#: How SQLite holds a moment (SQLAlchemy's form): as text it sorts and compares like the moment itself.
_STORED = "%Y-%m-%d %H:%M:%S.%f"


def _stored(value: datetime) -> str:
    return value.astimezone(UTC).strftime(_STORED)


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@dataclass
class _Hit:
    """One hit, light: its start and end as sortable text (a moment in UTC, or a date for a whole day, the end then the
    day after), and what the answer needs once it is on the page shown."""

    start: str
    end: str
    all_day: bool
    coming: bool
    title: str
    owner: int
    uid: str
    id: int
    calendar_id: int
    recurring: bool
    waiting: bool
    time_zone: str
    occurrence: Any = None
    declined: bool = False
    location: str = ""
    busy_only: bool = False
    #: The rule of a series, as its RRULE says (the column beside the month names it, TIME-13).
    rrule: str = ""
    first: str = ""
    #: The member's invitation in it, ``{"state", "by"}`` (the lists show it dashed, TIME-18 AC-1).
    invitation: dict[str, Any] | None = None


def _times_of(span: tuple[bool, Any, Any]) -> tuple[str, str]:
    all_day, begin, finish = span
    if all_day:
        return begin.isoformat(), finish.isoformat()
    return _stored(begin), _stored(finish)


def _rrule_line(text: str) -> str:
    """The value of the series' RRULE line (unfolded), without reading the whole object again."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    for line in unfolded.splitlines():
        if line.startswith("RRULE:"):
            return line[6:].strip()
    return ""


def _answer(hit: _Hit) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": hit.id,
        "occurrence": ical.recurrence_key(hit.occurrence) or None,
        "calendar_id": hit.calendar_id,
        "title": hit.title,
        "recurring": hit.recurring,
        "declined": hit.declined,
        "waiting": hit.waiting,
        "coming": hit.coming,
        "location": hit.location,
        "busy_only": hit.busy_only,
        "rrule": hit.rrule,
        "first": hit.first or None,
        "invitation": hit.invitation,
    }
    if hit.all_day:
        first, after = date.fromisoformat(hit.start), date.fromisoformat(hit.end)
        last = after - timedelta(days=1) if after > first else first
        entry.update(all_day=True, start=first.isoformat(), end=last.isoformat(), time_zone="")
    else:
        entry.update(all_day=False, start=f"{hit.start[:10]}T{hit.start[11:19]}Z",
                     end=f"{hit.end[:10]}T{hit.end[11:19]}Z", time_zone=hit.time_zone)
    return entry


def search(db: Session, account: Account, query: str, *, only: set[int] | None = None, now: datetime,
           zone: str = "", offset: int = 0, limit: int = PAGE, include_hidden: bool = False,
           everything: bool = False, background: bool = True, from_day: date | None = None) -> dict[str, Any]:
    """``include_hidden``: calendars the member hid count too (a key of the API reaches them, TIME-26).
    ``everything``: no words, every event of the calendars in ``only`` (the column beside the month, TIME-13), a "busy
    only" calendar too, its events without title and place. ``background`` false: background calendars (holidays,
    school holidays) left out; ``from_day``: only what is still coming and begins on that day or later (Today's
    "Nächste Termine", TIME-11)."""
    words = words_of(query)
    today = ical.local_wall(now, zone).date() if zone else now.date()
    listed = [item for item, setting in calendars.ordered(db, account.id)
              if (include_hidden or not setting.hidden) and (everything or item.right != RIGHT_BUSY)
              and (only is None or item.calendar.id in only) and (background or not item.calendar.background)]
    result: dict[str, Any] = {"total": 0, "future_total": 0, "offset": offset, "items": [], "behind": []}
    if (not words and not everything) or not listed:
        return result
    shown = {item.calendar.id: item for item in listed}
    place = {calendar_id: index for index, calendar_id in enumerate(shown)}
    own = {calendar_id: invitations.context(db, item)[0] for calendar_id, item in shown.items()}
    statement = select(
        Event.id, Event.calendar_id, Event.uid, Event.title, Event.all_day, Event.time_zone, Event.recurring,
        Event.waiting, Event.search_text.is_(None).label("unfolded"), Event.etag, Event.location, Event.busy,
        # The moments as stored: no conversion for each of thousands of rows.
        cast(Event.starts_at, String).label("starts"), cast(Event.ends_at, String).label("ends"),
        # Read the object only where its columns cannot tell: an invitation, a holiday named in two languages.
        (func.instr(Event.ical, "ATTENDEE") + func.instr(Event.ical, "X-NEXTIME-SUMMARY-")).label("special"),
    ).where(Event.calendar_id.in_(shown), Event.deleted_at.is_(None))
    for word in words:
        statement = statement.where(or_(func.instr(Event.search_text, word) > 0, Event.search_text.is_(None)))
    rows = db.execute(statement).all()
    texts: dict[int, str] = {}
    wanted = [row.id for row in rows if row.unfolded or row.recurring or row.special]
    for first in range(0, len(wanted), 900):
        texts.update(db.execute(select(Event.id, Event.ical).where(Event.id.in_(wanted[first:first + 900]))).all())
    language = events.language_of(account)
    now_text, today_text = _stored(now), today.isoformat()
    # "Nächste Termine" (TIME-11): a series stands with its first occurrence from that day on, also when one of it
    # is still to come today (review of the additions, 2026-11-23).
    from_moment = (ical.at_wall(datetime.combine(from_day, datetime.min.time()), zone).astimezone(UTC)
                   if from_day is not None else None)
    hits: list[_Hit] = []
    for row in sorted(rows, key=lambda found: (place[found.calendar_id], found.id)):
        text = texts.get(row.id)
        busy_only = shown[row.calendar_id].right == RIGHT_BUSY
        if busy_only and not row.busy:
            # "Busy only" leaves free events out altogether (TIME-19 AC-2).
            continue
        if row.unfolded and words:
            folded = ical.search_text(text or "")
            if not all(word in folded for word in words):
                continue
        owner = shown[row.calendar_id].calendar.owner_id
        hit = _Hit("", "", row.all_day, False, "" if busy_only else (row.title or ""), owner, row.uid, row.id,
                   row.calendar_id, row.recurring, row.waiting and not busy_only, row.time_zone,
                   location="" if busy_only else (row.location or ""), busy_only=busy_only)
        if text is not None and (row.recurring or row.special):
            series = SimpleNamespace(id=row.id, etag=row.etag, starts_at=_moment(row.starts),
                                     ends_at=_moment(row.ends) if row.ends else None)
            if row.special:
                master = ical.master_of(ical.parse(text))
                hit.title = "" if busy_only else events.summary_in(master, language)
                state = invitations.state_of(master, own[row.calendar_id])
                hit.declined = bool(state and state["state"] == "declined")
                hit.invitation = {"state": state["state"], "by": state["by"]} if state and not busy_only else None
            if row.recurring:
                if from_moment is not None and from_day is not None and from_moment > now:
                    hit.occurrence, span, hit.coming = _occurrence_of(ical.parse(text), series, from_moment, from_day)
                else:
                    hit.occurrence, span, hit.coming = _series_occurrence(series, text, now, today)
                if everything:
                    hit.rrule = _rrule_line(text)
                    # The day of the first occurrence in the series' own zone: "jährlich seit 1954" (TIME-13 column);
                    # the stored moment is UTC, a series from 1 January 00:30 in Berlin began on the 1st.
                    hit.first = (row.starts[:10] if row.all_day else
                                 ical.local_wall(_moment(row.starts), row.time_zone or zone).date().isoformat())
            else:
                begin = master.decoded("DTSTART")
                span = _span(begin, begin + ical.duration_of(master))
                hit.coming = _coming(span, now, today)
            hit.all_day = span[0]
            hit.start, hit.end = _times_of(span)
        elif row.all_day:
            hit.start = row.starts[:10]
            hit.end = row.ends[:10] if row.ends else (date.fromisoformat(hit.start) + timedelta(days=1)).isoformat()
            hit.coming = hit.end > today_text
        else:
            hit.start, hit.end = row.starts, row.ends or row.starts
            hit.coming = hit.end > now_text
        hits.append(hit)
    if from_day is not None:
        hits = [hit for hit in hits if hit.coming and _first_day(hit, zone) >= from_day]
    if display.view(account).get("merge_duplicates", True):
        hits = _once(hits)
    future = sorted((hit for hit in hits if hit.coming), key=_order)
    past = sorted((hit for hit in hits if not hit.coming), key=_order, reverse=True)
    every = future + past
    begin = max(0, offset)
    result.update(total=len(every), future_total=len(future),
                  items=[_answer(hit) for hit in every[begin:begin + max(1, min(limit, PAGE_MAX))]],
                  behind=_behind(db, listed))
    return result


def _first_day(hit: _Hit, zone: str) -> date:
    """The day a hit begins on, in the zone shown."""
    if hit.all_day:
        return date.fromisoformat(hit.start[:10])
    moment = _moment(hit.start)
    return ical.local_wall(moment, zone).date() if zone else moment.date()


def _order(hit: _Hit) -> tuple[str, str]:
    """By start (a whole day before the times of that day), then by title."""
    return hit.start, hit.title.casefold()


def _once(hits: list[_Hit]) -> list[_Hit]:
    """The same event in two calendars of one person once, the first in the order of the list (TIME-14 AC-3): by its
    UID, or by its title with the same start and end. Two persons' events never merge (EC-1)."""
    kept: list[_Hit] = []
    known: set[tuple] = set()
    for hit in hits:
        times = (hit.all_day, hit.start, hit.end)
        # The same UID at another time is two events (TIME-14 EC-5): the times are part of both keys.
        keys = {("uid", hit.owner, hit.uid, *times), ("title", hit.owner, hit.title.strip().casefold(), *times)}
        if keys & known:
            continue
        known |= keys
        kept.append(hit)
    return kept


def _behind(db: Session, listed: list[calendars.Access]) -> list[dict[str, Any]]:
    """The connected accounts among the calendars searched that do not answer right now: searched as last known, with
    the time of that state (EC-8)."""
    source_ids = {item.calendar.source_id for item in listed if item.calendar.source_id is not None}
    if not source_ids:
        return []
    rows = db.scalars(select(Source).where(Source.id.in_(source_ids), Source.state != "ok"))
    return [{"kind": row.kind, "label": row.label, "synced_at": row.synced_at.isoformat() if row.synced_at else None}
            for row in rows]
