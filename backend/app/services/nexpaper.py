"""A member's own nexpaper in the calendar: its open deadlines (to pay, to cancel by, a contract's end, a warranty, a
follow-up) as all-day entries on the day to act by, to look at only; a click opens the document in nexpaper.

Built as the nextasks connection (``services/nextasks.py``) is, with nexpaper's ``/api/v1`` and the member's key
(``npk_…``) in ``Authorization: Bearer`` and never an ``Origin`` (nexpaper refuses a request with one).

* **``/me`` first.** The key must be one that reads (scope ``read``, else ``nexpaper_key_scope``), and nexpaper must
  know deadlines (1.4.0 on, else ``nexpaper_too_old``).
* **The address is checked at every request** (``services/outbound.py``) with the same rule as nextasks: never the
  machine itself, link-local or a cloud's metadata service, the operator's own network only with
  ``connections_local_network``, ports below 1024 only 80 and 443, no redirect followed, the answer read up to a size
  and within a deadline.
* **Errors are codes.** Whatever goes wrong on the way is ``nexpaper_unreachable`` for the member; the details go to
  the log only. No body of nexpaper's answers reaches the log: titles, senders and amounts stand in them.
* **The key is sealed** with the server's key (``vault``), never logged, never answered.
* **Nothing goes back.** No marking done, no "later": nextime only reads.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, NoReturn
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..errors import detail
from ..models import Account, NexpaperConnection, utcnow
from . import nextasks, outbound, vault, verbinden_store

logger = logging.getLogger("nextime.nexpaper")

SECONDS = 10.0
CONNECT_SECONDS = 3.0
#: The largest answer read: three sections of 500 deadlines fit.
MAX_ANSWER = 4 * 1024 * 1024
MAX_URL = 500
MAX_TOKEN = 500
MAX_TITLE = 300
MAX_SENDER = 200
NOTE_CHARS = 300
#: Deadlines asked per section (nexpaper's largest page).
LIMIT = 500
#: How long the deadlines of an account are kept before asking again.
CACHE_SECONDS = 60
#: The first nexpaper with ``/api/v1/deadlines``.
FIRST_VERSION = (1, 4, 0)
AGENT = "nextime"
LOW_PORTS = (80, 443)
CONNECTS_PER_MINUTE = 5
REQUESTS_PER_MINUTE = 30
AT_ONCE = 2
#: What nexpaper says is to be done by the day.
WHATS = ("pay", "cancel", "ends", "warranty", "follow_up")
KINDS = ("due", "contract_end", "warranty_end", "follow_up")
CURRENCY = re.compile(r"^[A-Z]{3}$")
VERSION = re.compile(r"^(\d{1,4})\.(\d{1,4})(?:\.(\d{1,6}))?")

#: Stand-ins for the tests: how a name is resolved, the transport (``None``: the network), the clock.
RESOLVER = outbound.resolve
TRANSPORT: httpx.BaseTransport | None = None


def clock() -> datetime:
    return utcnow()


class NexpaperError(Exception):
    """A refusal with its code and its HTTP status for the page."""

    def __init__(self, code: str, message: str, status: int = 502, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = status
        self.headers = headers
        #: Only the operator's switch "Verbindungen ins eigene Netz" stood in the way (said to the operator only).
        self.local_only = False

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=detail(self.code, self.message), headers=self.headers)


def _unreachable(reason: str, *args: Any) -> NexpaperError:
    logger.info("nexpaper gave no usable answer: " + reason, *args)
    return NexpaperError("nexpaper_unreachable", "The nexpaper server cannot be reached or gave no usable answer.", 502)


# --- The brake and the cache ----------------------------------------------------------------------------------------

_gate_lock = threading.Lock()
_started: dict[tuple[int, bool], deque[float]] = defaultdict(deque)
_running: dict[int, int] = defaultdict(int)
_cache_lock = threading.Lock()
#: Per account: when asked, the address asked and the deadlines found.
_cache: dict[int, tuple[float, str, list[dict[str, Any]]]] = {}


@contextmanager
def outgoing(account_id: int, *, connecting: bool = False) -> Iterator[None]:
    now = time.monotonic()
    limit = CONNECTS_PER_MINUTE if connecting else REQUESTS_PER_MINUTE
    with _gate_lock:
        seen = _started[(account_id, connecting)]
        while seen and now - seen[0] > 60:
            seen.popleft()
        if len(seen) >= limit:
            raise NexpaperError("slow_down", "Too many requests to nexpaper. Wait a minute.", 429,
                                headers={"Retry-After": str(max(1, int(60 - (now - seen[0])) + 1))})
        if _running[account_id] >= AT_ONCE:
            raise NexpaperError("nexpaper_busy", "Requests to nexpaper are running already. Wait a moment.", 429,
                                headers={"Retry-After": "2"})
        seen.append(now)
        _running[account_id] += 1
    try:
        yield
    finally:
        with _gate_lock:
            _running[account_id] -= 1
            if _running[account_id] <= 0:
                del _running[account_id]


def forget() -> None:
    with _gate_lock:
        _started.clear()
        _running.clear()
    with _cache_lock:
        _cache.clear()


def _drop_cache(account_id: int) -> None:
    with _cache_lock:
        _cache.pop(account_id, None)


# --- The address ----------------------------------------------------------------------------------------------------


def clean_url(raw: str) -> str:
    """The address in its form, blanks at the edges gone: http(s), a host, no credentials, query or fragment; a
    sub-path is allowed. The machine itself by name never."""
    url = (raw or "").strip().rstrip("/")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise NexpaperError("nexpaper_address_invalid", "This is not an address of nexpaper.", 422) from exc
    if (len(url) > MAX_URL or parts.scheme not in ("http", "https") or not parts.hostname or parts.query
            or parts.fragment or parts.username or parts.password or (port is not None and not 0 < port < 65536)):
        raise NexpaperError("nexpaper_address_invalid", "This is not an address of nexpaper.", 422)
    if port is not None and port < 1024 and port not in LOW_PORTS:
        raise NexpaperError("nexpaper_address_port", "nextime asks nexpaper on port 80, 443 or from 1024 on.", 422)
    host = parts.hostname.lower().rstrip(".")
    if host in nextasks.OWN_NAMES or host.endswith(".localhost"):
        raise NexpaperError("nexpaper_address_refused", "nextime never asks this address.", 422)
    return url


def _allowed_with_switch(url: str) -> bool:
    try:
        outbound.pin(url, RESOLVER, nextasks.address_rule(True))
    except (outbound.Refused, outbound.Unreachable):
        return False
    return True


# --- Talking to nexpaper --------------------------------------------------------------------------------------------


@dataclass
class Access:
    url: str
    token: str
    local: bool


def _code(answer: httpx.Response) -> str:
    try:
        data = answer.json()
    except ValueError:
        return ""
    found = data.get("detail") if isinstance(data, dict) else None
    return str(found.get("code") or "") if isinstance(found, dict) else ""


def _refusal(answer: httpx.Response, path: str) -> NoReturn:
    status = answer.status_code
    code = _code(answer)
    logger.info("nexpaper answered HTTP %s (%s) on GET %s", status, code or "-", path)
    if status == 401 and code == "api_off":
        raise NexpaperError("nexpaper_api_off", "The operator of nexpaper has not switched API keys on.", 409)
    if status == 401 and code in ("key_invalid", "token_wrong_kind"):
        raise NexpaperError("nexpaper_token_invalid", "The nexpaper server does not take this key.", 409)
    if status == 403 and code == "key_scope":
        raise NexpaperError("nexpaper_key_scope", "This key of nexpaper is not one that reads.", 409)
    if status == 404:
        raise NexpaperError("nexpaper_too_old", "This nexpaper knows no deadlines yet.", 409)
    if status == 429:
        raise NexpaperError("nexpaper_slow_down", "The nexpaper server asks for fewer requests.", 429)
    raise _unreachable("an answer nextime cannot use (HTTP %s)", status)


def request(access: Access, path: str, *, params: dict[str, str] | None = None) -> Any:
    """``GET <address>/api/v1/<path>`` with the key; the JSON of the answer, every failure as ``NexpaperError``."""
    url = clean_url(access.url) + "/api/v1/" + path
    try:
        place = outbound.pin(url, RESOLVER, nextasks.address_rule(access.local))
    except outbound.Refused as exc:
        failed = _unreachable("an address nextime does not ask (%s)", exc.reason)
        failed.local_only = exc.reason == "local" and _allowed_with_switch(url)
        raise failed from exc
    except outbound.Unreachable as exc:
        raise _unreachable("the name does not resolve") from exc
    headers = {"Authorization": f"Bearer {access.token}", "User-Agent": AGENT, "Accept": "application/json"}
    try:
        with outbound.client(TRANSPORT) as session:
            answer = outbound.send(session, "GET", place, headers, deadline=time.monotonic() + SECONDS,
                                   ticks=time.monotonic, limit=MAX_ANSWER, connect_seconds=CONNECT_SECONDS,
                                   **({"params": params} if params else {}))
    except (outbound.Late, httpx.TimeoutException) as exc:
        raise _unreachable("no answer in time") from exc
    except outbound.TooLarge as exc:
        raise _unreachable("an answer larger than %s bytes", MAX_ANSWER) from exc
    except (outbound.Refused, httpx.HTTPError, OSError) as exc:
        raise _unreachable("no connection (%s)", type(exc).__name__) from exc
    if answer.status_code >= 300:
        # A redirect counts as a refusal: nextime follows none.
        _refusal(answer, path)
    try:
        return answer.json()
    except ValueError as exc:
        raise _unreachable("an answer that is no JSON") from exc


#: What connecting may say besides "cannot be reached": a nexpaper answered in its own words.
SAID_BY_NEXPAPER = ("nexpaper_token_invalid", "nexpaper_api_off", "nexpaper_key_scope", "nexpaper_unreachable")


def _version(value: Any) -> tuple[int, int, int] | None:
    found = VERSION.match(value) if isinstance(value, str) else None
    if not found:
        return None
    return int(found.group(1)), int(found.group(2)), int(found.group(3) or 0)


def check(access: Access) -> dict[str, Any]:
    """``GET /me``: the key holds, it reads, and nexpaper knows deadlines. Only a nexpaper that answers in its own
    form gets its own words; anything else on the address is "cannot be reached"."""
    try:
        me = request(access, "me")
    except NexpaperError as exc:
        if exc.code in SAID_BY_NEXPAPER:
            raise
        raise _unreachable("%s while connecting", exc.code) from exc
    if not isinstance(me, dict) or "scope" not in me:
        raise _unreachable("an answer to /me of another form")
    if me.get("scope") != "read":
        raise NexpaperError("nexpaper_key_scope", "This key of nexpaper is not one that reads.", 409)
    version = _version(me.get("version"))
    if version is None or version < FIRST_VERSION:
        raise NexpaperError("nexpaper_too_old", "This nexpaper knows no deadlines yet.", 409)
    return {"name": " ".join(str(me.get("display_name") or me.get("name") or "").split())[:200],
            "version": ".".join(str(part) for part in version)}


# --- The connection -------------------------------------------------------------------------------------------------


def _place(account_id: int) -> str:
    return f"nexpaper:{account_id}"


#: What ``token_sealed`` holds for a connection made by a click (the shared module verbinden, V18).
BY_CONNECTION = {"connection": "nexpaper"}


def by_connection(row: NexpaperConnection) -> bool:
    return bool((vault.open_(row.token_sealed, _place(row.account_id)) or {}).get("connection"))


def connection_of(db: Session, account_id: int) -> NexpaperConnection | None:
    row = db.get(NexpaperConnection, account_id)
    if row is None and verbinden_store.has_connection(db, account_id, "nexpaper"):
        # nexpaper started the connection (its consent page, then the token here): nextime takes it up on first use.
        try:
            return connected_by_click(db, account_id)
        except verbinden_store.NotConnected:
            return None
    if row is not None and by_connection(row) and not verbinden_store.has_connection(db, account_id, "nexpaper"):
        # The connection made by a click ended (here or in nexpaper): its row goes with it.
        db.delete(row)
        db.commit()
        _drop_cache(account_id)
        return None
    return row


def access_of(db: Session, row: NexpaperConnection) -> Access:
    opened = vault.open_(row.token_sealed, _place(row.account_id)) or {}
    if opened.get("connection"):
        try:
            _base, token = verbinden_store.access(db, row.account_id, "nexpaper")
        except verbinden_store.NotConnected as exc:
            if exc.code == "unreachable":
                raise _unreachable("the connection could not renew its access") from exc
            raise NexpaperError("nexpaper_token_invalid", "The nexpaper server does not take this key.", 409) from exc
        return Access(url=row.url, token=token, local=nextasks.local_allowed(db))
    return Access(url=row.url, token=str(opened.get("token") or ""), local=nextasks.local_allowed(db))


def connected_by_click(db: Session, account_id: int) -> NexpaperConnection:
    """The shared module verbinden made a connection with nexpaper: nextime keeps its row for it as for a key typed in
    by hand and reads who it is there; from then on the deadlines come as before (V18)."""
    base, token = verbinden_store.access(db, account_id, "nexpaper")
    clean = clean_url(base)
    row = db.get(NexpaperConnection, account_id)
    if row is None:
        row = NexpaperConnection(account_id=account_id, url=clean)
        db.add(row)
    row.url = clean
    row.token_sealed = vault.seal(BY_CONNECTION, _place(account_id))
    row.problem = ""
    row.checked_at = clock()
    try:
        with outgoing(account_id, connecting=True):
            found = check(Access(url=clean, token=token, local=nextasks.local_allowed(db)))
        row.name, row.version = found["name"], found["version"]
    except NexpaperError as exc:
        row.problem = exc.code if exc.code == "nexpaper_token_invalid" else ""
    db.commit()
    _drop_cache(account_id)
    logger.info("nexpaper connected by a click account_id=%s host=%s", account_id, urlsplit(clean).hostname)
    return row


def connect(db: Session, account_id: int, url: str, token: str) -> NexpaperConnection:
    """Checks address and key at once and keeps them only when they hold."""
    token = (token or "").strip()
    if not token or len(token) > MAX_TOKEN or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise NexpaperError("nexpaper_token_invalid", "The nexpaper server does not take this key.", 409)
    clean = clean_url(url)
    with outgoing(account_id, connecting=True):
        found = check(Access(url=clean, token=token, local=nextasks.local_allowed(db)))
    row = connection_of(db, account_id)
    if row is None:
        row = NexpaperConnection(account_id=account_id, url=clean)
        db.add(row)
    row.url = clean
    row.token_sealed = vault.seal({"token": token}, _place(account_id))
    row.name = found["name"]
    row.version = found["version"]
    row.problem = ""
    row.checked_at = clock()
    db.commit()
    _drop_cache(account_id)
    logger.info("nexpaper connected account_id=%s host=%s", account_id, urlsplit(clean).hostname)
    return row


def disconnect(db: Session, account_id: int) -> None:
    """The deadlines go from nextime with the key; in nexpaper everything stays."""
    row = connection_of(db, account_id)
    if row is not None:
        if by_connection(row):
            # A connection made by a click ends on both sides (the shared module verbinden, V3).
            verbinden_store.disconnect(db, account_id, "nexpaper")
        db.delete(row)
        db.commit()
    _drop_cache(account_id)
    logger.info("nexpaper disconnected account_id=%s", account_id)


def connection_out(db: Session, row: NexpaperConnection | None) -> dict[str, Any]:
    allowed = nextasks.local_allowed(db)
    if row is None:
        return {"connected": False, "local_allowed": allowed}
    return {"connected": True, "url": row.url, "host": urlsplit(row.url).hostname or "", "name": row.name,
            "version": row.version, "hidden": bool(row.hidden), "problem": row.problem or None,
            "checked_at": row.checked_at.isoformat() if row.checked_at else None, "local_allowed": allowed,
            # Since the shared module verbinden: made by a click (no key of its own) or typed in by hand.
            "by_connection": by_connection(row)}


def set_hidden(db: Session, account_id: int, hidden: bool) -> None:
    db.execute(update(NexpaperConnection).where(NexpaperConnection.account_id == account_id).values(hidden=hidden))
    db.commit()


def _note_problem(db: Session, account_id: int, code: str) -> None:
    """What "Verbindungen" says: a key run out, one that does not read, the API off, too old; cleared when it works."""
    remembered = code if code in ("nexpaper_token_invalid", "nexpaper_key_scope", "nexpaper_too_old",
                                  "nexpaper_api_off") else ""
    db.execute(update(NexpaperConnection).where(NexpaperConnection.account_id == account_id)
               .values(problem=remembered))
    db.commit()
    row = db.get(NexpaperConnection, account_id)
    if row is not None and by_connection(row):
        # The module's card says the same for a connection made by a click (``nexverbinden.reached``, V18).
        told = {"nexpaper_unreachable": "unreachable", "nexpaper_token_invalid": "token_invalid"}.get(code)
        verbinden_store.reached(db, account_id, "nexpaper", told)


# --- The deadlines --------------------------------------------------------------------------------------------------


def _text(value: Any, size: int) -> str:
    return " ".join(value.split())[:size] if isinstance(value, str) else ""


def document_link(item: dict[str, Any], base: str, document_id: int, status: str) -> str:
    """nexpaper's own address of the document when it gives one on the host connected; else the one its interface
    uses: ``/dokument/<id>`` once filed, ``/eingang/<id>`` while it waits in the inbox."""
    given = item.get("url")
    if isinstance(given, str) and len(given) <= 2000:
        try:
            parts, own = urlsplit(given), urlsplit(base)
            same = parts.scheme in ("http", "https") and bool(parts.hostname) and parts.hostname == own.hostname
        except ValueError:
            same = False
        if same:
            return given
    return f"{base.rstrip('/')}/{'eingang' if status == 'inbox' else 'dokument'}/{document_id}"


def _shown(item: Any, base: str) -> dict[str, Any] | None:
    """A deadline of nexpaper as the views draw it."""
    if not isinstance(item, dict):
        return None
    document_id = item.get("document_id")
    what, kind = item.get("what"), item.get("kind")
    if (not isinstance(document_id, int) or isinstance(document_id, bool) or not 0 < document_id < 2**62
            or what not in WHATS or kind not in KINDS):
        return None
    try:
        day = date.fromisoformat(str(item.get("date") or "")[:10])
    except ValueError:
        return None
    status = "inbox" if item.get("status") == "inbox" else "filed"
    amount = item.get("amount")
    currency = item.get("currency")
    return {
        "id": f"{document_id}-{kind}",
        "document_id": document_id,
        "kind": kind,
        "what": what,
        "title": _text(item.get("title"), MAX_TITLE),
        "sender": _text(item.get("sender"), MAX_SENDER),
        "amount": amount if isinstance(amount, int) and not isinstance(amount, bool) and abs(amount) < 10**13 else None,
        "currency": currency if isinstance(currency, str) and CURRENCY.match(currency) else "",
        "note": _text(item.get("note"), NOTE_CHARS) if what == "follow_up" else "",
        "day": day.isoformat(),
        "status": status,
        "link": document_link(item, base, document_id, status),
    }


def _fetch(access: Access, base: str) -> list[dict[str, Any]]:
    data = request(access, "deadlines", params={"limit": str(LIMIT)})
    if not isinstance(data, dict):
        raise _unreachable("an answer of another form")
    found: dict[str, dict[str, Any]] = {}
    for section in ("overdue", "soon", "later"):
        part = data.get(section)
        items = part.get("items") if isinstance(part, dict) else None
        if not isinstance(items, list):
            raise _unreachable("an answer of another form")
        for item in items:
            shown = _shown(item, base)
            if shown is not None:
                found.setdefault(shown["id"], shown)
    return sorted(found.values(), key=lambda one: (one["day"], one["title"].casefold(), one["id"]))


def deadlines_in(db: Session, account: Account, start: date | None, end: date) -> dict[str, Any]:
    """The open deadlines whose day to act by lies from ``start`` (``None``: overdue ones too) to ``end``. One answer
    of nexpaper per account and minute serves every view. Not reachable: none and the problem, quietly."""
    row = connection_of(db, account.id)
    if row is None:
        return {"connected": False, "hidden": False, "problem": None, "deadlines": []}
    problem = None
    with _cache_lock:
        kept = _cache.get(account.id)
    if kept and kept[1] == row.url and time.monotonic() - kept[0] < CACHE_SECONDS:
        found = kept[2]
    else:
        access = access_of(db, row)
        try:
            if not access.token:
                raise NexpaperError("nexpaper_token_invalid", "The nexpaper server does not take this key.", 409)
            with outgoing(account.id):
                found = _fetch(access, row.url)
            with _cache_lock:
                _cache[account.id] = (time.monotonic(), row.url, found)
            if row.problem:
                _note_problem(db, account.id, "")
        except NexpaperError as exc:
            problem = exc.code
            found = []
            _note_problem(db, account.id, problem)
    first, last = (start.isoformat() if start else ""), end.isoformat()
    shown = [one for one in found if first <= one["day"] <= last]
    return {"connected": True, "hidden": bool(row.hidden), "problem": problem, "deadlines": shown}

