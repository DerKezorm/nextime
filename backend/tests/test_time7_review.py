"""TIME-7 after the review of stage 6a: what a device read stays the version it saves against (no false conflict at
events with reminders or attachments), alarms a device set for single occurrences of a series stay and remind in the
browser too, alarms stored before this version go to everybody who saw them, "at the start" arrives, "Not twice"
only for calendars that go over the calendar account to a recent device of the same kind, planning that stays cheap
with ten thousand events of a source, and a mark for "Again in 5 min" that works once and only for its browser."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from app.db import SessionLocal
from app.models import (
    RIGHT_BUSY,
    RIGHT_READ,
    Account,
    Device,
    Event,
    EventConflict,
    ReminderChoice,
    UsedMark,
    WebPushSubscription,
    utcnow,
)
from app.services import alarms, devices, events, ical, reminders, webpush

from .calendar_helpers import make_calendar, make_event, share
from .conftest import make_account, new_client
from .test_time7 import IPHONE, Clock, at, clock, push_app, reminded, set_reminders  # noqa: F401 - the fixture
from .test_time9 import inline_media, upload  # noqa: F401 - the fixture
from .test_time19 import from_source, put_share, source_calendar, stored, vevent
from .test_time20 import by_name, calendar_url, connect, device_for, raw, server  # noqa: F401 - the fixture
from .test_time21 import FakeBrowser, PushService, prefs, push  # noqa: F401 - the fixture


def href_of(event_id: int) -> str:
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        assert row is not None
        return row.href


def conflicts() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(EventConflict)) or 0)


def on_platform(browser: FakeBrowser, platform: str) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(WebPushSubscription).where(WebPushSubscription.endpoint == browser.endpoint))
        assert row is not None
        row.platform = platform
        db.commit()


def moments(push: PushService, clock: Clock, start: str, hours: int, title: str = "") -> list[datetime]:
    """Every five minutes from ``start`` for ``hours``: when a reminder (of ``title``) arrived."""
    found = []
    begin = at(start)
    for minute in range(0, hours * 60 + 1, 5):
        before = len([item for item in reminded(push) if item["title"].startswith(title)])
        clock.to(begin + timedelta(minutes=minute))
        if len([item for item in reminded(push) if item["title"].startswith(title)]) > before:
            found.append(clock.now)
    return found


# --- Finding 1: If-Match against what the device read --------------------------------------------------------------


def test_time7_ac9_a_device_saves_what_it_read_with_its_own_alarms_without_a_conflict(
        server: str, client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Zahnarzt Ben", "2026-11-26T16:30", "2026-11-26T17:15", calendar_id=familie,
                      reminders=[15])
    with SessionLocal() as db:
        uid = db.get(Event, made["id"]).uid
    principal = connect(server, account, device_for(account)).principal()
    fetched = by_name(principal, "Familie").event_by_uid(uid)
    assert "TRIGGER:-PT15M" in fetched.data
    read_etag = fetched.etag
    assert read_etag and "-" in read_etag  # the personal ETag, with the mark of the alarms
    fetched.icalendar_component["SUMMARY"] = "Zahnarzt Ben (verschoben)"
    fetched.save()
    assert conflicts() == 0
    assert client.get(f"/api/events/{made['id']}").json()["title"] == "Zahnarzt Ben (verschoben)"
    # Against it: an ETag of before that change is stale, and the overwritten version is kept as a conflict.
    path = calendar_url(account, familie) + href_of(made["id"])
    password = device_for(account, "iPad")
    text = raw("GET", path, account, password).text.replace("(verschoben)", "(nochmal)")
    assert raw("PUT", path, account, password, text, **{"If-Match": read_etag}).status_code == 204
    assert conflicts() == 1


def test_time9_ac4_a_device_saves_an_event_with_attachments_against_the_etag_it_read(
        client: TestClient, account: Account) -> None:
    made = make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00")
    assert upload(client, made["id"], "Einladung.txt", b"Bitte Stifte mitbringen").status_code in (200, 201)
    password = device_for(account)
    path = calendar_url(account, made["calendar_id"]) + href_of(made["id"])
    shown = raw("GET", path, account, password)
    assert "ATTACH" in shown.text and "-" in shown.headers["etag"]
    changed = shown.text.replace("Elternabend", "Elternabend Klasse 3")
    assert raw("PUT", path, account, password, changed, **{"If-Match": shown.headers["etag"]}).status_code == 204
    assert conflicts() == 0
    again = raw("GET", path, account, password)
    stale = again.text.replace("Klasse 3", "Klasse 4")
    assert raw("PUT", path, account, password, stale, **{"If-Match": shown.headers["etag"]}).status_code == 204
    assert conflicts() == 1


# --- Finding 5: alarms of single occurrences ----------------------------------------------------------------------


SERIES = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Apple Inc.//iPhone OS 18.1//EN\r
BEGIN:VEVENT\r
UID:schwimmen@example.com\r
DTSTAMP:20261120T100000Z\r
DTSTART;TZID=Europe/Berlin:20261126T180000\r
DTEND;TZID=Europe/Berlin:20261126T190000\r
RRULE:FREQ=DAILY;COUNT=3\r
SUMMARY:Schwimmen\r
BEGIN:VALARM\r
X-WR-ALARMUID:SERIES-ALARM\r
ACTION:DISPLAY\r
DESCRIPTION:Reminder\r
TRIGGER:-PT15M\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:schwimmen@example.com\r
DTSTAMP:20261120T100000Z\r
RECURRENCE-ID;TZID=Europe/Berlin:20261127T180000\r
DTSTART;TZID=Europe/Berlin:20261127T180000\r
DTEND;TZID=Europe/Berlin:20261127T190000\r
SUMMARY:Schwimmen (Wettkampf)\r
BEGIN:VALARM\r
X-WR-ALARMUID:OCCURRENCE-ALARM\r
ACTION:DISPLAY\r
DESCRIPTION:Reminder\r
TRIGGER:-PT1H\r
END:VALARM\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_time7_ec10_alarms_a_device_set_for_one_occurrence_stay_and_remind_in_the_browser(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    familie = make_calendar(client, "Familie")
    password = device_for(account)
    path = calendar_url(account, familie) + "schwimmen.ics"
    assert raw("PUT", path, account, password, SERIES, **{"If-None-Match": "*"}).status_code == 201
    with SessionLocal() as db:
        event_id = db.scalar(select(Event.id).where(Event.uid == "schwimmen@example.com"))
        assert "VALARM" not in db.get(Event, event_id).ical
    shown = client.get(f"/api/events/{event_id}").json()["reminders"]
    assert shown["minutes"] == [15] and shown["occurrences"] == ["20261127T170000Z"]
    # The device gets both back as it sent them.
    assert raw("GET", path, account, password).text == SERIES
    assert moments(push, clock, "2026-11-26T17:00+01:00", 1) == [at("2026-11-26T17:45+01:00")]
    # The web changes the series: the occurrence keeps its own hour, block and all.
    assert set_reminders(client, event_id, [30]).status_code == 200
    again = raw("GET", path, account, password).text
    series, occurrence = again.split("RECURRENCE-ID", 1)
    assert "TRIGGER:-PT30M" in series and "TRIGGER:-PT15M" not in series and "SERIES-ALARM" not in series
    assert "X-WR-ALARMUID:OCCURRENCE-ALARM" in occurrence and "TRIGGER:-PT1H" in occurrence
    assert "TRIGGER:-PT30M" not in occurrence
    assert moments(push, clock, "2026-11-27T16:00+01:00", 26) == [at("2026-11-27T17:00+01:00"),
                                                                  at("2026-11-28T17:30+01:00")]
    # "Follow the default" for the series keeps the occurrence as well.
    assert set_reminders(client, event_id, None).status_code == 200
    assert client.get(f"/api/events/{event_id}").json()["reminders"]["occurrences"] == ["20261127T170000Z"]
    assert "TRIGGER:-PT1H" in raw("GET", path, account, password).text


def test_time7_ec10_an_occurrence_without_alarms_under_a_series_with_them_stays_quiet(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    familie = make_calendar(client, "Familie")
    password = device_for(account)
    path = calendar_url(account, familie) + "schwimmen.ics"
    quiet = SERIES.replace("BEGIN:VALARM\r\nX-WR-ALARMUID:OCCURRENCE-ALARM\r\nACTION:DISPLAY\r\n"
                           "DESCRIPTION:Reminder\r\nTRIGGER:-PT1H\r\nEND:VALARM\r\n", "")
    assert raw("PUT", path, account, password, quiet, **{"If-None-Match": "*"}).status_code == 201
    with SessionLocal() as db:
        event_id = db.scalar(select(Event.id).where(Event.uid == "schwimmen@example.com"))
    assert set_reminders(client, event_id, [30]).status_code == 200
    _series, occurrence = raw("GET", path, account, password).text.split("RECURRENCE-ID", 1)
    assert "VALARM" not in occurrence
    assert moments(push, clock, "2026-11-26T17:00+01:00", 49) == [at("2026-11-26T17:30+01:00"),
                                                                  at("2026-11-28T17:30+01:00")]


# --- Finding 6: alarms stored before this version -------------------------------------------------------------------


ALARM = "BEGIN:VALARM\nACTION:DISPLAY\nDESCRIPTION:x\nTRIGGER:-PT20M\nEND:VALARM"


def choices_of(event_id: int) -> dict[int, list[int]]:
    with SessionLocal() as db:
        return {row.account_id: list(row.minutes or []) for row in db.scalars(
            select(ReminderChoice).where(ReminderChoice.event_id == event_id))}


@pytest.fixture
def adopting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reminders, "_adopted", False)


def adopt() -> int:
    """The take-over as at a start (every start of a test client runs it as well)."""
    reminders._adopted = False
    return reminders.adopt_stored_alarms()


def test_time7_ac9_stored_alarms_go_to_everybody_who_saw_them_on_a_device(client: TestClient, account: Account,
                                                                          adopting: None) -> None:
    ben, carla, dora, eva, fritz = (make_account(name) for name in ("ben", "carla", "dora", "eva", "fritz"))
    familie = make_calendar(client, "Familie")
    put_share(client, familie, ben.id, RIGHT_READ)
    share(familie, carla.id, RIGHT_BUSY)  # "busy only" never had the alarm
    put_share(client, familie, dora.id, RIGHT_READ)  # no device: never saw it
    for person in (account, ben, carla):
        device_for(person)
    with new_client(eva) as browser:
        garten = make_calendar(browser, "Garten")
        put_share(browser, garten, fritz.id, RIGHT_READ)
    shared = stored(familie, vevent("alt", "20261127T150000Z", "20261127T160000Z", "Alt", extra=ALARM),
                    by=ben.id)
    alone = stored(garten, vevent("rasen", "20261128T150000Z", "20261128T160000Z", "Rasen", extra=ALARM),
                   by=eva.id)
    with SessionLocal() as db:
        db.get(Event, alone).created_by = fritz.id  # Fritz made it, Eva stored it last
        db.commit()
    with SessionLocal() as db:
        # What Ben chose meanwhile stays.
        db.add(ReminderChoice(event_id=shared, account_id=ben.id, minutes=[5], alarms="", updated_at=utcnow()))
        db.commit()
    assert adopt() == 2
    assert choices_of(shared) == {account.id: [20], ben.id: [5]}
    # Nobody with a device: whoever stored it last, not whoever made it.
    assert choices_of(alone) == {eva.id: [20]}
    assert dora and carla


def test_time7_ac9_the_take_over_skips_a_broken_event_and_resumes_without_doubles(
        client: TestClient, account: Account, adopting: None, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    familie = make_calendar(client, "Familie")
    device_for(account)
    first = stored(familie, vevent("eins", "20261127T150000Z", "20261127T160000Z", "Eins", extra=ALARM), by=account.id)
    broken = stored(familie, vevent("zwei", "20261128T150000Z", "20261128T160000Z", "Zwei", extra=ALARM),
                    by=account.id)
    third = stored(familie, vevent("drei", "20261129T150000Z", "20261129T160000Z", "Drei", extra=ALARM),
                   by=account.id)
    real_take = alarms.take

    def take(parsed: Any, text: str) -> Any:
        if "UID:zwei" in text:
            raise ValueError("broken")
        return real_take(parsed, text)

    monkeypatch.setattr(alarms, "take", take)
    with caplog.at_level(logging.ERROR, logger="nextime.reminders"):
        assert adopt() == 2
    assert f"event_id={broken}" in caplog.text
    assert choices_of(first) == {account.id: [20]} and choices_of(third) == {account.id: [20]}
    with SessionLocal() as db:
        assert "VALARM" in db.get(Event, broken).ical
    # The next start takes what is left, and nobody gets anything twice.
    monkeypatch.setattr(alarms, "take", real_take)
    assert adopt() == 1
    assert choices_of(broken) == {account.id: [20]} and choices_of(first) == {account.id: [20]}


class Stop(BaseException):
    """The process ends in the middle (a crash, a kill)."""


def test_time7_ac9_a_take_over_broken_off_keeps_what_it_did_and_the_next_start_finishes_it(
        client: TestClient, account: Account, adopting: None, monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    device_for(account)
    first = stored(familie, vevent("eins", "20261127T150000Z", "20261127T160000Z", "Eins", extra=ALARM), by=account.id)
    second = stored(familie, vevent("zwei", "20261128T150000Z", "20261128T160000Z", "Zwei", extra=ALARM),
                    by=account.id)
    real = reminders._receivers
    calls: list[int] = []

    def receivers(db: Any, row: Event) -> list[int]:
        calls.append(row.id)
        if len(calls) == 2:
            raise Stop
        return real(db, row)

    monkeypatch.setattr(reminders, "_receivers", receivers)
    with pytest.raises(Stop):
        adopt()
    assert choices_of(first) == {account.id: [20]} and choices_of(second) == {}
    with SessionLocal() as db:
        assert "VALARM" not in db.get(Event, first).ical and "VALARM" in db.get(Event, second).ical
    monkeypatch.setattr(reminders, "_receivers", real)
    assert adopt() == 1
    assert choices_of(first) == {account.id: [20]} and choices_of(second) == {account.id: [20]}


def test_time7_ac9_the_server_starts_even_when_the_take_over_fails(monkeypatch: pytest.MonkeyPatch,
                                                                   caplog: pytest.LogCaptureFixture) -> None:
    def fails() -> int:
        raise RuntimeError("database gone")

    monkeypatch.setattr(reminders, "adopt_stored_alarms", fails)
    with caplog.at_level(logging.ERROR, logger="nextime.reminders"), TestClient(push_app()) as started:
        assert started.get("/api/health").status_code == 200
    assert "database gone" in caplog.text


def test_time7_ac5_lead_times_of_source_events_written_before_are_filled_in(client: TestClient,
                                                                            account: Account) -> None:
    arbeit = source_calendar(account.id, kind="google")
    from_source(arbeit, vevent("quelle", "20261127T150000Z", "20261127T160000Z", "Quelle", extra=ALARM))
    with SessionLocal() as db:
        row = db.scalar(select(Event).where(Event.uid == "quelle"))
        assert row.alarm_leads == [20] and row.alarm_lead_max == 20
        row.alarm_leads, row.alarm_lead_max = None, None
        db.commit()
    assert reminders.fill_alarm_leads() == 1
    with SessionLocal() as db:
        row = db.scalar(select(Event).where(Event.uid == "quelle"))
        assert row.alarm_leads == [20] and row.alarm_lead_max == 20


# --- Finding 2: "at the start" ----------------------------------------------------------------------------------


def test_time7_ac2_at_the_start_arrives_from_the_page_and_from_a_device(client: TestClient, account: Account,
                                                                       push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[0])
    password = device_for(account)
    made = make_event(client, "Schwimmen", "2026-11-26T18:00", "2026-11-26T19:00")
    path = calendar_url(account, made["calendar_id"]) + href_of(made["id"])
    text = raw("GET", path, account, password).text.replace(
        "END:VEVENT", "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Reminder\r\nTRIGGER:PT0S\r\nEND:VALARM\r\n"
                      "END:VEVENT")
    assert raw("PUT", path, account, password, text).status_code == 204
    assert client.get(f"/api/events/{made['id']}").json()["reminders"]["minutes"] == [0]
    assert moments(push, clock, "2026-11-26T16:00+01:00", 3) == [at("2026-11-26T17:00+01:00"),
                                                                 at("2026-11-26T18:00+01:00")]


def test_time7_ec9_an_event_moved_while_its_reminder_waits_for_the_push_service_does_not_remind_then(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    made = make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    push.answer = 503
    clock.to(at("2026-11-26T16:45+01:00"))
    assert reminded(push) == []
    assert client.patch(f"/api/events/{made['id']}", json={
        "start": "2026-11-26T18:00", "end": "2026-11-26T19:00", "time_zone": "Europe/Berlin",
        "etag": made["etag"]}).status_code == 200
    push.answer = 201
    clock.to(at("2026-11-26T16:46+01:00"))
    clock.to(at("2026-11-26T16:50+01:00"))
    assert reminded(push) == []
    clock.to(at("2026-11-26T17:45+01:00"))
    assert [item["title"] for item in reminded(push)] == ["Elternabend · in 15 Minuten"]


# --- Finding 3: "Not twice" per calendar and kind of device ------------------------------------------------------


@pytest.fixture
def family_devices(client: TestClient, account: Account, push: PushService) -> dict[str, FakeBrowser]:
    """A laptop, and the web app on an iPhone, an iPad and a Mac; the calendar account only on the iPhone."""
    prefs(account.id)
    found = {"laptop": push.browser(client)}
    for kind in ("iphone", "ipad", "mac"):
        with new_client(account) as other:
            found[kind] = push.browser(other)
        on_platform(found[kind], kind)
    password = device_for(account, "iPhone")
    assert raw("PROPFIND", "/dav/", account, password, **{"User-Agent": IPHONE, "Depth": "0"}).status_code == 207
    return found


def arrived_on(push: PushService, title: str, browsers: dict[str, FakeBrowser]) -> set[str]:
    names = {browser.endpoint: name for name, browser in browsers.items()}
    return {names[browser.endpoint] for browser, message, _h in push.arrived if message["title"].startswith(title)}


def test_time7_ac7_only_the_iphone_stays_quiet_not_an_ipad_or_mac_without_the_calendar_account(
        client: TestClient, account: Account, push: PushService, clock: Clock,
        family_devices: dict[str, FakeBrowser]) -> None:
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    clock.to(at("2026-11-26T16:45+01:00"))
    assert arrived_on(push, "Elternabend", family_devices) == {"laptop", "ipad", "mac"}
    shown = client.get("/api/reminders").json()
    assert shown["apple"] == ["iphone"]


def test_time7_ac7_a_calendar_that_does_not_go_over_the_calendar_account_reminds_on_the_iphone_too(
        client: TestClient, account: Account, push: PushService, clock: Clock,
        family_devices: dict[str, FakeBrowser]) -> None:
    google = source_calendar(account.id, "Arbeit", kind="google")
    from_source(google, vevent("termin", "20261126T160000Z", "20261126T170000Z", "Kundentermin",
                               extra=ALARM.replace("PT20M", "PT15M")))
    clock.to(at("2026-11-26T16:45+01:00"))
    assert arrived_on(push, "Kundentermin", family_devices) == {"laptop", "iphone", "ipad", "mac"}


def test_time7_ac7_a_device_that_did_not_sync_for_14_days_counts_no_more(
        client: TestClient, account: Account, push: PushService, clock: Clock,
        family_devices: dict[str, FakeBrowser]) -> None:
    with SessionLocal() as db:
        db.scalar(select(Device)).last_used_at = clock.now - timedelta(days=15)
        db.commit()
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    clock.to(at("2026-11-26T16:45+01:00"))
    assert arrived_on(push, "Elternabend", family_devices) == {"laptop", "iphone", "ipad", "mac"}


def test_time7_ac7_the_kind_of_apple_device_from_what_it_calls_itself_or_its_name() -> None:
    assert devices.platform_of(IPHONE, "iPhone von Ben") == "iphone"
    assert devices.platform_of(IPHONE, "iPad von Ben") == "ipad"
    assert devices.platform_of("iPadOS/18.1 (22B83) dataaccessd/1.0", "Tablet") == "ipad"
    assert devices.platform_of("macOS/15.1 (24B83) CalendarAgent/1000", "") == "mac"
    assert devices.platform_of("DAVx5/4.4 (Android)", "iPad") == ""
    assert "ios" not in webpush.PLATFORMS and "ipad" in webpush.PLATFORMS


# --- Finding 4: planning stays cheap ---------------------------------------------------------------------------


def test_time7_ac1_after_a_change_only_that_event_is_planned_anew(client: TestClient, account: Account,
                                                                    push: PushService, clock: Clock,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    prefs(account.id)
    push.browser(client)
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    moved = make_event(client, "Schwimmen", "2026-11-26T18:00", "2026-11-26T19:00", reminders=[15])
    real = events.occurrence_list
    asked: list[Any] = []

    def occurrence_list(*args: Any, **kwargs: Any) -> Any:
        asked.append(kwargs.get("events_of"))
        return real(*args, **kwargs)

    monkeypatch.setattr(events, "occurrence_list", occurrence_list)
    clock.to(at("2026-11-26T16:00+01:00"))
    assert asked == [None]
    clock.to(at("2026-11-26T16:01+01:00"))
    assert asked == [None]  # nothing changed: the plan is kept
    assert client.patch(f"/api/events/{moved['id']}", json={
        "start": "2026-11-26T17:10", "end": "2026-11-26T18:10", "time_zone": "Europe/Berlin",
        "etag": moved["etag"]}).status_code == 200
    clock.to(at("2026-11-26T16:02+01:00"))
    assert asked[1:] == [{moved["calendar_id"]: {href_of(moved["id"])}}]
    clock.to(at("2026-11-26T16:45+01:00"))
    clock.to(at("2026-11-26T16:55+01:00"))
    assert [item["title"].split(" · ")[0] for item in reminded(push)] == ["Elternabend", "Schwimmen"]


@pytest.mark.timing
def test_time7_ac1_ten_thousand_events_of_a_source_are_planned_within_a_second(client: TestClient,
                                                                                account: Account) -> None:
    arbeit = source_calendar(account.id, kind="google")
    now = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
    text = vevent("bulk", "20261127T150000Z", "20261127T160000Z", "Termin", extra=ALARM)
    rows = []
    for index in range(20_000):
        start = now + timedelta(hours=index - 10_000) * 0.15
        rows.append({"calendar_id": arbeit, "uid": f"bulk-{index}", "href": f"bulk-{index}.ics",
                     "ical": text.replace("UID:bulk", f"UID:bulk-{index}"), "etag": f'"{index:032x}"',
                     "title": "Termin", "starts_at": start, "ends_at": start + timedelta(hours=1), "size": len(text),
                     "created_at": utcnow(), "updated_at": utcnow(), "busy": True, "time_zone": "",
                     "location": "", "prev_etag": "", "prev_ical": "", "alarm_leads": [20], "alarm_lead_max": 20})
    with SessionLocal() as db:
        db.execute(insert(Event), rows)
        db.commit()
    reminders.forget()
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        began = time.perf_counter()
        due = reminders._plan_of(db, row, now)
        first = time.perf_counter() - began
        reminders.touch(calendar_id=arbeit, href="bulk-10001.ics")
        began = time.perf_counter()
        again = reminders._plan_of(db, row, now + timedelta(seconds=20))
        second = time.perf_counter() - began
    assert due and len(again) == len(due)
    assert first < 1.0, first
    assert second < 0.2, second


# --- Findings 7 to 10 -------------------------------------------------------------------------------------------


def test_time7_ac4_ec17_a_mark_works_once_and_only_for_the_browser_it_was_made_for(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    laptop = push.browser(client)
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    clock.to(at("2026-11-26T16:45+01:00"))
    mark = next(message for browser, message, _h in push.arrived if browser is laptop)["snooze"]
    with TestClient(push_app(), base_url="http://testserver", headers={"X-Nextime-Client": "sw-reminder"}) as worker:
        assert worker.post("/api/reminders/snooze", json={"mark": mark}).status_code == 204
        # A second tap, a copy: nothing more.
        assert worker.post("/api/reminders/snooze", json={"mark": mark}).status_code == 204
    clock.to(at("2026-11-26T16:50+01:00"))
    clock.to(at("2026-11-26T16:55+01:00"))
    assert len(reminded(push)) == 2
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsedMark)) == 1
    # The browser's sign-up goes and another takes its number (SQLite may give it again): its marks are no use.
    unused = reminded(push)[1]["snooze"]
    with SessionLocal() as db:
        old = db.scalar(select(WebPushSubscription).where(WebPushSubscription.endpoint == laptop.endpoint))
        kept = {name: getattr(old, name) for name in ("id", "account_id", "session_id", "p256dh", "auth")}
        db.delete(old)
        db.commit()
        db.add(WebPushSubscription(**kept, endpoint="https://push.example/neu", created_at=utcnow(), reminders=True))
        db.commit()
    with TestClient(push_app(), base_url="http://testserver", headers={"X-Nextime-Client": "sw-reminder"}) as worker:
        assert worker.post("/api/reminders/snooze", json={"mark": unused}).status_code == 404


def test_time7_ac9_a_text_line_ending_in_begin_valarm_is_text_and_only_the_real_alarm_comes_off() -> None:
    text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//tests//EN\r\nBEGIN:VEVENT\r\nUID:falte\r\n"
            "DTSTAMP:20261120T100000Z\r\nDTSTART:20261127T150000Z\r\nDTEND:20261127T160000Z\r\nSUMMARY:Falte\r\n"
            "DESCRIPTION:Im Kalender steht dafuer am Ende des Textes ein BEGIN:VALARM\r\n"
            "LOCATION:Praxis Dr. Weber\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Reminder\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")
    stripped = alarms.strip(text)
    assert "LOCATION:Praxis Dr. Weber" in stripped and "ein BEGIN:VALARM\r\n" in stripped
    assert "TRIGGER" not in stripped
    assert alarms.leads(ical.parse(text)) == [15]
    # The same line folded: the continuation begins with a space, still text.
    folded = text.replace("ein BEGIN:VALARM\r\n", "ein\r\n BEGIN:VALARM\r\n")
    assert "LOCATION:Praxis Dr. Weber" in alarms.strip(folded) and "TRIGGER" not in alarms.strip(folded)


def test_time7_ec9_an_event_gone_at_its_source_leaves_the_plan_at_once(client: TestClient, account: Account,
                                                                        push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    google = source_calendar(account.id, "Arbeit", kind="google")
    from_source(google, vevent("weg", "20261126T160000Z", "20261126T170000Z", "Kundentermin", extra=ALARM))
    clock.to(at("2026-11-26T16:30+01:00"))
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert [due.minutes for due in reminders._plan_of(db, row, clock.now)] == [20]
        from app.models import Calendar
        from app.services import sync

        sync.remove(db, db.get(Calendar, google), "weg.ics")
        db.commit()
        assert reminders._plan_of(db, row, clock.now + timedelta(seconds=20)) == []
