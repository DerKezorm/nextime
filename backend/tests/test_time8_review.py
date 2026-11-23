"""TIME-8 after the review of stage 6a: brakes per account and for the whole server, so that the fair use of the
address service and of the OpenStreetMap tiles holds; the longest answer read; the services nextime brings along
called at the address that was checked."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import Account
from app.services import places

from .calendar_helpers import code
from .conftest import make_account, new_client
from .test_time8 import Services, services  # noqa: F401 - the fixture


class Minute:
    """The clock of the brakes."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def minute(monkeypatch: pytest.MonkeyPatch) -> Minute:
    made = Minute()
    monkeypatch.setattr(places, "monotonic", made)
    return made


def test_time8_ac2_more_than_60_maps_a_minute_are_braked_for_that_account_only(
        client: TestClient, account: Account, services: Services, minute: Minute) -> None:
    for number in range(places.MAPS_PER_MINUTE):
        assert client.get("/api/places/map", params={"lat": 52.5, "lon": 13.4 + number / 1000}).status_code == 200
    tiles = len(services.requests)
    refused = client.get("/api/places/map", params={"lat": 52.6, "lon": 13.4})
    assert refused.status_code == 429 and code(refused) == "places_busy"
    assert 1 <= int(refused.headers["retry-after"]) <= 60
    assert len(services.requests) == tiles
    with new_client(make_account("ben")) as other:
        assert other.get("/api/places/map", params={"lat": 52.5, "lon": 13.4}).status_code == 200
    minute.now += 61
    assert client.get("/api/places/map", params={"lat": 52.6, "lon": 13.4}).status_code == 200


def test_time8_ac1_more_than_120_suggestions_a_minute_are_braked(client: TestClient, account: Account,
                                                                 services: Services, minute: Minute) -> None:
    for _number in range(places.SUGGESTIONS_PER_MINUTE):
        assert client.get("/api/places", params={"q": "Lindenstraße 4"}).status_code == 200
    refused = client.get("/api/places", params={"q": "Lindenstraße 5"})
    assert refused.status_code == 429 and code(refused) == "places_busy" and refused.headers["retry-after"]
    assert len(services.requests) == 1
    minute.now += 61
    assert client.get("/api/places", params={"q": "Lindenstraße 5"}).status_code == 200


def test_time8_ec3_the_whole_server_asks_a_service_only_so_often_a_minute(
        client: TestClient, account: Account, services: Services, minute: Minute,
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(places.OUT_PER_MINUTE, "address", 2)
    answers = [client.get("/api/places", params={"q": f"Lindenstraße {number}"}).json() for number in range(3)]
    assert [answer["unavailable"] for answer in answers] == [False, False, True]
    assert len(services.requests) == 2
    assert client.get("/api/places/map", params={"lat": 52.5, "lon": 13.4}).status_code == 200
    tiles = len(services.requests) - 2
    monkeypatch.setitem(places.OUT_PER_MINUTE, "tiles", tiles)
    assert client.get("/api/places/map", params={"lat": 48.1, "lon": 11.5}).status_code == 503
    assert len(services.requests) == 2 + tiles
    minute.now += 61
    assert client.get("/api/places", params={"q": "Lindenstraße 9"}).json()["unavailable"] is False


def test_time8_ec3_an_answer_over_2_mb_is_not_read_to_the_end(client: TestClient, account: Account,
                                                             monkeypatch: pytest.MonkeyPatch,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    sent: list[int] = []

    def large(request: httpx.Request) -> httpx.Response:
        def chunks() -> Iterator[bytes]:
            for _number in range(48):
                sent.append(1)
                yield b"x" * (64 * 1024)

        return httpx.Response(200, content=chunks())

    monkeypatch.setattr(places, "transport", httpx.MockTransport(large))
    caplog.set_level(logging.WARNING, logger="nextime.places")
    assert client.get("/api/places", params={"q": "Lindenstraße 4"}).json() == {"suggestions": [],
                                                                                "unavailable": True}
    assert "reason=too_large" in caplog.text
    assert len(sent) < 48  # stopped after 2 MB, not at the end of 3 MB
    assert client.get("/api/places/map", params={"lat": 52.5, "lon": 13.4}).status_code == 503


def test_time8_ec3_the_default_services_are_called_at_the_address_that_was_checked(
        client: TestClient, account: Account, services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "places_url", places.DEFAULT_PLACES)
    monkeypatch.setattr(get_settings(), "tiles_url", places.DEFAULT_TILES)
    monkeypatch.setattr(places, "resolve", lambda host: ["93.184.216.34"] if host.startswith("photon")
                        else ["2606:2800:220:1:248:1893:25c8:1946"])
    assert client.get("/api/places", params={"q": "Lindenstraße 4"}).json()["unavailable"] is False
    assert client.get("/api/places/map", params={"lat": 52.5, "lon": 13.4}).status_code == 200
    asked, *tiles = services.requests
    assert asked.url.host == "93.184.216.34" and asked.url.scheme == "https"
    assert asked.headers["host"] == "photon.komoot.io"
    assert asked.extensions["sni_hostname"] == "photon.komoot.io"
    assert tiles and all(request.url.host == "2606:2800:220:1:248:1893:25c8:1946" for request in tiles)
    assert all(request.headers["host"] == "tile.openstreetmap.org" for request in tiles)
