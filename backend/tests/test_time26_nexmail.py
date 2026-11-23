"""TIME-26, what the API offers nexmail (2026-11-23): calendar groups, events by UID, answering invitations, entering an
invitation from a mail without anything being sent, a version to compare.

The other side of iCloud and CalDAV is nextime's own calendar account in a thread (``remote_helpers``); its switchboard
keeps the headers of every PUT and DELETE. Google is the fake behind ``httpx.MockTransport``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import Account, Calendar, Event, RemoteChange
from app.services import google, invitations, sync

from .calendar_helpers import calendars_of, code, make_calendar, make_event
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

ME = "tester@example.com"


@pytest.fixture
def program() -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as found:
        yield found


@pytest.fixture
def remote(client: TestClient) -> Iterator[Remote]:
    yield from remote_account()


def key(client: TestClient, level: str = "write", **values: Any) -> dict[str, str]:
    answer = client.post("/api/keys", json={"name": "nexmail", "level": level, **values})
    assert answer.status_code == 201, answer.text
    return {"Authorization": f"Bearer {answer.json()['token']}"}


def mail(uid: str = "meeting-1@example.com", *, method: str = "REQUEST", sequence: int = 0, title: str = "Planning",
         me: str = ME, start: str = "20261127T090000", end: str = "20261127T100000", extra: str = "") -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example Mail//EN\r\n"
            f"METHOD:{method}\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nSEQUENCE:{sequence}\r\nDTSTAMP:20261120T090000Z\r\n"
            f"DTSTART;TZID=Europe/Berlin:{start}\r\nDTEND;TZID=Europe/Berlin:{end}\r\nSUMMARY:{title}\r\n"
            "ORGANIZER;CN=Team Lead:mailto:lead@example.com\r\n"
            "ATTENDEE;CN=Team Lead;PARTSTAT=ACCEPTED:mailto:lead@example.com\r\n"
            f"ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\n{extra}"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nDESCRIPTION:x\r\nEND:VALARM\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


def invitation(uid: str, me: str = "NEEDS-ACTION") -> str:
    """An invitation as iCloud or a CalDAV server keeps it for the remote account."""
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iPhone OS 18.1//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261120T090000Z\r\nDTSTART;TZID=Europe/Berlin:20261127T090000\r\n"
            "DTEND;TZID=Europe/Berlin:20261127T100000\r\nSUMMARY:Planning\r\n"
            "ORGANIZER;CN=Team Lead:mailto:lead@example.com\r\n"
            "ATTENDEE;CN=Team Lead;PARTSTAT=ACCEPTED:mailto:lead@example.com\r\n"
            f"ATTENDEE;CN=Remote;PARTSTAT={me};RSVP=TRUE:mailto:remote@example.com\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


def take(program: TestClient, auth: dict[str, str], text: str, **values: Any) -> Any:
    return program.post("/api/v1/events/ical", headers=auth, json={"ical": text, "answer": "accepted", **values})


def sent() -> list[tuple[str, str, dict[str, str]]]:
    """The writes that reached the server (the library asks once without its login first)."""
    return [item for item in SWITCHES.writes if "authorization" in item[2]]


def live_rows(uid: str) -> list[Event]:
    """The events of the account under test with this UID (the remote account at the other side has its own)."""
    with SessionLocal() as db:
        rows = list(db.scalars(select(Event).join(Calendar, Calendar.id == Event.calendar_id).join(
            Account, Account.id == Calendar.owner_id).where(Event.uid == uid, Event.deleted_at.is_(None),
                                                             Account.name == "tester")))
        for row in rows:
            db.expunge(row)
        return rows


# --- 1. Calendars: group, source name, hidden --------------------------------------------------------------------


def test_time26_groups_calendars_name_their_group_source_and_hidden(
        dav_server: str, client: TestClient, account: Account, program: TestClient, remote: Remote) -> None:
    remote.calendar("Arbeit")
    connected(client, dav_server, remote, ["Arbeit"])
    familie = make_calendar(client, "Familie")
    client.patch(f"/api/calendars/{familie}", json={"hidden": True})
    every = {row["name"]: row["id"] for row in calendars_of(client)["calendars"]}
    found = {row["name"]: row for row in program.get(
        "/api/v1/calendars", headers=key(client, "read", calendar_ids=list(every.values()))).json()["calendars"]}
    arbeit = found["Arbeit"]
    assert (arbeit["group"], arbeit["source_name"].split(" ")[0], arbeit["hidden"]) == ("caldav", "remote", False)
    assert (found["Familie"]["group"], found["Familie"]["source_name"], found["Familie"]["hidden"]) == ("own", "", True)
    one = program.get(f"/api/v1/calendars/{familie}", headers=key(client, "read", calendar_ids=[familie])).json()
    assert one["hidden"] is True and one["group"] == "own"
    # A key for all lists the hidden calendar too, as hidden (for nexmail's sidebar, 2026-11-23).
    every_key = key(client, "read")
    listed = {row["name"]: row["hidden"] for row in program.get("/api/v1/calendars", headers=every_key).json()[
        "calendars"]}
    assert listed["Familie"] is True and listed["Arbeit"] is False
    assert program.get(f"/api/v1/calendars/{familie}", headers=every_key).json()["hidden"] is True


# --- 6. features, 7. state ------------------------------------------------------------------------------------------


def test_time26_features_name_what_nexmail_needs(client: TestClient, account: Account, program: TestClient) -> None:
    features = program.get("/api/v1/me", headers=key(client, "read")).json()["features"]
    assert {"calendar_groups", "uid_lookup", "answer", "ical_import", "state"} <= set(features)


def test_time26_state_stays_while_nothing_changes_and_moves_with_a_change(client: TestClient, account: Account,
                                                                         program: TestClient) -> None:
    auth = key(client, "read")
    first = program.get("/api/v1/state", headers=auth).json()["version"]
    assert program.get("/api/v1/state", headers=auth).json()["version"] == first
    made = make_event(client, "Dentist")
    second = program.get("/api/v1/state", headers=auth).json()["version"]
    assert second != first
    client.patch(f"/api/events/{made['id']}", json={"title": "Dentist 15:00"})
    assert program.get("/api/v1/state", headers=auth).json()["version"] != second


# --- 4. Entering an invitation from a mail ------------------------------------------------------------------------


def test_time26_import_keeps_the_invitation_answers_once_and_follows_sequence_and_cancel(
        client: TestClient, account: Account, program: TestClient) -> None:
    auth = key(client)
    made = take(program, auth, mail())
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["status"], body["reply_by"]) == ("created", "client")
    event = body["event"]
    assert event["uid"] == "meeting-1@example.com" and event["origin"] == "imip"
    assert event["organizer"] == {"name": "Team Lead", "email": "lead@example.com"}
    assert {"name": "Me", "email": ME, "status": "accepted"} in event["attendees"]
    assert event["invitation"] == "accepted"
    [row] = live_rows("meeting-1@example.com")
    assert "METHOD" not in row.ical and "VALARM" not in row.ical and "RSVP" not in row.ical
    assert "SCHEDULE-AGENT=CLIENT" in row.ical and "SEQUENCE:0" in row.ical
    # The same mail again: the same event, nothing new.
    again = take(program, auth, mail())
    assert (again.status_code, again.json()["status"], again.json()["event"]["id"]) == (200, "existing", event["id"])
    assert len(live_rows("meeting-1@example.com")) == 1
    # A higher SEQUENCE updates the copy, keeping the answer.
    moved = take(program, auth, mail(sequence=1, title="Planning, moved", start="20261127T110000",
                                     end="20261127T120000"), answer="tentative")
    assert (moved.status_code, moved.json()["status"]) == (200, "updated")
    assert moved.json()["event"]["title"] == "Planning, moved" and moved.json()["event"]["invitation"] == "tentative"
    # An older one changes nothing.
    assert take(program, auth, mail(sequence=0), answer="tentative").json()["status"] == "existing"
    assert live_rows("meeting-1@example.com")[0].title == "Planning, moved"
    # CANCEL puts the copy into the trash, saying why.
    gone = program.post("/api/v1/events/ical", headers=auth, json={"ical": mail(method="CANCEL", sequence=2)})
    assert (gone.status_code, gone.json()["status"], gone.json()["event"]) == (200, "cancelled", None)
    assert live_rows("meeting-1@example.com") == []
    trashed = client.get("/api/trash").json()["items"]
    assert [(item["title"], item["reason"]) for item in trashed] == [("Planning, moved", "cancelled")]
    # A cancellation of something not there.
    assert program.post("/api/v1/events/ical", headers=auth,
                        json={"ical": mail(method="CANCEL", uid="other@example.com")}).status_code == 404


def test_time26_import_refuses_what_is_no_invitation_to_the_member(client: TestClient, account: Account,
                                                                   program: TestClient) -> None:
    auth = key(client)
    assert code(take(program, auth, mail(me="somebody@example.com"))) == "attendee_unknown"
    # Named by the program: the mailbox it arrived in.
    named = take(program, auth, mail(me="somebody@example.com"), attendee="Somebody@example.com")
    assert named.status_code == 201 and named.json()["event"]["invitation"] == "accepted"
    assert code(take(program, auth, mail(uid="x@example.com", method="REPLY"))) == "ical_method_unsupported"
    assert code(take(program, auth, "BEGIN:VCALENDAR\r\nnonsense")) == "invalid_ical"
    assert take(program, auth, "X" * 1_000_001).status_code == 422
    assert code(program.post("/api/v1/events/ical", headers=auth, json={"ical": mail(uid="y@example.com")})) == \
        "invalid_answer"


def test_time26_import_twice_at_once_makes_one_event(client: TestClient, account: Account,
                                                     program: TestClient) -> None:
    auth = key(client)
    results: list[int] = []

    def send() -> None:
        with TestClient(app, base_url="http://testserver") as other:
            results.append(take(other, auth, mail(uid="twice@example.com")).status_code)

    threads = [threading.Thread(target=send) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(results) == [200, 200, 200, 201]
    assert len(live_rows("twice@example.com")) == 1


# --- 2. and 5. Events by UID, web_url ------------------------------------------------------------------------------


def test_time26_uid_lookup_finds_each_copy_with_its_state_and_a_link_into_nextime(
        client: TestClient, account: Account, program: TestClient) -> None:
    auth = key(client)
    made = take(program, auth, mail(), answer="declined").json()["event"]
    familie = make_calendar(client, "Familie")
    take(program, auth, mail(uid="other@example.com"), calendar_id=familie)
    found = program.get("/api/v1/events", headers=auth, params={"uid": "meeting-1@example.com"}).json()["events"]
    assert [(item["id"], item["invitation"], item["uid"], item["origin"]) for item in found] == [
        (made["id"], "declined", "meeting-1@example.com", "imip")]
    assert found[0]["start"] == "2026-11-27T08:00:00Z" and found[0]["occurrence"] is None
    assert found[0]["web_url"] == f"http://testserver/day?day=2026-11-27&event={made['id']}"
    assert made["web_url"] == found[0]["web_url"]
    # With a window: its occurrences there, declined ones too.
    windowed = program.get("/api/v1/events", headers=auth, params={
        "uid": "meeting-1@example.com", "start": "2026-11-01", "end": "2026-12-01"}).json()["events"]
    assert [item["id"] for item in windowed] == [made["id"]]
    assert program.get("/api/v1/events", headers=auth, params={"uid": "nothing@example.com"}).json() == {"events": []}
    # Without uid the window stays needed.
    assert program.get("/api/v1/events", headers=auth).status_code == 422
    # A key for other calendars does not see it.
    only = key(client, "read", calendar_ids=[familie])
    assert program.get("/api/v1/events", headers=only, params={"uid": "meeting-1@example.com"}).json()["events"] == []
    # The list names uid and link too; a series' occurrence with its key.
    serie = make_event(client, "Training", rule={"freq": "weekly", "weekdays": ["TH"]})
    listed = program.get("/api/v1/events", headers=auth, params={"start": "2026-11-26", "end": "2026-12-04"}).json()
    second = [item for item in listed["events"] if item["id"] == serie["id"]][1]
    assert second["uid"] and second["web_url"].endswith(f"&event={serie['id']}&occurrence={second['occurrence']}")
    assert "day=2026-12-03" in second["web_url"]


# --- 3. Answering ----------------------------------------------------------------------------------------------------


def test_time26_answer_a_copy_from_a_mail_the_program_replies_and_twice_is_nothing_new(
        client: TestClient, account: Account, program: TestClient) -> None:
    auth = key(client)
    made = take(program, auth, mail()).json()["event"]
    answer = program.post(f"/api/v1/events/{made['id']}/answer", headers=auth, json={"answer": "declined"})
    assert answer.status_code == 200, answer.text
    assert (answer.json()["reply_by"], answer.json()["changed"]) == ("client", True)
    assert answer.json()["event"]["invitation"] == "declined"
    again = program.post(f"/api/v1/events/{made['id']}/answer", headers=auth, json={"answer": "declined"}).json()
    assert again["changed"] is False
    # In the page it shows without buttons: the answer goes out by mail.
    detail = client.get(f"/api/events/{made['id']}").json()
    assert detail["invitation"]["can_answer"] is False and detail["invitation"]["why_not"] == "by_mail"
    # Not an invitation, a read key, a calendar outside the key.
    own = make_event(client, "Dentist")
    assert code(program.post(f"/api/v1/events/{own['id']}/answer", headers=auth,
                             json={"answer": "accepted"})) == "not_an_invitation"
    assert code(program.post(f"/api/v1/events/{made['id']}/answer", headers=key(client, "read"),
                             json={"answer": "accepted"})) == "token_reads_only"
    familie = make_calendar(client, "Familie")
    outside = key(client, calendar_ids=[familie])
    assert program.post(f"/api/v1/events/{made['id']}/answer", headers=outside,
                        json={"answer": "accepted"}).status_code == 404
    assert take(program, outside, mail(uid="z@example.com"), calendar_id=made["calendar_id"]).status_code == 404


def test_time26_answer_an_invitation_of_a_source_the_source_sends_it_once(
        dav_server: str, client: TestClient, account: Account, program: TestClient, remote: Remote,
        monkeypatch: pytest.MonkeyPatch) -> None:
    SWITCHES.schedules = True
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "i.ics", invitation("source-1@example.com"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    auth = key(client)
    [found] = program.get("/api/v1/events", headers=auth, params={"uid": "source-1@example.com"}).json()["events"]
    assert found["invitation"] == "open" and found["origin"] is None
    looked = invitations.context_of

    def slowly(*args: Any) -> Any:
        # Each request has read the event by now: without the lock all three would answer.
        found_now = looked(*args)
        time.sleep(0.3)
        return found_now

    monkeypatch.setattr(invitations, "context_of", slowly)
    results: list[dict[str, Any]] = []

    def send() -> None:
        with TestClient(app, base_url="http://testserver") as other:
            results.append(other.post(f"/api/v1/events/{found['id']}/answer", headers=auth,
                                      json={"answer": "accepted"}).json())

    threads = [threading.Thread(target=send) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(item["changed"] for item in results) == [False, False, True]
    assert {item["reply_by"] for item in results} == {"source"}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RemoteChange)) == 1
    round_now(source)
    puts = [path for method, path, _headers in sent() if method == "PUT"]
    assert len(puts) == 1
    # The source sends this one: no Schedule-Reply: F, no SCHEDULE-AGENT.
    assert "schedule-reply" not in sent()[0][2]
    stored = remote.get(arbeit, "i.ics").text
    assert "PARTSTAT=ACCEPTED:mailto:remote@example.com" in stored and "SCHEDULE-AGENT" not in stored


# --- 4. Sending nothing to iCloud / CalDAV and Google ----------------------------------------------------------------


def test_time26_import_into_caldav_tells_the_server_to_send_nothing(
        dav_server: str, client: TestClient, account: Account, program: TestClient, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    calendar = mirrored(source)[0].id
    auth = key(client)
    made = take(program, auth, mail(me="remote@example.com"), calendar_id=calendar)
    assert made.status_code == 201, made.text
    round_now(source)
    [(method, path, headers)] = sent()
    assert method == "PUT" and headers.get("schedule-reply") == "F"
    href = path.rsplit("/", 1)[-1]
    stored = remote.get(arbeit, href).text
    assert "SCHEDULE-AGENT=CLIENT:mailto:lead@example.com" in stored and "ORGANIZER;" in stored
    assert "PARTSTAT=ACCEPTED" in stored
    # A later answer goes the same quiet way, also after the server's text came back in a round.
    round_now(source)
    answer = program.post(f"/api/v1/events/{made.json()['event']['id']}/answer", headers=auth,
                          json={"answer": "tentative"}).json()
    assert answer["reply_by"] == "client"
    round_now(source)
    method, _path, headers = sent()[-1]
    assert method == "PUT" and headers.get("schedule-reply") == "F"
    assert "SCHEDULE-AGENT=CLIENT" in remote.get(arbeit, href).text
    # And the cancellation deletes it there quietly.
    program.post("/api/v1/events/ical", headers=auth, json={"ical": mail(method="CANCEL", me="remote@example.com")})
    round_now(source)
    method, _path, headers = sent()[-1]
    assert method == "DELETE" and headers.get("schedule-reply") == "F"


def test_time26_a_source_bringing_the_same_uid_wins_and_takes_the_answer_quietly(
        dav_server: str, client: TestClient, account: Account, program: TestClient, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    auth = key(client)
    personal = next(row["id"] for row in calendars_of(client)["calendars"] if row["group"] == "own")
    copy = take(program, auth, mail(uid="both@example.com"), answer="tentative", calendar_id=personal).json()["event"]
    remote.put(arbeit, "both.ics", invitation("both@example.com"))
    SWITCHES.writes.clear()
    round_now(source)
    # The copy went into the trash, saying why; the event stands once, with the member's answer.
    [left] = live_rows("both@example.com")
    assert left.id != copy["id"]
    assert [(item["id"], item["reason"]) for item in client.get("/api/trash").json()["items"]] == [
        (copy["id"], "source_has_it")]
    found = program.get("/api/v1/events", headers=auth, params={"uid": "both@example.com"}).json()["events"]
    assert [(item["id"], item["invitation"]) for item in found] == [(left.id, "tentative")]
    round_now(source)
    [(method, _path, headers)] = sent()
    assert method == "PUT" and headers.get("schedule-reply") == "F"
    stored = remote.get(arbeit, "both.ics").text
    assert "PARTSTAT=TENTATIVE" in stored and "SCHEDULE-AGENT=CLIENT" in stored
    # The answer was the source's already: nothing written.
    remote.put(arbeit, "same.ics", invitation("same@example.com", me="ACCEPTED"))
    take(program, auth, mail(uid="same@example.com"), calendar_id=personal)
    SWITCHES.writes.clear()
    round_now(source)
    round_now(source)
    assert sent() == []


def _google(client: TestClient, remote: Remote, monkeypatch: pytest.MonkeyPatch, dav_server: str) -> tuple[
        FakeGoogle, int, int]:
    fake = FakeGoogle(email="remote@example.com")
    monkeypatch.setattr(google, "transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(google, "CALDAV_URL", dav_server + f"/dav/calendars/{remote.account.name}/{{id}}/")
    SWITCHES.bearer[fake.access] = (remote.account.name, remote.password)
    arbeit = remote.calendar("Arbeit")
    fake.calendars.append((remote.dav_name(arbeit), "Arbeit", "#7986cb", "owner"))
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
    return fake, source, arbeit


def test_time26_import_into_google_goes_through_events_import_and_sends_nothing(
        dav_server: str, client: TestClient, account: Account, program: TestClient, remote: Remote,
        monkeypatch: pytest.MonkeyPatch) -> None:
    fake, source, _arbeit = _google(client, remote, monkeypatch, dav_server)
    auth = key(client)
    made = take(program, auth, mail(me="remote@example.com"), calendar_id=mirrored(source)[0].id)
    assert made.status_code == 201, made.text
    round_now(source)
    [body] = fake.imports
    assert body["iCalUID"] == "meeting-1@example.com" and body["organizer"]["email"] == "lead@example.com"
    assert {"email": "remote@example.com", "displayName": "Me", "responseStatus": "accepted"} in body["attendees"]
    assert body["start"]["dateTime"] == "2026-11-27T08:00:00Z"
    assert not fake.patches and not [item for item in sent() if item[0] == "PUT"]
    program.post("/api/v1/events/ical", headers=auth, json={"ical": mail(method="CANCEL", me="remote@example.com")})
    round_now(source)
    assert [params.get("sendUpdates") for _id, params in fake.deletes] == ["none"]


def test_time26_google_bringing_the_same_uid_takes_the_answer_without_sending(
        dav_server: str, client: TestClient, account: Account, program: TestClient, remote: Remote,
        monkeypatch: pytest.MonkeyPatch) -> None:
    fake, source, arbeit = _google(client, remote, monkeypatch, dav_server)
    auth = key(client)
    personal = next(row["id"] for row in calendars_of(client)["calendars"] if row["group"] == "own")
    take(program, auth, mail(uid="g@example.com"), answer="declined", calendar_id=personal)
    remote.put(arbeit, "g.ics", invitation("g@example.com"))
    fake.events["ev1"] = {"id": "ev1", "iCalUID": "g@example.com", "attendees": [
        {"email": "lead@example.com", "organizer": True, "responseStatus": "accepted"},
        {"email": "remote@example.com", "self": True, "responseStatus": "needsAction"}]}
    round_now(source)
    assert len(live_rows("g@example.com")) == 1
    sync.run_due()
    round_now(source)
    assert [(event_id, body["attendees"][-1]["responseStatus"], params["sendUpdates"])
            for event_id, body, params in fake.patches] == [("ev1", "declined", "none")]
