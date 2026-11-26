"""TIME-18 AC-9 (plan "Apps verbinden" V30): in an event of an iCloud or Google calendar the member names people to
invite; nextime writes them into the event at the source as its organizer (the source's account), and the source
sends the invitations as usual. Answers of people who stay are kept; nextime's own calendars invite nobody; a mail
address only. The source is nextime's own calendar account in a thread (``remote_helpers``), as in TIME-15.
Suggestions come from nexfolk's people over a connection (``GET /api/people``)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import icalendar
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account, Event
from app.services import sync
from app.vendor.nexverbinden import reach

from .calendar_helpers import make_calendar, make_event
from .remote_helpers import (
    Remote,
    connected,
    dav_server,  # noqa: F401 - the fixture
    mirrored,
    quiet_world,  # noqa: F401 - the fixture
    remote_account,
    round_now,
    use_icloud,
)
from .test_v18_guest import ACCESS, click_connection

INVITED = [{"email": "kim@example.com", "name": "Kim Muster"}, {"email": "sam@example.com"}]


@pytest.fixture
def remote(client: TestClient) -> Iterator[Remote]:
    yield from remote_account()


def _icloud(client: TestClient, dav_server: str, remote: Remote, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    """An iCloud account with its calendar "Arbeit": (the calendar at the source, the one in nextime)."""
    use_icloud(monkeypatch, dav_server)
    arbeit = remote.calendar("Arbeit")
    source = connected(client, dav_server, remote, ["Arbeit"], kind="icloud")
    round_now(source)
    return arbeit, mirrored(source)[0].id


def _people(remote: Remote, calendar_url: Any, event_id: int) -> tuple[str, dict[str, str]]:
    with SessionLocal() as db:
        href = db.get(Event, event_id).href
    master = next(item for item in icalendar.Calendar.from_ical(remote.get(calendar_url, href).text).walk("VEVENT"))
    organizer = str(master.get("ORGANIZER", ""))
    found = master.get("ATTENDEE") or []
    found = found if isinstance(found, list) else [found]
    return organizer, {str(item)[7:]: str(item.params.get("PARTSTAT")) for item in found}


def test_time18_ac12_attendees_go_to_the_icloud_event_with_the_member_as_organizer(
        dav_server: str, client: TestClient, account: Account, remote: Remote, monkeypatch: pytest.MonkeyPatch) -> None:
    arbeit, target = _icloud(client, dav_server, remote, monkeypatch)
    made = make_event(client, "Planung", calendar_id=target, attendees=INVITED)
    sync.run_due()
    organizer, people = _people(remote, arbeit, made["id"])
    assert organizer == f"mailto:{remote.account.name}"
    assert people == {remote.account.name: "ACCEPTED", "kim@example.com": "NEEDS-ACTION", "sam@example.com": "NEEDS-ACTION"}
    # Kim answered at the source meanwhile; dropping Sam keeps Kim's answer.
    with SessionLocal() as db:
        row = db.get(Event, made["id"])
        parsed = icalendar.Calendar.from_ical(row.ical)
        for item in next(iter(parsed.walk("VEVENT"))).get("ATTENDEE"):
            if str(item).endswith("kim@example.com"):
                item.params["PARTSTAT"] = "ACCEPTED"
        row.ical = parsed.to_ical().decode()
        db.commit()
    changed = client.patch(f"/api/events/{made['id']}", json={"attendees": [{"email": "kim@example.com"}]})
    assert changed.status_code == 200, changed.text
    sync.run_due()
    _organizer, people = _people(remote, arbeit, made["id"])
    assert people == {remote.account.name: "ACCEPTED", "kim@example.com": "ACCEPTED"}
    # Nobody invited any more: no organizer either.
    assert client.patch(f"/api/events/{made['id']}", json={"attendees": []}).status_code == 200
    sync.run_due()
    organizer, people = _people(remote, arbeit, made["id"])
    assert organizer == "" and people == {}


def test_time18_ac12_nextime_s_own_calendars_and_wrong_addresses_invite_nobody(client: TestClient,
                                                                               account: Account) -> None:
    own = make_calendar(client, "Familie")
    refused = client.post("/api/events", json={"title": "Grillen", "start": "2026-11-28T18:00",
                                               "time_zone": "Europe/Berlin", "calendar_id": own,
                                               "attendees": INVITED})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "attendees_not_here"
    wrong = client.post("/api/events", json={"title": "Grillen", "start": "2026-11-28T18:00",
                                             "time_zone": "Europe/Berlin", "calendar_id": own,
                                             "attendees": [{"email": "kein-mail"}]})
    assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "attendee_invalid"
    # Without attendees nothing changes for nextime's own calendars.
    assert client.post("/api/events", json={"title": "Grillen", "start": "2026-11-28T18:00",
                                            "time_zone": "Europe/Berlin", "calendar_id": own}).status_code == 201


def test_time18_ac12_the_suggestions_come_from_nexfolk_s_people(client: TestClient, account: Account,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        assert request.url.path == "/api/v1/people" and request.headers["authorization"] == f"Bearer {ACCESS}"
        return httpx.Response(200, json={"people": [{"id": "c1", "name": "Kim Muster", "emails": ["kim@example.com"]},
                                                    {"id": "c2", "name": "Ohne", "emails": []}], "more": False})

    monkeypatch.setattr(reach, "TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setattr(reach, "RESOLVER", lambda host, port: ["93.184.216.34"])
    assert client.get("/api/people", params={"q": "kim"}).json() == {"connected": False, "people": []}
    click_connection(account.id, "nexfolk", "https://folk.example.com")
    found = client.get("/api/people", params={"q": "kim"}).json()
    assert found == {"connected": True, "people": [{"id": "c1", "name": "Kim Muster", "emails": ["kim@example.com"]}]}
    assert calls == [{"q": "kim", "limit": "10"}]
