"""TIME-19 after the review of stage 5 ("TIME-19 Prüfbericht"): sharing sends nothing back to iCloud, Google or a
CalDAV server (finding 1); a guest's token never reaches the log (finding 2); the last day of a link in the owner's
zone (finding 5); whole days on the guest page by the owner's date (finding 6); the subscription built anew after a
change and without attachments (finding 7); a calendar program told to wait, not that the link is gone (finding 8);
a blocked owner's calendar stays writable for whoever may edit it (finding 9)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import icalendar
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import middleware
from app.db import SessionLocal
from app.main import app
from app.models import RIGHT_BUSY, RIGHT_EDIT, RIGHT_READ, Account, Calendar, GuestLink, RemoteChange
from app.services import accounts, guest, logs, shares

from .calendar_helpers import make_calendar, make_event, window
from .conftest import make_account, new_client
from .remote_helpers import (  # noqa: F401 - the fixtures
    Remote,
    connected,
    dav_server,
    event_text,
    mirrored,
    quiet_world,
    remote_account,
    round_now,
)
from .test_time19 import guest_client, link_on, put_share, stored, token_of, vevent
from .test_time20 import calendar_url, device_for, raw


@pytest.fixture
def remote(client: TestClient) -> Iterator[Remote]:
    yield from remote_account()


@pytest.fixture(autouse=True)
def _fresh_feed_cache() -> None:
    guest.forget()


def _waiting() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(RemoteChange)) or 0)


# --- Finding 1: sharing changes nothing at the source ------------------------------------------------------------


def test_time19_ac2_ac3_sharing_changing_and_taking_back_send_nothing_to_the_source_and_lose_no_change_made_there(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    anna = make_account("anna")
    arbeit = remote.calendar("Arbeit")
    for number in range(3):
        remote.put(arbeit, f"e{number}.ics", event_text(f"e{number}@x", f"Termin {number}"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    calendar = mirrored(source)[0].id
    assert _waiting() == 0
    # Changed on the iPhone, a moment before the owner shares the calendar in nextime, before the next round.
    remote.put(arbeit, "e0.ics", event_text("e0@x", "Termin 0 vom iPhone",
                                            extra="LAST-MODIFIED:20261122T120000Z\r\n"))
    for step in (lambda: put_share(client, calendar, anna.id, RIGHT_BUSY),
                 lambda: put_share(client, calendar, anna.id, RIGHT_READ),
                 lambda: client.delete(f"/api/calendars/{calendar}/sharing/{anna.id}")):
        assert step().status_code == 200
        assert _waiting() == 0
    round_now(source)
    assert "SUMMARY:Termin 0 vom iPhone" in remote.get(arbeit, "e0.ics").text
    assert "Termin 0 vom iPhone" in [row["title"] for row in window(client)]
    # The devices still sync anew: every event counts as changed for the calendar account.
    password = device_for(anna)
    put_share(client, calendar, anna.id, RIGHT_READ)
    listing = raw("PROPFIND", calendar_url(anna, calendar), anna, password, Depth="1")
    assert listing.status_code == 207 and listing.content.count(b"<D:href>") + listing.content.count(b"<d:href>") >= 4


# --- Finding 2: the guest's token never in the log -------------------------------------------------------------------


def test_time19_ec4_the_token_of_a_guest_link_never_reaches_the_log(client: TestClient, account: Account,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    token = token_of(link_on(client, familie))
    for line in (f"GET /abo/{token}.ics -> 200", f"GET /abo/{token} -> 200", f"GET /api/guest/{token}?offset=50"):
        assert token not in logs.redact(line)
    # Every request is "slow" now, so each path goes into the log; the subscription fails once with a 500.
    monkeypatch.setattr(middleware, "SLOW_MS", 0)
    with guest_client() as browser:
        assert browser.get(f"/abo/{token}.ics").status_code == 200
        assert browser.get(f"/api/guest/{token}").status_code == 200
        assert browser.get(f"/abo/{token}").status_code == 200
        monkeypatch.setattr(guest, "feed", _broken)
        assert browser.get(f"/abo/{token}.ics").status_code == 503
    text = logs.log_file().read_text(encoding="utf-8")
    assert "/abo/" in text and "/api/guest/" in text
    assert token not in text


def _broken(*_args: object, **_kwargs: object) -> tuple[bytes, str]:
    raise RuntimeError("the database went away")


# --- Finding 5 and 6: the owner's zone -------------------------------------------------------------------------------


def _zone(account_id: int, name: str) -> None:
    with SessionLocal() as db:
        row = db.get(Account, account_id)
        assert row is not None
        row.preferences = {**(row.preferences or {}), "time_zone": name}
        db.commit()


def test_time19_ec19_today_can_be_the_last_day_and_the_link_ends_at_midnight_in_the_owners_zone(
        client: TestClient, account: Account) -> None:
    _zone(account.id, "Asia/Tokyo")
    tokyo = ZoneInfo("Asia/Tokyo")
    today = datetime.now(UTC).astimezone(tokyo).date()
    familie = make_calendar(client, "Familie")
    link = link_on(client, familie, today.isoformat())
    assert link["expires_on"] == today.isoformat()
    with SessionLocal() as db:
        row = db.scalar(select(GuestLink))
        assert row is not None and row.expires_at is not None
        end = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=tokyo).astimezone(UTC)
        assert row.expires_at == end
        assert end.hour == 15  # midnight in Tokyo is 15:00 UTC
        assert shares._holds(row, end - timedelta(seconds=1)) and not shares._holds(row, end)
    yesterday = (today - timedelta(days=1)).isoformat()
    assert client.put(f"/api/calendars/{familie}/guest-link", json={"expires_on": yesterday}).status_code == 422


def test_time19_ec19_a_whole_day_is_coming_while_it_is_today_in_the_owners_zone(client: TestClient,
                                                                               account: Account) -> None:
    familie = make_calendar(client, "Familie")
    stored(familie, vevent("fest", "20261126", "20261127", "Stadtfest").replace(
        "DTSTART:20261126", "DTSTART;VALUE=DATE:20261126").replace("DTEND:20261127", "DTEND;VALUE=DATE:20261127"))
    with SessionLocal() as db:
        calendar = db.get(Calendar, familie)
        assert calendar is not None
        # 26 November, 20:00 in New York, already 27 November in UTC: still today there.
        evening = datetime(2026, 11, 27, 1, 0, tzinfo=UTC)
        york = guest.upcoming(db, calendar, evening, ZoneInfo("America/New_York"))
        assert [entry["title"] for entry in york] == ["Stadtfest"]
        assert york[0] == {"all_day": True, "start": "2026-11-26", "end": "2026-11-26", "title": "Stadtfest",
                           "location": ""}
        # 27 November, 08:00 in Tokyo, still 26 November in UTC: gone there.
        morning = datetime(2026, 11, 26, 23, 0, tzinfo=UTC)
        assert guest.upcoming(db, calendar, morning, ZoneInfo("Asia/Tokyo")) == []
        assert [entry["title"] for entry in guest.upcoming(db, calendar, morning, UTC)] == ["Stadtfest"]


# --- Finding 7: the subscription -----------------------------------------------------------------------------------


def test_time19_ac17_the_subscription_is_built_anew_after_every_change_and_carries_no_attachments_or_people(
        client: TestClient, account: Account) -> None:
    year = datetime.now(UTC).year + 2
    familie = make_calendar(client, "Familie")
    stored(familie, vevent("abend", f"{year}0303T170000Z", f"{year}0303T180000Z", "Elternabend",
                           extra="ATTACH:https://files.example.com/protokoll.pdf\n"
                                 "ATTACH;FMTTYPE=image/png;ENCODING=BASE64;VALUE=BINARY:iVBORw0KGgo=\n"
                                 "ATTENDEE;CN=Anna:mailto:anna@example.com\nORGANIZER:mailto:tester@example.com"))
    token = token_of(link_on(client, familie))

    def feed() -> tuple[str, str]:
        with guest_client() as browser:
            answer = browser.get(f"/abo/{token}.ics")
        assert answer.status_code == 200
        return answer.text, answer.headers["etag"]

    first, first_tag = feed()
    for hidden in ("ATTACH", "protokoll.pdf", "iVBORw0KGgo", "ATTENDEE", "anna@example.com", "ORGANIZER"):
        assert hidden not in first
    made = make_event(client, "Laternenumzug", f"{year}-11-11T17:00", f"{year}-11-11T18:00", calendar_id=familie)
    second, second_tag = feed()
    assert second_tag != first_tag and "Laternenumzug" in second
    client.patch(f"/api/events/{made['id']}", json={"title": "Martinsumzug"})
    third, third_tag = feed()
    assert third_tag != second_tag and "Martinsumzug" in third and "Laternenumzug" not in third
    client.delete(f"/api/events/{made['id']}")
    fourth, _tag = feed()
    assert "Martinsumzug" not in fourth
    client.patch(f"/api/calendars/{familie}", json={"name": "Familie Berg"})
    fifth, _tag = feed()
    assert str(icalendar.Calendar.from_ical(fifth)["X-WR-CALNAME"]) == "Familie Berg"


# --- Finding 8: the subscription and the brake -------------------------------------------------------------------


def test_time19_ec1_a_subscription_that_cannot_be_built_now_is_503_not_gone(client: TestClient, account: Account,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    token = token_of(link_on(client, familie))
    monkeypatch.setattr(guest, "feed", _broken)
    with guest_client() as browser:
        answer = browser.get(f"/abo/{token}.ics")
    assert answer.status_code == 503 and answer.headers["retry-after"] and answer.content == b""


# --- Finding 9: a blocked owner ------------------------------------------------------------------------------------


def test_time19_ec17_a_blocked_owners_calendar_stays_writable_for_whoever_may_edit_it(client: TestClient,
                                                                                    account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        garten = make_calendar(browser, "Garten")
        put_share(browser, garten, account.id, RIGHT_EDIT)
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        accounts.block(db, row)
    made = make_event(client, "Rasen mähen", "2026-11-28T10:00", "2026-11-28T11:00", calendar_id=garten)
    assert client.patch(f"/api/events/{made['id']}", json={"title": "Rasen vertikutieren"}).status_code == 200
    password = device_for(account)
    uid = uuid.uuid4().hex
    body = vevent(uid, "20261129T090000Z", "20261129T100000Z", "Vom iPhone")
    assert raw("PUT", f"{calendar_url(account, garten)}{uid}.ics", account, password, body).status_code == 201
    assert sorted(row["title"] for row in window(client)) == ["Rasen vertikutieren", "Vom iPhone"]


def test_time19_a_guest_link_and_its_page_say_nothing_to_a_client_without_one(client: TestClient,
                                                                              account: Account) -> None:
    with TestClient(app) as stranger:
        gone = stranger.get("/api/guest/" + "x" * 32)
    assert gone.status_code == 404 and gone.json()["detail"]["code"] == "link_invalid"
    assert gone.headers["cache-control"] == "no-store"
