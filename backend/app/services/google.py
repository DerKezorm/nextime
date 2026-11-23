"""Google calendars (TIME-16): the operator's Google Cloud client, members connecting with "Continue to Google", the
events over Google's CalDAV interface with an OAuth token (the same way as iCloud), the calendar list and the
answers to invitations over the Google Calendar API (TIME-18), because Google's CalDAV sends no answers.

Every call to Google goes through ``httpx`` with ``transport``, which the tests replace by a fake Google; the
addresses are module values the tests point at their own CalDAV server. The client secret and the members' grants are
sealed (EC-12); the operator sees how many members are connected, never who or what (AC-7).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
from datetime import timedelta
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import SOURCE_GOOGLE, STATE_EXPIRED, STATE_OK, Account, Calendar, Event, Source
from ..security import decrypt_secret, encrypt_secret
from . import ical, live, settings_service, sync, vault
from .remote_dav import AUTH, DOWN, REFUSED, Remote, RemoteError

logger = logging.getLogger("nextime.google")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
API_URL = "https://www.googleapis.com/calendar/v3"
#: The events of one calendar over CalDAV; ``{id}`` is the calendar's id at Google.
CALDAV_URL = "https://apidata.googleusercontent.com/caldav/v2/{id}/events/"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
SCOPES = f"openid email {CALENDAR_SCOPE}"
CALLBACK_PATH = "/api/google/callback"
#: The tests' fake Google.
transport: httpx.BaseTransport | None = None
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
#: How long a started "Continue to Google" may take.
STATE_SECONDS = 600

_states: dict[str, dict[str, Any]] = {}
_tokens: dict[int, tuple[str, float]] = {}
_lock = threading.Lock()


def forget() -> None:
    with _lock:
        _states.clear()
        _tokens.clear()


def _client() -> httpx.Client:
    return httpx.Client(transport=transport, timeout=TIMEOUT, trust_env=False, follow_redirects=False)


def enabled(db: Session) -> bool:
    return settings_service.google_on(db)


def configured(db: Session) -> bool:
    return settings_service.google_configured(db)


def _client_secret(db: Session) -> str:
    return decrypt_secret(str(settings_service.get(db, "google_client_secret_enc") or ""), "google-client")


def redirect_uri(db: Session, base: str) -> str:
    """The address Google sends members back to: the address people reach nextime at from outside (EC-4)."""
    root = settings_service.public_url(db) or base.rstrip("/")
    return root + CALLBACK_PATH


# --- The operator's set-up (AC-1, AC-6, AC-7, EC-2, EC-3, EC-4, EC-13) ----------------------------------------------


def members(db: Session) -> int:
    return int(db.scalar(select(func.count(func.distinct(Source.account_id))).where(Source.kind == SOURCE_GOOGLE))
               or 0)


def setup_view(db: Session, base: str) -> dict[str, Any]:
    redirect = redirect_uri(db, base)
    stored = str(settings_service.get(db, "google_redirect") or "")
    host = (urlsplit(redirect).hostname or "").lower()
    return {
        "client_id": str(settings_service.get(db, "google_client_id") or ""),
        "secret_set": bool(_client_secret(db)),
        "redirect_uri": redirect,
        "https": redirect.startswith("https://") or host in ("localhost", "127.0.0.1"),
        "ready": configured(db) and bool(_client_secret(db)),
        "enabled": bool(settings_service.get(db, "google_enabled")),
        "members": members(db),
        # Restored elsewhere or moved: the address registered at Google is another (EC-13).
        "redirect_changed": bool(stored) and stored != redirect,
    }


def _check_client(client_id: str, client_secret: str, redirect: str) -> None:
    """A code that cannot be right, exchanged at Google: ``invalid_grant`` means the client fits, ``invalid_client``
    names the value that does not (EC-3)."""
    try:
        with _client() as client:
            answer = client.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": "nextime-check",
                                                  "client_id": client_id, "client_secret": client_secret,
                                                  "redirect_uri": redirect})
    except httpx.HTTPError:
        raise error("google_unreachable", "Google does not answer. Try again in a moment.", 502) from None
    try:
        body = answer.json()
    except ValueError:
        body = {}
    code = str(body.get("error", ""))
    if code in ("invalid_grant", "invalid_request") and answer.status_code < 500:
        return
    if code == "invalid_client" or answer.status_code == 401:
        described = str(body.get("error_description", "")).lower()
        if "not found" in described or "deleted" in described:
            raise error("google_client_id_wrong", "Google does not know this client ID.", 422, field="client_id")
        raise error("google_secret_wrong", "The client secret does not fit this client ID.", 422,
                    field="client_secret")
    if answer.status_code >= 500:
        raise error("google_unreachable", "Google does not answer. Try again in a moment.", 502)


def save_setup(db: Session, base: str, *, client_id: str, client_secret: str, confirm: bool) -> dict[str, Any]:
    client_id = "".join((client_id or "").split())
    client_secret = "".join((client_secret or "").split())
    if not client_id:
        raise error("google_client_id_missing", "Enter the client ID.", 422, field="client_id")
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise error("google_client_id_wrong", "Google does not know this client ID.", 422, field="client_id")
    before_id = str(settings_service.get(db, "google_client_id") or "")
    if not client_secret and before_id == client_id:
        client_secret = _client_secret(db)
    if not client_secret:
        raise error("google_secret_missing", "Enter the client secret.", 422, field="client_secret")
    changed = client_id != before_id or client_secret != _client_secret(db)
    connected = members(db)
    if changed and before_id and connected and not confirm:
        # Every member has to connect Google again after this (EC-2).
        raise error("google_change_needs_confirm", "Changed values make every member connect Google again.", 409,
                    count=connected)
    redirect = redirect_uri(db, base)
    _check_client(client_id, client_secret, redirect)
    settings_service.save(db, {"google_client_id": client_id,
                               "google_client_secret_enc": encrypt_secret(client_secret, "google-client"),
                               "google_redirect": redirect}, commit=False)
    if changed and before_id:
        _expire_all(db, "setup_changed")
    db.commit()
    logger.info("Google set up changed=%s members=%s", changed, connected)
    return setup_view(db, base)


def remove_setup(db: Session, base: str, *, confirm: bool) -> dict[str, Any]:
    connected = members(db)
    if connected and not confirm:
        raise error("google_change_needs_confirm", "Changed values make every member connect Google again.", 409,
                    count=connected)
    settings_service.save(db, {"google_client_id": "", "google_client_secret_enc": "", "google_redirect": ""},
                          commit=False)
    _expire_all(db, "setup_changed")
    db.commit()
    return setup_view(db, base)


def _expire_all(db: Session, problem: str) -> None:
    for source in db.scalars(select(Source).where(Source.kind == SOURCE_GOOGLE)):
        sync.set_state(db, source, STATE_EXPIRED, problem)
    forget()


# --- A member connects (AC-2, AC-8, EC-6, EC-7) ----------------------------------------------------------------------


def start(db: Session, account: Account, base: str, source_id: int | None = None) -> str:
    """The address at Google the browser goes to; the answer comes back to ``CALLBACK_PATH``."""
    if not enabled(db):
        raise error("google_not_ready", "Google is not set up on this server.", 409)
    login_hint = ""
    if source_id is not None:
        row = db.get(Source, source_id)
        if row is None or row.account_id != account.id or row.kind != SOURCE_GOOGLE:
            raise error("not_found", "Not found.", 404)
        login_hint = row.username
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.monotonic()
        for key in [key for key, value in _states.items() if value["until"] < now]:
            _states.pop(key, None)
        _states[state] = {"account": account.id, "verifier": verifier, "source": source_id,
                          "until": now + STATE_SECONDS, "redirect": redirect_uri(db, base)}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = {"client_id": str(settings_service.get(db, "google_client_id")), "redirect_uri": redirect_uri(db, base),
             "response_type": "code", "scope": SCOPES, "access_type": "offline", "prompt": "consent",
             "include_granted_scopes": "true", "state": state, "code_challenge": challenge,
             "code_challenge_method": "S256"}
    if login_hint:
        query["login_hint"] = login_hint
    return AUTH_URL + "?" + urlencode(query)


def _post_token(db: Session, data: dict[str, str]) -> dict[str, Any]:
    data = {**data, "client_id": str(settings_service.get(db, "google_client_id")),
            "client_secret": _client_secret(db)}
    try:
        with _client() as client:
            answer = client.post(TOKEN_URL, data=data)
    except httpx.HTTPError as exc:
        raise RemoteError(DOWN) from exc
    if answer.status_code >= 500 or answer.status_code == 429:
        raise RemoteError(DOWN, answer.status_code)
    try:
        body = answer.json()
    except ValueError:
        raise RemoteError(DOWN, answer.status_code) from None
    if answer.status_code >= 400:
        raise RemoteError(AUTH, answer.status_code)
    return body


def callback(db: Session, account: Account, *, code: str, state: str, problem: str) -> str:
    """Where the browser goes after Google: back to "Calendars and accounts", with the choice of calendars or with
    why nothing was connected (EC-6, EC-7)."""
    with _lock:
        started = _states.pop(state, None)
    page = "/settings/calendars"
    if started is None or started["account"] != account.id or started["until"] < time.monotonic():
        return page + "?google=expired"
    if problem or not code:
        return page + "?google=denied"
    try:
        grant = _post_token(db, {"grant_type": "authorization_code", "code": code, "code_verifier":
                                 started["verifier"], "redirect_uri": started["redirect"]})
    except RemoteError:
        return page + "?google=failed"
    if CALENDAR_SCOPE not in str(grant.get("scope", "")).split():
        # Signed in, but no access to the calendars granted: nothing works without it (EC-6).
        return page + "?google=denied"
    token = str(grant.get("access_token", ""))
    try:
        with _client() as client:
            answer = client.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"})
        email = str(answer.json().get("email", "")).strip().lower()
    except (httpx.HTTPError, ValueError):
        return page + "?google=failed"
    if not email:
        return page + "?google=failed"
    refresh = str(grant.get("refresh_token", ""))
    if started["source"] is not None:
        row = db.get(Source, started["source"])
        if row is None or row.account_id != account.id:
            return page + "?google=failed"
        if email != row.username:
            return page + "?google=other_account"
        previous = sync.access_of(row) or {}
        row.secret_enc = vault.seal({"refresh_token": refresh or str(previous.get("refresh_token", ""))},
                                    sync.place(row))
        _remember(row.id, token, grant)
        row.failures, row.due_at = 0, sync.clock()
        sync.set_state(db, row, STATE_OK)
        db.commit()
        sync.nudge()
        return page + f"?google=reconnected&source={row.id}"
    identity = vault.identity(SOURCE_GOOGLE, email)
    existing = db.scalar(select(Source).where(Source.account_id == account.id, Source.kind == SOURCE_GOOGLE,
                                              Source.identity == identity))
    if existing is not None:
        return page + f"?google=exists&choose={existing.id}"
    if not refresh:
        return page + "?google=failed"
    row = Source(account_id=account.id, kind=SOURCE_GOOGLE, identity=identity, label=email, username=email,
                 addresses=[f"mailto:{email}"], schedules=True, state=STATE_OK, due_at=sync.clock())
    try:
        db.add(row)
        db.flush()
    except IntegrityError:
        db.rollback()
        return page + "?google=exists"
    row.secret_enc = vault.seal({"refresh_token": refresh}, sync.place(row))
    _remember(row.id, token, grant)
    try:
        refresh_listing(db, row, {"refresh_token": refresh}, first=True)
    except RemoteError:
        pass
    live.touch_accounts(db, {account.id})
    db.commit()
    logger.info("Google connected source=%s", row.id)
    return page + f"?choose={row.id}"


def _remember(source_id: int, token: str, grant: dict[str, Any]) -> None:
    if token:
        with _lock:
            _tokens[source_id] = (token, time.monotonic() + max(60, int(grant.get("expires_in", 3600) or 3600)) - 60)


def drop_token(source_id: int) -> None:
    with _lock:
        _tokens.pop(source_id, None)


def access_token(db: Session, source: Source, secret: dict[str, Any]) -> str:
    with _lock:
        known = _tokens.get(source.id)
    if known is not None and known[1] > time.monotonic():
        return known[0]
    grant = _post_token(db, {"grant_type": "refresh_token", "refresh_token": str(secret.get("refresh_token", ""))})
    token = str(grant.get("access_token", ""))
    if not token:
        raise RemoteError(AUTH)
    _remember(source.id, token, grant)
    return token


def remote_for(db: Session, source: Source, secret: dict[str, Any]) -> Remote:
    """The CalDAV connection with the member's token; the calendar list comes from the API first."""
    token = access_token(db, source, secret)
    refresh_listing(db, source, secret)
    return Remote(CALDAV_URL.format(id="primary"), bearer=token)


def calendar_url(calendar_id: str) -> str:
    return CALDAV_URL.format(id=quote(calendar_id, safe="@"))


def calendar_id_of(url: str) -> str:
    prefix, _, _rest = CALDAV_URL.partition("{id}")
    return unquote(url[len(prefix):].split("/", 1)[0]) if url.startswith(prefix) else ""


def _api(db: Session, source: Source, secret: dict[str, Any], method: str, path: str, **kwargs: Any) -> Any:
    for attempt in (0, 1):
        token = access_token(db, source, secret)
        try:
            with _client() as client:
                answer = client.request(method, API_URL + path, headers={"Authorization": f"Bearer {token}"},
                                        **kwargs)
        except httpx.HTTPError as exc:
            raise RemoteError(DOWN) from exc
        if answer.status_code == 401 and attempt == 0:
            with _lock:
                _tokens.pop(source.id, None)
            continue
        if answer.status_code == 401:
            raise RemoteError(AUTH, 401)
        if answer.status_code == 429 or answer.status_code >= 500 or (
                answer.status_code == 403 and "rateLimit" in answer.text):
            raise RemoteError(DOWN, answer.status_code)
        if answer.status_code >= 400:
            raise RemoteError(REFUSED, answer.status_code)
        return answer.json() if answer.content else {}
    raise RemoteError(AUTH)


def refresh_listing(db: Session, source: Source, secret: dict[str, Any], *, first: bool = False) -> None:
    """The member's calendars at Google, with name, color and right (only to read: "read only")."""
    from .remote_dav import RemoteCalendar
    from .sources import apply_listing

    found = []
    page = ""
    for _ in range(20):
        body = _api(db, source, secret, "GET", "/users/me/calendarList",
                    params={"maxResults": 250, **({"pageToken": page} if page else {})})
        for item in body.get("items", []):
            role = str(item.get("accessRole", ""))
            if role == "freeBusyReader":
                continue
            found.append(RemoteCalendar(url=calendar_url(str(item.get("id", ""))),
                                        name=str(item.get("summaryOverride") or item.get("summary") or ""),
                                        color=str(item.get("backgroundColor", "")),
                                        read_only=role not in ("owner", "writer")))
        page = str(body.get("nextPageToken", "") or "")
        if not page:
            break
    if first:
        from .sources import _listing

        source.remote = _listing(found, None, first=True)
    else:
        apply_listing(db, source, found)


# --- Answers to invitations (TIME-18 AC-2, EC-6) ---------------------------------------------------------------------


def reply(db: Session, source: Source, calendar: Calendar, row: Event, answer: str, occurrence: str, *,
          send: bool = True) -> None:
    """The member's answer through the API, so that Google tells the one who invites; for one occurrence of a series
    on that occurrence only. ``send=False``: Google takes the answer and tells nobody (``sendUpdates=none``), for an
    answer that already went out by mail (TIME-26)."""
    secret = sync.access_of(source)
    if secret is None:
        raise RemoteError(AUTH)
    calendar_id = calendar_id_of(calendar.remote_url)
    path = f"/calendars/{quote(calendar_id, safe='')}/events"
    found = _api(db, source, secret, "GET", path, params={"iCalUID": row.uid, "maxResults": 5})
    items = found.get("items", [])
    if not items:
        raise RemoteError(REFUSED, 404)
    event_id = str(items[0].get("id", ""))
    target = items[0]
    if occurrence:
        parsed = ical.master_of(ical.parse(row.ical)).decoded("DTSTART")
        begin = ical._as_utc(parsed if not occurrence else _occurrence_moment(occurrence))
        instances = _api(db, source, secret, "GET", f"{path}/{quote(event_id, safe='')}/instances",
                         params={"timeMin": (begin - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                                 "timeMax": (begin + timedelta(days=2)).isoformat().replace("+00:00", "Z")})
        target = next((item for item in instances.get("items", []) if _instance_key(item) == occurrence), None)
        if target is None:
            raise RemoteError(REFUSED, 404)
        event_id = str(target.get("id", ""))
    attendees = list(target.get("attendees", []))
    mine = {address.lower() for address in [source.username]}
    for person in attendees:
        if person.get("self") or str(person.get("email", "")).lower() in mine:
            person["responseStatus"] = answer
    _api(db, source, secret, "PATCH", f"{path}/{quote(event_id, safe='')}",
         params={"sendUpdates": "all" if send else "none"},
         json={"attendees": attendees})


def _occurrence_moment(key: str) -> Any:
    from datetime import UTC, date, datetime

    if "T" in key:
        return datetime.strptime(key, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return date(int(key[0:4]), int(key[4:6]), int(key[6:8]))


def _instance_key(item: dict[str, Any]) -> str:
    from datetime import date, datetime

    original = item.get("originalStartTime") or {}
    if original.get("dateTime"):
        return ical.recurrence_key(datetime.fromisoformat(str(original["dateTime"])))
    if original.get("date"):
        return ical.recurrence_key(date.fromisoformat(str(original["date"])))
    return ""


# --- Invitations entered from a mail (TIME-26, for nexmail) ---------------------------------------------------------

#: PARTSTAT as Google's ``responseStatus``.
RESPONSES = {"NEEDS-ACTION": "needsAction", "ACCEPTED": "accepted", "TENTATIVE": "tentative", "DECLINED": "declined"}


def _when(value: Any) -> dict[str, str]:
    from datetime import datetime

    if not isinstance(value, datetime):
        return {"date": value.isoformat()}
    zone = ical.zone_name(value)
    moment = ical._as_utc(value).isoformat().replace("+00:00", "Z")
    return {"dateTime": moment, **({"timeZone": zone} if zone else {})}


def _person(value: Any) -> dict[str, str]:
    text = str(value or "").strip()
    email = text[7:] if text.lower().startswith("mailto:") else text
    name = str((getattr(value, "params", {}) or {}).get("CN", "") or "").strip().strip('"')
    return {"email": email, **({"displayName": name} if name else {})}


def imported_body(row: Event) -> dict[str, Any]:
    """The event as Google's ``events.import`` takes it: its UID, times, rule, organizer and attendees with their
    answers, SEQUENCE. Changed single occurrences of a series are not carried (Google takes them only one by one)."""
    master = ical.master_of(ical.parse(row.ical))
    start = master.decoded("DTSTART")
    body: dict[str, Any] = {
        "iCalUID": row.uid,
        "summary": str(master.get("SUMMARY", "")),
        "description": str(master.get("DESCRIPTION", "")),
        "location": str(master.get("LOCATION", "")),
        "start": _when(start),
        "end": _when(start + ical.duration_of(master)),
        "sequence": int(master.get("SEQUENCE", 0) or 0),
        "transparency": "transparent" if str(master.get("TRANSP", "")).upper() == "TRANSPARENT" else "opaque",
        "status": "cancelled" if str(master.get("STATUS", "")).upper() == "CANCELLED" else "confirmed",
    }
    organizer = master.get("ORGANIZER")
    if organizer is not None:
        body["organizer"] = _person(organizer)
    attendees = master.get("ATTENDEE")
    if attendees is not None:
        body["attendees"] = [{**_person(item), "responseStatus": RESPONSES.get(
            str(item.params.get("PARTSTAT", "NEEDS-ACTION")).upper(), "needsAction")}
            for item in (attendees if isinstance(attendees, list) else [attendees])]
    rules = [line.to_ical().decode() if hasattr(line, "to_ical") else str(line)
             for line in master.content_lines()
             if str(line).split(":", 1)[0].split(";", 1)[0].upper() in ("RRULE", "EXDATE", "RDATE")]
    if rules:
        body["recurrence"] = rules
    return body


def import_event(db: Session, source: Source, calendar: Calendar, row: Event) -> None:
    """An invitation entered from a mail into a Google calendar: ``events.import`` keeps its UID, organizer and
    attendees and sends nobody anything; the same UID again updates it there."""
    secret = sync.access_of(source)
    if secret is None:
        raise RemoteError(AUTH)
    path = f"/calendars/{quote(calendar_id_of(calendar.remote_url), safe='')}/events/import"
    _api(db, source, secret, "POST", path, json=imported_body(row))


def delete_imported(db: Session, source: Source, calendar: Calendar, uid: str) -> None:
    """The copy of an invitation from a mail goes at Google too, telling nobody (``sendUpdates=none``)."""
    secret = sync.access_of(source)
    if secret is None:
        raise RemoteError(AUTH)
    path = f"/calendars/{quote(calendar_id_of(calendar.remote_url), safe='')}/events"
    found = _api(db, source, secret, "GET", path, params={"iCalUID": uid, "maxResults": 5})
    for item in found.get("items", []):
        _api(db, source, secret, "DELETE", f"{path}/{quote(str(item.get('id', '')), safe='')}",
             params={"sendUpdates": "none"})
