"""TIME-27 the nexdeck card: ``GET /api/v1/dashboard`` with a key gives today and tomorrow of the key's calendars in
the zone of its account: the running or next event, the rest of today, tomorrow, background titles, "nothing more"
with the first one tomorrow; "busy only" without title, place or link; a long event once; the name of the card from the
one calendar or the key; revoked keys and a switched-off API give nothing."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import RIGHT_BUSY, Account
from app.services import apiv1, display, settings_service

from .calendar_helpers import make_calendar, make_event, share
from .conftest import make_account, new_client


@pytest.fixture
def program() -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as found:
        yield found


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime]:
    """Thursday 26 November 2026, 16:05 in Berlin."""
    now = {"at": datetime(2026, 11, 26, 15, 5, tzinfo=UTC)}
    monkeypatch.setattr(apiv1, "now", lambda: now["at"])
    return now


def zone(account: Account, name: str = "Europe/Berlin") -> None:
    """The account's zone, and German as its language (the card's words follow it)."""
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        row.language = "de"
        display.save(db, row, {"time_zone": name})


def key(client: TestClient, name: str = "nexdeck Eingang", **values: Any) -> str:
    answer = client.post("/api/keys", json={"name": name, "level": "read", **values})
    assert answer.status_code == 201, answer.text
    return answer.json()["token"]


def card(program: TestClient, token: str) -> dict[str, Any]:
    answer = program.get("/api/v1/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert answer.status_code == 200, answer.text
    return answer.json()


def titles(items: list[dict[str, Any]]) -> list[str]:
    return [item["title"] for item in items]


def test_time27_ac1_ac7_ec1_a_key_for_one_calendar_names_the_card_after_it_and_sees_only_it(
        client: TestClient, account: Account, program: TestClient, clock: dict[str, datetime]) -> None:
    zone(account)
    familie = make_calendar(client, "Familie", color="green")
    arbeit = make_calendar(client, "Arbeit", color="teal")
    make_event(client, "Elternabend", start="2026-11-26T19:00", end="2026-11-26T20:00", calendar_id=familie)
    make_event(client, "Teamrunde", start="2026-11-26T18:00", end="2026-11-26T18:30", calendar_id=arbeit)
    found = card(program, key(client, calendar_ids=[familie]))
    assert found["name"] == "Familie" and found["color"] == "#2F7D4F"
    assert found["next"]["title"] == "Elternabend" and found["today_events"] == []
    assert found["next"]["color"] == "#2F7D4F"
    assert "Teamrunde" not in str(found)
    # A key for all calendars: the key's name, nextime's color.
    every = card(program, key(client, "Flur"))
    assert every["name"] == "Flur" and every["color"] == "#4A6491"
    assert every["next"]["title"] == "Teamrunde" and titles(every["today_events"]) == ["Elternabend"]


def test_time27_ac2_ac3_next_running_then_the_rest_and_tomorrow_background_small_links_into_nextime(
        client: TestClient, account: Account, program: TestClient, clock: dict[str, datetime]) -> None:
    zone(account)
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://time.example.com"})
    familie = make_calendar(client, "Familie")
    muell = make_calendar(client, "Müllabfuhr", background=True)
    make_event(client, "Teamrunde", start="2026-11-26T09:30", end="2026-11-26T10:30", calendar_id=familie)
    zahnarzt = make_event(client, "Zahnarzt", start="2026-11-26T16:00", end="2026-11-26T16:45", calendar_id=familie,
                          location="Lindenstraße 4")
    make_event(client, "Training", start="2026-11-26T18:30", end="2026-11-26T20:00", calendar_id=familie)
    make_event(client, "Werkstatt", start="2026-11-27T08:15", end="2026-11-27T09:00", calendar_id=familie)
    make_event(client, "Ausflug", start="2026-11-27", end="2026-11-27", all_day=True, calendar_id=familie)
    make_event(client, "Gelbe Tonne", start="2026-11-26", end="2026-11-26", all_day=True, calendar_id=muell)
    found = card(program, key(client))
    nxt = found["next"]
    assert nxt["title"] == "Zahnarzt" and nxt["state"] == "running" and nxt["minutes"] == 40
    assert nxt["start_time"] == "16:00" and nxt["end_time"] == "16:45" and nxt["location"] == "Lindenstraße 4"
    assert nxt["link"] == f"https://time.example.com/day?day=2026-11-26&event={zahnarzt['id']}"
    assert titles(found["today_events"]) == ["Training"]
    assert titles(found["tomorrow_events"]) == ["Ausflug", "Werkstatt"]
    assert found["background"] == {"today": ["Gelbe Tonne"], "tomorrow": []}
    assert found["nothing_more"] is False and found["next_tomorrow"] is None
    assert found["today"] == "2026-11-26" and found["time_zone"] == "Europe/Berlin"
    # Coming instead of running a minute after it ended.
    clock["at"] = datetime(2026, 11, 26, 15, 46, tzinfo=UTC)
    later = card(program, key(client, "Zweiter"))
    assert later["next"]["title"] == "Training" and later["next"]["state"] == "coming"
    assert later["next"]["minutes"] == 104


def test_time27_ac5_ec3_nothing_more_today_names_the_first_tomorrow_and_an_empty_card_says_so(
        client: TestClient, account: Account, program: TestClient, clock: dict[str, datetime]) -> None:
    zone(account)
    familie = make_calendar(client, "Familie")
    token = key(client)
    empty = card(program, token)
    assert empty["next"] is None and empty["today_events"] == [] and empty["tomorrow_events"] == []
    assert empty["nothing_more"] is True and empty["next_tomorrow"] is None
    make_event(client, "Training", start="2026-11-26T18:30", end="2026-11-26T20:00", calendar_id=familie)
    make_event(client, "Werkstatt", start="2026-11-27T08:15", end="2026-11-27T09:00", calendar_id=familie)
    make_event(client, "Weit weg", start="2026-12-10T08:15", end="2026-12-10T09:00", calendar_id=familie)
    clock["at"] = datetime(2026, 11, 26, 21, 40, tzinfo=UTC)
    late = card(program, token)
    assert late["next"] is None and late["nothing_more"] is True
    assert late["next_tomorrow"]["title"] == "Werkstatt" and late["next_tomorrow"]["start_time"] == "08:15"
    assert "Weit weg" not in str(late)


def test_time27_ec4_a_holiday_running_today_stands_once_with_its_day(client: TestClient, account: Account,
                                                                    program: TestClient,
                                                                    clock: dict[str, datetime]) -> None:
    zone(account)
    familie = make_calendar(client, "Familie")
    make_event(client, "Urlaub", start="2026-11-24", end="2026-11-30", all_day=True, calendar_id=familie)
    found = card(program, key(client))
    assert titles(found["today_events"]) == ["Urlaub"]
    assert found["today_events"][0]["day"] == 3 and found["today_events"][0]["days"] == 7
    assert found["tomorrow_events"] == [] and found["next"] is None


def test_time27_ec5_after_midnight_tomorrow_becomes_today(client: TestClient, account: Account, program: TestClient,
                                                         clock: dict[str, datetime]) -> None:
    zone(account)
    familie = make_calendar(client, "Familie")
    make_event(client, "Werkstatt", start="2026-11-27T08:15", end="2026-11-27T09:00", calendar_id=familie)
    token = key(client)
    clock["at"] = datetime(2026, 11, 26, 22, 59, tzinfo=UTC)
    assert titles(card(program, token)["tomorrow_events"]) == ["Werkstatt"]
    clock["at"] = datetime(2026, 11, 26, 23, 1, tzinfo=UTC)
    after = card(program, token)
    assert after["today"] == "2026-11-27" and after["next"]["title"] == "Werkstatt"
    assert after["tomorrow_events"] == []


def test_time27_ec7_ec10_busy_only_without_title_place_or_link_titles_stay_text(client: TestClient, account: Account,
                                                                               program: TestClient,
                                                                               clock: dict[str, datetime]) -> None:
    zone(account)
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://time.example.com"})
    anna = make_account("anna")
    with new_client(anna) as browser:
        privat = make_calendar(browser, "Annas")
        make_event(browser, "Geheim", start="2026-11-26T18:00", end="2026-11-26T19:00", calendar_id=privat,
                   location="Praxis")
    share(privat, account.id, RIGHT_BUSY)
    eigen = make_calendar(client, "Eigen")
    make_event(client, "<script>alert(1)</script> <b>javascript:alert(2)</b>", start="2026-11-26T20:00",
               end="2026-11-26T21:00", calendar_id=eigen)
    found = card(program, key(client))
    busy = found["next"]
    assert busy["title"] == "Belegt" and busy["location"] == "" and busy["id"] is None and busy["link"] == ""
    assert "Geheim" not in str(found) and "Praxis" not in str(found)
    # As text, as it was written: nexdeck draws it as text (EC-10).
    assert found["today_events"][0]["title"] == "<script>alert(1)</script> <b>javascript:alert(2)</b>"


def test_time27_ec8_a_revoked_key_or_the_api_switched_off_gives_nothing(client: TestClient, account: Account,
                                                                        program: TestClient,
                                                                        clock: dict[str, datetime]) -> None:
    zone(account)
    familie = make_calendar(client, "Familie")
    make_event(client, "Training", start="2026-11-26T18:30", end="2026-11-26T20:00", calendar_id=familie)
    made = client.post("/api/keys", json={"name": "Eingang", "level": "read"}).json()
    headers = {"Authorization": f"Bearer {made['token']}"}
    with SessionLocal() as db:
        settings_service.save(db, {"api_enabled": False})
    off = program.get("/api/v1/dashboard", headers=headers)
    assert off.status_code == 403 and "Training" not in off.text
    with SessionLocal() as db:
        settings_service.save(db, {"api_enabled": True})
    assert program.get("/api/v1/dashboard", headers=headers).status_code == 200
    assert client.delete(f"/api/keys/{made['made']['id']}").status_code == 204
    gone = program.get("/api/v1/dashboard", headers=headers)
    assert gone.status_code == 401 and "Training" not in gone.text


def test_time27_ec11_today_and_times_in_the_zone_of_the_account(client: TestClient, account: Account,
                                                               program: TestClient,
                                                               clock: dict[str, datetime]) -> None:
    zone(account, "America/New_York")
    familie = make_calendar(client, "Familie")
    # 26 November, 23:30 in Berlin is 17:30 in New York, still the 26th there.
    make_event(client, "Spät", start="2026-11-26T23:30", end="2026-11-26T23:45", calendar_id=familie)
    clock["at"] = datetime(2026, 11, 26, 22, 0, tzinfo=UTC)
    found = card(program, key(client))
    assert found["today"] == "2026-11-26" and found["time_zone"] == "America/New_York"
    assert found["next"]["title"] == "Spät" and found["next"]["start_time"] == "17:30"


def test_time27_ac1_the_dashboard_is_named_among_the_features(client: TestClient, account: Account,
                                                              program: TestClient) -> None:
    token = key(client)
    me = program.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert "dashboard" in me["features"]


def test_time27_ac5_ec7_a_whole_day_alone_is_all_day_and_holds_nothing_off_busy_background_left_out(
        client: TestClient, account: Account, program: TestClient, clock: dict[str, datetime]) -> None:
    """After the review (2026-11-23)."""
    zone(account)
    familie = make_calendar(client, "Familie")
    make_event(client, "Ausflug", start="2026-11-26", end="2026-11-26", all_day=True, calendar_id=familie)
    make_event(client, "Werkstatt", start="2026-11-27T08:15", end="2026-11-27T09:00", calendar_id=familie)
    anna = make_account("anna")
    with new_client(anna) as browser:
        muell = make_calendar(browser, "Annas Müll", background=True)
        make_event(browser, "Gelbe Tonne geheim", start="2026-11-26", end="2026-11-26", all_day=True,
                   calendar_id=muell, busy=True)
    share(muell, account.id, RIGHT_BUSY)
    found = card(program, key(client))
    assert found["today_events"][0]["title"] == "Ausflug" and found["today_events"][0]["state"] == "all_day"
    assert "day" not in found["today_events"][0]
    assert found["nothing_more"] is True and found["next_tomorrow"]["title"] == "Werkstatt"
    assert found["background"]["today"] == []
