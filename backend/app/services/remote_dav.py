"""Talking CalDAV to iCloud, Google and other servers (TIME-15, TIME-16).

The library ``caldav`` carries the connection: finding the account from an address (``/.well-known/caldav``, the
principal), signing in (Basic, Digest, or Google's Bearer), redirects, and servers that ask to slow down. What nextime
asks for itself is plain WebDAV on top: the calendars of the home with name, color, rights and tags; what changed
since the last sync token (RFC 6578), and where a server has none, the ETags compared; the objects in batches
(``calendar-multiget``); ``PUT`` and ``DELETE`` with ``If-Match``.

Every failure comes out as a ``RemoteError`` of one kind, which the sync turns into the states the member sees:
access expired (``auth``), not answering (``down``), refused for good (``refused``), changed there meanwhile
(``conflict``), no calendar server at this address (``not_caldav``).
"""

from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass, field
from typing import Self
from urllib.parse import unquote, urljoin, urlsplit

import caldav
from caldav.lib import error as dav_error
from lxml import etree

logger = logging.getLogger("nextime.remote")

AUTH = "auth"
DOWN = "down"
REFUSED = "refused"
CONFLICT = "conflict"
NOT_CALDAV = "not_caldav"
GONE = "gone"

#: Seconds a source has to answer; a slow one leaves the views alone, they read the copy (TIME-15 EC-17).
TIMEOUT = 20
#: Answers that mean "slow down" though they come as 403 (Google throttles CalDAV so): the source does not answer for
#: now, the change waits (TIME-15 EC-2, TIME-16 EC-8).
THROTTLED = ("ratelimitexceeded", "userratelimitexceeded", "quotaexceeded")
#: Objects per calendar-multiget.
BATCH = 100
D, C, CS, A = "DAV:", "urn:ietf:params:xml:ns:caldav", "http://calendarserver.org/ns/", "http://apple.com/ns/ical/"
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
USER_AGENT = "nextime"


class RemoteError(Exception):
    def __init__(self, kind: str, status: int = 0) -> None:
        super().__init__(f"{kind} {status}".strip())
        self.kind = kind
        self.status = status


@dataclass
class RemoteCalendar:
    url: str
    name: str
    color: str = ""
    read_only: bool = False
    ctag: str = ""
    sync_token: str = ""


@dataclass
class Account:
    """What a connection found: the calendars of the account, its own addresses, and whether the server answers
    invitations on its own (CalDAV scheduling, RFC 6638)."""

    calendars: list[RemoteCalendar] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    schedules: bool = False
    #: The collection that holds the calendars (calendar-home-set): what later rounds list again.
    home: str = ""


@dataclass
class Changes:
    #: ``(href, etag, text)`` of every object new or changed; ``text`` None for one that could not be fetched.
    changed: list[tuple[str, str, str | None]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    sync_token: str = ""
    #: Everything was listed (no token, or the token was refused): objects not named are gone.
    complete: bool = False


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _body(root: str, *props: tuple[str, str], extra: str = "") -> str:
    names = "".join(f"<{prefix}:{name}/>" for prefix, name in props)
    return (f'<?xml version="1.0" encoding="utf-8"?><d:{root} xmlns:d="DAV:" xmlns:c="{C}" xmlns:cs="{CS}" '
            f'xmlns:a="{A}">{extra}<d:prop>{names}</d:prop></d:{root}>')


def _status_ok(node: etree._Element) -> bool:
    status = node.findtext(_q(D, "status")) or ""
    return " 200" in status or not status


def _responses(text: bytes) -> tuple[list[tuple[str, int, dict[str, etree._Element]]], etree._Element]:
    """A multistatus as ``(href, status, {tag: element of the props that came with 200})``."""
    try:
        root = etree.fromstring(text, _PARSER)
    except etree.XMLSyntaxError as exc:
        raise RemoteError(NOT_CALDAV) from exc
    if root.tag != _q(D, "multistatus"):
        raise RemoteError(NOT_CALDAV)
    out = []
    for response in root.findall(_q(D, "response")):
        href = (response.findtext(_q(D, "href")) or "").strip()
        status_text = response.findtext(_q(D, "status")) or ""
        status = 200
        if status_text:
            try:
                status = int(status_text.split()[1])
            except (IndexError, ValueError):
                status = 200
        props: dict[str, etree._Element] = {}
        for propstat in response.findall(_q(D, "propstat")):
            if not _status_ok(propstat):
                continue
            prop = propstat.find(_q(D, "prop"))
            if prop is not None:
                for child in prop:
                    props[child.tag] = child
        out.append((href, status, props))
    return out, root


def _text(props: dict[str, etree._Element], tag: str) -> str:
    node = props.get(tag)
    return (node.text or "").strip() if node is not None else ""


def display_name(raw: str) -> str:
    """A calendar's name as people wrote it. iCloud escapes the name once more inside the XML ("Q&amp;amp;A"
    arrives as "Q&amp;A" after parsing): one more round of unescaping gives "Q&A"; a name without entities stays."""
    return html.unescape(raw) if "&" in raw else raw


def same_path(a: str, b: str) -> bool:
    return unquote(urlsplit(a).path).rstrip("/") == unquote(urlsplit(b).path).rstrip("/")


class Remote:
    """One connection to a CalDAV account. ``bearer`` for Google, else user and password."""

    def __init__(self, url: str, *, username: str = "", password: str = "", bearer: str = "",
                 timeout: int = TIMEOUT) -> None:
        self.url = url
        self.timeout = timeout
        #: The moment (``time.monotonic``) by which a round with this connection must be done (``limit``).
        self.deadline: float | None = None
        try:
            if bearer:
                self.client = caldav.DAVClient(url=url, password=bearer, auth_type="bearer", timeout=timeout,
                                               headers={"User-Agent": USER_AGENT}, enable_rfc6764=False)
            else:
                self.client = caldav.DAVClient(url=url, username=username or None, password=password or None,
                                               timeout=timeout, headers={"User-Agent": USER_AGENT})
        except dav_error.DAVError as exc:
            raise RemoteError(NOT_CALDAV) from exc
        except Exception as exc:
            raise self._translate(exc) from exc

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            logger.debug("Closing a connection failed")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- The transport ---------------------------------------------------------------------------------------------

    def limit(self, seconds: float) -> None:
        """From now on every request must be done within ``seconds`` altogether: a source that drags each answer to
        the timeout does not hold up the others (TIME-15 EC-17, AC-4)."""
        self.deadline = time.monotonic() + seconds

    def _budget(self) -> None:
        if self.deadline is None:
            return
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise RemoteError(DOWN, 408)
        self.client.timeout = max(1, min(self.timeout, int(left) + 1))

    @staticmethod
    def _translate(exc: BaseException) -> RemoteError:
        if isinstance(exc, RemoteError):
            return exc
        if isinstance(exc, dav_error.AuthorizationError):
            status = getattr(exc, "status", None) or 401
            body = getattr(exc, "body", None) or b""
            text = (body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)).lower()
            if status == 403 and any(mark in text for mark in THROTTLED):
                return RemoteError(DOWN, 403)
            return RemoteError(AUTH if status == 401 else REFUSED, status)
        if isinstance(exc, dav_error.RateLimitError):
            return RemoteError(DOWN, 429)
        if isinstance(exc, dav_error.NotFoundError):
            return RemoteError(GONE, 404)
        if isinstance(exc, dav_error.DAVError):
            return RemoteError(NOT_CALDAV)
        # Connection refused, a timeout, a name that does not resolve, TLS: the source does not answer.
        return RemoteError(DOWN)

    def request(self, url: str, method: str, body: str = "", headers: dict[str, str] | None = None) -> object:
        self._budget()
        try:
            response = self.client.request(url, method, body, headers or {})
        except Exception as exc:
            raise self._translate(exc) from exc
        status = int(getattr(response, "status", 0) or 0)
        # 408 and 429 ask to come back later, 5xx is the server's trouble: wait, never refused for good.
        if status in (408, 429) or status >= 500:
            raise RemoteError(DOWN, status)
        if status == 403 and any(mark in self._raw(response).decode("utf-8", "replace").lower() for mark in THROTTLED):
            raise RemoteError(DOWN, status)
        return response

    @staticmethod
    def _raw(response: object) -> bytes:
        raw = getattr(response, "_raw", b"")
        if isinstance(raw, str):
            return raw.encode("utf-8")
        return raw or b""

    @staticmethod
    def _header(response: object, name: str) -> str:
        headers = getattr(response, "headers", {}) or {}
        return str(headers.get(name, "") or "")

    # --- The account -----------------------------------------------------------------------------------------------

    def discover(self) -> Account:
        """The calendars of the account and its own addresses. ``auth`` when the access does not work,
        ``not_caldav`` when no calendar server answers at the address (TIME-15 EC-13)."""
        self._budget()
        try:
            principal = self.client.principal()
            principal_url = str(principal.url)
        except Exception as exc:
            raise self._translate(exc) from exc
        answer = self.request(principal_url, "PROPFIND", _body(
            "propfind", ("c", "calendar-home-set"), ("c", "calendar-user-address-set")), {"Depth": "0"})
        rows, _root = _responses(self._raw(answer))
        homes: list[str] = []
        addresses: list[str] = []
        for _href, _status, props in rows:
            home = props.get(_q(C, "calendar-home-set"))
            if home is not None:
                homes += [urljoin(principal_url, (node.text or "").strip()) for node in home.findall(_q(D, "href"))
                          if (node.text or "").strip()]
            found = props.get(_q(C, "calendar-user-address-set"))
            if found is not None:
                addresses += [(node.text or "").strip() for node in found.findall(_q(D, "href"))
                              if (node.text or "").strip()]
        if not homes:
            raise RemoteError(NOT_CALDAV)
        account = Account(addresses=[address for address in addresses if address.lower().startswith("mailto:")],
                          home=homes[0])
        account.calendars = self.calendars(homes[0])
        account.schedules = self._schedules(homes[0])
        return account

    def calendars(self, home: str) -> list[RemoteCalendar]:
        answer = self.request(home, "PROPFIND", _body(
            "propfind", ("d", "resourcetype"), ("d", "displayname"), ("a", "calendar-color"), ("cs", "getctag"),
            ("d", "sync-token"), ("d", "current-user-privilege-set"), ("c", "supported-calendar-component-set")),
            {"Depth": "1"})
        rows, _root = _responses(self._raw(answer))
        found = []
        for href, status, props in rows:
            kind = props.get(_q(D, "resourcetype"))
            if status != 200 or kind is None or kind.find(_q(C, "calendar")) is None:
                continue
            components = props.get(_q(C, "supported-calendar-component-set"))
            if components is not None and len(components):
                names = {(node.get("name") or "").upper() for node in components}
                # Reminder lists of iCloud and task lists elsewhere: not calendars of events (TIME-15 Out of Scope).
                if "VEVENT" not in names:
                    continue
            url = urljoin(home, href)
            if not url.endswith("/"):
                url += "/"
            privileges = props.get(_q(D, "current-user-privilege-set"))
            read_only = False
            if privileges is not None:
                granted = {child.tag for node in privileges.iter(_q(D, "privilege")) for child in node}
                read_only = not granted & {_q(D, "write"), _q(D, "write-content"), _q(D, "all"), _q(D, "bind")}
            last_part = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
            name = display_name(_text(props, _q(D, "displayname"))) or last_part
            found.append(RemoteCalendar(url=url, name=name, color=_text(props, _q(A, "calendar-color")),
                                        read_only=read_only, ctag=_text(props, _q(CS, "getctag")),
                                        sync_token=_text(props, _q(D, "sync-token"))))
        return found

    def _schedules(self, home: str) -> bool:
        """The server sends answers to invitations itself when the member changes its own part (RFC 6638)."""
        try:
            answer = self.request(home, "OPTIONS")
        except RemoteError:
            return False
        return "calendar-auto-schedule" in self._header(answer, "DAV").lower()

    # --- What changed ----------------------------------------------------------------------------------------------

    def changes(self, url: str, token: str, known: dict[str, str]) -> Changes:
        """What changed in the calendar at ``url`` since ``token``; ``known`` maps the hrefs nextime has to their
        ETags, for servers without sync tokens and for a token the server no longer takes."""
        if token or not known:
            try:
                return self._by_token(url, token, known)
            except RemoteError as exc:
                if exc.kind in (AUTH, DOWN):
                    raise
        return self._by_etags(url, known)

    def _by_token(self, url: str, token: str, known: dict[str, str]) -> Changes:
        extra = f"<d:sync-token>{_escape(token)}</d:sync-token><d:sync-level>1</d:sync-level>"
        answer = self.request(url, "REPORT", _body("sync-collection", ("d", "getetag"), extra=extra),
                              {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
        status = int(getattr(answer, "status", 0) or 0)
        if status in (403, 409, 400, 404, 405, 501):
            if status == 404:
                raise RemoteError(GONE, status)
            raise RemoteError(REFUSED, status)
        rows, root = _responses(self._raw(answer))
        new_token = (root.findtext(_q(D, "sync-token")) or "").strip()
        if not new_token:
            raise RemoteError(REFUSED)
        out = Changes(sync_token=new_token, complete=not token)
        wanted: list[tuple[str, str]] = []
        for href, status, props in rows:
            name = _name(href)
            if not name or same_path(href, url):
                continue
            if status == 404:
                out.deleted.append(name)
                continue
            etag = _text(props, _q(D, "getetag"))
            if etag and known.get(name) == etag:
                continue
            wanted.append((name, etag))
        out.changed = self._fetch(url, wanted)
        if out.complete:
            present = {name for name, _etag in wanted} | {
                _name(href) for href, status, _props in rows if status != 404}
            out.deleted += [name for name in known if name not in present]
        return out

    def _by_etags(self, url: str, known: dict[str, str]) -> Changes:
        answer = self.request(url, "PROPFIND", _body("propfind", ("d", "getetag"), ("d", "resourcetype")),
                              {"Depth": "1"})
        status = int(getattr(answer, "status", 0) or 0)
        if status == 404:
            raise RemoteError(GONE, status)
        rows, _root = _responses(self._raw(answer))
        present: dict[str, str] = {}
        for href, status, props in rows:
            name = _name(href)
            if status != 200 or not name or same_path(href, url):
                continue
            kind = props.get(_q(D, "resourcetype"))
            if kind is not None and kind.find(_q(D, "collection")) is not None:
                continue
            present[name] = _text(props, _q(D, "getetag"))
        wanted = [(name, etag) for name, etag in present.items() if not etag or known.get(name) != etag]
        return Changes(changed=self._fetch(url, wanted), deleted=[name for name in known if name not in present],
                       complete=True)

    def _fetch(self, url: str, wanted: list[tuple[str, str]]) -> list[tuple[str, str, str | None]]:
        """The objects named, in batches; one the server does not hand out comes back with ``None``."""
        out: list[tuple[str, str, str | None]] = []
        base = urlsplit(url).path
        for start in range(0, len(wanted), BATCH):
            part = wanted[start:start + BATCH]
            hrefs = "".join(f"<d:href>{_escape(base + name)}</d:href>" for name, _etag in part)
            body = (f'<?xml version="1.0" encoding="utf-8"?><c:calendar-multiget xmlns:d="DAV:" xmlns:c="{C}">'
                    f"<d:prop><d:getetag/><c:calendar-data/></d:prop>{hrefs}</c:calendar-multiget>")
            answer = self.request(url, "REPORT", body, {"Depth": "1",
                                                        "Content-Type": "application/xml; charset=utf-8"})
            rows, _root = _responses(self._raw(answer))
            got = {}
            for href, status, props in rows:
                if status == 200 and _q(C, "calendar-data") in props:
                    got[_name(href)] = (_text(props, _q(D, "getetag")), props[_q(C, "calendar-data")].text or "")
            for name, etag in part:
                if name in got:
                    out.append((name, got[name][0] or etag, got[name][1]))
                else:
                    out.append((name, etag, None))
        return out

    # --- Writing ---------------------------------------------------------------------------------------------------

    def get(self, url: str) -> tuple[str, str] | None:
        answer = self.request(url, "GET")
        status = int(getattr(answer, "status", 0) or 0)
        if status == 404 or status == 410:
            return None
        if status >= 400:
            raise RemoteError(REFUSED, status)
        return self._raw(answer).decode("utf-8", "replace"), self._header(answer, "ETag")

    def put(self, url: str, text: str, etag: str) -> str:
        """Stores the object; ``etag`` the version it was made on ("" for a new one). The new ETag, or "" when the
        server named none."""
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        else:
            headers["If-None-Match"] = "*"
        answer = self.request(url, "PUT", text, headers)
        status = int(getattr(answer, "status", 0) or 0)
        if status == 412:
            raise RemoteError(CONFLICT, status)
        if status >= 400:
            raise RemoteError(REFUSED, status)
        return self._header(answer, "ETag")

    def delete(self, url: str, etag: str) -> None:
        headers = {"If-Match": etag} if etag else {}
        answer = self.request(url, "DELETE", "", headers)
        status = int(getattr(answer, "status", 0) or 0)
        if status in (404, 410):
            return
        if status == 412:
            raise RemoteError(CONFLICT, status)
        if status >= 400:
            raise RemoteError(REFUSED, status)


def _name(href: str) -> str:
    """The last part of an object's address, as the server spells it."""
    path = urlsplit(href).path
    return path.rstrip("/").rsplit("/", 1)[-1] if not path.endswith("/") else ""


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
