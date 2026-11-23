"""TIME-23 Trash: what an owner's calendars lost, with its deadline, who deleted it, the time left; restoring exactly
once with series, attachments, reminders, shares and guest link; for good after the deadline or "Empty trash"; the
trash of the calendar's owner only; iCloud and CalDAV events made anew at their source only when they were gone
there, and nothing to a source on the way."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from app.db import SessionLocal
from app.models import (
    RIGHT_EDIT,
    RIGHT_READ,
    Account,
    Attachment,
    Calendar,
    Event,
    EventChange,
    GuestLink,
    RemoteChange,
    ShareNotice,
)
from app.services import accounts, settings_service, shares, sync, trash

from .calendar_helpers import code, make_calendar, make_event, share, window
from .conftest import MEDIA, make_account, new_client
from .remote_helpers import (
    SWITCHES,
    Remote,
    connected,
    dav_server,  # noqa: F401 - the fixture
    event_text,
    mirrored,
    quiet_world,  # noqa: F401 - the fixture
    remote_account,
    round_now,
)
from .test_time9 import inline_media, picture, upload  # noqa: F401 - the fixture
from .test_time19 import guest_client, link_on, token_of
from .test_time20 import calendar_url, device_for, raw

START = datetime(2026, 11, 26, 15, 5, tzinfo=UTC)


class Clock:
    """The server's clock of the trash, set by the test."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def days(self, count: float) -> None:
        self.now = START + timedelta(days=count)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    moment = Clock()
    monkeypatch.setattr(trash, "clock", moment)
    return moment


def listing(client: TestClient, **query: Any) -> dict[str, Any]:
    answer = client.get("/api/trash", params=query)
    assert answer.status_code == 200, answer.text
    return answer.json()


def entry(client: TestClient, kind: str, entry_id: int) -> dict[str, Any]:
    return next(item for item in listing(client)["items"] if item["kind"] == kind and item["id"] == entry_id)


def restore(client: TestClient, kind: str, entry_id: int, calendar_id: int | None = None) -> Any:
    body = {"calendar_id": calendar_id} if calendar_id is not None else None
    return client.post(f"/api/trash/{kind}s/{entry_id}/restore", json=body)


def delete_event(client: TestClient, event_id: int) -> None:
    answer = client.delete(f"/api/events/{event_id}")
    assert answer.status_code == 200, answer.text


def delete_calendar(client: TestClient, calendar_id: int) -> None:
    answer = client.delete(f"/api/calendars/{calendar_id}")
    assert answer.status_code == 200, answer.text


def titles(client: TestClient, **query: Any) -> list[str]:
    return sorted(row["title"] for row in window(client, "2016-01-01T00:00:00Z", "2016-12-31T00:00:00Z", **query)) \
        if query.get("old") else sorted(row["title"] for row in window(client))


def row_of(event_id: int) -> Event | None:
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        if row is not None:
            db.expunge(row)
        return row


@pytest.fixture
def anna(client: TestClient) -> Iterator[tuple[Account, TestClient]]:
    person = make_account("anna")
    with new_client(person) as browser:
        yield person, browser


# --- AC-1, AC-6, EC-3, EC-4, EC-5 ---------------------------------------------------------------------------------


def test_time23_ac1_entries_with_color_title_date_calendar_and_who_deleted_and_the_deadline_on_top(
        client: TestClient, account: Account, anna: tuple[Account, TestClient], clock: Clock) -> None:
    person, browser = anna
    familie = make_calendar(client, "Familie", color="green")
    share(familie, person.id, RIGHT_EDIT)
    kino = make_event(client, "Kino mit Anna", "2026-11-27T20:00", "2026-11-27T22:00", calendar_id=familie)
    clock.days(0.5)
    delete_event(browser, kino["id"])
    verein = make_calendar(client, "Verein alt", color="teal")
    for number in range(3):
        make_event(client, f"Training {number}", calendar_id=verein)
    clock.days(1)
    delete_calendar(client, verein)
    found = listing(client)
    assert found["trash_days"] == 30 and found["total"] == 2
    first, second = found["items"]
    assert first["kind"] == "calendar" and first["title"] == "Verein alt" and first["color"] == "teal"
    assert first["events"] == 3 and first["deleted_by"] == {"self": True, "name": account.shown_name}
    assert second["kind"] == "event" and second["title"] == "Kino mit Anna" and second["color"] == "green"
    assert second["calendar"]["name"] == "Familie" and second["calendar_gone"] is False
    assert second["start"] == "2026-11-27T19:00:00+00:00" and second["time_zone"] == "Europe/Berlin"
    assert second["deleted_by"] == {"self": False, "name": "Anna"}
    assert second["deleted_at"] == (START + timedelta(days=0.5)).isoformat()


def test_time23_ac6_time_left_in_days_and_orange_in_the_last_seven(client: TestClient, account: Account,
                                                                    clock: Clock) -> None:
    made = make_event(client, "Zahnarzt")
    delete_event(client, made["id"])
    expected = {0: (30, False), 22: (8, False), 23: (7, True), 29: (1, True), 29.9: (1, True)}
    for day, (left, warn) in expected.items():
        clock.days(day)
        item = entry(client, "event", made["id"])
        assert (item["days_left"], item["warn"]) == (left, warn), day
    clock.days(30)
    assert listing(client)["items"] == []


def test_time23_ec3_an_empty_trash_has_no_entries_and_empty_does_nothing(client: TestClient, account: Account,
                                                                         clock: Clock) -> None:
    make_event(client, "Bleibt")
    found = listing(client)
    assert found["items"] == [] and found["total"] == 0 and found["trash_days"] == 30
    answer = client.post("/api/trash/empty", json={"until": found["now"]})
    assert answer.status_code == 200 and answer.json() == {"events": 0, "calendars": 0}
    assert titles(client) == ["Bleibt"]


def test_time23_ec4_five_thousand_entries_come_page_by_page_and_long_titles_whole(client: TestClient,
                                                                                 account: Account,
                                                                                 clock: Clock) -> None:
    personal = make_calendar(client, "Import")
    long_title = "Ä" * 200
    with SessionLocal() as db:
        rows = [{"calendar_id": personal, "uid": f"u{number}", "href": f"u{number}.ics", "ical": "", "etag": "e",
                 "title": long_title if number == 4999 else f"Termin {number}", "starts_at": START,
                 "ends_at": START + timedelta(hours=1), "deleted_at": START + timedelta(seconds=number),
                 "deleted_by": account.id, "trash_until": START + timedelta(days=30)} for number in range(5000)]
        db.execute(insert(Event), rows)
        db.commit()
    first = listing(client, limit=100)
    assert first["total"] == 5000 and len(first["items"]) == 100
    assert first["items"][0]["title"] == long_title
    last = listing(client, offset=4900, limit=100)
    assert len(last["items"]) == 100 and last["items"][-1]["title"] == "Termin 0"
    seen = {item["id"] for page in range(0, 5000, 500) for item in listing(client, offset=page, limit=500)["items"]}
    assert len(seen) == 5000


def test_time23_ec5_titles_and_names_come_as_they_were_typed(client: TestClient, account: Account,
                                                            clock: Clock) -> None:
    hostile = '<img src=x onerror=alert(1)> 🎂 "Ä"'
    calendar = make_calendar(client, "<b>Kalender</b> 🗓")
    made = make_event(client, hostile, calendar_id=calendar)
    delete_event(client, made["id"])
    item = entry(client, "event", made["id"])
    assert item["title"] == hostile and item["calendar"]["name"] == "<b>Kalender</b> 🗓"


# --- AC-2, AC-4, AC-7, EC-16: back ---------------------------------------------------------------------------------


def test_time23_ac2_ec10_restored_by_the_owner_it_is_back_for_everybody_and_their_devices(
        client: TestClient, account: Account, anna: tuple[Account, TestClient], clock: Clock) -> None:
    person, browser = anna
    familie = make_calendar(client, "Familie")
    share(familie, person.id, RIGHT_EDIT)
    made = make_event(client, "Kino", calendar_id=familie)
    delete_event(browser, made["id"])
    # The editor who deleted it has nothing in its own trash; it stands with the owner (EC-10).
    assert listing(browser)["items"] == []
    assert restore(browser, "event", made["id"]).status_code == 404
    assert titles(browser) == [] and titles(client) == []
    answer = restore(client, "event", made["id"])
    assert answer.status_code == 200, answer.text
    assert titles(client) == ["Kino"] and titles(browser) == ["Kino"]
    with SessionLocal() as db:
        last = db.scalar(select(EventChange).where(EventChange.calendar_id == familie)
                         .order_by(EventChange.id.desc()))
        assert last is not None and last.href == row_of(made["id"]).href and not last.deleted
    assert listing(client)["items"] == []


def test_time23_ac2_ec2_deleted_on_a_device_it_is_in_the_trash_and_comes_back_to_the_device(
        client: TestClient, account: Account, clock: Clock) -> None:
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Elternabend", calendar_id=familie)
    href = row_of(made["id"]).href
    password = device_for(account)
    gone = raw("DELETE", calendar_url(account, familie) + href, account, password)
    assert gone.status_code == 204
    assert entry(client, "event", made["id"])["deleted_by"]["self"] is True
    assert restore(client, "event", made["id"]).status_code == 200
    back = raw("GET", calendar_url(account, familie) + href, account, password)
    assert back.status_code == 200 and "Elternabend" in back.text


def test_time23_ac4_a_series_comes_back_with_its_exceptions(client: TestClient, account: Account,
                                                           clock: Clock) -> None:
    rule = {"freq": "weekly", "weekdays": ["MO"]}
    made = make_event(client, "Training", "2026-11-02T17:00", "2026-11-02T18:00", rule=rule)
    gone = client.delete(f"/api/events/{made['id']}", params={"scope": "this", "occurrence": "20261109T160000Z"})
    assert gone.status_code == 200, gone.text
    moved = client.patch(f"/api/events/{made['id']}", json={
        "scope": "this", "occurrence": "20261116T160000Z", "title": "Training (Halle Süd)", "all_day": False,
        "start": "2026-11-16T18:00", "end": "2026-11-16T19:00", "time_zone": "Europe/Berlin",
        "etag": client.get(f"/api/events/{made['id']}").json()["etag"]})
    assert moved.status_code == 200, moved.text
    before = window(client)
    delete_event(client, made["id"])
    item = entry(client, "event", made["id"])
    assert item["recurring"] is True and item["exceptions"] is True
    assert restore(client, "event", made["id"]).status_code == 200
    assert window(client) == before


def test_time23_ac7_a_calendar_comes_back_with_its_events_shares_collections_and_guest_link(
        client: TestClient, account: Account, anna: tuple[Account, TestClient], clock: Clock) -> None:
    person, browser = anna
    verein = make_calendar(client, "Verein")
    for number in range(48):
        make_event(client, f"Training {number}", "2026-11-26T18:30", "2026-11-26T20:00", calendar_id=verein)
    share(verein, person.id, RIGHT_READ)
    collection = client.post("/api/collections", json={"name": "Sport", "calendar_ids": [verein]})
    assert collection.status_code == 201
    token = token_of(link_on(client, verein))
    with guest_client() as guest:
        assert guest.get(f"/api/guest/{token}").status_code == 200
    delete_calendar(client, verein)
    assert entry(client, "calendar", verein)["events"] == 48
    assert len(titles(browser)) == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ShareNotice).where(ShareNotice.account_id == person.id))
    with guest_client() as guest:
        assert guest.get(f"/api/guest/{token}").status_code == 404
    answer = restore(client, "calendar", verein)
    assert answer.status_code == 200, answer.text
    assert len(titles(client)) == 48 and len(titles(browser)) == 48
    assert client.get("/api/collections").json()["collections"][0]["calendar_ids"] == [verein]
    with SessionLocal() as db:
        assert not db.scalar(select(func.count()).select_from(ShareNotice).where(ShareNotice.account_id == person.id))
    with guest_client() as guest:
        assert guest.get(f"/api/guest/{token}").status_code == 200
    assert listing(client)["items"] == []


def test_time23_ac7_ac2_a_restored_calendar_counts_every_event_as_changed_for_devices(
        client: TestClient, account: Account, clock: Clock) -> None:
    verein = make_calendar(client, "Verein")
    hrefs = {row_of(make_event(client, f"Training {number}", calendar_id=verein)["id"]).href for number in range(5)}
    delete_calendar(client, verein)
    with SessionLocal() as db:
        before = db.scalar(select(func.max(EventChange.id))) or 0
    assert restore(client, "calendar", verein).status_code == 200
    with SessionLocal() as db:
        changed = set(db.scalars(select(EventChange.href).where(EventChange.calendar_id == verein,
                                                               EventChange.id > before)))
        assert changed == hrefs
        assert not db.scalar(select(func.count()).select_from(RemoteChange))


def test_time23_ac7_a_guest_link_that_ran_out_meanwhile_stays_out(client: TestClient, account: Account,
                                                                  clock: Clock) -> None:
    verein = make_calendar(client, "Verein")
    token = token_of(link_on(client, verein))
    delete_calendar(client, verein)
    with SessionLocal() as db:
        link = db.scalar(select(GuestLink).where(GuestLink.calendar_id == verein))
        assert link is not None
        link.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    assert restore(client, "calendar", verein).status_code == 200
    with guest_client() as guest:
        assert guest.get(f"/api/guest/{token}").status_code == 404


def test_time23_ec16_back_with_attachments_reminders_place_and_notes(client: TestClient, account: Account,
                                                                     clock: Clock, inline_media: None) -> None:
    made = make_event(client, "Zahnarzt", location="Praxis Dr. Sommer, Hauptstr. 3", notes="Karte mitnehmen",
                      reminders=[15, 1440])
    sent = upload(client, made["id"], "ueberweisung.jpg", picture())
    assert sent.status_code in (200, 201), sent.text
    before = client.get(f"/api/events/{made['id']}").json()
    delete_event(client, made["id"])
    assert restore(client, "event", made["id"]).status_code == 200
    after = client.get(f"/api/events/{made['id']}").json()
    for name in ("title", "location", "notes", "reminders", "attachments", "start", "end"):
        assert after[name] == before[name], name
    file_id = after["attachments"][0]["id"]
    assert (MEDIA / file_id).exists()


# --- AC-3, EC-13, EC-14: for good ----------------------------------------------------------------------------------


def test_time23_ac3_after_the_deadline_it_goes_for_good_with_its_files(client: TestClient, account: Account,
                                                                       clock: Clock, inline_media: None) -> None:
    made = make_event(client, "Weg")
    stays = make_event(client, "Noch da")
    sent = upload(client, made["id"], "bild.jpg", picture())
    file_id = sent.json()["id"]
    delete_event(client, made["id"])
    clock.days(10)
    delete_event(client, stays["id"])
    clock.days(29.9)
    assert trash.expire(clock.now) == 0
    clock.days(30)
    assert trash.expire(clock.now) == 1
    assert row_of(made["id"]) is None and row_of(stays["id"]) is not None
    with SessionLocal() as db:
        assert db.get(Attachment, file_id) is None
    assert not (MEDIA / file_id).exists()
    assert [item["title"] for item in listing(client)["items"]] == ["Noch da"]


def test_time23_ac3_empty_takes_what_was_deleted_until_the_list_was_loaded(client: TestClient, account: Account,
                                                                           clock: Clock) -> None:
    first = make_event(client, "Erster")
    calendar = make_calendar(client, "Alt")
    make_event(client, "Im Kalender", calendar_id=calendar)
    delete_event(client, first["id"])
    delete_calendar(client, calendar)
    seen = listing(client)["now"]
    clock.days(0.1)
    later = make_event(client, "Später gelöscht")
    delete_event(client, later["id"])
    answer = client.post("/api/trash/empty", json={"until": seen})
    assert answer.status_code == 200 and answer.json() == {"events": 1, "calendars": 1}
    assert [item["title"] for item in listing(client)["items"]] == ["Später gelöscht"]
    with SessionLocal() as db:
        assert db.get(Calendar, calendar) is None


def test_time23_ec13_a_new_deadline_counts_for_what_is_deleted_after(client: TestClient, account: Account,
                                                                     clock: Clock) -> None:
    before = make_event(client, "Vorher")
    after = make_event(client, "Nachher")
    delete_event(client, before["id"])
    with SessionLocal() as db:
        settings_service.save(db, {"trash_days": 10})
    delete_event(client, after["id"])
    found = listing(client)
    assert found["trash_days"] == 10
    left = {item["title"]: item["days_left"] for item in found["items"]}
    assert left == {"Vorher": 30, "Nachher": 10}
    clock.days(11)
    trash.expire(clock.now)
    assert [item["title"] for item in listing(client)["items"]] == ["Vorher"]


def test_time23_ec13_a_calendar_for_good_leaves_its_own_later_entries(client: TestClient, account: Account,
                                                                      clock: Clock) -> None:
    calendar = make_calendar(client, "Verein")
    single = make_event(client, "Einzeln gelöscht", calendar_id=calendar)
    delete_event(client, single["id"])
    with SessionLocal() as db:
        settings_service.save(db, {"trash_days": 5})
    delete_calendar(client, calendar)
    clock.days(6)
    trash.expire(clock.now)
    item = entry(client, "event", single["id"])
    assert item["calendar_gone"] is True and item["days_left"] == 24
    assert all(item["kind"] != "calendar" for item in listing(client)["items"])


def test_time23_ec14_past_events_restore_alike_and_the_deadline_goes_by_the_server_clock(
        client: TestClient, account: Account, clock: Clock) -> None:
    old = make_event(client, "Damals", "2016-05-04T10:00", "2016-05-04T11:00")
    delete_event(client, old["id"])
    item = entry(client, "event", old["id"])
    assert item["deleted_at"] == START.isoformat() and item["expires_at"] == (START + timedelta(days=30)).isoformat()
    assert restore(client, "event", old["id"]).status_code == 200
    assert titles(client, old=True) == ["Damals"]


# --- EC-1, EC-6, EC-7: asking for a target, once, all or nothing --------------------------------------------------


def test_time23_ec1_an_event_whose_calendar_went_since_asks_for_a_writable_target(
        client: TestClient, account: Account, anna: tuple[Account, TestClient], clock: Clock) -> None:
    _person, browser = anna
    schule = make_calendar(client, "Schule")
    made = make_event(client, "Elternsprechtag", calendar_id=schule)
    delete_event(client, made["id"])
    delete_calendar(client, schule)
    assert entry(client, "event", made["id"])["calendar_gone"] is True
    asked = restore(client, "event", made["id"])
    assert asked.status_code == 409 and code(asked) == "target_needed"
    annas = make_calendar(browser, "Annas")
    share(annas, account.id, RIGHT_READ)
    assert restore(client, "event", made["id"], annas).status_code == 403
    familie = make_calendar(client, "Familie")
    answer = restore(client, "event", made["id"], familie)
    assert answer.status_code == 200 and answer.json()["calendar_id"] == familie
    assert [row["calendar_id"] for row in window(client)] == [familie]


def test_time23_ec6_restored_exactly_once_by_two_windows_a_double_click_or_a_deadline_at_the_same_time(
        client: TestClient, account: Account, clock: Clock) -> None:
    made = make_event(client, "Kino")
    delete_event(client, made["id"])
    answers: list[int] = []

    def attempt() -> None:
        with new_client(account) as window_:
            answers.append(restore(window_, "event", made["id"]).status_code)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(answers) == [200, 404, 404, 404]
    assert titles(client) == ["Kino"]
    again = restore(client, "event", made["id"])
    assert again.status_code == 404 and code(again) == "not_in_trash"
    # The deadline runs out in the same moment: gone, never back as well.
    other = make_event(client, "Abgelaufen")
    delete_event(client, other["id"])
    clock.days(30)
    assert restore(client, "event", other["id"]).status_code == 404
    trash.expire(clock.now)
    assert row_of(other["id"]) is None


def test_time23_ec7_a_calendar_broken_off_while_restoring_stays_whole_in_the_trash(
        client: TestClient, account: Account, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    verein = make_calendar(client, "Verein")
    for number in range(20):
        make_event(client, f"Training {number}", calendar_id=verein)
    delete_calendar(client, verein)

    def broken(_db: Any, _calendar_id: int) -> None:
        raise RuntimeError("the server stops here")

    monkeypatch.setattr(shares, "calendar_restored", broken)
    with SessionLocal() as db, pytest.raises(RuntimeError):
        trash.restore_calendar(db, db.get(Account, account.id), verein)
    with SessionLocal() as db:
        row = db.get(Calendar, verein)
        assert row is not None and row.deleted_at is not None
    assert entry(client, "calendar", verein)["events"] == 20
    monkeypatch.undo()
    assert restore(client, "calendar", verein).status_code == 200
    assert len(titles(client)) == 20


def test_time23_the_same_event_again_in_its_calendar_is_refused_not_doubled(client: TestClient, account: Account,
                                                                           clock: Clock) -> None:
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Kino", calendar_id=familie)
    href = row_of(made["id"]).href
    text = row_of(made["id"]).ical
    password = device_for(account)
    delete_event(client, made["id"])
    put = raw("PUT", calendar_url(account, familie) + href, account, password, text,
              **{"Content-Type": "text/calendar"})
    assert put.status_code in (201, 204), put.text
    answer = restore(client, "event", made["id"])
    assert answer.status_code == 409 and code(answer) == "event_exists"
    assert titles(client) == ["Kino"]


# --- EC-10, EC-11, EC-12, EC-15: whose trash, who deleted ----------------------------------------------------------


def test_time23_ec10_nobody_else_lists_restores_or_empties_an_owner_s_trash(
        client: TestClient, account: Account, anna: tuple[Account, TestClient], clock: Clock) -> None:
    person, browser = anna
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Privat", calendar_id=familie)
    delete_event(client, made["id"])
    delete_calendar(client, make_calendar(client, "Alt"))
    share(familie, person.id, RIGHT_EDIT)
    assert listing(browser)["items"] == []
    assert restore(browser, "event", made["id"]).status_code == 404
    assert restore(browser, "event", made["id"], make_calendar(browser, "Meins")).status_code == 404
    assert restore(browser, "calendar", familie).status_code == 404
    assert browser.post("/api/trash/empty", json={}).status_code == 200
    assert listing(client)["total"] == 2
    with TestClient(client.app, base_url="http://testserver") as nobody:
        assert nobody.get("/api/trash").status_code == 401


def test_time23_ec11_ec2_deleted_through_the_calendar_account_it_names_the_device_s_person(
        client: TestClient, account: Account, anna: tuple[Account, TestClient], clock: Clock) -> None:
    person, _browser = anna
    familie = make_calendar(client, "Familie")
    share(familie, person.id, RIGHT_EDIT)
    made = make_event(client, "Schwimmen", calendar_id=familie)
    password = device_for(person, "iPhone von Anna")
    gone = raw("DELETE", f"/dav/calendars/{person.name}/{calendar_url(account, familie).split('/')[4]}/"
               f"{row_of(made['id']).href}", person, password)
    assert gone.status_code == 204, gone.text
    assert entry(client, "event", made["id"])["deleted_by"] == {"self": False, "name": "Anna"}


def test_time23_ec12_the_name_of_an_account_that_is_gone_stays_as_it_was(
        client: TestClient, account: Account, anna: tuple[Account, TestClient], clock: Clock) -> None:
    person, browser = anna
    familie = make_calendar(client, "Familie")
    share(familie, person.id, RIGHT_EDIT)
    made = make_event(client, "Kino", calendar_id=familie)
    delete_event(browser, made["id"])
    with SessionLocal() as db:
        row = db.get(Account, person.id)
        assert row is not None
        row.display_name = "Anna Neu"
        db.commit()
    assert entry(client, "event", made["id"])["deleted_by"]["name"] == "Anna Neu"
    with SessionLocal() as db:
        row = db.get(Account, person.id)
        assert row is not None
        accounts.delete_account(db, row)
        db.commit()
    assert entry(client, "event", made["id"])["deleted_by"]["name"] == "Anna"


def test_time23_ec15_a_calendar_deleted_while_another_has_an_event_open_says_so(
        client: TestClient, account: Account, anna: tuple[Account, TestClient], clock: Clock) -> None:
    person, browser = anna
    familie = make_calendar(client, "Familie")
    share(familie, person.id, RIGHT_EDIT)
    made = make_event(client, "Kino", calendar_id=familie)
    assert browser.get(f"/api/events/{made['id']}").status_code == 200
    delete_calendar(client, familie)
    opened = browser.get(f"/api/events/{made['id']}")
    assert opened.status_code == 409 and code(opened) == "calendar_gone"
    changed = browser.patch(f"/api/events/{made['id']}", json={"title": "Kino 2", "etag": "x"})
    assert changed.status_code == 409
    password = device_for(person)
    listed = raw("PROPFIND", f"/dav/calendars/{person.name}/", person, password, Depth="1")
    assert "Familie" not in listed.text


# --- AC-5, EC-8, EC-9: iCloud, Google and CalDAV --------------------------------------------------------------------


@pytest.fixture
def remote() -> Iterator[Remote]:
    yield from remote_account()


def _source_event(client: TestClient, server: str, remote: Remote, title: str = "Teamrunde") -> tuple[int, int, int]:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "team.ics", event_text("team@example.com", title))
    source = connected(client, server, remote, ["Arbeit"])
    round_now(source)
    calendar = mirrored(source)[0]
    with SessionLocal() as db:
        row = db.scalar(select(Event).where(Event.calendar_id == calendar.id))
        assert row is not None
        return source, arbeit, row.id


def test_time23_ac5_ec8_deleted_in_nextime_and_restored_it_is_made_anew_at_the_source(
        dav_server: str, client: TestClient, account: Account, remote: Remote, clock: Clock) -> None:
    source, arbeit, event_id = _source_event(client, dav_server, remote)
    delete_event(client, event_id)
    round_now(source)
    assert remote.get(arbeit, "team.ics").status_code == 404
    # The source does not answer: restored here at once, waiting for it (EC-8).
    SWITCHES.down = True
    assert restore(client, "event", event_id).status_code == 200
    assert [(row["title"], row["waiting"]) for row in window(client)] == [("Teamrunde", True)]
    SWITCHES.down = False
    round_now(source)
    back = remote.get(arbeit, "team.ics")
    assert back.status_code == 200 and "Teamrunde" in back.text
    assert [(row["title"], row["waiting"]) for row in window(client)] == [("Teamrunde", False)]


def test_time23_ac5_ec8_restored_while_its_deletion_is_on_the_way_it_comes_back_at_the_source(
        dav_server: str, client: TestClient, account: Account, remote: Remote, clock: Clock,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of TIME-23, finding 1: "Wiederherstellen" in the very moment the round sends the deletion."""
    from app.services import remote_dav

    source, arbeit, event_id = _source_event(client, dav_server, remote)
    delete_event(client, event_id)
    answers: list[int] = []
    workers: list[threading.Thread] = []
    sending = remote_dav.Remote.delete

    def deleting(self: Any, url: str, etag: str) -> None:
        def attempt() -> None:
            with new_client(account) as other:
                answers.append(restore(other, "event", event_id).status_code)

        worker = threading.Thread(target=attempt)
        workers.append(worker)
        worker.start()
        worker.join(1.0)
        sending(self, url, etag)

    monkeypatch.setattr(remote_dav.Remote, "delete", deleting)
    round_now(source)
    workers[0].join(30)
    monkeypatch.setattr(remote_dav.Remote, "delete", sending)
    assert answers == [200]
    round_now(source)
    back = remote.get(arbeit, "team.ics")
    assert back.status_code == 200 and "Teamrunde" in back.text
    round_now(source)
    assert [(row["title"], row["waiting"]) for row in window(client)] == [("Teamrunde", False)]
    assert listing(client)["items"] == []


def test_time23_ac5_restoring_waits_for_a_round_and_says_so_when_it_takes_too_long(
        dav_server: str, client: TestClient, account: Account, remote: Remote, clock: Clock,
        monkeypatch: pytest.MonkeyPatch) -> None:
    source, _arbeit, event_id = _source_event(client, dav_server, remote)
    delete_event(client, event_id)
    monkeypatch.setattr(trash, "SOURCE_WAIT", 0.2)
    lock = sync._lock(source)
    lock.acquire()
    try:
        busy = restore(client, "event", event_id)
    finally:
        lock.release()
    assert busy.status_code == 409 and code(busy) == "source_busy"
    assert entry(client, "event", event_id)["title"] == "Teamrunde"
    assert restore(client, "event", event_id).status_code == 200


def test_time23_ac5_a_deletion_still_waiting_is_dropped_and_nothing_goes_to_the_source(
        dav_server: str, client: TestClient, account: Account, remote: Remote, clock: Clock) -> None:
    source, arbeit, event_id = _source_event(client, dav_server, remote)
    before = remote.get(arbeit, "team.ics").headers["etag"]
    SWITCHES.down = True
    delete_event(client, event_id)
    assert restore(client, "event", event_id).status_code == 200
    SWITCHES.down = False
    SWITCHES.seen.clear()
    round_now(source)
    assert not [seen for seen in SWITCHES.seen if seen[0] in ("PUT", "DELETE")]
    assert remote.get(arbeit, "team.ics").headers["etag"] == before
    with SessionLocal() as db:
        assert not db.scalar(select(func.count()).select_from(RemoteChange))
    assert [row["waiting"] for row in window(client)] == [False]


def test_time23_ac5_deleted_at_the_source_it_is_not_in_the_trash_and_for_good_nothing_goes_there(
        dav_server: str, client: TestClient, account: Account, remote: Remote, clock: Clock) -> None:
    source, arbeit, _event_id = _source_event(client, dav_server, remote)
    remote.put(arbeit, "zwei.ics", event_text("zwei@example.com", "Zweiter"))
    round_now(source)
    remote.delete(arbeit, "team.ics")
    round_now(source)
    assert listing(client)["items"] == []
    second = next(row["id"] for row in window(client) if row["title"] == "Zweiter")
    delete_event(client, second)
    round_now(source)
    SWITCHES.seen.clear()
    clock.days(31)
    trash.expire(clock.now)
    round_now(source)
    assert not [seen for seen in SWITCHES.seen if seen[0] in ("PUT", "DELETE")]
    assert row_of(second) is None


def test_time23_ec9_its_account_disconnected_meanwhile_it_asks_for_a_target(
        dav_server: str, client: TestClient, account: Account, remote: Remote, clock: Clock) -> None:
    source, _arbeit, event_id = _source_event(client, dav_server, remote)
    delete_event(client, event_id)
    round_now(source)
    answer = client.request("DELETE", f"/api/sources/{source}", params={"drop_waiting": "true"})
    assert answer.status_code in (200, 204), answer.text
    item = entry(client, "event", event_id)
    assert item["calendar_gone"] is True and item["title"] == "Teamrunde"
    assert all(row["name"] != "Arbeit" for row in client.get("/api/calendars").json()["calendars"])
    asked = restore(client, "event", event_id)
    assert asked.status_code == 409 and code(asked) == "target_needed"
    personal = make_calendar(client, "Persönlich")
    assert restore(client, "event", event_id, personal).status_code == 200
    assert [(row["title"], row["calendar_id"]) for row in window(client)] == [("Teamrunde", personal)]
    # Its last event out, the calendar kept for it goes at once.
    with SessionLocal() as db:
        assert not db.scalar(select(func.count()).select_from(Calendar).where(Calendar.orphan.is_(True)))


def test_time23_ec9_an_orphan_goes_with_its_last_event(dav_server: str, client: TestClient, account: Account,
                                                      remote: Remote, clock: Clock) -> None:
    source, _arbeit, event_id = _source_event(client, dav_server, remote)
    delete_event(client, event_id)
    round_now(source)
    client.request("DELETE", f"/api/sources/{source}", params={"drop_waiting": "true"})
    clock.days(31)
    trash.expire(clock.now)
    with SessionLocal() as db:
        assert db.get(Event, event_id) is None
        assert not db.scalar(select(func.count()).select_from(Calendar).where(Calendar.orphan.is_(True)))


# --- Parts of a series (TIME-5 AC-6, AC-4; review of TIME-23, finding 2) -------------------------------------------

RULE = {"freq": "weekly", "weekdays": ["MO"]}


def _series(client: TestClient, **values: Any) -> int:
    return make_event(client, "Training", "2026-11-02T17:00", "2026-11-02T18:00", rule=RULE, **values)["id"]


def _etag(client: TestClient, event_id: int) -> str:
    return client.get(f"/api/events/{event_id}").json()["etag"]


def _delete_part(client: TestClient, event_id: int, scope: str, occurrence: str) -> None:
    answer = client.delete(f"/api/events/{event_id}", params={"scope": scope, "occurrence": occurrence})
    assert answer.status_code == 200, answer.text


def _move(client: TestClient, event_id: int, occurrence: str, start: str, end: str, title: str) -> None:
    moved = client.patch(f"/api/events/{event_id}", json={
        "scope": "this", "occurrence": occurrence, "title": title, "all_day": False, "start": start, "end": end,
        "time_zone": "Europe/Berlin", "notes": "Halle Süd", "etag": _etag(client, event_id)})
    assert moved.status_code == 200, moved.text


def _shown(client: TestClient) -> list[tuple[str, str]]:
    return [(row["start"], row["title"]) for row in window(client)]


def parts(client: TestClient) -> list[dict[str, Any]]:
    return [item for item in listing(client)["items"] if item["kind"] == "part"]


def test_time23_ac4_tm5_ac6_one_occurrence_goes_into_the_trash_and_back_with_its_change(
        client: TestClient, account: Account, clock: Clock) -> None:
    series = _series(client)
    _move(client, series, "20261116T160000Z", "2026-11-16T18:00", "2026-11-16T19:00", "Training (Halle Süd)")
    before = _shown(client)
    _delete_part(client, series, "this", "20261116T160000Z")
    clock.days(0.1)
    _delete_part(client, series, "this", "20261123T160000Z")
    shown = [title for _start, title in _shown(client)]
    assert "Training (Halle Süd)" not in shown and len(shown) == len(before) - 2
    found = parts(client)
    assert [(item["part"], item["title"], item["start"], item["recurring"]) for item in found] == [
        ("occurrence", "Training", "2026-11-23T16:00:00+00:00", False),
        ("occurrence", "Training (Halle Süd)", "2026-11-16T17:00:00+00:00", False)]
    assert all(item["calendar"]["name"] == "Personal" or item["calendar"]["name"] for item in found)
    for item in found:
        answer = restore(client, "part", item["id"])
        assert answer.status_code == 200, answer.text
    assert _shown(client) == before
    detail = client.get(f"/api/events/{series}", params={"occurrence": "20261116T160000Z"}).json()
    assert detail["notes"] == "Halle Süd" and detail["start"] == "2026-11-16T18:00"
    assert parts(client) == []


def test_time23_ac4_the_following_go_into_the_trash_with_their_exceptions_and_come_back(
        client: TestClient, account: Account, clock: Clock) -> None:
    series = _series(client)
    _move(client, series, "20261130T160000Z", "2026-11-30T19:00", "2026-11-30T20:00", "Training spät")
    before = _shown(client)
    _delete_part(client, series, "following", "20261123T160000Z")
    assert [start for start, _title in _shown(client)] == ["2026-11-02T16:00:00Z", "2026-11-09T16:00:00Z",
                                                          "2026-11-16T16:00:00Z"]
    item = parts(client)[0]
    assert (item["part"], item["recurring"], item["exceptions"]) == ("following", True, True)
    assert restore(client, "part", item["id"]).status_code == 200
    assert _shown(client) == before
    assert [row["id"] for row in window(client)] == [series] * len(before)


def test_time23_ac4_the_following_after_the_series_changed_come_back_as_a_series_of_their_own(
        client: TestClient, account: Account, clock: Clock) -> None:
    series = _series(client)
    _delete_part(client, series, "following", "20261123T160000Z")
    changed = client.patch(f"/api/events/{series}", json={"title": "Training neu", "etag": _etag(client, series)})
    assert changed.status_code == 200, changed.text
    answer = restore(client, "part", parts(client)[0]["id"])
    assert answer.status_code == 200 and answer.json()["id"] != series
    rows = window(client)
    assert [(row["start"][:10], row["title"]) for row in rows if row["id"] == series] == [
        ("2026-11-02", "Training neu"), ("2026-11-09", "Training neu"), ("2026-11-16", "Training neu")]
    tail = [row for row in rows if row["id"] != series]
    assert tail[0]["start"] == "2026-11-23T16:00:00Z" and {row["title"] for row in tail} == {"Training"}
    assert len(tail) == 6


def test_time23_ac4_an_occurrence_the_series_no_longer_has_comes_back_as_its_own_event(
        client: TestClient, account: Account, clock: Clock) -> None:
    series = _series(client)
    _delete_part(client, series, "this", "20261123T160000Z")
    tuesdays = client.patch(f"/api/events/{series}", json={
        "rule": {"freq": "weekly", "weekdays": ["TU"]}, "etag": _etag(client, series)})
    assert tuesdays.status_code == 200, tuesdays.text
    answer = restore(client, "part", parts(client)[0]["id"])
    assert answer.status_code == 200 and answer.json()["id"] != series
    single = [row for row in window(client) if row["id"] == answer.json()["id"]]
    assert [(row["start"], row["title"], row["recurring"]) for row in single] == [
        ("2026-11-23T16:00:00Z", "Training", False)]


def test_time23_ac4_a_part_of_a_series_in_the_trash_waits_for_its_series(client: TestClient, account: Account,
                                                                       clock: Clock) -> None:
    series = _series(client)
    _delete_part(client, series, "this", "20261123T160000Z")
    delete_event(client, series)
    item = parts(client)[0]
    assert item["series_gone"] is True
    gone = restore(client, "part", item["id"])
    assert gone.status_code == 409 and code(gone) == "series_gone"
    assert restore(client, "event", series).status_code == 200
    assert restore(client, "part", item["id"]).status_code == 200
    assert "2026-11-23T16:00:00Z" in [start for start, _title in _shown(client)]
    again = restore(client, "part", item["id"])
    assert again.status_code == 404 and code(again) == "not_in_trash"


def test_time23_ac3_parts_go_for_good_with_the_deadline_and_with_empty(client: TestClient, account: Account,
                                                                      clock: Clock) -> None:
    series = _series(client)
    _delete_part(client, series, "this", "20261123T160000Z")
    clock.days(5)
    _delete_part(client, series, "following", "20261207T160000Z")
    clock.days(30)
    trash.expire(clock.now)
    assert [item["part"] for item in parts(client)] == ["following"]
    answer = client.post("/api/trash/empty", json={"until": listing(client)["now"]})
    assert answer.status_code == 200 and answer.json()["events"] == 1
    assert parts(client) == []
    assert len(window(client)) == 4


def test_time23_ec10_an_occurrence_deleted_by_an_editor_stands_with_the_owner(
        client: TestClient, account: Account, anna: tuple[Account, TestClient], clock: Clock) -> None:
    person, browser = anna
    familie = make_calendar(client, "Familie")
    share(familie, person.id, RIGHT_EDIT)
    series = _series(client, calendar_id=familie)
    _delete_part(browser, series, "this", "20261123T160000Z")
    assert listing(browser)["items"] == []
    item = parts(client)[0]
    assert item["deleted_by"] == {"self": False, "name": "Anna"}
    assert restore(browser, "part", item["id"]).status_code == 404
    assert restore(client, "part", item["id"]).status_code == 200


def test_time23_ac5_parts_of_a_source_series_go_back_to_the_source_without_loss(
        dav_server: str, client: TestClient, account: Account, remote: Remote, clock: Clock) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "team.ics", event_text("team@example.com", "Teamrunde", "20261102T100000", "20261102T110000",
                                              extra="RRULE:FREQ=WEEKLY;BYDAY=MO\r\nX-APPLE-KEEP:bleibt\r\n"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    series = window(client)[0]["id"]
    _delete_part(client, series, "this", "20261123T090000Z")
    round_now(source)
    assert "EXDATE" in remote.get(arbeit, "team.ics").text
    _delete_part(client, series, "following", "20261207T090000Z")
    round_now(source)
    assert "UNTIL" in remote.get(arbeit, "team.ics").text
    for item in parts(client):
        assert restore(client, "part", item["id"]).status_code == 200
    round_now(source)
    back = remote.get(arbeit, "team.ics").text.replace("\r\n ", "")
    assert "EXDATE" not in back and "UNTIL" not in back and "X-APPLE-KEEP:bleibt" in back
    assert "2026-11-23T09:00:00Z" in [row["start"] for row in window(client)]
    assert [row["waiting"] for row in window(client)][:1] == [False]


def test_time23_ec6_a_calendar_past_its_deadline_is_not_restored(client: TestClient, account: Account,
                                                                  clock: Clock) -> None:
    verein = make_calendar(client, "Verein")
    delete_calendar(client, verein)
    clock.days(30)
    late = restore(client, "calendar", verein)
    assert late.status_code == 404 and code(late) == "not_in_trash"
    with SessionLocal() as db:
        row = db.get(Calendar, verein)
        assert row is not None and row.deleted_at is not None


def test_time23_ec6_going_for_good_leaves_what_was_restored_in_between(client: TestClient, account: Account,
                                                                       clock: Clock) -> None:
    made = make_event(client, "Kino")
    delete_event(client, made["id"])
    assert restore(client, "event", made["id"]).status_code == 200
    # Chosen for good before the restore, deleted after it: the guard keeps it.
    with SessionLocal() as db:
        assert trash._purge_events(db, [made["id"]]) == 0
        db.commit()
    assert titles(client) == ["Kino"]


def test_time23_ec6_a_part_restored_by_four_windows_at_once_comes_back_once(client: TestClient, account: Account,
                                                                            clock: Clock) -> None:
    series = _series(client)
    _delete_part(client, series, "following", "20261123T160000Z")
    part = parts(client)[0]["id"]
    answers: list[int] = []

    def attempt() -> None:
        with new_client(account) as window_:
            answers.append(restore(window_, "part", part).status_code)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(answers) == [200, 404, 404, 404]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Event).where(Event.deleted_at.is_(None))) == 1


def test_time23_ac2_ac4_deleting_names_its_trash_entry_and_undo_brings_it_back_from_there(
        client: TestClient, account: Account, clock: Clock) -> None:
    """The web's "Undo" after deleting: the answer names the entry of the trash it became, an event or a part of a
    series, and restoring that entry brings back exactly what went."""
    single = make_event(client, "Elternabend")
    gone = client.delete(f"/api/events/{single['id']}")
    assert gone.status_code == 200, gone.text
    assert gone.json() == {"trash": {"kind": "event", "id": single["id"]}}
    assert entry(client, "event", single["id"])["title"] == "Elternabend"
    assert restore(client, "event", gone.json()["trash"]["id"]).status_code == 200
    assert "Elternabend" in titles(client) and listing(client)["items"] == []

    series = _series(client)
    before = _shown(client)
    for scope, occurrence in (("this", "20261109T160000Z"), ("following", "20261123T160000Z")):
        gone = client.delete(f"/api/events/{series}", params={"scope": scope, "occurrence": occurrence})
        assert gone.status_code == 200, gone.text
        named = gone.json()["trash"]
        assert named["kind"] == "part"
        assert named["id"] == next(item["id"] for item in parts(client) if item["part"] == ("occurrence" if scope == "this" else "following"))
        assert restore(client, "part", named["id"]).status_code == 200
        assert _shown(client) == before and parts(client) == []
