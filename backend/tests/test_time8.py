"""TIME-8 Ort und Karte: the place of an event as free text with an optional point, address suggestions and the small
map through the server, from services that the tests play themselves (``httpx.MockTransport``; nothing reaches the
network). With the negative side: only the typed place goes out, nothing when the operator switched it off, nothing to
the server's own network from the services nextime brings along, no redirect followed, no map or place for "busy
only", and a broken service costs only the suggestions or the map."""

from __future__ import annotations

import io
import logging
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import get_settings
from app.db import SessionLocal
from app.models import RIGHT_BUSY, RIGHT_READ, Account, Event
from app.services import places

from .calendar_helpers import code, make_calendar, make_event
from .conftest import make_account, new_client
from .test_time19 import from_source, guest_client, link_on, put_share, source_calendar, token_of, vevent
from .test_time20 import calendar_url, device_for, raw

PHOTON = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [13.3889, 52.5170]},
         "properties": {"name": "Praxis Dr. Weber", "street": "Lindenstraße", "housenumber": "4",
                        "postcode": "10115", "city": "Berlin", "country": "Deutschland"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [13.40, 52.52]},
         "properties": {"street": "Lindenstraße", "housenumber": "40", "postcode": "10115", "city": "Berlin"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": ["x", 1]}, "properties": {"name": "Broken"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [13.0, 52.0]},
         "properties": {"name": "Lindendorf", "state": "Brandenburg", "country": "Deutschland"}},
    ],
}


def tile_png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (256, 256), (200, 220, 200)).save(out, "PNG")
    return out.getvalue()


class Services:
    """The address service and the tile server as the tests want them."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.places: str = "ok"
        self.tiles: str = "ok"
        self.tile = tile_png()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # The services nextime brings along are called at their checked address, with their name as Host.
        host = request.headers.get("host", request.url.host)
        if host.startswith(("tiles", "tile.")):
            if self.tiles == "down":
                raise httpx.ConnectError("down", request=request)
            if self.tiles == "error":
                return httpx.Response(503)
            if self.tiles == "nonsense":
                return httpx.Response(200, content=b"not a picture")
            return httpx.Response(200, content=self.tile, headers={"Content-Type": "image/png"})
        mode = self.places
        if mode == "down":
            raise httpx.ConnectError("down", request=request)
        if mode == "slow":
            raise httpx.ReadTimeout("slow", request=request)
        if mode == "error":
            return httpx.Response(500)
        if mode == "nonsense":
            return httpx.Response(200, content=b"<html>surprise</html>")
        if mode == "other_version":
            return httpx.Response(200, json={"results": [{"lat": 1, "lon": 2}]})
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "http://10.0.0.1/"})
        if mode == "empty":
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        return httpx.Response(200, json=PHOTON)


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> Services:
    made = Services()
    monkeypatch.setattr(places, "transport", httpx.MockTransport(made))
    monkeypatch.setattr(places, "resolve", lambda host: ["93.184.216.34"])
    return made


def switch(client: TestClient, on: bool) -> None:
    answer = client.put("/api/settings", json={"address_suggestions": on})
    assert answer.status_code == 200, answer.text


# --- AC-1, AC-5, EC-5: suggestions --------------------------------------------------------------------------------


def test_time8_ac1_ac5_suggestions_bring_address_and_name_and_only_the_typed_place_goes_out(
        client: TestClient, account: Account, services: Services) -> None:
    make_event(client, "Geheimtermin Vorstand", notes="Gehaltsrunde", location="Linden")
    answer = client.get("/api/places", params={"q": "  Lindenstr  4 "})
    assert answer.status_code == 200
    found = answer.json()
    assert found["unavailable"] is False
    assert found["suggestions"] == [
        {"address": "Lindenstraße 4, 10115 Berlin", "name": "Praxis Dr. Weber",
         "value": "Praxis Dr. Weber, Lindenstraße 4, 10115 Berlin", "lat": 52.517, "lon": 13.3889},
        {"address": "Lindenstraße 40, 10115 Berlin", "name": "", "value": "Lindenstraße 40, 10115 Berlin",
         "lat": 52.52, "lon": 13.4},
        {"address": "Lindendorf, Brandenburg, Deutschland", "name": "",
         "value": "Lindendorf, Brandenburg, Deutschland", "lat": 52.0, "lon": 13.0},
    ]
    assert len(services.requests) == 1
    request = services.requests[0]
    assert request.url.host == "photon.example.com"
    assert dict(request.url.params) == {"q": "Lindenstr 4", "limit": "5", "lang": "en"}
    sent = str(request.url) + " ".join(f"{key}:{value}" for key, value in request.headers.items())
    assert "Geheim" not in sent and "Gehalt" not in sent and "tester" not in sent.lower()
    assert "cookie" not in request.headers and "authorization" not in request.headers
    assert request.headers["user-agent"].startswith("nextime/")
    # The same text again: from memory, nothing goes out.
    client.get("/api/places", params={"q": "Lindenstr 4"})
    assert len(services.requests) == 1
    # Under three characters nothing is asked.
    assert client.get("/api/places", params={"q": "Li"}).json() == {"suggestions": [], "unavailable": False}
    assert len(services.requests) == 1


def test_time8_ec5_when_nothing_fits_there_are_no_suggestions(client: TestClient, account: Account,
                                                               services: Services) -> None:
    services.places = "empty"
    assert client.get("/api/places", params={"q": "bei Oma"}).json() == {"suggestions": [], "unavailable": False}


def test_time8_ac1_only_for_a_signed_in_account(services: Services) -> None:
    with new_client() as stranger:
        assert stranger.get("/api/places", params={"q": "Lindenstr"}).status_code == 401
        assert stranger.get("/api/places/map", params={"lat": 52.5, "lon": 13.4}).status_code == 401
    assert services.requests == []


# --- AC-6: switched off -----------------------------------------------------------------------------------------


def test_time8_ac6_switched_off_nothing_is_asked_and_the_place_stays_free_text(client: TestClient, account: Account,
                                                                               services: Services) -> None:
    assert client.get("/api/calendars").json()["places"] is True
    switch(client, False)
    assert client.get("/api/calendars").json()["places"] is False
    for path, params in (("/api/places", {"q": "Lindenstraße 4"}), ("/api/places/lookup", {"q": "Lindenstraße 4"}),
                         ("/api/places/map", {"lat": 52.5, "lon": 13.4})):
        answer = client.get(path, params=params)
        assert answer.status_code == 409 and code(answer) == "places_off", path
    made = make_event(client, "Arzt", location="bei Oma")
    assert made["location"] == "bei Oma"
    assert services.requests == []
    # A member sees the same.
    anna = make_account("anna")
    with new_client(anna) as browser:
        assert browser.get("/api/calendars").json()["places"] is False
        assert browser.get("/api/places", params={"q": "Lindenstraße"}).status_code == 409
    assert services.requests == []


# --- EC-3: a failing service ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["down", "slow", "error", "nonsense", "other_version", "redirect"])
def test_time8_ec3_a_failing_service_costs_only_the_suggestions_and_the_log_says_so(
        client: TestClient, account: Account, services: Services, caplog: pytest.LogCaptureFixture,
        mode: str) -> None:
    services.places = mode
    caplog.set_level(logging.WARNING, logger="nextime.places")
    assert client.get("/api/places", params={"q": "Lindenstraße 4"}).json() == {"suggestions": [],
                                                                                "unavailable": True}
    assert "The address service gave no usable answer" in caplog.text
    # What was typed never reaches the log.
    assert "Lindenstraße" not in caplog.text
    # Saving goes on.
    assert make_event(client, "Arzt", location="Lindenstraße 4")["location"] == "Lindenstraße 4"
    # A redirect is not followed: one request only.
    assert len(services.requests) == 1


def test_time8_ec3_the_deadline_is_short(services: Services) -> None:
    assert places.CONNECT_SECONDS <= 3 and places.TOTAL_SECONDS <= 5


# --- Never into the own network (the services nextime brings along) ---------------------------------------------


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1",
                                     "fe80::1", "::ffff:10.0.0.1", "64:ff9b::a00:1"])
def test_time8_ec3_the_default_services_are_asked_only_at_public_addresses(
        client: TestClient, account: Account, services: Services, monkeypatch: pytest.MonkeyPatch,
        address: str) -> None:
    monkeypatch.setattr(get_settings(), "places_url", places.DEFAULT_PLACES)
    monkeypatch.setattr(get_settings(), "tiles_url", places.DEFAULT_TILES)
    monkeypatch.setattr(places, "resolve", lambda host: ["93.184.216.34", address])
    assert client.get("/api/places", params={"q": "Lindenstraße 4"}).json()["unavailable"] is True
    assert client.get("/api/places/map", params={"lat": 52.5, "lon": 13.4}).status_code == 503
    assert services.requests == []


def test_time8_ec3_the_default_services_only_over_https(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(places.Unavailable):
        places._checked("http://photon.komoot.io/api/", places.DEFAULT_PLACES)
    with pytest.raises(places.Unavailable):
        places._checked("https://photon.komoot.io:8443/api/", places.DEFAULT_PLACES)
    with pytest.raises(places.Unavailable):
        # A login in the address (put together here, so that no scanner takes it for a mail address).
        places._checked("https://user:secret" + "@" + "photon.komoot.io/api/", places.DEFAULT_PLACES)
    # The operator's own service may be in the home network: the operator chose it.
    assert places._checked("http://photon.lan:2322/api", places.DEFAULT_PLACES) == "http://photon.lan:2322/api"


# --- AC-2, AC-4, EC-1, EC-2: point and map ------------------------------------------------------------------------


def test_time8_ac2_a_chosen_suggestion_gives_a_point_and_a_map(client: TestClient, account: Account,
                                                              services: Services) -> None:
    made = make_event(client, "Zahnarzt", location="Praxis Dr. Weber, Lindenstraße 4, 10115 Berlin",
                      geo={"lat": 52.517, "lon": 13.3889})
    assert made["place"] == {"lat": 52.517, "lon": 13.3889}
    shown = client.get("/api/places/map", params=made["place"])
    assert shown.status_code == 200 and shown.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(shown.content)) as picture:
        assert picture.size == (places.MAP_WIDTH, places.MAP_HEIGHT)
    tiles = [request for request in services.requests if request.url.host.startswith("tiles")]
    assert tiles and all("/16/" in request.url.path for request in tiles)
    assert all("Zahnarzt" not in str(request.url) for request in services.requests)
    # Kept: the same map again asks nothing.
    before = len(services.requests)
    assert client.get("/api/places/map", params=made["place"]).status_code == 200
    assert len(services.requests) == before
    # Another place typed without choosing: the point goes, there is no map of the old place.
    changed = client.patch(f"/api/events/{made['id']}", json={"location": "bei Oma", "etag": made["etag"]}).json()
    assert changed["location"] == "bei Oma" and changed["place"] is None
    with SessionLocal() as db:
        assert "GEO" not in db.get(Event, made["id"]).ical


def test_time8_ac4_a_place_from_icloud_comes_with_its_point(client: TestClient, account: Account) -> None:
    arbeit = source_calendar(account.id, "Arbeit")
    from_source(arbeit, vevent("termin", "20261126T150000Z", "20261126T160000Z", "Termin",
                               location="Lindenstraße 4\\n10115 Berlin",
                               extra="X-APPLE-STRUCTURED-LOCATION;VALUE=URI;X-TITLE=Lindenstraße 4:geo:52.517,13.3889"))
    with SessionLocal() as db:
        row = db.query(Event).filter_by(calendar_id=arbeit).one()
    shown = client.get(f"/api/events/{row.id}").json()
    assert shown["location"] == "Lindenstraße 4\n10115 Berlin"
    assert shown["place"] == {"lat": 52.517, "lon": 13.3889}


def test_time8_ec2_ac5_a_place_without_a_point_has_a_map_only_when_it_was_typed_here_and_looks_like_an_address(
        client: TestClient, account: Account, services: Services) -> None:
    assert client.get("/api/places/lookup", params={"q": "bei Oma"}).json() == {"place": None, "unavailable": False}
    assert client.get("/api/places/lookup", params={"q": "javascript:alert(1)"}).json()["place"] is None
    assert client.get("/api/places/lookup", params={"q": "https://meet.example.com/123"}).json()["place"] is None
    # Looking at an event asks nobody (review of TIME-8, finding 2): an address never typed here has no map.
    assert client.get("/api/places/lookup", params={"q": "Lindenstraße 4, Berlin"}).json() == {
        "place": None, "unavailable": False}
    assert services.requests == []
    # Typed into the place field: the suggestions bring where it lies, and from then on it has its map.
    client.get("/api/places", params={"q": "Lindenstraße 4, Berlin"})
    assert len(services.requests) == 1
    found = client.get("/api/places/lookup", params={"q": "Lindenstraße 4, Berlin"}).json()
    assert found == {"place": {"lat": 52.517, "lon": 13.3889}, "unavailable": False}
    chosen = client.get("/api/places/lookup", params={"q": "Lindenstraße 40, 10115 Berlin"}).json()
    assert chosen["place"] == {"lat": 52.52, "lon": 13.4}
    assert len(services.requests) == 1


@pytest.mark.parametrize("mode", ["down", "error", "nonsense"])
def test_time8_ec1_without_tiles_only_the_map_is_missing(client: TestClient, account: Account, services: Services,
                                                         mode: str) -> None:
    services.tiles = mode
    made = make_event(client, "Zahnarzt", location="Lindenstraße 4", geo={"lat": 52.517, "lon": 13.3889})
    answer = client.get("/api/places/map", params=made["place"])
    assert answer.status_code == 503 and code(answer) == "map_unavailable"
    shown = client.get(f"/api/events/{made['id']}").json()
    assert shown["location"] == "Lindenstraße 4" and shown["place"] == {"lat": 52.517, "lon": 13.3889}


# --- EC-4, EC-6, EC-7, AC-7: the text --------------------------------------------------------------------------


def test_time8_ec4_an_empty_or_emptied_place_is_no_place(client: TestClient, account: Account) -> None:
    made = make_event(client, "Arzt", location="   ")
    assert made["location"] == "" and made["place"] is None
    filled = client.patch(f"/api/events/{made['id']}", json={"location": "Lindenstraße 4",
                                                             "geo": {"lat": 52.5, "lon": 13.4},
                                                             "etag": made["etag"]}).json()
    assert filled["place"] == {"lat": 52.5, "lon": 13.4}
    emptied = client.patch(f"/api/events/{made['id']}", json={"location": "", "geo": None,
                                                              "etag": filled["etag"]}).json()
    assert emptied["location"] == "" and emptied["place"] is None
    with SessionLocal() as db:
        text = db.get(Event, made["id"]).ical
        assert "LOCATION" not in text and "GEO" not in text


def test_time8_ec6_a_long_place_with_umlauts_emoji_and_breaks_stays_whole(client: TestClient,
                                                                           account: Account) -> None:
    place = "Ärztehaus 🎉 Süd\nzweite Zeile, " + "x" * 300
    made = make_event(client, "Arzt", location=f"  {place}  ")
    assert made["location"] == place
    # Over the calendar account it stays the same text.
    with SessionLocal() as db:
        name = db.get(Event, made["id"]).href
    shown = raw("GET", calendar_url(account, made["calendar_id"]) + name, account, device_for(account))
    from app.services import ical

    assert str(ical.master_of(ical.parse(shown.text))["LOCATION"]) == place
    refused = client.post("/api/events", json={"title": "Arzt", "start": "2026-11-26T10:00",
                                               "time_zone": "Europe/Berlin", "location": "y" * 2001})
    assert refused.status_code == 422 and code(refused) == "location_too_long"


def test_time8_ec7_ac7_markup_scripts_and_addresses_stay_plain_text(client: TestClient, account: Account) -> None:
    for place in ("<script>alert(1)</script>", "javascript:alert(1)", "https://meet.example.com/abc"):
        made = make_event(client, "Treffen", location=place)
        assert made["location"] == place and made["place"] is None
    assert not places.looks_like_address("javascript:alert(1)")
    assert not places.looks_like_address("https://meet.example.com/123")
    assert places.looks_like_address("Lindenstraße 4")


def test_time8_ec7_a_point_that_is_not_one_is_refused(client: TestClient, account: Account) -> None:
    refused = client.post("/api/events", json={"title": "Arzt", "start": "2026-11-26T10:00",
                                               "time_zone": "Europe/Berlin", "location": "x",
                                               "geo": {"lat": 91, "lon": 0}})
    assert refused.status_code == 422
    assert client.get("/api/places/map", params={"lat": 100, "lon": 0}).status_code == 422


# --- EC-9: rights -------------------------------------------------------------------------------------------------


def test_time8_ec9_read_shows_place_and_point_busy_only_neither_and_the_guest_page_the_text(
        client: TestClient, account: Account) -> None:
    ben, anna = make_account("ben"), make_account("anna")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, ben.id, RIGHT_READ)
    made = make_event(client, "Arzt", "2026-11-27T10:00", "2026-11-27T11:00", calendar_id=familie,
                      location="Lindenstraße 4", geo={"lat": 52.5, "lon": 13.4})
    with new_client(ben) as browser:
        shown = browser.get(f"/api/events/{made['id']}").json()
        assert shown["location"] == "Lindenstraße 4" and shown["place"] == {"lat": 52.5, "lon": 13.4}
        assert shown["writable"] is False
        refused = browser.patch(f"/api/events/{made['id']}", json={"location": "anders", "etag": shown["etag"]})
        assert refused.status_code == 403
    arbeit = source_calendar(account.id, "Arbeit")
    from_source(arbeit, vevent("geheim", "20261127T100000Z", "20261127T110000Z", "Geheim",
                               location="Vorstandsetage", extra="GEO:52.5;13.4"))
    put_share(client, arbeit, anna.id, RIGHT_BUSY)
    with SessionLocal() as db:
        busy_id = db.query(Event).filter_by(calendar_id=arbeit).one().id
    with new_client(anna) as browser:
        hidden = browser.get(f"/api/events/{busy_id}").json()
        assert hidden["location"] == "" and hidden["place"] is None
        listed = browser.get("/api/events", params={"start": "2026-11-27T00:00:00Z",
                                                    "end": "2026-11-28T00:00:00Z"}).json()
        assert all(entry["location"] == "" for entry in listed)
    link = link_on(client, familie)
    with guest_client() as guest:
        page = guest.get(f"/api/guest/{token_of(link)}").json()
    assert any(entry["location"] == "Lindenstraße 4" for entry in _entries(page))


def _entries(page: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for value in page.values():
        if isinstance(value, list):
            found += [item for item in value if isinstance(item, dict) and "location" in item]
        elif isinstance(value, dict):
            found += _entries(value)
    return found
