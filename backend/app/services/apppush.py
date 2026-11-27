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

Since the shared module geraete 0.1.0 the nudge goes through ``nexgeraete.push.tell`` (kind ``changed``, ``quiet``,
sealed byte for byte as before): only to devices that understand it and have it on (G16, G25), never while the
account is blocked or deleted (its devices rest, G26) or the operator has the app switched off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable

import httpx

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


class Gone(Exception):
    """The relay says the ticket is gone (410)."""


class Failed(Exception):
    """Not delivered now; ``final``: never will be (the request was refused)."""

    def __init__(self, code: str, final: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.final = final


#: The longest text of a notification's fields.
TITLE_MAX = 120
TEXT_MAX = 300


def shows_alerts(device: AppDevice) -> bool:
    """The device gets notifications with text about changes of others (``changes``): it has a ticket, understands
    them (the published app: ``alerts``) and has them on (G16, G25)."""
    from ..vendor import nexgeraete
    from . import geraete

    found = geraete.device_of(device)
    return nexgeraete.push.understood(geraete.CONFIG, found, geraete.KIND_CHANGES) and bool(
        found.kinds.get(geraete.KIND_CHANGES, True))


def alert(device: AppDevice, message: dict[str, object], collapse_mark: str) -> None:
    """A notification with text for one device (TIME-21 through the relay, contract 0.3): the alert's own words are
    the app's (``push_title``/``push_body``); what it shows comes from ``enc``, which its extension opens. Raises
    ``Gone`` (410) or ``Failed``."""
    relay = relay_url()
    if not relay:
        raise Failed("no_relay")
    if not device.push_ticket or not shows_alerts(device):
        raise Failed("no_ticket", final=True)
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        enc = push_crypto.encrypt(text, device.push_key)
    except ValueError as exc:
        raise Failed("bad_key", final=True) from exc
    body: dict[str, object] = {"ticket": device.push_ticket, "enc": enc, "collapse": collapse_mark[:COLLAPSE_MAX]}
    try:
        status = POST(f"{relay}/v1/send", body)
    except (httpx.HTTPError, OSError) as exc:
        raise Failed(type(exc).__name__.lower()[:40]) from exc
    if status == 410:
        raise Gone
    if status in (400, 403, 413):
        raise Failed(f"http_{status}", final=True)
    if status != 200:
        raise Failed(f"http_{status}")


def _server_id() -> str:
    from .appdevices import server_id

    return server_id()


def plaintext(account_id: int) -> bytes:
    return json.dumps({"type": "changed", "account": account_id, "server": _server_id()},
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
    from ..vendor import nexgeraete
    from . import geraete

    sent = 0
    with SessionLocal() as db:
        data = geraete.Store(db)
        for account_id in sorted(due):
            told = nexgeraete.push.tell(data, geraete.CONFIG, account_id, geraete.KIND_CHANGED,
                                        {"type": "changed", "account": account_id, "server": _server_id()},
                                        collapse=collapse(account_id), quiet=True,
                                        send=lambda url, body: POST(url, body))
            sent += told.sent
            if told.gone:
                logger.info("App nudge: the relay forgot a device's ticket account_id=%s", account_id)
            if told.failed:
                logger.warning("App nudge not sent account_id=%s count=%s", account_id, told.failed)
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
