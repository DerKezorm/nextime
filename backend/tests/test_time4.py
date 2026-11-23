"""TIME-4 Own calendars: making them with name and one of eleven colors, changing, hiding, ordering, deleting into the
trash, the default for new events, "Personal" for a new account; what a shared calendar changes only for the one
who sees it."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import CALENDAR_COLORS, RIGHT_EDIT, RIGHT_READ, Account, Calendar, CalendarPrefs
from app.routers.dav import ctag
from app.services import calendars

from .calendar_helpers import calendars_of, code, make_calendar, make_event, named, share, window
from .conftest import make_account, new_client


def _own(client: TestClient) -> list[dict]:
    return [row for row in calendars_of(client)["calendars"] if row["group"] == "own"]


# --- AC-10: "Personal" for a new account --------------------------------------------------------------------------


def test_time4_ac10_a_new_account_gets_personal_in_slate_as_the_default(client: TestClient, account: Account) -> None:
    listed = calendars_of(client)
    assert [(row["name"], row["color"], row["default"]) for row in listed["calendars"]] == [
        ("Personal", "slate", True)]
    assert listed["default_id"] == listed["calendars"][0]["id"]


def test_time4_ac10_a_german_account_gets_persoenlich(client: TestClient) -> None:
    anna = make_account("anna")
    with SessionLocal() as db:
        db.get(Account, anna.id).language = "de"  # type: ignore[union-attr]
        db.commit()
    with new_client(anna) as browser:
        assert [row["name"] for row in calendars_of(browser)["calendars"]] == ["Persönlich"]


def test_time4_ac10_two_sign_ins_at_the_same_time_make_it_exactly_once(client: TestClient) -> None:
    anna = make_account("anna")
    browsers = [new_client(anna) for _ in range(6)]
    threads = [threading.Thread(target=browser.get, args=("/api/calendars",)) for browser in browsers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for browser in browsers:
        browser.close()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Calendar).where(Calendar.owner_id == anna.id)) == 1
    with new_client(anna) as browser:
        assert len(calendars_of(browser)["calendars"]) == 1


def test_time4_ac10_two_first_sign_ins_in_the_very_same_moment_make_one(client: TestClient,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Both sign-ins have read "not made yet" before either writes: only the claim on the account's row keeps the
    second from making another "Personal"."""
    anna = make_account("anna")
    meeting = threading.Barrier(2, timeout=10)
    real_insert = calendars.insert

    def insert_after_both_read(table):
        if table is CalendarPrefs:
            meeting.wait()
        return real_insert(table)

    monkeypatch.setattr(calendars, "insert", insert_after_both_read)
    problems: list[BaseException] = []

    def first_sign_in() -> None:
        try:
            with SessionLocal() as db:
                row = db.get(Account, anna.id)
                assert row is not None
                calendars.ensure_seeded(db, row)
        except BaseException as problem:  # noqa: BLE001 - handed to the test thread
            problems.append(problem)

    threads = [threading.Thread(target=first_sign_in) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert problems == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Calendar).where(Calendar.owner_id == anna.id)) == 1


# --- AC-1, AC-7, EC-2, EC-3, EC-6: making ---------------------------------------------------------------------


def test_time4_ac1_a_calendar_with_each_of_the_eleven_colors_stands_under_own_at_once(client: TestClient,
                                                                                    account: Account) -> None:
    for color in CALENDAR_COLORS:
        make_calendar(client, f"Kalender {color}", color=color)
    own = _own(client)
    assert {row["color"] for row in own} == set(CALENDAR_COLORS)
    assert len(own) == 12  # with "Personal"
    assert client.post("/api/calendars", json={"name": "Lila", "color": "purple"}).status_code == 422


def test_time4_ac1_slate_is_the_color_when_none_is_chosen(client: TestClient, account: Account) -> None:
    make_calendar(client, "Verein")
    assert named(client, "Verein")["color"] == "slate"


def test_time4_ac7_the_dialog_values_are_kept_and_can_be_changed_later(client: TestClient, account: Account) -> None:
    calendar = make_calendar(client, "Feiertage", background=True, default=True, reminder=15)
    row = named(client, "Feiertage")
    assert (row["background"], row["default"], row["reminder"]) == (True, True, 15)
    assert calendars_of(client)["reminders"] == [0, 5, 15, 30, 60, 1440]
    made = named(client, "Personal")
    assert made["reminder"] == 0 and made["background"] is False
    answer = client.patch(f"/api/calendars/{calendar}", json={"background": False, "reminder": 0})
    assert answer.status_code == 200
    row = named(client, "Feiertage")
    assert (row["background"], row["reminder"]) == (False, 0)
    assert client.patch(f"/api/calendars/{calendar}", json={"reminder": 7}).status_code == 422


def test_time4_ec2_two_calendars_may_have_the_same_name(client: TestClient, account: Account) -> None:
    first, second = make_calendar(client, "Familie", color="red"), make_calendar(client, "Familie", color="blue")
    assert first != second
    assert [row["color"] for row in _own(client) if row["name"] == "Familie"] == ["red", "blue"]


def test_time4_ec3_names_empty_too_long_blanks_umlauts_and_emoji(client: TestClient, account: Account) -> None:
    for empty in ("", "   "):
        answer = client.post("/api/calendars", json={"name": empty})
        assert answer.status_code == 422 and code(answer) == "name_missing"
        assert answer.json()["detail"]["field"] == "name"
    too_long = client.post("/api/calendars", json={"name": "x" * 61})
    assert too_long.status_code == 422 and code(too_long) == "name_too_long"
    make_calendar(client, "y" * 60)
    make_calendar(client, "  Grüße 🎉 Ölmühle  ")
    names = [row["name"] for row in _own(client)]
    assert "y" * 60 in names and "Grüße 🎉 Ölmühle" in names


def test_time4_ec6_two_clicks_or_a_retry_make_one_calendar(client: TestClient, account: Account) -> None:
    first = client.post("/api/calendars", json={"name": "Verein", "client_ref": "ref-verein-1"})
    again = client.post("/api/calendars", json={"name": "Verein", "client_ref": "ref-verein-1"})
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["created"] == again.json()["created"]
    browsers = [new_client(account) for _ in range(5)]
    threads = [threading.Thread(target=browser.post, args=("/api/calendars",),
                                kwargs={"json": {"name": "Chor", "client_ref": "ref-chor"}}) for browser in browsers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for browser in browsers:
        browser.close()
    assert [row["name"] for row in _own(client)].count("Chor") == 1
    assert [row["name"] for row in _own(client)].count("Verein") == 1


# --- AC-2, EC-11: changing --------------------------------------------------------------------------------------


def test_time4_ac2_name_and_color_change_everywhere_and_devices_see_a_new_tag(client: TestClient,
                                                                            account: Account) -> None:
    calendar = make_calendar(client, "Familie")
    with SessionLocal() as db:
        before = ctag(db, db.get(Calendar, calendar))
    assert client.patch(f"/api/calendars/{calendar}", json={"name": "Familie Muster", "color": "green"}).status_code \
        == 200
    with new_client(account) as other:
        row = named(other, "Familie Muster")
        assert row["color"] == "green"
    with SessionLocal() as db:
        assert ctag(db, db.get(Calendar, calendar)) != before


def test_time4_ec11_two_sessions_change_the_same_calendar_and_the_later_wins(client: TestClient,
                                                                           account: Account) -> None:
    calendar = make_calendar(client, "Familie")
    with new_client(account) as other:
        client.patch(f"/api/calendars/{calendar}", json={"name": "Erste", "color": "red"})
        other.patch(f"/api/calendars/{calendar}", json={"name": "Zweite", "color": "blue"})
    row = next(row for row in _own(client) if row["id"] == calendar)
    assert (row["name"], row["color"]) == ("Zweite", "blue")


# --- AC-3, AC-5, EC-1, EC-8, EC-9: hiding, order, default -----------------------------------------------------


def test_time4_ac3_a_hidden_calendar_keeps_its_events_and_hides_them_in_every_session(client: TestClient,
                                                                                    account: Account) -> None:
    calendar = make_calendar(client, "Verein")
    make_event(client, "Training", calendar_id=calendar)
    assert [row["title"] for row in window(client)] == ["Training"]
    assert client.patch(f"/api/calendars/{calendar}", json={"hidden": True}).status_code == 200
    with new_client(account) as other:
        assert named(other, "Verein")["hidden"] is True
        assert window(other) == []
        assert [row["title"] for row in window(other, hidden="true")] == ["Training"]
    client.patch(f"/api/calendars/{calendar}", json={"hidden": False})
    assert [row["title"] for row in window(client)] == ["Training"]


def test_time4_ac5_the_order_within_a_group_is_free_and_the_groups_stay(client: TestClient, account: Account) -> None:
    personal = named(client, "Personal")["id"]
    first, second = make_calendar(client, "A"), make_calendar(client, "B")
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas")
    share(shared, account.id, RIGHT_READ)
    assert client.put("/api/calendars/order", json={"ids": [second, personal, first]}).status_code == 200
    listed = calendars_of(client)["calendars"]
    assert [row["name"] for row in listed] == ["B", "Personal", "A", "Annas"]
    assert [row["group"] for row in listed] == ["own", "own", "own", "shared"]
    mixed = client.put("/api/calendars/order", json={"ids": [shared, first]})
    assert mixed.status_code == 422 and code(mixed) == "invalid_order"
    with new_client(account) as other:
        assert [row["name"] for row in calendars_of(other)["calendars"]] == ["B", "Personal", "A", "Annas"]


def test_time4_ec1_the_default_moves_on_when_it_is_deleted_hidden_or_read_only(client: TestClient,
                                                                            account: Account) -> None:
    personal = named(client, "Personal")["id"]
    family = make_calendar(client, "Familie", default=True)
    assert calendars_of(client)["default_id"] == family
    client.delete(f"/api/calendars/{family}")
    listed = calendars_of(client)
    assert listed["default_id"] == personal
    assert (listed["default_moved"], listed["default_was"]) == ("deleted", "Familie")
    made = make_event(client, "Ohne Kalender")
    assert made["calendar_id"] == personal
    club = make_calendar(client, "Verein", default=True)
    client.patch(f"/api/calendars/{club}", json={"hidden": True})
    listed = calendars_of(client)
    assert listed["default_id"] == personal and listed["default_moved"] == "hidden"
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas")
    share(shared, account.id, RIGHT_EDIT)
    client.patch(f"/api/calendars/{club}", json={"hidden": False})
    assert client.patch(f"/api/calendars/{shared}", json={"default": True}).status_code == 200
    share(shared, account.id, RIGHT_READ)
    listed = calendars_of(client)
    assert listed["default_moved"] == "read_only" and listed["default_was"] == "Annas"
    assert listed["default_id"] in (personal, club)


def test_time4_ec8_hiding_ordering_and_the_default_of_a_shared_calendar_change_nothing_for_the_owner(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        calendars_of(browser)
        shared = make_calendar(browser, "Familie")
        other = make_calendar(browser, "Arbeit")
        before = calendars_of(browser)
    share(shared, account.id, RIGHT_EDIT)
    share(other, account.id, RIGHT_EDIT)
    client.patch(f"/api/calendars/{shared}", json={"hidden": True, "default": True})
    client.put("/api/calendars/order", json={"ids": [other, shared]})
    mine = calendars_of(client)
    assert mine["default_id"] == shared or mine["default_moved"] == "hidden"
    with new_client(anna) as browser:
        after = calendars_of(browser)
    assert after["default_id"] == before["default_id"]
    assert [(row["name"], row["hidden"]) for row in after["calendars"]] == [
        (row["name"], row["hidden"]) for row in before["calendars"]]
    refused = client.patch(f"/api/calendars/{shared}", json={"name": "Meins"})
    assert refused.status_code == 403 and code(refused) == "calendar_not_own"


def test_time4_ec9_a_read_only_calendar_is_never_the_default(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Abo-artig")
    share(shared, account.id, RIGHT_READ)
    answer = client.patch(f"/api/calendars/{shared}", json={"default": True})
    assert answer.status_code == 409 and code(answer) == "calendar_read_only"
    row = named(client, "Abo-artig")
    assert row["writable"] is False and row["default"] is False


# --- AC-4, EC-7, EC-10: deleting ------------------------------------------------------------------------------


def test_time4_ac4_deleting_puts_the_calendar_with_its_events_into_the_trash(client: TestClient,
                                                                           account: Account) -> None:
    calendar = make_calendar(client, "Verein")
    make_event(client, "Training", calendar_id=calendar)
    make_event(client, "Sommerfest", calendar_id=calendar, start="2026-11-28T15:00", end="2026-11-28T18:00")
    assert named(client, "Verein")["events"] == 2
    answer = client.delete(f"/api/calendars/{calendar}")
    assert answer.status_code == 200 and answer.json()["deleted_events"] == 2
    assert "Verein" not in [row["name"] for row in calendars_of(client)["calendars"]]
    assert window(client, hidden="true") == []
    with SessionLocal() as db:
        row = db.get(Calendar, calendar)
        assert row is not None and row.deleted_at is not None and row.deleted_by == account.id


def test_time4_ec7_a_calendar_deleted_while_someone_writes_into_it_says_so(client: TestClient,
                                                                         account: Account) -> None:
    anna = make_account("anna")
    calendar = make_calendar(client, "Familie")
    share(calendar, anna.id, RIGHT_EDIT)
    with new_client(anna) as browser:
        made = make_event(browser, "Elternabend", calendar_id=calendar)
        client.delete(f"/api/calendars/{calendar}")
        late = browser.post("/api/events", json={"title": "Noch einer", "start": "2026-11-27T10:00",
                                                 "time_zone": "Europe/Berlin", "calendar_id": calendar})
        assert late.status_code == 409 and code(late) == "calendar_gone"
        changed = browser.patch(f"/api/events/{made['id']}", json={"title": "Elternabend 2"})
        assert changed.status_code == 409 and code(changed) == "calendar_gone"
        assert [row["name"] for row in calendars_of(browser)["calendars"] if row["writable"]] == ["Personal"]


def test_time4_ec10_a_deleted_shared_calendar_disappears_for_everybody(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    calendar = make_calendar(client, "Familie")
    share(calendar, anna.id, RIGHT_READ)
    with new_client(anna) as browser:
        assert "Familie" in [row["name"] for row in calendars_of(browser)["calendars"]]
        client.delete(f"/api/calendars/{calendar}")
        assert "Familie" not in [row["name"] for row in calendars_of(browser)["calendars"]]
    refused = client.delete(f"/api/calendars/{calendar}")
    assert refused.status_code == 404


# --- AC-6, AC-9, EC-12: counter and groups --------------------------------------------------------------------


def test_time4_ac6_the_counter_says_how_many_of_20(client: TestClient, account: Account) -> None:
    for number in range(8):
        make_calendar(client, f"K{number}")
    listed = calendars_of(client)
    assert (listed["count"], listed["smooth_up_to"]) == (9, 20)
    for number in range(11):
        make_calendar(client, f"L{number}")
    assert calendars_of(client)["count"] == 20
    assert client.post("/api/calendars", json={"name": "Einer mehr"}).status_code == 201
    assert calendars_of(client)["count"] == 21


def test_time4_ac9_ec12_the_list_comes_by_group_and_an_empty_source_has_none(client: TestClient,
                                                                            account: Account) -> None:
    assert {row["group"] for row in calendars_of(client)["calendars"]} == {"own"}
    anna = make_account("anna")
    with new_client(anna) as browser:
        shared = make_calendar(browser, "Annas")
    share(shared, account.id, RIGHT_READ)
    listed = calendars_of(client)["calendars"]
    assert [row["group"] for row in listed] == ["own", "shared"]
    assert listed[1]["owner"] == "Anna" and listed[1]["right"] == "read"


def test_time4_ec4_a_name_with_code_is_kept_as_text(client: TestClient, account: Account) -> None:
    make_calendar(client, "<script>alert(1)</script>")
    assert named(client, "<script>alert(1)</script>")["name"] == "<script>alert(1)</script>"
