"""TIME-4, "Vorgaben für neue Termine" (decided while testing, 2026-11-23): what a new event of a calendar starts with,
per calendar and person: whole day, length, repeat, reminders, busy. Each person keeps their own (a shared calendar
too), any right may set them, a calendar of a source keeps them in nextime only, nothing goes there."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import RIGHT_READ, Account, Calendar, RemoteChange, Source
from app.services import calendars as calendar_service

from .calendar_helpers import code, make_calendar, named, share
from .conftest import make_account, new_client

BIRTHDAYS = {"all_day": True, "rule": "yearly", "reminders": [360], "busy": False}


def test_time4_defaults_made_with_the_calendar_changed_and_taken_back(client: TestClient, account: Account) -> None:
    made = client.post("/api/calendars", json={"name": "Geburtstage", "defaults": BIRTHDAYS})
    assert made.status_code == 201, made.text
    assert named(client, "Geburtstage")["defaults"] == BIRTHDAYS
    geburtstage = named(client, "Geburtstage")["id"]
    timed = {"minutes": 30, "rule": "biweekly", "reminders": [15, 60]}
    assert client.patch(f"/api/calendars/{geburtstage}", json={"defaults": timed}).status_code == 200
    assert named(client, "Geburtstage")["defaults"] == {"minutes": 30, "rule": "biweekly", "reminders": [60, 15]}
    # Nothing set is as from the start; a calendar without any says so with an empty set.
    assert client.patch(f"/api/calendars/{geburtstage}", json={"defaults": {}}).status_code == 200
    assert named(client, "Geburtstage")["defaults"] == {}
    make_calendar(client, "Ohne")
    assert named(client, "Ohne")["defaults"] == {}


def test_time4_defaults_wrong_ones_are_refused_and_change_nothing(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    assert client.patch(f"/api/calendars/{familie}", json={"defaults": BIRTHDAYS}).status_code == 200
    for wrong in ({"minutes": 50}, {"rule": "hourly"}, {"all_day": "ja"}, {"colour": "red"}, {"reminders": [-5]},
                  {"reminders": "15"}, {"minutes": True}):
        refused = client.patch(f"/api/calendars/{familie}", json={"defaults": wrong})
        assert refused.status_code == 422, wrong
        assert code(refused) in ("invalid_defaults", "invalid_reminder"), wrong
    assert named(client, "Familie")["defaults"] == BIRTHDAYS
    refused = client.post("/api/calendars", json={"name": "Nein", "defaults": {"minutes": 7}})
    assert refused.status_code == 422
    assert all(row["name"] != "Nein" for row in client.get("/api/calendars").json()["calendars"])


def test_time4_defaults_each_person_their_own_also_with_a_right_to_read(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    assert client.patch(f"/api/calendars/{familie}", json={"defaults": BIRTHDAYS}).status_code == 200
    anna = make_account("anna")
    share(familie, anna.id, RIGHT_READ)
    with new_client(anna) as browser:
        seen = next(row for row in browser.get("/api/calendars").json()["calendars"] if row["id"] == familie)
        assert seen["defaults"] == {}
        assert browser.patch(f"/api/calendars/{familie}", json={"defaults": {"minutes": 90}}).status_code == 200
        seen = next(row for row in browser.get("/api/calendars").json()["calendars"] if row["id"] == familie)
        assert seen["defaults"] == {"minutes": 90}
        # A calendar the person does not see: not there.
        mine = make_calendar(browser, "Annas")
    assert named(client, "Familie")["defaults"] == BIRTHDAYS
    assert client.patch(f"/api/calendars/{mine}", json={"defaults": {"minutes": 30}}).status_code == 404


def test_time4_defaults_a_calendar_of_a_source_keeps_them_here_only(client: TestClient, account: Account) -> None:
    with SessionLocal() as db:
        source = Source(account_id=account.id, kind="caldav", label="Nextcloud", identity="x",
                        server="https://dav.example.com/", state="ok")
        db.add(source)
        db.flush()
        row = calendar_service._make(db, account.id, "Arbeit", "green")
        row.source_id = source.id
        db.commit()
        calendar_id, rev = row.id, row.props_rev
    assert client.patch(f"/api/calendars/{calendar_id}", json={"defaults": {"minutes": 45}}).status_code == 200
    assert named(client, "Arbeit")["defaults"] == {"minutes": 45}
    with SessionLocal() as db:
        found = db.get(Calendar, calendar_id)
        assert found is not None and found.props_rev == rev
        assert db.scalars(select(RemoteChange)).all() == []


def test_time4_defaults_reminders_per_kind_each_checked_for_its_kind(client: TestClient, account: Account) -> None:
    """After the review (2026-11-23): reminders for events with a time and for whole days are two fields."""
    made = client.post("/api/calendars", json={"name": "Geburtstage", "defaults": {
        "all_day": True, "reminders": [15], "reminders_all_day": [360, -540]}})
    assert made.status_code == 201, made.text
    assert named(client, "Geburtstage")["defaults"] == {"all_day": True, "reminders": [15],
                                                       "reminders_all_day": [360, -540]}
    calendar_id = named(client, "Geburtstage")["id"]
    # "On the day at 09:00" (-540) is a reminder of whole days only.
    refused = client.patch(f"/api/calendars/{calendar_id}", json={"defaults": {"reminders": [-540]}})
    assert refused.status_code == 422
    assert named(client, "Geburtstage")["defaults"]["reminders_all_day"] == [360, -540]
