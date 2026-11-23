"""Where the calendars report back to the accounts.

The frame knows no calendars. The features that bring calendars, events, attachments and shares register here what
the accounts side needs from them:

- ``Counter``: how many calendars and events an account owns and how many of its calendars it shares (the question
  before deleting an account), and how many bytes it keeps (the operator's account list).
- ``Listener``: an account was deleted (its shares disappear for the others, with a notice), brought back, or removed
  for good after 30 days (its calendars, events and stored access to iCloud and Google go).
- ``backup_folders``: folders under the data folder that a backup carries next to the database (attachments).

Nothing here ever reads a calendar's name or an event: the operator sees numbers, never content.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Counts:
    calendars: int = 0
    events: int = 0
    shared: int = 0
    bytes: int = 0


Counter = Callable[[Session, int], Counts]
Listener = Callable[[Session, int], None]

_counters: list[Counter] = []
_deleted: list[Listener] = []
_restored: list[Listener] = []
_purged: list[Listener] = []
#: Folder names below the data folder that belong in every backup (``services/backups.py``).
backup_folders: list[str] = []


def register_counter(counter: Counter) -> None:
    _counters.append(counter)


def on_deleted(listener: Listener) -> None:
    _deleted.append(listener)


def on_restored(listener: Listener) -> None:
    _restored.append(listener)


def on_purged(listener: Listener) -> None:
    _purged.append(listener)


def counts(db: Session, account_id: int) -> Counts:
    calendars = events = shared = size = 0
    for counter in _counters:
        found = counter(db, account_id)
        calendars += found.calendars
        events += found.events
        shared += found.shared
        size += found.bytes
    return Counts(calendars, events, shared, size)


def account_deleted(db: Session, account_id: int) -> None:
    for listener in _deleted:
        listener(db, account_id)


def account_restored(db: Session, account_id: int) -> None:
    for listener in _restored:
        listener(db, account_id)


def account_purged(db: Session, account_id: int) -> None:
    for listener in _purged:
        listener(db, account_id)
