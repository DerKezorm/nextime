"""What the nextime app gets and sends (TIME-29, ``docs/vertrag-app1.md``): ``changes`` since a cursor, ``wait``, and
the queue of changes made on the device (``ops``).

**The cursor** is ``<database mark>.<change number>.<mark of the lists>``: the number of the last ``event_changes``
row the device had (the same numbers the calendar account hands out, ``routers/dav.py``), and a short hash of the
lists that come whole on every page (calendars, collections, conflicts, the account). A restored backup has another
database mark, so an old cursor gets the full listing (EC-4). A full listing goes page by page by the events' ids
(``…f<id>`` behind the cursor) and ends with a cursor of the change number it started at, so that what changed
meanwhile comes with the next call.

**Events** come whole as their iCalendar object (series with their exceptions), without alarms: the app plans
reminders itself (G6 of TIMEIOS). "Busy only" gives what the calendar account gives (``dav.busy_view``).

**The queue**: each op stands alone in its own transaction and is answered once; the answer is kept per account and
``ref`` (``app_ops``, 30 days), so the same op sent again changes nothing (EC-6). The later arrival wins (AC-2); the
one overwritten hears of it through ``event_conflicts`` with both versions (TIME-5 EC-2).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import RIGHT_BUSY, Account, AppOp, Event, EventChange, EventConflict, Source, utcnow
from . import calendars, collections, display, events, symbols

logger = logging.getLogger("nextime.app")

#: Events per page of ``changes``.
PAGE = 500
#: Ops per call.
OPS_MAX = 50
#: How long an answer of an op is kept for the same ``ref``.
OPS_DAYS = 30
REF = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
#: Codes after which the same ``ref`` may come again with another answer: the op could not run now.
RETRY_CODES = {"source_busy"}


# --- What the account sees -------------------------------------------------------------------------------------------


class _People:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.known: dict[int, dict[str, Any] | None] = {}

    def __call__(self, account_id: int | None) -> dict[str, Any] | None:
        if account_id is None:
            return None
        if account_id not in self.known:
            row = self.db.get(Account, account_id)
            self.known[account_id] = {"id": row.id, "name": row.shown_name} if row is not None else None
        return self.known[account_id]


def iso(value: Any) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def account_out(account: Account) -> dict[str, Any]:
    """``operator``: whether the account runs the server; the app names the browser's server pages only then."""
    from ..models import OPERATOR

    return {"id": account.id, "name": account.shown_name, "login": account.name, "email": account.email or "",
            "avatar": iso(account.avatar_at) or "", "operator": account.role == OPERATOR}


def account_block(account: Account) -> dict[str, Any]:
    return {**account_out(account), "language": account.language or "", "display": display.view(account)}


def _calendar_out(db: Session, item: calendars.Access, row: Any, default_id: int | None, people: _People
                  ) -> dict[str, Any]:
    calendar = item.calendar
    own = item.right == calendars.OWNER
    state = None
    if own and calendar.source_id is not None:
        state = db.scalar(select(Source.state).where(Source.id == calendar.source_id))
    return {
        "id": calendar.id, "name": calendar.name, "color": calendar.color, "icon": symbols.shown(calendar.icon),
        "background": calendar.background, "holiday": calendars.highlighted(calendar, row),
        "reminder": calendar.reminder, "defaults": row.new_defaults or {}, "group": item.group, "kind": item.kind,
        "right": item.right, "writable": item.writable, "read_only": calendar.read_only, "hidden": row.hidden,
        "position": row.position, "default": calendar.id == default_id,
        "owner": None if own else people(calendar.owner_id),
        "shared": own and events.calendars_shared(db, calendar.id), "source_state": state,
    }


class Seen:
    """The calendars the account sees now (with its rights) and the lists that come whole on every page."""

    def __init__(self, db: Session, account_id: int) -> None:
        account = db.get(Account, account_id)
        assert account is not None
        self.account = account
        calendars.ensure_seeded(db, account)
        chosen = calendars.default_of(db, account_id)
        default_id = chosen.calendar.id if chosen.calendar else None
        rows = calendars.ordered(db, account_id)
        db.commit()
        people = _People(db)
        self.people = people
        self.access = {item.calendar.id: item for item, _row in rows}
        self.calendars = [_calendar_out(db, item, row, default_id, people) for item, row in rows]
        self.collections = [{"id": entry["id"], "name": entry["name"], "calendar_ids": entry["calendar_ids"],
                             "with_tasks": entry["with_tasks"]} for entry in collections.view(db, account_id)]
        self.conflicts = _conflicts(db, account_id, set(self.access), people)
        self.account_block = account_block(account)
        self.german = (account.language or account.browser_language or "").lower().startswith("de")

    def lists(self) -> dict[str, Any]:
        return {"calendars": self.calendars, "collections": self.collections, "conflicts": self.conflicts,
                "account": self.account_block}

    def mark(self) -> str:
        text = json.dumps(self.lists(), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _conflicts(db: Session, account_id: int, seen: set[int], people: _People) -> list[dict[str, Any]]:
    rows = db.execute(select(EventConflict, Event.calendar_id).join(Event, Event.id == EventConflict.event_id)
                      .where(EventConflict.account_id == account_id)
                      .order_by(EventConflict.id.desc())).all()
    out: list[dict[str, Any]] = []
    done: set[int] = set()
    for conflict, calendar_id in rows:
        if conflict.event_id in done or calendar_id not in seen:
            continue
        done.add(conflict.event_id)
        out.append({"event_id": conflict.event_id, "mine": conflict.mine_ical, "by": people(conflict.by_id),
                    "at": iso(conflict.created_at)})
    return out


def event_out(row: Event, access: calendars.Access, people: _People, german: bool) -> dict[str, Any] | None:
    """An event as the app gets it; ``None`` when the account does not see it (a free event of "busy only")."""
    from ..routers.dav import busy_view
    from . import alarms

    busy_only = access.right == RIGHT_BUSY
    if busy_only:
        text = busy_view(row.ical, german)
        if text is None:
            return None
    else:
        text = alarms.strip(row.ical)
    return {
        "id": row.id, "calendar_id": row.calendar_id, "href": row.href, "uid": row.uid, "etag": row.etag,
        "ical": text, "title": "" if busy_only else row.title, "all_day": row.all_day,
        "starts_at": iso(row.starts_at), "ends_at": iso(row.ends_at), "time_zone": row.time_zone,
        "recurring": row.recurring, "busy": row.busy, "location": "" if busy_only else row.location,
        "busy_only": busy_only, "waiting": row.waiting, "origin": row.origin,
        "created_by": None if busy_only else people(row.created_by),
        "updated_by": None if busy_only else people(row.updated_by), "updated_at": iso(row.updated_at),
    }


# --- The cursor -----------------------------------------------------------------------------------------------------


def _epoch(db: Session) -> str:
    from ..routers.dav import epoch

    return epoch(db)


def _head(db: Session) -> int:
    return int(db.scalar(select(func.max(EventChange.id))) or 0)


def read_cursor(db: Session, cursor: str | None) -> tuple[int, str, int | None] | None:
    """``(change number, mark of the lists, event id of a full listing going on)``, or ``None``: no cursor, another
    database's, or none the server made."""
    if not cursor or cursor == "0" or len(cursor) > 200:
        return None
    parts = cursor.split(".")
    if len(parts) not in (3, 4) or parts[0] != _epoch(db):
        return None
    try:
        number = int(parts[1])
        after = int(parts[3][1:]) if len(parts) == 4 and parts[3][:1] == "f" else None
    except ValueError:
        return None
    if len(parts) == 4 and after is None or number < 0:
        return None
    return number, parts[2], after


def _cursor(db: Session, number: int, mark: str, after: int | None = None) -> str:
    base = f"{_epoch(db)}.{number}.{mark}"
    return base if after is None else f"{base}.f{after}"


def changes(db: Session, account_id: int, cursor: str | None, only: set[int] | None = None,
            page: int = PAGE) -> dict[str, Any]:
    seen = Seen(db, account_id)
    mark = seen.mark()
    ids = [calendar_id for calendar_id in seen.access if only is None or calendar_id in only]
    head = _head(db)
    known = read_cursor(db, cursor)
    out: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    if known is None or known[2] is not None:
        # The full listing: every event of the calendars, by id; it started at ``start``.
        start, after = (head, 0) if known is None else (known[0], known[2] or 0)
        rows = db.scalars(select(Event).where(Event.calendar_id.in_(ids), Event.deleted_at.is_(None),
                                              Event.id > after).order_by(Event.id).limit(page + 1)).all() if ids else []
        more = len(rows) > page
        rows = rows[:page]
        for row in rows:
            shown = event_out(row, seen.access[row.calendar_id], seen.people, seen.german)
            if shown is not None:
                out.append(shown)
        next_cursor = _cursor(db, start, mark, rows[-1].id) if more else _cursor(db, start, mark)
        full = True
    else:
        number = known[0]
        found = db.execute(select(EventChange.calendar_id, EventChange.href, func.max(EventChange.id).label("last"))
                           .where(EventChange.calendar_id.in_(ids), EventChange.id > number)
                           .group_by(EventChange.calendar_id, EventChange.href)
                           .order_by(func.max(EventChange.id)).limit(page + 1)).all() if ids else []
        more = len(found) > page
        found = found[:page]
        wanted: dict[int, list[str]] = {}
        for calendar_id, href, _last in found:
            wanted.setdefault(calendar_id, []).append(href)
        live: dict[tuple[int, str], Event] = {}
        for calendar_id, hrefs in wanted.items():
            for start in range(0, len(hrefs), 400):
                for row in db.scalars(select(Event).where(Event.calendar_id == calendar_id,
                                                          Event.href.in_(hrefs[start:start + 400]),
                                                          Event.deleted_at.is_(None))):
                    live[(calendar_id, row.href)] = row
        for calendar_id, href, _last in found:
            row = live.get((calendar_id, href))
            shown = event_out(row, seen.access[calendar_id], seen.people, seen.german) if row is not None else None
            if shown is not None:
                out.append(shown)
            else:
                removed.append({"kind": "event", "calendar_id": calendar_id, "href": href})
        last = found[-1][2] if found else number
        next_cursor = _cursor(db, last if more else max(head, last), mark)
        full = False
    return {"cursor": next_cursor, "full": full, "more": more, "events": out, "removed": removed, **seen.lists()}


def changed_since(db: Session, account_id: int, cursor: str) -> str | None:
    """A newer cursor once something the account sees changed after ``cursor``; ``None`` while nothing did."""
    known = read_cursor(db, cursor)
    seen = Seen(db, account_id)
    mark = seen.mark()
    if known is None or known[2] is not None:
        return _cursor(db, _head(db), mark)
    number = known[0]
    ids = list(seen.access)
    newer = db.scalar(select(func.max(EventChange.id)).where(EventChange.calendar_id.in_(ids),
                                                             EventChange.id > number)) if ids else None
    if newer is None and mark == known[1]:
        return None
    return _cursor(db, int(newer or number), mark)


def current(db: Session, account_id: int) -> str:
    """The cursor ``changes`` would give right now after everything (for the answer of ``ops``)."""
    return _cursor(db, _head(db), Seen(db, account_id).mark())


# --- The queue of the device ----------------------------------------------------------------------------------------


def _refused(code: str, message: str, **values: Any) -> dict[str, Any]:
    return {"state": "refused", "code": code, "message": message, **values}


def _from_http(exc: HTTPException) -> dict[str, Any]:
    found = exc.detail if isinstance(exc.detail, dict) else {"code": "invalid_input", "message": str(exc.detail)}
    values = {key: value for key, value in found.items() if key not in ("code", "message")}
    return _refused(str(found.get("code") or "invalid_input"), str(found.get("message") or ""), **values)


def _from_validation(exc: ValidationError) -> dict[str, Any]:
    fields = sorted({".".join(str(part) for part in item.get("loc", ())) for item in exc.errors()})
    return _refused("invalid_input", "The input is not valid.", fields=fields)


def _done(db: Session, account: Account, row: Event, **more: Any) -> dict[str, Any]:
    access = calendars.access(db, account.id, row.calendar_id)
    shown = event_out(row, access, _People(db), (account.language or "").startswith("de")) if access else None
    return {"state": "done", "event": shown, **more}


def _run(db: Session, account: Account, op: dict[str, Any]) -> dict[str, Any]:
    from ..routers.events import EventChangeIn, EventIn, _change

    kind = op.get("op")
    if kind == "create":
        fields = dict(op.get("event") or {}) if isinstance(op.get("event"), dict) else {}
        if not fields.get("all_day") and not fields.get("time_zone"):
            # Without a zone: the account's (as the web, which always sends the one it shows).
            fields["time_zone"] = _zone(account)
        body = EventIn.model_validate({**fields, "client_ref": op["ref"]})
        row, _new = events.create(db, account, _change(body, creating=True), op["ref"])
        return _done(db, account, row)
    if kind == "change":
        event_id = _event_id(op)
        fields = dict(op.get("event") or {}) if isinstance(op.get("event"), dict) else {}
        if fields.get("start") is not None and not fields.get("all_day") and not fields.get("time_zone"):
            # New times without a zone: the event's own, else the account's.
            known = db.get(Event, event_id)
            fields["time_zone"] = (known.time_zone if known is not None and known.time_zone else "") or _zone(account)
        fields.update({"scope": op.get("scope") or "all", "occurrence": op.get("occurrence"), "etag": op.get("etag")})
        body = EventChangeIn.model_validate(fields)
        if body.scope != "all" and not body.occurrence:
            return _refused("occurrence_unknown", "This occurrence is not part of the series.")
        change = _change(body, creating=False)
        try:
            row = events.update(db, account, event_id, change, scope=body.scope, occurrence=body.occurrence,
                                base_etag=body.etag)
        except HTTPException as exc:
            if not (isinstance(exc.detail, dict) and exc.detail.get("code") == "event_deleted"):
                raise
            # Deleted meanwhile, changed here later: the later arrival wins, the event comes back with the change
            # (TIMEIOS-5 EC-5). Only from the account's own trash; otherwise it stays deleted and says so.
            db.rollback()
            from . import trash

            try:
                trash.restore_event(db, account, event_id)
            except HTTPException:
                db.rollback()
                raise exc from None
            row = events.update(db, account, event_id, _change(body, creating=False), scope=body.scope,
                                occurrence=body.occurrence, base_etag=None)
        return _done(db, account, row)
    if kind == "delete":
        event_id = _event_id(op)
        scope = op.get("scope") or "all"
        if scope not in events.SCOPES:
            return _refused("invalid_input", "The input is not valid.", fields=["scope"])
        occurrence = op.get("occurrence")
        if occurrence is not None and (not isinstance(occurrence, str) or len(occurrence) > 20):
            return _refused("invalid_input", "The input is not valid.", fields=["occurrence"])
        try:
            entry = events.delete(db, account, event_id, scope=scope, occurrence=occurrence)
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "event_deleted":
                # Deleted meanwhile somewhere else: what the device wanted is so.
                return {"state": "done", "event": None}
            raise
        row = db.get(Event, event_id)
        if row is not None and row.deleted_at is None:
            return _done(db, account, row, trash=entry)
        return {"state": "done", "event": None, "trash": entry}
    if kind == "resolve":
        event_id = _event_id(op)
        restore = op.get("restore")
        if not isinstance(restore, bool):
            return _refused("invalid_input", "The input is not valid.", fields=["restore"])
        row = db.get(Event, event_id)
        if row is not None and events.conflict_of(db, account, row) is None and not restore:
            return _done(db, account, row) if row.deleted_at is None else {"state": "done", "event": None}
        row = events.resolve_conflict(db, account, event_id, restore=restore)
        return _done(db, account, row)
    if kind == "hide":
        calendar_id = op.get("calendar_id")
        hidden = op.get("hidden")
        if not isinstance(calendar_id, int) or isinstance(calendar_id, bool) or not isinstance(hidden, bool):
            return _refused("invalid_input", "The input is not valid.", fields=["calendar_id", "hidden"])
        calendars.change(db, account, calendar_id, {"hidden": hidden})
        return {"state": "done"}
    return _refused("op_unknown", "This change is not known.", op=str(kind)[:32])


def _zone(account: Account) -> str:
    return str(getattr(display.zone_of(account), "key", "") or "UTC")


def _event_id(op: dict[str, Any]) -> int:
    value = op.get("id")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        from ..errors import error

        raise error("invalid_input", "The input is not valid.", 422, fields=["id"])
    return value


def run_ops(session_factory: Any, account_id: int, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each op in its own session and transaction, in the order given; each answered once (EC-6, EC-7)."""
    answers = []
    with session_factory() as db:
        db.execute(delete(AppOp).where(AppOp.created_at < utcnow() - timedelta(days=OPS_DAYS)))
        db.commit()
    for op in ops:
        ref = op.get("ref") if isinstance(op, dict) else None
        if not isinstance(ref, str) or not REF.match(ref):
            answers.append({"ref": ref if isinstance(ref, str) else None,
                            **_refused("invalid_input", "The input is not valid.", fields=["ref"])})
            continue
        with session_factory() as db:
            kept = db.scalar(select(AppOp).where(AppOp.account_id == account_id, AppOp.ref == ref))
            if kept is not None:
                answers.append({"ref": ref, **kept.answer})
                continue
            account = db.get(Account, account_id)
            assert account is not None
            try:
                answer = _run(db, account, op)
            except HTTPException as exc:
                db.rollback()
                answer = _from_http(exc)
            except ValidationError as exc:
                db.rollback()
                answer = _from_validation(exc)
            if answer.get("code") not in RETRY_CODES:
                db.add(AppOp(account_id=account_id, ref=ref, op=str(op.get("op"))[:16], answer=answer))
                try:
                    db.commit()
                except IntegrityError:
                    # The same ref at the same moment from another request: its answer counts.
                    db.rollback()
                    kept = db.scalar(select(AppOp).where(AppOp.account_id == account_id, AppOp.ref == ref))
                    if kept is not None:
                        answer = kept.answer
            logger.info("App op ref=%s op=%s state=%s code=%s", ref[:8], str(op.get("op"))[:16], answer.get("state"),
                        answer.get("code", ""))
            answers.append({"ref": ref, **answer})
    return answers
