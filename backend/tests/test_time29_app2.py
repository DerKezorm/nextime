"""TIME-29, interface 0.4 (etappe 3 of the app): collections written from the app (TIMEIOS-9 AC-1, EC-8), tasks of
nextasks (TIMEIOS-17), deadlines of nexpaper (G9), invitations in ``changes`` (TIMEIOS-16), people with pictures
(TIMEIOS-9) and the year as the browser counts it (TIMEIOS-8 AC-16). Each with the browser's rights."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db import SessionLocal
from app.models import RIGHT_EDIT, RIGHT_READ, Account, Source
from app.services import accounts

from .calendar_helpers import make_calendar, make_event, share
from .conftest import make_account, new_client
from .test_time29 import all_changes, call, ops, pair, ref


def collection(device: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return ops(device["token"], {"ref": ref(), "op": "collection_create", "collection": fields})[0]


# --- Collections (TIMEIOS-9 AC-1, EC-8) -----------------------------------------------------------------------------


def test_TIME29_app2_collections_are_made_changed_and_deleted_from_the_app(client: TestClient,
                                                                            account: Account) -> None:
    device = pair(client)
    family = make_calendar(client, "Familie")
    work = make_calendar(client, "Arbeit")
    made_op = {"ref": ref(), "op": "collection_create",
               "collection": {"name": "Zuhause", "calendar_ids": [family], "with_tasks": False}}
    first = ops(device["token"], made_op)[0]
    again = ops(device["token"], made_op)[0]
    assert first["state"] == "done" and first == again
    made = first["collection"]
    assert made["name"] == "Zuhause" and made["calendar_ids"] == [family] and made["task_areas"] == []
    assert [row["name"] for row in client.get("/api/collections").json()["collections"]] == ["Zuhause"]
    changed = ops(device["token"], {"ref": ref(), "op": "collection_change", "id": made["id"],
                                    "collection": {"calendar_ids": [family, work], "name": "Alles daheim"}})[0]
    assert changed["collection"]["calendar_ids"] == sorted([family, work])
    body, _events = all_changes(device["token"])
    assert body["collections"] == [changed["collection"]]
    gone = ops(device["token"], {"ref": ref(), "op": "collection_delete", "id": made["id"]},
               {"ref": ref(), "op": "collection_delete", "id": made["id"]})
    assert [row["state"] for row in gone] == ["done", "done"]
    assert client.get("/api/collections").json()["collections"] == []


def test_TIME29_app2_collections_keep_the_browsers_rules(client: TestClient, account: Account) -> None:
    device = pair(client)
    family = make_calendar(client, "Familie")
    collection(device, name="Zuhause", calendar_ids=[family])
    kim = make_account("kim")
    with new_client(kim) as browser:
        foreign = make_calendar(browser, "Kims")
    answers = ops(device["token"],
                  {"ref": ref(), "op": "collection_create", "collection": {"name": "  ", "calendar_ids": []}},
                  {"ref": ref(), "op": "collection_create", "collection": {"name": "zuhause"}},
                  {"ref": ref(), "op": "collection_create",
                   "collection": {"name": "Fremd", "calendar_ids": [foreign]}},
                  {"ref": ref(), "op": "collection_change", "id": 999_999, "collection": {"name": "x"}})
    assert [row["code"] for row in answers] == ["name_missing", "collection_name_taken", "calendar_unknown",
                                                "collection_gone"]
    with new_client(kim) as browser:
        kims = pair(browser)
    other = ops(kims["token"], {"ref": ref(), "op": "collection_change", "id": answers[0].get("id", 1),
                                "collection": {"name": "Gekapert"}})[0]
    assert other["state"] == "refused"
    assert [row["name"] for row in client.get("/api/collections").json()["collections"]] == ["Zuhause"]


# --- Tasks and deadlines (TIMEIOS-17, G9) ---------------------------------------------------------------------------


@pytest.fixture
def tasks_fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.services import nextasks

    from .test_time25 import PUBLIC, Fake

    made = Fake()
    monkeypatch.setattr(nextasks, "TRANSPORT", httpx.MockTransport(made))
    monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port: [PUBLIC])
    return made


@pytest.fixture
def paper(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.services import nexpaper

    from .test_nexpaper import PUBLIC, Fake

    made = Fake()
    monkeypatch.setattr(nexpaper, "TRANSPORT", httpx.MockTransport(made))
    monkeypatch.setattr(nexpaper, "RESOLVER", lambda host, port: [PUBLIC])
    return made


def test_TIME29_app2_tasks_and_deadlines_answer_as_the_browser_also_unconnected(client: TestClient,
                                                                               account: Account) -> None:
    device = pair(client)
    for path in ("/tasks", "/deadlines"):
        app = call("GET", path, device["token"], params={"to": "2026-11-30"})
        browser = client.get(f"/api{path}", params={"to": "2026-11-30"})
        assert app.status_code == 200 and app.json() == browser.json() and app.json()["connected"] is False
    assert call("GET", "/tasks", device["token"]).status_code == 422


def test_TIME29_app2_tasks_of_a_connected_nextasks(client: TestClient, account: Account, tasks_fake: Any) -> None:
    from .test_time25 import connect

    tasks_fake.add("own", "Eigene", "2026-11-26")
    tasks_fake.add("late", "Überfällig", "2026-11-23")
    connect(client)
    device = pair(client)
    app = call("GET", "/tasks", device["token"], params={"to": "2026-11-30"}).json()
    assert app == client.get("/api/tasks", params={"to": "2026-11-30"}).json()
    assert [task["id"] for task in app["tasks"]] == ["late", "own"]
    later = call("GET", "/tasks", device["token"], params={"from": "2026-11-25", "to": "2026-11-30"}).json()
    assert [task["id"] for task in later["tasks"]] == ["own"]
    with new_client(make_account("kim")) as browser:
        kims = pair(browser)
    assert call("GET", "/tasks", kims["token"], params={"to": "2026-11-30"}).json()["connected"] is False


def test_TIME29_app2_deadlines_of_a_connected_nexpaper(client: TestClient, account: Account, paper: Any) -> None:
    from .test_nexpaper import connect, deadline

    assert connect(client).status_code == 200
    paper.soon = [deadline(3, "2026-12-01")]
    device = pair(client)
    app = call("GET", "/deadlines", device["token"], params={"to": "2026-12-31"}).json()
    assert app == client.get("/api/deadlines", params={"to": "2026-12-31"}).json()
    assert [row["id"] for row in app["deadlines"]] == ["3-due"]


# --- Invitations (TIMEIOS-16) --------------------------------------------------------------------------------------


INVITE = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//tests//EN\r
BEGIN:VEVENT\r
UID:jour-fixe@example.com\r
DTSTAMP:20261120T100000Z\r
DTSTART:20261203T090000Z\r
DTEND:20261203T100000Z\r
RRULE:FREQ=WEEKLY;COUNT=4\r
SUMMARY:Jour fixe\r
ORGANIZER;CN=Kim Example:mailto:kim@example.net\r
ATTENDEE;CN=Kim Example;PARTSTAT=ACCEPTED:mailto:kim@example.net\r
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:tester@example.com\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:jour-fixe@example.com\r
DTSTAMP:20261120T100000Z\r
RECURRENCE-ID:20261210T090000Z\r
DTSTART:20261210T090000Z\r
DTEND:20261210T100000Z\r
SUMMARY:Jour fixe\r
ORGANIZER;CN=Kim Example:mailto:kim@example.net\r
ATTENDEE;CN=Kim Example;PARTSTAT=ACCEPTED:mailto:kim@example.net\r
ATTENDEE;PARTSTAT=DECLINED:mailto:tester@example.com\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_TIME29_app2_an_invitation_carries_its_state_and_who_invites(client: TestClient, account: Account) -> None:
    from .test_time19 import from_source, put_share, source_calendar

    work = source_calendar(account.id, "Arbeit")
    with SessionLocal() as db:
        db.execute(update(Source).values(addresses=["mailto:tester@example.com"]))
        db.commit()
    from_source(work, INVITE)
    make_event(client, "Ohne Einladung")
    device = pair(client)
    _body, events = all_changes(device["token"])
    invited = next(row for row in events if row["uid"] == "jour-fixe@example.com")
    assert invited["invitation"] == {"state": "needs-action", "from": "Kim Example", "cancelled": False,
                                     "occurrences": {"20261210T090000Z": "declined"}}
    assert next(row for row in events if row["title"] == "Ohne Einladung")["invitation"] is None
    # Shared busy only with somebody else: no invitation, no title for them.
    mika = make_account("mika")
    assert put_share(client, work, mika.id, "busy").status_code == 200
    with new_client(mika) as browser:
        mikas = pair(browser)
    _body, theirs = all_changes(mikas["token"])
    busy = next(row for row in theirs if row["calendar_id"] == work)
    assert busy["invitation"] is None and busy["title"] == ""


# --- People and pictures (TIMEIOS-9) ---------------------------------------------------------------------------------


def picture_for(account_id: int, when: str = "2026-11-20T10:00:00+00:00") -> None:
    with SessionLocal() as db:
        row = db.get(Account, account_id)
        assert row is not None
        row.avatar = b"RIFF\x00\x00\x00\x00WEBPVP8 "
        row.avatar_at = datetime.fromisoformat(when).astimezone(UTC)
        db.commit()


def test_TIME29_app2_people_carry_picture_and_color_and_the_picture_follows_the_rules(
        client: TestClient, account: Account) -> None:
    mika = make_account("mika")
    stranger = make_account("stranger")
    picture_for(mika.id)
    picture_for(stranger.id)
    family = make_calendar(client, "Familie")
    share(family, mika.id, RIGHT_EDIT)
    with new_client(mika) as browser:
        made = make_event(browser, "Elternabend", calendar_id=family)
        mikas_calendar = make_calendar(browser, "Mikas")
    share(mikas_calendar, account.id, RIGHT_READ)
    member = make_account("member")
    with new_client(member) as browser:
        device = pair(browser)
    share(family, member.id, RIGHT_READ)
    body, events = all_changes(device["token"])
    shown = next(row for row in events if row["id"] == made["id"])
    assert shown["created_by"]["avatar"] == "2026-11-20T10:00:00Z" and isinstance(shown["created_by"]["color"], int)
    owner = next(row for row in body["calendars"] if row["id"] == family)["owner"]
    assert owner["id"] == account.id and owner["avatar"] == "" and isinstance(owner["color"], int)
    assert call("GET", f"/avatars/{mika.id}", device["token"]).status_code == 200
    # Every signed-in account sees every picture (konten 1.0.0), the app's device too.
    assert call("GET", f"/avatars/{stranger.id}", device["token"]).status_code == 200
    with SessionLocal() as db:
        row = db.get(Account, mika.id)
        assert row is not None
        accounts.block(db, row)
    _body, later = all_changes(device["token"])
    assert next(row for row in later if row["id"] == made["id"])["created_by"]["avatar"] == ""
    assert call("GET", f"/avatars/{mika.id}", device["token"]).status_code == 404


# --- The year (TIMEIOS-8 AC-16) ---------------------------------------------------------------------------------------


def test_TIME29_app2_the_year_counts_as_the_browser(client: TestClient, account: Account) -> None:
    family = make_calendar(client, "Familie")
    hidden = make_calendar(client, "Verborgen")
    make_event(client, "Eins", start="2026-03-03T10:00", end="2026-03-03T11:00", calendar_id=family)
    make_event(client, "Zwei", start="2026-03-03T12:00", end="2026-03-03T13:00")
    make_event(client, "Drei", start="2026-03-04T12:00", end="2026-03-04T13:00", calendar_id=hidden)
    client.patch(f"/api/calendars/{hidden}", json={"hidden": True})
    device = pair(client)
    params = {"year": 2026, "zone": "Europe/Berlin"}
    app = call("GET", "/year", device["token"], params=params).json()
    assert app == client.get("/api/events/year", params=params).json()
    assert app["days"] == {"2026-03-03": 2}
    made = ops(device["token"], {"ref": ref(), "op": "collection_create",
                                 "collection": {"name": "Familie", "calendar_ids": [family]}})[0]["collection"]
    narrowed = call("GET", "/year", device["token"], params={**params, "collection": made["id"]}).json()
    assert narrowed["days"] == {"2026-03-03": 1}
    alone = call("GET", "/year", device["token"], params={**params, "only": hidden}).json()
    assert alone["days"] == {"2026-03-04": 1}
    with new_client(make_account("kim")) as browser:
        kims = pair(browser)
    assert call("GET", "/year", kims["token"], params={**params, "only": hidden}).status_code == 404
    assert call("GET", "/year", kims["token"], params={**params, "collection": made["id"]}).status_code == 404


def test_TIME29_app2_hello_names_the_new_parts(client: TestClient) -> None:
    from .test_time29 import app_client

    with app_client() as phone:
        features = set(phone.get("/api/app/1/hello").json()["features"])
    assert {"collections_write", "tasks", "deadlines", "invitations", "people", "year"} <= features


def test_TIME29_app2_a_source_calendar_says_when_it_last_synced(client: TestClient, account: Account) -> None:
    """TIMEIOS-6 EC-7: "<source> not reachable · state of 15:52" needs the last round that worked."""
    from .test_time19 import source_calendar

    work = source_calendar(account.id, "Arbeit")
    with SessionLocal() as db:
        db.execute(update(Source).values(state="unreachable",
                                         synced_at=datetime(2026, 11, 24, 14, 52, tzinfo=UTC)))
        db.commit()
    own = make_calendar(client, "Familie")
    device = pair(client)
    body, _events = all_changes(device["token"])
    rows = {row["id"]: row for row in body["calendars"]}
    assert (rows[work]["source_state"], rows[work]["synced_at"]) == ("unreachable", "2026-11-24T14:52:00Z")
    assert rows[own]["synced_at"] is None and rows[own]["source_state"] is None
