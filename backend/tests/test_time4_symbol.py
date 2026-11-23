"""TIME-4, "Kalender-Symbol wie nexnotes": a calendar carries a symbol of the list nexnotes' folders have, drawn in its
color; without one it keeps its color square. Only names of the list, else refused; stored only in nextime (a calendar
of a source keeps it here, nothing goes there); in the list of calendars, the API and the export."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import RIGHT_EDIT, Account, Calendar, RemoteChange, Source
from app.services import calendars as calendar_service
from app.services.symbols import SYMBOLS

from .calendar_helpers import code, make_calendar, named, share
from .conftest import make_account, new_client

ROOT = Path(__file__).resolve().parents[2]


def test_time4_symbol_the_page_and_the_server_know_the_same_symbols_as_nexnotes() -> None:
    symbols = (ROOT / "frontend" / "src" / "lib" / "symbols.ts").read_text(encoding="utf-8")
    drawn = re.findall(r"\{ name: '([a-z0-9-]+)', icon: \w+, group: '\w+', words: '[^']*' \}", symbols)
    assert tuple(drawn) == SYMBOLS
    assert len(SYMBOLS) == len(set(SYMBOLS)) >= 250
    sister = ROOT.parent / "nexnotes" / "backend" / "app" / "services" / "symbols.py"
    if sister.exists():
        # The same list as nexnotes' folders (read where the sister app lies beside nextime).
        assert all(f'"{name}"' in sister.read_text(encoding="utf-8") for name in SYMBOLS)


def test_time4_symbol_made_changed_taken_away_and_an_unknown_one_refused(client: TestClient,
                                                                          account: Account) -> None:
    made = client.post("/api/calendars", json={"name": "Reisen", "color": "blue", "icon": "plane"})
    assert made.status_code == 201, made.text
    reisen = named(client, "Reisen")
    assert reisen["icon"] == "plane"
    assert client.patch(f"/api/calendars/{reisen['id']}", json={"icon": "beach"}).status_code == 200
    assert named(client, "Reisen")["icon"] == "beach"
    for wrong in ("skull-crossbones", "Plane", "plane ", "<svg>", "folder/x"):
        refused = client.patch(f"/api/calendars/{reisen['id']}", json={"icon": wrong})
        assert refused.status_code == 422 and code(refused) == "calendar_icon_unknown", wrong
        refused = client.post("/api/calendars", json={"name": "Nein", "icon": wrong})
        assert refused.status_code == 422 and code(refused) == "calendar_icon_unknown", wrong
    assert named(client, "Reisen")["icon"] == "beach"
    assert all(row["name"] != "Nein" for row in client.get("/api/calendars").json()["calendars"])
    # Empty takes it away: the color square again.
    assert client.patch(f"/api/calendars/{reisen['id']}", json={"icon": ""}).status_code == 200
    assert named(client, "Reisen")["icon"] == ""


def test_time4_symbol_existing_calendars_stay_without_and_an_unknown_stored_name_shows_none(
        client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    assert named(client, "Familie")["icon"] == ""
    with SessionLocal() as db:
        row = db.get(Calendar, familie)
        assert row is not None and row.icon == ""
        # A name the list does not know (left by a newer version taken back): shown as none.
        row.icon = "rocket-ship-of-later"
        db.commit()
    assert named(client, "Familie")["icon"] == ""


def test_time4_symbol_only_the_owner_sets_it(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie", icon="house")
    anna = make_account("anna")
    share(familie, anna.id, RIGHT_EDIT)
    with new_client(anna) as browser:
        refused = browser.patch(f"/api/calendars/{familie}", json={"icon": "star"})
        assert refused.status_code == 403 and code(refused) == "calendar_not_own"
        seen = next(row for row in browser.get("/api/calendars").json()["calendars"] if row["id"] == familie)
        assert seen["icon"] == "house"


def test_time4_symbol_a_calendar_of_a_source_keeps_it_here_and_nothing_goes_there(client: TestClient,
                                                                                  account: Account) -> None:
    with SessionLocal() as db:
        source = Source(account_id=account.id, kind="caldav", label="Nextcloud", identity="x",
                        server="https://dav.example.com/", state="ok")
        db.add(source)
        db.flush()
        row = calendar_service._make(db, account.id, "Arbeit", "green")
        row.source_id = source.id
        row.remote_url = "https://dav.example.com/arbeit/"
        db.commit()
        calendar_id, rev = row.id, row.props_rev
    assert client.patch(f"/api/calendars/{calendar_id}", json={"icon": "briefcase"}).status_code == 200
    assert named(client, "Arbeit")["icon"] == "briefcase"
    with SessionLocal() as db:
        row = db.get(Calendar, calendar_id)
        assert row is not None and row.props_rev == rev
        assert db.scalars(select(RemoteChange)).all() == []


def test_time4_symbol_in_the_export_and_the_api(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie", icon="family")
    text = client.get(f"/api/calendars/{familie}/export.ics").text
    assert "X-NEXTIME-ICON:family\r\n" in text
    plain = make_calendar(client, "Ohne")
    assert "X-NEXTIME-ICON" not in client.get(f"/api/calendars/{plain}/export.ics").text
    made = client.post("/api/keys", json={"name": "Skript", "level": "read"})
    assert made.status_code == 201, made.text
    with TestClient(client.app) as program:
        listed = program.get("/api/v1/calendars", headers={"Authorization": f"Bearer {made.json()['token']}"})
    assert listed.status_code == 200, listed.text
    assert {row["name"]: row["icon"] for row in listed.json()["calendars"]}["Familie"] == "family"
