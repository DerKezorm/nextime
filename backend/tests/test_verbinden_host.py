"""nextime as the host of a connection (plan "Apps verbinden", V18/V19, stage 1): a partner's token opens
``/api/v1`` with the right of its purposes and only the calendars given. nexmail (``calendars.read``,
``events.write``, ``invitations.answer``) reads and writes; nexdiary (``calendars.read``) only reads, a change gives
403. The connection's own switch counts, not the one of API keys."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Account
from app.services import settings_service, verbinden_store
from app.vendor import nexverbinden
from app.vendor.nexmcp import oauthcore

from .calendar_helpers import make_calendar, make_event


def hosted(account_id: int, partner: str, areas: list[int] | None = None) -> str:
    """A connection ``partner`` made with nextime (its consent here): the token nextime gave it."""
    token = "ntv_" + secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    grants = verbinden_store.CONFIG.catalog.uses(partner, "nextime")
    with SessionLocal() as db:
        store = verbinden_store.Store(db)
        store.save(nexverbinden.Connection(
            id=secrets.token_urlsafe(18), account_id=account_id, partner=partner,
            partner_base=f"https://{partner}.example.com", partner_account="kim", uses=(), grants=grants,
            areas_granted=areas, areas_used=None, held=store.seal('{"access": "", "refresh": "", "expires": "'
                                                                  + now.isoformat() + '", "own": ""}'),
            issued_access_hash=oauthcore.digest(token), issued_refresh_hash=secrets.token_hex(32),
            issued_access_expires=now + timedelta(hours=1), created_at=now))
        store.commit()
    return token


def _as(token: str) -> TestClient:
    return TestClient(app, base_url="http://testserver", headers={"Authorization": f"Bearer {token}"})


def test_nexmail_reads_and_writes_the_calendars_given(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    arbeit = make_calendar(client, "Arbeit")
    make_event(client, "Training", calendar_id=familie)
    token = hosted(account.id, "nexmail", [familie])
    with _as(token) as partner:
        seen = partner.get("/api/v1/calendars")
        assert seen.status_code == 200, seen.text
        assert [calendar["id"] for calendar in seen.json()["calendars"]] == [familie]
        made = partner.post("/api/v1/events", json={"title": "Zahnarzt", "start": "2026-11-27T09:00",
                                                    "time_zone": "Europe/Berlin", "calendar_id": familie})
        assert made.status_code in (200, 201), made.text
        assert partner.post("/api/v1/events", json={"title": "Nein", "start": "2026-11-27T09:00",
                                                     "time_zone": "Europe/Berlin", "calendar_id": arbeit}
                            ).status_code == 404


def test_nexdiary_only_reads(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    token = hosted(account.id, "nexdiary")
    with _as(token) as partner:
        assert partner.get("/api/v1/calendars").status_code == 200
        refused = partner.post("/api/v1/events", json={"title": "Nein", "start": "2026-11-27T09:00",
                                                       "time_zone": "Europe/Berlin", "calendar_id": familie})
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "token_reads_only"


def test_a_connection_works_with_the_api_switched_off_and_rests_with_connections_off(client: TestClient,
                                                                                      account: Account) -> None:
    token = hosted(account.id, "nexdiary")
    with SessionLocal() as db:
        settings_service.save(db, {"api_enabled": False})
    with _as(token) as partner:
        assert partner.get("/api/v1/calendars").status_code == 200
        with SessionLocal() as db:
            settings_service.save(db, {"connections_enabled": False})
        off = partner.get("/api/v1/calendars")
        assert off.status_code == 403 and off.json()["detail"]["code"] == "connections_off"
