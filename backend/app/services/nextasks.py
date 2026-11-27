"""A member's own nextasks in the calendar (TIME-25): connecting it, the tasks due, ticking them off.

nextime speaks nextasks' ``/api/v1`` (``nextasks/docs/api.md``) with the member's key (``ntk_…``) in
``Authorization: Bearer`` and never an ``Origin``, as nexnotes and nexmail do.

* **``/me`` first (AC-1, EC-5).** A nextasks whose ``features`` lack ``tasks``, or one without ``/api/v1``, is refused
  while connecting (``nextasks_too_old``). A key that may only read is taken: the tasks show, ticking says why it cannot
  (EC-4).
* **The address is checked at every request** (``services/outbound.py``): resolved once and connected to exactly what
  was checked, never the machine itself, link-local or a cloud's metadata service, an address in the operator's own
  network only when the operator allowed it (``connections_local_network``, as nexnotes and nexmail), ports below 1024
  only 80 and 443, no redirect followed, the answer read up to a size and within a deadline.
* **Errors are codes.** Whatever goes wrong on the way is one code for the member, ``nextasks_unreachable``; the
  details go to the log only, so that nobody maps ports or services from the server's place.
* **The key is sealed** with the server's key (``vault``, as the sources), never logged, never answered (EC-13). No
  body of nextasks' answers reaches the log: task titles stand in them.
* **"Deine und dir zugewiesene":** nextasks' API does not name who made a task. The tasks kept are those assigned to
  the member and those assigned to nobody; a task only for others never shows (out of scope "Aufgaben anderer").
* **Ticking waits** (EC-3, EC-12): each tap is a row of ``task_ticks`` with the state wanted; sent at once, and while
  nextasks is away again every round until it answers, also after a restart. A refusal for good (gone, no right, a key
  that only reads) ends it with a notice, once.
"""

from __future__ import annotations

import ipaddress
import json as jsonlib
import logging
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any, NoReturn
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from dateutil.rrule import rrulestr
from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import detail
from ..models import Account, NextasksConnection, TaskTick, utcnow
from . import display, ical, outbound, settings_service, vault, verbinden_store

logger = logging.getLogger("nextime.nextasks")

#: A person waits in front of the page: short.
SECONDS = 10.0
CONNECT_SECONDS = 3.0
#: The largest answer read: a page of 500 tasks fits many times.
MAX_ANSWER = 2 * 1024 * 1024
MAX_URL = 500
MAX_TOKEN = 500
MAX_TITLE = 300
#: Pages of tasks read at most per question (500 each).
MAX_PAGES = 4
PAGE = 500
#: How long the tasks of an account are kept before asking again.
CACHE_SECONDS = 60
#: The features a nextasks must name in ``/me``.
FEATURES = ("tasks",)
AGENT = "nextime"
LOW_PORTS = (80, 443)
CONNECTS_PER_MINUTE = 5
REQUESTS_PER_MINUTE = 30
AT_ONCE = 2
LOCAL_SWITCH = "connections_local_network"
#: Between tries of a waiting tick.
RETRY_SECONDS = 30
#: How long a tick being sent is held by whoever claimed it: longer than its requests can take (3 × 10 s). A claim
#: left behind by a stopped process is taken up again after it.
LEASE_SECONDS = 120
TASK_ID = re.compile(r"^[A-Za-z0-9-]{1,100}$")
#: A rule of repetition as nextasks writes it (its subset of RRULE).
RULE = re.compile(r"^FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)(;[A-Z]+=[A-Z0-9,+-]{1,60}){0,6}$")
#: How far ahead one answer of nextasks reaches: the lists beside the views want the coming weeks.
HORIZON_DAYS = 92
#: Areas and lists change seldom; asked again after this.
AREAS_SECONDS = 300
MAX_AREAS = 200
MAX_PREVIEWS = 62
NOTE_LINES = 3
NOTE_CHARS = 300
#: The symbol and colour of a space or list as nextasks stores them (its own names, or an own colour ``#rrggbb``).
SYMBOL = re.compile(r"^[a-z0-9-]{1,40}$")
COLOR = re.compile(r"^([a-z]{1,20}|#[0-9a-fA-F]{6})$")
#: Done tasks show struck through in the views from this many days back (or from the first day a view shows).
DONE_DAYS = 31

LOCAL_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7", "fec0::/10",
))
OWN_NAMES = ("localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback")

#: Stand-ins for the tests: how a name is resolved, the transport (``None``: the network), the clock.
RESOLVER = outbound.resolve
TRANSPORT: httpx.BaseTransport | None = None


def clock() -> datetime:
    return utcnow()


class NextasksError(Exception):
    """A refusal with its code, its HTTP status for the page and values for the sentence."""

    def __init__(self, code: str, message: str, status: int = 502, *, headers: dict[str, str] | None = None,
                 final: bool = False, **values: Any) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = status
        self.headers = headers
        #: nextasks refused for good (a tick that waits stops waiting).
        self.final = final
        self.values = values
        #: Only the operator's switch "Verbindungen ins eigene Netz" stood in the way: the operator may be told so,
        #: a member never (TIME-25 EC-6, review finding 3).
        self.local_only = False

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=detail(self.code, self.message, **self.values),
                             headers=self.headers)


def _unreachable(reason: str, *args: Any) -> NextasksError:
    logger.info("nextasks gave no usable answer: " + reason, *args)
    return NextasksError("nextasks_unreachable", "The nextasks server cannot be reached or gave no usable answer.", 502)


# --- The brake ------------------------------------------------------------------------------------------------------

_gate_lock = threading.Lock()
_started: dict[tuple[int, bool], deque[float]] = defaultdict(deque)
_running: dict[int, int] = defaultdict(int)


@contextmanager
def outgoing(account_id: int, *, connecting: bool = False) -> Iterator[None]:
    now = time.monotonic()
    limit = CONNECTS_PER_MINUTE if connecting else REQUESTS_PER_MINUTE
    with _gate_lock:
        seen = _started[(account_id, connecting)]
        while seen and now - seen[0] > 60:
            seen.popleft()
        if len(seen) >= limit:
            raise NextasksError("slow_down", "Too many requests to nextasks. Wait a minute.", 429,
                                headers={"Retry-After": str(max(1, int(60 - (now - seen[0])) + 1))})
        if _running[account_id] >= AT_ONCE:
            raise NextasksError("nextasks_busy", "Requests to nextasks are running already. Wait a moment.", 429,
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


_cache_lock = threading.Lock()
_cache: dict[int, tuple[float, str, str, list[dict[str, Any]], dict[str, Any], str]] = {}
_world_cache: dict[int, tuple[float, dict[str, Any]]] = {}


def forget() -> None:
    with _gate_lock:
        _started.clear()
        _running.clear()
    with _cache_lock:
        _cache.clear()
        _world_cache.clear()


def _drop_cache(account_id: int) -> None:
    with _cache_lock:
        _cache.pop(account_id, None)
        _world_cache.pop(account_id, None)


# --- The address ----------------------------------------------------------------------------------------------------


def clean_url(raw: str) -> str:
    """The address in its form, blanks at the edges gone (EC-6): http(s), a host, no credentials, query or fragment;
    a sub-path is allowed. The machine itself by name never."""
    url = (raw or "").strip().rstrip("/")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise NextasksError("nextasks_address_invalid", "This is not an address of nextasks.", 422) from exc
    if (len(url) > MAX_URL or parts.scheme not in ("http", "https") or not parts.hostname or parts.query
            or parts.fragment or parts.username or parts.password or (port is not None and not 0 < port < 65536)):
        raise NextasksError("nextasks_address_invalid", "This is not an address of nextasks.", 422)
    if port is not None and port < 1024 and port not in LOW_PORTS:
        raise NextasksError("nextasks_address_port", "nextime asks nextasks on port 80, 443 or from 1024 on.", 422)
    host = parts.hostname.lower().rstrip(".")
    if host in OWN_NAMES or host.endswith(".localhost"):
        raise NextasksError("nextasks_address_refused", "nextime never asks this address.", 422)
    return url


def _local(ip: outbound.IP) -> bool:
    return any(ip in net for net in LOCAL_NETWORKS if ip.version == net.version)


def address_rule(local_allowed: bool) -> Any:
    def rule(ip: outbound.IP) -> None:
        for one in outbound.inner(ip):
            if one.is_loopback:
                raise outbound.Refused("refused")
            if outbound.public(str(one)):
                continue
            if _local(one):
                if not local_allowed:
                    raise outbound.Refused("local")
                continue
            raise outbound.Refused("refused")

    return rule


def local_allowed(db: Session) -> bool:
    return bool(settings_service.get(db, LOCAL_SWITCH))


# --- Talking to nextasks --------------------------------------------------------------------------------------------


@dataclass
class Access:
    url: str
    token: str
    local: bool
    #: A connection made by a click (the shared module verbinden): its account, to renew the access once on a 401.
    connection_of: int | None = None


def _code(answer: httpx.Response) -> str:
    try:
        data = answer.json()
    except ValueError:
        return ""
    found = data.get("detail") if isinstance(data, dict) else None
    return str(found.get("code") or "") if isinstance(found, dict) else ""


def _refusal(answer: httpx.Response, method: str, path: str) -> NoReturn:
    status = answer.status_code
    code = _code(answer)
    logger.info("nextasks answered HTTP %s (%s) on %s %s", status, code or "-", method, path.split("/")[0])
    if status == 401 and code == "api_off":
        raise NextasksError("nextasks_api_off", "The operator of nextasks has switched API keys off.", 409)
    # ``account_resting``: the module's word for a connection made by a click whose account rests there (V18).
    if status == 401 and code in ("token_invalid", "account_resting"):
        raise NextasksError("nextasks_token_invalid", "The nextasks server does not take this key.", 409, final=True)
    if status == 403 and code == "token_reads_only":
        raise NextasksError("nextasks_read_only", "This key of nextasks may only read.", 409, final=True)
    if status == 403:
        raise NextasksError("nextasks_not_allowed", "The nextasks server does not let this key change the task.", 409,
                            final=True)
    if status == 404:
        raise NextasksError("nextasks_not_found", "The nextasks server does not know this.", 404, final=True)
    if status == 429:
        raise NextasksError("nextasks_slow_down", "The nextasks server asks for fewer requests.", 429)
    raise _unreachable("an answer nextime cannot use (HTTP %s)", status)


def _allowed_with_switch(url: str) -> bool:
    """Whether the switch for the own network alone keeps nextime from this address: every address of the name is
    one nextime would ask with it on (never the machine itself, link-local or a cloud's metadata)."""
    try:
        outbound.pin(url, RESOLVER, address_rule(True))
    except (outbound.Refused, outbound.Unreachable):
        return False
    return True


def _request_once(access: Access, method: str, path: str, *, params: dict[str, str] | None = None,
            body: dict[str, Any] | None = None, seconds: float = SECONDS) -> Any:
    """``<address>/api/v1/<path>`` with the key; the JSON of the answer, every failure as ``NextasksError``."""
    url = clean_url(access.url) + "/api/v1/" + path
    try:
        place = outbound.pin(url, RESOLVER, address_rule(access.local))
    except outbound.Refused as exc:
        # Where a name points stays in the operator's log: outside it is "cannot be reached" like any other failure
        # on the way, so nobody maps the server's network through this form.
        failed = _unreachable("an address nextime does not ask (%s)", exc.reason)
        failed.local_only = exc.reason == "local" and _allowed_with_switch(url)
        raise failed from exc
    except outbound.Unreachable as exc:
        raise _unreachable("the name does not resolve") from exc
    headers = {"Authorization": f"Bearer {access.token}", "User-Agent": AGENT, "Accept": "application/json"}
    sent: dict[str, Any] = {}
    if params:
        sent["params"] = params
    if body is not None:
        sent["content"] = jsonlib.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        with outbound.client(TRANSPORT) as session:
            answer = outbound.send(session, method, place, headers, deadline=time.monotonic() + seconds,
                                   ticks=time.monotonic, limit=MAX_ANSWER, connect_seconds=CONNECT_SECONDS, **sent)
    except (outbound.Late, httpx.TimeoutException) as exc:
        raise _unreachable("no answer in time") from exc
    except outbound.TooLarge as exc:
        raise _unreachable("an answer larger than %s bytes", MAX_ANSWER) from exc
    except (outbound.Refused, httpx.HTTPError, OSError) as exc:
        raise _unreachable("no connection (%s)", type(exc).__name__) from exc
    if answer.status_code == 204:
        return None
    if answer.status_code >= 300:
        # A redirect counts as a refusal: nextime follows none.
        _refusal(answer, method, path)
    try:
        return answer.json()
    except ValueError as exc:
        raise _unreachable("an answer that is no JSON") from exc


def request(access: Access, *args: Any, **kwargs: Any) -> Any:
    """One request to nextasks; over a connection made by a click a 401 asks once more with a renewed access (an access
    can end before its time, V18), and only then counts as refused."""
    try:
        return _request_once(access, *args, **kwargs)
    except NextasksError as exc:
        if exc.code != "nextasks_token_invalid" or access.connection_of is None:
            raise
        try:
            token = verbinden_store.renewed(access.connection_of, "nextasks", failed=access.token)
        except verbinden_store.NotConnected:
            raise exc from None
        return _request_once(replace(access, token=token), *args, **kwargs)


#: What connecting may say besides "cannot be reached": a nextasks answered in its own words.
SAID_BY_NEXTASKS = ("nextasks_token_invalid", "nextasks_api_off")


def check(access: Access) -> dict[str, Any]:
    """``GET /me``: the key holds, and nextasks offers what nextime needs (AC-1, EC-5). Only a nextasks that answers
    in its own form gets its own words (a key refused, the API off, another API); anything else on the address, a
    page of another service, a 404 or a 403, is "cannot be reached", so the form tells nothing about what runs where."""
    try:
        me = request(access, "GET", "me", seconds=SECONDS)
    except NextasksError as exc:
        if exc.code in SAID_BY_NEXTASKS or exc.code == "nextasks_unreachable":
            raise
        raise _unreachable("%s while connecting", exc.code) from exc
    features = me.get("features") if isinstance(me, dict) else None
    if not isinstance(features, list):
        raise _unreachable("an answer to /me without features")
    if not all(feature in features for feature in FEATURES):
        raise NextasksError("nextasks_too_old", "This nextasks speaks another API than nextime.", 409)
    zone = str(me.get("time_zone") or "")
    try:
        ZoneInfo(zone)
    except (ValueError, KeyError):
        zone = ""
    return {"name": str(me.get("display_name") or me.get("name") or "")[:200], "time_zone": zone,
            "level": "write" if me.get("level") == "write" else "read"}


# --- The connection (AC-1, AC-5, AC-8, EC-4 to EC-6, EC-13) ---------------------------------------------------------


def _place(account_id: int) -> str:
    return f"nextasks:{account_id}"


#: What ``token_sealed`` holds for a connection made by a click (the shared module verbinden, V18): no key of its own;
#: the access comes from the module's connection each time. The row keeps nextime's choices as for a key.
BY_CONNECTION = {"connection": "nextasks"}


def by_connection(row: NextasksConnection) -> bool:
    return bool((vault.open_(row.token_sealed, _place(row.account_id)) or {}).get("connection"))


def connection_of(db: Session, account_id: int) -> NextasksConnection | None:
    row = db.get(NextasksConnection, account_id)
    if row is None and verbinden_store.has_connection(db, account_id, "nextasks"):
        # nextasks started the connection (its consent page, then the token here): nextime takes it up on first use.
        try:
            return connected_by_click(db, account_id)
        except verbinden_store.NotConnected:
            return None
    if row is not None and by_connection(row) and not verbinden_store.has_connection(db, account_id, "nextasks"):
        # The connection made by a click ended (here or in nextasks): its row goes with it, as after "Trennen".
        db.delete(row)
        db.query(TaskTick).filter(TaskTick.account_id == account_id).delete()
        db.commit()
        _drop_cache(account_id)
        return None
    return row


def access_of(db: Session, row: NextasksConnection) -> Access:
    opened = vault.open_(row.token_sealed, _place(row.account_id)) or {}
    if opened.get("connection"):
        return Access(url=row.url, token=_connection_token(db, row.account_id), local=local_allowed(db),
                      connection_of=row.account_id)
    return Access(url=row.url, token=str(opened.get("token") or ""), local=local_allowed(db))


def _connection_token(db: Session, account_id: int) -> str:
    """A valid access of the connection made by a click; its refusals in the words of a key (EC-4, EC-5)."""
    try:
        _base, token = verbinden_store.access(db, account_id, "nextasks")
    except verbinden_store.NotConnected as exc:
        if exc.code == "unreachable":
            raise _unreachable("the connection could not renew its access") from exc
        raise NextasksError("nextasks_token_invalid", "The nextasks server does not take this key.", 409) from exc
    return token


def connected_by_click(db: Session, account_id: int) -> NextasksConnection:
    """The shared module verbinden made a connection with nextasks (``on_connected``): nextime keeps its row for it as
    for a key typed in by hand (same address: the choices of before stay), reads who it is there, and from then on
    everything goes as before (V18). It may run more than once for a connection, and also where nextasks started it,
    right after nextime answered its token request (verbinden 0.2.1, BS6): nextasks may still be saving its side then
    and refuse this first question. Such a refusal is not kept as the key's problem (the next use asks again and says
    it, if it stays); the row and the choices stand either way."""
    base, token = verbinden_store.access(db, account_id, "nextasks")
    clean = clean_url(base)
    row = db.get(NextasksConnection, account_id)
    if row is None:
        row = NextasksConnection(account_id=account_id, url=clean)
        db.add(row)
    elif row.url != clean:
        db.query(TaskTick).filter(TaskTick.account_id == account_id,
                                  TaskTick.state.in_(("waiting", "sending"))).delete()
    row.url = clean
    row.token_sealed = vault.seal(BY_CONNECTION, _place(account_id))
    row.problem = ""
    row.checked_at = clock()
    try:
        with outgoing(account_id, connecting=True):
            found = check(Access(url=clean, token=token, local=local_allowed(db)))
        row.name, row.time_zone, row.level = found["name"], found["time_zone"], found["level"]
    except NextasksError as exc:
        row.problem = exc.code if exc.code in ("nextasks_too_old", "nextasks_api_off") else ""
    db.commit()
    _drop_cache(account_id)
    logger.info("nextasks connected by a click account_id=%s host=%s", account_id, urlsplit(clean).hostname)
    return row


def connect(db: Session, account_id: int, url: str, token: str) -> NextasksConnection:
    """Checks address and key at once and keeps them only when they hold (AC-1, EC-6)."""
    token = (token or "").strip()
    if not token or len(token) > MAX_TOKEN or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise NextasksError("nextasks_token_invalid", "The nextasks server does not take this key.", 409)
    clean = clean_url(url)
    with outgoing(account_id, connecting=True):
        found = check(Access(url=clean, token=token, local=local_allowed(db)))
    row = connection_of(db, account_id)
    if row is None:
        row = NextasksConnection(account_id=account_id, url=clean)
        db.add(row)
    elif row.url != clean:
        # Another nextasks: its tasks have other ids; ticks waiting for the old one would only be refused there.
        db.query(TaskTick).filter(TaskTick.account_id == account_id,
                                  TaskTick.state.in_(("waiting", "sending"))).delete()
    row.url = clean
    row.token_sealed = vault.seal({"token": token}, _place(account_id))
    row.name = found["name"]
    row.time_zone = found["time_zone"]
    row.level = found["level"]
    row.problem = ""
    row.checked_at = clock()
    db.commit()
    _drop_cache(account_id)
    logger.info("nextasks connected account_id=%s host=%s level=%s", account_id, urlsplit(clean).hostname, row.level)
    return row


def disconnect(db: Session, account_id: int) -> None:
    """The tasks go from nextime with the key; in nextasks everything stays (AC-8)."""
    row = connection_of(db, account_id)
    if row is not None:
        if by_connection(row):
            # A connection made by a click ends on both sides (the shared module verbinden, V3).
            verbinden_store.disconnect(db, account_id, "nextasks")
        db.delete(row)
    db.query(TaskTick).filter(TaskTick.account_id == account_id).delete()
    db.commit()
    _drop_cache(account_id)
    logger.info("nextasks disconnected account_id=%s", account_id)


def connection_out(db: Session, row: NextasksConnection | None) -> dict[str, Any]:
    allowed = local_allowed(db)
    if row is None:
        return {"connected": False, "local_allowed": allowed}
    return {"connected": True, "url": row.url, "host": urlsplit(row.url).hostname or "", "name": row.name,
            "level": row.level, "hidden": bool(row.hidden), "problem": row.problem or None,
            "checked_at": row.checked_at.isoformat() if row.checked_at else None, "local_allowed": allowed,
            # Since the shared module verbinden: made by a click (no key of its own) or typed in by hand.
            "by_connection": by_connection(row)}


def _note_problem(db: Session, account_id: int, code: str) -> None:
    """What "Verbindungen" says (EC-4, EC-5): a key run out, one that only reads, another API; cleared when it works."""
    remembered = code if code in ("nextasks_token_invalid", "nextasks_read_only", "nextasks_too_old",
                                  "nextasks_api_off") else ""
    db.execute(update(NextasksConnection).where(NextasksConnection.account_id == account_id)
               .values(problem=remembered))
    db.commit()
    row = db.get(NextasksConnection, account_id)
    if row is not None and by_connection(row):
        # The module's card says the same for a connection made by a click (``nexverbinden.reached``, V18).
        told = {"nextasks_unreachable": "unreachable", "nextasks_token_invalid": "token_invalid"}.get(code)
        verbinden_store.reached(db, account_id, "nextasks", told)


# --- The tasks (AC-2, AC-4, AC-6, EC-1, EC-2, EC-8, EC-9, EC-11) ----------------------------------------------------


def _link(value: Any) -> str:
    if isinstance(value, str) and len(value) <= 2000 and urlsplit(value).scheme in ("http", "https"):
        return value
    return ""


def _fetch(access: Access, params: dict[str, str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    cursor = ""
    for _page in range(MAX_PAGES):
        asked = {**params, "limit": str(PAGE)}
        if cursor:
            asked["cursor"] = cursor
        data = request(access, "GET", "tasks", params=asked)
        rows = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise _unreachable("an answer of another form")
        found.extend(row for row in rows if isinstance(row, dict))
        cursor = str(data.get("next_cursor") or "")
        if not cursor:
            break
    return found


def _text(value: Any, size: int) -> str:
    return " ".join(value.split())[:size] if isinstance(value, str) else ""


def _task_link(task: dict[str, Any], base: str) -> str:
    """nextasks' own link; without its public address the one its interface uses, on the address connected."""
    found = _link(task.get("link"))
    list_id = task.get("list_id")
    if found or not isinstance(list_id, str) or not TASK_ID.match(list_id):
        return found
    return _link(f"{base.rstrip('/')}/lists/{list_id}?task={task.get('id')}")


def _notes(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    lines = [" ".join(line.split()) for line in value.splitlines() if line.strip()]
    return "\n".join(lines[:NOTE_LINES])[:NOTE_CHARS]


def _person(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return _text(value.get("display_name") or value.get("name"), 100)


def _shown(task: dict[str, Any], source_zone: str, zone: str, world: dict[str, Any] | None = None,
           base: str = "") -> dict[str, Any] | None:
    """A task of nextasks as the views draw it: its day and time in the member's zone (EC-11), its title as text, its
    area and section, the first lines of its note, who it is assigned to, its rule of repetition."""
    task_id = task.get("id")
    due = task.get("due_date")
    if not isinstance(task_id, str) or not TASK_ID.match(task_id) or not isinstance(due, str):
        return None
    try:
        day = date.fromisoformat(due[:10])
    except ValueError:
        return None
    clock_text = task.get("due_time") if isinstance(task.get("due_time"), str) else None
    time_shown = None
    if clock_text and re.fullmatch(r"\d{2}:\d{2}", clock_text):
        hour, minute = int(clock_text[:2]), int(clock_text[3:])
        if hour > 23 or minute > 59:
            return None
        moment = ical.at_wall(datetime.combine(day, dtime(hour, minute)), source_zone)
        wall = ical.local_wall(moment, zone) if zone else moment
        day, time_shown = wall.date(), wall.strftime("%H:%M")
    title = " ".join(str(task.get("title") or "").split())[:MAX_TITLE]
    place = (world or {}).get("lists", {}).get(task.get("list_id"), {})
    section = place.get("sections", {}).get(task.get("section_id"), "")
    rule = task.get("repeat") if isinstance(task.get("repeat"), str) and RULE.match(task["repeat"]) else ""
    return {"id": task_id, "title": title, "day": day.isoformat(), "time": time_shown,
            "link": _task_link(task, base), "repeat": rule, "area": place.get("area", ""),
            "list": task["list_id"] if isinstance(task.get("list_id"), str) and task["list_id"] in
            (world or {}).get("lists", {}) else "",
            "area_name": place.get("area_name", ""), "list_name": place.get("name", ""), "section": section,
            "icon": place.get("icon", ""), "color": place.get("color", ""),
            "notes": _notes(task.get("notes_text")), "completed": task.get("completed") is True,
            "assignees": [name for name in (_person(one) for one in task.get("assignees") or []) if name]}


def _coming(task: dict[str, Any], start: date | None, end: date) -> list[str]:
    """The days a repeating task comes again after its due day, up to ``end``: previews only (nextasks moves the task
    on when it is ticked off). The rule counts from the due day."""
    due = date.fromisoformat(task["day"])
    try:
        rule = rrulestr(task["repeat"], dtstart=datetime.combine(due, dtime()))
    except (ValueError, TypeError):
        return []
    first = max(due + timedelta(days=1), start or due)
    days: list[str] = []
    for moment in rule.xafter(datetime.combine(first, dtime()), count=MAX_PREVIEWS, inc=True):
        if moment.date() > end:
            break
        days.append(moment.date().isoformat())
    return days


def _ids(raw: str) -> list[str]:
    try:
        found = jsonlib.loads(raw) if raw else []
    except ValueError:
        return []
    return [one for one in found if isinstance(one, str)] if isinstance(found, list) else []


def _world(access: Access, account_id: int) -> dict[str, Any]:
    """The areas of nextasks (its spaces) and its lists with their sections, kept a few minutes. A nextasks without
    them (older than its ``/spaces``) shows its tasks without areas."""
    with _cache_lock:
        kept = _world_cache.get(account_id)
    if kept and time.monotonic() - kept[0] < AREAS_SECONDS:
        return kept[1]
    areas: dict[str, dict[str, Any]] = {}
    lists: dict[str, dict[str, Any]] = {}
    try:
        spaces = request(access, "GET", "spaces")
        rows = request(access, "GET", "lists")
    except NextasksError as exc:
        if exc.code != "nextasks_not_found":
            raise
        spaces, rows = {}, {}
    named: dict[str, dict[str, Any]] = {}
    for one in (spaces.get("spaces") if isinstance(spaces, dict) else None) or []:
        if isinstance(one, dict) and isinstance(one.get("id"), str) and TASK_ID.match(one["id"]):
            named[one["id"]] = {"id": one["id"], "name": _text(one.get("name"), 100),
                                "icon": _mark(one.get("icon"), SYMBOL), "color": _mark(one.get("color"), COLOR)}
    # The order of nextasks' sidebar: ``/lists`` comes in it, spaces without a list after them.
    for one in (rows.get("lists") if isinstance(rows, dict) else None) or []:
        if not isinstance(one, dict) or not isinstance(one.get("id"), str) or not TASK_ID.match(one["id"]):
            continue
        area = one.get("space_id") if isinstance(one.get("space_id"), str) else ""
        if area and TASK_ID.match(area) and area not in areas:
            areas[area] = named.get(area) or {"id": area, "name": _text(one.get("space_name"), 100), "icon": "",
                                              "color": ""}
        sections = {section["id"]: _text(section.get("name"), 100) for section in one.get("sections") or []
                    if isinstance(section, dict) and isinstance(section.get("id"), str)}
        lists[one["id"]] = {"id": one["id"], "area": area if area in areas else "",
                            "area_name": areas.get(area, {}).get("name", ""),
                            "name": _text(one.get("name"), 100), "sections": sections,
                            "icon": _mark(one.get("icon"), SYMBOL), "color": _mark(one.get("color"), COLOR)}
    for area_id, area in named.items():
        areas.setdefault(area_id, area)
    world = {"areas": dict(list(areas.items())[:MAX_AREAS]), "lists": lists}
    with _cache_lock:
        _world_cache[account_id] = (time.monotonic(), world)
    return world


def _choice(row: NextasksConnection) -> dict[str, Any]:
    """What the member chose in "Verbindungen": whole areas (later lists included) and single lists. Unset: every area
    whole, a new one too (``all``, less those in ``except``)."""
    try:
        found = jsonlib.loads(row.task_choice) if row.task_choice else {}
    except ValueError:
        found = {}
    if not isinstance(found, dict) or not found:
        found = {"all": True}
    return {"all": found.get("all") is True,
            **{name: [one for one in found.get(name) or [] if isinstance(one, str)]
               for name in ("except", "areas", "lists")}}


def _rows(row: NextasksConnection, world: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The rows of the sidebar in nextasks' order: a whole area, or the single lists chosen of an area that is not;
    and for each list the row its tasks belong to."""
    choice = _choice(row)
    whole = {area for area in world["areas"]
             if (choice["all"] and area not in choice["except"]) or area in choice["areas"]}
    single = set(choice["lists"])
    base = row.url.rstrip("/")
    rows: list[dict[str, Any]] = []
    of_list: dict[str, str] = {}
    for area_id, area in world["areas"].items():
        lists = [one for one in world["lists"].values() if one["area"] == area_id]
        if area_id in whole:
            rows.append({"id": area_id, "kind": "area", "name": area["name"], "area_name": "", "icon": area["icon"],
                         "color": area["color"], "link": _link(base)})
            of_list.update({one["id"]: area_id for one in lists})
            continue
        for one in lists:
            if one["id"] in single:
                rows.append({"id": one["id"], "kind": "list", "name": one["name"], "area_name": area["name"],
                             "icon": one["icon"], "color": one["color"], "link": _link(f"{base}/lists/{one['id']}")})
                of_list[one["id"]] = one["id"]
    return rows[:MAX_AREAS], of_list


def _mark(value: Any, rule: re.Pattern[str]) -> str:
    return value if isinstance(value, str) and rule.match(value) else ""


def _hidden_areas(row: NextasksConnection, known: list[str]) -> set[str]:
    # "Meine Aufgaben" hidden from before the areas: every row hidden, until one eye is opened.
    return set(known) if row.hidden else set(_ids(row.areas_hidden))


def tasks_in(db: Session, account: Account, start: date | None, end: date, *, previews: bool = True
             ) -> dict[str, Any]:
    """The open tasks due from ``start`` (``None``: overdue ones too) to ``end`` that are the member's own (assigned to
    nobody) or assigned to it, subtasks included, out of the areas chosen (TIME-25 areas). A repeating task comes
    again as a preview on its next days (``previews``). One answer of nextasks per account and minute reaches some
    months ahead and serves every view up to that day (EC-2); a view beyond asks anew. Not reachable: no tasks and the
    problem, quietly (EC-1)."""
    row = connection_of(db, account.id)
    if row is None:
        return {"connected": False, "tasks": [], "problem": None, "hidden": False, "areas": []}
    zone_name = str(getattr(display.zone_of(account), "key", "") or "UTC")
    source_zone = row.time_zone or zone_name
    with _cache_lock:
        kept = _cache.get(account.id)
    problem = None
    world: dict[str, Any] = {"areas": {}, "lists": {}}
    today = clock().date()
    done_from = min(start or today, today - timedelta(days=DONE_DAYS))
    if (kept and kept[1] == zone_name and kept[2] >= end.isoformat() and kept[5] <= done_from.isoformat()
            and time.monotonic() - kept[0] < CACHE_SECONDS):
        tasks, world = kept[3], kept[4]
    else:
        access = access_of(db, row)
        horizon = max(end, today + timedelta(days=HORIZON_DAYS))
        try:
            if not access.token:
                raise NextasksError("nextasks_token_invalid", "The nextasks server does not take this key.", 409)
            before = (horizon + timedelta(days=1)).isoformat()
            with outgoing(account.id):
                world = _world(access, account.id)
                mine = _fetch(access, {"state": "open", "due_before": before, "assigned": "me", "subtasks": "true"})
                every = _fetch(access, {"state": "open", "due_before": before, "subtasks": "true"})
                # Done ones of the days around, struck through in the views (a repeating task's done copy too).
                after = (done_from - timedelta(days=1)).isoformat()
                mine += _fetch(access, {"state": "completed", "due_after": after, "due_before": before,
                                        "assigned": "me", "subtasks": "true"})
                every += _fetch(access, {"state": "completed", "due_after": after, "due_before": before,
                                         "subtasks": "true"})
            chosen: dict[str, dict[str, Any]] = {}
            assigned = {task.get("id") for task in mine}
            for task in [*mine, *every]:
                if task.get("id") in assigned or not task.get("assignees"):
                    shown = _shown(task, source_zone, zone_name, world, row.url)
                    if shown is not None:
                        chosen.setdefault(shown["id"], shown)
            tasks = sorted(chosen.values(), key=lambda one: (one["day"], one["time"] or "", one["title"].casefold()))
            with _cache_lock:
                _cache[account.id] = (time.monotonic(), zone_name, horizon.isoformat(), tasks, world,
                                      done_from.isoformat())
            if row.problem:
                _note_problem(db, account.id, "")
        except NextasksError as exc:
            # ``/tasks`` not found: a nextasks that changed its API after an update (EC-5).
            problem = "nextasks_too_old" if exc.code == "nextasks_not_found" else exc.code
            tasks = []
            _note_problem(db, account.id, problem)
    rows, of_list = _rows(row, world)
    hidden = _hidden_areas(row, [one["id"] for one in rows])
    areas = [{**one, "hidden": one["id"] in hidden} for one in rows]
    ticks = _latest_ticks(db, account.id)
    first, last = (start.isoformat() if start else ""), end.isoformat()
    out = []
    for found in tasks:
        # A nextasks without areas: every task, without a row.
        if world["areas"] and found["list"] not in of_list:
            continue
        # Done ones only for the views that show days (Day, Week, Month), never as overdue or coming.
        if found["completed"] and not previews:
            continue
        task = {**found, "row": of_list.get(found["list"], "")}
        tick = ticks.get(task["id"])
        # A repeating task ticked here moved on in nextasks: its done copy stands on the old day, it is open again.
        ticked = bool(tick and tick.done and tick.state in ("waiting", "sent") and not tick.copy_id)
        if first <= task["day"] <= last:
            out.append({**task, "done": task["completed"] or ticked,
                        "waiting": bool(tick and tick.state == "waiting"), "preview": False})
        if previews and task["repeat"] and not task["completed"]:
            out.extend({**task, "day": day, "done": False, "waiting": False, "preview": True}
                       for day in _coming(task, start, end))
    out.sort(key=lambda one: (one["day"], one["time"] or "", one["title"].casefold()))
    return {"connected": True, "hidden": bool(row.hidden), "problem": problem, "tasks": out,
            "notices": _notices(db, account.id), "level": row.level, "areas": areas}


def tasks_due(db: Session, account: Account, until: date) -> dict[str, Any]:
    """The open tasks due up to ``until``, overdue ones included, without previews."""
    return tasks_in(db, account, None, until, previews=False)


def areas_out(db: Session, account: Account) -> list[dict[str, Any]]:
    """Every area of nextasks with its lists, in nextasks' order: whether the area is chosen whole, and which lists
    alone; for the tree in "Verbindungen"."""
    row = connection_of(db, account.id)
    if row is None:
        raise NextasksError("nextasks_not_connected", "No nextasks is connected.", 409)
    with outgoing(account.id):
        world = _world(access_of(db, row), account.id)
    choice = _choice(row)
    out = []
    for area_id, area in world["areas"].items():
        whole = (choice["all"] and area_id not in choice["except"]) or area_id in choice["areas"]
        out.append({**area, "whole": whole,
                    "lists": [{"id": one["id"], "name": one["name"], "icon": one["icon"], "color": one["color"],
                               "chosen": whole or one["id"] in choice["lists"]}
                              for one in world["lists"].values() if one["area"] == area_id]})
    return out


def set_choice(db: Session, account_id: int, areas: list[str], lists: list[str]) -> None:
    clean = {"all": False, "areas": sorted({one for one in areas if TASK_ID.match(one)})[:MAX_AREAS],
             "lists": sorted({one for one in lists if TASK_ID.match(one)})[:MAX_AREAS]}
    db.execute(update(NextasksConnection).where(NextasksConnection.account_id == account_id)
               .values(task_choice=jsonlib.dumps(clean)))
    db.commit()


def move_choices(db: Session) -> int:
    """At the start: the areas switched off before lists could be chosen become "every area but these", once."""
    moved = 0
    for row in db.scalars(select(NextasksConnection).where(NextasksConnection.task_choice == "",
                                                           NextasksConnection.areas_off != "")):
        off = _ids(row.areas_off)
        if off:
            row.task_choice = jsonlib.dumps({"all": True, "except": off, "areas": [], "lists": []})
            moved += 1
        row.areas_off = ""
    db.commit()
    return moved


def set_area_hidden(db: Session, account: Account, area_id: str, hidden: bool) -> None:
    """The eye of one area in the sidebar; "Meine Aufgaben" hidden from before becomes every area hidden first."""
    row = connection_of(db, account.id)
    if row is None or not TASK_ID.match(area_id):
        raise NextasksError("nextasks_not_connected", "No nextasks is connected.", 409)
    with _cache_lock:
        kept = _world_cache.get(account.id)
    known = [one["id"] for one in _rows(row, kept[1])[0]] if kept else []
    found = _hidden_areas(row, known)
    found = found | {area_id} if hidden else found - {area_id}
    db.execute(update(NextasksConnection).where(NextasksConnection.account_id == account.id)
               .values(hidden=False, areas_hidden=jsonlib.dumps(sorted(found)[:MAX_AREAS])))
    db.commit()


def _latest_ticks(db: Session, account_id: int) -> dict[str, TaskTick]:
    found: dict[str, TaskTick] = {}
    for tick in db.scalars(select(TaskTick).where(TaskTick.account_id == account_id).order_by(TaskTick.id)):
        found[tick.task_id] = tick
    return found


def _notices(db: Session, account_id: int) -> list[dict[str, Any]]:
    return [{"id": tick.id, "title": tick.title, "problem": tick.problem}
            for tick in db.scalars(select(TaskTick).where(TaskTick.account_id == account_id,
                                                          TaskTick.state == "failed", TaskTick.seen.is_(False)))]


def notice_seen(db: Session, account_id: int, tick_id: int) -> None:
    db.execute(update(TaskTick).where(TaskTick.account_id == account_id, TaskTick.id == tick_id).values(seen=True))
    db.commit()


def set_hidden(db: Session, account_id: int, hidden: bool) -> None:
    db.execute(update(NextasksConnection).where(NextasksConnection.account_id == account_id).values(hidden=hidden))
    db.commit()


# --- Ticking (AC-3, AC-9, EC-3, EC-4, EC-10, EC-12) -----------------------------------------------------------------


def _copy_of(db: Session, row: NextasksConnection, account: Account, copy_id: str) -> dict[str, Any] | None:
    """A done copy of a repeating task ticked off in nextasks itself: the open task it came from (same list, same
    title, a rule) and whether that stands exactly one repetition on (then taking back brings its day back)."""
    access = access_of(db, row)
    try:
        with outgoing(account.id):
            copy = request(access, "GET", f"tasks/{copy_id}")
            if not isinstance(copy, dict) or copy.get("completed") is not True or copy.get("repeat"):
                return None
            due = date.fromisoformat(str(copy.get("due_date") or "")[:10])
            found = request(access, "GET", "tasks", params={"list_id": str(copy.get("list_id") or ""),
                                                             "state": "open", "subtasks": "true", "limit": "500"})
    except (NextasksError, ValueError):
        return None
    title = " ".join(str(copy.get("title") or "").split())
    for one in (found.get("tasks") if isinstance(found, dict) else None) or []:
        if not isinstance(one, dict) or not isinstance(one.get("id"), str) or not TASK_ID.match(one["id"]):
            continue
        if not one.get("repeat") or " ".join(str(one.get("title") or "").split()) != title:
            continue
        try:
            rule = rrulestr(str(one["repeat"]), dtstart=datetime.combine(due, dtime()))
            following = rule.after(datetime.combine(due, dtime()))
            now_due = date.fromisoformat(str(one.get("due_date") or "")[:10])
        except (ValueError, TypeError):
            continue
        return {"task_id": one["id"], "one": following is not None and following.date() == now_due,
                "due": due.isoformat(), "time": str(copy.get("due_time") or "")[:5]}
    return None


def tick(db: Session, account: Account, task_id: str, *, done: bool, ref: str, title: str = "") -> dict[str, Any]:
    """A tap on a task's box. The same ``ref`` again is the same tap (EC-10). Taking back a tick that still waits
    drops both, nothing goes to nextasks; otherwise it goes now, and waits when nextasks is away (EC-3)."""
    if not TASK_ID.match(task_id):
        raise NextasksError("nextasks_not_found", "The nextasks server does not know this.", 404)
    row = connection_of(db, account.id)
    if row is None:
        raise NextasksError("nextasks_not_connected", "No nextasks is connected.", 409)
    known = db.scalar(select(TaskTick).where(TaskTick.account_id == account.id, TaskTick.ref == ref))
    if known is not None:
        return tick_out(known)
    if row.level != "write":
        # The task stays open, the page says why (EC-4).
        raise NextasksError("nextasks_read_only", "This key of nextasks may only read.", 409)
    original = None
    if not done:
        # "Nicht erledigt" on the done copy of a repeating task ticked here: taken back on the task itself.
        original = db.scalar(select(TaskTick).where(TaskTick.account_id == account.id, TaskTick.copy_id == task_id,
                                                    TaskTick.state == "sent", TaskTick.done.is_(True))
                             .order_by(TaskTick.id.desc()))
        if original is not None:
            task_id = original.task_id
    back: dict[str, Any] | None = None
    moved_on = False
    if not done and original is None:
        # Ticked off in nextasks itself: one repetition on, the task gets the copy's day back and the copy goes;
        # further on, only the copy opens again (and the page says so).
        found = _copy_of(db, row, account, task_id)
        if found is not None and found["one"]:
            back = {"copy": task_id, "due": found["due"], "time": found["time"]}
            task_id = found["task_id"]
        elif found is not None:
            moved_on = True
    earlier = db.scalar(select(TaskTick).where(TaskTick.account_id == account.id, TaskTick.task_id == task_id,
                                               TaskTick.state.in_(("waiting", "sending", "sent")))
                        .order_by(TaskTick.id.desc()))
    # Born claimed: the round never takes a tick that is being sent here (no second PATCH, review finding 1).
    made = TaskTick(account_id=account.id, task_id=task_id, ref=ref, done=done, title=" ".join(title.split())[:300],
                    state="sending", next_at=clock() + timedelta(seconds=LEASE_SECONDS))
    db.add(made)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        known = db.scalar(select(TaskTick).where(TaskTick.account_id == account.id, TaskTick.ref == ref))
        if known is None:
            raise
        return tick_out(known)
    if earlier is not None and earlier.state == "waiting" and earlier.done != done:
        # Taken back before it went: neither goes.
        earlier.state = made.state = "dropped"
        db.commit()
        logger.info("nextasks tick dropped account_id=%s", account.id)
        return tick_out(made)
    if earlier is not None and earlier.state == "sending" and earlier.done != done:
        # The tick before is still on its way: this one goes after it, in a round.
        made.state = "waiting"
        made.next_at = clock() + timedelta(seconds=RETRY_SECONDS)
        db.commit()
        _drop_cache(account.id)
        return tick_out(made)
    if earlier is not None and earlier.state == "sent" and not done:
        made.copy_id, made.due_before, made.time_before = earlier.copy_id, earlier.due_before, earlier.time_before
    if back is not None:
        made.copy_id, made.due_before, made.time_before = back["copy"], back["due"], back["time"]
    db.commit()
    send(db, made)
    _drop_cache(account.id)
    return {**tick_out(made), "moved_on": moved_on}


def tick_out(row: TaskTick) -> dict[str, Any]:
    state = "waiting" if row.state == "sending" else row.state
    return {"id": row.id, "task_id": row.task_id, "done": row.done, "state": state,
            "waiting": state == "waiting", "problem": row.problem or None}


def _taken_back_from(db: Session, row: TaskTick) -> None:
    """Taking a tick back that waited behind the tick itself: what that one learnt goes along."""
    if row.done or row.copy_id:
        return
    earlier = db.scalar(select(TaskTick).where(TaskTick.account_id == row.account_id, TaskTick.task_id == row.task_id,
                                               TaskTick.id < row.id, TaskTick.done.is_(True),
                                               TaskTick.state == "sent").order_by(TaskTick.id.desc()))
    if earlier is not None:
        row.copy_id, row.due_before, row.time_before = earlier.copy_id, earlier.due_before, earlier.time_before


def send(db: Session, row: TaskTick) -> None:
    """One try of a tick its caller has claimed (state ``sending``); a refusal for good ends it with a notice,
    anything else lets it wait for the next round. A repeating task is looked at first: when a try before already
    reached nextasks (a time-out after the PATCH arrived), it has moved on, and nothing is sent again."""
    connection = connection_of(db, row.account_id)
    if connection is None:
        row.state = "dropped"
        db.commit()
        return
    access = access_of(db, connection)
    row.tries += 1
    _taken_back_from(db, row)
    try:
        with outgoing(row.account_id):
            if row.done:
                before = request(access, "GET", f"tasks/{row.task_id}")
                before = before if isinstance(before, dict) else {}
                due_now = str(before.get("due_date") or "")[:10]
                if row.tries == 1 and before.get("repeat"):
                    # A repeating task: the day it is ticked on, kept before anything goes; a later try compares with
                    # it, and taking the tick back brings that day back.
                    row.due_before = due_now
                    row.time_before = str(before.get("due_time") or "")[:5]
                    db.commit()
                arrived = before.get("completed") is True or (
                    bool(before.get("repeat")) and row.tries > 1 and bool(row.due_before) and due_now != row.due_before)
                if arrived:
                    logger.info("nextasks tick had arrived before account_id=%s", row.account_id)
                else:
                    answer = request(access, "PATCH", f"tasks/{row.task_id}", body={"completed": True})
                    if isinstance(answer, dict) and isinstance(answer.get("completed_copy"), str):
                        # A repeating task moved on; what to bring back on taking the tick back.
                        row.copy_id = answer["completed_copy"][:100]
            elif row.copy_id or row.due_before:
                if row.due_before:
                    # Back to the day it was ticked on; ``null`` would clear the repeat as well.
                    request(access, "PATCH", f"tasks/{row.task_id}",
                            body={"due_date": row.due_before, "due_time": row.time_before or None})
                if row.copy_id:
                    try:
                        request(access, "DELETE", f"tasks/{row.copy_id}")
                    except NextasksError as exc:
                        if exc.code != "nextasks_not_found":
                            raise
            else:
                request(access, "PATCH", f"tasks/{row.task_id}", body={"completed": False})
        row.state = "sent"
        row.problem = ""
        db.commit()
        logger.info("nextasks tick sent account_id=%s done=%s", row.account_id, row.done)
    except NextasksError as exc:
        if exc.final:
            row.state = "failed"
            row.problem = exc.code
            logger.info("nextasks tick refused account_id=%s code=%s", row.account_id, exc.code)
            if exc.code in ("nextasks_read_only", "nextasks_token_invalid"):
                _note_problem(db, row.account_id, exc.code)
        else:
            row.state = "waiting"
            row.next_at = clock() + timedelta(seconds=RETRY_SECONDS)
        db.commit()


def claim(db: Session, tick_id: int) -> bool:
    """Takes a due tick for sending, atomically: of a round and a tap at the same moment only one gets it (review
    finding 1). A claim left by a stopped process runs out after ``LEASE_SECONDS``."""
    now = clock()
    taken = db.execute(update(TaskTick).where(TaskTick.id == tick_id, TaskTick.state.in_(("waiting", "sending")),
                                              TaskTick.next_at <= now)
                       .values(state="sending", next_at=now + timedelta(seconds=LEASE_SECONDS)))
    db.commit()
    return taken.rowcount == 1


def run_waiting(db: Session) -> int:
    """A round: every tick that waits and is due goes once more (EC-12), each only by whoever claimed it."""
    # An account that is out (blocked or deleted, E38): its ticks wait until it is back.
    out = select(Account.id).where(or_(Account.blocked_at.is_not(None), Account.deleted_at.is_not(None)))
    due = list(db.scalars(select(TaskTick.id).where(TaskTick.state.in_(("waiting", "sending")),
                                                    TaskTick.next_at <= clock(),
                                                    TaskTick.account_id.not_in(out)).order_by(TaskTick.id)))
    sent = 0
    for tick_id in due:
        if not claim(db, tick_id):
            continue
        row = db.get(TaskTick, tick_id)
        if row is None:
            continue
        db.refresh(row)
        sent += 1
        try:
            send(db, row)
        except NextasksError:
            # The brake of the account: the tick waits for the next round.
            db.rollback()
            db.execute(update(TaskTick).where(TaskTick.id == tick_id, TaskTick.state == "sending")
                       .values(state="waiting", next_at=clock() + timedelta(seconds=RETRY_SECONDS)))
            db.commit()
    return sent


def opened(db: Session, account: Account, task_id: str) -> dict[str, Any]:
    """The link of a task, or that it is gone (EC-7): deleted for good or in the trash of nextasks."""
    row = connection_of(db, account.id)
    if row is None or not TASK_ID.match(task_id):
        raise NextasksError("nextasks_not_connected", "No nextasks is connected.", 409)
    try:
        with outgoing(account.id):
            task = request(access_of(db, row), "GET", f"tasks/{task_id}")
    except NextasksError as exc:
        if exc.code == "nextasks_not_found":
            _drop_cache(account.id)
            return {"gone": True, "link": ""}
        raise
    if not isinstance(task, dict) or task.get("trashed") is True:
        _drop_cache(account.id)
        return {"gone": True, "link": ""}
    return {"gone": False, "link": _task_link(task, row.url)}


def run_round() -> int:
    from ..db import SessionLocal

    with SessionLocal() as db:
        return run_waiting(db)


async def run_forever(stop: Any) -> None:
    """Ticks that wait go once more every round, also after a restart (EC-12)."""
    import asyncio

    while not stop.is_set():
        try:
            await asyncio.to_thread(run_round)
        except Exception:
            logger.exception("nextasks round failed")
        try:
            await asyncio.wait_for(stop.wait(), RETRY_SECONDS)
        except TimeoutError:
            continue
