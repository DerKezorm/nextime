"""TIME-3 Languages and appearance, the server's part: the account's language and its way of seeing times (first day
of the week, week numbers, clock, time zone) travel to every device; the home zone is the first device's."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import Account
from app.services import display

from .conftest import make_account, new_client


def _code(answer: object) -> str:
    return str(answer.json()["detail"]["code"])  # type: ignore[attr-defined]


def test_time3_ac2_ac3_ac8_a_new_account_starts_on_monday_with_week_numbers_and_the_clock_of_its_language(
    client: TestClient, operator: Account
) -> None:
    shown = client.get("/api/auth/me").json()["display"]
    assert shown["week_start"] == 1 and shown["week_numbers"] is True
    assert shown["clock"] == "auto" and shown["time_zone"] == ""


@pytest.mark.parametrize("day", range(7))
def test_time3_ac2_every_day_of_the_week_can_begin_it(client: TestClient, operator: Account, day: int) -> None:
    assert client.put("/api/me/display", json={"week_start": day}).json()["display"]["week_start"] == day


def test_time3_ac3_week_numbers_switch_off_and_on(client: TestClient, operator: Account) -> None:
    assert client.put("/api/me/display", json={"week_numbers": False}).json()["display"]["week_numbers"] is False
    assert client.put("/api/me/display", json={"week_numbers": True}).json()["display"]["week_numbers"] is True


def test_time3_ac8_the_clock_is_fixed_to_24_or_12_hours_or_follows_the_language(client: TestClient,
                                                                                 operator: Account) -> None:
    for clock in ("12", "24", "auto"):
        assert client.put("/api/me/display", json={"clock": clock}).json()["display"]["clock"] == clock


def test_time3_ac6_a_fixed_time_zone_counts_until_it_is_lifted(client: TestClient, operator: Account) -> None:
    fixed = client.put("/api/me/display", json={"time_zone": "America/New_York"}).json()["display"]
    assert fixed["time_zone"] == "America/New_York"
    assert client.put("/api/me/display", json={"time_zone": ""}).json()["display"]["time_zone"] == ""


@pytest.mark.parametrize("change", [
    {"week_start": 7}, {"week_start": -1}, {"week_start": True}, {"week_numbers": "yes"}, {"clock": "13"},
    {"time_zone": "Mars/Olympus_Mons"}, {"time_zone": "../../etc/passwd"},
])
def test_time3_ac7_choices_that_do_not_exist_are_refused_and_change_nothing(client: TestClient, operator: Account,
                                                                            change: dict) -> None:
    refused = client.put("/api/me/display", json=change)
    assert refused.status_code == 422
    assert client.get("/api/auth/me").json()["display"] == display.DEFAULTS


def test_time3_ac11_the_first_devices_zone_becomes_the_home_zone_and_later_ones_change_nothing(
    client: TestClient, operator: Account
) -> None:
    assert client.get("/api/auth/me", headers={"X-Nextime-Zone": "Europe/Berlin"}).json()["display"]["home_zone"] == \
        "Europe/Berlin"
    travelling = client.get("/api/auth/me", headers={"X-Nextime-Zone": "America/New_York"}).json()["display"]
    assert travelling["home_zone"] == "Europe/Berlin"


def test_time3_ac11_an_unknown_zone_from_a_device_is_ignored(client: TestClient, operator: Account) -> None:
    assert client.get("/api/auth/me", headers={"X-Nextime-Zone": "Nowhere/Here"}).json()["display"]["home_zone"] == ""
    assert client.get("/api/auth/me", headers={"X-Nextime-Zone": "Asia/Kolkata"}).json()["display"]["home_zone"] == \
        "Asia/Kolkata"


def test_time3_ec7_choices_count_for_the_account_on_every_device(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as phone, new_client(anna) as laptop:
        phone.put("/api/me/display", json={"week_start": 0, "week_numbers": False, "clock": "12",
                                           "time_zone": "Asia/Kolkata"})
        phone.put("/api/me/language", json={"language": "de"})
        seen = laptop.get("/api/auth/me").json()
    assert seen["display"]["week_start"] == 0 and seen["display"]["week_numbers"] is False
    assert seen["display"]["clock"] == "12" and seen["display"]["time_zone"] == "Asia/Kolkata"
    assert seen["language"] == "de"
    # Another account keeps its own.
    assert client.get("/api/auth/me").json()["display"]["week_start"] == 1


def test_time3_ac1_the_language_is_one_nextime_has_or_follows_the_browser(client: TestClient,
                                                                         operator: Account) -> None:
    assert client.put("/api/me/language", json={"language": "en"}).json()["language"] == "en"
    assert client.put("/api/me/language", json={"language": ""}).json()["language"] == ""
    refused = client.put("/api/me/language", json={"language": "xx"})
    assert refused.status_code == 422 and _code(refused) == "unknown_language"
