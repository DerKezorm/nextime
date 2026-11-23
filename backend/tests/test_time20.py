"""TIME-20 Calendar account for iPhone and co: the CalDAV server under /dav/, played through with the library
``caldav`` as the other side (as Apple Calendar and DAVx5 would), and the setup with device passwords, QR link and
profile.

The library speaks real HTTP: a server runs in a thread for the tests of this file and stops with them. Requests that
need no client library go through the test client.
"""

from __future__ import annotations

import base64
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import caldav
import icalendar
import pytest
import uvicorn
from caldav.elements import dav as dav_elements
from caldav.elements import ical as ical_elements
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import func, insert, select

from app.db import SessionLocal
from app.main import app
from app.models import RIGHT_BUSY, RIGHT_EDIT, RIGHT_READ, Account, Calendar, Device, DeviceSetup, Event, utcnow
from app.services import devices, ical, settings_service

from .calendar_helpers import calendars_of, code, make_calendar, make_event, named, share, unshare, window
from .conftest import TAB, make_account, new_client

D, C = "DAV:", "urn:ietf:params:xml:ns:caldav"
IPHONE = "iOS/18.1 (22B83) dataaccessd/1.0"


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
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


def device_for(account: Account, name: str = "iPhone") -> str:
    """A device of ``account`` and its password (as after naming it on the account page)."""
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        _device, password, _token = devices.create(db, row, name)
    return password


def connect(server: str, account: Account, password: str, path: str = "/.well-known/caldav") -> caldav.DAVClient:
    return caldav.DAVClient(url=server + path, username=account.name, password=password, require_tls=False,
                            headers={"User-Agent": IPHONE})


def basic(account: Account, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{account.name}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "User-Agent": IPHONE}


def raw(method: str, path: str, account: Account, password: str, body: str | bytes = b"",
        **headers: str) -> object:
    with TestClient(app, base_url="https://testserver") as plain:
        return plain.request(method, path, content=body, headers={**basic(account, password), **headers})


def dav_name(calendar_id: int) -> str:
    with SessionLocal() as db:
        row = db.get(Calendar, calendar_id)
        assert row is not None
        return row.dav_name


def calendar_url(account: Account, calendar_id: int) -> str:
    return f"/dav/calendars/{account.name}/{dav_name(calendar_id)}/"


def by_name(principal: caldav.Principal, name: str) -> caldav.Calendar:
    return next(item for item in principal.calendars() if item.get_display_name() == name)


EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iPhone OS 18.1//EN
BEGIN:VEVENT
UID:zahnarzt-ben@example.com
DTSTAMP:20261126T150000Z
DTSTART;TZID=Europe/Berlin:20261126T163000
DTEND;TZID=Europe/Berlin:20261126T171500
SUMMARY:Zahnarzt Ben
LOCATION:Praxis
TRANSP:OPAQUE
X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC
X-NEXTIME-UNKNOWN:stays as it is
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
DESCRIPTION:Reminder
END:VALARM
END:VEVENT
END:VCALENDAR
"""


# --- Discovery, rights (AC-3, AC-12, EC-16) --------------------------------------------------------------------


def test_time20_ac3_discovery_finds_own_and_shared_calendars_with_name_and_color(server: str, client: TestClient,
                                                                               account: Account) -> None:
    make_calendar(client, "Familie", color="green")
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas Arbeit", color="gray")
    share(shared, account.id, RIGHT_READ)
    principal = connect(server, account, device_for(account)).principal()
    found = {item.get_display_name(): item for item in principal.calendars()}
    assert sorted(found) == ["Annas Arbeit", "Familie", "Personal"]
    color = found["Familie"].get_properties([ical_elements.CalendarColor()])
    assert list(color.values()) == ["#2F7D4FFF"]
    assert found["Familie"].get_supported_components() == ["VEVENT"]


def test_time20_ac12_ec16_new_calendars_come_and_ended_shares_go_without_new_setup(server: str, client: TestClient,
                                                                                 account: Account) -> None:
    password = device_for(account)
    principal = connect(server, account, password).principal()
    assert [item.get_display_name() for item in principal.calendars()] == ["Personal"]
    make_calendar(client, "Neu nach der Einrichtung")
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas")
    share(shared, account.id, RIGHT_EDIT)
    names = sorted(item.get_display_name() for item in principal.calendars())
    assert names == ["Annas", "Neu nach der Einrichtung", "Personal"]
    share(shared, account.id, RIGHT_READ)
    privileges = raw("PROPFIND", calendar_url(account, shared), account, password,
                     '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-privilege-set/></d:prop></d:propfind>',
                     Depth="0")
    assert b"write-content" not in privileges.content and b"<d:read/>" in privileges.content
    unshare(shared, account.id)
    assert "Annas" not in [item.get_display_name() for item in principal.calendars()]


# --- Events through the device (AC-4, EC-4, EC-10, EC-11) ---------------------------------------------------------


def test_time20_ac4_ec4_put_get_change_delete_with_etag_and_unknown_fields_stay(server: str, client: TestClient,
                                                                             account: Account) -> None:
    principal = connect(server, account, device_for(account)).principal()
    calendar = by_name(principal, "Personal")
    saved = calendar.save_event(EVENT)
    assert [row["title"] for row in window(client)] == ["Zahnarzt Ben"]
    fetched = calendar.event_by_uid("zahnarzt-ben@example.com")
    assert "X-NEXTIME-UNKNOWN:stays as it is" in fetched.data and "BEGIN:VALARM" in fetched.data
    component = fetched.icalendar_component
    component["SUMMARY"] = icalendar.vText("Zahnarzt Ben (verschoben)")
    fetched.save()
    assert [row["title"] for row in window(client)] == ["Zahnarzt Ben (verschoben)"]
    again = calendar.event_by_uid("zahnarzt-ben@example.com").data
    assert "X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC" in again and "TRIGGER:-PT15M" in again
    saved.delete()
    assert window(client) == []
    with SessionLocal() as db:
        assert db.scalar(select(Event.deleted_at).where(Event.uid == "zahnarzt-ben@example.com")) is not None


def test_time20_ac4_etags_guard_creating_twice(client: TestClient, account: Account) -> None:
    password = device_for(account)
    path = calendar_url(account, named(client, "Personal")["id"]) + "zahnarzt.ics"
    made = raw("PUT", path, account, password, EVENT, **{"If-None-Match": "*", "Content-Type": "text/calendar"})
    assert made.status_code == 201 and made.headers["etag"].startswith('"')
    twice = raw("PUT", path, account, password, EVENT, **{"If-None-Match": "*"})
    assert twice.status_code == 412
    missing = raw("PUT", path.replace("zahnarzt", "nicht-da"), account, password, EVENT.replace("zahnarzt-ben", "x"),
                  **{"If-Match": '"abc"'})
    assert missing.status_code == 412
    fetched = raw("GET", path, account, password)
    assert fetched.status_code == 200 and fetched.headers["etag"] == made.headers["etag"]
    assert fetched.text == EVENT


def test_time20_ac4_a_change_in_nextime_reaches_the_device_at_the_next_sync(server: str, client: TestClient,
                                                                          account: Account) -> None:
    principal = connect(server, account, device_for(account)).principal()
    calendar = by_name(principal, "Personal")
    synced = calendar.objects_by_sync_token(load_objects=True)
    assert len(list(synced)) == 0
    made = make_event(client, "Elternabend")
    updated, deleted = synced.sync()
    assert [item.icalendar_component["SUMMARY"] for item in updated] == ["Elternabend"]
    client.delete(f"/api/events/{made['id']}")
    updated, deleted = synced.sync()
    assert len(deleted) == 1 and len(updated) == 0


def test_time20_ec10_titles_with_breaks_semicolons_and_markup_stay_text(server: str, client: TestClient,
                                                                       account: Account) -> None:
    title = "Treffen; Raum 2 <script>x()</script> END:VEVENT"
    made = make_event(client, title, notes="Zeile 1\nZeile 2;\\nkein Umbruch\nBEGIN:VALARM")
    principal = connect(server, account, device_for(account)).principal()
    fetched = by_name(principal, "Personal").event_by_uid(_uid(made["id"]))
    parsed = icalendar.Calendar.from_ical(fetched.data)
    events = parsed.walk("VEVENT")
    assert len(events) == 1 and not events[0].walk("VALARM")
    assert str(events[0]["SUMMARY"]) == title
    assert str(events[0]["DESCRIPTION"]) == "Zeile 1\nZeile 2;\\nkein Umbruch\nBEGIN:VALARM"


def _uid(event_id: int) -> str:
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        assert row is not None
        return row.uid


@pytest.mark.parametrize(("body", "condition"), [
    ("BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nSUMMARY:kaputt", "valid-calendar-data"),
    ("not a calendar", "valid-calendar-data"),
    (EVENT.replace("UID:zahnarzt-ben@example.com\n", ""), "valid-calendar-object-resource"),
    (EVENT.replace("DTSTART;TZID=Europe/Berlin:20261126T163000\n", ""), "valid-calendar-object-resource"),
    (EVENT.replace("DTEND;TZID=Europe/Berlin:20261126T171500", "DTEND;TZID=Europe/Berlin:20261126T100000"),
     "valid-calendar-object-resource"),
    (EVENT.replace("VEVENT", "VTODO"), "supported-calendar-component"),
    (EVENT.replace("END:VEVENT\nEND:VCALENDAR", "END:VEVENT\nBEGIN:VEVENT\nUID:zweiter\nDTSTART:20261126T100000Z\n"
                                                "END:VEVENT\nEND:VCALENDAR"), "valid-calendar-object-resource"),
])
def test_time20_ec11_broken_or_foreign_objects_are_refused_and_nothing_changes(client: TestClient, account: Account,
                                                                            body: str, condition: str) -> None:
    password = device_for(account)
    path = calendar_url(account, named(client, "Personal")["id"]) + "kaputt.ics"
    answer = raw("PUT", path, account, password, body)
    assert answer.status_code == 403 and condition.encode() in answer.content
    assert window(client, hidden="true") == []


def test_time20_ec11_an_object_over_the_largest_size_is_refused(client: TestClient, account: Account) -> None:
    password = device_for(account)
    path = calendar_url(account, named(client, "Personal")["id"]) + "gross.ics"
    attachment = "ATTACH;ENCODING=BASE64;VALUE=BINARY:" + "A" * (ical.MAX_OBJECT_BYTES + 10) + "\n"
    answer = raw("PUT", path, account, password, EVENT.replace("TRANSP:OPAQUE\n", "TRANSP:OPAQUE\n" + attachment))
    assert answer.status_code in (403, 413) and b"max-resource-size" in answer.content
    assert window(client) == []


def test_time20_ec4_a_series_with_exceptions_tokyo_whole_days_and_the_clocks_change(server: str, client: TestClient,
                                                                                  account: Account) -> None:
    series = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iPhone OS 18.1//EN
BEGIN:VEVENT
UID:training@example.com
DTSTAMP:20261126T150000Z
DTSTART;TZID=Europe/Berlin:20270322T183000
DTEND;TZID=Europe/Berlin:20270322T200000
RRULE:FREQ=WEEKLY;BYDAY=MO,TH;COUNT=6
EXDATE;TZID=Europe/Berlin:20270325T183000
SUMMARY:Training
END:VEVENT
BEGIN:VEVENT
UID:training@example.com
RECURRENCE-ID;TZID=Europe/Berlin:20270329T183000
DTSTAMP:20261126T150000Z
DTSTART;TZID=Europe/Berlin:20270329T190000
DTEND;TZID=Europe/Berlin:20270329T203000
SUMMARY:Training (später)
END:VEVENT
END:VCALENDAR
"""
    tokyo = EVENT.replace("zahnarzt-ben", "tokio").replace("Europe/Berlin", "Asia/Tokyo")
    whole = EVENT.replace("zahnarzt-ben", "urlaub").replace(
        "DTSTART;TZID=Europe/Berlin:20261126T163000", "DTSTART;VALUE=DATE:20261201").replace(
        "DTEND;TZID=Europe/Berlin:20261126T171500", "DTEND;VALUE=DATE:20261204")
    calendar = by_name(connect(server, account, device_for(account)).principal(), "Personal")
    for text in (series, tokyo, whole):
        calendar.save_event(text)
    rows = window(client, "2027-03-20T00:00:00Z", "2027-04-05T00:00:00Z")
    assert [(row["start"], row["title"]) for row in rows] == [
        ("2027-03-22T17:30:00Z", "Training"), ("2027-03-29T17:00:00Z", "Training (später)"),
        ("2027-04-01T16:30:00Z", "Training")]
    assert window(client, "2026-11-26T00:00:00Z", "2026-11-27T00:00:00Z")[0]["start"] == "2026-11-26T07:30:00Z"
    days = window(client, "2026-12-01T00:00:00Z", "2026-12-05T00:00:00Z")
    assert [(row["all_day"], row["start"], row["end"]) for row in days] == [(True, "2026-12-01", "2026-12-03")]
    back = icalendar.Calendar.from_ical(calendar.event_by_uid("training@example.com").data)
    assert [str(component.get("SUMMARY")) for component in back.walk("VEVENT")] == ["Training", "Training (später)"]
    assert back.walk("VEVENT")[0]["RRULE"].to_ical() == b"FREQ=WEEKLY;COUNT=6;BYDAY=MO,TH"
    assert "EXDATE" in back.walk("VEVENT")[0]


# --- Reports (multiget, calendar-query, sync-collection; EC-12, EC-13, EC-20) ---------------------------------


def test_time20_multiget_and_calendar_query_find_what_was_asked(server: str, client: TestClient,
                                                              account: Account) -> None:
    make_event(client, "Im November", "2026-11-26T10:00", "2026-11-26T11:00")
    make_event(client, "Serie", "2026-10-05T10:00", "2026-10-05T11:00",
               rule={"freq": "weekly", "weekdays": ["MO"]})
    make_event(client, "Im Januar", "2027-01-10T10:00", "2027-01-10T11:00")
    calendar = by_name(connect(server, account, device_for(account)).principal(), "Personal")
    found = calendar.search(start=datetime(2026, 11, 20, tzinfo=UTC), end=datetime(2026, 12, 1, tzinfo=UTC),
                            event=True, expand=False)
    assert sorted(str(item.icalendar_component["SUMMARY"]) for item in found) == ["Im November", "Serie"]
    urls = [item.url for item in calendar.events()]
    fetched = calendar.calendar_multiget(urls)
    assert sorted(str(item.icalendar_component["SUMMARY"]) for item in fetched) == ["Im Januar", "Im November",
                                                                                     "Serie"]
    todos = calendar.search(todo=True)
    assert todos == []


def _sync_report(account: Account, password: str, calendar_id: int, token: str = "") -> tuple[int, list, str]:
    body = (f'<d:sync-collection xmlns:d="DAV:"><d:sync-token>{token}</d:sync-token><d:sync-level>1</d:sync-level>'
            '<d:prop><d:getetag/></d:prop></d:sync-collection>')
    answer = raw("REPORT", calendar_url(account, calendar_id), account, password, body, Depth="1")
    if answer.status_code != 207:
        return answer.status_code, [], ""
    root = etree.fromstring(answer.content)
    entries = [(node.findtext(f"{{{D}}}href"), node.findtext(f"{{{D}}}status") or "200")
               for node in root.findall(f"{{{D}}}response")]
    return 207, entries, root.findtext(f"{{{D}}}sync-token") or ""


def test_time20_ec13_a_sync_broken_off_or_a_restart_resumes_without_doubles(client: TestClient,
                                                                          account: Account) -> None:
    password = device_for(account)
    calendar = named(client, "Personal")["id"]
    first = make_event(client, "Eins")
    _status, entries, token = _sync_report(account, password, calendar)
    assert len(entries) == 1
    make_event(client, "Zwei")
    client.patch(f"/api/events/{first['id']}", json={"title": "Eins geändert"})
    client.patch(f"/api/events/{first['id']}", json={"title": "Eins noch einmal"})
    status, entries, newer = _sync_report(account, password, calendar, token)
    assert status == 207 and len(entries) == 2 and newer != token
    # The answer got lost: the device asks again with the old token and gets the same, once each.
    _status, again, _newer = _sync_report(account, password, calendar, token)
    assert again == entries
    _status, nothing, _same = _sync_report(account, password, calendar, newer)
    assert nothing == []


def test_time20_ec20_a_token_from_before_a_restored_backup_starts_the_device_over(client: TestClient,
                                                                                 account: Account) -> None:
    password = device_for(account)
    calendar = named(client, "Personal")["id"]
    make_event(client, "Eins")
    _status, _entries, token = _sync_report(account, password, calendar)
    with SessionLocal() as db:
        from app.models import EventChange

        latest = db.scalar(select(EventChange).order_by(EventChange.id.desc()))
        assert latest is not None
        latest.created_at = latest.created_at - timedelta(seconds=5)  # same number, another moment: another database
        db.commit()
    status, _entries, _token = _sync_report(account, password, calendar, token)
    assert status == 403
    answer = raw("REPORT", calendar_url(account, calendar), account, password,
                 f'<d:sync-collection xmlns:d="DAV:"><d:sync-token>{token}</d:sync-token>'
                 '<d:prop><d:getetag/></d:prop></d:sync-collection>')
    assert b"valid-sync-token" in answer.content
    status, entries, _token = _sync_report(account, password, calendar)
    assert status == 207 and len(entries) == 1


def test_time20_ec12_twenty_calendars_with_twenty_thousand_events_come_complete(client: TestClient,
                                                                              account: Account) -> None:
    password = device_for(account)
    ids = [named(client, "Personal")["id"]] + [make_calendar(client, f"K{number}") for number in range(19)]
    now = utcnow()
    with SessionLocal() as db:
        rows = []
        for index in range(20_000):
            uid = f"bulk-{index}"
            text = EVENT.replace("zahnarzt-ben@example.com", uid)
            rows.append({"calendar_id": ids[index % 20], "uid": uid, "href": f"{uid}.ics", "ical": text,
                         "etag": ical.etag_of(text), "title": "Zahnarzt Ben", "starts_at": now, "ends_at": now,
                         "size": len(text), "created_at": now, "updated_at": now, "busy": True,
                         "time_zone": "Europe/Berlin", "location": "", "prev_etag": "", "prev_ical": ""})
        db.execute(insert(Event), rows)
        db.commit()
    total = 0
    for calendar in ids:
        answer = raw("PROPFIND", calendar_url(account, calendar), account, password,
                     '<d:propfind xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></d:propfind>', Depth="1")
        total += answer.content.count(b"<d:getetag>")
    assert total == 20_000
    _status, entries, _token = _sync_report(account, password, ids[3])
    assert len(entries) == 1000


# --- Read only and busy only (EC-1, EC-5) -------------------------------------------------------------------------


def test_time20_ec1_a_calendar_shared_for_reading_refuses_writing_from_the_device(server: str, client: TestClient,
                                                                                account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas")
        made = make_event(browser, "Arbeit", calendar_id=shared)
    share(shared, account.id, RIGHT_READ)
    password = device_for(account)
    calendar = by_name(connect(server, account, password).principal(), "Annas")
    with pytest.raises(caldav.lib.error.DAVError):
        calendar.save_event(EVENT)
    existing = f"{calendar_url(account, shared)}{_uid(made['id'])}.ics"
    assert raw("DELETE", existing, account, password).status_code == 403
    assert raw("PUT", existing, account, password, EVENT).status_code == 403
    with new_client(anna) as browser:
        assert [row["title"] for row in window(browser)] == ["Arbeit"]


def test_time20_ec5_busy_only_brings_blocks_without_details_and_no_free_ones(server: str, client: TestClient,
                                                                           account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas iCloud")
        make_event(browser, "Arzt Dr. Weber", calendar_id=shared, notes="Befund besprechen")
        make_event(browser, "Kaffee", calendar_id=shared, start="2026-11-27T10:00", end="2026-11-27T11:00",
                   busy=False)
    share(shared, account.id, RIGHT_BUSY)
    password = device_for(account)
    calendar = by_name(connect(server, account, password).principal(), "Annas iCloud")
    objects = calendar.events()
    assert len(objects) == 1
    data = objects[0].data
    assert "SUMMARY:Busy" in data and "Arzt" not in data and "Befund" not in data and "LOCATION" not in data
    with pytest.raises(caldav.lib.error.DAVError):
        calendar.save_event(EVENT)


# --- Devices: password, brake, lock out (AC-5, EC-2, EC-3, EC-17, EC-18) -----------------------------------------


def test_time20_ec2_a_locked_out_device_gets_nothing_and_the_others_go_on(client: TestClient, account: Account) -> None:
    lost, kept = device_for(account, "Altes iPhone"), device_for(account, "iPad")
    assert raw("PROPFIND", "/dav/", account, lost, Depth="0").status_code == 207
    with SessionLocal() as db:
        device = db.scalar(select(Device).where(Device.name == "Altes iPhone"))
        assert device is not None
        device_id = device.id
    with TestClient(app, base_url="https://testserver", headers=TAB) as secure:
        from .conftest import sign_in

        sign_in(secure, account)
        assert secure.delete(f"/api/devices/{device_id}").status_code == 204
    assert raw("PROPFIND", "/dav/", account, lost, Depth="0").status_code == 401
    assert raw("PROPFIND", "/dav/", account, kept, Depth="0").status_code == 207


def test_time20_ec3_many_wrong_passwords_are_braked(client: TestClient, account: Account) -> None:
    right = device_for(account)
    statuses = [raw("PROPFIND", "/dav/", account, f"wrong-{number}", Depth="0").status_code for number in range(1000)]
    assert statuses[:10] == [401] * 10
    assert statuses.count(429) > 900
    assert raw("PROPFIND", "/dav/", account, right, Depth="0").status_code == 429
    unauthenticated = TestClient(app).request("PROPFIND", "/dav/")
    assert unauthenticated.status_code == 401 and "Basic" in unauthenticated.headers["www-authenticate"]


def test_time20_ec17_a_device_password_opens_nothing_else_and_survives_a_new_password(client: TestClient,
                                                                                    account: Account) -> None:
    password = device_for(account)
    with TestClient(app, base_url="http://testserver", headers=TAB) as stranger:
        login = stranger.post("/api/auth/login", json={"name": account.name, "password": password})
        assert login.status_code == 401
        bearer = stranger.get("/api/calendars", headers={"Authorization": f"Bearer {password}"})
        assert bearer.status_code == 401
        basic_api = stranger.get("/api/calendars", headers=basic(account, password))
        assert basic_api.status_code == 401
    changed = client.put("/api/auth/password", json={"current": "correct horse battery",
                                                     "new": "a brand new password", "password_confirm":
                                                     "a brand new password"})
    assert changed.status_code == 204
    assert raw("PROPFIND", "/dav/", account, password, Depth="0").status_code == 207


def test_time20_ec18_a_blocked_or_deleted_account_gets_nothing(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    password = device_for(anna)
    assert raw("PROPFIND", "/dav/", anna, password, Depth="0").status_code == 207
    with SessionLocal() as db:
        db.get(Account, anna.id).blocked_at = utcnow()  # type: ignore[union-attr]
        db.commit()
    assert raw("PROPFIND", "/dav/", anna, password, Depth="0").status_code == 401
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        row.blocked_at = None  # type: ignore[union-attr]
        row.deleted_at = utcnow()  # type: ignore[union-attr]
        db.commit()
    assert raw("PROPFIND", "/dav/", anna, password, Depth="0").status_code == 401


def test_time20_a_device_sees_only_its_own_account(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    password = device_for(anna)
    assert raw("PROPFIND", f"/dav/calendars/{account.name}/", anna, password, Depth="1").status_code == 404
    with TestClient(app) as plain:
        wrong_name = plain.request("PROPFIND", "/dav/", headers=basic(account, password))
    assert wrong_name.status_code == 401


# --- Calendars from the device (AC-14) ----------------------------------------------------------------------------


def test_time20_ac14_a_calendar_made_on_the_device_is_own_and_deleting_puts_it_into_the_trash(
        server: str, client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas")
    share(shared, account.id, RIGHT_EDIT)
    principal = connect(server, account, device_for(account)).principal()
    made = principal.make_calendar(name="Vom iPhone", cal_id="vom-iphone-1")
    row = named(client, "Vom iPhone")
    assert row["group"] == "own" and row["writable"] is True
    made.delete()
    assert "Vom iPhone" not in [item["name"] for item in calendars_of(client)["calendars"]]
    with SessionLocal() as db:
        assert db.scalar(select(Calendar.deleted_at).where(Calendar.dav_name == "vom-iphone-1")) is not None
    with pytest.raises(caldav.lib.error.DAVError):
        by_name(principal, "Annas").delete()
    assert "Annas" in [item.get_display_name() for item in principal.calendars()]


def test_time20_ac2_time4_custom_colour_from_the_device_is_kept_as_it_is(server: str, client: TestClient,
                                                                         account: Account) -> None:
    """A color an iPhone sets stays that color (decided while testing, 2026-11-23: before, the nearest of the eleven);
    exactly one of the eleven stays that one by name, and an own color goes back to Apple as it is."""
    principal = connect(server, account, device_for(account)).principal()
    calendar = by_name(principal, "Personal")
    calendar.set_properties([dav_elements.DisplayName("Privat"), ical_elements.CalendarColor("#2562A9FF")])
    assert named(client, "Privat")["color"] == "#2562a9"
    calendar.set_properties([ical_elements.CalendarColor("#2563A8FF")])
    assert named(client, "Privat")["color"] == "blue"
    assert client.patch(f"/api/calendars/{named(client, 'Privat')['id']}", json={"color": "#1E3A8A"}).status_code == 200
    found = by_name(connect(server, account, device_for(account, "iPad")).principal(), "Privat")
    assert list(found.get_properties([ical_elements.CalendarColor()]).values()) == ["#1E3A8AFF"]


# --- The setup (AC-1, AC-2, AC-5, AC-7, AC-8, AC-9, AC-13, EC-6 to EC-9, EC-21 to EC-23) -----------------------


@pytest.fixture
def secure(account: Account) -> Iterator[TestClient]:
    """The account's browser over HTTPS."""
    from .conftest import sign_in

    with TestClient(app, base_url="https://testserver", headers=TAB) as browser:
        sign_in(browser, account)
        yield browser


def _setup(browser: TestClient, name: str = "iPhone von Tester") -> dict:
    answer = browser.post("/api/devices", json={"name": name})
    assert answer.status_code == 201, answer.text
    return answer.json()


def test_time20_ac1_naming_a_device_makes_its_own_password_and_shows_what_to_type(secure: TestClient,
                                                                                account: Account) -> None:
    made = _setup(secure)
    assert made["address"] == "https://testserver/dav/" and made["user"] == account.name
    assert len(made["password"]) == 19 and made["password"].count("-") == 3
    assert raw("PROPFIND", "/dav/", account, made["password"], Depth="0").status_code == 207
    assert made["qr"].startswith("data:image/svg+xml") and made["link"].startswith("https://testserver/setup/")


def test_time20_ac7_the_password_is_shown_once_and_only_its_hash_is_kept(secure: TestClient,
                                                                        account: Account) -> None:
    made = _setup(secure)
    listed = secure.get("/api/devices").json()
    assert made["password"] not in str(listed)
    with SessionLocal() as db:
        device = db.get(Device, made["device"]["id"])
        assert device is not None and device.password_hash == devices.password_hash(made["password"])
        setup = db.scalar(select(DeviceSetup))
        assert setup is not None and made["password"] not in setup.password_enc


def test_time20_ac2_the_profile_carries_the_account_and_needs_no_typing(secure: TestClient, account: Account) -> None:
    made = _setup(secure)
    with TestClient(app, base_url="https://testserver") as phone:
        profile = phone.get(f"/api/device-setup/{made['code']}/profile", params={"name": "Mein iPhone"})
    assert profile.status_code == 200
    assert profile.headers["content-type"].startswith("application/x-apple-aspen-config")
    text = profile.text
    for piece in ("<key>CalDAVHostName</key><string>testserver</string>", "<integer>443</integer>",
                  f"<string>{account.name}</string>", f"<string>{made['password']}</string>",
                  f"<string>https://testserver/dav/principals/{account.name}/</string>", "com.apple.caldav.account"):
        assert piece in text
    assert [row["name"] for row in secure.get("/api/devices").json()["devices"]] == ["Mein iPhone"]


def test_time20_ac8_ec6_the_link_holds_ten_minutes_and_then_makes_nothing(secure: TestClient,
                                                                        account: Account) -> None:
    made = _setup(secure)
    assert made["minutes"] == 10
    with SessionLocal() as db:
        row = db.scalar(select(DeviceSetup))
        assert row is not None
        assert timedelta(minutes=9) < row.expires_at - utcnow() <= timedelta(minutes=10)
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    with TestClient(app, base_url="https://testserver", headers=TAB) as phone:
        late = phone.get(f"/api/device-setup/{made['code']}")
        assert late.status_code == 410 and code(late) == "setup_expired"
        assert phone.get(f"/api/device-setup/{made['code']}/profile").status_code == 410
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Device)) == 1
        assert db.scalar(select(DeviceSetup.password_enc)) == ""


def test_time20_ac13_a_link_used_once_says_so_and_makes_no_further_device(secure: TestClient,
                                                                        account: Account) -> None:
    made = _setup(secure)
    with TestClient(app, base_url="https://testserver", headers=TAB) as phone:
        # Loading the profile again before the device signed in is fine ("Ignore" in Safari, then once more).
        assert phone.get(f"/api/device-setup/{made['code']}/profile").status_code == 200
        assert phone.get(f"/api/device-setup/{made['code']}/profile").status_code == 200
        assert len(secure.get("/api/devices").json()["devices"]) == 1
        assert raw("PROPFIND", "/dav/", account, made["password"], Depth="0").status_code == 207
        again = phone.get(f"/api/device-setup/{made['code']}/profile")
        assert again.status_code == 410 and code(again) == "setup_used"
        assert code(phone.get(f"/api/device-setup/{made['code']}")) == "setup_used"
        unknown = phone.get("/api/device-setup/made-up-code")
        assert unknown.status_code == 404 and code(unknown) == "setup_invalid"
    assert len(secure.get("/api/devices").json()["devices"]) == 1


def test_time20_ec7_ec9_an_unfinished_setup_stands_in_the_list_and_names_are_text(secure: TestClient,
                                                                                account: Account) -> None:
    for name in ("", "   "):
        refused = secure.post("/api/devices", json={"name": name})
        assert refused.status_code == 422 and code(refused) == "device_name_missing"
    first, second = _setup(secure, "iPhone"), _setup(secure, "iPhone")
    _setup(secure, "<b>Ölgerät</b> 📱")
    listed = secure.get("/api/devices").json()["devices"]
    assert [row["name"] for row in listed] == ["iPhone", "iPhone", "<b>Ölgerät</b> 📱"]
    assert all(row["last_used_at"] is None for row in listed)
    secure.delete(f"/api/devices/{first['device']['id']}")
    assert raw("PROPFIND", "/dav/", account, second["password"], Depth="0").status_code == 207
    assert raw("PROPFIND", "/dav/", account, first["password"], Depth="0").status_code == 401


def test_time20_ac5_the_list_names_the_program_and_the_last_sync(secure: TestClient, account: Account) -> None:
    made = _setup(secure)
    raw("PROPFIND", "/dav/", account, made["password"], Depth="0")
    row = secure.get("/api/devices").json()["devices"][0]
    assert row["client"] == "Apple Calendar" and row["last_used_at"] is not None


def test_time20_ac9_ec8_the_phone_page_lists_the_calendars_that_come_along(secure: TestClient,
                                                                          account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas")
    share(shared, account.id, RIGHT_BUSY)
    made = _setup(secure)
    with TestClient(app, base_url="https://testserver") as phone:
        page = phone.get(f"/api/device-setup/{made['code']}").json()
    assert page["device"] == "iPhone von Tester"
    assert [(row["name"], row["right"]) for row in page["calendars"]] == [("Personal", "owner"), ("Annas", "busy")]
    personal = named(secure, "Personal")["id"]
    secure.delete(f"/api/calendars/{personal}")
    # A blocked owner's calendar stays as it was (TIME-2 EC-2, TIME-19 EC-17); a deleted owner's goes.
    with SessionLocal() as db:
        db.get(Account, anna.id).blocked_at = utcnow()  # type: ignore[union-attr]
        db.commit()
    assert [row["name"] for row in secure.get("/api/devices").json()["calendars"]] == ["Annas"]
    with SessionLocal() as db:
        db.get(Account, anna.id).deleted_at = utcnow()  # type: ignore[union-attr]
        db.commit()
    assert secure.get("/api/devices").json()["calendars"] == []


def test_time20_ec21_without_https_there_is_no_setup(client: TestClient, account: Account) -> None:
    assert client.get("/api/devices").json()["https"] is False
    refused = client.post("/api/devices", json={"name": "iPhone"})
    assert refused.status_code == 409 and code(refused) == "https_required"


def test_time20_ec22_behind_a_proxy_everything_carries_the_outside_address(secure: TestClient,
                                                                          account: Account) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://kalender.example.com"})
    made = _setup(secure)
    assert made["address"] == "https://kalender.example.com/dav/"
    assert made["link"].startswith("https://kalender.example.com/setup/")
    with TestClient(app, base_url="https://testserver") as phone:
        profile = phone.get(f"/api/device-setup/{made['code']}/profile").text
    assert "<string>kalender.example.com</string>" in profile


def test_time20_ec23_android_gets_the_data_to_type_instead_of_a_profile(secure: TestClient,
                                                                       account: Account) -> None:
    made = _setup(secure)
    with TestClient(app, base_url="https://testserver", headers=TAB) as phone:
        data = phone.post(f"/api/device-setup/{made['code']}/credentials", json={"name": "Pixel"}).json()
        assert data == {"address": "https://testserver/dav/", "user": account.name, "password": made["password"],
                        "device": "Pixel"}
        assert phone.post(f"/api/device-setup/{made['code']}/credentials", json={}).status_code == 200
        raw("PROPFIND", "/dav/", account, made["password"], Depth="0")
        assert phone.post(f"/api/device-setup/{made['code']}/credentials", json={}).status_code == 410


def test_time20_well_known_leads_to_the_root(client: TestClient) -> None:
    with TestClient(app) as plain:
        answer = plain.request("PROPFIND", "/.well-known/caldav", follow_redirects=False)
        assert answer.status_code == 301 and answer.headers["location"] == "/dav/"
        options = plain.options("/dav/")
        assert "calendar-access" in options.headers["dav"]
