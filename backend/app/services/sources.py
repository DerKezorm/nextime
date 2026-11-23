"""The member's sources on "Calendars and accounts" (TIME-15, TIME-16, TIME-17): connecting iCloud and other CalDAV
servers, choosing their calendars, reconnecting, disconnecting, the state of every source, and the notices about
changes a source refused.

A source belongs to one member; the operator sees neither its access nor its events (EC-21). Its calendars are
ordinary calendars with ``source_id``: the views, collections and the quiet display treat them like any other.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import (
    SOURCE_CALDAV,
    SOURCE_GOOGLE,
    SOURCE_HOLIDAYS,
    SOURCE_ICLOUD,
    SOURCE_SUBSCRIPTION,
    STATE_OK,
    Account,
    Calendar,
    Event,
    RemoteChange,
    RemoteNotice,
    Source,
)
from . import calendars, ical, live, shares, symbols, sync, trash, vault
from .remote_dav import AUTH, DOWN, Remote, RemoteCalendar, RemoteError

logger = logging.getLogger("nextime.sources")

#: Where iCloud's calendars are found; the tests point it at their own server.
ICLOUD_URL = "https://caldav.icloud.com/"
#: What Apple's app-specific passwords look like (TIME-15 EC-4).
APP_PASSWORD = re.compile(r"^[a-z]{4}-[a-z]{4}-[a-z]{4}-[a-z]{4}$", re.IGNORECASE)
#: Up to this many events an account stays smooth for sure (TIME-15 EC-12, as TIME-4 AC-6 for calendars).
EVENTS_SMOOTH_UP_TO = 20_000
DAV_KINDS = (SOURCE_ICLOUD, SOURCE_CALDAV)


def own(db: Session, account: Account, source_id: int) -> Source:
    row = db.get(Source, source_id)
    if row is None or row.account_id != account.id:
        raise error("not_found", "Not found.", 404)
    return row


def _color(value: str) -> str:
    from ..routers.dav import _nearest_color

    return _nearest_color(value) if value else "slate"


def _listing(found: list[RemoteCalendar], before: list[dict[str, Any]] | None, *, first: bool) -> list[dict]:
    old = {item["url"]: item for item in before or []}
    return [{"url": item.url, "name": item.name, "color": _color(item.color), "read_only": item.read_only,
             "tag": item.ctag or item.sync_token,
             # A calendar that appears later is only offered, marked "new" (TIME-15 AC-14).
             "new": (not first) and (item.url not in old or bool(old[item.url].get("new")))}
            for item in found]


def clean_server(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise error("server_missing", "Enter the address of the calendar server.", 422, field="server")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise error("no_calendar_server", "No calendar server answers at this address.", 422, field="server")
    return text


# --- iCloud and other CalDAV servers (AC-1, AC-2, AC-11, EC-4, EC-13, EC-14, EC-15) ---------------------------------


def _check_dav(kind: str, server: str, username: str, password: str) -> Any:
    try:
        with Remote(server, username=username, password=password) as remote:
            return remote.discover()
    except RemoteError as exc:
        if exc.kind == AUTH:
            if kind == SOURCE_ICLOUD and not APP_PASSWORD.match(password):
                raise error("apple_password_not_app_specific", "Apple refuses. This looks like your normal Apple "
                            "password; nextime needs an app-specific one (xxxx-xxxx-xxxx-xxxx).", 422,
                            field="password") from None
            raise error("credentials_refused", "The server refuses this name and password.", 422,
                        field="password") from None
        if exc.kind == DOWN:
            raise error("source_unreachable", "The server does not answer. Try again in a moment.", 502) from None
        raise error("no_calendar_server", "No calendar server answers at this address.", 422,
                    field="server") from None


def connect_dav(db: Session, account: Account, kind: str, *, server: str, username: str, password: str,
                client_ref: str | None) -> tuple[Source, bool]:
    """Checks the access at once and keeps it; the calendars are chosen next (AC-9). True when it is new."""
    if client_ref:
        known = db.scalar(select(Source).where(Source.account_id == account.id, Source.client_ref == client_ref))
        if known is not None:
            return known, False
    username, password = (username or "").strip(), (password or "").strip()
    server = ICLOUD_URL if kind == SOURCE_ICLOUD else clean_server(server)
    if not username:
        raise error("username_missing", "Enter the name.", 422, field="username")
    if not password:
        raise error("password_missing", "Enter the password.", 422, field="password")
    identity = vault.identity(kind, server.rstrip("/"), username)
    existing = db.scalar(select(Source).where(Source.account_id == account.id, Source.kind == kind,
                                              Source.identity == identity))
    if existing is not None:
        # The same access twice is one connection: on to "Choose calendars" of the one there (EC-14).
        raise error("source_exists", "This account is connected already.", 409, source_id=existing.id)
    found = _check_dav(kind, server, username, password)
    host = urlsplit(server).hostname or ""
    row = Source(account_id=account.id, kind=kind, identity=identity, server=server, username=username,
                 label=username if kind == SOURCE_ICLOUD else f"{username} · {host}", client_ref=client_ref,
                 remote=_listing(found.calendars, None, first=True), options={"home": found.home},
                 addresses=found.addresses, schedules=found.schedules, state=STATE_OK, due_at=sync.clock())
    try:
        db.add(row)
        db.flush()
        row.secret_enc = vault.seal({"password": password}, sync.place(row))
        live.touch_accounts(db, {account.id})
        db.commit()
    except IntegrityError:
        db.rollback()
        known = db.scalar(select(Source).where(Source.account_id == account.id, (
            (Source.client_ref == client_ref) if client_ref else (Source.identity == identity))))
        if known is None:
            raise
        return known, False
    logger.info("Source connected source=%s kind=%s calendars=%s", row.id, kind, len(found.calendars))
    return row, True


def reconnect_dav(db: Session, account: Account, source_id: int, password: str) -> Source:
    """A new password for an expired access: calendars, colors, collections and shares stay (AC-11)."""
    row = own(db, account, source_id)
    if row.kind not in DAV_KINDS:
        raise error("not_found", "Not found.", 404)
    password = (password or "").strip()
    if not password:
        raise error("password_missing", "Enter the password.", 422, field="password")
    found = _check_dav(row.kind, row.server, row.username, password)
    row.secret_enc = vault.seal({"password": password}, sync.place(row))
    row.addresses, row.schedules = found.addresses, found.schedules
    options = dict(row.options or {})
    options["home"] = found.home
    row.options = options
    apply_listing(db, row, found.calendars)
    row.failures = 0
    row.due_at = sync.clock()
    sync.set_state(db, row, STATE_OK)
    db.commit()
    sync.nudge()
    return row


def apply_listing(db: Session, source: Source, found: list[RemoteCalendar]) -> None:
    """What the source lists now: a calendar gone there goes here, not into the trash, and out of the collections; a
    renamed one takes the new name; one only to be read becomes so (EC-20); a new one is offered (AC-14)."""
    before = {item["url"]: item for item in source.remote or []}
    source.remote = _listing(found, source.remote, first=False)
    now = {item.url: item for item in found}
    for calendar in db.scalars(select(Calendar).where(Calendar.source_id == source.id)):
        item = now.get(calendar.remote_url)
        if item is None:
            live.touch_calendar(db, calendar.id)
            shares.calendar_going(db, calendar, for_good=True)
            # Events of it in the trash stay restorable elsewhere (TIME-23 EC-9).
            if not trash.keep_trashed(db, calendar):
                db.delete(calendar)
            continue
        changed = False
        if item.name and item.name != before.get(calendar.remote_url, {}).get("name", calendar.name):
            calendar.name = item.name[: calendars.NAME_MAX]
            changed = True
        if item.read_only != calendar.read_only:
            calendar.read_only = item.read_only
            changed = True
        if changed:
            calendar.props_rev += 1
            live.touch_calendar(db, calendar.id)
    db.flush()


def choices(db: Session, account: Account, source_id: int) -> dict[str, Any]:
    """"Choose calendars": every calendar the account has there, with color, "read only" and "new" (AC-6, AC-9,
    AC-14). Asked fresh when the source answers, else the last list."""
    row = own(db, account, source_id)
    if row.kind in DAV_KINDS or row.kind == SOURCE_GOOGLE:
        try:
            _refresh_listing(db, row)
        except RemoteError:
            pass
    chosen = set(db.scalars(select(Calendar.remote_url).where(Calendar.source_id == row.id)))
    db.commit()
    return {"source_id": row.id, "kind": row.kind, "label": row.label,
            "calendars": [{"url": item["url"], "name": item["name"], "color": item["color"],
                           "read_only": item["read_only"], "new": bool(item.get("new")),
                           "chosen": item["url"] in chosen} for item in row.remote or []]}


def _refresh_listing(db: Session, row: Source) -> None:
    from . import google

    secret = sync.access_of(row)
    if secret is None or row.state != STATE_OK:
        return
    if row.kind == SOURCE_GOOGLE:
        google.refresh_listing(db, row, secret)
        return
    home = str((row.options or {}).get("home") or "")
    if not home:
        return
    with Remote(row.server, username=row.username, password=str(secret.get("password", ""))) as remote:
        apply_listing(db, row, remote.calendars(home))


def choose(db: Session, account: Account, source_id: int, urls: list[str], *, drop_waiting: bool = False) -> Source:
    """The calendars that show in nextime (AC-9): new ones come in their source's group, ones no longer chosen go
    (at the source they stay). The same choice twice changes nothing (EC-15). A calendar with changes still waiting
    goes only after nextime said how many get lost, as with "Disconnect" (EC-8)."""
    row = own(db, account, source_id)
    offered = {item["url"]: item for item in row.remote or []}
    wanted = list(dict.fromkeys(urls))
    if any(url not in offered for url in wanted):
        raise error("invalid_choice", "Choose among the calendars of this account.", 422)
    present = {calendar.remote_url: calendar for calendar in db.scalars(
        select(Calendar).where(Calendar.source_id == row.id))}
    leaving = [calendar.id for url, calendar in present.items() if url not in wanted]
    if leaving and not drop_waiting:
        waiting = int(db.scalar(select(func.count(func.distinct(RemoteChange.href))).where(
            RemoteChange.calendar_id.in_(leaving))) or 0)
        if waiting:
            raise error("changes_waiting", "Changes are still waiting to reach the source.", 409, count=waiting)
    added = False
    for url in wanted:
        if url in present:
            continue
        item = offered[url]
        made = calendars._make(db, account.id, (item["name"] or "Calendar")[: calendars.NAME_MAX], item["color"],
                               background=row.kind in (SOURCE_HOLIDAYS,))
        made.source_id, made.remote_url, made.read_only = row.id, url, bool(item["read_only"])
        made.locked = row.state not in (STATE_OK, "unreachable")
        added = True
    for url, calendar in present.items():
        if url not in wanted:
            live.touch_calendar(db, calendar.id)
            shares.calendar_going(db, calendar, for_good=True)
            if not trash.keep_trashed(db, calendar):
                db.delete(calendar)
    row.remote = [{**item, "new": False} for item in row.remote or []]
    if added:
        row.loading = True
        row.due_at = sync.clock()
    live.touch_accounts(db, {account.id})
    db.commit()
    sync.nudge()
    return row


def leave(db: Session, account: Account, source_id: int, calendar_id: int, *, drop_waiting: bool = False) -> Source:
    """"Aus nextime entfernen" of one calendar of an account: the same as leaving it out in "Kalender wählen"; at the
    source it stays."""
    row = own(db, account, source_id)
    present = list(db.scalars(select(Calendar).where(Calendar.source_id == row.id)))
    if not any(calendar.id == calendar_id for calendar in present):
        raise error("not_found", "Not found.", 404)
    offered = {item["url"] for item in row.remote or []}
    urls = [calendar.remote_url for calendar in present
            if calendar.id != calendar_id and calendar.remote_url in offered]
    return choose(db, account, source_id, urls, drop_waiting=drop_waiting)


def disconnect(db: Session, account: Account, source_id: int, *, drop_waiting: bool) -> None:
    """Calendars, events and the stored access leave nextime; at the source everything stays (AC-5). With changes
    still waiting, nextime asks first how many get lost (EC-8)."""
    row = own(db, account, source_id)
    waiting = sync.pending(db, row.id)
    if waiting and not drop_waiting:
        raise error("changes_waiting", "Changes are still waiting to reach the source.", 409, count=waiting)
    for calendar in db.scalars(select(Calendar).where(Calendar.source_id == row.id)):
        live.touch_calendar(db, calendar.id)
        # Gone for everybody it was shared with too, each with a notice (TIME-19 EC-3).
        shares.calendar_going(db, calendar, for_good=True)
    live.touch_accounts(db, {account.id})
    # Events of its calendars in the trash stay restorable elsewhere until their deadline (TIME-23 EC-9).
    trash.keep_trashed_of_source(db, row.id)
    db.execute(delete(Source).where(Source.id == row.id))
    db.commit()
    logger.info("Source disconnected source=%s kind=%s", source_id, row.kind)


def refresh(db: Session, account: Account, source_id: int) -> None:
    """"Fetch again", "Try again": a round now; a second click while it runs makes no second one (TIME-17 EC-9)."""
    row = own(db, account, source_id)
    row.due_at = sync.clock()
    db.commit()
    sync.run_source(row.id, force=True)


# --- The rows of "Calendars and accounts" (AC-10, EC-1, EC-9, EC-18) -------------------------------------------------


def rows(db: Session, account: Account) -> dict[str, Any]:
    out = []
    for row in db.scalars(select(Source).where(Source.account_id == account.id).order_by(Source.id)):
        cals = list(db.scalars(select(Calendar).where(Calendar.source_id == row.id).order_by(Calendar.id)))
        options = row.options or {}
        unreadable = sum(len(hrefs) for hrefs in (options.get("unreadable") or {}).values())
        entry: dict[str, Any] = {
            "id": row.id,
            "kind": row.kind,
            "label": row.label,
            "calendars": [{"id": calendar.id, "name": calendar.name, "color": calendar.color,
                           "icon": symbols.shown(calendar.icon), "read_only": calendar.read_only} for calendar in cals],
            "state": row.state,
            "since": row.state_since.isoformat() if row.state_since else None,
            "synced_at": row.synced_at.isoformat() if row.synced_at else None,
            "problem": row.problem,
            "waiting": sync.pending(db, row.id),
            "unreadable": unreadable,
            "new": sum(1 for item in row.remote or [] if item.get("new")),
            "loading": row.loading,
            "events": int(db.scalar(select(func.count()).select_from(Event).where(
                Event.calendar_id.in_([calendar.id for calendar in cals]), Event.deleted_at.is_(None))) or 0),
        }
        if row.kind == SOURCE_HOLIDAYS:
            entry["holidays"] = {"country": options.get("country"), "regions": options.get("regions", []),
                                 "school": bool(options.get("school"))}
        if row.kind == SOURCE_SUBSCRIPTION:
            entry["google"] = bool(options.get("google"))
        out.append(entry)
    total = int(db.scalar(select(func.count()).select_from(Event).join(Calendar, Calendar.id == Event.calendar_id)
                          .where(Calendar.owner_id == account.id, Calendar.deleted_at.is_(None),
                                 Event.deleted_at.is_(None))) or 0)
    return {"sources": out, "notices": notices(db, account), "events_total": total,
            "events_smooth_up_to": EVENTS_SMOOTH_UP_TO}


# --- Refused changes (EC-6, TIME-5 EC-5) -----------------------------------------------------------------------------


def notices(db: Session, account: Account) -> list[dict[str, Any]]:
    out = []
    for row in db.scalars(select(RemoteNotice).where(RemoteNotice.account_id == account.id)
                          .order_by(RemoteNotice.id)):
        calendar = db.get(Calendar, row.calendar_id) if row.calendar_id else None
        summary = None
        if row.lost_ical:
            from . import events

            try:
                summary = events._summary(ical.parse(row.lost_ical))
            except ical.IcalError:
                summary = None
        out.append({"id": row.id, "title": row.title, "source": row.source_kind,
                    "calendar": calendar.name if calendar else "", "calendar_id": row.calendar_id,
                    "lost": summary, "created_at": row.created_at.isoformat()})
    return out


def settle_notice(db: Session, account: Account, notice_id: int, *, create: bool) -> int | None:
    """"Create as a new event": the lost change as a new event in its calendar while that is writable, else in the
    default calendar; "Discard": the notice goes. The new event's id, if one was made."""
    from . import events

    row = db.get(RemoteNotice, notice_id)
    if row is None or row.account_id != account.id:
        raise error("not_found", "Not found.", 404)
    made = None
    if create and row.lost_ical:
        target = calendars.access(db, account.id, row.calendar_id) if row.calendar_id else None
        if target is None or not target.writable:
            calendars.ensure_seeded(db, account)
            chosen = calendars.default_of(db, account.id).calendar
            if chosen is None:
                raise error("no_writable_calendar", "There is no calendar to write into.", 409)
            calendar_id = chosen.id
        else:
            calendar_id = target.calendar.id
        parsed = ical.parse(row.lost_ical)
        uid = str(uuid.uuid4())
        for component in ical.events_of(parsed):
            component.pop("UID", None)
            component.add("UID", uid)
            ical.stamp(component)
        event = Event(calendar_id=calendar_id, uid=uid, href=f"{uid}.ics", ical="", etag="", created_by=account.id,
                      starts_at=sync.clock())
        db.add(event)
        events._store(db, event, parsed, account.id)
        db.flush()
        made = event.id
    db.delete(row)
    live.touch_accounts(db, {account.id})
    db.commit()
    return made
