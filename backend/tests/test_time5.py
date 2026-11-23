"""TIME-5 Events: making, changing and deleting them; whole days, time zones, busy or free, the checks of the fields,
read-only calendars, who entered and changed an event, two saves at the same time, a second click."""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import RIGHT_BUSY, RIGHT_EDIT, RIGHT_READ, Account, Event
from app.services import ical

from .calendar_helpers import code, make_calendar, make_event, share, window
from .conftest import make_account, new_client


def _ical(event_id: int) -> str:
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        assert row is not None
        return row.ical


# --- AC-1, AC-10, AC-6 -------------------------------------------------------------------------------------------


def test_time5_ac1_an_event_with_title_start_and_end_shows_at_once(client: TestClient, account: Account) -> None:
    made = make_event(client, "Elternabend", "2026-11-26T19:30", "2026-11-26T21:00")
    assert (made["title"], made["start"], made["end"], made["time_zone"]) == (
        "Elternabend", "2026-11-26T19:30", "2026-11-26T21:00", "Europe/Berlin")
    listed = window(client)
    assert [(row["title"], row["start"], row["end"]) for row in listed] == [
        ("Elternabend", "2026-11-26T18:30:00Z", "2026-11-26T20:00:00Z")]
    assert client.get(f"/api/events/{made['id']}").json()["title"] == "Elternabend"


def test_time5_ac10_without_an_end_an_event_lasts_an_hour(client: TestClient, account: Account) -> None:
    made = make_event(client, "Anruf", "2026-11-26T16:00", None)
    assert made["end"] == "2026-11-26T17:00"


def test_time5_ac6_a_deleted_event_goes_into_the_trash(client: TestClient, account: Account) -> None:
    made = make_event(client)
    assert client.delete(f"/api/events/{made['id']}").status_code == 200
    assert window(client) == []
    with SessionLocal() as db:
        row = db.get(Event, made["id"])
        assert row is not None and row.deleted_at is not None and row.deleted_by == account.id


# --- AC-2, AC-3, EC-10 ---------------------------------------------------------------------------------------------


def test_time5_ac2_a_whole_day_over_three_days_counts_only_the_dates(client: TestClient, account: Account) -> None:
    made = make_event(client, "Klassenfahrt", "2026-11-25", "2026-11-27", zone="", all_day=True)
    assert (made["all_day"], made["start"], made["end"], made["time_zone"]) == (True, "2026-11-25", "2026-11-27", "")
    text = _ical(made["id"])
    assert "DTSTART;VALUE=DATE:20261125" in text and "DTEND;VALUE=DATE:20261128" in text
    listed = window(client)
    assert [(row["all_day"], row["start"], row["end"]) for row in listed] == [(True, "2026-11-25", "2026-11-27")]


def test_time5_ac3_an_event_in_new_york_keeps_its_zone_and_shows_converted(client: TestClient,
                                                                         account: Account) -> None:
    made = make_event(client, "Flug", "2026-11-26T09:00", "2026-11-26T17:00", zone="America/New_York")
    assert (made["start"], made["time_zone"]) == ("2026-11-26T09:00", "America/New_York")
    assert "DTSTART;TZID=America/New_York:20261126T090000" in _ical(made["id"])
    assert "BEGIN:VTIMEZONE" in _ical(made["id"])
    assert window(client)[0]["start"] == "2026-11-26T14:00:00Z"
    refused = client.post("/api/events", json={"title": "X", "start": "2026-11-26T09:00", "time_zone": "Mars/Base"})
    assert refused.status_code == 422 and code(refused) == "invalid_time_zone"


def test_time5_ec10_events_in_1970_and_2100_show_right(client: TestClient, account: Account) -> None:
    old = make_event(client, "Damals", "1970-01-01T10:00", "1970-01-01T11:00")
    far = make_event(client, "Später", "2100-12-31T23:00", "2101-01-01T01:00")
    assert old["start"] == "1970-01-01T10:00" and far["end"] == "2101-01-01T01:00"
    assert [row["title"] for row in window(client, "1969-12-31T00:00:00Z", "1970-01-02T00:00:00Z")] == ["Damals"]
    assert [row["start"] for row in window(client, "2100-12-31T00:00:00Z", "2101-01-02T00:00:00Z")] == [
        "2100-12-31T22:00:00Z"]


# --- EC-1, EC-3, EC-6, EC-13: the checks ---------------------------------------------------------------------


def test_time5_ec1_an_end_before_the_start_is_refused(client: TestClient, account: Account) -> None:
    answer = client.post("/api/events", json={"title": "X", "start": "2026-11-26T19:30", "end": "2026-11-26T19:00",
                                              "time_zone": "Europe/Berlin"})
    assert answer.status_code == 422 and code(answer) == "end_before_start"
    assert answer.json()["detail"]["field"] == "end"
    days = client.post("/api/events", json={"title": "X", "all_day": True, "start": "2026-11-26",
                                            "end": "2026-11-25"})
    assert days.status_code == 422 and code(days) == "end_before_start"
    assert window(client) == []


def test_time5_ec3_titles_up_to_200_and_notes_up_to_10000(client: TestClient, account: Account) -> None:
    make_event(client, "t" * 200, notes="n" * 10_000)
    long_title = client.post("/api/events", json={"title": "t" * 201, "start": "2026-11-26T10:00",
                                                  "time_zone": "Europe/Berlin"})
    assert long_title.status_code == 422 and code(long_title) == "title_too_long"
    assert long_title.json()["detail"]["field"] == "title"
    long_notes = client.post("/api/events", json={"title": "x", "notes": "n" * 10_001, "start": "2026-11-26T10:00",
                                                  "time_zone": "Europe/Berlin"})
    assert long_notes.status_code == 422 and code(long_notes) == "notes_too_long"
    assert len(window(client)) == 1


def test_time5_ec6_an_empty_title_is_refused(client: TestClient, account: Account) -> None:
    for empty in ("", "    "):
        answer = client.post("/api/events", json={"title": empty, "start": "2026-11-26T10:00",
                                                  "time_zone": "Europe/Berlin"})
        assert answer.status_code == 422 and code(answer) == "title_missing"
    made = make_event(client, "  Rand  ")
    assert made["title"] == "Rand"


def test_time5_ec13_dates_that_do_not_exist_and_pasted_markup(client: TestClient, account: Account) -> None:
    for start in ("2026-02-31T10:00", "T10:00", "2026-11-26", "26.11.2026 10:00"):
        answer = client.post("/api/events", json={"title": "x", "start": start, "time_zone": "Europe/Berlin"})
        assert answer.status_code == 422 and code(answer) == "invalid_date", start
        assert answer.json()["detail"]["field"] == "start"
    whole = client.post("/api/events", json={"title": "x", "all_day": True, "start": "2026-02-31"})
    assert whole.status_code == 422 and code(whole) == "invalid_date"
    pasted = make_event(client, "Treffen", notes="<b>fett</b> <a href='https://example.com'>Link</a>")
    assert client.get(f"/api/events/{pasted['id']}").json()["notes"] == (
        "<b>fett</b> <a href='https://example.com'>Link</a>")


# --- AC-4, EC-7: what is stored for the links ------------------------------------------------------------------


def test_time5_ec7_titles_and_notes_keep_umlauts_emoji_and_code_as_text(client: TestClient, account: Account) -> None:
    made = make_event(client, "  Grüße 🎉 <script>x()</script>  ", notes="javascript:alert(1) https://example.com")
    detail = client.get(f"/api/events/{made['id']}").json()
    assert detail["title"] == "Grüße 🎉 <script>x()</script>"
    assert detail["notes"] == "javascript:alert(1) https://example.com"


# --- AC-5, EC-9: read only ---------------------------------------------------------------------------------------


def test_time5_ac5_an_event_in_a_read_only_calendar_cannot_be_changed(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        calendar = make_calendar(browser, "Annas")
        made = make_event(browser, "Arbeit", calendar_id=calendar)
    share(calendar, account.id, RIGHT_READ)
    detail = client.get(f"/api/events/{made['id']}").json()
    assert (detail["writable"], detail["right"], detail["title"]) == (False, "read", "Arbeit")
    for answer in (client.patch(f"/api/events/{made['id']}", json={"title": "Meins"}),
                   client.delete(f"/api/events/{made['id']}"),
                   client.post("/api/events", json={"title": "x", "start": "2026-11-26T10:00",
                                                    "time_zone": "Europe/Berlin", "calendar_id": calendar})):
        assert answer.status_code == 403 and code(answer) == "calendar_read_only"
    assert _ical(made["id"]).count("SUMMARY:Arbeit") == 1


def test_time5_ac5_busy_only_shows_blocks_without_title(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        calendar = make_calendar(browser, "Annas")
        made = make_event(browser, "Arzt", calendar_id=calendar, notes="geheim")
        make_event(browser, "Frei", calendar_id=calendar, start="2026-11-27T10:00", end="2026-11-27T11:00",
                   busy=False)
    share(calendar, account.id, RIGHT_BUSY)
    listed = window(client)
    assert [(row["title"], row["busy_only"]) for row in listed] == [("", True)]
    detail = client.get(f"/api/events/{made['id']}").json()
    assert (detail["title"], detail["notes"], detail["busy_only"], detail["writable"]) == ("", "", True, False)


def test_time5_ec9_deleted_by_someone_else_or_the_right_taken_says_so(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    calendar = make_calendar(client, "Familie")
    share(calendar, anna.id, RIGHT_EDIT)
    made = make_event(client, "Elternabend", calendar_id=calendar)
    second = make_event(client, "Sport", calendar_id=calendar)
    with new_client(anna) as browser:
        client.delete(f"/api/events/{made['id']}")
        late = browser.patch(f"/api/events/{made['id']}", json={"title": "Elternabend neu"})
        assert late.status_code == 409 and code(late) == "event_deleted"
        assert late.json()["detail"]["by"] == "Tester"
        share(calendar, anna.id, RIGHT_READ)
        lowered = browser.patch(f"/api/events/{second['id']}", json={"title": "Sport neu"})
        assert lowered.status_code == 403 and code(lowered) == "calendar_read_only"
        # Who took the right: the owner of the calendar, by name.
        assert lowered.json()["detail"]["owner"] == "Tester"
        moved_in = browser.post("/api/events", json={"title": "x", "start": "2026-11-27T10:00",
                                                     "time_zone": "Europe/Berlin", "calendar_id": calendar})
        assert moved_in.status_code == 403 and moved_in.json()["detail"]["owner"] == "Tester"


# --- AC-9, EC-12: busy or free -----------------------------------------------------------------------------------


def test_time5_ac9_a_timed_event_is_busy_and_a_whole_day_free_unless_set(client: TestClient, account: Account) -> None:
    timed = make_event(client, "Termin")
    whole = make_event(client, "Urlaub", "2026-11-27", None, zone="", all_day=True)
    free = make_event(client, "Vielleicht", busy=False)
    busy_day = make_event(client, "Umzug", "2026-11-28", None, zone="", all_day=True, busy=True)
    assert [made["busy"] for made in (timed, whole, free, busy_day)] == [True, False, False, True]
    assert "TRANSP:OPAQUE" in _ical(timed["id"]) and "TRANSP:TRANSPARENT" in _ical(whole["id"])
    client.patch(f"/api/events/{timed['id']}", json={"busy": False})
    assert "TRANSP:TRANSPARENT" in _ical(timed["id"])


# --- AC-11, EC-2: who, and two at the same time ---------------------------------------------------------------


def test_time5_ac11_a_shared_event_says_who_entered_and_who_changed_it(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    calendar = make_calendar(client, "Familie")
    share(calendar, anna.id, RIGHT_EDIT)
    with new_client(anna) as browser:
        made = make_event(browser, "Fußball", calendar_id=calendar)
    client.patch(f"/api/events/{made['id']}", json={"title": "Fußball Ben"})
    with new_client(anna) as browser:
        detail = browser.get(f"/api/events/{made['id']}").json()
    assert detail["created_by"]["name"] == "Anna" and detail["updated_by"]["name"] == "Tester"
    assert detail["shared"] is True and detail["updated_at"]


def test_time5_ec2_the_later_save_wins_and_the_earlier_one_hears_of_it(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    calendar = make_calendar(client, "Familie")
    share(calendar, anna.id, RIGHT_EDIT)
    made = make_event(client, "Training", calendar_id=calendar)
    base = made["etag"]
    mine = client.patch(f"/api/events/{made['id']}", json={"title": "Training (Halle 2)", "etag": base})
    assert mine.status_code == 200
    with new_client(anna) as browser:
        hers = browser.patch(f"/api/events/{made['id']}", json={"title": "Training fällt aus", "etag": base})
        assert hers.status_code == 200 and hers.json()["conflict"] is None
    detail = client.get(f"/api/events/{made['id']}").json()
    assert detail["title"] == "Training fällt aus"
    assert detail["conflict"]["by"]["name"] == "Anna"
    assert detail["conflict"]["mine"]["title"] == "Training (Halle 2)"
    assert detail["conflict"]["theirs"]["title"] == "Training fällt aus"
    restored = client.post(f"/api/events/{made['id']}/conflict", json={"restore": True}).json()
    assert restored["title"] == "Training (Halle 2)" and restored["conflict"] is None


def test_time5_ec2_leave_it_keeps_the_later_version(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    calendar = make_calendar(client, "Familie")
    share(calendar, anna.id, RIGHT_EDIT)
    made = make_event(client, "Training", calendar_id=calendar)
    client.patch(f"/api/events/{made['id']}", json={"title": "Meins", "etag": made["etag"]})
    with new_client(anna) as browser:
        browser.patch(f"/api/events/{made['id']}", json={"title": "Ihrs", "etag": made["etag"]})
    left = client.post(f"/api/events/{made['id']}/conflict", json={"restore": False}).json()
    assert left["title"] == "Ihrs" and left["conflict"] is None
    plain = client.patch(f"/api/events/{made['id']}", json={"title": "Weiter", "etag": left["etag"]}).json()
    assert plain["conflict"] is None


# --- EC-8: a second click --------------------------------------------------------------------------------------


def test_time5_ec8_two_clicks_or_a_retry_make_one_event(client: TestClient, account: Account) -> None:
    body = {"title": "Zahnarzt", "start": "2026-11-26T16:30", "time_zone": "Europe/Berlin", "client_ref": "ev-1"}
    first, again = client.post("/api/events", json=body), client.post("/api/events", json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    browsers = [new_client(account) for _ in range(5)]
    racing = {**body, "client_ref": "ev-2"}
    threads = [threading.Thread(target=browser.post, args=("/api/events",), kwargs={"json": racing})
               for browser in browsers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for browser in browsers:
        browser.close()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 2


def test_time5_a_moved_event_lands_only_in_the_other_calendar(client: TestClient, account: Account) -> None:
    other = make_calendar(client, "Familie")
    made = make_event(client, "Sport")
    moved = client.patch(f"/api/events/{made['id']}", json={"calendar_id": other}).json()
    assert moved["calendar_id"] == other
    assert [row["calendar_id"] for row in window(client)] == [other]
    assert ical.etag_of(_ical(made["id"])) == moved["etag"]
