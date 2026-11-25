"""TIME-20 after the review of stage 2: hidden calendars stay off devices (TIME-4 AC-3), open pages hear of changes at
once (AC-4), HTTPS only (EC-21), a changed or ended share makes devices sync anew (EC-16, EC-5), no task lists from
Apple Reminders (AC-14), a new mark after a restored backup (EC-20), the order and color set on a device (TIME-4
AC-5, AC-2), PROPPATCH all or nothing."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import RIGHT_BUSY, RIGHT_EDIT, RIGHT_READ, Account, Calendar
from app.security import SESSION_COOKIE, start_session
from app.services import backup_hooks, calendars, live

from .calendar_helpers import calendars_of, make_calendar, make_event, named, share, unshare
from .conftest import make_account, new_client
from .test_time20 import EVENT, _sync_report, calendar_url, device_for, raw

D = "DAV:"


def _home(account: Account, password: str) -> list[str]:
    answer = raw("PROPFIND", f"/dav/calendars/{account.name}/", account, password,
                 '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>', Depth="1")
    root = etree.fromstring(answer.content)
    return [node.text or "" for node in root.iter(f"{{{D}}}displayname")][1:]


def _ctag(account: Account, password: str, calendar_id: int) -> str:
    answer = raw("PROPFIND", calendar_url(account, calendar_id), account, password,
                 '<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/"><d:prop><cs:getctag/>'
                 "</d:prop></d:propfind>", Depth="0")
    return etree.fromstring(answer.content).findtext(".//{http://calendarserver.org/ns/}getctag") or ""


# --- TIME-4 AC-3: hidden is hidden on every device ---------------------------------------------------------------


def test_time4_ac3_a_hidden_calendar_stays_off_the_calendar_account_until_shown(client: TestClient,
                                                                              account: Account) -> None:
    password = device_for(account)
    calendar = make_calendar(client, "Verein")
    made = make_event(client, "Training", calendar_id=calendar)
    assert _home(account, password) == ["Personal", "Verein"]
    path = calendar_url(account, calendar)
    client.patch(f"/api/calendars/{calendar}", json={"hidden": True})
    assert _home(account, password) == ["Personal"]
    assert raw("PROPFIND", path, account, password, Depth="0").status_code == 404
    assert raw("GET", f"{path}{made['id']}.ics", account, password).status_code == 404
    client.patch(f"/api/calendars/{calendar}", json={"hidden": False})
    assert _home(account, password) == ["Personal", "Verein"]
    _status, entries, _token = _sync_report(account, password, calendar)
    assert len(entries) == 1


def test_time4_ac3_the_setup_page_lists_no_hidden_calendar(client: TestClient, account: Account) -> None:
    calendar = make_calendar(client, "Verein")
    client.patch(f"/api/calendars/{calendar}", json={"hidden": True})
    assert [row["name"] for row in client.get("/api/devices").json()["calendars"]] == ["Personal"]


# --- AC-4: open pages hear of changes at once --------------------------------------------------------------------


async def test_time20_ac4_a_change_tells_every_page_that_sees_the_calendar_and_nobody_else(client: TestClient,
                                                                                        account: Account) -> None:
    anna, ben = make_account("anna"), make_account("ben")
    calendar = make_calendar(client, "Familie")
    share(calendar, anna.id, RIGHT_READ)
    mine, annas, bens = live.subscribe(account.id), live.subscribe(anna.id), live.subscribe(ben.id)
    try:
        password = device_for(account)
        path = calendar_url(account, calendar) + "vom-iphone.ics"
        answer = await asyncio.to_thread(raw, "PUT", path, account, password, EVENT)
        assert answer.status_code == 201
        assert await asyncio.wait_for(mine[1].get(), 2) == "changed"
        assert await asyncio.wait_for(annas[1].get(), 2) == "changed"
        assert bens[1].empty()
    finally:
        for account_id, entry in ((account.id, mine), (anna.id, annas), (ben.id, bens)):
            live.unsubscribe(account_id, entry)


async def test_time20_ac4_a_change_rolled_back_tells_nobody(account: Account, client: TestClient) -> None:
    entry = live.subscribe(account.id)
    try:
        def failing() -> None:
            with SessionLocal() as db:
                live.touch_accounts(db, {account.id})
                db.rollback()

        await asyncio.to_thread(failing)
        await asyncio.sleep(0.05)
        assert entry[1].empty()
    finally:
        live.unsubscribe(account.id, entry)


@pytest.fixture(scope="module")
def http_server() -> Iterator[str]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    for _ in range(500):
        if running.started:
            break
        time.sleep(0.01)
    yield f"http://127.0.0.1:{running.servers[0].sockets[0].getsockname()[1]}"
    running.should_exit = True
    thread.join(10)


def test_time20_ac4_the_open_page_gets_changed_over_its_stream(http_server: str, client: TestClient,
                                                             account: Account) -> None:
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        token = start_session(db, row, "127.0.0.1", "tests")
    lines: list[str] = []
    with httpx.Client(base_url=http_server, cookies={SESSION_COOKIE: token}, timeout=10) as browser, \
            browser.stream("GET", "/api/live") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        reader = stream.iter_lines()
        lines.append(next(reader))
        for _ in range(200):
            if live.listening(account.id):
                break
            time.sleep(0.01)
        make_event(client, "Vom anderen Gerät")
        for line in reader:
            lines.append(line)
            if line.startswith("event:"):
                break
    assert "event: changed" in lines
    with httpx.Client(base_url=http_server, timeout=10) as stranger:
        assert stranger.get("/api/live").status_code == 401


# --- EC-21: HTTPS only ---------------------------------------------------------------------------------------------


def test_time20_ec21_the_calendar_account_refuses_plain_http_without_looking_at_the_password(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    password = device_for(account)
    monkeypatch.setattr(get_settings(), "dav_allow_http", False)
    with TestClient(app, base_url="http://testserver") as plain:
        for path in ("/dav/", "/.well-known/caldav"):
            answer = plain.request("PROPFIND", path, headers={"Authorization": "Basic eDp5"},
                                   follow_redirects=False)
            assert answer.status_code == 403 and "HTTPS" in answer.text
        wrong = [plain.request("PROPFIND", "/dav/", auth=(account.name, f"wrong-{n}")).status_code for n in range(15)]
        assert set(wrong) == {403}
    assert raw("PROPFIND", "/dav/", account, password, Depth="0").status_code == 207


# --- EC-16, EC-5: a changed or ended share -------------------------------------------------------------------------


def test_time20_ec16_ec5_a_new_right_makes_devices_sync_anew(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas")
        make_event(browser, "Arzt Dr. Weber", calendar_id=shared)
        make_event(browser, "Elternabend", calendar_id=shared, start="2026-11-27T19:00", end="2026-11-27T20:00")
    share(shared, account.id, RIGHT_EDIT)
    password = device_for(account)
    before = _ctag(account, password, shared)
    _status, first, token = _sync_report(account, password, shared)
    assert len(first) == 2
    share(shared, account.id, RIGHT_BUSY)
    assert _ctag(account, password, shared) != before
    status, changed, _newer = _sync_report(account, password, shared, token)
    assert status == 207 and len(changed) == 2
    body = raw("REPORT", calendar_url(account, shared), account, password,
               '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-data/>'
               '</d:prop><c:filter><c:comp-filter name="VCALENDAR"/></c:filter></c:calendar-query>').text
    assert "Arzt" not in body and body.count("SUMMARY:Busy") == 2
    unshare(shared, account.id)
    assert raw("PROPFIND", calendar_url(account, shared), account, password, Depth="0").status_code == 404


# --- AC-14: task lists are no calendars -------------------------------------------------------------------------


def test_time20_ac14_a_task_list_from_apple_reminders_is_refused(client: TestClient, account: Account) -> None:
    password = device_for(account)
    tasks = ('<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop>'
             '<d:displayname>Einkauf</d:displayname><c:supported-calendar-component-set><c:comp name="VTODO"/>'
             '</c:supported-calendar-component-set></d:prop></d:set></c:mkcalendar>')
    answer = raw("MKCALENDAR", f"/dav/calendars/{account.name}/einkauf/", account, password, tasks)
    assert answer.status_code == 403 and b"supported-calendar-component" in answer.content
    assert "Einkauf" not in [row["name"] for row in calendars_of(client)["calendars"]]
    both = tasks.replace('<c:comp name="VTODO"/>', '<c:comp name="VEVENT"/><c:comp name="VTODO"/>')
    assert raw("MKCALENDAR", f"/dav/calendars/{account.name}/beides/", account, password, both).status_code == 201


def test_time20_ac14_time4_custom_colour_a_new_calendar_from_the_device_keeps_its_color(client: TestClient,
                                                                                        account: Account) -> None:
    """A calendar made on the iPhone keeps the color it was made with (decided while testing, 2026-11-23)."""
    password = device_for(account)
    made = ('<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
            'xmlns:a="http://apple.com/ns/ical/"><d:set><d:prop><d:displayname>Garten</d:displayname>'
            '<a:calendar-color>#2562A9FF</a:calendar-color></d:prop></d:set></c:mkcalendar>')
    assert raw("MKCALENDAR", f"/dav/calendars/{account.name}/garten/", account, password, made).status_code == 201
    assert named(client, "Garten")["color"] == "#2562a9"


# --- EC-20: after a restored backup ---------------------------------------------------------------------------------


def test_time20_ec20_a_restored_backup_gives_tokens_and_tags_a_new_mark(client: TestClient, account: Account) -> None:
    password = device_for(account)
    calendar = named(client, "Personal")["id"]
    make_event(client, "Eins")
    _status, _entries, token = _sync_report(account, password, calendar)
    tag = _ctag(account, password, calendar)
    backup_hooks.new_sync_mark(get_settings().database_path)
    status, _entries, _token = _sync_report(account, password, calendar, token)
    assert status == 403
    assert _ctag(account, password, calendar) != tag
    status, entries, fresh = _sync_report(account, password, calendar)
    assert status == 207 and len(entries) == 1 and fresh != token


# --- TIME-4 AC-5 and AC-2 from a device, PROPPATCH all or nothing -------------------------------------------------


def _proppatch(props: str) -> str:
    return ('<d:propertyupdate xmlns:d="DAV:" xmlns:a="http://apple.com/ns/ical/"><d:set><d:prop>' + props
            + "</d:prop></d:set></d:propertyupdate>")


def test_time4_ac5_the_order_set_on_a_device_holds_in_nextime(client: TestClient, account: Account) -> None:
    password = device_for(account)
    first, second = make_calendar(client, "A"), make_calendar(client, "B")
    for calendar, order in ((second, 1), (named(client, "Personal")["id"], 2), (first, 3)):
        answer = raw("PROPPATCH", calendar_url(account, calendar), account, password,
                     _proppatch(f"<a:calendar-order>{order}</a:calendar-order>"))
        assert answer.status_code == 207 and b"200 OK" in answer.content
    assert [row["name"] for row in calendars_of(client)["calendars"]] == ["B", "Personal", "A"]
    assert _home(account, password) == ["B", "Personal", "A"]


def test_time20_proppatch_on_a_shared_calendar_is_all_or_nothing(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas")
    share(shared, account.id, RIGHT_EDIT)
    password = device_for(account)
    answer = raw("PROPPATCH", calendar_url(account, shared), account, password,
                 _proppatch("<d:displayname>Meins</d:displayname><a:calendar-order>0</a:calendar-order>"))
    assert b"403 Forbidden" in answer.content and b"424 Failed Dependency" in answer.content
    with SessionLocal() as db:
        assert db.scalar(select(Calendar.name).where(Calendar.id == shared)) == "Annas"
        assert calendars.setting(db, account.id, shared).position != 0
    color = raw("PROPPATCH", calendar_url(account, named(client, "Personal")["id"]), account, password,
                _proppatch("<a:calendar-color>#C62F47FF</a:calendar-color>"))
    assert b"200 OK" in color.content and named(client, "Personal")["color"] == "red"
