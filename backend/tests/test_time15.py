"""TIME-15 iCloud and CalDAV: connecting, choosing calendars, syncing both ways, waiting changes, refusals, states.

The other side is nextime's own calendar account in a thread (``remote_helpers``), behind a switchboard that plays a
source that is away, slow, strict or refusing. Rounds run directly (``sync.run_source``), never on a clock.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from app.db import SessionLocal
from app.models import RIGHT_EDIT, RIGHT_READ, Account, Calendar, Event, RemoteChange, Source
from app.services import ical, logs, sync

from .calendar_helpers import calendars_of, code, make_calendar, make_event, share, window
from .conftest import make_account, new_client
from .remote_helpers import (
    SWITCHES,
    Remote,
    connect,
    connected,
    dav_server,  # noqa: F401 - the fixture
    event_text,
    mirrored,
    quiet_world,  # noqa: F401 - the fixture
    remote_account,
    round_now,
    source_row,
    use_icloud,
)


@pytest.fixture
def remote(client: TestClient) -> Iterator[Remote]:
    yield from remote_account()


def _titles(client: TestClient) -> list[str]:
    return sorted(row["title"] for row in window(client))


# --- Connecting (AC-1, AC-2, AC-9, EC-4, EC-10, EC-13, EC-14, EC-15) ------------------------------------------------


def test_time15_ac1_icloud_checks_the_access_at_once_and_leads_to_the_choice(
        dav_server: str, client: TestClient, account: Account, remote: Remote, monkeypatch: pytest.MonkeyPatch) -> None:
    use_icloud(monkeypatch, dav_server)
    remote.calendar("Arbeit", color="teal")
    answer = connect(client, dav_server, remote, kind="icloud")
    assert answer.status_code == 201, answer.text
    choice = answer.json()
    assert choice["kind"] == "icloud" and choice["label"] == "remote"
    assert {item["name"]: item["color"] for item in choice["calendars"]} == {"Arbeit": "teal", "Personal": "slate"}
    assert all(not item["chosen"] and not item["new"] for item in choice["calendars"])
    # Connected, nothing chosen yet: no calendar shows.
    assert [row["group"] for row in calendars_of(client)["calendars"]] == ["own"]


def test_time15_ac1_a_wrong_password_connects_nothing_and_stores_nothing(
        dav_server: str, client: TestClient, account: Account, remote: Remote, monkeypatch: pytest.MonkeyPatch) -> None:
    use_icloud(monkeypatch, dav_server)
    typed = "abcd-" + "efgh-ijkl-mnop"
    answer = connect(client, dav_server, remote, kind="icloud", password=typed)
    assert answer.status_code == 422 and code(answer) == "credentials_refused"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 0


def test_time15_ec4_the_normal_apple_password_is_named_as_such(
        dav_server: str, client: TestClient, account: Account, remote: Remote, monkeypatch: pytest.MonkeyPatch) -> None:
    use_icloud(monkeypatch, dav_server)
    typed = "Mein" + "Passwort2026!"
    answer = connect(client, dav_server, remote, kind="icloud", password=typed)
    assert answer.status_code == 422
    assert code(answer) == "apple_password_not_app_specific" and answer.json()["detail"]["field"] == "password"


def test_time15_ac2_another_caldav_server_goes_the_same_way(dav_server: str, client: TestClient, account: Account,
                                                            remote: Remote) -> None:
    remote.calendar("Verein")
    remote.put(remote.calendar("Nextcloud"), "a.ics", event_text("a@x", "Probe"))
    source = connected(client, dav_server, remote, ["Nextcloud"])
    row = source_row(client, source)
    assert row["kind"] == "caldav" and row["label"].startswith("remote · 127.0.0.1")
    assert [item["name"] for item in row["calendars"]] == ["Nextcloud"]
    round_now(source)
    assert _titles(client) == ["Probe"]
    groups = {row["name"]: row["group"] for row in calendars_of(client)["calendars"]}
    assert groups["Nextcloud"] == "caldav"


def test_time15_ac9_only_chosen_calendars_show_and_the_choice_changes_later(
        dav_server: str, client: TestClient, account: Account, remote: Remote, monkeypatch: pytest.MonkeyPatch) -> None:
    use_icloud(monkeypatch, dav_server)
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, dav_server, remote, ["Arbeit"], kind="icloud")
    round_now(source)
    shown = {row["name"]: row["group"] for row in calendars_of(client)["calendars"]}
    assert shown == {"Personal": "own", "Arbeit": "icloud"}
    assert _titles(client) == ["Kundentermin"]
    choice = client.get(f"/api/sources/{source}/calendars").json()
    assert {item["name"]: item["chosen"] for item in choice["calendars"]} == {"Arbeit": True, "Personal": False}
    personal = next(item["url"] for item in choice["calendars"] if item["name"] == "Personal")
    assert client.put(f"/api/sources/{source}/calendars", json={"urls": [personal]}).status_code == 200
    assert [row.name for row in mirrored(source)] == ["Personal"]
    assert _titles(client) == []
    # At the source, nothing went.
    assert remote.get(arbeit, "a.ics").status_code == 200


def test_time15_remove_from_nextime_leaves_one_calendar_out_and_it_stays_at_the_source(
        dav_server: str, client: TestClient, account: Account, remote: Remote, monkeypatch: pytest.MonkeyPatch) -> None:
    """"Aus nextime entfernen" (asked for while testing, 2026-11-23): the same as leaving it out in "Kalender wählen"."""
    use_icloud(monkeypatch, dav_server)
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, dav_server, remote, ["Arbeit", "Personal"], kind="icloud")
    gone = next(row.id for row in mirrored(source) if row.name == "Arbeit")
    assert client.delete(f"/api/sources/{source}/calendars/{gone}").status_code == 200
    assert [row.name for row in mirrored(source)] == ["Personal"]
    assert remote.get(arbeit, "a.ics").status_code == 200
    assert client.delete(f"/api/sources/{source}/calendars/{gone}").status_code == 404


def test_time15_ec10_an_account_without_calendars_or_a_closed_window_stays_connected(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    answer = connect(client, dav_server, remote)
    source = answer.json()["source_id"]
    # The window closed before "Done": connected, no calendar.
    assert source_row(client, source)["calendars"] == []
    assert len(client.get(f"/api/sources/{source}/calendars").json()["calendars"]) == 1
    remote.browser.delete(f"/api/calendars/{remote.browser.get('/api/calendars').json()['calendars'][0]['id']}")
    assert client.get(f"/api/sources/{source}/calendars").json()["calendars"] == []


def test_time15_ec13_blanks_go_and_an_address_without_calendar_server_is_refused(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    answer = client.post("/api/sources/dav", json={
        "kind": "caldav", "server": f"  {dav_server}/.well-known/caldav \n", "username": f" {remote.account.name} ",
        "password": f"\t{remote.password}  "})
    assert answer.status_code == 201, answer.text
    wrong = client.post("/api/sources/dav", json={"kind": "caldav", "server": f"{dav_server}/not-dav/",
                                                  "username": "x", "password": "y"})
    assert wrong.status_code == 422 and code(wrong) == "no_calendar_server"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 1


def test_time15_ec14_the_same_access_again_leads_to_the_one_there(dav_server: str, client: TestClient,
                                                                  account: Account, remote: Remote) -> None:
    first = connect(client, dav_server, remote).json()["source_id"]
    again = connect(client, dav_server, remote)
    assert again.status_code == 409 and code(again) == "source_exists"
    assert again.json()["detail"]["source_id"] == first


def test_time15_ec15_two_clicks_make_one_connection_and_each_calendar_once(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    first = connect(client, dav_server, remote, ref="ref-double")
    second = connect(client, dav_server, remote, ref="ref-double")
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["source_id"] == second.json()["source_id"]
    source = first.json()["source_id"]
    urls = [item["url"] for item in first.json()["calendars"]]
    for _ in range(2):
        assert client.put(f"/api/sources/{source}/calendars", json={"urls": urls}).status_code == 200
    assert len(mirrored(source)) == 1


# --- Syncing (AC-3, AC-4, AC-6, AC-12, EC-3, EC-19) ------------------------------------------------------------------


def test_time15_ac3_made_changed_deleted_in_nextime_reaches_the_source(dav_server: str, client: TestClient,
                                                                       account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    target = mirrored(source)[0].id
    made = make_event(client, "Werkstatt", calendar_id=target)
    # At once in nextime, waiting until the round took it.
    assert [row["waiting"] for row in window(client)] == [True]
    sync.run_due()
    assert [row["waiting"] for row in window(client)] == [False]
    href = _href(made["id"])
    assert "SUMMARY:Werkstatt" in remote.get(arbeit, href).text
    assert client.patch(f"/api/events/{made['id']}", json={"title": "Werkstatt Golf"}).status_code == 200
    sync.run_due()
    assert "SUMMARY:Werkstatt Golf" in remote.get(arbeit, href).text
    assert client.delete(f"/api/events/{made['id']}").status_code == 200
    sync.run_due()
    assert remote.get(arbeit, href).status_code == 404


def _href(event_id: int) -> str:
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        assert row is not None
        return row.href


def test_time15_ac4_a_change_at_the_source_shows_after_the_next_round(dav_server: str, client: TestClient,
                                                                      account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    assert _titles(client) == ["Kundentermin"]
    remote.put(arbeit, "a.ics", event_text("a@x", "Kundentermin verschoben"))
    remote.put(arbeit, "b.ics", event_text("b@x", "Neu vom iPhone"))
    round_now(source)
    assert _titles(client) == ["Kundentermin verschoben", "Neu vom iPhone"]
    remote.delete(arbeit, "a.ics")
    round_now(source)
    assert _titles(client) == ["Neu vom iPhone"]
    # The round comes every minute: within the two minutes of AC-4.
    assert sync.INTERVALS["caldav"] == sync.INTERVALS["icloud"] == 60


def test_time15_ac6_a_calendar_shared_for_reading_is_read_only_already_in_the_choice(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    chris = make_account("chris")
    with new_client(chris) as browser:
        team = make_calendar(browser, "Team")
        make_event(browser, "Planung", calendar_id=team)
    share(team, remote.account.id, RIGHT_READ)
    choice = connect(client, dav_server, remote).json()
    assert {item["name"]: item["read_only"] for item in choice["calendars"]} == {"Personal": False, "Team": True}
    source = choice["source_id"]
    client.put(f"/api/sources/{source}/calendars", json={"urls": [item["url"] for item in choice["calendars"]]})
    round_now(source)
    shown = {row["name"]: row["writable"] for row in calendars_of(client)["calendars"]}
    assert shown["Team"] is False and shown["Personal"] is True
    planung = next(row for row in window(client) if row["title"] == "Planung")
    assert client.patch(f"/api/events/{planung['id']}", json={"title": "x"}).status_code == 403


def test_time15_ac12_free_and_busy_are_the_same_on_both_sides(dav_server: str, client: TestClient,
                                                              account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Homeoffice", extra="TRANSP:TRANSPARENT\r\n"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    row = window(client)[0]
    assert row["busy"] is False
    assert client.patch(f"/api/events/{row['id']}", json={"busy": True}).status_code == 200
    sync.run_due()
    assert "TRANSP:OPAQUE" in remote.get(arbeit, "a.ics").text


def test_time15_ec3_changed_on_both_sides_the_later_save_wins(dav_server: str, client: TestClient,
                                                              account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Treffen"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    row = window(client)[0]
    SWITCHES.strict_if_match = True
    # Earlier at the source (an iPhone offline), later here: nextime's version wins.
    remote.put(arbeit, "a.ics", event_text("a@x", "Treffen iPhone", extra="LAST-MODIFIED:20200101T000000Z\r\n"))
    assert client.patch(f"/api/events/{row['id']}", json={"title": "Treffen nextime"}).status_code == 200
    sync.run_due()
    assert "SUMMARY:Treffen nextime" in remote.get(arbeit, "a.ics").text
    # Later at the source: the source's version wins, and the member hears of its overwritten one.
    assert client.patch(f"/api/events/{row['id']}", json={"title": "Treffen mein"}).status_code == 200
    remote.put(arbeit, "a.ics", event_text("a@x", "Treffen später", extra="LAST-MODIFIED:20991231T000000Z\r\n"))
    sync.run_due()
    assert "SUMMARY:Treffen später" in remote.get(arbeit, "a.ics").text
    detail = client.get(f"/api/events/{row['id']}").json()
    assert detail["title"] == "Treffen später" and detail["conflict"]["mine"]["title"] == "Treffen mein"


SERIES = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iPhone OS 18.1//EN\r\n"
          "BEGIN:VTIMEZONE\r\nTZID:America/New_York\r\nBEGIN:STANDARD\r\nDTSTART:20071104T020000\r\n"
          "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\nTZOFFSETFROM:-0400\r\nTZOFFSETTO:-0500\r\nEND:STANDARD\r\n"
          "BEGIN:DAYLIGHT\r\nDTSTART:20070311T020000\r\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\n"
          "TZOFFSETFROM:-0500\r\nTZOFFSETTO:-0400\r\nEND:DAYLIGHT\r\nEND:VTIMEZONE\r\n"
          "BEGIN:VEVENT\r\nUID:serie@x\r\nDTSTAMP:20261120T090000Z\r\nDTSTART;TZID=America/New_York:20261102T090000\r\n"
          "DTEND;TZID=America/New_York:20261102T100000\r\nRRULE:FREQ=WEEKLY;BYDAY=MO\r\n"
          "EXDATE;TZID=America/New_York:20261109T090000\r\nSUMMARY:Standup\r\nX-APPLE-STRUCTURED-LOCATION:x\r\n"
          "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT10M\r\nDESCRIPTION:Erinnerung\r\nEND:VALARM\r\nEND:VEVENT\r\n"
          "BEGIN:VEVENT\r\nUID:serie@x\r\nDTSTAMP:20261120T090000Z\r\nRECURRENCE-ID;TZID=America/New_York:"
          "20261116T090000\r\nDTSTART;TZID=America/New_York:20261116T110000\r\nDTEND;TZID=America/New_York:"
          "20261116T120000\r\nSUMMARY:Standup spaeter\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def test_time15_ec19_zone_whole_days_series_and_unknown_fields_come_back_unharmed(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "serie.ics", SERIES)
    remote.put(arbeit, "ganz.ics", event_text("ganz@x", "Urlaub", "20261123", "20261128",
                                              extra="X-UNKNOWN-THING:bleibt\r\n"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    rows = window(client)
    serie = next(row for row in rows if row["recurring"])
    ganz = next(row for row in rows if row["all_day"])
    assert client.patch(f"/api/events/{serie['id']}", json={"title": "Standup Team", "scope": "all"}).status_code == 200
    assert client.patch(f"/api/events/{ganz['id']}", json={"title": "Urlaub Ostsee"}).status_code == 200
    sync.run_due()
    text = remote.get(arbeit, "serie.ics").text
    for kept in ("TZID=America/New_York:20261102T090000", "EXDATE;TZID=America/New_York:20261109T090000",
                 "RECURRENCE-ID;TZID=America/New_York:20261116T090000", "SUMMARY:Standup spaeter",
                 "X-APPLE-STRUCTURED-LOCATION:x", "TRIGGER:-PT10M", "SUMMARY:Standup Team"):
        assert kept in text, kept
    text = remote.get(arbeit, "ganz.ics").text
    assert "DTSTART;VALUE=DATE:20261123" in text and "X-UNKNOWN-THING:bleibt" in text


# --- Waiting, refusals, restarts (EC-2, EC-5, EC-6, EC-7, EC-17) ------------------------------------------------------


def test_time15_ec2_ec5_away_the_change_waits_the_copy_stays_and_goes_out_later(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    stand = source_row(client, source)["synced_at"]
    SWITCHES.down = True
    round_now(source)
    row = source_row(client, source)
    assert row["state"] == "unreachable" and row["synced_at"] == stand
    assert _titles(client) == ["Kundentermin"]
    event = window(client)[0]
    assert client.patch(f"/api/events/{event['id']}", json={"title": "Kundentermin 15 Uhr"}).status_code == 200
    sync.run_due()
    assert window(client)[0]["waiting"] is True and source_row(client, source)["waiting"] == 1
    SWITCHES.down = False
    round_now(source)
    assert window(client)[0]["waiting"] is False
    assert source_row(client, source)["state"] == "ok"
    assert "SUMMARY:Kundentermin 15 Uhr" in remote.get(arbeit, "a.ics").text


def test_time15_ec6_a_refused_change_brings_the_source_state_back_with_a_notice(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Werkstatt"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    event = window(client)[0]
    SWITCHES.refuse_writes = True
    assert client.patch(f"/api/events/{event['id']}", json={"title": "Werkstatt 10:45"}).status_code == 200
    sync.run_due()
    assert [(row["title"], row["waiting"]) for row in window(client)] == [("Werkstatt", False)]
    notices = client.get("/api/sources").json()["notices"]
    assert [(item["title"], item["lost"]["title"], item["calendar"]) for item in notices] == [
        ("Werkstatt 10:45", "Werkstatt 10:45", "Arbeit")]
    # "Create as a new event": in its calendar while that is writable.
    SWITCHES.refuse_writes = False
    made = client.post(f"/api/sources/notices/{notices[0]['id']}", json={"create": True}).json()
    assert made["notices"] == [] and made["created_event"]
    assert _titles(client) == ["Werkstatt", "Werkstatt 10:45"]


def test_time15_ec6_discard_drops_the_notice_and_a_read_only_calendar_sends_the_new_one_to_the_default(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Werkstatt"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    event = window(client)[0]
    SWITCHES.refuse_writes = True
    client.patch(f"/api/events/{event['id']}", json={"title": "Werkstatt neu"})
    client.patch(f"/api/events/{event['id']}", json={"title": "Werkstatt neu"})
    sync.run_due()
    notices = client.get("/api/sources").json()["notices"]
    assert len(notices) == 1
    with SessionLocal() as db:
        calendar = db.get(Calendar, mirrored(source)[0].id)
        assert calendar is not None
        calendar.read_only = True
        db.commit()
    made = client.post(f"/api/sources/notices/{notices[0]['id']}", json={"create": True})
    assert made.status_code == 200, made.text
    made = made.json()
    created = client.get(f"/api/events/{made['created_event']}").json()
    assert created["calendar_id"] == calendars_of(client)["default_id"]
    other = client.get("/api/sources").json()
    assert other["notices"] == []


def test_time15_ec7_waiting_changes_survive_a_restart(dav_server: str, client: TestClient, account: Account,
                                                      remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Training"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    SWITCHES.down = True
    event = window(client)[0]
    client.patch(f"/api/events/{event['id']}", json={"title": "Training fällt aus"})
    for _ in range(3):
        round_now(source)
    # What a restart keeps: the database. What it loses: everything in memory.
    sync._locks.clear()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RemoteChange)) == 1
    SWITCHES.down = False
    round_now(source)
    assert "SUMMARY:Training fällt aus" in remote.get(arbeit, "a.ics").text


def test_time15_ec17_a_slow_source_leaves_the_views_usable(dav_server: str, client: TestClient, account: Account,
                                                           remote: Remote) -> None:
    remote.put(remote.calendar("Arbeit"), "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    SWITCHES.slow = 2.5
    worker = threading.Thread(target=round_now, args=(source,))
    worker.start()
    time.sleep(0.3)
    began = time.monotonic()
    assert _titles(client) == ["Kundentermin"]
    assert time.monotonic() - began < 1.5
    worker.join(30)


# --- "Calendars and accounts" (AC-5, AC-7, AC-10, AC-11, AC-13, AC-14, EC-1, EC-8, EC-9, EC-11) -----------------------


def test_time15_ac5_disconnecting_removes_calendars_and_access_and_leaves_the_source(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    answer = client.delete(f"/api/sources/{source}")
    assert answer.status_code == 200 and answer.json()["sources"] == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 0
        assert db.scalar(select(func.count()).select_from(Calendar).where(Calendar.source_id.is_not(None))) == 0
    assert window(client) == []
    assert remote.get(arbeit, "a.ics").status_code == 200


def test_time15_ac7_ec11_the_count_holds_calendars_of_every_source_and_more_than_20_go(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    for number in range(24):
        remote.calendar(f"Kalender {number:02d}")
    source = connected(client, dav_server, remote)
    listing = calendars_of(client)
    assert listing["count"] == 26 and listing["smooth_up_to"] == 20
    assert len(mirrored(source)) == 25


def test_time15_ac10_ac13_each_account_its_row_with_state_and_calendars(dav_server: str, client: TestClient,
                                                                        account: Account, remote: Remote) -> None:
    remote.calendar("Arbeit")
    first = connected(client, dav_server, remote, ["Arbeit"])
    other = next(remote_account("zweiter"))
    other.calendar("Familie")
    second = connected(client, dav_server, other, ["Familie"])
    round_now(first)
    rows = client.get("/api/sources").json()["sources"]
    assert [(row["id"], [item["name"] for item in row["calendars"]]) for row in rows] == [
        (first, ["Arbeit"]), (second, ["Familie"])]
    assert rows[0]["state"] == "ok" and rows[0]["synced_at"] and rows[0]["waiting"] == 0


def test_time15_ac11_ec1_expired_access_keeps_the_copy_and_reconnecting_keeps_every_setting(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    mine = mirrored(source)[0].id
    assert client.patch(f"/api/calendars/{mine}", json={"color": "pink"}).status_code == 200
    assert client.post("/api/collections", json={"name": "Arbeit", "calendar_ids": [mine]}).status_code == 201
    # The app-specific password revoked: the device password goes.
    with SessionLocal() as db:
        from app.models import Device

        db.execute(Device.__table__.delete())
        db.commit()
    round_now(source)
    row = source_row(client, source)
    assert row["state"] == "expired" and row["since"] and row["problem"] == "credentials"
    event = window(client)[0]
    assert event["title"] == "Kundentermin" and event["writable"] is False
    assert client.patch(f"/api/events/{event['id']}", json={"title": "x"}).status_code == 403
    fresh = next(remote_account("remote2"))
    with SessionLocal() as db:
        from app.models import Device

        device = db.scalar(select(Device).where(Device.account_id == fresh.account.id))
        assert device is not None
        device.account_id = remote.account.id
        db.commit()
    answer = client.post(f"/api/sources/{source}/reconnect", json={"password": fresh.password})
    assert answer.status_code == 200, answer.text
    assert source_row(client, source)["state"] == "ok"
    assert {row["name"]: row["color"] for row in calendars_of(client)["calendars"]}["Arbeit"] == "pink"
    assert client.get("/api/collections").json()["collections"][0]["calendar_ids"] == [mine]
    assert window(client)[0]["writable"] is True


def test_time15_ac14_a_new_calendar_at_the_source_is_only_offered_as_new(dav_server: str, client: TestClient,
                                                                         account: Account, remote: Remote) -> None:
    remote.calendar("Arbeit")
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    remote.put(remote.calendar("Urlaub"), "u.ics", event_text("u@x", "Ostsee"))
    round_now(source)
    assert [row.name for row in mirrored(source)] == ["Arbeit"]
    assert _titles(client) == []
    assert source_row(client, source)["new"] == 1
    choice = client.get(f"/api/sources/{source}/calendars").json()
    assert {item["name"]: item["new"] for item in choice["calendars"]} == {"Arbeit": False, "Personal": False,
                                                                           "Urlaub": True}


def test_time15_ec8_disconnecting_with_waiting_changes_says_how_many_first(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    remote.calendar("Arbeit")
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    SWITCHES.down = True
    target = mirrored(source)[0].id
    make_event(client, "Eins", calendar_id=target)
    make_event(client, "Zwei", calendar_id=target)
    assert source_row(client, source)["waiting"] == 2
    refused = client.delete(f"/api/sources/{source}")
    assert refused.status_code == 409 and code(refused) == "changes_waiting"
    assert refused.json()["detail"]["count"] == 2
    assert client.delete(f"/api/sources/{source}", params={"drop_waiting": "true"}).status_code == 200


def test_time15_ec9_without_a_source_there_is_no_group_and_no_error(client: TestClient, account: Account) -> None:
    answer = client.get("/api/sources")
    assert answer.status_code == 200
    assert answer.json()["sources"] == [] and answer.json()["notices"] == []
    assert {row["group"] for row in calendars_of(client)["calendars"]} == {"own"}


# --- Many, unreadable, dangerous, gone (EC-12, EC-16, EC-18, EC-20) --------------------------------------------------


def test_time15_ec12_a_large_calendar_comes_in_batches_with_the_past_and_a_hint_above_the_limit(
        dav_server: str, client: TestClient, account: Account, remote: Remote,
        monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import sources

    arbeit = remote.calendar("Archiv")
    with SessionLocal() as db:
        rows = []
        for number in range(1200):
            year = 2012 + number % 15
            text = event_text(f"e{number}@x", f"Termin {number}", f"{year}0{1 + number % 9}10T090000",
                              f"{year}0{1 + number % 9}10T100000")
            rows.append({"calendar_id": arbeit, "uid": f"e{number}@x", "href": f"e{number}.ics", "ical": text,
                         "etag": ical.etag_of(text), "title": f"Termin {number}", "starts_at": sync.clock(),
                         "size": len(text)})
        db.execute(insert(Event), rows)
        db.commit()
    monkeypatch.setattr(sync, "COMMIT_EVERY", 250)
    monkeypatch.setattr(sources, "EVENTS_SMOOTH_UP_TO", 1000)
    source = connected(client, dav_server, remote, ["Archiv"])
    assert source_row(client, source)["loading"] is True
    round_now(source)
    row = source_row(client, source)
    assert row["loading"] is False and row["events"] == 1200
    listing = client.get("/api/sources").json()
    assert listing["events_total"] > listing["events_smooth_up_to"]
    # The past came too (for the search).
    assert len(window(client, "2012-01-01T00:00:00Z", "2012-12-31T00:00:00Z")) == 80


def test_time15_ec16_code_in_a_title_comes_as_plain_text(dav_server: str, client: TestClient, account: Account,
                                                         remote: Remote) -> None:
    title = '<img src=x onerror=alert(1)><script>alert(2)</script>'
    remote.put(remote.calendar("Arbeit"), "a.ics", event_text(
        "a@x", title, extra="LOCATION:javascript:alert(3)\r\nDESCRIPTION:<b>fett</b>\r\n"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    row = window(client)[0]
    assert row["title"] == title and row["location"] == "javascript:alert(3)"
    assert client.get(f"/api/events/{row['id']}").json()["notes"] == "<b>fett</b>"


def test_time15_ec18_unreadable_events_are_skipped_and_counted(dav_server: str, client: TestClient,
                                                               account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Lesbar"))
    broken = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:t@x\r\nSUMMARY:Aufgabe\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    with SessionLocal() as db:
        db.execute(insert(Event), [{"calendar_id": arbeit, "uid": "t@x", "href": "t.ics", "ical": broken,
                                    "etag": ical.etag_of(broken), "title": "", "starts_at": sync.clock()}])
        db.commit()
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    assert _titles(client) == ["Lesbar"]
    assert source_row(client, source)["unreadable"] == 1


def test_time15_ec20_deleted_renamed_and_read_only_at_the_source(dav_server: str, client: TestClient,
                                                                 account: Account, remote: Remote) -> None:
    chris = make_account("chris")
    with new_client(chris) as browser:
        team = make_calendar(browser, "Team")
    share(team, remote.account.id, RIGHT_EDIT)
    weg = remote.calendar("Weg")
    umbenannt = remote.calendar("Alt")
    source = connected(client, dav_server, remote, ["Weg", "Alt", "Team"])
    round_now(source)
    weg_here = next(row.id for row in mirrored(source) if row.name == "Weg")
    assert client.post("/api/collections", json={"name": "S", "calendar_ids": [weg_here]}).status_code == 201
    assert remote.browser.delete(f"/api/calendars/{weg}").status_code == 200
    assert remote.browser.patch(f"/api/calendars/{umbenannt}", json={"name": "Neu"}).status_code == 200
    share(team, remote.account.id, RIGHT_READ)
    round_now(source)
    here = {row.name: row.read_only for row in mirrored(source)}
    assert here == {"Neu": False, "Team": True}
    assert client.get("/api/collections").json()["collections"][0]["calendar_ids"] == []
    with SessionLocal() as db:
        # Not into the trash: gone.
        assert db.get(Calendar, weg_here) is None


# --- Secrets (EC-21, EC-22) ------------------------------------------------------------------------------------------


def test_time15_ec21_the_password_never_shows_again_nor_in_the_log_nor_for_the_operator(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    import logging

    source = connected(client, dav_server, remote)
    round_now(source)
    for path in ("/api/sources", f"/api/sources/{source}/calendars", "/api/calendars", "/api/accounts"):
        answer = client.get(path)
        assert remote.password not in answer.text, path
    with SessionLocal() as db:
        row = db.get(Source, source)
        assert row is not None and remote.password not in row.secret_enc
    text = logs.log_file().read_text(encoding="utf-8") if logs.log_file().exists() else ""
    assert remote.password not in text
    # The CalDAV library and its HTTP stack never write addresses, bodies or headers into the log.
    assert all(logging.getLogger(name).getEffectiveLevel() >= logging.WARNING for name in ("caldav", "niquests"))


def test_time15_ec22_a_backup_with_another_key_stands_on_expired_and_keeps_its_events(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    remote.put(remote.calendar("Arbeit"), "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    with SessionLocal() as db:
        row = db.get(Source, source)
        assert row is not None
        row.secret_enc = Fernet(Fernet.generate_key()).encrypt(b'{"place": "x", "value": {}}').decode()
        db.commit()
    round_now(source)
    row = source_row(client, source)
    assert row["state"] == "expired" and row["problem"] == "key"
    assert _titles(client) == ["Kundentermin"] and [item["name"] for item in row["calendars"]] == ["Arbeit"]


# --- After the review (2026-11-22) -----------------------------------------------------------------------------------


def test_time15_ec3_a_deletion_that_waited_loses_against_a_newer_version_at_the_source(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Elternabend"))
    remote.put(arbeit, "b.ics", event_text("b@x", "Sprechstunde"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    rows = {row["title"]: row for row in window(client)}
    SWITCHES.strict_if_match = True
    SWITCHES.down = True
    assert client.delete(f"/api/events/{rows['Elternabend']['id']}").status_code == 200
    assert client.delete(f"/api/events/{rows['Sprechstunde']['id']}").status_code == 200
    sync.run_due()
    SWITCHES.down = False
    # Meanwhile on the phone: the one changed after the deletion here, the other before it.
    remote.put(arbeit, "a.ics", event_text("a@x", "Elternabend verlegt", extra="LAST-MODIFIED:20991231T000000Z\r\n"))
    remote.put(arbeit, "b.ics", event_text("b@x", "Sprechstunde alt", extra="LAST-MODIFIED:20200101T000000Z\r\n"))
    round_now(source)
    assert remote.get(arbeit, "a.ics").status_code == 200
    assert remote.get(arbeit, "b.ics").status_code == 404
    assert _titles(client) == ["Elternabend verlegt"]
    notices = client.get("/api/sources").json()["notices"]
    assert [(item["title"], item["lost"]) for item in notices] == [("Elternabend", None)]


@pytest.mark.parametrize("answer", [
    (503, b""), (408, b""), (403, b'{"error": {"errors": [{"reason": "rateLimitExceeded"}]}}'),
    (403, b'{"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}')], ids=["503", "408", "403-rate", "403-user"])
def test_time15_ec2_a_source_failing_only_at_writing_keeps_the_change_waiting(
        dav_server: str, client: TestClient, account: Account, remote: Remote, answer: tuple[int, bytes]) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    SWITCHES.writes_answer = answer
    event = window(client)[0]
    client.patch(f"/api/events/{event['id']}", json={"title": "Kundentermin 15 Uhr"})
    sync.run_due()
    assert window(client)[0]["waiting"] is True
    listing = client.get("/api/sources").json()
    assert listing["notices"] == [] and listing["sources"][0]["state"] == "unreachable"
    SWITCHES.writes_answer = None
    round_now(source)
    assert window(client)[0]["waiting"] is False
    assert "SUMMARY:Kundentermin 15 Uhr" in remote.get(arbeit, "a.ics").text


def test_time15_ec8_leaving_out_a_calendar_with_waiting_changes_says_how_many_first(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    remote.calendar("Arbeit")
    remote.calendar("Familie")
    source = connected(client, dav_server, remote, ["Arbeit", "Familie"])
    round_now(source)
    SWITCHES.down = True
    arbeit = next(row.id for row in mirrored(source) if row.name == "Arbeit")
    make_event(client, "Eins", calendar_id=arbeit)
    choice = client.get(f"/api/sources/{source}/calendars").json()
    familie = [item["url"] for item in choice["calendars"] if item["name"] == "Familie"]
    refused = client.put(f"/api/sources/{source}/calendars", json={"urls": familie})
    assert refused.status_code == 409 and code(refused) == "changes_waiting" and refused.json()["detail"]["count"] == 1
    assert len(mirrored(source)) == 2
    done = client.put(f"/api/sources/{source}/calendars", json={"urls": familie, "drop_waiting": True})
    assert done.status_code == 200 and [row.name for row in mirrored(source)] == ["Familie"]


def test_time15_ec1_an_expired_access_is_tried_again_once_an_hour_not_every_minute(
        dav_server: str, client: TestClient, account: Account, remote: Remote,
        monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from app.models import Device

    remote.calendar("Arbeit")
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    with SessionLocal() as db:
        db.execute(Device.__table__.delete())
        db.commit()
    round_now(source)
    assert source_row(client, source)["state"] == "expired"
    asked = len(SWITCHES.seen)
    now = sync.clock()
    for minutes in (2, 30, 59):
        monkeypatch.setattr(sync, "clock", lambda minutes=minutes: now + timedelta(minutes=minutes))
        sync.run_due()
    assert len(SWITCHES.seen) == asked
    monkeypatch.setattr(sync, "clock", lambda: now + timedelta(minutes=61))
    sync.run_due()
    assert len(SWITCHES.seen) > asked


def test_time15_ec17_ac4_a_hanging_source_ends_its_round_in_time_and_does_not_hold_up_others(
        dav_server: str, client: TestClient, account: Account, remote: Remote,
        monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.services import subscriptions

    remote.calendar("Arbeit")
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    body = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:m@x\r\nDTSTAMP:20261101T000000Z\r\n"
            "DTSTART;VALUE=DATE:20261126\r\nSUMMARY:Gelbe Tonne\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    made = client.post("/api/sources/subscriptions", json={"url": "https://abfall.example.com/a.ics"}).json()
    with SessionLocal() as db:
        for row in db.scalars(select(Source)):
            row.due_at = None
        db.commit()
    monkeypatch.setattr(sync, "ROUND_SECONDS", 1)
    SWITCHES.slow = 2
    began = time.monotonic()
    ran = sync.run_due()
    took = time.monotonic() - began
    assert sorted(ran) == sorted([source, made["created"]])
    # Not two seconds per request of the round: the round stops at its limit, the subscription ran beside it.
    assert took < 4.5
    rows = {row["kind"]: row["state"] for row in client.get("/api/sources").json()["sources"]}
    assert rows == {"caldav": "unreachable", "subscription": "ok"}


def test_time15_ac10_a_member_reaches_no_source_and_no_notice_of_another(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Werkstatt"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    SWITCHES.refuse_writes = True
    event = window(client)[0]
    client.patch(f"/api/events/{event['id']}", json={"title": "Werkstatt neu"})
    sync.run_due()
    notice = client.get("/api/sources").json()["notices"][0]["id"]
    anna = make_account("anna")
    with new_client(anna) as browser:
        assert browser.get("/api/sources").json()["sources"] == []
        assert browser.get("/api/sources").json()["notices"] == []
        attempts = [
            browser.get(f"/api/sources/{source}/calendars"),
            browser.put(f"/api/sources/{source}/calendars", json={"urls": []}),
            browser.post(f"/api/sources/{source}/reconnect", json={"password": "x"}),
            browser.post(f"/api/sources/{source}/refresh"),
            browser.put(f"/api/sources/{source}/link", json={"url": "https://x.example.com/a.ics"}),
            browser.delete(f"/api/sources/{source}"),
            browser.post(f"/api/sources/notices/{notice}", json={"create": True}),
        ]
        assert [answer.status_code for answer in attempts] == [404] * 7
    assert len(mirrored(source)) == 1
    assert client.get("/api/sources").json()["notices"][0]["id"] == notice


def test_time15_ac9_calendars_from_icloud_google_and_caldav_stay_off_the_calendar_account(
        dav_server: str, client: TestClient, account: Account, remote: Remote,
        monkeypatch: pytest.MonkeyPatch) -> None:
    import base64

    import httpx

    from app.main import app
    from app.services import devices, subscriptions

    remote.calendar("Arbeit")
    source = connected(client, dav_server, remote, ["Arbeit"])
    body = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:m@x\r\nDTSTAMP:20261101T000000Z\r\n"
            "DTSTART;VALUE=DATE:20261126\r\nSUMMARY:Gelbe Tonne\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    client.post("/api/sources/subscriptions", json={"url": "https://abfall.example.com/a.ics", "name": "Müll"})
    arbeit = mirrored(source)[0]
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        _device, password, _token = devices.create(db, row, "iPhone")
    token = base64.b64encode(f"{account.name}:{password}".encode()).decode()
    with TestClient(app, base_url="https://testserver") as plain:
        listing = plain.request("PROPFIND", f"/dav/calendars/{account.name}/", headers={
            "Authorization": f"Basic {token}", "Depth": "1"}).text
        direct = plain.request("PROPFIND", f"/dav/calendars/{account.name}/{arbeit.dav_name}/", headers={
            "Authorization": f"Basic {token}", "Depth": "0"})
    assert "Personal" in listing and "Müll" in listing and "Arbeit" not in listing
    assert arbeit.dav_name not in listing and direct.status_code == 404
    names = [item["name"] for item in client.get("/api/devices").json()["calendars"]]
    assert "Arbeit" not in names and "Müll" in names


def test_time15_calendar_name_from_icloud_is_unescaped_once():
    from app.services.remote_dav import display_name

    assert display_name("Q&amp;A") == "Q&A"
    assert display_name("Arbeit & Freizeit") == "Arbeit & Freizeit"
    assert display_name("Familie") == "Familie"
