"""Subscriptions to calendars on the net (ICS links, TIME-17), and Google's secret address as the way to read Google
without a set-up Google (TIME-16 AC-4).

A link is read only. nextime fetches it once an hour (AC-2) with ``If-None-Match``/``If-Modified-Since``, splits it
into one object per event (UID with its exceptions), and puts the whole content in place at once: a fetch that
breaks off leaves the state before (EC-8). A link with a secret in it is shown shortened only, and kept sealed like a
password (EC-11).
"""

from __future__ import annotations

import hashlib
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import icalendar
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import SOURCE_SUBSCRIPTION, STATE_EXPIRED, STATE_OK, Account, Calendar, Event, Source
from . import addresses, calendars, ical, live, outbound, sync, vault

logger = logging.getLogger("nextime.subscriptions")

#: The tests put their own server here (``httpx.MockTransport``); nothing leaves the machine in a test.
transport: httpx.BaseTransport | None = None
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
#: The longest one fetch may take altogether, and the redirects it follows.
FETCH_SECONDS = 60
MAX_REDIRECTS = 5
#: The largest feed nextime reads.
MAX_BYTES = 25 * 1024 * 1024
GOOGLE_HOSTS = ("calendar.google.com", "www.google.com")


class FetchError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Feed:
    name: str = ""
    objects: dict[str, str] = field(default_factory=dict)
    first: datetime | None = None
    last: datetime | None = None
    unreadable: int = 0


@dataclass
class Fetched:
    text: str | None
    etag: str = ""
    modified: str = ""


def clean_url(raw: str) -> str:
    """``webcal://``, blanks at the edges and from the clipboard taken (EC-5); anything but a web address refused."""
    text = "".join((raw or "").split())
    if text.lower().startswith("webcal://"):
        text = "https://" + text[9:]
    elif text.lower().startswith("webcals://"):
        text = "https://" + text[10:]
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise error("subscription_not_a_calendar", "There is no calendar at this link.", 422, field="url")
    return text


def shown_url(url: str) -> str:
    """The link without its secret parts: long path parts and the query shortened (EC-11)."""
    parts = urlsplit(url)
    path = "/".join(piece if len(piece) <= 12 else piece[:4] + "…" for piece in parts.path.split("/"))
    return urlunsplit((parts.scheme, parts.hostname or "", path, "…" if parts.query else "", ""))


def is_google(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or "").lower() in GOOGLE_HOSTS and "/calendar/ical/" in parts.path


def _resolve(host: str) -> list[str]:
    try:
        return sorted({str(item[4][0]) for item in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except (OSError, UnicodeError) as exc:
        raise FetchError("subscription_unreachable") from exc


#: How a host becomes its addresses; the tests answer without asking any DNS.
resolve: Callable[[str], list[str]] = _resolve


def check_host(url: str, checked: addresses.Rule) -> None:
    """Refuses a link whose host is, or resolves to, an address the rule turns down (``addresses``): the code
    ``subscription_address_blocked``, or ``subscription_address_local`` for the own network while that is not allowed.
    Asked for every hop of a redirect; the connection itself checks again what it connects to."""
    host = (urlsplit(url).hostname or "").strip("[]")
    if not host:
        raise FetchError("subscription_not_a_calendar")
    given = addresses.literal(host)
    found = [given] if given is not None else resolve(host)
    if not found:
        raise FetchError("subscription_address_blocked")
    try:
        addresses.check(found, checked)
    except outbound.Refused as exc:
        raise FetchError(_blocked(exc.reason)) from None


def _blocked(reason: str) -> str:
    return "subscription_address_local" if reason == "local" else "subscription_address_blocked"


def _client(checked: addresses.Rule) -> httpx.Client:
    """The tests' transport as it is; else httpx's own, whose sockets go only to checked addresses (DNS rebinding)."""
    chosen = transport if transport is not None else addresses.transport(checked, lambda host: resolve(host))
    return httpx.Client(transport=chosen, timeout=TIMEOUT, follow_redirects=False, trust_env=False)


def fetch(url: str, *, checked: addresses.Rule, username: str = "", password: str = "", etag: str = "",
          modified: str = "", bearer: str = "") -> Fetched:
    """The feed, within ``FETCH_SECONDS`` altogether, redirects followed by hand (each hop checked), at most
    ``MAX_REDIRECTS``. The login (or the access of a connection, ``bearer``) goes to the host of the link only."""
    headers = {"User-Agent": "nextime", "Accept": "text/calendar, */*"}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    deadline = time.monotonic() + FETCH_SECONDS
    first_host = (urlsplit(url).hostname or "").lower()
    current = url
    try:
        with _client(checked) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                check_host(current, checked)
                same = (urlsplit(current).hostname or "").lower() == first_host
                auth = httpx.BasicAuth(username, password) if username and same else None
                sent = {**headers, "Authorization": f"Bearer {bearer}"} if bearer and same else headers
                with client.stream("GET", current, headers=sent, auth=auth) as answer:
                    if answer.status_code in (301, 302, 303, 307, 308) and answer.headers.get("Location"):
                        target = urljoin(current, answer.headers["Location"])
                        if urlsplit(target).scheme.lower() not in ("http", "https"):
                            raise FetchError("subscription_unreachable")
                        current = target
                        continue
                    if answer.status_code == 304:
                        return Fetched(None, etag, modified)
                    if answer.status_code in (401, 403):
                        raise FetchError("subscription_login_refused" if username or bearer
                                         else "subscription_login_needed")
                    if answer.status_code in (404, 410):
                        raise FetchError("subscription_gone")
                    if answer.status_code >= 400:
                        raise FetchError("subscription_unreachable")
                    body = b""
                    for chunk in answer.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise FetchError("subscription_too_large")
                        if time.monotonic() > deadline:
                            # A server that trickles its answer does not hold the round (TIME-17 EC-8).
                            raise FetchError("subscription_unreachable")
                    return Fetched(body.decode(answer.encoding or "utf-8", "replace"), answer.headers.get("ETag", ""),
                                   answer.headers.get("Last-Modified", ""))
            raise FetchError("subscription_unreachable")
    except FetchError:
        raise
    except outbound.Refused as exc:
        # The name answered differently when the connection was made (DNS rebinding): refused there.
        raise FetchError(_blocked(exc.reason)) from None
    except (httpx.HTTPError, OSError, outbound.Unreachable) as exc:
        raise FetchError("subscription_unreachable") from exc


def parse(text: str) -> Feed:
    """One object per event; a feed that is no calendar is refused (EC-4), an event nextime cannot read skipped."""
    stripped = text.lstrip("﻿ \r\n\t")
    if not stripped.upper().startswith("BEGIN:VCALENDAR"):
        raise FetchError("subscription_not_a_calendar")
    try:
        whole = icalendar.Calendar.from_ical(stripped)
    except Exception as exc:
        raise FetchError("subscription_not_a_calendar") from exc
    zones = {str(component.get("TZID")): component for component in whole.walk("VTIMEZONE")}
    groups: dict[str, list[Any]] = {}
    for component in whole.walk("VEVENT"):
        uid = str(component.get("UID", "")).strip()
        if not uid:
            seed = f"{component.get('DTSTART').to_ical() if component.get('DTSTART') else ''}{component.get('SUMMARY')}"
            uid = "nextime-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
            component.add("UID", uid)
        groups.setdefault(uid, []).append(component)
    feed = Feed(name=str(whole.get("X-WR-CALNAME", "") or "").strip())
    for uid, components in groups.items():
        single = ical.new_calendar()
        used = set()
        for component in components:
            for name in ("DTSTART", "DTEND", "RECURRENCE-ID"):
                value = component.get(name)
                tzid = getattr(value, "params", {}).get("TZID") if value is not None else None
                if tzid:
                    used.add(str(tzid))
        for tzid in sorted(used):
            if tzid in zones:
                single.add_component(zones[tzid])
        for component in components:
            single.add_component(component)
        object_text = ical.serialize(single)
        try:
            parsed = ical.parse(object_text)
            ical.check(parsed)
            derived = ical.derive(parsed)
        except Exception:  # noqa: BLE001 - one broken event does not stop the others
            feed.unreadable += 1
            continue
        feed.objects[hashlib.sha256(uid.encode("utf-8")).hexdigest()[:32] + ".ics"] = object_text
        feed.first = derived.starts_at if feed.first is None else min(feed.first, derived.starts_at)
        end = derived.ends_at or derived.starts_at
        feed.last = end if feed.last is None else max(feed.last, end)
    return feed


def _refuse(code: str) -> Exception:
    texts = {
        "subscription_not_a_calendar": "There is no calendar at this link.",
        "subscription_login_needed": "This link asks for a name and password.",
        "subscription_login_refused": "The name and password do not fit.",
        "subscription_gone": "There is no calendar at this link.",
        "subscription_too_large": "This calendar is too large.",
        "subscription_unreachable": "The link does not answer. Try again in a moment.",
        "subscription_address_blocked": "nextime does not fetch from this address.",
        "subscription_address_local": "This address is in the local network. The operator has to allow that first.",
    }
    field_name = "password" if code == "subscription_login_refused" else "url"
    return error(code, texts.get(code, "There is no calendar at this link."), 422 if code != "subscription_unreachable"
                 else 502, field=field_name)


def _events_total(db: Session, account: Account) -> int:
    return int(db.scalar(select(func.count()).select_from(Event).join(Calendar, Calendar.id == Event.calendar_id)
                         .where(Calendar.owner_id == account.id, Calendar.deleted_at.is_(None),
                                Event.deleted_at.is_(None))) or 0)


def preview(db: Session, account: Account, raw: str, *, username: str = "", password: str = "",
            google: bool = False) -> dict[str, Any]:
    """Name, number of events and span before subscribing (AC-1), and a warning above 20,000 events (EC-2)."""
    from .sources import EVENTS_SMOOTH_UP_TO

    url = clean_url(raw)
    if google and not is_google(url):
        raise error("not_google_address", "This is not the secret address of a Google calendar.", 422, field="url")
    try:
        feed = parse(fetch(url, checked=addresses.for_account(db, account.id), username=username.strip(),
                           password=password.strip()).text or "")
    except FetchError as exc:
        raise _refuse(exc.code) from None
    if google and not feed.objects and not feed.name:
        raise _refuse("subscription_not_a_calendar")
    total = _events_total(db, account)
    known = _identity(url)
    exists = db.scalar(select(Source.id).where(Source.account_id == account.id, Source.kind == SOURCE_SUBSCRIPTION,
                                               Source.identity == known))
    return {"name": feed.name or (urlsplit(url).hostname or ""), "count": len(feed.objects),
            "first": feed.first.date().isoformat() if feed.first else None,
            "last": feed.last.date().isoformat() if feed.last else None,
            "over": total + len(feed.objects) > EVENTS_SMOOTH_UP_TO, "events_total": total,
            "events_smooth_up_to": EVENTS_SMOOTH_UP_TO, "exists": exists}


def _identity(url: str) -> str:
    return vault.identity(SOURCE_SUBSCRIPTION, url)


def subscribe(db: Session, account: Account, raw: str, *, name: str, background: bool, username: str = "",
              password: str = "", google: bool = False, client_ref: str | None = None) -> tuple[Source, bool]:
    """Read only, at once with its events (AC-1). The same link again is refused (EC-6); two clicks make one (EC-9).
    "Cancel" sends nothing, so nothing is made."""
    if client_ref:
        known = db.scalar(select(Source).where(Source.account_id == account.id, Source.client_ref == client_ref))
        if known is not None:
            return known, False
    url = clean_url(raw)
    if google and not is_google(url):
        raise error("not_google_address", "This is not the secret address of a Google calendar.", 422, field="url")
    identity = _identity(url)
    existing = db.scalar(select(Source).where(Source.account_id == account.id, Source.kind == SOURCE_SUBSCRIPTION,
                                              Source.identity == identity))
    if existing is not None:
        raise error("subscription_exists", "This link is subscribed already.", 409, source_id=existing.id)
    username, password = username.strip(), password.strip()
    try:
        fetched = fetch(url, checked=addresses.for_account(db, account.id), username=username, password=password)
        feed = parse(fetched.text or "")
    except FetchError as exc:
        raise _refuse(exc.code) from None
    title = (name or "").strip() or feed.name or (urlsplit(url).hostname or "Calendar")
    row = Source(account_id=account.id, kind=SOURCE_SUBSCRIPTION, identity=identity, label=shown_url(url),
                 client_ref=client_ref, state=STATE_OK, synced_at=sync.clock(),
                 due_at=sync.clock() + _hour(),
                 options={"etag": fetched.etag, "modified": fetched.modified, "google": google,
                          "unreadable": {}})
    try:
        db.add(row)
        db.flush()
        row.secret_enc = vault.seal({"url": url, "username": username, "password": password}, sync.place(row))
        calendar = calendars._make(db, account.id, title[: calendars.NAME_MAX], "red" if google else "brown",
                                   background=background)
        calendar.source_id, calendar.remote_url, calendar.read_only = row.id, "subscription", True
        db.flush()
        sync.replace_all(db, calendar, feed.objects)
        if feed.unreadable:
            row.options = {**row.options, "unreadable": {str(calendar.id): [f"#{n}" for n in range(feed.unreadable)]}}
        live.touch_accounts(db, {account.id})
        db.commit()
    except IntegrityError:
        db.rollback()
        known = db.scalar(select(Source).where(Source.account_id == account.id, (
            (Source.client_ref == client_ref) if client_ref else (Source.identity == identity))))
        if known is None:
            raise
        return known, False
    logger.info("Subscribed source=%s events=%s", row.id, len(feed.objects))
    return row, True


def change_link(db: Session, account: Account, source_id: int, raw: str) -> Source:
    """A new link after the old one was reset at its source (TIME-16 EC-11): the events come from the new one."""
    from .sources import own

    row = own(db, account, source_id)
    if row.kind != SOURCE_SUBSCRIPTION:
        raise error("not_found", "Not found.", 404)
    url = clean_url(raw)
    google = bool((row.options or {}).get("google"))
    if google and not is_google(url):
        raise error("not_google_address", "This is not the secret address of a Google calendar.", 422, field="url")
    secret = sync.access_of(row) or {}
    try:
        parse(fetch(url, checked=addresses.for_account(db, account.id), username=str(secret.get("username", "")),
                    password=str(secret.get("password", ""))).text or "")
    except FetchError as exc:
        raise _refuse(exc.code) from None
    row.secret_enc = vault.seal({**secret, "url": url}, sync.place(row))
    row.identity = _identity(url)
    row.label = shown_url(url)
    row.options = {**(row.options or {}), "etag": "", "modified": ""}
    row.due_at = sync.clock()
    db.commit()
    sync.run_source(row.id, force=True)
    return row


def _hour():
    from datetime import timedelta

    return timedelta(seconds=sync.INTERVALS[SOURCE_SUBSCRIPTION])


def round_(db: Session, source: Source, *, force: bool = False) -> None:
    """Fetch, and on a change put the new content in place. A broken link keeps the last events and turns the row
    orange: "not updated since …" (EC-1)."""
    secret = sync.access_of(source)
    if secret is None:
        source.due_at = sync.clock() + _hour()
        sync.set_state(db, source, STATE_EXPIRED, "key")
        return
    options = dict(source.options or {})
    try:
        if secret.get("connection"):
            fetched = _fetch_by_connection(db, source, str(secret["connection"]), etag="" if force else str(
                options.get("etag", "")), modified="" if force else str(options.get("modified", "")))
            if fetched is None:
                return
        else:
            fetched = fetch(str(secret.get("url", "")), checked=addresses.for_account(db, source.account_id),
                            username=str(secret.get("username", "")),
                            password=str(secret.get("password", "")),
                            etag="" if force else str(options.get("etag", "")),
                            modified="" if force else str(options.get("modified", "")))
        feed = parse(fetched.text) if fetched.text is not None else None
    except FetchError as exc:
        source.failures += 1
        source.due_at = sync.clock() + _hour()
        if exc.code == "subscription_login_refused":
            sync.set_state(db, source, STATE_EXPIRED, "credentials")
        else:
            sync.set_state(db, source, "unreachable", exc.code)
        return
    if feed is not None:
        for calendar in db.scalars(select(Calendar).where(Calendar.source_id == source.id)):
            sync.replace_all(db, calendar, feed.objects)
            options["unreadable"] = {str(calendar.id): [f"#{n}" for n in range(feed.unreadable)]}
        options["etag"], options["modified"] = fetched.etag, fetched.modified
        source.options = options
    source.failures = 0
    source.synced_at = sync.clock()
    source.due_at = sync.clock() + _hour()
    sync.set_state(db, source, STATE_OK)


# --- A subscription over a connection with another nex app (plan "Apps verbinden" V1 8a) ------------------------------

#: What a partner offers as a calendar over a connection made by a click: nexfolk's birthdays (``birthdays.read``,
#: only the address books given; ``GET /api/v1/birthdays.ics`` with the connection's access).
BY_CONNECTION = {"nexfolk": "/api/v1/birthdays.ics"}


def _identity_of(partner: str) -> str:
    return f"connection:{partner}"


def by_connection(db: Session, account_id: int, partner: str) -> Source | None:
    return db.scalar(select(Source).where(Source.account_id == account_id, Source.kind == SOURCE_SUBSCRIPTION,
                                          Source.identity == _identity_of(partner)))


def subscribe_by_connection(db: Session, account_id: int, partner: str) -> Source | None:
    """"Mit nexfolk verbinden" makes the subscription itself (V1 8a): a calendar like any subscription (read only,
    once an hour, the last events kept while nexfolk is away), fetched with the connection's access instead of a
    secret link. Once per connection; nothing when the partner offers no calendar."""
    from . import verbinden_store

    if partner not in BY_CONNECTION:
        return None
    known = by_connection(db, account_id, partner)
    if known is not None:
        return known
    account = db.get(Account, account_id)
    if account is None:
        return None
    try:
        base, token = verbinden_store.access(db, account_id, partner)
        fetched = fetch(clean_url(base) + BY_CONNECTION[partner], checked=addresses.for_account(db, account_id),
                        bearer=token)
        feed = parse(fetched.text or "")
    except (verbinden_store.NotConnected, FetchError) as exc:
        logger.info("No calendar over the connection yet partner=%s reason=%s", partner,
                    getattr(exc, "code", type(exc).__name__))
        return None
    row = Source(account_id=account_id, kind=SOURCE_SUBSCRIPTION, identity=_identity_of(partner), label=partner,
                 state=STATE_OK, synced_at=sync.clock(), due_at=sync.clock() + _hour(),
                 options={"etag": fetched.etag, "modified": fetched.modified, "google": False, "unreadable": {},
                          "connection": partner})
    db.add(row)
    db.flush()
    row.secret_enc = vault.seal({"connection": partner}, sync.place(row))
    calendar = calendars._make(db, account_id, (feed.name or partner)[: calendars.NAME_MAX], "brown",
                               background=False)
    calendar.source_id, calendar.remote_url, calendar.read_only = row.id, "subscription", True
    db.flush()
    sync.replace_all(db, calendar, feed.objects)
    live.touch_accounts(db, {account_id})
    db.commit()
    logger.info("Subscribed over a connection partner=%s source=%s events=%s", partner, row.id, len(feed.objects))
    return row


def _fetch_by_connection(db: Session, source: Source, partner: str, *, etag: str, modified: str) -> Fetched | None:
    """The calendar of ``partner`` with the connection's access; a 401 asks once more with a renewed access (V18).
    The connection ended: the subscription goes with it (what was only mirrored goes), ``None``."""
    from . import sources, verbinden_store

    try:
        base, token = verbinden_store.access(db, source.account_id, partner)
    except verbinden_store.NotConnected as exc:
        if exc.code == "not_connected":
            account = db.get(Account, source.account_id)
            if account is not None:
                sources.disconnect(db, account, source.id, drop_waiting=True)
            return None
        raise FetchError("subscription_unreachable" if exc.code == "unreachable" else "subscription_login_refused") \
            from None
    url = clean_url(base) + BY_CONNECTION.get(partner, "")
    checked = addresses.for_account(db, source.account_id)
    try:
        found = fetch(url, checked=checked, bearer=token, etag=etag, modified=modified)
    except FetchError as exc:
        if exc.code != "subscription_login_refused":
            verbinden_store.reached(db, source.account_id, partner, "unreachable")
            raise
        try:
            token = verbinden_store.renewed(source.account_id, partner, failed=token)
            found = fetch(url, checked=checked, bearer=token, etag=etag, modified=modified)
        except (verbinden_store.NotConnected, FetchError):
            verbinden_store.reached(db, source.account_id, partner, "token_invalid")
            raise FetchError("subscription_login_refused") from None
    verbinden_store.reached(db, source.account_id, partner, None)
    return found
