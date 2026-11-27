"""V18 (plan "Apps verbinden"): every use nextime makes of nextasks and nexpaper over a connection typed in by hand also
runs over a connection made by a click (the shared module verbinden), with the same behaviour: tasks with their
choices and ticks, deadlines, the states of a refusal. The same uses once over the old, once over the click
connection, against the fakes of ``test_time25.py`` and ``test_nexpaper.py``. The click connection is the module's
row with the partner's tokens, as ``connect.come_back`` leaves it (the partner side is the module's own contract,
``tests/nexverbinden``).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account, NexpaperConnection, NextasksConnection
from app.services import nexpaper as nexpaper_service
from app.services import verbinden_store
from app.vendor import nexverbinden
from app.vendor.nexverbinden import connect as verbinden_connect

from . import test_nexpaper as paper_tests
from .test_time25 import PUBLIC, Fake, connect, fake, tasks, tick

#: The fake nextasks of TIME-25, used here as a fixture.
FIXTURES = (fake,)

ACCESS = "ntc_" + "a" * 40


def click_connection(account_id: int, partner: str, base: str, access: str = ACCESS, *,
                     expires: datetime | None = None) -> None:
    """The module's row of a connection nextime made by a click with ``partner``: what nextime holds of it (sealed)."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        store = verbinden_store.Store(db)
        # A refresh token of its own each time: since verbinden 0.2.1 the process remembers the last renewal of a
        # refresh token (``connect.RENEWALS``), and the same one in the next test would bring that test's access back.
        held = verbinden_connect._held(store, access, "ntcr_" + secrets.token_hex(20),
                                       expires or now + timedelta(hours=1), "https://time.example.com")
        store.save(nexverbinden.Connection(
            id=secrets.token_urlsafe(18), account_id=account_id, partner=partner, partner_base=base,
            partner_account="tester", uses=verbinden_store.CONFIG.catalog.uses("nextime", partner), grants=(),
            areas_granted=None, areas_used=None, held=held, issued_access_hash=secrets.token_hex(32),
            issued_refresh_hash=secrets.token_hex(32), issued_access_expires=now + timedelta(hours=1),
            created_at=now))
        store.commit()


@pytest.mark.parametrize("way", ["old", "click"])
def test_v18_tasks_and_ticks_run_alike_over_the_old_and_the_click_connection(
        client: TestClient, account: Account, fake: Fake, way: str) -> None:
    fake.add("own", "Eigene", "2026-11-26")
    fake.add("late", "Überfällig", "2026-11-23")
    if way == "old":
        assert connect(client).status_code == 200
    else:
        fake.token = ACCESS
        click_connection(account.id, "nextasks", "https://tasks.example.com")
    found = tasks(client, "2026-11-30")
    assert [task["id"] for task in found["tasks"]] == ["late", "own"] and found["problem"] is None
    shown = client.get("/api/nextasks").json()
    assert shown["connected"] is True and shown["host"] == "tasks.example.com" and shown["name"] == "Tester"
    assert shown["by_connection"] is (way == "click")
    assert tick(client, "own", True, "r1").status_code in (200, 202)
    # The old connection shows in the module's card as one by API key, the click one as connected.
    card = next(view for view in client.get("/api/connect/partners").json() if view["partner"] == "nextasks")
    assert card["state"] == ("legacy" if way == "old" else "connected")


def test_v18_a_refused_click_connection_says_so_as_a_refused_key(client: TestClient, account: Account,
                                                                 fake: Fake) -> None:
    fake.add("own", "Eigene", "2026-11-26")
    fake.token = "ntc_" + "z" * 40
    click_connection(account.id, "nextasks", "https://tasks.example.com")
    found = tasks(client, "2026-11-30")
    assert found["tasks"] == [] and found["problem"] == "nextasks_token_invalid"


def test_v18_when_the_click_connection_ends_its_row_goes_too(client: TestClient, account: Account, fake: Fake) -> None:
    fake.token = ACCESS
    click_connection(account.id, "nextasks", "https://tasks.example.com")
    assert client.get("/api/nextasks").json()["connected"] is True
    with SessionLocal() as db:
        store = verbinden_store.Store(db)
        store.remove(store.connection(account.id, "nextasks").id)
        store.commit()
    assert client.get("/api/nextasks").json()["connected"] is False
    with SessionLocal() as db:
        assert db.get(NextasksConnection, account.id) is None


@pytest.fixture
def paper(monkeypatch: pytest.MonkeyPatch) -> Any:
    made = paper_tests.Fake()
    monkeypatch.setattr(nexpaper_service, "TRANSPORT", httpx.MockTransport(made))
    monkeypatch.setattr(nexpaper_service, "RESOLVER", lambda host, port: [PUBLIC])
    return made


@pytest.mark.parametrize("way", ["old", "click"])
def test_v18_deadlines_run_alike_over_the_old_and_the_click_connection(
        client: TestClient, account: Account, paper: Any, monkeypatch: pytest.MonkeyPatch, way: str) -> None:
    paper.soon = [paper_tests.deadline(3, "2026-12-01")]
    if way == "old":
        assert paper_tests.connect(client).status_code == 200
    else:
        monkeypatch.setattr(paper_tests, "TOKEN", ACCESS)
        click_connection(account.id, "nexpaper", "https://paper.example.com")
    found = client.get("/api/deadlines", params={"to": "2026-12-31"}).json()
    assert found["connected"] is True and [row["id"] for row in found["deadlines"]] == ["3-due"]
    assert client.get("/api/nexpaper").json()["by_connection"] is (way == "click")
    with SessionLocal() as db:
        assert db.get(NexpaperConnection, account.id) is not None


def test_v18_a_401_asks_once_more_with_a_renewed_access(client: TestClient, account: Account, fake: Fake,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """An access can end before its time: nextasks answers 401, nextime renews the access of the connection once
    (``nexverbinden.access(force=True)``) and asks again; only a second 401 counts as refused."""
    from app.vendor.nexverbinden import reach

    renewed = "ntc_" + "n" * 40
    trades: list[str] = []

    def partner(request: httpx.Request) -> httpx.Response:
        trades.append(request.url.path)
        return httpx.Response(200, json={"access_token": renewed, "refresh_token": "ntcr_" + "m" * 40,
                                         "expires_in": 3600, "token_type": "Bearer"})

    monkeypatch.setattr(reach, "TRANSPORT", httpx.MockTransport(partner))
    monkeypatch.setattr(reach, "RESOLVER", lambda host, port: [PUBLIC])
    fake.add("own", "Eigene", "2026-11-26")
    fake.token = renewed
    click_connection(account.id, "nextasks", "https://tasks.example.com")
    found = tasks(client, "2026-11-30")
    assert [task["id"] for task in found["tasks"]] == ["own"] and found["problem"] is None
    # Renewed at the partner (one renewal per request that met the 401; requests of one view may run side by side).
    assert trades and set(trades) == {"/api/connect/token"}
    before = len(trades)
    # With a key typed in by hand nothing is renewed: a 401 is refused at once, as before.
    fake.token = "ntk_" + "x" * 40
    client.delete("/api/nextasks")
    assert connect(client).status_code == 409
    assert trades.count("/api/connect/token") == before


def _heard_connected(account_id: int, partner: str) -> None:
    """The module's ``on_connected`` of nextime, as it runs after nextime answered the partner's token request."""
    with SessionLocal() as db:
        verbinden_store.connected(verbinden_store.Store(db), account_id, partner)


def test_v18_on_connected_runs_again_and_bears_a_first_refusal_of_nextasks(client: TestClient, account: Account,
                                                                            fake: Fake) -> None:
    """verbinden 0.2.1 (BS6): where nextasks started the connection, nextime's hook runs right after the answer to its
    token request, while nextasks may still be saving its side and refuses the first question. The row stands without
    a problem of the key; the hook again (it may run more than once) keeps the one row and reads who it is."""
    fake.add("own", "Eigene", "2026-11-26")
    fake.token = "ntc_" + "z" * 40
    click_connection(account.id, "nextasks", "https://tasks.example.com")
    _heard_connected(account.id, "nextasks")
    shown = client.get("/api/nextasks").json()
    assert shown["connected"] is True and shown["by_connection"] is True and shown["problem"] is None
    fake.token = ACCESS
    _heard_connected(account.id, "nextasks")
    with SessionLocal() as db:
        assert db.query(NextasksConnection).filter(NextasksConnection.account_id == account.id).count() == 1
    shown = client.get("/api/nextasks").json()
    assert shown["name"] == "Tester" and shown["problem"] is None
    assert [task["id"] for task in tasks(client, "2026-11-30")["tasks"]] == ["own"]


def test_v18_on_connected_bears_a_first_refusal_of_nexpaper(client: TestClient, account: Account, paper: Any,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """The same for nexpaper (verbinden 0.2.1, BS6): a first refusal is no problem of the key, the hook may run again."""
    paper.soon = [paper_tests.deadline(3, "2026-12-01")]
    click_connection(account.id, "nexpaper", "https://paper.example.com")
    _heard_connected(account.id, "nexpaper")
    assert client.get("/api/nexpaper").json()["problem"] is None
    monkeypatch.setattr(paper_tests, "TOKEN", ACCESS)
    _heard_connected(account.id, "nexpaper")
    with SessionLocal() as db:
        assert db.query(NexpaperConnection).filter(NexpaperConnection.account_id == account.id).count() == 1
    found = client.get("/api/deadlines", params={"to": "2026-12-31"}).json()
    assert found["problem"] is None and [row["id"] for row in found["deadlines"]] == ["3-due"]
