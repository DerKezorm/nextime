"""The shared module mcp in nextime (bauplaene/mcp 1.1.0; plan "Entwurf MCP-Modul", section "nextime: vom Haken zur
eigenen MCP-Art"): what the module's contract tests (``tests/nexmcp``) do not hold for nextime itself.

- MCP keys made by hand and the programs that connected over OAuth stand in their own table ``mcp_keys``, never in
  ``api_keys`` (nextime 0.4.0, the way back, reads that table and would take such a row for an API key).
- An MCP key opens ``/api/mcp`` and never ``/api/v1``; an API key of before with the tick opens both, as before.
- The older ways answer as before: ``GET /api/keys`` lists the API keys only, with their numbers; ``/api/admin/keys``
  counts per account.
- ``/api/mcp`` keeps its answers of before (403 ``mcp_off``, 401 ``token_invalid``) and names the metadata for programs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, ApiKey, McpKey
from app.services import settings_service

from .calendar_helpers import make_event
from .conftest import new_client

PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def _mcp_on() -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"mcp_enabled": True})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _count(model: Any) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_an_mcp_key_by_hand_stands_in_its_own_table_and_opens_mcp_only(client: TestClient, account: Account) -> None:
    _mcp_on()
    make_event(client, "Training")
    made = client.post("/api/connections/keys", json={"kind": "mcp", "name": "Planer", "right": "write"})
    assert made.status_code == 201, made.text
    secret = made.json()["secret"]
    assert secret.startswith("nt_mcp_") and str(made.json()["key"]["id"]).startswith("mcp-")
    assert (_count(McpKey), _count(ApiKey)) == (1, 0)
    with new_client() as program:
        assert program.post("/api/mcp", headers=_bearer(secret), json=PING).status_code == 200
        assert program.get("/api/v1/me", headers=_bearer(secret)).status_code == 401
    # The older list of API keys knows nothing of it, as 0.4.0 would not.
    assert client.get("/api/keys").json()["keys"] == []


def test_a_program_connects_over_oauth_into_mcp_keys_never_api_keys(client: TestClient, account: Account) -> None:
    _mcp_on()
    with new_client() as program:
        registered = program.post("/api/oauth/register", json={"client_name": "Probe",
                                                                "redirect_uris": ["http://127.0.0.1:33418/callback"]})
        assert registered.status_code == 201, registered.text
        client_id = registered.json()["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {"client_id": client_id, "redirect_uri": "http://127.0.0.1:33418/callback", "response_type": "code",
                  "code_challenge": challenge, "code_challenge_method": "S256", "state": "s1"}
        agreed = client.post("/api/oauth/authorize", json={**params, "allow": True})
        assert agreed.status_code == 200, agreed.text
        code = parse_qs(urlsplit(agreed.json()["redirect"]).query)["code"][0]
        traded = program.post("/api/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                         "redirect_uri": params["redirect_uri"], "client_id": client_id,
                                                         "code_verifier": verifier})
        assert traded.status_code == 200, traded.text
        tokens = traded.json()
        assert tokens["access_token"].startswith("nt_mcp_") and tokens["refresh_token"].startswith("ntr_")
        assert program.post("/api/mcp", headers=_bearer(tokens["access_token"]), json=PING).status_code == 200
    assert (_count(McpKey), _count(ApiKey)) == (1, 0)
    with SessionLocal() as db:
        row = db.scalar(select(McpKey))
        assert row is not None and row.client_id == client_id and row.refresh_hash


def test_an_api_key_of_before_with_the_tick_opens_both_as_before(client: TestClient, account: Account) -> None:
    _mcp_on()
    made = client.post("/api/keys", json={"name": "Skript", "level": "read", "mcp": True})
    assert made.status_code == 201, made.text
    token = made.json()["token"]
    assert made.json()["made"]["id"] == 1 and made.json()["made"]["mcp"] is True
    with new_client() as program:
        assert program.post("/api/mcp", headers=_bearer(token), json=PING).status_code == 200
        assert program.get("/api/v1/me", headers=_bearer(token)).status_code == 200
    listed = client.get("/api/keys").json()["keys"]
    assert [(key["id"], key["mcp"]) for key in listed] == [(1, True)]
    assert client.delete("/api/keys/1").status_code == 204
    assert client.get("/api/keys").json()["keys"] == []


def test_the_operator_s_count_of_before_counts_api_keys_and_mcp_keys_by_hand(client: TestClient,
                                                                               account: Account) -> None:
    _mcp_on()
    client.post("/api/keys", json={"name": "A", "level": "read"})
    client.post("/api/keys", json={"name": "B", "level": "read", "mcp": True})
    client.post("/api/connections/keys", json={"kind": "mcp", "name": "C"})
    counted = client.get("/api/admin/keys").json()
    assert (counted["keys"], counted["mcp"]) == (3, 2)


def test_the_walls_keep_nextime_s_answers_and_name_the_metadata(client: TestClient, account: Account) -> None:
    with new_client() as program:
        off = program.post("/api/mcp", headers=_bearer("nt_mcp_nothing"), json=PING)
        assert off.status_code == 403 and off.json()["detail"]["code"] == "mcp_off"
        _mcp_on()
        wrong = program.post("/api/mcp", headers=_bearer("nt_mcp_nothing"), json=PING)
        assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "token_invalid"
        assert "resource_metadata=" in wrong.headers["www-authenticate"]
        meta = program.get("/.well-known/oauth-protected-resource/api/mcp")
        assert meta.status_code == 200 and meta.json()["resource"].endswith("/api/mcp")


def test_a_blocked_account_s_mcp_keys_rest_and_its_programs_end(client: TestClient, operator: Account) -> None:
    """E41, E42: a block lets the account's MCP keys rest (they work again after the unblock); AI programs connected
    over OAuth end like sessions and devices."""
    from .conftest import PASSWORD, make_account

    _mcp_on()
    anna = make_account("anna")
    with new_client(anna) as browser:
        secret = browser.post("/api/connections/keys", json={"kind": "mcp", "name": "Planer"}).json()["secret"]
        registered = client.post("/api/oauth/register", json={"client_name": "Probe",
                                                               "redirect_uris": ["http://127.0.0.1:33418/callback"]})
        client_id = registered.json()["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {"client_id": client_id, "redirect_uri": "http://127.0.0.1:33418/callback", "response_type": "code",
                  "code_challenge": challenge, "code_challenge_method": "S256", "state": "s1"}
        code = parse_qs(urlsplit(browser.post("/api/oauth/authorize", json={**params, "allow": True})
                                 .json()["redirect"]).query)["code"][0]
    with new_client() as program:
        tokens = program.post("/api/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                        "redirect_uri": params["redirect_uri"], "client_id": client_id,
                                                        "code_verifier": verifier}).json()
        assert program.post("/api/mcp", headers=_bearer(tokens["access_token"]), json=PING).status_code == 200
        sure = {"current_password": PASSWORD}
        assert client.post(f"/api/accounts/{anna.id}/block", json=sure).status_code == 204
        assert program.post("/api/mcp", headers=_bearer(secret), json=PING).status_code == 401
        assert program.post("/api/mcp", headers=_bearer(tokens["access_token"]), json=PING).status_code == 401
        assert client.post(f"/api/accounts/{anna.id}/unblock", json=sure).status_code == 204
        assert program.post("/api/mcp", headers=_bearer(secret), json=PING).status_code == 200
        assert program.post("/api/mcp", headers=_bearer(tokens["access_token"]), json=PING).status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(McpKey).where(McpKey.client_id.is_not(None))) == 0


def test_a_program_registers_trades_and_revokes_without_a_tab_as_programs_do(client: TestClient,
                                                                              account: Account) -> None:
    """A program sends no ``X-Nextime-Client``: the OAuth routes of the program's side take it without one (the
    consent stays a page of a person, with its tab)."""
    _mcp_on()
    with TestClient(client.app, base_url="http://testserver") as program:
        registered = program.post("/api/oauth/register", json={"client_name": "Probe",
                                                                "redirect_uris": ["http://127.0.0.1:33418/callback"]})
        assert registered.status_code == 201, registered.text
        assert program.post("/api/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "ntr_x",
                                                      "client_id": registered.json()["client_id"]}).status_code == 400
        assert program.post("/api/oauth/revoke", data={"token": "ntr_x"}).status_code == 200
        # The consent is a person's page: without the tab it is refused as every change.
        assert program.post("/api/oauth/authorize", json={}).status_code == 400


def test_a_request_that_waits_goes_to_the_account_s_browsers(client: TestClient, account: Account,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP decision 14: nextime has no bell, so "Ein Programm wartet auf dein Ja" goes as a notification to the
    account's browsers, with the program's name and the request's line, never its arguments; a tap opens the page."""
    from app.models import AuthSession, NotifyOutbox, WebPushSubscription
    from app.security import hash_token, start_session
    from app.services import notify, webpush

    _mcp_on()
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        token = start_session(db, row, "127.0.0.1", "browser")
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        assert session is not None
        db.add(WebPushSubscription(account_id=row.id, session_id=session.id, endpoint="https://push.example.com/a",
                                   p256dh="p" * 87, auth="a" * 22))
        db.commit()
    made = client.post("/api/connections/keys", json={"kind": "mcp", "name": "Planer", "right": "write"}).json()
    assert client.put(f"/api/connections/mcp/keys/{made['key']['id']}/rights",
                      json={"rights": {"create_event": "ask"}}).status_code == 200
    with new_client() as program:
        asked = program.post("/api/mcp", headers=_bearer(made["secret"]), json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "create_event", "arguments": {"title": "Geheimer Titel", "start": "2026-11-27T09:00"}}})
    assert asked.status_code == 200, asked.text
    with SessionLocal() as db:
        (waiting,) = db.scalars(select(NotifyOutbox).where(NotifyOutbox.kind == "mcp")).all()
        assert waiting.payload["url"].startswith("/mcp/requests/")
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(webpush, "deliver", lambda _db, _sub, body, **_kw: sent.append(json.loads(body)))
    notify.send_round(datetime.now(UTC) + timedelta(seconds=1))
    assert len(sent) == 1 and sent[0]["url"].startswith("/mcp/requests/")
    assert sent[0]["title"] in ("Ein Programm wartet auf dein Ja", "A program waits for your yes")
    assert sent[0]["body"].startswith("Planer: ") and "Geheimer Titel" in sent[0]["body"]
