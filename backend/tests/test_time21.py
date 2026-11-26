"""TIME-21 Mitteilungen bei Änderungen: a switch per shared calendar, changes of others bundled after two quiet
minutes, sent to every browser of the member by Web Push (encrypted for it; the test plays the browser and reads
what arrives), in the member's language, zone and clock. With the negative side: nothing of one's own changes, nothing
after a share ended, nothing for a signed-out browser or a blocked account, no title from a "busy only" calendar, and
nobody switches or reads another member's notifications."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    RIGHT_BUSY,
    RIGHT_EDIT,
    RIGHT_READ,
    Account,
    CalendarShare,
    NotifyChange,
    NotifyOutbox,
    WebPushSubscription,
)
from app.security import SESSION_COOKIE
from app.services import accounts, internet, notify, webpush

from .calendar_helpers import code, make_calendar, make_event
from .conftest import make_account, new_client
from .test_time19 import from_source, put_share, source_calendar, vevent
from .test_time20 import calendar_url, device_for, raw

START = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


class FakeBrowser:
    """A browser that allowed notifications: its own keys, as the push service would hand them on."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.endpoint = f"https://push.example.com/send/{uuid.uuid4().hex}"
        self.public = self.key.public_key().public_bytes(serialization.Encoding.X962,
                                                         serialization.PublicFormat.UncompressedPoint)

    def sign_up(self, client: TestClient, installed: bool = False) -> Any:
        return client.put("/api/notify/webpush", json={
            "endpoint": self.endpoint, "keys": {"p256dh": b64url(self.public), "auth": b64url(self.auth)},
            "installed": installed})

    def read(self, body: bytes) -> dict[str, Any]:
        """RFC 8291 from the browser's side: what the push service could not read."""
        salt, size, length = body[:16], int.from_bytes(body[16:20], "big"), body[20]
        sender_bytes = body[21:21 + length]
        sealed = body[21 + length:]
        assert size == webpush.RECORD_SIZE
        sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_bytes)
        shared = self.key.exchange(ec.ECDH(), sender)
        ikm = _hmac(_hmac(self.auth, shared), b"WebPush: info\x00" + self.public + sender_bytes + b"\x01")
        prk = _hmac(salt, ikm)
        cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
        nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
        plain = AESGCM(cek).decrypt(nonce, sealed, None)
        assert plain.endswith(b"\x02")
        return json.loads(plain[:-1].decode("utf-8"))


class PushService:
    """Stands in for every push service: what arrives where, decrypted; or an answer of its choosing."""

    def __init__(self) -> None:
        self.browsers: dict[str, FakeBrowser] = {}
        self.arrived: list[tuple[FakeBrowser, dict[str, Any], dict[str, str]]] = []
        self.answer: int | Exception = 201

    def browser(self, client: TestClient, installed: bool = False) -> FakeBrowser:
        made = FakeBrowser()
        assert made.sign_up(client, installed).status_code == 204
        self.browsers[made.endpoint] = made
        return made

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer < 300:
            target = self.browsers[url]
            self.arrived.append((target, target.read(body), headers))
        return self.answer

    def messages(self) -> list[dict[str, Any]]:
        return [message for _browser, message, _headers in self.arrived]


@pytest.fixture
def push(monkeypatch: pytest.MonkeyPatch) -> PushService:
    service = PushService()
    monkeypatch.setattr(webpush, "POST", service.post)
    return service


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def later(self, seconds: float) -> tuple[int, int]:
        """Time goes on, and the ticker looks."""
        self.now += timedelta(seconds=seconds)
        return notify.tick(self.now)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    made = Clock()
    monkeypatch.setattr(notify, "clock", made)
    return made


def prefs(account_id: int, language: str = "de", zone: str = "Europe/Berlin", clock_kind: str = "auto") -> None:
    with SessionLocal() as db:
        row = db.get(Account, account_id)
        assert row is not None
        row.language = language
        row.preferences = {**(row.preferences or {}), "time_zone": zone, "clock": clock_kind}
        db.commit()


def switch(client: TestClient, calendar_id: int, on: bool = True) -> Any:
    return client.put(f"/api/calendars/{calendar_id}/notify", json={"on": on})


@pytest.fixture
def family(client: TestClient, account: Account) -> dict[str, Any]:
    """Tester's calendar "Familie", shared with Anna (edit) and Ben (read); Tester switched notifications on."""
    prefs(account.id)
    anna, ben = make_account("anna"), make_account("ben")
    prefs(anna.id)
    prefs(ben.id)
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    put_share(client, familie, ben.id, RIGHT_READ)
    assert switch(client, familie).status_code == 200
    return {"calendar": familie, "anna": anna, "ben": ben}


# --- AC-1, AC-5, AC-6: the settings of a calendar -----------------------------------------------------------------


def test_time21_ac1_the_switch_is_off_from_the_start_and_only_for_calendars_others_see(client: TestClient,
                                                                                    account: Account) -> None:
    anna, ben = make_account("anna"), make_account("ben")
    familie = make_calendar(client, "Familie")
    alone = client.get(f"/api/calendars/{familie}/notify").json()
    assert alone["on"] is False and alone["shared"] is False
    refused = switch(client, familie)
    assert refused.status_code == 409 and code(refused) == "calendar_not_shared"
    put_share(client, familie, anna.id, RIGHT_READ)
    assert client.get(f"/api/calendars/{familie}/notify").json() | {} and \
        client.get(f"/api/calendars/{familie}/notify").json()["on"] is False
    assert switch(client, familie).json()["on"] is True
    with new_client(anna) as browser:
        assert browser.get(f"/api/calendars/{familie}/notify").json()["on"] is False
        assert switch(browser, familie).json()["on"] is True
    assert client.get(f"/api/calendars/{familie}/notify").json()["on"] is True
    # Somebody the calendar is not shared with neither reads nor switches it.
    with new_client(ben) as browser:
        assert browser.get(f"/api/calendars/{familie}/notify").status_code == 404
        assert switch(browser, familie).status_code == 404
        assert switch(browser, familie, False).status_code == 404
    assert client.get(f"/api/calendars/{familie}/notify").json()["on"] is True


def test_time21_ac5_the_settings_list_the_members_browsers_and_whether_this_one_is_among_them(
        client: TestClient, account: Account, push: PushService) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_READ)
    before = client.get(f"/api/calendars/{familie}/notify").json()["devices"]
    assert before == {"browsers": [], "this_browser": False}
    push.browser(client)
    with new_client(account) as phone:
        phone.headers["User-Agent"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) Safari/604.1"
        push.browser(phone, installed=True)
    devices = client.get(f"/api/calendars/{familie}/notify").json()["devices"]
    assert devices["this_browser"] is True
    assert [(row["installed"], row["this"]) for row in devices["browsers"]] == [(False, True), (True, False)]
    assert devices["browsers"][1]["system"] == "iOS"
    with new_client(anna) as browser:
        assert browser.get(f"/api/calendars/{familie}/notify").json()["devices"] == {"browsers": [],
                                                                                    "this_browser": False}


def test_time21_ac6_the_preview_shows_a_bundle_and_one_of_a_busy_only_calendar(client: TestClient,
                                                                                account: Account) -> None:
    prefs(account.id)
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_READ)
    preview = client.get(f"/api/calendars/{familie}/notify").json()["preview"]
    assert preview["bundle"] == {
        "title": "Familie",
        "text": "Anna hat „Elternsprechtag“ am Di, 1.12. um 17:00 eingetragen und „Schwimmen“ auf 11:30 verschoben."}
    assert preview["busy"] == {"title": "Arbeit (von Anna, nur belegt)", "text": "Neuer belegter Zeitraum am Do, 3.12."}


def test_time21_the_browser_signs_up_only_with_a_push_services_address_and_usable_keys(client: TestClient,
                                                                                     account: Account) -> None:
    made = FakeBrowser()
    good = {"p256dh": b64url(made.public), "auth": b64url(made.auth)}
    for endpoint in ("http://push.example.com/x", "https://localhost/x", "https://user:pw@push.example.com/x",
                     "https://printer.local/x"):
        answer = client.put("/api/notify/webpush", json={"endpoint": endpoint, "keys": good})
        assert answer.status_code == 422 and code(answer) == "webpush_endpoint"
    bad = client.put("/api/notify/webpush", json={"endpoint": made.endpoint, "keys": {"p256dh": "AAAA",
                                                                                       "auth": good["auth"]}})
    assert bad.status_code == 422 and code(bad) == "webpush_keys"
    assert client.get("/api/notify/webpush").json()["subscribed"] is False
    with TestClient(app) as stranger:
        assert stranger.put("/api/notify/webpush", json={"endpoint": made.endpoint, "keys": good},
                            headers={"X-Nextime-Client": "tab-stranger"}).status_code == 401


# --- AC-2, AC-3, AC-4, AC-7: what comes ----------------------------------------------------------------------------


def test_time21_ac2_ac4_a_change_of_another_comes_with_the_calendar_as_heading_and_opens_the_event(
        client: TestClient, family: dict[str, Any], push: PushService, clock: Clock) -> None:
    browser = push.browser(client)
    with new_client(family["anna"]) as anna:
        made = make_event(anna, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    assert clock.later(60) == (0, 0)
    assert clock.later(61) == (1, 1)
    assert push.messages() == [{"title": "Familie", "body": "Anna hat „Elternabend“ am Do, 3.12. um 18:00 eingetragen.",
                                "url": f"/day?day=2026-12-03&event={made['id']}",
                                "tag": f"calendar-{family['calendar']}"}]
    headers = push.arrived[0][2]
    assert headers["Content-Encoding"] == "aes128gcm" and headers["TTL"] == str(webpush.TTL_SECONDS)
    # VAPID (RFC 8292): the token is signed with the key the header names, for the push service's origin.
    token = headers["Authorization"].split("t=", 1)[1].split(",", 1)[0]
    key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), webpush.unb64url(headers["Authorization"].split("k=", 1)[1].strip()))
    claims = jwt.decode(token, key, algorithms=["ES256"], audience="https://push.example.com")
    assert claims["sub"].startswith(("https://", "mailto:"))
    with SessionLocal() as db:
        assert webpush.b64url(webpush.public_bytes(webpush.server_key(db))) == headers["Authorization"].split("k=")[1]
    assert push.arrived[0][0] is browser
    assert clock.later(300) == (0, 0)


def test_time21_ac2_own_changes_tell_nothing(client: TestClient, family: dict[str, Any], push: PushService,
                                             clock: Clock) -> None:
    push.browser(client)
    made = make_event(client, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    client.patch(f"/api/events/{made['id']}", json={"title": "Elternabend neu"})
    client.delete(f"/api/events/{made['id']}")
    clock.later(600)
    assert push.messages() == []


def test_time21_ac3_changes_close_together_come_as_one_after_two_quiet_minutes(client: TestClient,
                                                                              family: dict[str, Any],
                                                                              push: PushService,
                                                                              clock: Clock) -> None:
    push.browser(client)
    swimming = make_event(client, "Schwimmen", "2026-12-01T10:00", "2026-12-01T11:00",
                          calendar_id=family["calendar"])
    with new_client(family["anna"]) as anna:
        make_event(anna, "Elternsprechtag", "2026-12-01T17:00", "2026-12-01T18:00", calendar_id=family["calendar"])
        clock.later(90)
        assert push.messages() == []
        anna.patch(f"/api/events/{swimming['id']}", json={"start": "2026-12-01T11:30", "end": "2026-12-01T12:30",
                                                          "time_zone": "Europe/Berlin"})
    clock.later(100)
    assert push.messages() == []
    clock.later(30)
    assert [message["body"] for message in push.messages()] == [
        "Anna hat „Elternsprechtag“ am Di, 1.12. um 17:00 eingetragen und „Schwimmen“ auf 11:30 verschoben."]
    assert push.messages()[0]["url"] == "/day?day=2026-12-01"


def test_time21_ac7_own_changes_from_a_phone_tell_nothing_those_of_others_from_their_phones_do(
        client: TestClient, account: Account, family: dict[str, Any], push: PushService, clock: Clock) -> None:
    push.browser(client)
    with new_client(family["anna"]) as anna:
        assert switch(anna, family["calendar"]).status_code == 200
        push.browser(anna)
    mine = device_for(account)
    annas = device_for(family["anna"])
    body = vevent("vom-iphone", "20261203T170000Z", "20261203T180000Z", "Vom iPhone")
    assert raw("PUT", calendar_url(account, family["calendar"]) + "vom-iphone.ics", account, mine,
               body).status_code == 201
    clock.later(200)
    assert [(target.endpoint in push.browsers, message["body"]) for target, message, _ in push.arrived] == [
        (True, "Tester hat „Vom iPhone“ am Do, 3.12. um 18:00 eingetragen.")]
    assert push.arrived[0][0] is not next(iter(push.browsers.values()))
    push.arrived.clear()
    other = vevent("annas-iphone", "20261204T170000Z", "20261204T180000Z", "Von Annas iPhone")
    assert raw("PUT", calendar_url(family["anna"], family["calendar"]) + "annas-iphone.ics", family["anna"], annas,
               other).status_code == 201
    clock.later(200)
    assert [message["body"] for message in push.messages()] == [
        "Anna hat „Von Annas iPhone“ am Fr, 4.12. um 18:00 eingetragen."]


# --- EC-1, EC-5, EC-6, EC-7, EC-12, EC-13 -----------------------------------------------------------------------------


def test_time21_ec1_after_the_share_ends_or_the_member_leaves_nothing_comes(client: TestClient, account: Account,
                                                                            family: dict[str, Any],
                                                                            push: PushService, clock: Clock) -> None:
    calendar = family["calendar"]
    with new_client(family["anna"]) as anna:
        switch(anna, calendar)
        push.browser(anna)
    with new_client(family["ben"]) as ben:
        switch(ben, calendar)
        push.browser(ben)
    make_event(client, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=calendar)
    client.delete(f"/api/calendars/{calendar}/sharing/{family['anna'].id}")
    with new_client(family["ben"]) as ben:
        assert ben.post(f"/api/calendars/{calendar}/leave").status_code == 204
    clock.later(200)
    assert push.messages() == []
    # Queued already, then the share ends: dropped before sending.
    put_share(client, calendar, family["anna"].id, RIGHT_READ)
    with new_client(family["anna"]) as anna:
        assert anna.get(f"/api/calendars/{calendar}/notify").json()["on"] is False
        switch(anna, calendar)
    make_event(client, "Laternenumzug", "2026-12-04T18:00", "2026-12-04T19:00", calendar_id=calendar)
    clock.now += timedelta(seconds=200)
    assert notify.flush_due(clock.now) == 1
    client.delete(f"/api/calendars/{calendar}/sharing/{family['anna'].id}")
    assert notify.send_round(clock.now) == 0
    assert push.messages() == []
    # Gone another way (the share row removed underneath): asked again before sending, the message is dropped.
    put_share(client, calendar, family["anna"].id, RIGHT_READ)
    with new_client(family["anna"]) as anna:
        switch(anna, calendar)
    make_event(client, "Krippenspiel", "2026-12-06T18:00", "2026-12-06T19:00", calendar_id=calendar)
    clock.now += timedelta(seconds=200)
    assert notify.flush_due(clock.now) == 1
    with SessionLocal() as db:
        db.execute(delete(CalendarShare).where(CalendarShare.account_id == family["anna"].id))
        db.commit()
    assert notify.send_round(clock.now) == 0
    assert push.messages() == []


def test_time21_ec5_five_hundred_changes_of_several_people_come_as_one_naming_all_and_counting_the_rest(
        client: TestClient, family: dict[str, Any], push: PushService, clock: Clock) -> None:
    push.browser(client)
    with new_client(family["anna"]) as anna:
        for number in range(300):
            make_event(anna, f"Import {number}", "2026-12-07T08:00", "2026-12-07T09:00",
                       calendar_id=family["calendar"])
    put_share(client, family["calendar"], family["ben"].id, RIGHT_EDIT)
    with new_client(family["ben"]) as ben:
        for number in range(200):
            make_event(ben, f"Ben {number}", "2026-12-08T08:00", "2026-12-08T09:00", calendar_id=family["calendar"])
    clock.later(200)
    messages = push.messages()
    assert len(messages) == 1
    assert messages[0]["body"].startswith("Anna und Ben haben „Import 0“ am Mo, 7.12. um 8:00 eingetragen, „Import 1“")
    assert messages[0]["body"].endswith("und 498 weitere Änderungen.")


def test_time21_ec6_an_event_changed_several_times_is_told_as_it_ends(client: TestClient, family: dict[str, Any],
                                                                      push: PushService, clock: Clock) -> None:
    push.browser(client)
    with new_client(family["anna"]) as anna:
        made = make_event(anna, "Fest", "2026-12-05T15:00", "2026-12-05T16:00", calendar_id=family["calendar"])
        anna.patch(f"/api/events/{made['id']}", json={"title": "Adventsfest"})
        anna.patch(f"/api/events/{made['id']}", json={"start": "2026-12-05T16:00", "end": "2026-12-05T17:00",
                                                      "time_zone": "Europe/Berlin"})
        gone = make_event(anna, "Versehen", "2026-12-06T15:00", "2026-12-06T16:00", calendar_id=family["calendar"])
        anna.delete(f"/api/events/{gone['id']}")
    clock.later(200)
    assert [message["body"] for message in push.messages()] == [
        "Anna hat „Adventsfest“ am Sa, 5.12. um 16:00 eingetragen."]


def test_time21_ec7_code_and_long_titles_come_as_plain_shortened_text(client: TestClient, family: dict[str, Any],
                                                                      push: PushService, clock: Clock) -> None:
    push.browser(client)
    title = "<img src=x onerror=alert(1)>\n" + "lang " * 60
    with new_client(family["anna"]) as anna:
        make_event(anna, title[:200], "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    clock.later(200)
    body = push.messages()[0]["body"]
    assert "\n" not in body
    quoted = body.split("„", 1)[1].split("“", 1)[0]
    assert notify.TITLE_CHARS - 2 <= len(quoted) <= notify.TITLE_CHARS and quoted.endswith("…")
    assert quoted.startswith("<img src=x onerror=alert(1)> lang")


def test_time21_ec12_a_signed_out_browser_and_a_blocked_account_get_nothing(client: TestClient, account: Account,
                                                                            family: dict[str, Any],
                                                                            push: PushService, clock: Clock) -> None:
    with new_client(family["anna"]) as anna:
        switch(anna, family["calendar"])
        stays = push.browser(anna)
        with new_client(family["anna"]) as second:
            push.browser(second)
            assert second.post("/api/auth/logout").status_code in (200, 204)
    make_event(client, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    clock.later(200)
    assert [target for target, _message, _headers in push.arrived] == [stays]
    push.arrived.clear()
    with SessionLocal() as db:
        row = db.get(Account, family["anna"].id)
        assert row is not None
        accounts.block(db, row)
    make_event(client, "Laternenumzug", "2026-12-04T18:00", "2026-12-04T19:00", calendar_id=family["calendar"])
    clock.later(200)
    assert push.arrived == []
    # Its browser stays signed up for its return (konten 1.1.0, E38); nothing reaches it while it is out.
    with SessionLocal() as db:
        assert db.scalar(select(WebPushSubscription).where(WebPushSubscription.account_id == family["anna"].id)) \
            is not None
    # Blocked while its browser is still signed up (the block came another way): the bundle leaves it out all the same.
    with SessionLocal() as db:
        row = db.get(Account, family["anna"].id)
        assert row is not None
        accounts.unblock(db, row)
    with new_client(family["anna"]) as anna:
        push.browser(anna)
    with SessionLocal() as db:
        row = db.get(Account, family["anna"].id)
        assert row is not None
        row.blocked_at = datetime.now(UTC)
        db.commit()
    make_event(client, "Adventsfeier", "2026-12-05T18:00", "2026-12-05T19:00", calendar_id=family["calendar"])
    assert clock.later(200) == (0, 0)
    assert push.arrived == []


def test_time21_ec13_another_zone_and_english_come_in_the_members_own_words_and_clock(
        client: TestClient, account: Account, family: dict[str, Any], push: PushService, clock: Clock) -> None:
    prefs(account.id, "en", "America/New_York")
    push.browser(client)
    swimming = make_event(client, "Schwimmen", "2026-12-01T10:00", "2026-12-01T11:00", calendar_id=family["calendar"])
    with new_client(family["anna"]) as anna:
        make_event(anna, "Parents' evening", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
        anna.patch(f"/api/events/{swimming['id']}", json={"start": "2026-12-01T11:30", "end": "2026-12-01T12:30",
                                                          "time_zone": "Europe/Berlin"})
    clock.later(200)
    assert push.messages()[0]["body"] == ("Anna added “Parents' evening” on Thu, Dec 3 at 12:00 PM and moved "
                                          "“Schwimmen” to 5:30 AM.")
    assert push.messages()[0]["url"].startswith("/day?day=2026-12-01")


# --- EC-2, EC-3: calendars from iCloud and Google ------------------------------------------------------------------


def test_time21_ec2_busy_only_names_no_title_and_free_events_tell_nothing(client: TestClient, account: Account,
                                                                          push: PushService, clock: Clock) -> None:
    prefs(account.id)
    anna = make_account("anna")
    arbeit = source_calendar(anna.id)
    with new_client(anna) as browser:
        put_share(browser, arbeit, account.id, RIGHT_BUSY)
    switch(client, arbeit)
    push.browser(client)
    from_source(arbeit, vevent("team", "20261203T083000Z", "20261203T093000Z", "Teamrunde geheim",
                               location="Raum Nord"))
    from_source(arbeit, vevent("frei", "20261204T083000Z", "20261204T093000Z", "Homeoffice", free=True))
    clock.later(200)
    assert push.messages() == [{"title": "Arbeit (von Anna, nur belegt)", "body": "Neuer belegter Zeitraum am Do, 3.12.",
                                "url": "/day?day=2026-12-03", "tag": f"calendar-{arbeit}"}]
    with SessionLocal() as db:
        assert all("Teamrunde" not in json.dumps(row.payload) for row in db.scalars(select(NotifyOutbox)))
    push.arrived.clear()
    from_source(arbeit, vevent("frei2", "20261205T083000Z", "20261205T093000Z", "Frei", free=True))
    clock.later(200)
    assert push.messages() == []


def test_time21_ec3_a_change_at_the_source_names_the_calendar_and_its_owner_not_a_person(
        client: TestClient, account: Account, push: PushService, clock: Clock) -> None:
    prefs(account.id)
    anna = make_account("anna")
    prefs(anna.id)
    arbeit = source_calendar(anna.id)
    with new_client(anna) as browser:
        put_share(browser, arbeit, account.id, RIGHT_READ)
        switch(browser, arbeit)
        push.browser(browser)
    switch(client, arbeit)
    push.browser(client)
    from_source(arbeit, vevent("team", "20261203T083000Z", "20261203T093000Z", "Teamrunde"))
    clock.later(200)
    assert [(message["title"], message["body"]) for message in push.messages()] == [
        ("Arbeit (von Anna)", "„Teamrunde“ am Do, 3.12. um 9:30 eingetragen.")]


# --- EC-10, EC-11, EC-14: delivery ---------------------------------------------------------------------------------


def test_time21_ec10_an_unreachable_push_service_is_tried_again_and_a_gone_browser_is_dropped(
        client: TestClient, family: dict[str, Any], push: PushService, clock: Clock,
        caplog: pytest.LogCaptureFixture) -> None:
    push.browser(client)
    push.answer = webpush.Failed("unreachable")
    with new_client(family["anna"]) as anna:
        make_event(anna, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    with caplog.at_level(logging.WARNING, logger="nextime.notify"):
        assert clock.later(200) == (1, 0)
    assert "tried again later" in caplog.text
    assert clock.later(30) == (0, 0)
    push.answer = 201
    assert clock.later(40) == (0, 1)
    assert len(push.messages()) == 1
    push.answer = 410
    with new_client(family["anna"]) as anna:
        make_event(anna, "Laternenumzug", "2026-12-04T18:00", "2026-12-04T19:00", calendar_id=family["calendar"])
    clock.later(200)
    assert client.get("/api/notify/webpush").json()["subscribed"] is False
    devices = client.get(f"/api/calendars/{family['calendar']}/notify").json()["devices"]
    assert devices == {"browsers": [], "this_browser": False}


def test_time21_ec10_after_the_last_try_the_message_is_given_up_and_the_log_says_so(
        client: TestClient, family: dict[str, Any], push: PushService, clock: Clock,
        caplog: pytest.LogCaptureFixture) -> None:
    push.browser(client)
    push.answer = 503
    with new_client(family["anna"]) as anna:
        make_event(anna, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    with caplog.at_level(logging.WARNING, logger="nextime.notify"):
        clock.later(200)
        for minutes in notify.RETRY_MINUTES:
            clock.later(minutes * 60)
    assert "Notification given up" in caplog.text
    with SessionLocal() as db:
        row = db.scalar(select(NotifyOutbox))
        assert row is not None and row.failed_at is not None and row.payload == {}


def test_time21_ec11_a_restart_in_the_quiet_time_still_sends_the_bundle_exactly_once(
        client: TestClient, family: dict[str, Any], push: PushService, clock: Clock) -> None:
    push.browser(client)
    with new_client(family["anna"]) as anna:
        make_event(anna, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    with SessionLocal() as db:
        assert len(list(db.scalars(select(NotifyChange)))) == 1
    # A restart: the app starts anew, what waited is in the database.
    with TestClient(app):
        pass
    clock.later(200)
    clock.later(200)
    with TestClient(app):
        pass
    clock.later(200)
    assert len(push.messages()) == 1


def test_time21_ec14_without_https_or_internet_the_settings_say_so_and_the_log_names_why(
        client: TestClient, account: Account, caplog: pytest.LogCaptureFixture) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_READ)
    notify._warned.clear()
    internet.probe_override = lambda: False
    with caplog.at_level(logging.WARNING, logger="nextime.notify"):
        delivery = client.get(f"/api/calendars/{familie}/notify").json()["delivery"]
    assert delivery == {"https": False, "internet": False}
    assert "not reached over HTTPS" in caplog.text and "does not reach the internet" in caplog.text
    internet.forget()
    internet.probe_override = lambda: True
    with TestClient(app, base_url="https://testserver", headers={"X-Nextime-Client": "tab-secure00"}) as secure:
        secure.cookies.set(SESSION_COOKIE, client.cookies.get(SESSION_COOKIE) or "")
        assert secure.get(f"/api/calendars/{familie}/notify").json()["delivery"] == {"https": True, "internet": True}
