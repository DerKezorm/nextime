"""The other side for the tests of iCloud, Google, CalDAV servers and subscriptions (TIME-15 to TIME-18).

No test goes out to the net. As "iCloud" and "another CalDAV server" serves nextime's own calendar account under
``/dav/`` (TIME-20), started in a thread with real HTTP for the library ``caldav``; the account ``remote`` there owns
the calendars, a device password is the "app-specific password". A switchboard in front of it plays what a real
source does: not answering, slow, refusing changes, ``If-Match`` checked strictly, scheduling announced, Google's
Bearer tokens. Google's OAuth and Calendar API are a fake behind ``httpx.MockTransport``; subscriptions come from
another one.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Account, Calendar, Event, Source
from app.services import devices, google, internet, sources, subscriptions, sync

from .calendar_helpers import make_calendar
from .conftest import new_client


@dataclass
class Switches:
    down: bool = False
    slow: float = 0.0
    refuse_writes: bool = False
    #: Only writing fails, with this status and text (503, 408, a 403 that throttles); reading works.
    writes_answer: tuple[int, bytes] | None = None
    strict_if_match: bool = False
    schedules: bool = False
    #: Google's tokens as the account they stand for: ``{token: (user, device password)}``.
    bearer: dict[str, tuple[str, str]] = field(default_factory=dict)
    seen: list[tuple[str, str]] = field(default_factory=list)

    def reset(self) -> None:
        self.down = self.refuse_writes = self.strict_if_match = self.schedules = False
        self.writes_answer = None
        self.slow = 0.0
        self.bearer.clear()
        self.seen.clear()


SWITCHES = Switches()


class Switchboard:
    """ASGI in front of the app: what the tests switch on happens before the app sees the request."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        SWITCHES.seen.append((method, path))
        if path.startswith("/not-dav"):
            await _answer(send, 200, b"<html><body>Welcome</body></html>", "text/html")
            return
        if SWITCHES.slow:
            await asyncio.sleep(SWITCHES.slow)
        if SWITCHES.down:
            await _answer(send, 503, b"", "text/plain")
            return
        headers = [(key, value) for key, value in scope["headers"] if key != b"authorization"]
        for key, value in scope["headers"]:
            if key == b"authorization" and value.startswith(b"Bearer "):
                found = SWITCHES.bearer.get(value[7:].decode())
                if found is None:
                    await _answer(send, 401, b"", "text/plain", [(b"www-authenticate", b'Bearer realm="google"')])
                    return
                token = base64.b64encode(f"{found[0]}:{found[1]}".encode())
                headers.append((b"authorization", b"Basic " + token))
            elif key == b"authorization":
                headers.append((key, value))
        scope = {**scope, "headers": headers}
        if method in ("PUT", "DELETE") and SWITCHES.writes_answer is not None:
            await _answer(send, SWITCHES.writes_answer[0], SWITCHES.writes_answer[1], "application/json")
            return
        if method in ("PUT", "DELETE") and SWITCHES.refuse_writes:
            await _answer(send, 403, b"", "text/plain")
            return
        if method in ("PUT", "DELETE") and SWITCHES.strict_if_match:
            given = dict(headers).get(b"if-match", b"").decode()
            if given and given != _current_etag(path):
                await _answer(send, 412, b"", "text/plain")
                return
        if method == "OPTIONS" and SWITCHES.schedules:
            async def scheduling(message: dict) -> None:
                if message["type"] == "http.response.start":
                    message = {**message, "headers": [
                        (key, value + b", calendar-auto-schedule" if key.lower() == b"dav" else value)
                        for key, value in message["headers"]]}
                await send(message)

            await self.inner(scope, receive, scheduling)
            return
        await self.inner(scope, receive, send)


async def _answer(send: Callable, status: int, body: bytes, kind: str,
                  extra: list[tuple[bytes, bytes]] | None = None) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", kind.encode()), (b"content-length", str(len(body)).encode())]
                + (extra or [])})
    await send({"type": "http.response.body", "body": body})


def _current_etag(path: str) -> str:
    parts = [unquote(part) for part in path.strip("/").split("/")]
    if len(parts) != 5:
        return ""
    with SessionLocal() as db:
        calendar = db.scalar(select(Calendar).where(Calendar.dav_name == parts[3]))
        if calendar is None:
            return ""
        row = db.scalar(select(Event).where(Event.calendar_id == calendar.id, Event.href == parts[4],
                                            Event.deleted_at.is_(None)))
        return row.etag if row is not None else ""


@pytest.fixture(scope="module")
def dav_server() -> Iterator[str]:
    config = uvicorn.Config(Switchboard(app), host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    for _ in range(500):
        if running.started:
            break
        time.sleep(0.01)
    port = running.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    running.should_exit = True
    thread.join(10)


@pytest.fixture(autouse=True)
def quiet_world(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The internet counts as there, the switches are off, nothing is remembered from the test before."""
    SWITCHES.reset()
    google.forget()
    monkeypatch.setattr(internet, "probe_override", lambda: True)
    monkeypatch.setattr(subscriptions, "transport", None)
    monkeypatch.setattr(google, "transport", None)
    # No DNS from a test: every host of a subscription is somewhere on the net, unless a test says otherwise.
    monkeypatch.setattr(subscriptions, "resolve", lambda host: ["203.0.113.10"])
    yield
    SWITCHES.reset()


@dataclass
class Remote:
    """The account at the other side: its name, its "app-specific password", its browser."""

    account: Account
    password: str
    browser: TestClient

    def calendar(self, name: str, **values: Any) -> int:
        return make_calendar(self.browser, name, **values)

    def dav_name(self, calendar_id: int) -> str:
        with SessionLocal() as db:
            row = db.get(Calendar, calendar_id)
            assert row is not None
            return row.dav_name

    def put(self, calendar_id: int, name: str, text: str) -> str:
        answer = self.raw("PUT", f"{self.calendar_path(calendar_id)}{name}", text,
                          **{"Content-Type": "text/calendar"})
        assert answer.status_code in (201, 204), answer.text
        return answer.headers["etag"]

    def delete(self, calendar_id: int, name: str) -> None:
        assert self.raw("DELETE", f"{self.calendar_path(calendar_id)}{name}").status_code == 204

    def get(self, calendar_id: int, name: str) -> httpx.Response:
        return self.raw("GET", f"{self.calendar_path(calendar_id)}{name}")

    def calendar_path(self, calendar_id: int) -> str:
        return f"/dav/calendars/{self.account.name}/{self.dav_name(calendar_id)}/"

    def raw(self, method: str, path: str, body: str = "", **headers: str) -> httpx.Response:
        token = base64.b64encode(f"{self.account.name}:{self.password}".encode()).decode()
        with TestClient(app, base_url="https://testserver") as plain:
            return plain.request(method, path, content=body.encode(), headers={
                "Authorization": f"Basic {token}", **headers})


def remote_account(name: str = "remote") -> Iterator[Remote]:
    from .conftest import make_account

    account = make_account(name)
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        _device, password, _token = devices.create(db, row, "nextime elsewhere")
    with new_client(account) as browser:
        yield Remote(account, password, browser)


def event_text(uid: str, title: str, start: str = "20261126T163000", end: str = "20261126T171500", *,
               extra: str = "", zone: str = "Europe/Berlin") -> str:
    when = f"DTSTART;TZID={zone}:{start}\r\nDTEND;TZID={zone}:{end}" if "T" in start else (
        f"DTSTART;VALUE=DATE:{start}\r\nDTEND;VALUE=DATE:{end}")
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iPhone OS 18.1//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261120T090000Z\r\n{when}\r\nSUMMARY:{title}\r\n{extra}"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


def connect(client: TestClient, server: str, remote: Remote, *, kind: str = "caldav", password: str | None = None,
            ref: str | None = None) -> httpx.Response:
    body = {"kind": kind, "server": server + "/.well-known/caldav", "username": remote.account.name,
            "password": remote.password if password is None else password}
    if ref:
        body["client_ref"] = ref
    return client.post("/api/sources/dav", json=body)


def connected(client: TestClient, server: str, remote: Remote, names: list[str] | None = None, *,
              kind: str = "caldav") -> int:
    """Connected and the calendars chosen (all, or the ones named); the source's id."""
    answer = connect(client, server, remote, kind=kind)
    assert answer.status_code in (200, 201), answer.text
    found = answer.json()
    urls = [item["url"] for item in found["calendars"] if names is None or item["name"] in names]
    chosen = client.put(f"/api/sources/{found['source_id']}/calendars", json={"urls": urls})
    assert chosen.status_code == 200, chosen.text
    return int(found["source_id"])


def round_now(source_id: int) -> None:
    """A round of the source now, as the loop runs it when due."""
    with SessionLocal() as db:
        row = db.get(Source, source_id)
        assert row is not None
        row.due_at = None
        db.commit()
    assert sync.run_source(source_id)


def source_row(client: TestClient, source_id: int) -> dict[str, Any]:
    answer = client.get("/api/sources")
    assert answer.status_code == 200, answer.text
    return next(row for row in answer.json()["sources"] if row["id"] == source_id)


def mirrored(source_id: int) -> list[Calendar]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(Calendar).where(Calendar.source_id == source_id).order_by(Calendar.id)))
        for row in rows:
            db.expunge(row)
        return rows


def use_icloud(monkeypatch: pytest.MonkeyPatch, server: str) -> None:
    monkeypatch.setattr(sources, "ICLOUD_URL", server + "/.well-known/caldav")


# --- A fake Google -----------------------------------------------------------------------------------------------


@dataclass
class FakeGoogle:
    """Google's token endpoint, user info and Calendar API, as far as nextime asks them."""

    client_id: str = "1234-test.apps.googleusercontent.com"
    client_secret: str = "test-client-secret"
    email: str = "familie.test@example.com"
    #: The calendars the account has: ``(id, summary, color, accessRole)``; the ids are CalDAV names at ``remote``.
    calendars: list[tuple[str, str, str, str]] = field(default_factory=list)
    scope: str = google.SCOPES
    refresh_valid: bool = True
    down: bool = False
    #: The access token handed out; the CalDAV side knows it through ``SWITCHES.bearer``.
    access: str = "google-access-1"
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    patches: list[tuple[str, dict[str, Any], dict[str, str]]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(f"{request.method} {urlsplit(url).path}")
        if self.down:
            return httpx.Response(503)
        if url.startswith(google.TOKEN_URL):
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            if form.get("client_id") != self.client_id:
                return httpx.Response(401, json={"error": "invalid_client",
                                                 "error_description": "The OAuth client was not found."})
            if form.get("client_secret") != self.client_secret:
                return httpx.Response(401, json={"error": "invalid_client", "error_description": "Unauthorized"})
            if form.get("grant_type") == "refresh_token":
                if not self.refresh_valid:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": self.access, "expires_in": 3600})
            if form.get("code") != "good-code":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": self.access, "refresh_token": "refresh-1",
                                             "expires_in": 3600, "scope": self.scope})
        if url.startswith(google.USERINFO_URL):
            return httpx.Response(200, json={"email": self.email})
        if url.startswith(google.API_URL):
            if request.headers.get("authorization") != f"Bearer {self.access}":
                return httpx.Response(401)
            path = urlsplit(url).path[len(urlsplit(google.API_URL).path):]
            if path == "/users/me/calendarList":
                return httpx.Response(200, json={"items": [
                    {"id": key, "summary": name, "backgroundColor": color, "accessRole": role}
                    for key, name, color, role in self.calendars]})
            if request.method == "GET" and path.endswith("/events"):
                uid = request.url.params.get("iCalUID", "")
                found = [item for item in self.events.values() if item["iCalUID"] == uid]
                return httpx.Response(200, json={"items": found})
            if request.method == "GET" and path.endswith("/instances"):
                event_id = unquote(path.split("/")[-2])
                return httpx.Response(200, json={"items": self.events.get(event_id, {}).get("instances", [])})
            if request.method == "PATCH":
                event_id = unquote(path.split("/")[-1])
                self.patches.append((event_id, json.loads(request.content), dict(request.url.params)))
                return httpx.Response(200, json={"id": event_id})
        return httpx.Response(404)
