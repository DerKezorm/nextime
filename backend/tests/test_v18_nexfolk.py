"""nexfolk over a connection made by a click: the birthdays as a subscribed calendar (V1 8a), the names of attendees
from nexfolk's people (V29)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models import Account
from app.services import internet
from app.vendor.nexverbinden import reach

from .test_v18_guest import ACCESS, click_connection

ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//nexfolk//EN\r
X-WR-CALNAME:Geburtstage\r
BEGIN:VEVENT\r
UID:kim-birthday@example.com\r
DTSTART;VALUE=DATE:20261127\r
RRULE:FREQ=YEARLY\r
SUMMARY:Kim (40)\r
END:VEVENT\r
END:VCALENDAR\r
"""


@pytest.fixture
def folk(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """nexfolk's calendar of birthdays, open only with the connection's access (``/api/v1/birthdays.ics``)."""
    from app.services import subscriptions

    seen: dict[str, Any] = {"calls": [], "token": ACCESS}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["calls"].append((request.url.path, request.headers.get("authorization", "")))
        if request.headers.get("authorization") != f"Bearer {seen['token']}":
            return httpx.Response(401, json={"detail": {"code": "token_invalid", "message": "No."}})
        return httpx.Response(200, text=ICS, headers={"Content-Type": "text/calendar"})

    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(handle))
    monkeypatch.setattr(subscriptions, "resolve", lambda host: ["93.184.216.34"])
    # The rounds of subscriptions wait while the internet is away; here it is there.
    monkeypatch.setattr(internet, "probe_override", lambda: True)
    return seen


def _calendars(client: TestClient) -> list[dict[str, Any]]:
    answer = client.get("/api/calendars").json()
    return answer["calendars"] if isinstance(answer, dict) else answer


def test_connecting_with_nexfolk_makes_the_birthday_calendar_as_a_subscription(client: TestClient, account: Account,
                                                                                folk: dict) -> None:
    """V1 8a: "Mit nexfolk verbinden" subscribes the birthdays itself, with the connection's access, a calendar read
    only like any subscription; once per connection; the round fetches with the access again; the connection ends,
    the calendar goes."""
    from app.db import SessionLocal
    from app.services import subscriptions, sync, verbinden_store

    click_connection(account.id, "nexfolk", "https://folk.example.com")
    with SessionLocal() as db:
        assert verbinden_store.calendars_due(db) == 1
        assert verbinden_store.calendars_due(db) == 0
        source = subscriptions.by_connection(db, account.id, "nexfolk")
        assert source is not None and source.kind == "subscription"
        source_id = source.id
    assert folk["calls"] == [("/api/v1/birthdays.ics", f"Bearer {ACCESS}")]
    named = [calendar for calendar in _calendars(client) if calendar.get("name") == "Geburtstage"]
    assert len(named) == 1 and named[0].get("read_only", True)
    assert named[0]["origin"]["connection"] == "nexfolk"  # the sidebar's group "Aus nexfolk"
    assert sync.run_source(source_id, force=True)
    assert len(folk["calls"]) == 2
    with SessionLocal() as db:
        store = verbinden_store.Store(db)
        store.remove(store.connection(account.id, "nexfolk").id)
        store.commit()
    sync.run_source(source_id, force=True)
    with SessionLocal() as db:
        assert subscriptions.by_connection(db, account.id, "nexfolk") is None
    assert not [calendar for calendar in _calendars(client) if calendar.get("name") == "Geburtstage"]


def test_a_401_of_nexfolk_renews_the_access_once_then_the_subscription_says_so(client: TestClient, account: Account,
                                                                               folk: dict,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import SessionLocal
    from app.models import Source
    from app.services import subscriptions, sync, verbinden_store

    click_connection(account.id, "nexfolk", "https://folk.example.com")
    with SessionLocal() as db:
        verbinden_store.calendars_due(db)
        source_id = subscriptions.by_connection(db, account.id, "nexfolk").id
    renewed = "ntc_" + "n" * 40
    monkeypatch.setattr(reach, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, json={
        "access_token": renewed, "refresh_token": "ntcr_" + "m" * 40, "expires_in": 3600})))
    monkeypatch.setattr(reach, "RESOLVER", lambda host, port: ["93.184.216.34"])
    folk["token"] = renewed
    sync.run_source(source_id, force=True)
    with SessionLocal() as db:
        assert db.get(Source, source_id).state == "ok"
    assert folk["calls"][-1] == ("/api/v1/birthdays.ics", f"Bearer {renewed}")
    folk["token"] = "ntc_" + "z" * 40
    sync.run_source(source_id, force=True)
    with SessionLocal() as db:
        assert db.get(Source, source_id).state == "expired"


PEOPLE = {"people": [{"id": "c1", "name": "Kim Muster", "emails": ["Kim@Example.com", "kim.privat@example.com"]},
                     {"id": "c2", "name": "Kimberly", "emails": ["kimberly@example.com"]}], "more": False}


@pytest.fixture
def people(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"calls": []}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["calls"].append((request.url.path, dict(request.url.params)))
        if request.headers.get("authorization") != f"Bearer {ACCESS}":
            return httpx.Response(401, json={"detail": {"code": "token_invalid", "message": "No."}})
        return httpx.Response(200, json=PEOPLE)

    monkeypatch.setattr(reach, "TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setattr(reach, "RESOLVER", lambda host, port: ["93.184.216.34"])
    return seen


def test_attendees_get_their_names_from_the_people_of_nexfolk(client: TestClient, account: Account,
                                                               people: dict) -> None:
    """V29: one search per address in ``/api/v1/people`` (id, name, addresses only); a name only where an address of
    the person is exactly the attendee's; nothing without a connection."""
    asked = client.get("/api/people/names", params={"addresses": "kim@example.com,someone@example.com,nonsense"})
    assert asked.json() == {"connected": False, "names": {}} and people["calls"] == []
    click_connection(account.id, "nexfolk", "https://folk.example.com")
    found = client.get("/api/people/names", params={"addresses": "kim@example.com,someone@example.com,nonsense"})
    assert found.json() == {"connected": True, "names": {"kim@example.com": "Kim Muster"}}
    assert people["calls"] == [("/api/v1/people", {"q": "kim@example.com", "limit": "5"}),
                               ("/api/v1/people", {"q": "someone@example.com", "limit": "5"})]
