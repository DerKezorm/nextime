"""TIME-7 Erinnerungen: reminders per person and event, a default per calendar and person, sent as Web Push to every
browser that reminds (the test plays the push service and the browsers and reads what arrives), with a fixed clock:
lead times on the wall clock over the change of the clocks, whole days in the member's zone, series with their
exceptions, missed ones at most 15 minutes late and never after the start. With the negative side: nobody hears another
person's reminder, "busy only" names nothing, a signed-out browser or a blocked account hears nothing, a device that
switched reminders off stays quiet, an Apple device with the calendar account leaves it to Apple Calendar, and the
calendar account hands every person their own alarms only."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    RIGHT_BUSY,
    RIGHT_EDIT,
    RIGHT_READ,
    Account,
    Device,
    Event,
    NotifyOutbox,
    ReminderChoice,
    RemoteChange,
    WebPushSubscription,
)
from app.services import accounts, alarms, events, notify, reminders

from .calendar_helpers import code, make_calendar, make_event
from .conftest import make_account, new_client
from .test_time19 import from_source, put_share, source_calendar, vevent
from .test_time20 import calendar_url, device_for, raw
from .test_time21 import PushService, prefs, push  # noqa: F401 - the fixture

START = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
IPHONE = "iOS/18.1 (22B83) dataaccessd/1.0"


class Clock:
    """The clock of reminders and notifications; ``to`` moves it and lets the ticker look."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def to(self, moment: datetime) -> tuple[int, int]:
        self.now = moment
        return reminders.tick(moment)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    made = Clock()
    monkeypatch.setattr(reminders, "clock", made)
    monkeypatch.setattr(notify, "clock", made)
    return made


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def reminded(service: PushService) -> list[dict[str, Any]]:
    return [message for message in service.messages() if message.get("kind") == "reminder"]


def set_reminders(client: TestClient, event_id: int, minutes: list[Any] | None) -> Any:
    return client.put(f"/api/events/{event_id}/reminders", json={"minutes": minutes})


def default(client: TestClient, calendar_id: int, choice: str) -> Any:
    return client.put(f"/api/reminders/calendars/{calendar_id}", json={"choice": choice})


def dentist(client: TestClient, **values: Any) -> dict[str, Any]:
    return make_event(client, "Zahnarzt Ben", "2026-11-26T16:30", "2026-11-26T17:15",
                      location="Praxis Dr. Weber, Lindenstraße 4", **values)


# --- AC-1, AC-2, AC-3, AC-8, EC-8: choosing ---------------------------------------------------------------------


def test_time7_ac1_a_reminder_names_title_lead_time_times_and_place(client: TestClient, account: Account,
                                                                     push: PushService, clock: Clock) -> None:
    prefs(account.id)
    browser = push.browser(client)
    made = dentist(client, reminders=[15])
    assert clock.to(at("2026-11-26T16:14+01:00")) == (0, 0)
    queued, sent = clock.to(at("2026-11-26T16:15+01:00"))
    assert (queued, sent) == (1, 1)
    message = reminded(push)[0]
    assert message["title"] == "Zahnarzt Ben · in 15 Minuten"
    assert message["body"] == "16:30–17:15 · Praxis Dr. Weber, Lindenstraße 4"
    assert message["url"] == f"/day?day=2026-11-26&event={made['id']}"
    assert [action["title"] for action in message["actions"]] == ["In 5 Min erneut", "Öffnen"]
    assert push.arrived[0][0] is browser
    # Once: the next rounds send nothing again (EC-1).
    assert clock.to(at("2026-11-26T16:16+01:00")) == (0, 0)


def test_time7_ac2_several_fixed_and_free_lead_times_each_removable(client: TestClient, account: Account) -> None:
    made = make_event(client, "Elternabend", reminders=[15, 60])
    assert made["reminders"]["minutes"] == [60, 15] and made["reminders"]["own"] is True
    assert made["reminders"]["presets"] == [5, 15, 30, 60, 1440]
    # A free one (two hours) beside fixed ones, then one removed.
    answer = set_reminders(client, made["id"], [5, 1440, 120])
    assert answer.status_code == 200 and answer.json()["reminders"]["minutes"] == [1440, 120, 5]
    assert set_reminders(client, made["id"], [1440, 5]).json()["reminders"]["minutes"] == [1440, 5]
    assert set_reminders(client, made["id"], []).json()["reminders"]["minutes"] == []
    # A whole day: the day before at 18:00, on the day at 09:00; not "after the start" for a time.
    whole = make_event(client, "Ausflug", "2026-11-28", None, all_day=True)
    assert whole["reminders"]["presets"] == [360, -540]
    assert set_reminders(client, whole["id"], [360, -540]).json()["reminders"]["minutes"] == [360, -540]
    refused = set_reminders(client, made["id"], [-540])
    assert refused.status_code == 422 and code(refused) == "invalid_reminder"


def test_time7_ec8_a_lead_time_that_is_not_one_is_refused_and_the_same_twice_is_once(client: TestClient,
                                                                                     account: Account) -> None:
    made = make_event(client, "Training")
    for wrong in ([-5], [alarms.MAX_MINUTES + 1], ["abc"], [1.5]):
        answer = set_reminders(client, made["id"], wrong)
        assert answer.status_code == 422, wrong
    assert set_reminders(client, made["id"], [15, 15, 15]).json()["reminders"]["minutes"] == [15]
    with SessionLocal() as db:
        assert db.get(ReminderChoice, (made["id"], account.id)).minutes == [15]
    assert client.get(f"/api/events/{made['id']}").json()["reminders"]["minutes"] == [15]
    many = set_reminders(client, made["id"], list(range(1, 13)))
    assert many.status_code == 422 and code(many) == "too_many_reminders"


def test_time7_ac3_a_calendar_default_goes_to_new_events_and_each_event_may_change_it(client: TestClient,
                                                                                    account: Account) -> None:
    verein = make_calendar(client, "Verein", reminder=30)
    nothing = make_calendar(client, "Arbeit")
    first = make_event(client, "Training", calendar_id=verein)
    assert first["reminders"] == {"minutes": [30], "own": False, "default": [30], "presets": [5, 15, 30, 60, 1440], "occurrences": []}
    assert make_event(client, "Sitzung", calendar_id=nothing)["reminders"]["minutes"] == []
    changed = set_reminders(client, first["id"], [5]).json()["reminders"]
    assert changed["minutes"] == [5] and changed["own"] is True
    # Back to the default.
    assert set_reminders(client, first["id"], None).json()["reminders"]["minutes"] == [30]
    # A whole day gets "the day before at 18:00" from a default of a day, "on the day at 09:00" from less.
    whole = make_event(client, "Turnier", "2026-11-28", None, all_day=True, calendar_id=verein)
    assert whole["reminders"]["minutes"] == [-540]
    assert default(client, verein, "1440").status_code == 200
    assert client.get(f"/api/events/{whole['id']}").json()["reminders"]["minutes"] == [360]
    # "None" from the start: a new calendar's default.
    rows = {row["name"]: row for row in client.get("/api/reminders").json()["calendars"]}
    assert rows["Arbeit"]["choice"] == "none" and rows["Verein"]["choice"] == "1440"


def test_time7_ac8_the_area_has_both_switches_the_defaults_and_a_preview(client: TestClient,
                                                                          account: Account) -> None:
    prefs(account.id)
    anna = make_account("anna")
    prefs(anna.id)
    familie = make_calendar(client, "Familie", color="green")
    put_share(client, familie, anna.id, RIGHT_READ)
    shown = client.get("/api/reminders").json()
    assert shown["not_double"] is True
    assert shown["this_browser"] == {"subscribed": False, "reminders": True, "quiet": False}
    assert shown["preview"] == {"title": "Zahnarzt Ben · in 15 Minuten",
                                "text": "16:30–17:15 · Praxis Dr. Weber, Lindenstraße 4",
                                "snooze": "In 5 Min erneut", "open": "Öffnen"}
    row = next(item for item in shown["calendars"] if item["name"] == "Familie")
    assert row["color"] == "green" and row["choice"] == "none"
    assert row["options"] == ["none", "5", "15", "30", "60", "1440"]
    assert client.put("/api/reminders", json={"not_double": False}).json()["not_double"] is False
    assert default(client, familie, "15").json()["calendars"][0]["choice"] in ("15", "none")
    # Anna's default of the same calendar is hers alone.
    with new_client(anna) as browser:
        theirs = next(item for item in browser.get("/api/reminders").json()["calendars"] if item["id"] == familie)
        assert theirs["choice"] == "none" and theirs["owner"] == "Tester"
        assert default(browser, familie, "60").status_code == 200
    assert next(item for item in client.get("/api/reminders").json()["calendars"]
                if item["id"] == familie)["choice"] == "15"
    refused = default(client, familie, "7")
    assert refused.status_code == 422 and code(refused) == "invalid_reminder"
    # English, 12 hours.
    prefs(account.id, "en", "Europe/Berlin", "12")
    english = client.get("/api/reminders").json()["preview"]
    assert english["title"] == "Dentist Ben · in 15 minutes" and english["text"].startswith("4:30 PM–5:15 PM")


def test_time7_ac8_nobody_reads_or_sets_another_members_defaults_or_reminders(client: TestClient,
                                                                               account: Account) -> None:
    ben = make_account("ben")
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Elternabend", calendar_id=familie)
    with new_client(ben) as browser:
        assert default(browser, familie, "15").status_code == 404
        assert set_reminders(browser, made["id"], [15]).status_code == 404
        assert all(item["id"] != familie for item in browser.get("/api/reminders").json()["calendars"])


# --- AC-9: one's own ---------------------------------------------------------------------------------------------


def test_time7_ac9_in_a_shared_calendar_everybody_has_their_own_reminders(client: TestClient, account: Account,
                                                                            push: PushService, clock: Clock) -> None:
    prefs(account.id)
    anna, ben, carla = make_account("anna"), make_account("ben"), make_account("carla")
    prefs(anna.id)
    prefs(ben.id)
    familie = make_calendar(client, "Familie", reminder=5)
    put_share(client, familie, anna.id, RIGHT_EDIT)
    put_share(client, familie, ben.id, RIGHT_READ)
    put_share(client, familie, carla.id, RIGHT_READ)
    mine = push.browser(client)
    with new_client(carla) as carla_browser:
        # Carla chose nothing: the owner's 5 minutes are not hers ("None" from the start).
        push.browser(carla_browser)
    with new_client(anna) as anna_browser, new_client(ben) as ben_browser:
        annas = push.browser(anna_browser)
        bens = push.browser(ben_browser)
        made = make_event(anna_browser, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00",
                          calendar_id=familie, reminders=[30])
        # Ben ("read") chose his own default; Tester (the owner) has 5 minutes from the calendar.
        assert default(ben_browser, familie, "15").status_code == 200
        assert ben_browser.get(f"/api/events/{made['id']}").json()["reminders"]["minutes"] == [15]
        assert set_reminders(ben_browser, made["id"], [60]).status_code == 200
        assert anna_browser.get(f"/api/events/{made['id']}").json()["reminders"]["minutes"] == [30]
    for minute in range(61):
        clock.to(at("2026-11-26T16:00+01:00") + timedelta(minutes=minute))
    got = {(browser.endpoint, message["title"]) for browser, message, _headers in push.arrived}
    assert got == {(bens.endpoint, "Elternabend · in 1 Stunde"), (annas.endpoint, "Elternabend · in 30 Minuten"),
                   (mine.endpoint, "Elternabend · in 5 Minuten")}


def test_time7_ac9_the_calendar_account_hands_every_person_their_own_alarms(client: TestClient,
                                                                             account: Account) -> None:
    ben = make_account("ben")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, ben.id, RIGHT_EDIT)
    made = make_event(client, "Elternabend", calendar_id=familie, reminders=[15])
    href = calendar_url(account, familie) + f"{made['id']}"
    with SessionLocal() as db:
        name = db.get(Event, made["id"]).href
    mine = raw("GET", calendar_url(account, familie) + name, account, device_for(account))
    assert mine.status_code == 200 and "TRIGGER:-PT15M" in mine.text
    bens_password = device_for(ben)
    bens_path = calendar_url(ben, familie) + name
    theirs = raw("GET", bens_path, ben, bens_password)
    assert theirs.status_code == 200 and "VALARM" not in theirs.text
    assert theirs.headers["etag"] != mine.headers["etag"]
    # Ben's phone sets 5 minutes: his own; what is stored carries no alarm, Tester's phone keeps its 15 minutes.
    sent = theirs.text.replace("END:VEVENT", "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Reminder\r\n"
                               "TRIGGER:-PT5M\r\nEND:VALARM\r\nEND:VEVENT")
    put = raw("PUT", bens_path, ben, bens_password, sent, **{"If-Match": theirs.headers["etag"]})
    assert put.status_code == 204
    with SessionLocal() as db:
        assert "VALARM" not in db.get(Event, made["id"]).ical
    with new_client(ben) as browser:
        assert browser.get(f"/api/events/{made['id']}").json()["reminders"]["minutes"] == [5]
    assert client.get(f"/api/events/{made['id']}").json()["reminders"]["minutes"] == [15]
    again = raw("GET", calendar_url(account, familie) + name, account, device_for(account, "iPad"))
    assert "TRIGGER:-PT15M" in again.text and "TRIGGER:-PT5M" not in again.text
    assert "TRIGGER:-PT5M" in raw("GET", bens_path, ben, bens_password).text
    assert href


def test_time7_ac9_a_phone_removing_its_alarm_removes_the_reminder_and_the_default_comes_back_unchanged(
        client: TestClient, account: Account) -> None:
    verein = make_calendar(client, "Verein", reminder=30)
    made = make_event(client, "Training", calendar_id=verein)
    with SessionLocal() as db:
        name = db.get(Event, made["id"]).href
    password = device_for(account)
    path = calendar_url(account, verein) + name
    shown = raw("GET", path, account, password)
    assert "TRIGGER:-PT30M" in shown.text and alarms.DEFAULT_MARK in shown.text
    # Sent back with the default untouched: still the default (a later default still reaches it).
    assert raw("PUT", path, account, password, shown.text.replace("Training", "Training!")).status_code == 204
    assert client.get(f"/api/events/{made['id']}").json()["reminders"] == {
        "minutes": [30], "own": False, "default": [30], "presets": [5, 15, 30, 60, 1440], "occurrences": []}
    # Without any alarm: the person took it away.
    bare = alarms.strip(raw("GET", path, account, password).text)
    assert raw("PUT", path, account, password, bare).status_code == 204
    assert client.get(f"/api/events/{made['id']}").json()["reminders"]["minutes"] == []


def test_time7_ac5_ac9_a_shared_source_calendar_keeps_its_alarms_and_sends_nothing_of_the_phone(
        client: TestClient, account: Account) -> None:
    ben = make_account("ben")
    arbeit = source_calendar(account.id, "Arbeit")
    from_source(arbeit, vevent("sitzung", "20261126T150000Z", "20261126T160000Z", "Sitzung",
                               extra="BEGIN:VALARM\nACTION:DISPLAY\nDESCRIPTION:x\nTRIGGER:-PT30M\nEND:VALARM"))
    assert put_share(client, arbeit, ben.id, RIGHT_READ).status_code == 200
    password = device_for(ben)
    path = calendar_url(ben, arbeit) + "sitzung.ics"
    shown = raw("GET", path, ben, password)
    # The alarm of the source is the owner's: not on Ben's phone.
    assert shown.status_code == 200 and "VALARM" not in shown.text
    with SessionLocal() as db:
        row = db.scalar(select(Event).where(Event.calendar_id == arbeit))
        assert row is not None
        before = row.ical
    with new_client(ben) as browser:
        assert set_reminders(browser, row.id, [5]).json()["reminders"]["minutes"] == [5]
    assert "TRIGGER:-PT5M" in raw("GET", path, ben, password).text
    # Nothing of it goes to the source: the event stays as the source has it, nothing waits to go there.
    with SessionLocal() as db:
        assert db.get(Event, row.id).ical == before
        assert db.scalar(select(RemoteChange).where(RemoteChange.calendar_id == arbeit)) is None
    # A phone writing into such a calendar (Ben may not) never takes the source's alarms away nor adds its own.
    sent = alarms.strip(before).replace("END:VEVENT", "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT5M\r\n"
                                        "END:VALARM\r\nEND:VEVENT")
    kept = alarms.restore(alarms.strip(sent), before)
    assert "TRIGGER:-PT30M" in kept and "TRIGGER:-PT5M" not in kept
    assert raw("PUT", path, ben, password, sent).status_code == 403
    # The owner reminds "as in iCloud" (AC-5): the alarm of the source.
    owner = client.get(f"/api/events/{row.id}").json()["reminders"]
    assert owner == {"minutes": [30], "own": False, "default": [30], "presets": [5, 15, 30, 60, 1440], "occurrences": []}
    rows = {item["id"]: item for item in client.get("/api/reminders").json()["calendars"]}
    assert rows[arbeit]["choice"] == "source" and rows[arbeit]["options"][0] == "source"
    with new_client(ben) as browser:
        theirs = {item["id"]: item for item in browser.get("/api/reminders").json()["calendars"]}
        assert theirs[arbeit]["choice"] == "none" and "source" not in theirs[arbeit]["options"]


# --- EC-9, EC-10, EC-11: when --------------------------------------------------------------------------------------


def test_time7_ec9_a_moved_deleted_or_restored_event_reminds_at_its_new_time_or_not_at_all(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    moved = make_event(client, "Verschoben", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    gone = make_event(client, "Gelöscht", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    later = make_event(client, "Zurück", "2026-11-26T19:00", "2026-11-26T20:00", reminders=[15])
    assert client.patch(f"/api/events/{moved['id']}", json={
        "start": "2026-11-26T18:00", "end": "2026-11-26T19:00", "time_zone": "Europe/Berlin",
        "etag": moved["etag"]}).status_code == 200
    assert client.delete(f"/api/events/{gone['id']}").status_code == 200
    assert client.delete(f"/api/events/{later['id']}").status_code == 200
    clock.to(at("2026-11-26T16:45+01:00"))
    assert reminded(push) == []
    # Back from the trash (TIME-23 brings it back the same way): in the future, it reminds again.
    with SessionLocal() as db:
        row = db.get(Event, later["id"])
        row.deleted_at = None
        events.record_change(db, row.calendar_id, row.href, row=row, remote=False)
        db.commit()
    clock.to(at("2026-11-26T17:45+01:00"))
    clock.to(at("2026-11-26T18:45+01:00"))
    assert [message["title"] for message in reminded(push)] == ["Verschoben · in 15 Minuten",
                                                               "Zurück · in 15 Minuten"]


def test_time7_ec9_a_restored_event_whose_reminder_passed_does_not_remind(client: TestClient, account: Account,
                                                                          push: PushService, clock: Clock) -> None:
    push.browser(client)
    made = make_event(client, "Zurück", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[60])
    assert client.delete(f"/api/events/{made['id']}").status_code == 200
    clock.to(at("2026-11-26T16:05+01:00"))
    with SessionLocal() as db:
        row = db.get(Event, made["id"])
        row.deleted_at = None
        events.record_change(db, row.calendar_id, row.href, row=row, remote=False)
        db.commit()
    # Due at 16:00, restored at 16:20: more than 15 minutes late.
    clock.to(at("2026-11-26T16:20+01:00"))
    assert reminded(push) == []


def test_time7_ec10_every_occurrence_of_a_series_reminds_moved_ones_at_their_time_deleted_ones_not(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    series = make_event(client, "Schwimmen", "2026-11-26T18:00", "2026-11-26T19:00", reminders=[30],
                        rule={"freq": "daily", "interval": 1, "end": "count", "count": 4})
    # The 27th moved to 19:00, the 28th deleted.
    assert client.patch(f"/api/events/{series['id']}", json={
        "start": "2026-11-27T19:00", "end": "2026-11-27T20:00", "time_zone": "Europe/Berlin", "scope": "this",
        "occurrence": "20261127T170000Z", "etag": series["etag"]}).status_code == 200
    assert client.delete(f"/api/events/{series['id']}", params={"scope": "this",
                                                                "occurrence": "20261128T170000Z"}).status_code == 200
    moments = []
    for minute in range(0, 4 * 24 * 60, 5):
        before = len(reminded(push))
        clock.to(at("2026-11-26T17:00+01:00") + timedelta(minutes=minute))
        if len(reminded(push)) > before:
            moments.append(clock.now)
    assert moments == [at("2026-11-26T17:30+01:00"), at("2026-11-27T18:30+01:00"), at("2026-11-29T17:30+01:00")]


@pytest.mark.parametrize(("event_start", "due"), [
    # Spring: "1 day before" is 12:00 on the Saturday (CET), not 24 hours earlier.
    ("2027-03-28T12:00", "2027-03-27T12:00+01:00"),
    # Autumn: 12:00 on the Saturday (CEST).
    ("2027-10-31T12:00", "2027-10-30T12:00+02:00"),
])
def test_time7_ec11_a_day_before_is_the_same_clock_time_over_the_change_of_the_clocks(
        client: TestClient, account: Account, push: PushService, clock: Clock, event_start: str, due: str) -> None:
    prefs(account.id)
    push.browser(client)
    end = event_start[:11] + "13:00"
    make_event(client, "Brunch", event_start, end, reminders=[1440])
    clock.to(at(due) - timedelta(minutes=1))
    assert reminded(push) == []
    clock.to(at(due))
    assert [message["title"] for message in reminded(push)] == ["Brunch · morgen um 12:00"]


def test_time7_ec11_another_zone_and_whole_days_in_the_members_zone(client: TestClient, account: Account,
                                                                      push: PushService, clock: Clock) -> None:
    prefs(account.id, "en", "America/New_York")
    push.browser(client)
    # An event in New York at 9:00, reminded an hour before: 13:00 UTC.
    make_event(client, "Call", "2026-11-27T09:00", "2026-11-27T10:00", zone="America/New_York", reminders=[60])
    # A whole day on the 28th: "the day before at 18:00" and "on the day at 09:00", in New York.
    make_event(client, "Trip", "2026-11-28", None, all_day=True, reminders=[360, -540])
    clock.to(at("2026-11-27T12:59:00+00:00"))
    clock.to(at("2026-11-27T13:00:00+00:00"))
    clock.to(at("2026-11-27T22:59:00+00:00"))
    clock.to(at("2026-11-27T23:00:00+00:00"))
    clock.to(at("2026-11-28T14:00:00+00:00"))
    titles = [message["title"] for message in reminded(push)]
    assert titles == ["Call · in 1 hour", "Trip · tomorrow", "Trip · today"]
    assert [message["body"] for message in reminded(push)] == ["9:00 AM–10:00 AM", "all day", "all day"]


# --- EC-3: missed ----------------------------------------------------------------------------------------------


def test_time7_ec3_a_missed_reminder_comes_at_most_15_minutes_late_and_never_after_the_start(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    ten = make_event(client, "Zehn", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[30])
    twenty = make_event(client, "Zwanzig", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[50])
    # The server was off from 16:00 to 16:31: "Zehn" was due at 16:30 (a minute late: comes), "Zwanzig" at 16:10
    # (21 minutes late: not at all).
    clock.to(at("2026-11-26T16:31+01:00"))
    clock.to(at("2026-11-26T16:45+01:00"))
    titles = [message["title"] for message in reminded(push)]
    assert titles == ["Zehn · in 29 Minuten"]
    assert ten and twenty
    # Made after its moment: an event in 5 minutes with a reminder 15 minutes before comes at once, before the start.
    make_event(client, "Gleich", "2026-11-26T16:50", "2026-11-26T17:30", reminders=[15])
    clock.to(at("2026-11-26T16:46+01:00"))
    assert reminded(push)[-1]["title"] == "Gleich · in 4 Minuten"
    # Never after the start: the server back at 17:01 sends nothing for 17:00.
    make_event(client, "Vorbei", "2026-11-26T17:00", "2026-11-26T17:30", reminders=[5])
    clock.to(at("2026-11-26T17:01+01:00"))
    assert reminded(push)[-1]["title"] == "Gleich · in 4 Minuten"


def test_time7_ec3_a_reminder_that_cannot_go_before_the_start_is_dropped(client: TestClient, account: Account,
                                                                          push: PushService, clock: Clock) -> None:
    push.browser(client)
    push.answer = 503
    make_event(client, "Kurz", "2026-11-26T16:20", "2026-11-26T17:00", reminders=[5])
    clock.to(at("2026-11-26T16:15+01:00"))
    push.answer = 201
    clock.to(at("2026-11-26T16:21+01:00"))
    assert reminded(push) == []
    with SessionLocal() as db:
        row = db.scalar(select(NotifyOutbox).where(NotifyOutbox.kind == "reminder"))
        assert row is not None and row.failed_at is not None and row.payload == {}


# --- EC-14, EC-15: the push service and the server ---------------------------------------------------------------


def test_time7_ec14_an_unreachable_push_service_is_tried_again_and_the_log_says_so(
        client: TestClient, account: Account, push: PushService, clock: Clock,
        caplog: pytest.LogCaptureFixture) -> None:
    prefs(account.id)
    push.browser(client)
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    push.answer = 503
    caplog.set_level(logging.WARNING, logger="nextime.notify")
    clock.to(at("2026-11-26T16:45+01:00"))
    assert "Reminder not delivered, tried again later" in caplog.text
    clock.to(at("2026-11-26T16:46+01:00"))
    push.answer = 201
    clock.to(at("2026-11-26T16:47+01:00"))
    assert [message["title"] for message in reminded(push)] == ["Elternabend · in 13 Minuten"]
    with SessionLocal() as db:
        row = db.scalar(select(NotifyOutbox).where(NotifyOutbox.kind == "reminder"))
        assert row is not None and row.attempts == 3 and row.sent_at is not None


def test_time7_ec14_a_subscription_the_service_dropped_goes_and_the_browser_must_allow_again(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    push.browser(client)
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    push.answer = 410
    clock.to(at("2026-11-26T16:45+01:00"))
    with SessionLocal() as db:
        assert db.scalar(select(WebPushSubscription)) is None
    assert client.get("/api/reminders").json()["this_browser"]["subscribed"] is False


def test_time7_ec14_after_its_deadline_a_reminder_is_given_up_with_a_log_line(
        client: TestClient, account: Account, push: PushService, clock: Clock,
        caplog: pytest.LogCaptureFixture) -> None:
    push.browser(client)
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    push.answer = 503
    caplog.set_level(logging.WARNING, logger="nextime.notify")
    for minute in range(45, 61):
        clock.to(at("2026-11-26T16:00+01:00") + timedelta(minutes=minute))
    assert "Reminder given up" in caplog.text
    assert reminded(push) == []


def test_time7_ec15_without_https_or_internet_the_area_says_so_and_the_log_names_why(
        client: TestClient, account: Account, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="nextime.notify")
    notify._warned.clear()
    shown = client.get("/api/reminders").json()
    assert shown["delivery"] == {"https": False, "internet": False}
    assert "not reached over HTTPS" in caplog.text and "does not reach the internet" in caplog.text


# --- EC-6, EC-7, EC-12, EC-13: what and to whom ------------------------------------------------------------------


def test_time7_ec6_five_reminders_in_the_same_minute_each_come_on_their_own(client: TestClient, account: Account,
                                                                            push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    for number in range(5):
        make_event(client, f"Termin {number}", "2026-11-26T17:00", "2026-11-26T17:30", reminders=[15])
    clock.to(at("2026-11-26T16:45+01:00"))
    shown = reminded(push)
    assert sorted(message["title"] for message in shown) == [f"Termin {n} · in 15 Minuten" for n in range(5)]
    assert len({message["tag"] for message in shown}) == 5
    assert len({headers["Topic"] for _browser, message, headers in push.arrived}) == 5


def test_time7_ec7_a_long_title_with_markup_is_cut_plain_text(client: TestClient, account: Account,
                                                              push: PushService, clock: Clock) -> None:
    prefs(account.id)
    push.browser(client)
    title = "<img src=x onerror=alert(1)> 🎉" + "x" * 300
    place = "Lindenstraße\n4\t" + "y" * 400
    made = make_event(client, title[:200], "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15], location=place)
    clock.to(at("2026-11-26T16:45+01:00"))
    message = reminded(push)[0]
    assert message["title"].startswith("<img src=x onerror=alert(1)> 🎉x") and "…" in message["title"]
    assert len(message["title"].split(" · ")[0]) <= reminders.TITLE_CHARS
    assert "\n" not in message["body"] and "\t" not in message["body"] and len(message["body"]) <= 300
    # In the event itself the whole title and place stay.
    detail = client.get(f"/api/events/{made['id']}").json()
    assert detail["title"] == title[:200] and detail["location"] == place.strip()


def test_time7_ec12_busy_only_names_neither_title_nor_place(client: TestClient, account: Account,
                                                            push: PushService, clock: Clock) -> None:
    anna = make_account("anna")
    prefs(anna.id)
    arbeit = source_calendar(account.id, "Arbeit")
    from_source(arbeit, vevent("geheim", "20261126T160000Z", "20261126T170000Z", "Geheime Sitzung",
                               location="Vorstandsetage", notes="Gehaltsrunde"))
    assert put_share(client, arbeit, anna.id, RIGHT_BUSY).status_code == 200
    with new_client(anna) as browser:
        push.browser(browser)
        assert default(browser, arbeit, "15").status_code == 200
    clock.to(at("2026-11-26T16:45+01:00"))
    message = reminded(push)[0]
    assert message["title"] == "Belegt · in 15 Minuten" and message["body"] == "17:00–18:00"
    text = str(push.messages())
    assert "Geheime" not in text and "Vorstand" not in text and "Gehalt" not in text


def test_time7_ec13_a_signed_out_browser_and_a_blocked_account_get_nothing(client: TestClient, account: Account,
                                                                            push: PushService, clock: Clock) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    made = make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", calendar_id=familie)
    with new_client(anna) as browser:
        push.browser(browser)
        assert set_reminders(browser, made["id"], [15]).status_code == 200
        assert browser.post("/api/auth/logout").status_code in (200, 204)
    with SessionLocal() as db:
        assert db.scalar(select(WebPushSubscription)) is None
    with new_client(anna) as browser:
        push.browser(browser)
    with SessionLocal() as db:
        accounts.block(db, db.get(Account, anna.id))
    clock.to(at("2026-11-26T16:45+01:00"))
    assert reminded(push) == []


# --- AC-4, AC-6, AC-7, EC-1, EC-17: devices --------------------------------------------------------------------


def test_time7_ac6_a_device_that_switched_reminders_off_stays_quiet_the_others_remind(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    laptop = push.browser(client)
    with new_client(account) as phone_client:
        phone = push.browser(phone_client)
        answer = phone_client.put("/api/notify/webpush/reminders", json={"on": False})
        assert answer.status_code == 200 and answer.json()["this_browser"]["reminders"] is False
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    clock.to(at("2026-11-26T16:45+01:00"))
    assert [browser for browser, _message, _headers in push.arrived] == [laptop]
    assert phone
    # Without a sign-up of this browser there is nothing to switch.
    with new_client(account) as other:
        refused = other.put("/api/notify/webpush/reminders", json={"on": False})
        assert refused.status_code == 409 and code(refused) == "not_subscribed"


def test_time7_ac7_ec1_an_apple_device_with_the_calendar_account_leaves_it_to_apple_calendar(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    laptop = push.browser(client)
    with new_client(account) as phone_client:
        phone = push.browser(phone_client)
    with SessionLocal() as db:
        db.get(WebPushSubscription, db.scalar(select(WebPushSubscription.id).where(
            WebPushSubscription.endpoint == phone.endpoint))).platform = "iphone"
        db.commit()
    # The calendar account on the iPhone, synced once (Apple Calendar says who it is).
    password = device_for(account)
    assert raw("PROPFIND", "/dav/", account, password, **{"User-Agent": IPHONE, "Depth": "0"}).status_code == 207
    with SessionLocal() as db:
        assert db.scalar(select(Device.platform)) == "iphone"
    made = make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    clock.to(at("2026-11-26T16:45+01:00"))
    assert [browser for browser, _message, _headers in push.arrived] == [laptop]
    # The iPhone's Apple Calendar has the reminder through the calendar account.
    with SessionLocal() as db:
        name = db.get(Event, made["id"]).href
    assert "TRIGGER:-PT15M" in raw("GET", calendar_url(account, made["calendar_id"]) + name, account,
                                   password).text
    shown = client.get("/api/reminders").json()
    assert shown["apple"] == ["iphone"] and [row["quiet"] for row in shown["browsers"]] == [False, True]
    # "Not twice" off: the web app on the iPhone reminds as well.
    assert client.put("/api/reminders", json={"not_double": False}).status_code == 200
    make_event(client, "Schwimmen", "2026-11-26T17:30", "2026-11-26T18:00", reminders=[15])
    clock.to(at("2026-11-26T17:15+01:00"))
    assert {browser.endpoint for browser, message, _h in push.arrived
            if message["title"].startswith("Schwimmen")} == {laptop.endpoint, phone.endpoint}


def test_time7_ac4_ec17_again_in_5_minutes_comes_once_more_on_that_browser_only(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    laptop = push.browser(client)
    with new_client(account) as phone_client:
        phone = push.browser(phone_client)
    make_event(client, "Elternabend", "2026-11-26T17:00", "2026-11-26T18:00", reminders=[15])
    clock.to(at("2026-11-26T16:45+01:00"))
    on_laptop = next(message for browser, message, _h in push.arrived if browser is laptop)
    # The service worker sends the mark back, without a session.
    with TestClient(push_app(), base_url="http://testserver", headers={"X-Nextime-Client": "sw-reminder"}) as worker:
        assert worker.post("/api/reminders/snooze", json={"mark": on_laptop["snooze"]}).status_code == 204
        forged = on_laptop["snooze"][:-3] + ("AAA" if not on_laptop["snooze"].endswith("AAA") else "BBB")
        assert worker.post("/api/reminders/snooze", json={"mark": forged}).status_code == 404
    clock.to(at("2026-11-26T16:49+01:00"))
    assert len(push.arrived) == 2
    clock.to(at("2026-11-26T16:50+01:00"))
    assert len(push.arrived) == 3
    browser, message, _headers = push.arrived[-1]
    assert browser is laptop and message["title"] == "Elternabend · in 10 Minuten"
    assert phone
    # A mark older than a day is no use.
    clock.now = at("2026-11-27T17:00+01:00")
    with TestClient(push_app(), base_url="http://testserver", headers={"X-Nextime-Client": "sw-reminder"}) as worker:
        assert worker.post("/api/reminders/snooze", json={"mark": on_laptop["snooze"]}).status_code == 404


def push_app() -> Any:
    from app.main import app

    return app


def test_time7_ac9_alarms_stored_before_this_version_stay_with_whoever_stored_them(client: TestClient,
                                                                                   account: Account,
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_time19 import stored

    ben = make_account("ben")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, ben.id, RIGHT_READ)
    event_id = stored(familie, vevent("alt", "20261127T150000Z", "20261127T160000Z", "Alt",
                                      extra="BEGIN:VALARM\nACTION:DISPLAY\nDESCRIPTION:x\nTRIGGER:-PT20M\nEND:VALARM"),
                      by=account.id)
    monkeypatch.setattr(reminders, "_adopted", False)
    assert reminders.adopt_stored_alarms() == 1
    assert reminders.adopt_stored_alarms() == 0
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        assert "VALARM" not in row.ical
        name = row.href
    assert client.get(f"/api/events/{event_id}").json()["reminders"]["minutes"] == [20]
    assert "TRIGGER:-PT20M" in raw("GET", calendar_url(account, familie) + name, account, device_for(account)).text
    assert "VALARM" not in raw("GET", calendar_url(ben, familie) + name, ben, device_for(ben)).text
