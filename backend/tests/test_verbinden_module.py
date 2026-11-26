"""The shared module verbinden in nextime (bauplaene/verbinden 0.1.1): what its contract tests (``tests/nexverbinden``,
nextime as the host of nexmail) do not hold for nextime itself.

- The connections typed in by hand before the module (nextasks, nexpaper) stay in their tables and on their routes
  (``GET/PUT /api/nextasks``, ``GET/PUT /api/nexpaper``) and show in the module's cards as connections by API key.
- The description ``/.well-known/nex-app`` names nextime and its partners' places.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account, NexpaperConnection, NextasksConnection
from app.services import vault


def test_connections_typed_in_before_show_as_by_api_key_and_their_routes_stay(client: TestClient,
                                                                               account: Account) -> None:
    with SessionLocal() as db:
        db.add(NextasksConnection(account_id=account.id, url="https://tasks.example.com",
                                  token_sealed=vault.seal({"token": "ntk_AbCdEfGh" + "x" * 30},
                                                          f"nextasks:{account.id}"),
                                  name="tester", level="write"))
        db.add(NexpaperConnection(account_id=account.id, url="https://paper.example.com",
                                  token_sealed=vault.seal({"token": "npk_WxYzAbCd" + "y" * 30},
                                                          f"nexpaper:{account.id}"),
                                  name="tester"))
        db.commit()
    cards = {view["partner"]: view for view in client.get("/api/connect/partners").json()}
    assert set(cards) == {"nextasks", "nexpaper", "nexmail", "nexfolk", "nexdiary", "nexnotes"}
    for partner, shown, host in (("nextasks", "ntk_AbCd", "tasks.example.com"),
                                 ("nexpaper", "npk_WxYz", "paper.example.com")):
        assert cards[partner]["state"] == "legacy", cards[partner]
        assert cards[partner]["legacy"]["shown"] == shown and cards[partner]["host"] == host
    for other in ("nexmail", "nexfolk", "nexdiary", "nexnotes"):
        assert cards[other]["state"] == "none"
    # The older routes answer as before.
    assert client.get("/api/nextasks").json()["connected"] is True
    assert client.get("/api/nexpaper").json()["connected"] is True
    assert client.delete("/api/nextasks").status_code == 204
    with SessionLocal() as db:
        assert db.get(NextasksConnection, account.id) is None and db.get(NexpaperConnection, account.id) is not None


def test_the_description_names_nextime(client: TestClient) -> None:
    found = TestClient(client.app, base_url="http://testserver").get("/.well-known/nex-app")
    assert found.status_code == 200 and found.json()["app"] == "nextime" and found.json()["connect"] is True
