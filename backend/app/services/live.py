"""Telling open pages that something changed (TIME-20 AC-4: a change from a phone shows "at once").

A change to a calendar or an event notes the accounts that see it on the database session; once the session commits,
every open page of those accounts hears "changed" over its stream (``routers/live.py``, Server-Sent Events) and asks
again. Nothing about the change travels, only that there was one. A rolled-back change tells nobody.
"""

from __future__ import annotations

import asyncio
import secrets
import threading

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..models import Calendar, CalendarShare

_KEY = "live_accounts"
_lock = threading.Lock()
#: Per account the queues of its open pages, each with the loop it lives in.
_listeners: dict[int, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str]]]] = {}
#: Per account how many changes it heard of since the start, and a mark of this start: together the version a
#: program compares (``GET /api/v1/state``) without asking the database. A restart begins anew under another mark.
_counts: dict[int, int] = {}
_boot = secrets.token_hex(4)


def subscribe(account_id: int) -> tuple[asyncio.AbstractEventLoop, asyncio.Queue[str]]:
    entry = (asyncio.get_running_loop(), asyncio.Queue(maxsize=8))
    with _lock:
        _listeners.setdefault(account_id, set()).add(entry)
    return entry


def unsubscribe(account_id: int, entry: tuple[asyncio.AbstractEventLoop, asyncio.Queue[str]]) -> None:
    with _lock:
        found = _listeners.get(account_id)
        if found is not None:
            found.discard(entry)
            if not found:
                _listeners.pop(account_id, None)


def listening(account_id: int) -> int:
    with _lock:
        return len(_listeners.get(account_id, ()))


def _put(queue: asyncio.Queue[str], message: str) -> None:
    if not queue.full():
        queue.put_nowait(message)


def version(account_id: int) -> str:
    """Changes whenever the account hears of a change to a calendar or event it sees (or to its own list)."""
    with _lock:
        return f"{_boot}-{_counts.get(account_id, 0)}"


def notify(account_ids: set[int], message: str = "changed") -> None:
    with _lock:
        for account_id in account_ids:
            _counts[account_id] = _counts.get(account_id, 0) + 1
        targets = [entry for account_id in account_ids for entry in _listeners.get(account_id, ())]
    for loop, queue in targets:
        try:
            loop.call_soon_threadsafe(_put, queue, message)
        except RuntimeError:  # the loop of a page that went away
            continue


def touch_accounts(db: Session, account_ids: set[int]) -> None:
    """These accounts hear of a change once ``db`` commits."""
    db.info.setdefault(_KEY, set()).update(account_ids)


def touch_calendar(db: Session, calendar_id: int) -> None:
    """Everybody who sees this calendar (its owner and the accounts it is shared with) hears of the change."""
    owner = db.scalar(select(Calendar.owner_id).where(Calendar.id == calendar_id))
    shared = set(db.scalars(select(CalendarShare.account_id).where(CalendarShare.calendar_id == calendar_id)))
    touch_accounts(db, ({owner} if owner is not None else set()) | shared)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    accounts = session.info.pop(_KEY, None)
    if accounts:
        notify(set(accounts))


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_KEY, None)


def forget() -> None:
    """For the tests."""
    with _lock:
        _listeners.clear()

