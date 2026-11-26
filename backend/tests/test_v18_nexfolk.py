"""Birthdays from nexfolk over a connection made by a click (V1 8a, V18): ``GET /api/birthdays`` asks nexfolk's
``/api/v1/birthdays`` with the connection's token, through the module's way out (``nexverbinden.call``); nothing
without a connection; a refusal is a quiet problem, never an error of the page."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models import Account
from app.vendor.nexverbinden import reach

from .test_v18_guest import ACCESS, click_connection

OCCASIONS = [
    {"date": "2026-11-28", "contact": "c2", "book": "b1", "name": "Mara", "kind": "anniversary", "label": "Hochzeitstag",
     "years": 5},
    {"date": "2026-11-27", "contact": "c1", "book": "b1", "name": "Kim", "kind": "birthday", "label": "", "years": 40},
    {"date": "kaputt", "name": "Niemand"},
]


@pytest.fixture
def folk(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"calls": [], "token": ACCESS}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["calls"].append((request.method, request.url.path, dict(request.url.params)))
        if request.headers.get("authorization") != f"Bearer {seen['token']}":
            return httpx.Response(401, json={"detail": {"code": "token_invalid", "message": "No."}})
        return httpx.Response(200, json={"from": "2026-11-26", "to": "2026-12-09", "occasions": OCCASIONS})

    monkeypatch.setattr(reach, "TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setattr(reach, "RESOLVER", lambda host, port: ["93.184.216.34"])
    return seen


def test_without_a_connection_there_are_no_birthdays(client: TestClient, account: Account, folk: dict) -> None:
    assert client.get("/api/birthdays").json() == {"connected": False, "birthdays": [], "problem": None}
    assert folk["calls"] == []


def test_the_birthdays_come_over_the_connection_soonest_first(client: TestClient, account: Account,
                                                               folk: dict) -> None:
    click_connection(account.id, "nexfolk", "https://folk.example.com")
    found = client.get("/api/birthdays").json()
    assert found["connected"] is True and found["problem"] is None
    assert [(one["day"], one["name"], one["kind"], one["years"]) for one in found["birthdays"]] == [
        ("2026-11-27", "Kim", "birthday", 40), ("2026-11-28", "Mara", "anniversary", 5)]
    assert folk["calls"] == [("GET", "/api/v1/birthdays", {"days": "14"})]
    # One answer serves a minute.
    client.get("/api/birthdays")
    assert len(folk["calls"]) == 1


def test_a_refused_connection_is_a_quiet_problem(client: TestClient, account: Account, folk: dict) -> None:
    folk["token"] = "ntc_" + "z" * 40
    click_connection(account.id, "nexfolk", "https://folk.example.com")
    found = client.get("/api/birthdays")
    assert found.status_code == 200 and found.json()["birthdays"] == []
    assert found.json()["problem"] == "nexfolk_refused"
