"""TIME-18 Answering invitations: what counts as an invitation, the answer back through CalDAV scheduling or
Google's API, waiting and changed invitations, series, many attendees, shared calendars.

The other side is nextime's calendar account in a thread (``remote_helpers``); its switchboard announces CalDAV
scheduling where a test wants a server that sends answers itself, as iCloud does.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import RIGHT_READ, Account, RemoteChange
from app.services import google, sync

from .calendar_helpers import code, share, window
from .conftest import make_account, new_client
from .remote_helpers import (
    SWITCHES,
    FakeGoogle,
    Remote,
    connected,
    dav_server,  # noqa: F401 - the fixture
    mirrored,
    quiet_world,  # noqa: F401 - the fixture
    remote_account,
    round_now,
)


@pytest.fixture
def remote(client: TestClient) -> Iterator[Remote]:
    yield from remote_account()


def invitation(uid: str, title: str, start: str = "20261127T090000", end: str = "20261127T100000", *,
               me: str = "NEEDS-ACTION", extra: str = "", others: int = 1, zone: str = "Europe/Berlin",
               organizer: str = "Chris") -> str:
    people = "".join(f"ATTENDEE;CN=Gast {n};PARTSTAT=ACCEPTED:mailto:gast{n}@example.com\r\n"
                     for n in range(others - 1))
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iPhone OS 18.1//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261120T090000Z\r\nDTSTART;TZID={zone}:{start}\r\nDTEND;TZID={zone}:{end}\r\n"
            f"SUMMARY:{title}\r\nORGANIZER;CN={organizer}:mailto:chris@example.com\r\n"
            "ATTENDEE;CN=Chris;PARTSTAT=ACCEPTED:mailto:chris@example.com\r\n"
            f"ATTENDEE;CN=Remote;PARTSTAT={me};RSVP=TRUE:mailto:remote@example.com\r\n{people}{extra}"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


def _setup(dav_server: str, client: TestClient, remote: Remote, *texts: str, schedules: bool = True) -> tuple[int, int]:
    SWITCHES.schedules = schedules
    arbeit = remote.calendar("Arbeit")
    for number, text in enumerate(texts):
        remote.put(arbeit, f"i{number}.ics", text)
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    return source, arbeit


def _open(client: TestClient) -> list[dict]:
    answer = client.get("/api/invitations")
    assert answer.status_code == 200, answer.text
    return answer.json()


@pytest.fixture(autouse=True)
def friday(monkeypatch: pytest.MonkeyPatch) -> None:
    """Now is Monday 23 November 2026, before every invitation of the tests."""
    from datetime import UTC, datetime

    monkeypatch.setattr(sync, "clock", lambda: datetime(2026, 11, 23, 8, 0, tzinfo=UTC))


# --- Seeing invitations (AC-1, AC-3, AC-6, EC-7, EC-8, EC-9, EC-11) --------------------------------------------------


def test_time18_ac1_ac6_open_invitations_are_counted_and_marked_with_who_invites(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    _setup(dav_server, client, remote, invitation("a@x", "Kundentermin"), invitation("b@x", "Workshop",
                                                                                      "20261130T090000",
                                                                                      "20261130T120000"))
    found = _open(client)
    assert [(row["title"], row["by"]) for row in found] == [("Kundentermin", "Chris"), ("Workshop", "Chris")]
    rows = {row["title"]: row["invitation"] for row in window(client)}
    assert rows["Kundentermin"] == {"state": "open", "by": "Chris"}


def test_time18_ac3_attendees_with_their_state_the_organizer_and_you(dav_server: str, client: TestClient,
                                                                     account: Account, remote: Remote) -> None:
    _setup(dav_server, client, remote, invitation("a@x", "Kundentermin", others=3))
    detail = client.get(f"/api/events/{window(client)[0]['id']}").json()
    people = [(person["name"], person["state"], person["organizer"], person["self"]) for person in detail["attendees"]]
    assert people[0] == ("Chris", "accepted", True, False)
    assert ("Remote", "open", False, True) in people and ("Gast 0", "accepted", False, False) in people
    assert detail["attendees_count"] == 4
    assert detail["invitation"]["source"] == "caldav" and detail["invitation"]["can_answer"] is True


def test_time18_ec7_code_in_an_invitation_comes_as_text(dav_server: str, client: TestClient, account: Account,
                                                        remote: Remote) -> None:
    title = "<script>alert(1)</script>"
    _setup(dav_server, client, remote, invitation("a@x", title, organizer='"<img src=x onerror=alert(2)>"'))
    row = window(client)[0]
    assert row["title"] == title and row["invitation"]["by"] == "<img src=x onerror=alert(2)>"


def test_time18_ec8_two_hundred_attendees_come_with_their_count(dav_server: str, client: TestClient,
                                                                account: Account, remote: Remote) -> None:
    _setup(dav_server, client, remote, invitation("a@x", "Betriebsversammlung", others=200))
    detail = client.get(f"/api/events/{window(client)[0]['id']}").json()
    assert detail["attendees_count"] == 201


def test_time18_ec9_an_open_invitation_in_the_past_no_longer_counts(dav_server: str, client: TestClient,
                                                                    account: Account, remote: Remote) -> None:
    _setup(dav_server, client, remote, invitation("a@x", "Vorbei", "20261120T090000", "20261120T100000"),
           invitation("b@x", "Kommt", "20261201T090000", "20261201T100000"))
    assert [row["title"] for row in _open(client)] == ["Kommt"]


def test_time18_ec11_an_invitation_from_another_zone_stands_at_its_instant(dav_server: str, client: TestClient,
                                                                           account: Account, remote: Remote) -> None:
    _setup(dav_server, client, remote, invitation("a@x", "Call", "20261127T090000", "20261127T100000",
                                                  zone="America/New_York"))
    row = window(client)[0]
    assert row["start"] == "2026-11-27T14:00:00Z" and row["time_zone"] == "America/New_York"


# --- Answering (AC-2, AC-5, AC-7, EC-1, EC-3, EC-5, EC-6) ------------------------------------------------------------


def test_time18_ac2_ac5_the_answer_goes_out_through_the_server_and_can_change(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    _source, arbeit = _setup(dav_server, client, remote, invitation("a@x", "Kundentermin"))
    event = window(client)[0]
    answer = client.post(f"/api/events/{event['id']}/answer", json={"answer": "accepted", "etag": event["etag"]})
    assert answer.status_code == 200, answer.text
    assert answer.json()["invitation"]["state"] == "accepted" and answer.json()["waiting"] is True
    assert _open(client) == []
    sync.run_due()
    assert "PARTSTAT=ACCEPTED:mailto:remote@example.com" in remote.get(arbeit, "i0.ics").text
    detail = client.get(f"/api/events/{event['id']}").json()
    changed = client.post(f"/api/events/{event['id']}/answer", json={"answer": "tentative", "etag": detail["etag"]})
    assert changed.json()["invitation"]["state"] == "tentative"
    sync.run_due()
    assert "PARTSTAT=TENTATIVE:mailto:remote@example.com" in remote.get(arbeit, "i0.ics").text


def test_time18_ac7_after_the_answer_accepted_and_tentative_stay_declined_leaves_the_views(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    _setup(dav_server, client, remote, invitation("a@x", "Eins"), invitation("b@x", "Zwei"),
           invitation("c@x", "Drei"))
    rows = {row["title"]: row for row in window(client)}
    for title, reply in (("Eins", "accepted"), ("Zwei", "tentative"), ("Drei", "declined")):
        assert client.post(f"/api/events/{rows[title]['id']}/answer", json={"answer": reply}).status_code == 200
    shown = {row["title"]: row["invitation"]["state"] for row in window(client)}
    assert shown == {"Eins": "accepted", "Zwei": "tentative"}
    # Declined: still found (the search asks with declined ones), and the answer can change there.
    everything = client.get("/api/events", params={"start": "2026-11-01T00:00:00Z", "end": "2027-01-01T00:00:00Z",
                                                   "declined": "true"}).json()
    assert {row["title"] for row in everything} == {"Eins", "Zwei", "Drei"}
    again = client.post(f"/api/events/{rows['Drei']['id']}/answer", json={"answer": "accepted"})
    assert again.status_code == 200 and len(window(client)) == 3


def test_time18_ec1_ec4_a_changed_or_cancelled_invitation_shows_its_new_state(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    source, arbeit = _setup(dav_server, client, remote, invitation("a@x", "Kundentermin"))
    event = window(client)[0]
    # Answered on the iPhone meanwhile: nextime shows it after the next round.
    remote.put(arbeit, "i0.ics", invitation("a@x", "Kundentermin", me="ACCEPTED"))
    round_now(source)
    stale = client.post(f"/api/events/{event['id']}/answer", json={"answer": "declined", "etag": event["etag"]})
    assert stale.status_code == 409 and code(stale) == "invitation_changed"
    assert window(client)[0]["invitation"]["state"] == "accepted"
    remote.put(arbeit, "i0.ics", invitation("a@x", "Kundentermin", extra="STATUS:CANCELLED\r\n"))
    round_now(source)
    cancelled = client.post(f"/api/events/{event['id']}/answer", json={"answer": "accepted"})
    assert code(cancelled) == "invitation_changed"


def test_time18_ec3_away_the_answer_counts_here_and_goes_out_later(dav_server: str, client: TestClient,
                                                                   account: Account, remote: Remote) -> None:
    source, arbeit = _setup(dav_server, client, remote, invitation("a@x", "Kundentermin"))
    event = window(client)[0]
    SWITCHES.down = True
    assert client.post(f"/api/events/{event['id']}/answer", json={"answer": "accepted"}).status_code == 200
    sync.run_due()
    row = window(client)[0]
    assert row["invitation"]["state"] == "accepted" and row["waiting"] is True
    SWITCHES.down = False
    round_now(source)
    assert window(client)[0]["waiting"] is False
    assert "PARTSTAT=ACCEPTED:mailto:remote@example.com" in remote.get(arbeit, "i0.ics").text


def test_time18_ec5_two_clicks_send_one_answer(dav_server: str, client: TestClient, account: Account,
                                               remote: Remote) -> None:
    _setup(dav_server, client, remote, invitation("a@x", "Kundentermin"))
    SWITCHES.down = True
    event = window(client)[0]
    for _ in range(2):
        assert client.post(f"/api/events/{event['id']}/answer", json={"answer": "accepted"}).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RemoteChange)) == 1


SERIES = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iPhone OS 18.1//EN\r\nBEGIN:VEVENT\r\n"
          "UID:serie@x\r\nDTSTAMP:20261120T090000Z\r\nDTSTART;TZID=Europe/Berlin:20261124T090000\r\n"
          "DTEND;TZID=Europe/Berlin:20261124T093000\r\nRRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:Jour fixe\r\n"
          "ORGANIZER;CN=Chris:mailto:chris@example.com\r\n"
          "ATTENDEE;CN=Remote;PARTSTAT=NEEDS-ACTION:mailto:remote@example.com\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def test_time18_ec6_a_series_answers_for_all_or_for_this_occurrence(dav_server: str, client: TestClient,
                                                                    account: Account, remote: Remote) -> None:
    _source, arbeit = _setup(dav_server, client, remote, SERIES)
    rows = window(client)
    assert [row["invitation"]["state"] for row in rows] == ["open"] * 4
    second = rows[1]
    answer = client.post(f"/api/events/{second['id']}/answer", json={
        "answer": "declined", "scope": "this", "occurrence": second["occurrence"]})
    assert answer.status_code == 200, answer.text
    assert [row["invitation"]["state"] for row in window(client)] == ["open"] * 3
    # "For all" answers every occurrence, the one declined before too.
    client.post(f"/api/events/{rows[0]['id']}/answer", json={"answer": "accepted", "scope": "all"})
    assert [row["invitation"]["state"] for row in window(client)] == ["accepted"] * 4
    sync.run_due()
    text = remote.get(arbeit, "i0.ics").text
    assert "RECURRENCE-ID;TZID=Europe/Berlin:20261201T090000" in text


# --- Where answers cannot go (EC-2, EC-10) ---------------------------------------------------------------------------


def test_time18_ec2_a_server_that_sends_no_answers_shows_no_buttons(dav_server: str, client: TestClient,
                                                                    account: Account, remote: Remote) -> None:
    _setup(dav_server, client, remote, invitation("a@x", "Kundentermin"), schedules=False)
    event = window(client)[0]
    detail = client.get(f"/api/events/{event['id']}").json()
    assert detail["invitation"]["can_answer"] is False and detail["invitation"]["why_not"] == "no_replies"
    refused = client.post(f"/api/events/{event['id']}/answer", json={"answer": "accepted"})
    assert refused.status_code == 409 and code(refused) == "answers_not_possible"


def test_time18_ec2_a_subscription_with_attendees_takes_no_answers(client: TestClient, account: Account,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import subscriptions

    body = invitation("a@x", "Kundentermin")
    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(lambda request: httpx.Response(200,
                                                                                                       text=body)))
    assert client.post("/api/sources/subscriptions", json={"url": "https://x.example.com/a.ics"}).status_code == 201
    event = window(client)[0]
    assert event["invitation"] is None
    detail = client.get(f"/api/events/{event['id']}").json()
    assert detail["invitation"] is None and detail["no_answers"] == "read_only_source"
    assert detail["attendees_count"] == 2


def test_time18_ec10_a_shared_calendar_shows_no_invitation_to_the_others(dav_server: str, client: TestClient,
                                                                         account: Account, remote: Remote) -> None:
    source, _arbeit = _setup(dav_server, client, remote, invitation("a@x", "Kundentermin"))
    anna = make_account("anna")
    share(mirrored(source)[0].id, anna.id, RIGHT_READ)
    with new_client(anna) as browser:
        event = window(browser)[0]
        assert event["invitation"] is None
        assert browser.get(f"/api/events/{event['id']}").json()["invitation"] is None
        assert browser.get("/api/invitations").json() == []
        assert browser.post(f"/api/events/{event['id']}/answer", json={"answer": "accepted"}).status_code == 409


# --- Google answers through its API (AC-2, EC-6) ---------------------------------------------------------------------


def test_time18_ac2_google_answers_go_through_the_calendar_api_for_all_or_one(
        dav_server: str, client: TestClient, account: Account, remote: Remote, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGoogle(email="remote@example.com")
    monkeypatch.setattr(google, "transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(google, "CALDAV_URL", dav_server + f"/dav/calendars/{remote.account.name}/{{id}}/")
    SWITCHES.bearer[fake.access] = (remote.account.name, remote.password)
    arbeit = remote.calendar("Arbeit")
    fake.calendars.append((remote.dav_name(arbeit), "Arbeit", "#7986cb", "owner"))
    remote.put(arbeit, "s.ics", SERIES)
    fake.events["ev1"] = {"id": "ev1", "iCalUID": "serie@x", "attendees": [
        {"email": "chris@example.com", "organizer": True, "responseStatus": "accepted"},
        {"email": "remote@example.com", "self": True, "responseStatus": "needsAction"}],
        "instances": [{"id": "ev1_20261201T080000Z", "originalStartTime": {"dateTime": "2026-12-01T09:00:00+01:00"},
                       "attendees": [{"email": "remote@example.com", "self": True,
                                      "responseStatus": "needsAction"}]}]}
    assert client.put("/api/server/google", json={"client_id": fake.client_id,
                                                  "client_secret": fake.client_secret}).status_code == 200
    started = client.post("/api/sources/google/start", json={}).json()["url"]
    state = parse_qs(urlsplit(started).query)["state"][0]
    place = client.get("/api/google/callback", params={"state": state, "code": "good-code"},
                       follow_redirects=False).headers["location"]
    source = int(parse_qs(urlsplit(place).query)["choose"][0])
    choice = client.get(f"/api/sources/{source}/calendars").json()
    client.put(f"/api/sources/{source}/calendars", json={"urls": [item["url"] for item in choice["calendars"]]})
    round_now(source)
    rows = window(client)
    assert rows[0]["invitation"] == {"state": "open", "by": "Chris"}
    assert client.post(f"/api/events/{rows[0]['id']}/answer", json={"answer": "accepted"}).status_code == 200
    second = rows[1]
    assert client.post(f"/api/events/{second['id']}/answer", json={
        "answer": "declined", "scope": "this", "occurrence": second["occurrence"]}).status_code == 200
    sync.run_due()
    assert [(event_id, body["attendees"][-1]["responseStatus"], params["sendUpdates"])
            for event_id, body, params in fake.patches] == [("ev1", "accepted", "all"),
                                                             ("ev1_20261201T080000Z", "declined", "all")]
    # No PUT of the event to Google's CalDAV for an answer: Google's API sends it.
    assert not [path for method, path in SWITCHES.seen if method == "PUT" and "/dav/" in path]
    assert window(client)[0]["waiting"] is False


# --- After the review (2026-11-22) -----------------------------------------------------------------------------------


def test_time18_ec2_an_invitation_in_a_calendar_only_to_be_read_has_no_buttons(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    from app.models import RIGHT_READ as READ

    from .calendar_helpers import make_calendar

    SWITCHES.schedules = True
    chris = make_account("chris")
    with new_client(chris) as browser:
        team = make_calendar(browser, "Team")
    share(team, remote.account.id, READ)
    text = invitation("a@x", "Teamtreffen")
    with SessionLocal() as db:
        from app.models import Event
        from app.services import ical as ical_service

        row = Event(calendar_id=team, uid="a@x", href="i.ics", ical="", etag="", starts_at=sync.clock())
        db.add(row)
        parsed = ical_service.parse(text)
        from app.services import events as events_service

        events_service.write(db, row, text, parsed, None)
        db.commit()
    source = connected(client, dav_server, remote, ["Team"])
    round_now(source)
    event = window(client)[0]
    detail = client.get(f"/api/events/{event['id']}").json()
    assert detail["invitation"]["can_answer"] is False and detail["invitation"]["why_not"] == "read_only_source"
    refused = client.post(f"/api/events/{event['id']}/answer", json={"answer": "accepted"})
    assert refused.status_code == 409 and code(refused) == "answers_not_possible"
