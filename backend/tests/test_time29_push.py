"""TIME-29 follow-up 3: the silent nudge through the push relay (``services/apppush.py``, ``docs/vertrag-app1.md``
"Notifications"). Every change the account sees wakes its app devices with notifications on, encrypted for each, at
most every ``GAP`` seconds; nothing goes into the network (``apppush.POST`` records)."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import Account
from app.services import appdevices, apppush, push_crypto

from .calendar_helpers import make_calendar, make_event
from .conftest import make_account, new_client
from .test_time29 import call, pair


class Relay:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.status = 200

    def __call__(self, url: str, body: dict[str, Any]) -> int:
        self.sent.append((url, body))
        return self.status


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> Iterator[Relay]:
    recorder = Relay()
    monkeypatch.setattr(apppush, "POST", recorder)
    monkeypatch.setattr(get_settings(), "push_relay", "https://push.example.com/")
    apppush.forget()
    appdevices.forget()
    yield recorder
    apppush.forget()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(apppush, "clock", fake)
    return fake


def keys() -> tuple[str, str]:
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    return base64.b64encode(raw).decode(), base64.b64encode(private.public_key().public_bytes_raw()).decode()


def with_push(device: dict[str, Any], ticket: str) -> str:
    private, public = keys()
    assert call("PUT", "/push", device["token"], json={"ticket": ticket, "key": public}).status_code == 204
    return private


def test_TIME29_follow_up3_a_change_nudges_the_devices_with_notifications_on(client: TestClient, account: Account,
                                                                              relay: Relay, clock: Clock) -> None:
    phone = pair(client)
    pad = pair(client, "iPad", "ipados")
    quiet_one = pair(client, "Altes iPhone")
    secret = with_push(phone, "1.phone")
    with_push(pad, "1.pad")
    assert call("GET", "/me", phone["token"]).json()["offers"]["push"] is True
    make_event(client, "Zahnarzt")
    assert apppush.flush(clock.now) == 0
    clock.now += apppush.DELAY
    assert apppush.flush() == 2
    tickets = sorted(body["ticket"] for _url, body in relay.sent)
    assert tickets == ["1.pad", "1.phone"] and quiet_one["device"]["id"]
    url, body = next(item for item in relay.sent if item[1]["ticket"] == "1.phone")
    assert url == "https://push.example.com/v1/send" and body["quiet"] is True
    assert body["collapse"] == f"nt-{appdevices.server_id()}-{account.id}"
    message = json.loads(push_crypto.decrypt(body["enc"], secret))
    assert message == {"type": "changed", "account": account.id, "server": appdevices.server_id()}
    assert "Zahnarzt" not in json.dumps(body)


def test_TIME29_follow_up3_a_burst_is_one_nudge_and_nudges_keep_their_distance(client: TestClient, account: Account,
                                                                               relay: Relay, clock: Clock) -> None:
    phone = pair(client)
    with_push(phone, "1.phone")
    for title in ("Eins", "Zwei", "Drei"):
        make_event(client, title)
    clock.now += apppush.DELAY
    assert apppush.flush() == 1
    clock.now += 1
    make_calendar(client, "Familie")
    clock.now += apppush.DELAY
    assert apppush.flush() == 0
    clock.now = 1000.0 + apppush.DELAY + apppush.GAP
    assert apppush.flush() == 1
    assert len(relay.sent) == 2


def test_TIME29_follow_up3_others_and_shared_calendars(client: TestClient, account: Account, relay: Relay,
                                                      clock: Clock) -> None:
    from .calendar_helpers import share

    mika = make_account("mika")
    kim = make_account("kim")
    with new_client(mika) as browser:
        mikas = pair(browser)
    with new_client(kim) as browser:
        kims = pair(browser)
    with_push(mikas, "1.mika")
    with_push(kims, "1.kim")
    family = make_calendar(client, "Familie")
    share(family, mika.id, "edit")
    clock.now += apppush.DELAY + apppush.GAP
    apppush.flush()
    relay.sent.clear()
    clock.now += apppush.GAP
    make_event(client, "Elternabend", calendar_id=family)
    clock.now += apppush.DELAY
    apppush.flush()
    assert [body["ticket"] for _url, body in relay.sent] == ["1.mika"]


def test_TIME29_follow_up3_a_ticket_the_relay_forgot_goes(client: TestClient, account: Account, relay: Relay,
                                                         clock: Clock) -> None:
    phone = pair(client)
    with_push(phone, "1.phone")
    relay.status = 410
    make_event(client, "Zahnarzt")
    clock.now += apppush.DELAY
    assert apppush.flush() == 0
    assert client.get("/api/app-devices").json()[0]["push"] is False


def test_TIME29_follow_up3_without_a_relay_nothing_goes_out(client: TestClient, account: Account, relay: Relay,
                                                           clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    phone = pair(client)
    with_push(phone, "1.phone")
    make_event(client, "Vorher")
    monkeypatch.setattr(get_settings(), "push_relay", "")
    assert call("GET", "/me", phone["token"]).json()["offers"]["push"] is False
    make_event(client, "Zahnarzt")
    clock.now += apppush.DELAY
    # Neither what was due before the relay went nor what came after goes out.
    assert apppush.flush() == 0 and relay.sent == []


# --- Notifications of changes with text (contract 0.3, TIME-29 AC-3, follow-up 5) --------------------------------------


@pytest.fixture
def ticker(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.services import notify

    from .test_time21 import Clock as NotifyClock

    made = NotifyClock()
    monkeypatch.setattr(notify, "clock", made)
    return made


def shared_family(client: TestClient, account: Account) -> tuple[int, Account]:
    from .test_time19 import put_share
    from .test_time21 import prefs, switch

    prefs(account.id)
    mika = make_account("mika")
    family = make_calendar(client, "Familie")
    put_share(client, family, mika.id, "edit")
    assert switch(client, family).status_code == 200
    return family, mika


def test_TIME29_AC3_a_change_of_another_reaches_the_app_that_shows_notifications(
        client: TestClient, account: Account, relay: Relay, ticker: Any) -> None:
    family, mika = shared_family(client, account)
    phone = pair(client)
    old_build = pair(client, "Altes iPhone")
    private, public = keys()
    assert call("PUT", "/push", phone["token"], json={"ticket": "1.phone", "key": public,
                                                      "alerts": True}).status_code == 204
    with_push(old_build, "1.old")
    with new_client(mika) as browser:
        made = make_event(browser, "Elternabend", calendar_id=family, start="2026-12-03T19:00",
                          end="2026-12-03T20:00")
    ticker.later(150)
    alerts = [body for _url, body in relay.sent if not body.get("quiet")]
    assert [body["ticket"] for body in alerts] == ["1.phone"]
    # The build without the extension is not even queued for (only the nudge reaches it).
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import NotifyOutbox

    with SessionLocal() as db:
        assert [row.target_id for row in db.scalars(select(NotifyOutbox).where(NotifyOutbox.kind == "app"))] == [
            phone["device"]["id"]]
    body = alerts[0]
    assert "quiet" not in body and body["collapse"].startswith("nt-c")
    message = json.loads(push_crypto.decrypt(body["enc"], private))
    assert message["type"] == "changes" and message["account"] == account.id
    assert message["server"] == appdevices.server_id() and message["calendar_id"] == family
    assert message["title"] == "Familie" and "Mika" in message["text"] and "Elternabend" in message["text"]
    assert message["who"] == ["Mika"] and message["calendar"] == "Familie" and message["count"] == 1
    assert message["items"] == [{"kind": "created", "event_id": made["id"], "title": "Elternabend",
                                 "starts_at": "2026-12-03T18:00:00Z", "all_day": False, "before_start": None,
                                 "recurring": False}]
    assert message["day"] == "2026-12-03" and message["event_id"] == made["id"]
    # Again a minute later: nothing twice.
    relay.sent.clear()
    ticker.later(60)
    assert relay.sent == []


def test_TIME29_AC3_own_changes_and_switched_off_calendars_send_nothing(
        client: TestClient, account: Account, relay: Relay, ticker: Any) -> None:
    from .test_time21 import switch

    family, mika = shared_family(client, account)
    phone = pair(client)
    _private, public = keys()
    call("PUT", "/push", phone["token"], json={"ticket": "1.phone", "key": public, "alerts": True})
    make_event(client, "Eigener Termin", calendar_id=family)
    ticker.later(150)
    assert relay.sent == []
    switch(client, family, False)
    with new_client(mika) as browser:
        make_event(browser, "Elternabend", calendar_id=family)
    ticker.later(150)
    assert relay.sent == []


def test_TIME29_AC3_a_ticket_the_relay_forgot_stops_the_notifications(
        client: TestClient, account: Account, relay: Relay, ticker: Any) -> None:
    family, mika = shared_family(client, account)
    phone = pair(client)
    _private, public = keys()
    call("PUT", "/push", phone["token"], json={"ticket": "1.phone", "key": public, "alerts": True})
    relay.status = 410
    with new_client(mika) as browser:
        make_event(browser, "Elternabend", calendar_id=family)
    ticker.later(150)
    assert len(relay.sent) == 1
    assert client.get("/api/app-devices").json()[0]["push"] is False


def test_TIME29_AC3_busy_only_tells_times_without_titles_names_or_events(
        client: TestClient, account: Account, relay: Relay, ticker: Any) -> None:
    from .test_time19 import from_source, put_share, source_calendar, vevent
    from .test_time21 import prefs, switch

    mika = make_account("mika")
    prefs(mika.id)
    work = source_calendar(account.id, "Arbeit")
    assert put_share(client, work, mika.id, "busy").status_code == 200
    with new_client(mika) as browser:
        assert switch(browser, work).status_code == 200
        phone = pair(browser)
    private, public = keys()
    call("PUT", "/push", phone["token"], json={"ticket": "1.mika", "key": public, "alerts": True})
    from_source(work, vevent("kunde@example.com", "20261203T090000Z", "20261203T100000Z", "Kundentermin Geheim",
                             location="Büro"))
    ticker.later(150)
    alerts = [body for _url, body in relay.sent if body["ticket"] == "1.mika"]
    assert len(alerts) == 1
    message = json.loads(push_crypto.decrypt(alerts[0]["enc"], private))
    assert message["busy"] is True and message["who"] == [] and message["event_id"] is None
    assert [(item["title"], item["event_id"]) for item in message["items"]] == [("", None)]
    assert message["items"][0]["starts_at"] == "2026-12-03T09:00:00Z"
    assert "Kundentermin" not in json.dumps(message, ensure_ascii=False)


def test_TIME29_AC3_many_people_are_named_three_and_counted(client: TestClient, account: Account, relay: Relay,
                                                            ticker: Any) -> None:
    from .test_time19 import put_share
    from .test_time21 import prefs, switch

    prefs(account.id)
    family = make_calendar(client, "Familie")
    people = [make_account(name) for name in ("anna", "ben", "cleo", "dora")]
    for person in people:
        put_share(client, family, person.id, "edit")
    assert switch(client, family).status_code == 200
    phone = pair(client)
    private, public = keys()
    call("PUT", "/push", phone["token"], json={"ticket": "1.phone", "key": public, "alerts": True})
    for person in people:
        with new_client(person) as browser:
            make_event(browser, f"Termin von {person.name}", calendar_id=family)
    ticker.later(150)
    message = json.loads(push_crypto.decrypt(relay.sent[0][1]["enc"], private))
    assert message["who"] == ["Anna", "Ben", "Cleo"] and message["who_more"] == 1 and message["count"] == 4
    assert len(relay.sent[0][1]["enc"]) < 3000


def test_geraete_g61_alerts_of_the_testflight_app_are_no_choice_of_the_device(
        client: TestClient, account: Account, relay: Relay) -> None:
    """geraete 0.1.3 (G61, G62): ``alerts`` is nextime's own older form; the kinds it stands for come from nextime,
    so the push card says "kommende App-Version" (``chooses_kinds`` false). A device naming ``understands`` chose.
    nextime's own ``PUT /push`` answers alone (204), the module's is left out."""
    _private, public = keys()
    old_build = pair(client, "TestFlight")
    new_build = pair(client, "Neue App")
    assert call("PUT", "/push", old_build["token"], json={"ticket": "1.old", "key": public,
                                                          "alerts": True}).status_code == 204
    assert call("PUT", "/push", new_build["token"], json={"ticket": "1.new", "key": public,
                                                          "understands": ["changed", "changes"]}).status_code == 204
    listed = client.get("/api/geraete/app-devices").json()["devices"]
    assert {row["name"]: (row["push"], row["chooses_kinds"]) for row in listed} == {
        "TestFlight": (True, False), "Neue App": (True, True)}
    from app.routers import app1

    def puts(router: Any) -> list[Any]:
        return [route for route in router.routes
                if getattr(route, "path", "").endswith("/push") and "PUT" in getattr(route, "methods", set())]

    assert len(puts(app1.router)) == 1 and puts(app1.module_router) == []
