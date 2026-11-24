"""Events in the month with a dot or tinted (Einstellungen › Darstellung, asked for while testing, 2026-11-24): kept
with the account, a dot from the start, nothing else taken."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import Account


def test_month_style_is_a_dot_from_the_start_tint_is_kept_and_nothing_else(client: TestClient, account: Account) -> None:
    assert client.get("/api/auth/me").json()["display"]["month_style"] == "dot"
    assert client.put("/api/me/display", json={"month_style": "tint"}).status_code == 200
    assert client.get("/api/auth/me").json()["display"]["month_style"] == "tint"
    assert client.put("/api/me/display", json={"month_style": "stripes"}).status_code == 422
    assert client.get("/api/auth/me").json()["display"]["month_style"] == "tint"
