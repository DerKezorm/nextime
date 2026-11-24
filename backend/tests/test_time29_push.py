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
