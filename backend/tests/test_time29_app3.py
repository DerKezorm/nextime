"""TIME-29, interface 0.5 (stage 4 of the app, events: TIMEIOS-11, 14, 20): the account's reminders with each event,
attachments of nextime and of the source with upload, files and removal, the trash, ``removed[].trashed``. Each
with the browser's rights."""

from __future__ import annotations

import secrets
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models import RIGHT_BUSY, RIGHT_EDIT, RIGHT_READ, Account
from app.services import media_worker

from .calendar_helpers import make_calendar, make_event, share
from .conftest import make_account, new_client
from .test_time9 import pdf
from .test_time29 import all_changes, app_client, bearer, call, ops, pair, ref


@pytest.fixture(autouse=True)
def inline_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media_worker, "INLINE", True)


def put_file(token: str, data: bytes, name: str = "Plan.pdf", **params: Any) -> Any:
    with app_client() as phone:
        return phone.post("/api/app/1/attachments", params={"name": name, **params}, content=data,
                          headers={**bearer(token), "Content-Type": "application/octet-stream"})


def shown(token: str, event_id: int) -> dict[str, Any]:
    _body, events = all_changes(token)
    return next(row for row in events if row["id"] == event_id)


# --- Reminders (TIME-7, TIMEIOS-20) ---------------------------------------------------------------------------------


def test_TIME29_app3_reminders_come_with_each_event_and_move_the_cursor(client: TestClient, account: Account) -> None:
    device = pair(client)
    plain = make_event(client, "Ohne Wahl")
    chosen = make_event(client, "Zahnarzt", reminders=[15, 60])
    body, events = all_changes(device["token"])
    rows = {row["id"]: row for row in events}
    assert rows[plain["id"]]["reminders"] is None and rows[plain["id"]]["reminder_occurrences"] == {}
    assert rows[chosen["id"]]["reminders"] == [60, 15]
    assert body["calendars"][0]["reminder_default"] == "none"
    assert client.put(f"/api/reminders/calendars/{body['calendars'][0]['id']}", json={"choice": "30"}).status_code == 200
    body, _events = all_changes(device["token"])
    assert body["calendars"][0]["reminder_default"] == "30"
    # Only the reminders change: the cursor moves, and the event comes again.
    client.put(f"/api/events/{plain['id']}/reminders", json={"minutes": [5]})
    later, changed = all_changes(device["token"], body["cursor"])
    assert [row["id"] for row in changed] == [plain["id"]] and changed[0]["reminders"] == [5]
    # The app sets them itself: its own op, null back to the calendar's default.
    answer = ops(device["token"], {"ref": ref(), "op": "reminders", "id": chosen["id"], "minutes": None})[0]
    assert answer["state"] == "done" and answer["event"]["reminders"] is None
    assert later["cursor"]


def test_TIME29_app3_own_reminders_wake_the_account_only(client: TestClient, account: Account,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import live

    mika = make_account("mika")
    family = make_calendar(client, "Familie")
    share(family, mika.id, RIGHT_READ)
    made = make_event(client, "Elternabend", calendar_id=family)
    heard: list[set[int]] = []
    monkeypatch.setattr(live, "CHANGED", [heard.append])
    assert client.put(f"/api/events/{made['id']}/reminders", json={"minutes": [5]}).status_code == 200
    # A waiting ``changes`` and the nudge of the app answer; nobody else hears of somebody's own reminders.
    assert heard == [{account.id}]


def test_TIME29_app3_reminders_of_single_occurrences_come_by_their_name(client: TestClient, account: Account) -> None:
    from app.db import SessionLocal
    from app.models import ReminderChoice

    made = make_event(client, "Training", rule={"freq": "daily", "end": "count", "count": 3})
    with SessionLocal() as db:
        db.add(ReminderChoice(event_id=made["id"], account_id=account.id, minutes=[15],
                              occurrences={"20261127T173000Z": {"line": "RECURRENCE-ID:20261127T173000Z",
                                                                "minutes": [60]}}))
        db.commit()
    device = pair(client)
    row = shown(device["token"], made["id"])
    assert row["reminders"] == [15] and row["reminder_occurrences"] == {"20261127T173000Z": [60]}


def test_TIME29_app3_reminders_are_the_accounts_own(client: TestClient, account: Account) -> None:
    mika = make_account("mika")
    family = make_calendar(client, "Familie")
    share(family, mika.id, RIGHT_READ)
    made = make_event(client, "Elternabend", calendar_id=family, reminders=[30])
    with new_client(mika) as browser:
        device = pair(browser)
    assert shown(device["token"], made["id"])["reminders"] is None
    # Reminders are the account's own: also in a calendar it may only read (TIME-7 AC-9).
    answer = ops(device["token"], {"ref": ref(), "op": "reminders", "id": made["id"], "minutes": [10]})[0]
    assert answer["state"] == "done" and answer["event"]["reminders"] == [10]
    assert client.get(f"/api/events/{made['id']}").json()["reminders"]["minutes"] == [30]


# --- Attachments (TIME-9, TIMEIOS-11) -------------------------------------------------------------------------------


def test_TIME29_app3_an_upload_lands_once_and_shows_with_the_event(client: TestClient, account: Account) -> None:
    device = pair(client)
    made = make_event(client, "Besprechung")
    body, _events = all_changes(device["token"])
    key = secrets.token_urlsafe(16)
    first = put_file(device["token"], pdf(2), event_id=made["id"], ref=key)
    again = put_file(device["token"], pdf(2), event_id=made["id"], ref=key)
    assert first.status_code == 201 and again.status_code == 200 and first.json()["id"] == again.json()["id"]
    attachment = first.json()
    assert attachment["kind"] == "pdf" and attachment["pages"] == 2 and attachment["thumb"] is True
    later, changed = all_changes(device["token"], body["cursor"])
    assert [row["attachments"] for row in changed if row["id"] == made["id"]] == [[attachment]]
    for part in ("file", "thumb"):
        assert call("GET", f"/attachments/{attachment['id']}/{part}", device["token"]).status_code == 200
    assert call("DELETE", f"/attachments/{attachment['id']}", device["token"]).status_code == 204
    assert call("DELETE", f"/attachments/{attachment['id']}", device["token"]).status_code == 204
    _after, gone = all_changes(device["token"], later["cursor"])
    assert [row["attachments"] for row in gone if row["id"] == made["id"]] == [[]]


def test_TIME29_app3_an_upload_waits_for_the_event_made_offline(client: TestClient, account: Account) -> None:
    device = pair(client)
    event_ref = ref()
    early = put_file(device["token"], pdf(1), event_ref=event_ref, ref=ref())
    assert early.status_code == 404 and early.json()["detail"]["code"] == "event_unknown"
    made = ops(device["token"], {"ref": event_ref, "op": "create",
                                 "event": {"title": "Offline", "start": "2026-11-26T09:00"}})[0]["event"]
    later = put_file(device["token"], pdf(1), event_ref=event_ref, ref=ref())
    assert later.status_code == 201
    # The ``ref`` of another account's op names nothing here.
    with new_client(make_account("kim")) as browser:
        kims = pair(browser)
    other = put_file(kims["token"], pdf(1), event_ref=event_ref, ref=ref())
    assert other.status_code == 404 and other.json()["detail"]["code"] == "event_unknown"
    assert shown(device["token"], made["id"])["attachments"][0]["id"] == later.json()["id"]


def test_TIME29_app3_uploads_keep_the_rights_and_the_limit(client: TestClient, account: Account) -> None:
    mika = make_account("mika")
    family = make_calendar(client, "Familie")
    share(family, mika.id, RIGHT_READ)
    made = make_event(client, "Elternabend", calendar_id=family)
    mine = put_file(pair(client)["token"], pdf(1), event_id=made["id"], ref=ref()).json()
    private = make_event(client, "Geheim")
    with new_client(mika) as browser:
        device = pair(browser)
    refused = put_file(device["token"], pdf(1), event_id=made["id"], ref=ref())
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "calendar_read_only"
    assert put_file(device["token"], pdf(1), event_id=private["id"], ref=ref()).status_code == 404
    # Reading is allowed, removing is not.
    assert call("GET", f"/attachments/{mine['id']}/file", device["token"]).status_code == 200
    assert call("DELETE", f"/attachments/{mine['id']}", device["token"]).status_code == 403
    share(family, mika.id, RIGHT_EDIT)
    assert client.put("/api/settings", json={"upload_max_mb": 1}).status_code == 200
    big = put_file(device["token"], b"\0" * (1024 * 1024 + 1), name="gross.bin", event_id=made["id"], ref=ref())
    assert big.status_code == 413 and big.json()["detail"]["code"] == "attachment_too_large"
    assert big.json()["detail"]["max_mb"] == 1
    assert put_file(device["token"], pdf(1), event_id=made["id"], ref="kurz").status_code == 422


def test_TIME29_app3_a_source_event_names_its_attachments(client: TestClient, account: Account) -> None:
    from .test_time19 import from_source, source_calendar, vevent

    work = source_calendar(account.id, "Arbeit")
    from_source(work, vevent("plan@example.com", "20261203T090000Z", "20261203T100000Z", "Planung",
                             extra="ATTACH;FMTTYPE=application/pdf;FILENAME=Agenda.pdf;SIZE=1234:"
                                   "https://files.example.com/agenda.pdf"))
    device = pair(client)
    _body, events = all_changes(device["token"])
    row = next(item for item in events if item["uid"] == "plan@example.com")
    assert row["remote_attachments"] == [{"index": 0, "name": "Agenda.pdf", "mime": "application/pdf", "size": 1234,
                                          "link": "https://files.example.com/agenda.pdf"}]
    assert row["attachments"] == []


# --- Trash (TIME-23, TIMEIOS-14) -------------------------------------------------------------------------------------


def test_TIME29_app3_the_trash_as_the_browser_with_restore_and_empty(client: TestClient, account: Account) -> None:
    device = pair(client)
    body, _events = all_changes(device["token"])
    first = make_event(client, "Erster")
    second = make_event(client, "Zweiter")
    for made in (first, second):
        client.delete(f"/api/events/{made['id']}")
    later, _changed = all_changes(device["token"], body["cursor"])
    assert [row["trashed"] for row in later["removed"]] == [True, True]
    listing = call("GET", "/trash", device["token"], params={"limit": 1}).json()
    assert listing["total"] == 2 and len(listing["items"]) == 1
    browser = client.get("/api/trash", params={"limit": 1}).json()
    assert {**listing, "now": ""} == {**browser, "now": ""}
    assert "reason" in listing["items"][0]
    back = call("POST", f"/trash/events/{first['id']}/restore", device["token"], json={})
    assert back.status_code == 200 and back.json()["id"] == first["id"]
    twice = call("POST", f"/trash/events/{first['id']}/restore", device["token"], json={})
    assert twice.status_code == 404 and twice.json()["detail"]["code"] == "not_in_trash"
    emptied = call("POST", "/trash/empty", device["token"], json={"until": listing["now"]}).json()
    assert emptied["count"] == 1
    assert call("GET", "/trash", device["token"]).json()["total"] == 0
    _final, events = all_changes(device["token"], later["cursor"])
    assert [row["id"] for row in events] == [first["id"]]


def test_TIME29_app3_an_occurrence_and_a_calendar_come_back_from_the_app(client: TestClient,
                                                                          account: Account) -> None:
    device = pair(client)
    series = make_event(client, "Training", "2026-11-02T17:00", "2026-11-02T18:00",
                        rule={"freq": "weekly", "weekdays": ["MO"], "end": "count", "count": 4})
    assert client.delete(f"/api/events/{series['id']}",
                         params={"scope": "this", "occurrence": "20261109T160000Z"}).status_code == 200
    club = make_calendar(client, "Verein")
    make_event(client, "Sitzung", calendar_id=club)
    assert client.delete(f"/api/calendars/{club}").status_code == 200
    items = call("GET", "/trash", device["token"]).json()["items"]
    kinds = {item["kind"]: item["id"] for item in items}
    assert set(kinds) == {"part", "calendar"}
    body, _events = all_changes(device["token"])
    assert club not in [row["id"] for row in body["calendars"]]
    part = call("POST", f"/trash/parts/{kinds['part']}/restore", device["token"], json={})
    assert part.status_code == 200 and part.json()["id"] == series["id"]
    calendar = call("POST", f"/trash/calendars/{club}/restore", device["token"], json={})
    assert calendar.status_code == 200 and calendar.json() == {"id": club}
    again = call("POST", f"/trash/calendars/{club}/restore", device["token"], json={})
    assert again.status_code == 404 and again.json()["detail"]["code"] == "not_in_trash"
    later, events = all_changes(device["token"], body["cursor"])
    assert club in [row["id"] for row in later["calendars"]]
    assert {row["title"] for row in events} == {"Training", "Sitzung"}


def test_TIME29_app3_another_accounts_trash_stays_closed(client: TestClient, account: Account) -> None:
    made = make_event(client, "Geheim")
    client.delete(f"/api/events/{made['id']}")
    with new_client(make_account("kim")) as browser:
        kims = pair(browser)
    assert call("GET", "/trash", kims["token"]).json()["total"] == 0
    refused = call("POST", f"/trash/events/{made['id']}/restore", kims["token"], json={})
    assert refused.status_code == 404


def test_TIME29_app3_an_event_moved_away_is_removed_but_not_trashed(client: TestClient, account: Account) -> None:
    device = pair(client)
    made = make_event(client, "Bleibt")
    body, _events = all_changes(device["token"])
    moved = ops(device["token"], {"ref": ref(), "op": "change", "id": made["id"], "etag": made["etag"],
                                  "event": {"calendar_id": make_calendar(client, "Neu")}})[0]
    assert moved["state"] == "done"
    later, _events = all_changes(device["token"], body["cursor"])
    # Moved to another calendar: gone from the first, but not into the trash.
    assert [row["trashed"] for row in later["removed"]] == [False]


def test_TIME29_app3_an_event_turned_free_under_busy_only_is_removed_but_not_trashed(client: TestClient,
                                                                                    account: Account) -> None:
    mika = make_account("mika")
    family = make_calendar(client, "Familie")
    share(family, mika.id, RIGHT_BUSY)
    made = make_event(client, "Elternabend", calendar_id=family)
    with new_client(mika) as browser:
        device = pair(browser)
    body, events = all_changes(device["token"])
    assert [row["id"] for row in events] == [made["id"]]
    assert client.patch(f"/api/events/{made['id']}", json={"busy": False, "etag": made["etag"]}).status_code == 200
    later, _events = all_changes(device["token"], body["cursor"])
    assert [(row["href"], row["trashed"]) for row in later["removed"]] == [(events[0]["href"], False)]


def test_TIME29_app3_hello_names_the_new_parts(client: TestClient) -> None:
    with app_client() as phone:
        features = set(phone.get("/api/app/1/hello").json()["features"])
    assert {"reminders", "attachments", "trash"} <= features


def test_TIME29_app3_an_upload_may_be_larger_than_an_ordinary_request(client: TestClient, account: Account) -> None:
    assert client.put("/api/settings", json={"upload_max_mb": 20}).status_code == 200
    made = make_event(client, "Video")
    answer = put_file(pair(client)["token"], b"\0" * (17 * 1024 * 1024), name="Film.bin", event_id=made["id"],
                      ref=ref())
    assert answer.status_code == 201 and answer.json()["size"] == 17 * 1024 * 1024
