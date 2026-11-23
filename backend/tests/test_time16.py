"""TIME-16 Google: the operator's set-up, connecting with "Continue to Google", events over CalDAV with Google's
token, the read-only way over the secret address, the switch, expired grants.

Google is a fake behind ``httpx.MockTransport`` (token, user info, Calendar API); its CalDAV side is nextime's own
calendar account in a thread, which takes the fake's token in place of a device password (``remote_helpers``).
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, Source
from app.services import google, settings_service, subscriptions, sync

from .calendar_helpers import calendars_of, code, make_event, window
from .remote_helpers import (
    SWITCHES,
    FakeGoogle,
    Remote,
    dav_server,  # noqa: F401 - the fixture
    event_text,
    mirrored,
    quiet_world,  # noqa: F401 - the fixture
    remote_account,
    round_now,
    source_row,
)


@pytest.fixture
def remote(client: TestClient) -> Iterator[Remote]:
    yield from remote_account()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, dav_server: str, remote: Remote) -> FakeGoogle:
    found = FakeGoogle()
    monkeypatch.setattr(google, "transport", httpx.MockTransport(found.handler))
    monkeypatch.setattr(google, "CALDAV_URL", dav_server + f"/dav/calendars/{remote.account.name}/{{id}}/")
    SWITCHES.bearer[found.access] = (remote.account.name, remote.password)
    return found


def _set_up(client: TestClient, fake: FakeGoogle) -> None:
    answer = client.put("/api/server/google", json={"client_id": fake.client_id, "client_secret": fake.client_secret})
    assert answer.status_code == 200, answer.text


def _connect(client: TestClient, fake: FakeGoogle, *, code_value: str = "good-code", source_id: int | None = None,
             error: str = "") -> str:
    started = client.post("/api/sources/google/start", json={"source_id": source_id})
    assert started.status_code == 200, started.text
    query = parse_qs(urlsplit(started.json()["url"]).query)
    params = {"state": query["state"][0], "code": code_value}
    if error:
        params = {"state": query["state"][0], "error": error}
    back = client.get("/api/google/callback", params=params, follow_redirects=False)
    assert back.status_code == 303
    return back.headers["location"]


def _google_calendar(remote: Remote, fake: FakeGoogle, name: str, role: str = "owner") -> int:
    calendar = remote.calendar(name)
    fake.calendars.append((remote.dav_name(calendar), name, "#7986cb", role))
    return calendar


def _connected(client: TestClient, fake: FakeGoogle) -> int:
    _set_up(client, fake)
    place = _connect(client, fake)
    source = int(parse_qs(urlsplit(place).query)["choose"][0])
    choice = client.get(f"/api/sources/{source}/calendars").json()
    client.put(f"/api/sources/{source}/calendars", json={"urls": [item["url"] for item in choice["calendars"]]})
    return source


# --- The operator (AC-1, AC-6, AC-7, EC-2, EC-3, EC-4, EC-13) --------------------------------------------------------


def test_time16_ac1_ac7_the_values_are_checked_at_once_and_google_says_ready_with_a_count(
        client: TestClient, account: Account, fake: FakeGoogle) -> None:
    before = client.get("/api/server/google").json()
    assert before["ready"] is False and before["redirect_uri"] == "http://testserver/api/google/callback"
    _set_up(client, fake)
    view = client.get("/api/server/google").json()
    assert view["ready"] is True and view["members"] == 0 and view["secret_set"] is True
    assert "client_secret" not in view and fake.client_secret not in client.get("/api/settings").text
    _connect(client, fake)
    assert client.get("/api/server/google").json()["members"] == 1
    # How many, never who or what.
    assert fake.email not in client.get("/api/server/google").text


def test_time16_ec3_blanks_go_and_the_wrong_value_is_named(client: TestClient, account: Account,
                                                           fake: FakeGoogle) -> None:
    wrong_id = client.put("/api/server/google", json={"client_id": "999-x.apps.googleusercontent.com",
                                                      "client_secret": fake.client_secret})
    assert code(wrong_id) == "google_client_id_wrong" and wrong_id.json()["detail"]["field"] == "client_id"
    half = client.put("/api/server/google", json={"client_id": "nur-ein-teil", "client_secret": "x"})
    assert code(half) == "google_client_id_wrong"
    wrong_secret = client.put("/api/server/google", json={"client_id": fake.client_id, "client_secret": "falsch"})
    assert code(wrong_secret) == "google_secret_wrong" and wrong_secret.json()["detail"]["field"] == "client_secret"
    assert client.get("/api/server/google").json()["ready"] is False
    blanks = client.put("/api/server/google", json={"client_id": f"  {fake.client_id}\n",
                                                    "client_secret": f" {fake.client_secret} "})
    assert blanks.status_code == 200 and blanks.json()["ready"] is True


def test_time16_ec2_changing_the_values_warns_first_and_then_everybody_reconnects(
        client: TestClient, account: Account, fake: FakeGoogle, remote: Remote) -> None:
    _google_calendar(remote, fake, "Elternbeirat")
    source = _connected(client, fake)
    fake.client_secret = "neues-geheimnis"
    refused = client.put("/api/server/google", json={"client_id": fake.client_id, "client_secret": "neues-geheimnis"})
    assert refused.status_code == 409 and code(refused) == "google_change_needs_confirm"
    assert refused.json()["detail"]["count"] == 1
    done = client.put("/api/server/google", json={"client_id": fake.client_id, "client_secret": "neues-geheimnis",
                                                  "confirm": True})
    assert done.status_code == 200
    assert source_row(client, source)["state"] == "expired"


def test_time16_ec4_ec13_the_address_from_outside_and_a_warning_without_https(
        client: TestClient, account: Account, fake: FakeGoogle) -> None:
    assert client.get("/api/server/google").json()["https"] is False
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://time.example.com"})
    view = client.get("/api/server/google").json()
    assert view["redirect_uri"] == "https://time.example.com/api/google/callback" and view["https"] is True
    _set_up(client, fake)
    assert client.get("/api/server/google").json()["redirect_changed"] is False
    # Restored on another machine with another address: the address at Google has to change too.
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://neu.example.com"})
    assert client.get("/api/server/google").json()["redirect_changed"] is True


def test_time16_the_set_up_is_for_the_operator_only(client: TestClient, account: Account) -> None:
    from .conftest import make_account, new_client

    with new_client(make_account("anna")) as browser:
        assert browser.get("/api/server/google").status_code == 403
        assert browser.put("/api/server/google", json={"client_id": "x"}).status_code == 403


# --- Members (AC-2, AC-3, AC-5, AC-8, EC-1, EC-5 to EC-9) ------------------------------------------------------------


def test_time16_ac2_continue_to_google_and_back_to_the_choice(client: TestClient, account: Account, fake: FakeGoogle,
                                                              remote: Remote) -> None:
    _google_calendar(remote, fake, "Elternbeirat")
    _google_calendar(remote, fake, "Feiertage", role="reader")
    _set_up(client, fake)
    started = client.post("/api/sources/google/start", json={}).json()["url"]
    query = parse_qs(urlsplit(started).query)
    assert started.startswith(google.AUTH_URL)
    assert query["scope"] == [google.SCOPES] and query["access_type"] == ["offline"]
    assert query["redirect_uri"] == ["http://testserver/api/google/callback"]
    place = _connect(client, fake)
    source = int(parse_qs(urlsplit(place).query)["choose"][0])
    choice = client.get(f"/api/sources/{source}/calendars").json()
    assert {item["name"]: item["read_only"] for item in choice["calendars"]} == {"Elternbeirat": False,
                                                                                 "Feiertage": True}
    assert source_row(client, source)["label"] == fake.email


def test_time16_ac3_events_go_both_ways_over_caldav_with_googles_token(client: TestClient, account: Account,
                                                                       fake: FakeGoogle, remote: Remote) -> None:
    beirat = _google_calendar(remote, fake, "Elternbeirat")
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend", extra="TRANSP:TRANSPARENT\r\n"))
    source = _connected(client, fake)
    round_now(source)
    rows = window(client)
    assert [(row["title"], row["busy"]) for row in rows] == [("Elternabend", False)]
    groups = {row["name"]: row["group"] for row in calendars_of(client)["calendars"]}
    assert groups["Elternbeirat"] == "google"
    make_event(client, "Basar", calendar_id=mirrored(source)[0].id)
    sync.run_due()
    listing = remote.raw("PROPFIND", remote.calendar_path(beirat), Depth="1").text
    assert listing.count(".ics") == 2
    assert source_row(client, source)["state"] == "ok"


def test_time16_ac4_ec10_without_set_up_the_secret_address_reads_and_others_are_refused(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "https://calendar.google.com/calendar/ical/abc%40group.calendar.google.com/private-0a1b2c/basic.ics"
    body = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:Elternbeirat\r\nBEGIN:VEVENT\r\nUID:x@example.com\r\n"
            "DTSTAMP:20261101T000000Z\r\nDTSTART;VALUE=DATE:20261126\r\nSUMMARY:Basar\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(
        lambda request: httpx.Response(200, text=body) if str(request.url) == secret else httpx.Response(404)))
    assert client.get("/api/sources").json()["google"]["ready"] is False
    other = client.post("/api/sources/subscriptions", json={"url": "https://example.com/x.ics", "google": True})
    assert other.status_code == 422 and code(other) == "not_google_address"
    gone = client.post("/api/sources/subscriptions", json={
        "url": "https://calendar.google.com/calendar/ical/x/private-0/basic.ics", "google": True})
    assert gone.status_code == 422
    made = client.post("/api/sources/subscriptions", json={"url": secret, "google": True})
    assert made.status_code == 201, made.text
    row = made.json()["sources"][0]
    assert row["google"] is True and row["kind"] == "subscription"
    assert [entry["title"] for entry in window(client)] == ["Basar"]
    assert all(entry["writable"] is False for entry in window(client))


def test_time16_ac9_the_read_only_way_comes_once_an_hour(client: TestClient, account: Account) -> None:
    assert sync.INTERVALS["subscription"] == 3600 and sync.INTERVALS["google"] == 60


def test_time16_ac5_ec5_switched_off_no_way_connected_ones_rest_and_go_on_after(
        client: TestClient, account: Account, fake: FakeGoogle, remote: Remote) -> None:
    beirat = _google_calendar(remote, fake, "Elternbeirat")
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend"))
    source = _connected(client, fake)
    round_now(source)
    assert client.put("/api/settings", json={"google_enabled": False}).status_code == 200
    assert client.get("/api/sources").json()["google"]["ready"] is False
    assert code(client.post("/api/sources/google/start", json={})) == "google_not_ready"
    round_now(source)
    assert source_row(client, source)["state"] == "paused"
    event = window(client)[0]
    assert event["title"] == "Elternabend" and event["writable"] is False
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend verschoben"))
    assert client.put("/api/settings", json={"google_enabled": True}).status_code == 200
    round_now(source)
    assert source_row(client, source)["state"] == "ok"
    assert window(client)[0]["title"] == "Elternabend verschoben"


def test_time16_ac8_ec1_a_withdrawn_grant_expires_and_reconnecting_keeps_the_settings(
        client: TestClient, account: Account, fake: FakeGoogle, remote: Remote) -> None:
    beirat = _google_calendar(remote, fake, "Elternbeirat")
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend"))
    source = _connected(client, fake)
    round_now(source)
    mine = mirrored(source)[0].id
    client.patch(f"/api/calendars/{mine}", json={"color": "green"})
    google.forget()
    fake.refresh_valid = False
    round_now(source)
    row = source_row(client, source)
    assert row["state"] == "expired" and row["since"]
    assert window(client)[0]["title"] == "Elternabend"
    fake.refresh_valid = True
    place = _connect(client, fake, source_id=source)
    assert "google=reconnected" in place
    round_now(source)
    assert source_row(client, source)["state"] == "ok"
    assert {row["name"]: row["color"] for row in calendars_of(client)["calendars"]}["Elternbeirat"] == "green"


def test_time16_ec6_cancelled_at_google_or_no_calendar_access_connects_nothing(
        client: TestClient, account: Account, fake: FakeGoogle) -> None:
    _set_up(client, fake)
    assert _connect(client, fake, error="access_denied").endswith("?google=denied")
    fake.scope = "openid email"
    assert _connect(client, fake).endswith("?google=denied")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 0


def test_time16_ec7_the_same_google_account_again_makes_no_second_connection(
        client: TestClient, account: Account, fake: FakeGoogle) -> None:
    _set_up(client, fake)
    first = _connect(client, fake)
    again = _connect(client, fake)
    source = parse_qs(urlsplit(first).query)["choose"][0]
    assert "google=exists" in again and f"choose={source}" in again
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 1


def test_time16_ec8_google_away_or_slowing_down_keeps_the_copy(client: TestClient, account: Account,
                                                               fake: FakeGoogle, remote: Remote) -> None:
    beirat = _google_calendar(remote, fake, "Elternbeirat")
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend"))
    source = _connected(client, fake)
    round_now(source)
    google.forget()
    fake.down = True
    round_now(source)
    row = source_row(client, source)
    assert row["state"] == "unreachable"
    assert window(client)[0]["title"] == "Elternabend"
    event = window(client)[0]
    client.patch(f"/api/events/{event['id']}", json={"title": "Elternabend 20 Uhr"})
    sync.run_due()
    assert window(client)[0]["waiting"] is True
    fake.down = False
    round_now(source)
    assert window(client)[0]["waiting"] is False
    assert "Elternabend 20 Uhr" in remote.get(beirat, "a.ics").text


def test_time16_ec9_fields_of_google_stay_when_changed_in_nextime(client: TestClient, account: Account,
                                                                  fake: FakeGoogle, remote: Remote) -> None:
    beirat = _google_calendar(remote, fake, "Elternbeirat")
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend", extra=(
        "COLOR:tomato\r\nATTACH;FILENAME=Protokoll.pdf:https://drive.google.com/file/d/abc\r\n"
        "X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij\r\n"
        "DESCRIPTION:Beitreten: https://meet.google.com/abc-defg-hij\r\n")))
    source = _connected(client, fake)
    round_now(source)
    event = window(client)[0]
    assert "meet.google.com/abc-defg-hij" in client.get(f"/api/events/{event['id']}").json()["notes"]
    client.patch(f"/api/events/{event['id']}", json={"title": "Elternabend Klasse 2"})
    sync.run_due()
    text = remote.get(beirat, "a.ics").text
    for kept in ("COLOR:tomato", "ATTACH;FILENAME=Protokoll.pdf", "X-GOOGLE-CONFERENCE", "SUMMARY:Elternabend Klasse 2"):
        assert kept in text, kept


def test_time16_ec12_secrets_and_grants_never_show(client: TestClient, account: Account, fake: FakeGoogle,
                                                   remote: Remote) -> None:
    from app.services import logs

    _google_calendar(remote, fake, "Elternbeirat")
    source = _connected(client, fake)
    round_now(source)
    for path in ("/api/sources", "/api/server/google", "/api/settings", f"/api/sources/{source}/calendars"):
        text = client.get(path).text
        for secret in (fake.client_secret, "refresh-1", fake.access):
            assert secret not in text, (path, secret)
    log = logs.log_file().read_text(encoding="utf-8") if logs.log_file().exists() else ""
    for secret in (fake.client_secret, "refresh-1", fake.access):
        assert secret not in log
    with SessionLocal() as db:
        row = db.get(Source, source)
        assert row is not None and "refresh-1" not in row.secret_enc


# --- After the review (2026-11-22) -----------------------------------------------------------------------------------


def test_time16_ac2_the_way_back_belongs_to_the_account_that_started_and_holds_once(
        client: TestClient, account: Account, fake: FakeGoogle) -> None:
    from .conftest import make_account, new_client

    _set_up(client, fake)
    started = client.post("/api/sources/google/start", json={}).json()["url"]
    state = parse_qs(urlsplit(started).query)["state"][0]
    with new_client(make_account("anna")) as other:
        back = other.get("/api/google/callback", params={"state": state, "code": "good-code"}, follow_redirects=False)
        assert back.headers["location"].endswith("?google=expired")
    # Used up by the try of the other account: it no longer opens for the one who started either.
    again = client.get("/api/google/callback", params={"state": state, "code": "good-code"}, follow_redirects=False)
    assert again.headers["location"].endswith("?google=expired")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 0


def test_time16_ac9_the_read_only_way_is_fetched_once_an_hour(client: TestClient, account: Account,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    secret = "https://calendar.google.com/calendar/ical/abc%40group.calendar.google.com/private-0a1b2c/basic.ics"
    body = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x@example.com\r\nDTSTAMP:20261101T000000Z\r\n"
            "DTSTART;VALUE=DATE:20261126\r\nSUMMARY:Basar\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, text=body.replace("Basar", f"Basar {len(asked)}"))

    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(answer))
    assert client.post("/api/sources/subscriptions", json={"url": secret, "google": True}).status_code == 201
    now = sync.clock()
    monkeypatch.setattr(sync, "clock", lambda: now + timedelta(minutes=59))
    sync.run_due()
    assert len(asked) == 1
    monkeypatch.setattr(sync, "clock", lambda: now + timedelta(minutes=61))
    sync.run_due()
    assert len(asked) == 2 and [row["title"] for row in window(client)] == ["Basar 2"]


def test_time16_ac8_ec1_a_token_withdrawn_early_is_renewed_once_before_access_expires(
        client: TestClient, account: Account, fake: FakeGoogle, remote: Remote) -> None:
    beirat = _google_calendar(remote, fake, "Elternbeirat")
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend"))
    source = _connected(client, fake)
    round_now(source)
    # Google withdraws the token in use; the stored grant still gives a new one.
    SWITCHES.bearer.pop(fake.access)
    fake.access = "google-access-2"
    SWITCHES.bearer[fake.access] = (remote.account.name, remote.password)
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend neu"))
    round_now(source)
    assert source_row(client, source)["state"] == "ok"
    assert window(client)[0]["title"] == "Elternabend neu"
    # Without a grant that works: "Access expired".
    SWITCHES.bearer.clear()
    fake.refresh_valid = False
    round_now(source)
    assert source_row(client, source)["state"] == "expired"


def test_time16_ec8_google_throttling_over_caldav_keeps_the_change_waiting(
        client: TestClient, account: Account, fake: FakeGoogle, remote: Remote) -> None:
    beirat = _google_calendar(remote, fake, "Elternbeirat")
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend"))
    source = _connected(client, fake)
    round_now(source)
    SWITCHES.writes_answer = (403, b'{"error": {"code": 403, "errors": [{"reason": "rateLimitExceeded"}]}}')
    event = window(client)[0]
    client.patch(f"/api/events/{event['id']}", json={"title": "Elternabend 20 Uhr"})
    sync.run_due()
    assert window(client)[0]["waiting"] is True and client.get("/api/sources").json()["notices"] == []
    SWITCHES.writes_answer = None
    round_now(source)
    assert "Elternabend 20 Uhr" in remote.get(beirat, "a.ics").text
