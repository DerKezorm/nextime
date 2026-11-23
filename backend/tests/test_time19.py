"""TIME-19 Teilen: members with a right, calendars from iCloud and Google whole or "busy only", guest links with a page
and a subscription, "Shares" under Settings, notices when a calendar goes. With the negative side: a member never
sees or changes the shares, links or notices of others, a guest link only reads and only its one calendar, a right
taken away holds at once (also over ``/dav/`` and the live stream), "busy only" gives nothing away."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import icalendar
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    RIGHT_BUSY,
    RIGHT_EDIT,
    RIGHT_READ,
    Account,
    Calendar,
    CalendarSetting,
    CalendarShare,
    Event,
    GuestLink,
    ShareNotice,
    Source,
    utcnow,
)
from app.security import brake
from app.services import accounts, backups, events, guest, ical, live, settings_service, shares, sources, sync

from .calendar_helpers import code, make_calendar, make_event, named, window
from .conftest import make_account, new_client
from .test_time20 import calendar_url, device_for, raw

FUTURE = datetime.now(UTC).year + 3


def vevent(uid: str, start: str, end: str, title: str, *, location: str = "", notes: str = "", free: bool = False,
           extra: str = "") -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//tests//EN", "BEGIN:VEVENT", f"UID:{uid}",
             "DTSTAMP:20261120T100000Z", f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{title}"]
    if location:
        lines.append(f"LOCATION:{location}")
    if notes:
        lines.append(f"DESCRIPTION:{notes}")
    lines.append("TRANSP:TRANSPARENT" if free else "TRANSP:OPAQUE")
    if extra:
        lines.extend(extra.split("\n"))
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def stored(calendar_id: int, text: str, by: int | None = None) -> int:
    """An event straight into a calendar, as a device would bring it (with a place, notes, an alarm)."""
    with SessionLocal() as db:
        parsed = ical.parse(text)
        uid = ical.check(parsed)
        row = Event(calendar_id=calendar_id, uid=uid, href=f"{uid}.ics", ical="", etag="", created_by=by,
                    starts_at=utcnow())
        db.add(row)
        events.write(db, row, text, parsed, by)
        events.record_change(db, calendar_id, row.href, remote=False)
        db.commit()
        return row.id


def source_calendar(owner_id: int, name: str = "Arbeit", kind: str = "icloud") -> int:
    """A calendar mirrored from iCloud (or Google, CalDAV, a subscription) of ``owner_id``."""
    with SessionLocal() as db:
        row = Source(account_id=owner_id, kind=kind, label="anna@example.com", identity=uuid.uuid4().hex)
        db.add(row)
        db.flush()
        calendar = Calendar(owner_id=owner_id, dav_name=uuid.uuid4().hex, name=name, color="blue", source_id=row.id,
                            remote_url=f"https://caldav.example.com/{name}/", read_only=kind in ("subscription",))
        db.add(calendar)
        db.flush()
        db.add(CalendarSetting(account_id=owner_id, calendar_id=calendar.id))
        db.commit()
        return calendar.id


def from_source(calendar_id: int, text: str) -> None:
    with SessionLocal() as db:
        calendar = db.get(Calendar, calendar_id)
        assert calendar is not None
        uid = ical.check(ical.parse(text))
        assert sync.store(db, calendar, f"{uid}.ics", f'"{uid}"', text)
        db.commit()


def put_share(client: TestClient, calendar_id: int, person_id: int, right: str) -> Any:
    return client.put(f"/api/calendars/{calendar_id}/sharing/{person_id}", json={"right": right})


def link_on(client: TestClient, calendar_id: int, expires_on: str | None = None) -> dict[str, Any]:
    answer = client.put(f"/api/calendars/{calendar_id}/guest-link", json={"expires_on": expires_on})
    assert answer.status_code == 200, answer.text
    return answer.json()["link"]


def token_of(link: dict[str, Any]) -> str:
    return link["url"].rsplit("/", 1)[1]


def guest_client() -> TestClient:
    """A browser without an account (no cookie, no tab header)."""
    return TestClient(app, base_url="http://testserver")


def calendar_names(client: TestClient) -> list[str]:
    return [row["name"] for row in client.get("/api/calendars").json()["calendars"]]


@pytest.fixture(autouse=True)
def _fresh_feed_cache() -> None:
    guest.forget()


# --- Members (AC-1, AC-3, AC-4, AC-8, AC-15, AC-16, EC-5, EC-8, EC-9, EC-14, EC-15) -------------------------------


async def test_time19_ac1_the_owner_finds_members_and_each_right_shows_at_once_with_the_owners_name(
        client: TestClient, account: Account) -> None:
    anna, ben = make_account("anna"), make_account("ben")
    familie = make_calendar(client, "Familie")
    found = client.get("/api/shares/people", params={"q": "an"}).json()["people"]
    assert [person["name"] for person in found] == ["Anna"]
    assert found[0]["email"] == "anna@example.com"
    listening = live.subscribe(anna.id)
    try:
        answer = await asyncio.to_thread(put_share, client, familie, anna.id, RIGHT_EDIT)
        assert answer.status_code == 200
        assert await asyncio.wait_for(listening[1].get(), 2) == "changed"
    finally:
        live.unsubscribe(anna.id, listening)
    assert put_share(client, familie, ben.id, RIGHT_READ).status_code == 200
    with new_client(anna) as browser:
        shown = named(browser, "Familie")
        assert (shown["owner"], shown["right"], shown["writable"], shown["group"]) == ("Tester", "edit", True, "shared")
    with new_client(ben) as browser:
        shown = named(browser, "Familie")
        assert (shown["owner"], shown["right"], shown["writable"]) == ("Tester", "read", False)


def test_time19_ac9_the_dialog_lists_each_person_with_picture_name_address_and_right(client: TestClient,
                                                                                    account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    view = put_share(client, familie, anna.id, RIGHT_EDIT).json()
    assert view["calendar"] == {"id": familie, "name": "Familie", "color": "slate", "icon": "", "kind": "own"}
    assert view["rights"] == ["read", "edit"]
    person = view["people"][0]
    assert {key: person[key] for key in ("id", "name", "email", "right")} == {
        "id": anna.id, "name": "Anna", "email": "anna@example.com", "right": "edit"}
    assert "avatar" in person and "color" in person and person["since"]
    assert view["links_allowed"] is True and view["link"] is None


def test_time19_ec8_the_search_ignores_case_and_tells_namesakes_apart_by_their_address(client: TestClient,
                                                                                     account: Account) -> None:
    first = make_account("anna1", email="anna@familie.example.com")
    second = make_account("anna2", email="anna@verein.example.com")
    with SessionLocal() as db:
        for row in (db.get(Account, first.id), db.get(Account, second.id)):
            assert row is not None
            row.display_name = "Anna Berg"
        db.commit()
    blocked = make_account("annab")
    with SessionLocal() as db:
        row = db.get(Account, blocked.id)
        assert row is not None
        row.blocked_at = utcnow()
        db.commit()
    for query in ("ANNA BERG", "anna berg", "Anna"):
        found = client.get("/api/shares/people", params={"q": query}).json()["people"]
        assert sorted(person["email"] for person in found) == ["anna@familie.example.com", "anna@verein.example.com"]
    assert client.get("/api/shares/people", params={"q": "VEREIN"}).json()["people"][0]["id"] == second.id
    # Never the account itself.
    assert client.get("/api/shares/people", params={"q": "tester"}).json()["people"] == []


def test_time19_ec5_a_search_without_hits_finds_nobody_and_the_guest_link_still_works(client: TestClient,
                                                                                     account: Account) -> None:
    familie = make_calendar(client, "Familie")
    assert client.get("/api/shares/people", params={"q": "zzz"}).json()["people"] == []
    link = link_on(client, familie)
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token_of(link)}").status_code == 200


def test_time19_ec9_oneself_never_and_the_same_person_twice_only_changes_the_right(client: TestClient,
                                                                                account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    refused = put_share(client, familie, account.id, RIGHT_READ)
    assert refused.status_code == 422 and code(refused) == "share_self"
    put_share(client, familie, anna.id, RIGHT_READ)
    put_share(client, familie, anna.id, RIGHT_READ)
    view = put_share(client, familie, anna.id, RIGHT_EDIT).json()
    assert [(person["id"], person["right"]) for person in view["people"]] == [(anna.id, "edit")]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CalendarShare)) == 1
    unknown = put_share(client, familie, 999_999, RIGHT_READ)
    assert unknown.status_code == 404 and code(unknown) == "person_not_found"
    wrong = put_share(client, familie, anna.id, RIGHT_BUSY)
    assert wrong.status_code == 422 and code(wrong) == "invalid_right"


def test_time19_ec15_a_double_click_and_a_session_ended_in_the_dialog_leave_no_double_and_no_half_share(
        client: TestClient, account: Account) -> None:
    anna, ben = make_account("anna"), make_account("ben")
    familie = make_calendar(client, "Familie")
    for _ in range(2):
        assert put_share(client, familie, anna.id, RIGHT_EDIT).status_code == 200
        assert client.put(f"/api/calendars/{familie}/guest-link", json={"expires_on": None}).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CalendarShare)) == 1
        assert db.scalar(select(func.count()).select_from(GuestLink)) == 1
    cookies = dict(client.cookies)
    client.cookies.clear()
    ended = put_share(client, familie, ben.id, RIGHT_READ)
    assert ended.status_code == 401
    for name, value in cookies.items():
        client.cookies.set(name, value)
    view = client.get(f"/api/calendars/{familie}/sharing").json()
    assert [person["id"] for person in view["people"]] == [anna.id]


def test_time19_ac8_ac15_a_member_with_edit_rights_changes_events_but_never_shares_renames_or_deletes(
        client: TestClient, account: Account) -> None:
    anna, ben = make_account("anna"), make_account("ben")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    with new_client(anna) as browser:
        made = make_event(browser, "Elternabend", calendar_id=familie)
        assert browser.patch(f"/api/events/{made['id']}", json={"title": "Elternabend 2"}).status_code == 200
        assert browser.delete(f"/api/events/{made['id']}").status_code == 200
        for answer in (browser.get(f"/api/calendars/{familie}/sharing"),
                       put_share(browser, familie, ben.id, RIGHT_READ),
                       browser.delete(f"/api/calendars/{familie}/sharing/{anna.id}"),
                       browser.put(f"/api/calendars/{familie}/guest-link", json={}),
                       browser.delete(f"/api/calendars/{familie}/guest-link"),
                       browser.patch(f"/api/calendars/{familie}", json={"name": "Meins"}),
                       browser.delete(f"/api/calendars/{familie}")):
            assert answer.status_code == 403, answer.text
            assert code(answer) == "calendar_not_own"
    with SessionLocal() as db:
        assert db.get(Calendar, familie).name == "Familie"  # type: ignore[union-attr]
        assert db.get(CalendarShare, (familie, ben.id)) is None


def test_time19_ac16_a_member_sees_the_owner_only_never_the_others_it_is_shared_with(client: TestClient,
                                                                                   account: Account) -> None:
    anna, ben = make_account("anna"), make_account("ben")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    put_share(client, familie, ben.id, RIGHT_READ)
    with new_client(anna) as browser:
        mine = browser.get("/api/shares").json()
        assert mine["with_me"] == [{"calendar": {"id": familie, "name": "Familie", "color": "slate", "kind": "own"},
                                    "owner": "Tester", "right": "edit"}]
        assert "Ben" not in str(mine) and "ben@example.com" not in str(mine)
        assert "ben@example.com" not in browser.get("/api/calendars").text
        assert browser.get(f"/api/calendars/{familie}/sharing").status_code == 403


def test_time19_a_member_neither_sees_nor_changes_the_shares_links_or_notices_of_others(client: TestClient,
                                                                                      account: Account) -> None:
    anna, ben = make_account("anna"), make_account("ben")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_READ)
    link_on(client, familie)
    put_share(client, familie, anna.id, RIGHT_READ)
    client.delete(f"/api/calendars/{familie}/sharing/{anna.id}")
    with SessionLocal() as db:
        notice = db.scalar(select(ShareNotice.id).where(ShareNotice.account_id == anna.id))
    assert notice is not None
    with new_client(ben) as browser:
        for answer in (browser.get(f"/api/calendars/{familie}/sharing"),
                       put_share(browser, familie, ben.id, RIGHT_READ),
                       browser.delete(f"/api/calendars/{familie}/sharing/{anna.id}"),
                       browser.put(f"/api/calendars/{familie}/guest-link", json={}),
                       browser.delete(f"/api/calendars/{familie}/guest-link"),
                       browser.post(f"/api/calendars/{familie}/guest-link/renew"),
                       browser.post(f"/api/calendars/{familie}/leave"),
                       browser.delete(f"/api/shares/notices/{notice}")):
            assert answer.status_code == 404, answer.text
        overview = browser.get("/api/shares").json()
        assert overview == {"mine": [], "with_me": [], "notices": []}
    with new_client(anna) as browser:
        assert browser.get("/api/shares").json()["notices"][0]["id"] == notice
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(GuestLink)) == 1


async def test_time19_ac3_taking_back_or_lowering_a_right_holds_at_once_in_nextime_on_devices_and_in_the_stream(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Elternabend", calendar_id=familie)
    put_share(client, familie, anna.id, RIGHT_EDIT)
    password = device_for(anna)
    path = calendar_url(anna, familie)
    assert raw("PROPFIND", path, anna, password, Depth="0").status_code == 207
    assert raw("GET", f"{path}{made['id']}.ics", anna, password).status_code in (200, 404)
    put_share(client, familie, anna.id, RIGHT_READ)
    with new_client(anna) as browser:
        refused = browser.patch(f"/api/events/{made['id']}", json={"title": "Meins"})
        assert refused.status_code == 403 and code(refused) == "calendar_read_only"
    body = vevent("vom-iphone", "20301203T170000Z", "20301203T180000Z", "Vom iPhone")
    assert raw("PUT", f"{path}vom-iphone.ics", anna, password, body).status_code == 403
    listening = live.subscribe(anna.id)
    try:
        answer = await asyncio.to_thread(client.delete, f"/api/calendars/{familie}/sharing/{anna.id}")
        assert answer.status_code == 200
        assert await asyncio.wait_for(listening[1].get(), 2) == "changed"
    finally:
        live.unsubscribe(anna.id, listening)
    assert raw("PROPFIND", path, anna, password, Depth="0").status_code == 404
    with new_client(anna) as browser:
        assert "Familie" not in calendar_names(browser)
        assert browser.get(f"/api/events/{made['id']}").status_code == 404
        assert all(row["calendar_id"] != familie for row in window(browser, "2026-11-01T00:00:00Z",
                                                                   "2027-01-01T00:00:00Z"))


def test_time19_ec14_saving_after_the_right_was_lowered_or_taken_is_refused_with_a_reason_and_changes_nothing(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Elternabend", calendar_id=familie)
    put_share(client, familie, anna.id, RIGHT_EDIT)
    with new_client(anna) as browser:
        opened = browser.get(f"/api/events/{made['id']}").json()
        put_share(client, familie, anna.id, RIGHT_READ)
        refused = browser.patch(f"/api/events/{made['id']}", json={"title": "Geändert", "etag": opened["etag"]})
        assert refused.status_code == 403
        assert refused.json()["detail"]["owner"] == "Tester"
        put_share(client, familie, anna.id, RIGHT_EDIT)
        client.delete(f"/api/calendars/{familie}/sharing/{anna.id}")
        gone = browser.patch(f"/api/events/{made['id']}", json={"title": "Geändert"})
        assert gone.status_code == 404
        assert browser.delete(f"/api/events/{made['id']}").status_code == 404
    assert client.get(f"/api/events/{made['id']}").json()["title"] == "Elternabend"


def test_time19_ac4_leaving_takes_the_calendar_from_the_member_only(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    make_event(client, "Elternabend", calendar_id=familie)
    put_share(client, familie, anna.id, RIGHT_EDIT)
    with new_client(anna) as browser:
        own = named(browser, "Personal")["id"]
        assert browser.post(f"/api/calendars/{familie}/leave").status_code == 204
        assert "Familie" not in calendar_names(browser)
        assert browser.get("/api/shares").json() == {"mine": [], "with_me": [], "notices": []}
        assert browser.post(f"/api/calendars/{familie}/leave").status_code == 404
        # Its own calendar is nothing to leave.
        assert browser.post(f"/api/calendars/{own}/leave").status_code == 404
    assert [title["title"] for title in window(client)] == ["Elternabend"]
    assert client.get(f"/api/calendars/{familie}/sharing").json()["people"] == []


# --- "Shares" (AC-6, AC-14, EC-6) -----------------------------------------------------------------------------------


def test_time19_ec6_without_shares_the_page_is_empty(client: TestClient, account: Account) -> None:
    assert client.get("/api/shares").json() == {"mine": [], "with_me": [], "notices": []}


def test_time19_ac6_shares_lists_people_and_link_with_since_and_until_and_what_is_shared_with_me(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    link_on(client, familie, f"{FUTURE}-12-31")
    with new_client(anna) as browser:
        annas = make_calendar(browser, "Garten")
        put_share(browser, annas, account.id, RIGHT_READ)
    mine = client.get("/api/shares").json()
    assert [(row["kind"], row["calendar"]["name"]) for row in mine["mine"]] == [("person", "Familie"),
                                                                                  ("link", "Familie")]
    person, link = mine["mine"]
    assert person["person"]["name"] == "Anna" and person["right"] == "edit" and person["since"]
    assert link["until"] == f"{FUTURE}-12-31" and link["since"] and link["active"] is True
    assert mine["with_me"] == [{"calendar": {"id": annas, "name": "Garten", "color": "slate", "kind": "own"},
                                "owner": "Anna", "right": "read"}]


def test_time19_ac14_when_the_owner_ends_the_share_the_other_one_finds_a_notice_to_hide(client: TestClient,
                                                                                      account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie", color="green")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    client.delete(f"/api/calendars/{familie}/sharing/{anna.id}")
    with new_client(anna) as browser:
        notices = browser.get("/api/shares").json()["notices"]
        assert [(row["calendar"], row["owner"], row["color"]) for row in notices] == [("Familie", "Tester", "green")]
        assert browser.delete(f"/api/shares/notices/{notices[0]['id']}").status_code == 204
        assert browser.get("/api/shares").json()["notices"] == []
    # Shared again: an old notice would no longer be true.
    put_share(client, familie, anna.id, RIGHT_EDIT)
    client.delete(f"/api/calendars/{familie}/sharing/{anna.id}")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    with new_client(anna) as browser:
        assert browser.get("/api/shares").json()["notices"] == []


def test_time19_ec18_a_deleted_calendar_goes_with_a_notice_and_comes_back_with_its_shares_and_link(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    token = token_of(link_on(client, familie))
    assert client.delete(f"/api/calendars/{familie}").status_code == 200
    with new_client(anna) as browser:
        assert "Familie" not in calendar_names(browser)
        assert [row["calendar"] for row in browser.get("/api/shares").json()["notices"]] == ["Familie"]
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token}").status_code == 404
        assert browser.get(f"/abo/{token}.ics").status_code == 404
    # Restored from the trash (TIME-23 brings the button): shares and link hold again, the notice goes.
    with SessionLocal() as db:
        row = db.get(Calendar, familie)
        assert row is not None
        row.deleted_at = None
        shares.calendar_restored(db, familie)
        db.commit()
    with new_client(anna) as browser:
        assert "Familie" in calendar_names(browser)
        assert browser.get("/api/shares").json()["notices"] == []
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token}").status_code == 200


def test_time19_ec17_a_blocked_owner_stays_a_deleted_one_goes_with_a_notice_a_deleted_recipient_leaves_the_list(
        client: TestClient, account: Account) -> None:
    anna, ben = make_account("anna"), make_account("ben")
    with new_client(anna) as browser:
        garten = make_calendar(browser, "Garten")
        make_event(browser, "Rasen", calendar_id=garten)
        put_share(browser, garten, account.id, RIGHT_EDIT)
        put_share(browser, garten, ben.id, RIGHT_READ)
        token = token_of(link_on(browser, garten))
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        accounts.block(db, row)
    assert "Garten" in calendar_names(client)
    assert [row["title"] for row in window(client)] == ["Rasen"]
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token}").status_code == 200
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        accounts.unblock(db, row)
        accounts.delete_account(db, row)
    assert "Garten" not in calendar_names(client)
    assert [row["calendar"] for row in client.get("/api/shares").json()["notices"]] == ["Garten"]
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token}").status_code == 404
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        accounts.restore_account(db, row)
    assert "Garten" in calendar_names(client)
    assert client.get("/api/shares").json()["notices"] == []
    # A recipient deleted by the operator is gone from "What you share".
    with SessionLocal() as db:
        row = db.get(Account, ben.id)
        assert row is not None
        accounts.delete_account(db, row)
    with new_client(anna) as browser:
        people = browser.get(f"/api/calendars/{garten}/sharing").json()["people"]
        assert [person["name"] for person in people] == ["Tester"]
        assert all(row.get("person", {}).get("name") != "Ben" for row in browser.get("/api/shares").json()["mine"])


def test_time19_ec2_an_event_deleted_by_a_member_lands_in_the_owners_trash_with_the_name_of_who_deleted_it(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Elternabend", calendar_id=familie)
    put_share(client, familie, anna.id, RIGHT_EDIT)
    with new_client(anna) as browser:
        assert browser.delete(f"/api/events/{made['id']}").status_code == 200
    with SessionLocal() as db:
        row = db.get(Event, made["id"])
        assert row is not None and row.deleted_at is not None
        assert row.deleted_by == anna.id and row.calendar_id == familie
    gone = client.get(f"/api/events/{made['id']}")
    assert gone.status_code == 409 and gone.json()["detail"]["by"] == "Anna"


# --- Calendars from iCloud and Google (AC-2, AC-11, AC-12, EC-3, EC-13) ----------------------------------------------


def test_time19_ac2_ec13_a_calendar_from_icloud_is_shared_whole_or_busy_and_stays_read_only_for_the_other(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    arbeit = source_calendar(account.id)
    from_source(arbeit, vevent("team", "20261126T083000Z", "20261126T093000Z", "Teamrunde", location="Raum Nord",
                               notes="Geheime Notiz"))
    from_source(arbeit, vevent("frei", "20261127T083000Z", "20261127T093000Z", "Homeoffice", free=True))
    view = client.get(f"/api/calendars/{arbeit}/sharing").json()
    assert view["rights"] == ["read", "busy"] and view["links_allowed"] is False and view["link"] is None
    wrong = put_share(client, arbeit, anna.id, RIGHT_EDIT)
    assert wrong.status_code == 422 and code(wrong) == "invalid_right"
    put_share(client, arbeit, anna.id, RIGHT_READ)
    with new_client(anna) as browser:
        shown = named(browser, "Arbeit")
        assert shown["writable"] is False and shown["owner"] == "Tester"
        rows = window(browser)
        assert sorted(row["title"] for row in rows) == ["Homeoffice", "Teamrunde"]
        team = next(row for row in rows if row["title"] == "Teamrunde")
        refused = browser.patch(f"/api/events/{team['id']}", json={"title": "Meins"})
        assert refused.status_code == 403
        assert browser.post("/api/events", json={"title": "Neu", "start": "2026-11-28T10:00", "end": "2026-11-28T11:00",
                                                 "time_zone": "Europe/Berlin", "calendar_id": arbeit}).status_code == 403
    put_share(client, arbeit, anna.id, RIGHT_BUSY)
    with new_client(anna) as browser:
        rows = window(browser)
        assert [(row["title"], row["location"], row["busy_only"]) for row in rows] == [("", "", True)]
        assert browser.get(f"/api/events/{rows[0]['id']}").json()["notes"] == ""
        free = next(row["id"] for row in window(client) if row["title"] == "Homeoffice")
        assert browser.get(f"/api/events/{free}").status_code == 404


async def test_time19_ac11_busy_only_gives_nothing_away_over_the_calendar_account_or_the_live_stream(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    arbeit = source_calendar(account.id)
    from_source(arbeit, vevent("team", "20261126T083000Z", "20261126T093000Z", "Teamrunde", location="Raum Nord",
                               notes="Geheime Notiz"))
    put_share(client, arbeit, anna.id, RIGHT_BUSY)
    password = device_for(anna)
    path = calendar_url(anna, arbeit)
    listing = raw("PROPFIND", path, anna, password, Depth="1")
    assert listing.status_code == 207
    got = raw("GET", f"{path}team.ics", anna, password)
    assert got.status_code == 200
    text = got.content.decode()
    for secret in ("Teamrunde", "Raum Nord", "Geheime Notiz"):
        assert secret not in text and secret not in listing.content.decode()
    listening = live.subscribe(anna.id)
    try:
        await asyncio.to_thread(from_source, arbeit,
                                vevent("team", "20261126T090000Z", "20261126T100000Z", "Teamrunde neu"))
        assert await asyncio.wait_for(listening[1].get(), 2) == "changed"
    finally:
        live.unsubscribe(anna.id, listening)


def test_time19_ac12_a_calendar_from_icloud_or_google_has_no_guest_link(client: TestClient,
                                                                        account: Account) -> None:
    for kind in ("icloud", "google", "caldav"):
        calendar = source_calendar(account.id, f"Quelle {kind}", kind)
        refused = client.put(f"/api/calendars/{calendar}/guest-link", json={})
        assert refused.status_code == 409 and code(refused) == "link_own_only"
    abo = source_calendar(account.id, "Abo", "subscription")
    refused = client.get(f"/api/calendars/{abo}/sharing")
    assert refused.status_code == 409 and code(refused) == "calendar_not_shareable"


def test_time19_ec3_disconnecting_the_icloud_account_takes_its_shared_calendar_from_everybody_with_a_notice(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    arbeit = source_calendar(account.id)
    from_source(arbeit, vevent("team", "20261126T083000Z", "20261126T093000Z", "Teamrunde"))
    put_share(client, arbeit, anna.id, RIGHT_READ)
    with SessionLocal() as db:
        source_id = db.get(Calendar, arbeit).source_id  # type: ignore[union-attr]
        row = db.get(Account, account.id)
        assert row is not None and source_id is not None
        sources.disconnect(db, row, source_id, drop_waiting=True)
    with new_client(anna) as browser:
        assert "Arbeit" not in calendar_names(browser)
        assert window(browser) == []
        assert [row["calendar"] for row in browser.get("/api/shares").json()["notices"]] == ["Arbeit"]


def test_time19_ac10_a_shared_icloud_calendar_comes_to_the_others_phone_but_not_twice_to_the_owners(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    arbeit = source_calendar(account.id)
    put_share(client, arbeit, anna.id, RIGHT_READ)
    with new_client(anna) as browser:
        assert "Arbeit" in [row["name"] for row in browser.get("/api/devices").json()["calendars"]]
    assert "Arbeit" not in [row["name"] for row in client.get("/api/devices").json()["calendars"]]
    # The calendar account itself: on Anna's phone it is there, on the owner's not twice.
    home = '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>'
    annas = raw("PROPFIND", f"/dav/calendars/{anna.name}/", anna, device_for(anna), home, Depth="1")
    assert b"Arbeit" in annas.content
    mine = raw("PROPFIND", f"/dav/calendars/{account.name}/", account, device_for(account), home, Depth="1")
    assert mine.status_code == 207 and b"Arbeit" not in mine.content


# --- Guest links (AC-5, AC-7, AC-13, AC-17, EC-1, EC-4, EC-7, EC-10 to EC-12, EC-19 to EC-22) -------------------------


def test_time19_ac5_the_guest_link_comes_with_an_address_to_copy_holds_until_revoked_and_ends(
        client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    link = link_on(client, familie)
    assert link["url"].startswith("http://testserver/abo/") and link["feed"] == link["url"] + ".ics"
    assert link["expires_on"] is None
    assert len(token_of(link)) >= 32
    # The same address again later, to copy once more.
    assert client.get(f"/api/calendars/{familie}/sharing").json()["link"]["url"] == link["url"]
    changed = link_on(client, familie, f"{FUTURE}-06-30")
    assert changed["url"] == link["url"] and changed["expires_on"] == f"{FUTURE}-06-30"
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token_of(link)}").status_code == 200
        assert browser.get(f"/abo/{token_of(link)}.ics").status_code == 200
        assert client.delete(f"/api/calendars/{familie}/guest-link").json()["link"] is None
        assert browser.get(f"/api/guest/{token_of(link)}").status_code == 404


def test_time19_ec20_switched_on_again_after_revoking_the_link_has_a_new_address(client: TestClient,
                                                                               account: Account) -> None:
    familie = make_calendar(client, "Familie")
    first = token_of(link_on(client, familie))
    client.delete(f"/api/calendars/{familie}/guest-link")
    second = token_of(link_on(client, familie))
    assert first != second
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{first}").status_code == 404
        assert browser.get(f"/api/guest/{second}").status_code == 200


def test_time19_ec19_no_day_in_the_past_and_the_end_counts_by_the_servers_clock(client: TestClient,
                                                                             account: Account) -> None:
    familie = make_calendar(client, "Familie")
    refused = client.put(f"/api/calendars/{familie}/guest-link", json={"expires_on": "2020-01-01"})
    assert refused.status_code == 422 and code(refused) == "date_past"
    nonsense = client.put(f"/api/calendars/{familie}/guest-link", json={"expires_on": "2026-02-30"})
    assert nonsense.status_code == 422
    token = token_of(link_on(client, familie, f"{FUTURE}-01-01"))
    with SessionLocal() as db:
        row = db.scalar(select(GuestLink))
        assert row is not None and row.expires_at is not None
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token}").status_code == 404
    assert client.get("/api/shares").json()["mine"][0]["active"] is False


def test_time19_ec1_a_revoked_or_run_out_link_gives_no_events_and_no_name(client: TestClient,
                                                                         account: Account) -> None:
    familie = make_calendar(client, "Familienkalender")
    make_event(client, "Elternabend", f"{FUTURE}-03-03T18:00", f"{FUTURE}-03-03T19:00", calendar_id=familie)
    token = token_of(link_on(client, familie))
    client.delete(f"/api/calendars/{familie}/guest-link")
    with guest_client() as browser:
        page = browser.get(f"/api/guest/{token}")
        assert page.status_code == 404 and code(page) == "link_invalid"
        feed = browser.get(f"/abo/{token}.ics")
        assert feed.status_code == 404 and feed.content == b""
        for answer in (page, feed):
            assert "Familienkalender" not in answer.text and "Elternabend" not in answer.text


def test_time19_ac7_the_operators_switch_hides_the_switch_and_stops_existing_links(client: TestClient,
                                                                                account: Account) -> None:
    familie = make_calendar(client, "Familie")
    token = token_of(link_on(client, familie))
    with SessionLocal() as db:
        settings_service.save(db, {"share_links": False})
    view = client.get(f"/api/calendars/{familie}/sharing").json()
    assert view["links_allowed"] is False and view["link"] is None
    refused = client.put(f"/api/calendars/{familie}/guest-link", json={})
    assert refused.status_code == 403 and code(refused) == "links_off"
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token}").status_code == 404
        assert browser.get(f"/abo/{token}.ics").status_code == 404
    with SessionLocal() as db:
        settings_service.save(db, {"share_links": True})
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token}").status_code == 200


def test_time19_ac13_ac17_the_guest_page_shows_name_color_owner_and_coming_events_with_title_time_and_place_only(
        client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie", color="green")
    stored(familie, vevent("abend", f"{FUTURE}0303T170000Z", f"{FUTURE}0303T180000Z", "Elternabend",
                           location="Schule, Raum 4", notes="Geheime Notiz",
                           extra="ATTENDEE:mailto:anna@example.com\nATTACH:https://files.example.com/x.pdf\n"
                                 "X-PRIVATE:intern"), account.id)
    stored(familie, vevent("ferien", f"{FUTURE}0310", f"{FUTURE}0312", "Ferien").replace(
        f"DTSTART:{FUTURE}0310", f"DTSTART;VALUE=DATE:{FUTURE}0310").replace(
        f"DTEND:{FUTURE}0312", f"DTEND;VALUE=DATE:{FUTURE}0312"), account.id)
    stored(familie, vevent("vorbei", "20200101T100000Z", "20200101T110000Z", "Vorbei"), account.id)
    just = datetime.now(UTC)
    stored(familie, vevent("eben", (just - timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ"),
                           (just - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ"), "Eben vorbei"), account.id)
    token = token_of(link_on(client, familie))
    with guest_client() as browser:
        answer = browser.get(f"/api/guest/{token}")
    assert answer.status_code == 200
    page = answer.json()
    assert page["calendar"] == {"name": "Familie", "color": "green"} and page["owner"] == "Tester"
    assert page["events"] == [
        {"all_day": False, "start": f"{FUTURE}-03-03T17:00:00Z", "end": f"{FUTURE}-03-03T18:00:00Z",
         "title": "Elternabend", "location": "Schule, Raum 4"},
        {"all_day": True, "start": f"{FUTURE}-03-10", "end": f"{FUTURE}-03-11", "title": "Ferien", "location": ""}]
    assert page["more"] is False and page["feed"].endswith(f"/abo/{token}.ics")
    for secret in ("Geheime Notiz", "anna@example.com", "x.pdf", "intern", "Vorbei", "Eben vorbei"):
        assert secret not in answer.text


def test_time19_ac17_a_long_calendar_shows_more_on_request_and_the_subscription_holds_every_event(
        client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    for number in range(300):
        day = datetime(FUTURE, 1, 1, 9, tzinfo=UTC) + timedelta(days=number)
        stamp = day.strftime("%Y%m%dT%H%M%SZ")
        stored(familie, vevent(f"e{number}", stamp, (day + timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ"),
                               f"Termin {number}", notes="Notiz", extra="BEGIN:VALARM\nACTION:DISPLAY\n"
                               "TRIGGER:-PT15M\nDESCRIPTION:Alarm\nEND:VALARM"))
    stored(familie, vevent("alt", "20200101T100000Z", "20200101T110000Z", "Alt"))
    stored(familie, vevent("serie", f"{FUTURE - 1}0105T070000Z", f"{FUTURE - 1}0105T080000Z", "Laufen",
                           extra="RRULE:FREQ=WEEKLY;COUNT=3"))
    token = token_of(link_on(client, familie))
    with guest_client() as browser:
        first = browser.get(f"/api/guest/{token}").json()
        assert len(first["events"]) == 50 and first["more"] is True and first["next"] == 50
        last = browser.get(f"/api/guest/{token}", params={"offset": 250}).json()
        assert len(last["events"]) == 50 and last["more"] is False
        assert last["events"][-1]["title"] == "Termin 299"
        feed = browser.get(f"/abo/{token}.ics")
    assert feed.status_code == 200 and feed.headers["content-type"].startswith("text/calendar")
    parsed = icalendar.Calendar.from_ical(feed.content)
    found = [component for component in parsed.walk("VEVENT")]
    assert len(found) == 302
    assert {"Alt", "Laufen", "Termin 0"} <= {str(component["SUMMARY"]) for component in found}
    assert not list(parsed.walk("VALARM"))
    assert b"DESCRIPTION" not in feed.content and b"Notiz" not in feed.content
    assert str(parsed["X-WR-CALNAME"]) == "Familie"
    again = TestClient(app).get(f"/abo/{token}.ics", headers={"If-None-Match": feed.headers["etag"]})
    assert again.status_code == 304


def test_time19_ec7_a_calendar_without_coming_events_says_so_and_can_still_be_subscribed(client: TestClient,
                                                                                       account: Account) -> None:
    familie = make_calendar(client, "Familie")
    stored(familie, vevent("alt", "20200101T100000Z", "20200101T110000Z", "Alt"))
    token = token_of(link_on(client, familie))
    with guest_client() as browser:
        page = browser.get(f"/api/guest/{token}").json()
        assert page["events"] == [] and page["more"] is False
        feed = browser.get(f"/abo/{token}.ics")
        assert feed.status_code == 200 and b"SUMMARY:Alt" in feed.content


def test_time19_ec10_code_and_line_breaks_stay_text_on_the_page_and_in_the_subscription(client: TestClient,
                                                                                       account: Account) -> None:
    familie = make_calendar(client, "<img src=x onerror=alert(1)>")
    made = make_event(client, "Zeile 1\nZeile 2; mit, END:VEVENT <script>x</script> 🎉",
                      f"{FUTURE}-03-03T18:00", f"{FUTURE}-03-03T19:00", calendar_id=familie)
    title = client.get(f"/api/events/{made['id']}").json()["title"]
    token = token_of(link_on(client, familie))
    with guest_client() as browser:
        page = browser.get(f"/api/guest/{token}").json()
        feed = browser.get(f"/abo/{token}.ics").content
    assert page["calendar"]["name"] == "<img src=x onerror=alert(1)>"
    assert page["events"][0]["title"] == title
    parsed = icalendar.Calendar.from_ical(feed)
    found = list(parsed.walk("VEVENT"))
    assert len(found) == 1 and str(found[0]["SUMMARY"]) == title
    assert str(parsed["X-WR-CALNAME"]) == "<img src=x onerror=alert(1)>"
    assert found[0].decoded("DTSTART") == datetime(FUTURE, 3, 3, 17, 0, tzinfo=UTC).astimezone(
        found[0].decoded("DTSTART").tzinfo)


def test_time19_ec11_a_messenger_preview_gets_no_events(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    make_event(client, "Elternabend", f"{FUTURE}-03-03T18:00", f"{FUTURE}-03-03T19:00", calendar_id=familie)
    token = token_of(link_on(client, familie))
    for agent in ("WhatsApp/2.23.20.0", "TelegramBot (like TwitterBot)", "Mozilla/5.0 (compatible; Signal)"):
        with TestClient(app, headers={"User-Agent": agent}) as messenger:
            answer = messenger.get(f"/abo/{token}")
        assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/html")
        assert "Elternabend" not in answer.text and "Familie" not in answer.text
        assert answer.headers["x-robots-tag"] == "noindex, nofollow"


def test_time19_ec12_a_guest_sees_this_one_calendar_and_only_the_owners_name(client: TestClient,
                                                                          account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    make_calendar(client, "Privat")
    put_share(client, familie, anna.id, RIGHT_READ)
    token = token_of(link_on(client, familie))
    with guest_client() as browser:
        page = browser.get(f"/api/guest/{token}")
        assert set(page.json()) == {"calendar", "owner", "events", "more", "next", "feed"}
        for hidden in ("Privat", "Anna", "anna@example.com", "tester@example.com", "Personal"):
            assert hidden not in page.text
        # The link is no key to anything else: not the app, not the calendar account, not another calendar.
        assert browser.get("/api/calendars").status_code == 401
        assert browser.get("/api/events", params={"start": "2026-11-01T00:00:00Z",
                                                  "end": "2026-12-01T00:00:00Z"}).status_code == 401
        assert browser.get("/api/calendars", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        assert browser.request("PROPFIND", "/dav/", auth=("tester", token)).status_code == 401
        assert browser.put(f"/api/guest/{token}", json={}).status_code in (400, 405)


def test_time19_ec4_a_thousand_wrong_addresses_are_slowed_down_and_links_are_not_to_be_guessed(
        client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    token = token_of(link_on(client, familie))
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", token)
    # The brake's clock stands still: however long the 1,000 requests take, the wait has not run out.
    brake.clock = lambda: 1000.0
    statuses = []
    with guest_client() as browser:
        for number in range(1000):
            statuses.append(browser.get(f"/api/guest/wrong{number:04d}").status_code)
        assert statuses.count(404) == 20 and statuses.count(429) == 980
        # A calendar program from the same sender waits too, told so (429 with Retry-After), never "gone" (404).
        feed = browser.get(f"/abo/{token}.ics")
        assert feed.status_code == 429 and int(feed.headers["retry-after"]) > 0
        page = browser.get(f"/api/guest/{token}")
        assert page.status_code == 429 and int(page.headers["retry-after"]) > 0


def test_time19_ec21_after_a_restored_backup_the_link_holds_with_the_same_address(client: TestClient,
                                                                                account: Account,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    link = link_on(client, familie)
    name = backups.create().name
    client.delete(f"/api/calendars/{familie}/guest-link")
    monkeypatch.setattr(backups, "restart_soon", lambda: None)
    backups.stage_restore(name)
    assert backups.apply_pending() is True
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token_of(link)}").status_code == 200
        assert browser.get(f"/abo/{token_of(link)}.ics").status_code == 200


def test_time19_ec22_behind_a_proxy_the_link_carries_the_outside_address(client: TestClient,
                                                                       account: Account) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://kalender.example.com"})
    familie = make_calendar(client, "Familie")
    link = link_on(client, familie)
    assert link["url"].startswith("https://kalender.example.com/abo/")
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{token_of(link)}").json()["feed"].startswith(
            "https://kalender.example.com/abo/")


def test_time19_a_link_with_an_unreadable_stored_address_still_works_and_gets_a_new_one(client: TestClient,
                                                                                       account: Account) -> None:
    familie = make_calendar(client, "Familie")
    old = token_of(link_on(client, familie))
    with SessionLocal() as db:
        row = db.scalar(select(GuestLink))
        assert row is not None
        row.token_enc = "00" * 40  # sealed with another key: unreadable
        db.commit()
    view = client.get(f"/api/calendars/{familie}/sharing").json()
    assert view["link"]["url"] is None
    with guest_client() as browser:
        assert browser.get(f"/api/guest/{old}").status_code == 200
    renewed = client.post(f"/api/calendars/{familie}/guest-link/renew").json()["link"]
    assert renewed["url"] and token_of(renewed) != old


@pytest.mark.timing
def test_time19_ec24_twenty_thousand_events_and_thirty_people_stay_smooth(client: TestClient,
                                                                          account: Account) -> None:
    familie = make_calendar(client, "Familie " + "x" * 52)
    people = [make_account(f"person{number:02d}") for number in range(30)]
    with SessionLocal() as db:
        for person in people:
            row = db.get(Account, person.id)
            assert row is not None
            row.display_name = "P" * 100
        db.commit()
    for person in people:
        put_share(client, familie, person.id, RIGHT_READ)
    with SessionLocal() as db:
        base = datetime(FUTURE, 1, 1, 8, tzinfo=UTC)
        for number in range(20_000):
            start = base + timedelta(hours=number)
            text = vevent(f"bulk{number}", start.strftime("%Y%m%dT%H%M%SZ"),
                          (start + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ"), f"Termin {number}")
            parsed = ical.parse(text)
            row = Event(calendar_id=familie, uid=f"bulk{number}", href=f"bulk{number}.ics", ical="", etag="",
                        starts_at=start)
            db.add(row)
            events.write(db, row, text, parsed, account.id)
        db.commit()
    token = token_of(link_on(client, familie))
    timings = {}
    # Sharing a calendar that is already full, changing the right and taking it back (review of TIME-19, finding 3).
    late = make_account("spaet")
    for name, right in (("share", RIGHT_READ), ("change", RIGHT_EDIT)):
        began = time.perf_counter()
        assert put_share(client, familie, late.id, right).status_code == 200
        timings[name] = time.perf_counter() - began
    began = time.perf_counter()
    assert client.delete(f"/api/calendars/{familie}/sharing/{late.id}").status_code == 200
    timings["take back"] = time.perf_counter() - began
    with guest_client() as browser:
        for name, call in (("dialog", lambda: client.get(f"/api/calendars/{familie}/sharing")),
                           ("shares", lambda: client.get("/api/shares")),
                           ("page", lambda: browser.get(f"/api/guest/{token}")),
                           ("feed", lambda: browser.get(f"/abo/{token}.ics")),
                           ("feed again", lambda: browser.get(f"/abo/{token}.ics"))):
            began = time.perf_counter()
            assert call().status_code == 200
            timings[name] = time.perf_counter() - began
    assert timings["dialog"] < 1.0 and timings["shares"] < 1.0, timings
    assert timings["share"] < 1.0 and timings["change"] < 1.0 and timings["take back"] < 1.0, timings
    assert timings["page"] < 2.0 and timings["feed"] < 5.0 and timings["feed again"] < 1.0, timings
