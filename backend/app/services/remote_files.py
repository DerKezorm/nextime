"""Attachments of events from iCloud and other CalDAV servers, fetched through nextime (TIME-9 AC-5, EC-14).

Such an attachment lies on the source's own server and needs the source's access, which only the server has. nextime
fetches it for whoever may see the event and hands it on as a download:

* only from the source's own host (iCloud: a host of ``icloud.com``), with the source's login, never a redirect to
  anywhere else;
* never from the server itself, link-local or metadata addresses, from the own network only as the source's account
  may (``addresses``, as subscriptions), each checked where the connection is made; for iCloud only from public
  addresses;
* within ``FETCH_SECONDS`` and at most the operator's limit per attachment.

Anything else (Google Drive links) the page opens in a new tab: the browser has the person's Google sign-in, nextime
does not need it. What cannot be had is ``remote_unavailable``: the page shows the name and a sentence (EC-14).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from ..models import SOURCE_CALDAV, SOURCE_ICLOUD, Calendar, Source
from . import addresses, outbound, subscriptions, webpush

logger = logging.getLogger("nextime.attachments")

FETCH_SECONDS = 20.0
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
#: The transport of every fetch; the tests put their own here.
transport: httpx.BaseTransport | None = None


def _unavailable(reason: str) -> Exception:
    from .attachments import AttachmentError

    logger.info("An attachment of a source could not be fetched reason=%s", reason)
    return AttachmentError("remote_unavailable", "The attachment cannot be fetched right now.", 502)


def allowed(source: Source, url: str) -> bool:
    """The address lies on the source's own server: iCloud's hosts for iCloud, the same host as the CalDAV server
    otherwise; https, or the scheme of a CalDAV server in the home network."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password:
        return False
    if source.kind == SOURCE_ICLOUD:
        return parts.scheme == "https" and (host == "icloud.com" or host.endswith(".icloud.com"))
    if source.kind == SOURCE_CALDAV:
        server = urlsplit(source.server)
        return host == (server.hostname or "").lower() and parts.scheme in ("https", server.scheme)
    return False


def fetch(db: Session, calendar: Calendar, url: str, max_bytes: int, into: Path) -> int:
    """The attachment at ``url`` into the file ``into``, at most ``max_bytes``; its size."""
    from . import sync

    source = db.get(Source, calendar.source_id) if calendar.source_id is not None else None
    if source is None or not allowed(source, url):
        raise _unavailable("not_on_the_source")
    # iCloud only from public addresses, the connection itself included.
    checked = addresses.rule(False) if source.kind == SOURCE_ICLOUD else addresses.for_account(db, source.account_id)
    try:
        subscriptions.check_host(url, checked)
    except subscriptions.FetchError:
        raise _unavailable("address_blocked") from None
    if source.kind == SOURCE_ICLOUD:
        try:
            found = subscriptions.resolve(urlsplit(url).hostname or "")
        except subscriptions.FetchError:
            raise _unavailable("dns") from None
        if not found or not all(webpush._public(address) for address in found):
            raise _unavailable("not_public")
    secret = sync.access_of(source) or {}
    auth = httpx.BasicAuth(source.username, str(secret.get("password", ""))) if source.username else None
    deadline = time.monotonic() + FETCH_SECONDS
    try:
        chosen = transport if transport is not None else addresses.transport(
            checked, lambda host: subscriptions.resolve(host))
        with (httpx.Client(transport=chosen, timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client,
              client.stream("GET", url, headers={"User-Agent": "nextime"}, auth=auth) as answer):
            if answer.status_code != 200:
                raise _unavailable(f"http_{answer.status_code}")
            size = 0
            with open(into, "wb") as sink:
                for chunk in answer.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise _unavailable("too_large")
                    if time.monotonic() > deadline:
                        raise _unavailable("timeout")
                    sink.write(chunk)
            return size
    except outbound.Refused:
        raise _unavailable("address_blocked") from None
    except (httpx.HTTPError, outbound.Unreachable, subscriptions.FetchError) as exc:
        raise _unavailable(type(exc).__name__.lower()[:40]) from exc
    except OSError as exc:
        raise _unavailable("disk") from exc
