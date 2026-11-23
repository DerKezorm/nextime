"""TIME-4 custom colour (decided while testing, 2026-11-23, as nexnotes' "Eigene Farbe"): besides the eleven colors a
calendar takes an own one as exactly six hex digits, kept in small letters; anything else is refused and nothing
changes. A calendar always has a color: there is no "without". The device side is in ``test_time20.py``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import Account

from .calendar_helpers import make_calendar, named


def test_time4_custom_colour_is_saved_as_rrggbb(client: TestClient, account: Account) -> None:
    made = client.post("/api/calendars", json={"name": "Garten", "color": "#1E3A8A"})
    assert made.status_code == 201, made.text
    assert named(client, "Garten")["color"] == "#1e3a8a"
    calendar_id = named(client, "Garten")["id"]
    assert client.patch(f"/api/calendars/{calendar_id}", json={"color": "#a1B2c3"}).status_code == 200
    assert named(client, "Garten")["color"] == "#a1b2c3"
    # Back to one of the eleven by its name.
    assert client.patch(f"/api/calendars/{calendar_id}", json={"color": "teal"}).status_code == 200
    assert named(client, "Garten")["color"] == "teal"


@pytest.mark.parametrize("color", ["#12345", "#1234567", "123456", "#12345g", "", "#fff", "rgb(1,2,3)", "red2",
                                   "#1e3a8a "])
def test_time4_custom_colour_only_six_hex_digits(client: TestClient, account: Account, color: str) -> None:
    calendar_id = make_calendar(client, "Garten", color="green")
    refused = client.patch(f"/api/calendars/{calendar_id}", json={"color": color})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "invalid_color"
    assert named(client, "Garten")["color"] == "green"
    made = client.post("/api/calendars", json={"name": "Neu", "color": color})
    assert made.status_code == 422 and made.json()["detail"]["code"] == "invalid_color"
