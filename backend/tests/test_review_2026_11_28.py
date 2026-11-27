"""After the review of 2026-11-28: the addresses a member types for a source (CalDAV server, subscription link) are
checked where the connection is made, a connection's token rests while a second factor is owed, and an account
deleted for good goes through the shared module.

* A CalDAV server on the machine itself, link-local or a cloud's metadata service is never asked, at connecting, in
  every round and after a redirect; the own network only for the operator or with the operator's switch.
* A subscription's link is checked again by the connection itself (DNS rebinding), metadata addresses hidden in IPv6
  are refused, the own network as for CalDAV servers.
* A token of a connection (``ntv_``) rests while the operator requires a second factor the account has not set up.
* An account deleted for good: its name stays reserved, its connections end on both sides, its gone devices go.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from datetime import timedelta

import httpcore
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, AppConnection, AppGoneDevice, Source, utcnow
from app.services import accounts, addresses, outbound, settings_service, subscriptions, verbinden_store
from app.services.konten_store import KontenStore
from app.services.remote_dav import BLOCKED, Remote, RemoteError

from .calendar_helpers import code
from .conftest import make_account, new_client
from .remote_helpers import (
    SWITCHES,
    connect,
    connected,
    dav_server,  # noqa: F401 - the fixture
    quiet_world,  # noqa: F401 - the fixture
    remote_account,
    round_now,
    source_row,
)
from .remote_helpers import Remote as RemoteAccount
from .test_verbinden_host import _as, hosted


@pytest.fixture
def remote(client: TestClient) -> Iterator[RemoteAccount]:
    yield from remote_account()


def _local_switch(on: bool) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"connections_local_network": on})


# --- CalDAV servers --------------------------------------------------------------------------------------------------


def test_a_caldav_server_on_the_machine_itself_is_never_asked(
        dav_server: str, client: TestClient, account: object, remote: RemoteAccount,
        monkeypatch: pytest.MonkeyPatch) -> None:
    # The tests' own server stands on 127.0.0.1; as in the running app, nothing is let through here.
    monkeypatch.setattr(addresses, "TESTS_ALLOWED", ())
    answer = connect(client, dav_server, remote)
    assert answer.status_code == 422 and code(answer) == "source_address_blocked"
    assert SWITCHES.seen == [], "not one request reached the server"


@pytest.mark.parametrize("server", ["http://169.254.169.254/", "http://[fd00:ec2::254]/", "http://[::1]:8080/",
                                    "http://0.0.0.0/"],
                         ids=["metadata", "metadata-ipv6", "ipv6-loopback", "any"])
def test_link_local_and_metadata_servers_are_refused_before_a_socket_is_made(
        client: TestClient, account: object, server: str) -> None:
    answer = client.post("/api/sources/dav", json={"kind": "caldav", "server": server, "username": "kim",
                                                   "password": "geheim"})
    assert answer.status_code == 422 and code(answer) == "source_address_blocked"


def test_a_member_reaches_the_own_network_only_when_the_operator_allows_it(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(addresses, "TESTS_ALLOWED", ())
    member = make_account("kim")
    with new_client(member) as browser:
        answer = browser.post("/api/sources/dav", json={"kind": "caldav", "server": "http://192.168.1.20/",
                                                        "username": "kim", "password": "geheim"})
    assert answer.status_code == 422 and code(answer) == "source_address_local"
    home = outbound.ip_of("192.168.1.20")
    with SessionLocal() as db:
        with pytest.raises(outbound.Refused):
            addresses.for_account(db, member.id)(home)
        # The operator's own sources may, and everybody's once the operator allows it.
        addresses.for_account(db, account.id)(home)
    _local_switch(True)
    with SessionLocal() as db:
        addresses.for_account(db, member.id)(home)
        with pytest.raises(outbound.Refused):
            addresses.for_account(db, member.id)(outbound.ip_of("127.0.0.1"))


def test_every_round_checks_the_server_again(dav_server: str, client: TestClient, account: object,
                                             remote: RemoteAccount, monkeypatch: pytest.MonkeyPatch) -> None:
    remote.calendar("Arbeit")
    source_id = connected(client, dav_server, remote)
    round_now(source_id)
    assert source_row(client, source_id)["state"] == "ok"
    # The same address now refused (a name that answers differently later): the round asks nothing there.
    monkeypatch.setattr(addresses, "TESTS_ALLOWED", ())
    SWITCHES.seen.clear()
    round_now(source_id)
    assert SWITCHES.seen == []
    with SessionLocal() as db:
        row = db.get(Source, source_id)
        assert row is not None and row.state == "unreachable" and row.problem == "blocked"


def test_a_redirect_into_the_machine_itself_is_not_followed(dav_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    port = int(dav_server.rsplit(":", 1)[1])
    monkeypatch.setattr(addresses, "TESTS_ALLOWED", (ipaddress.ip_network("127.0.0.1/32"),))
    with Remote(dav_server + "/", checked=addresses.rule(False)) as remote, pytest.raises(RemoteError) as caught:
        remote.request(f"{dav_server}/moved-to?to=http://[::1]:{port}/dav/", "GET")
    assert caught.value.kind == BLOCKED


# --- Subscriptions ---------------------------------------------------------------------------------------------------


@pytest.fixture
def answered(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n")

    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(handler))
    return calls


@pytest.mark.parametrize("link", ["http://[fd00:ec2::254]/k.ics", "http://100.100.100.200/k.ics",
                                  "http://[64:ff9b::a9fe:a9fe]/k.ics", "http://[2002:a9fe:a9fe::1]/k.ics",
                                  "http://[::ffff:127.0.0.1]/k.ics"],
                         ids=["aws-ipv6", "alibaba", "nat64", "6to4", "mapped-loopback"])
def test_metadata_addresses_hidden_anywhere_are_never_fetched(client: TestClient, account: object,
                                                              answered: list[str], link: str,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(addresses, "TESTS_ALLOWED", ())
    answer = client.post("/api/sources/subscriptions/preview", json={"url": link})
    assert answer.status_code == 422 and code(answer) == "subscription_address_blocked"
    assert answered == []


def test_a_member_subscribes_in_the_own_network_only_when_the_operator_allows_it(
        client: TestClient, account: object, answered: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subscriptions, "resolve", lambda host: ["192.168.178.20"])
    home = "http://nextcloud.fritz.box/remote.php/dav/public-calendars/x?export"
    member = make_account("kim")
    with new_client(member) as browser:
        refused = browser.post("/api/sources/subscriptions/preview", json={"url": home})
        assert refused.status_code == 422 and code(refused) == "subscription_address_local"
        assert answered == []
        _local_switch(True)
        assert browser.post("/api/sources/subscriptions/preview", json={"url": home}).status_code == 200


def test_the_connection_itself_checks_what_the_name_gives_then(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding: the name gives a public address when checked, the machine itself when connected."""
    monkeypatch.setattr(addresses, "TESTS_ALLOWED", ())
    given = iter([["93.184.216.34"], ["127.0.0.1"]])
    monkeypatch.setattr(subscriptions, "resolve", lambda host: next(given))
    monkeypatch.setattr(subscriptions, "transport", None)
    sockets: list[tuple[str, int]] = []

    def recorded(self: object, host: str, port: int, *args: object, **kwargs: object) -> object:
        sockets.append((host, port))
        raise httpcore.ConnectError("nothing leaves a test")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", recorded)
    with pytest.raises(subscriptions.FetchError) as caught:
        subscriptions.fetch("http://rebind.example.test/k.ics", checked=addresses.rule(True))
    assert caught.value.code == "subscription_address_blocked"
    assert sockets == []


# --- A connection's token and the second factor -----------------------------------------------------------------------


def test_a_connection_rests_while_a_second_factor_is_owed(client: TestClient, account: Account) -> None:
    token = hosted(account.id, "nexmail")
    with _as(token) as partner:
        assert partner.get("/api/v1/calendars").status_code == 200
        with SessionLocal() as db:
            settings_service.save(db, {"two_factor_required": True})
        resting = partner.get("/api/v1/calendars")
        assert resting.status_code == 401 and resting.json()["detail"]["code"] == "account_resting"
        with SessionLocal() as db:
            settings_service.save(db, {"two_factor_required": False})
        assert partner.get("/api/v1/calendars").status_code == 200


# --- An account deleted for good -----------------------------------------------------------------------------------


def test_an_account_deleted_for_good_goes_through_the_module(client: TestClient, account: Account,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    anna = make_account("anna")
    hosted(anna.id, "nexmail")
    ended: list[int] = []
    real_end_all = verbinden_store.end_all
    monkeypatch.setattr(verbinden_store, "end_all", lambda db, account_id: ended.append(account_id)
                        or real_end_all(db, account_id))
    with SessionLocal() as db:
        db.add(AppGoneDevice(token_hash="a" * 64, account_id=anna.id))
        row = db.get(Account, anna.id)
        assert row is not None
        row.deleted_at = utcnow() - timedelta(days=31)
        db.commit()
        assert accounts.purge_deleted(db) == 1
    assert ended == [anna.id]
    with SessionLocal() as db:
        assert db.get(Account, anna.id) is None
        assert KontenStore(db).name_reserved("anna", None), "the name stays reserved for ever"
        assert db.scalars(select(AppConnection).where(AppConnection.account_id == anna.id)).first() is None
        assert db.scalars(select(AppGoneDevice).where(AppGoneDevice.account_id == anna.id)).first() is None
