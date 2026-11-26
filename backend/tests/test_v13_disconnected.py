"""verbinden 0.1.4 (V-13): "Trennen" of a connection made by a click clears up at once what nextime kept of it, not
only on its next use: the row and the ticks of nextasks, the row of nexpaper, the birthday calendar of nexfolk. The
partner hearing the end is the module's own contract (``tests/nexverbinden``, ``disconnected()``)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, Calendar, NexpaperConnection, NextasksConnection, Source, TaskTick
from app.vendor.nexverbinden import reach

from . import test_nexpaper as paper_tests
from .test_time25 import Fake, fake, tasks, tick
from .test_v18_guest import ACCESS, click_connection, paper
from .test_v18_nexfolk import folk

#: The fakes of nextasks, nexpaper and nexfolk, used here as fixtures.
FIXTURES = (fake, paper, folk)


@pytest.fixture
def partner_hears(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The partner takes the revocation (``POST /api/connect/revoke``)."""
    heard: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        heard.append(request.url.path)
        return httpx.Response(200, json={})

    monkeypatch.setattr(reach, "TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setattr(reach, "RESOLVER", lambda host, port: ["93.184.216.34"])
    return heard


def _end(client: TestClient, partner: str) -> None:
    gone = client.delete(f"/api/connect/partners/{partner}")
    assert gone.status_code == 200 and gone.json()["state"] == "none", gone.text


def test_v13_ending_nextasks_drops_its_row_and_ticks_at_once(client: TestClient, account: Account, fake: Fake,
                                                              partner_hears: list[str]) -> None:
    fake.add("own", "Eigene", "2026-11-26")
    fake.token = ACCESS
    click_connection(account.id, "nextasks", "https://tasks.example.com")
    assert [task["id"] for task in tasks(client, "2026-11-30")["tasks"]] == ["own"]
    assert tick(client, "own", True, "r1").status_code in (200, 202)
    _end(client, "nextasks")
    assert partner_hears == ["/api/connect/revoke"]
    with SessionLocal() as db:
        assert db.get(NextasksConnection, account.id) is None
        assert db.scalars(select(TaskTick).where(TaskTick.account_id == account.id)).all() == []


def test_v13_ending_nexpaper_drops_its_row_at_once(client: TestClient, account: Account, paper: Any,
                                                   monkeypatch: pytest.MonkeyPatch, partner_hears: list[str]) -> None:
    paper.soon = [paper_tests.deadline(3, "2026-12-01")]
    monkeypatch.setattr(paper_tests, "TOKEN", ACCESS)
    click_connection(account.id, "nexpaper", "https://paper.example.com")
    assert client.get("/api/deadlines", params={"to": "2026-12-31"}).json()["connected"] is True
    _end(client, "nexpaper")
    with SessionLocal() as db:
        assert db.get(NexpaperConnection, account.id) is None


def test_v13_ending_nexfolk_drops_the_birthday_calendar_at_once(client: TestClient, account: Account, folk: dict,
                                                                partner_hears: list[str]) -> None:
    from app.services import subscriptions, verbinden_store

    click_connection(account.id, "nexfolk", "https://folk.example.com")
    with SessionLocal() as db:
        assert verbinden_store.calendars_due(db) == 1
        source_id = subscriptions.by_connection(db, account.id, "nexfolk").id
        assert db.scalars(select(Calendar).where(Calendar.source_id == source_id)).all() != []
    _end(client, "nexfolk")
    with SessionLocal() as db:
        assert db.get(Source, source_id) is None
        assert db.scalars(select(Calendar).where(Calendar.source_id == source_id)).all() == []


def test_v12_the_box_in_the_card_and_nextime_s_own_switch_show_one_value(client: TestClient, operator: Account) -> None:
    """verbinden 0.1.4 (V-12): "Verbindungen ins eigene Netz" in the card "Andere nex-Apps" is the setting of nextime's
    switch under Server (``connections_local_network``); set on one side, the other shows it."""
    assert client.get("/api/connect/admin").json()["local"] is False
    assert client.put("/api/connect/admin", json={"local": True}).status_code == 200
    assert client.get("/api/settings").json()["connections_local_network"] is True
    assert client.put("/api/settings", json={"connections_local_network": False}).status_code == 200
    assert client.get("/api/connect/admin").json()["local"] is False
