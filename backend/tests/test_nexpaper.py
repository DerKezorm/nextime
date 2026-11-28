"""nexpaper in the calendar: connecting the member's own nexpaper (a key that reads, a nexpaper with deadlines, the key
sealed and never answered) and its open deadlines as entries of the views, with the link to the document. The other
side is a stand-in of nexpaper's ``/api/v1`` behind ``httpx.MockTransport``; nothing reaches the network."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models import Account
from app.services import nexpaper

TOKEN = "npk_richtig-und-geheim-1234567890"
PUBLIC = "93.184.216.34"
#: The day of the stand-in (its ``today``): the views ask from it, not from the day the tests run.
TODAY = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)


def deadline(document_id: int, day: str, what: str = "pay", *, kind: str = "due", status: str = "filed",
             title: str = "Rechnung Abrechnung 10/2026", **more: Any) -> dict[str, Any]:
    return {"document_id": document_id, "title": title, "status": status, "vault_id": 1, "via_share": False,
            "sender": "Stadtwerke", "amount": 12345, "currency": "EUR", "kind": kind, "what": what,
            "note": more.pop("note", None), "date": day, "days": 0, "end": day, "notice": None, "renew_months": None,
            "renews_to": None, "missed": None, "done": None, "snoozed_until": None, **more}


class Fake:
    """nexpaper as far as nextime uses it: ``/me`` and ``/deadlines``."""

    def __init__(self) -> None:
        self.scope = "read"
        self.version = "1.10.0"
        self.overdue: list[dict[str, Any]] = []
        self.soon: list[dict[str, Any]] = []
        self.later: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    @staticmethod
    def refuse(status: int, code: str) -> httpx.Response:
        return httpx.Response(status, json={"detail": {"code": code, "message": code}})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.headers.get("origin"):
            return self.refuse(403, "origin_refused")
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return self.refuse(401, "key_invalid")
        tail = request.url.path.split("/api/v1/", 1)[-1]
        if tail == "me":
            return httpx.Response(200, json={"name": "tester", "display_name": "Tester", "scope": self.scope,
                                             "version": self.version})
        if tail == "deadlines":
            section = lambda items: {"total": len(items), "items": items}
            return httpx.Response(200, json={"today": "2026-11-26", "soon_days": 14, "overdue": section(self.overdue),
                                             "soon": section(self.soon), "later": section(self.later), "done": None,
                                             "counts": {}, "shared": 0})
        return self.refuse(404, "not_found")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[Fake]:
    made = Fake()
    monkeypatch.setattr(nexpaper, "TRANSPORT", httpx.MockTransport(made))
    monkeypatch.setattr(nexpaper, "RESOLVER", lambda host, port: [PUBLIC])
    monkeypatch.setattr(nexpaper, "clock", lambda: TODAY)
    yield made


def connect(client: TestClient, url: str = "https://paper.example.com") -> httpx.Response:
    return client.put("/api/nexpaper", json={"url": url, "token": TOKEN})


def test_nexpaper_connecting_wants_a_key_that_reads_and_a_nexpaper_with_deadlines(
        client: TestClient, account: Account, fake: Fake) -> None:
    fake.scope = "upload"
    answer = connect(client)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "nexpaper_key_scope"
    fake.scope, fake.version = "read", "1.3.0"
    answer = connect(client)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "nexpaper_too_old"
    assert client.get("/api/nexpaper").json()["connected"] is False
    fake.version = "1.10.0"
    answer = connect(client, "  https://paper.example.com/  ")
    assert answer.status_code == 200, answer.text
    shown = answer.json()
    assert shown["connected"] is True and shown["url"] == "https://paper.example.com" and shown["version"] == "1.10.0"
    # The key never comes back, and no request carries an Origin.
    assert TOKEN not in answer.text and TOKEN not in client.get("/api/nexpaper").text
    assert all(path == "/api/v1/me" for _method, path, _params in fake.calls)


def test_nexpaper_deadlines_become_entries_with_the_link_to_the_document(
        client: TestClient, account: Account, fake: Fake, caplog: pytest.LogCaptureFixture) -> None:
    assert connect(client).status_code == 200
    fake.overdue = [deadline(7, "2026-11-20", "cancel", kind="contract_end", status="inbox", title="Handyvertrag")]
    fake.soon = [deadline(3, "2026-12-01"),
                 deadline(9, "2026-12-03", "follow_up", kind="follow_up", note="Anrufen", amount=None,
                          url="https://paper.example.com/dokument/9?from=deadlines"),
                 # A link to another host is not taken: nextime builds the document's own.
                 deadline(4, "2026-12-05", "warranty", kind="warranty_end", url="https://evil.example.net/x")]
    fake.later = [{"document_id": "kaputt", "what": "pay", "kind": "due", "date": "2026-12-09"}]
    with caplog.at_level("INFO"):
        answer = client.get("/api/deadlines", params={"to": "2026-12-31"})
    assert answer.status_code == 200, answer.text
    found = answer.json()
    assert found["connected"] is True and found["problem"] is None
    rows = {row["id"]: row for row in found["deadlines"]}
    assert list(rows) == ["7-contract_end", "3-due", "9-follow_up", "4-warranty_end"]
    assert rows["7-contract_end"]["link"] == "https://paper.example.com/eingang/7"
    assert rows["3-due"] == {"id": "3-due", "document_id": 3, "kind": "due", "what": "pay",
                             "title": "Rechnung Abrechnung 10/2026", "sender": "Stadtwerke", "amount": 12345,
                             "currency": "EUR", "note": "", "day": "2026-12-01", "status": "filed",
                             "link": "https://paper.example.com/dokument/3"}
    assert rows["9-follow_up"]["note"] == "Anrufen" and rows["9-follow_up"]["amount"] is None
    assert rows["9-follow_up"]["link"] == "https://paper.example.com/dokument/9?from=deadlines"
    assert rows["4-warranty_end"]["link"] == "https://paper.example.com/dokument/4"
    # From a day on: no overdue ones; one answer of nexpaper serves both.
    later = client.get("/api/deadlines", params={"from": "2026-12-02", "to": "2026-12-31"}).json()
    assert [row["id"] for row in later["deadlines"]] == ["9-follow_up", "4-warranty_end"]
    assert [path for _m, path, _p in fake.calls].count("/api/v1/deadlines") == 1
    # Titles, senders and the key stay out of the log.
    assert "Handyvertrag" not in caplog.text and "Stadtwerke" not in caplog.text and TOKEN not in caplog.text


def test_nexpaper_away_is_a_quiet_problem_and_another_account_sees_nothing(
        client: TestClient, account: Account, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    assert connect(client).status_code == 200

    def away(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nobody there")

    monkeypatch.setattr(nexpaper, "TRANSPORT", httpx.MockTransport(away))
    found = client.get("/api/deadlines", params={"to": "2026-12-31"}).json()
    assert found["deadlines"] == [] and found["problem"] == "nexpaper_unreachable"
    assert client.patch("/api/nexpaper", json={"hidden": True}).json()["hidden"] is True
    assert client.delete("/api/nexpaper").status_code == 204
    assert client.get("/api/deadlines", params={"to": "2026-12-31"}).json() == {
        "connected": False, "hidden": False, "problem": None, "deadlines": []}
