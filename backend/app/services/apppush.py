"""The silent nudge to the nextime app through the push relay (TIME-29, follow-up 3; ``docs/vertrag-app1.md``,
Notifications). The relay is nexnotes' and nextasks' (``https://push.nexapps.dev``, its interface in its README).

Whenever something the account sees changes (an event, a calendar, a share, a collection, its display: whatever wakes
the open pages, ``services/live.py``), every app device of the account with notifications on (a ticket of ``PUT
/push``) gets one silent push: ``quiet`` (no alert, the app wakes in the background and asks ``changes``), with ``enc``
= ``{"type": "changed", "account", "server"}`` encrypted for exactly that device (``push_crypto``, info
``nextime-push-v1``), so that the app knows which of its accounts to sync. The relay and Apple see only the ticket and
the ciphertext.

Changes come in bursts: an account is nudged ``DELAY`` seconds after its first change and then at most every ``GAP``
seconds (the relay takes 30 a minute per device). It is a nudge, not the data: kept in memory only, a restart may drop
one, and the app still syncs when it opens. Only 410 from the relay drops a ticket (the app brings a new one); any
other failure is logged without the ticket and left.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable

import httpx
from sqlalchemy import select, update

from ..config import get_settings
from ..db import SessionLocal
from ..models import AppDevice
from . import live, outbound, push_crypto

logger = logging.getLogger("nextime.app")

#: Seconds from the first change of a burst to its nudge, and the least time between two nudges of an account.
DELAY = 2.0
GAP = 10.0
RELAY_SECONDS = 8.0
#: The relay takes at most this many characters of ``collapse``.
COLLAPSE_MAX = 64

_lock = threading.Lock()
#: Per account: when its nudge is due (monotonic), and when it was last nudged.
_due: dict[int, float] = {}
_last: dict[int, float] = {}
#: What the clock is; the tests set their own.
clock: Callable[[], float] = time.monotonic


def relay_url() -> str:
    """The relay's address; empty: the operator wants nothing to go out."""
    return get_settings().push_relay.strip().rstrip("/")


def note(account_ids: set[int]) -> None:
    """Something these accounts see changed (called by ``live.notify`` after a commit)."""
    if not account_ids or not relay_url():
        return
    now = clock()
    with _lock:
        for account_id in account_ids:
            if account_id not in _due:
                _due[account_id] = max(now + DELAY, _last.get(account_id, -GAP) + GAP)


def register() -> None:
    if note not in live.CHANGED:
        live.CHANGED.append(note)


def _post(url: str, body: dict[str, object]) -> int:
    with httpx.Client(timeout=RELAY_SECONDS, verify=outbound.tls(), trust_env=False) as client:
        return client.post(url, json=body).status_code


#: What sends to the relay; the tests put a recorder in its place (nothing goes into the network).
POST: Callable[[str, dict[str, object]], int] = _post


def plaintext(account_id: int) -> bytes:
    from .appdevices import server_id

    return json.dumps({"type": "changed", "account": account_id, "server": server_id()},
                      separators=(",", ":")).encode("utf-8")


def collapse(account_id: int) -> str:
    """One mark per account and server: a newer nudge replaces one Apple still holds."""
    from .appdevices import server_id

    return f"nt-{server_id()}-{account_id}"[:COLLAPSE_MAX]


def flush(now: float | None = None) -> int:
    """Sends the nudges that are due; how many went out."""
    relay = relay_url()
    now = clock() if now is None else now
    with _lock:
        due = [account_id for account_id, at in _due.items() if at <= now]
        for account_id in due:
            _due.pop(account_id, None)
            _last[account_id] = now
    if not due or not relay:
        return 0
    sent = 0
    with SessionLocal() as db:
        rows = db.execute(select(AppDevice.id, AppDevice.account_id, AppDevice.push_ticket, AppDevice.push_key)
                          .where(AppDevice.account_id.in_(due), AppDevice.push_ticket != "")).all()
        for device_id, account_id, ticket, key in rows:
            try:
                enc = push_crypto.encrypt(plaintext(account_id), key)
            except ValueError:
                logger.warning("App nudge not sent device_id=%s reason=bad_key", device_id)
                continue
            body: dict[str, object] = {"ticket": ticket, "enc": enc, "quiet": True, "collapse": collapse(account_id)}
            try:
                status = POST(f"{relay}/v1/send", body)
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("App nudge not sent device_id=%s reason=%s", device_id, type(exc).__name__.lower())
                continue
            if status == 410:
                db.execute(update(AppDevice).where(AppDevice.id == device_id, AppDevice.push_ticket == ticket)
                           .values(push_ticket="", push_key=""))
                db.commit()
                logger.info("App nudge: the relay forgot the device device_id=%s", device_id)
            elif status == 200:
                sent += 1
            else:
                logger.warning("App nudge not sent device_id=%s reason=http_%s", device_id, status)
    return sent


async def run_forever(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(flush)
        except Exception:  # a nudge never stops the server
            logger.exception("App nudges failed")
        try:
            await asyncio.wait_for(stop.wait(), 1.0)
        except TimeoutError:
            continue


def forget() -> None:
    """For the tests."""
    with _lock:
        _due.clear()
        _last.clear()
