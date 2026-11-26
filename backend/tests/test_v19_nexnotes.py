"""A note to an event in nexnotes over a connection made by a click (V19, pair nextime → nexnotes, ``notes.add``,
draft): nexnotes' own ``POST /api/v1/notes`` with the event's title, time, place and notes and a reference of its own;
nothing without a connection; a refusal of nexnotes is said, never a note from a calendar shown as "busy" only."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models import Account
from app.vendor.nexverbinden import reach

from .calendar_helpers import make_event
from .test_v18_guest import ACCESS, click_connection


@pytest.fixture
def notes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"calls": [], "token": ACCESS}

    def handle(request: httpx.Request) -> httpx.Response:
        raw = request.read()
        if request.url.path != "/api/v1/notes":
            # The module renews the access once when it is refused (a form to /api/connect/token): refused too.
            seen["calls"].append((request.method, request.url.path, None))
            return httpx.Response(400, json={"error": "invalid_grant"})
        seen["calls"].append((request.method, request.url.path, json.loads(raw or b"null")))
        if request.headers.get("authorization") != f"Bearer {seen['token']}":
            return httpx.Response(401, json={"detail": {"code": "token_invalid", "message": "No."}})
        return httpx.Response(201, json={"id": "n1", "title": "x"})

    monkeypatch.setattr(reach, "TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setattr(reach, "RESOLVER", lambda host, port: ["93.184.216.34"])
    return seen


def test_without_a_connection_there_is_no_note(client: TestClient, account: Account, notes: dict) -> None:
    event = make_event(client, "Zahnarzt", location="Praxis Weiß")
    assert client.get("/api/nexnotes").json() == {"connected": False}
    refused = client.post(f"/api/events/{event['id']}/nexnotes")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "nexnotes_not_connected"
    assert notes["calls"] == []


def test_the_note_goes_to_nexnotes_with_the_event(client: TestClient, account: Account, notes: dict) -> None:
    event = make_event(client, "Zahnarzt", location="Praxis Weiß", notes="Karte mitnehmen")
    click_connection(account.id, "nexnotes", "https://notes.example.org")
    assert client.get("/api/nexnotes").json() == {"connected": True}
    made = client.post(f"/api/events/{event['id']}/nexnotes")
    assert made.status_code == 200, made.text
    assert made.json() == {"made": True, "title": "Zahnarzt"}
    (method, path, body), = notes["calls"]
    assert (method, path) == ("POST", "/api/v1/notes")
    assert body["title"] == "Zahnarzt" and body["external_ref"] == f"nextime:{account.id}:{event['id']}:"
    assert "2026-11-26 18:30" in body["markdown"] and "Praxis Weiß" in body["markdown"]
    assert "Karte mitnehmen" in body["markdown"]


def test_a_refusal_of_nexnotes_is_said(client: TestClient, account: Account, notes: dict) -> None:
    event = make_event(client, "Zahnarzt")
    notes["token"] = "ntc_" + "z" * 40
    click_connection(account.id, "nexnotes", "https://notes.example.org")
    refused = client.post(f"/api/events/{event['id']}/nexnotes")
    assert refused.status_code == 502 and refused.json()["detail"]["code"] == "nexnotes_refused"
