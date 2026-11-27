"""verbinden 0.2.0 (B9, V56): a connection by click, "Trennen" and a no on the consent page end nextime's connection
typed in by hand with nextasks or nexpaper (``Store.forget_legacy``). After a click the row stays as the click's with
the choices of before; without one it goes. The contract of the module cannot show this in nextime (its partner there
is nexmail, with which nextime never had a key typed in by hand), so here against the fakes of TIME-25 and nexpaper.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account, NexpaperConnection, NextasksConnection, TaskTick
from app.services import nextasks as nextasks_service
from app.services import verbinden_store

from . import test_nexpaper as paper_tests
from .test_time25 import Fake, connect, fake, tasks, tick
from .test_v18_guest import ACCESS, click_connection, paper

#: The fakes of nextasks and nexpaper, used here as fixtures.
FIXTURES = (fake, paper)


def _card(client: TestClient, partner: str) -> dict[str, Any]:
    return next(view for view in client.get("/api/connect/partners").json() if view["partner"] == partner)


def test_v56_trennen_ends_the_connection_to_nextasks_typed_in_by_hand(client: TestClient, account: Account,
                                                                      fake: Fake) -> None:
    fake.add("own", "Eigene", "2026-11-26")
    assert connect(client).status_code == 200
    assert tick(client, "own", True, "r1").status_code in (200, 202)
    card = _card(client, "nextasks")
    assert card["state"] == "legacy" and card["legacy"]["can_end"] is True
    gone = client.delete("/api/connect/partners/nextasks")
    assert gone.status_code == 200 and gone.json()["state"] == "none" and gone.json()["legacy"] is None
    assert client.get("/api/nextasks").json()["connected"] is False
    with SessionLocal() as db:
        assert db.get(NextasksConnection, account.id) is None
        assert db.query(TaskTick).filter(TaskTick.account_id == account.id).count() == 0
    assert client.delete("/api/connect/partners/nextasks").status_code == 404


def test_v56_trennen_ends_the_connection_to_nexpaper_typed_in_by_hand(client: TestClient, account: Account,
                                                                      paper: Any) -> None:
    assert paper_tests.connect(client).status_code == 200
    assert _card(client, "nexpaper")["state"] == "legacy"
    gone = client.delete("/api/connect/partners/nexpaper")
    assert gone.status_code == 200 and gone.json()["state"] == "none"
    assert client.get("/api/nexpaper").json()["connected"] is False
    with SessionLocal() as db:
        assert db.get(NexpaperConnection, account.id) is None


def test_v56_a_click_takes_the_place_of_the_key_and_keeps_the_choices(client: TestClient, account: Account,
                                                                      fake: Fake) -> None:
    fake.add("own", "Eigene", "2026-11-26")
    assert connect(client).status_code == 200
    with SessionLocal() as db:
        row = db.get(NextasksConnection, account.id)
        assert row is not None
        row.hidden = True
        db.commit()
    # As the module does it after a connection by click made from either side: the click's row, then forget_legacy.
    fake.token = ACCESS
    click_connection(account.id, "nextasks", "https://tasks.example.com")
    with SessionLocal() as db:
        store = verbinden_store.Store(db)
        assert [entry.partner for entry in store.legacy(account.id)] == ["nextasks"]
        store.forget_legacy(account.id, "nextasks")
        store.commit()
        assert store.legacy(account.id) == []
        row = db.get(NextasksConnection, account.id)
        assert row is not None and nextasks_service.by_connection(row) and row.hidden is True
    card = _card(client, "nextasks")
    assert card["state"] == "connected" and card["legacy"] is None
    shown = client.get("/api/nextasks").json()
    assert shown["connected"] is True and shown["by_connection"] is True
    assert [task["id"] for task in tasks(client)["tasks"]] == ["own"]


def test_v56_nothing_else_is_touched(client: TestClient, account: Account, fake: Fake, paper: Any) -> None:
    """Another partner, a row of a click and an account without a connection stay as they are."""
    assert connect(client).status_code == 200
    with SessionLocal() as db:
        store = verbinden_store.Store(db)
        store.forget_legacy(account.id, "nexmail")
        store.forget_legacy(account.id, "nexpaper")
        store.forget_legacy(account.id + 1000, "nextasks")
        store.commit()
        assert [entry.partner for entry in store.legacy(account.id)] == ["nextasks"]
    with SessionLocal() as db:
        db.delete(db.get(NextasksConnection, account.id))
        db.commit()
    fake.token = ACCESS
    click_connection(account.id, "nextasks", "https://tasks.example.com")
    assert client.get("/api/nextasks").json()["by_connection"] is True
    with SessionLocal() as db:
        store = verbinden_store.Store(db)
        store.forget_legacy(account.id, "nextasks")
        store.commit()
        row = db.get(NextasksConnection, account.id)
        assert row is not None and nextasks_service.by_connection(row)
