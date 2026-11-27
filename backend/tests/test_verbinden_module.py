"""The shared module verbinden in nextime (bauplaene/verbinden 0.1.1): what its contract tests (``tests/nexverbinden``,
nextime as the host of nexmail) do not hold for nextime itself.

- The connections typed in by hand before the module (nextasks, nexpaper) stay in their tables and on their routes
  (``GET/PUT /api/nextasks``, ``GET/PUT /api/nexpaper``) and show in the module's cards as connections by API key.
- The description ``/.well-known/nex-app`` names nextime and its partners' places.
- Since 0.1.8 (B1, V43): the consent page shows the account as nextime's own pages do (``Store.account_card``).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account, NexpaperConnection, NextasksConnection
from app.services import vault
from app.services.accounts import PERSON_HEX
from app.services.verbinden_store import Store


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


def test_v43_the_consent_page_shows_the_account_as_nextime_s_own_pages_do(client: TestClient,
                                                                          account: Account) -> None:
    """verbinden 0.1.8 (B1, V43): shown name, user name, the own picture at the address nextime's pages use, and the
    color of the circle with the initial as nextime draws it (``personColor(color ?? accountId)``)."""
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        row.color = None
        db.commit()
        card = Store(db).account_card(account.id)
    assert card == {"name": account.display_name, "username": account.name, "picture": None,
                    "color": PERSON_HEX[account.id % len(PERSON_HEX)]}
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        row.avatar, row.avatar_at = b"RIFF-picture", datetime(2026, 11, 27, 9, 30, tzinfo=UTC)
        db.commit()
    me = client.get("/api/auth/me").json()
    with SessionLocal() as db:
        card = Store(db).account_card(account.id)
        assert Store(db).account_card(999_999) is None
    assert card["picture"] == f"/api/avatars/{account.id}?v={quote(me['avatar'], safe='')}"
    assert card["color"] == PERSON_HEX[me["color"]]
    assert client.get(card["picture"]).content == b"RIFF-picture"
    # Over the module's route: who is signed in, as the consent page gets it.
    from app.vendor.nexverbinden import connect

    with SessionLocal() as db:
        assert connect.account_card(Store(db), account.id) == card


def test_the_person_colors_are_the_browser_s() -> None:
    """``PERSON_HEX`` (the color the consent page gets) is the list nextime's pages draw the circles with."""
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "avatar.ts").read_text("utf-8")
    shown = re.search(r"PERSON_COLORS = \[([^\]]*)\]", source)
    assert shown is not None
    assert tuple(re.findall(r"'(#[0-9a-f]{6})'", shown.group(1))) == PERSON_HEX
