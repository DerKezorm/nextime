"""TIME-14 Calm display on the server: background calendars come with their mark, a person stands at events of shared
calendars (picture or initial), the same event in two calendars of one person comes once with all its sources,
"busy only" shows busy blocks without title."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.db import SessionLocal
from app.models import RIGHT_BUSY, RIGHT_EDIT, RIGHT_READ, Account, Event
from app.services import events, ical

from .calendar_helpers import calendars_of, make_calendar, make_event, named, share, unshare, window
from .conftest import make_account, new_client


def _set(account_id: int, **values: object) -> None:
    with SessionLocal() as db:
        row = db.get(Account, account_id)
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()


def _copy_into(event_id: int, calendar_id: int) -> None:
    """The very same iCalendar object (same UID) in a second calendar, as a sync from another source brings it."""
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        assert row is not None
        db.execute(insert(Event), [{
            "calendar_id": calendar_id, "uid": row.uid, "href": "copy-" + row.href, "ical": row.ical, "etag": row.etag,
            "title": row.title, "starts_at": row.starts_at, "ends_at": row.ends_at, "all_day": row.all_day,
            "time_zone": row.time_zone, "recurring": row.recurring, "busy": row.busy, "location": row.location,
            "size": row.size, "created_by": row.created_by, "updated_by": row.updated_by, "prev_etag": "",
            "prev_ical": "", "created_at": row.created_at, "updated_at": row.updated_at}])
        db.commit()


def _locate(event_id: int, place: str) -> None:
    """A place as a device or TIME-8 puts it into the object."""
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        assert row is not None
        text = row.ical.replace("SUMMARY:", f"LOCATION:{place}\r\nSUMMARY:", 1)
        # As every change does: the object and the columns derived from it together.
        events.write(db, row, text, ical.parse(text), row.updated_by)
        db.commit()


# --- AC-1, EC-10 ------------------------------------------------------------------------------------------------------


def test_time14_ac1_the_switch_show_as_background_comes_with_the_calendar_on_and_off(client: TestClient,
                                                                                    account: Account) -> None:
    feiertage = make_calendar(client, "Feiertage NRW", background=True)
    assert named(client, "Feiertage NRW")["background"] is True
    assert client.patch(f"/api/calendars/{feiertage}", json={"background": False}).status_code == 200
    assert named(client, "Feiertage NRW")["background"] is False
    assert named(client, "Personal")["background"] is False


def test_time14_ec10_a_background_entry_over_two_weeks_comes_on_each_of_its_days(client: TestClient,
                                                                                account: Account) -> None:
    ferien = make_calendar(client, "Schulferien", background=True)
    make_event(client, "Weihnachtsferien", "2026-12-21", "2027-01-02", zone="", all_day=True, calendar_id=ferien)
    for day in ("2026-12-21", "2026-12-28", "2027-01-02"):
        rows = window(client, f"{day}T00:00:00Z", f"{day}T23:59:00Z")
        assert [(row["title"], row["start"], row["end"]) for row in rows] == [
            ("Weihnachtsferien", "2026-12-21", "2027-01-02")]
    assert window(client, "2027-01-05T00:00:00Z", "2027-01-05T23:00:00Z") == []


# --- AC-2, EC-7, EC-8, EC-9, EC-11 ------------------------------------------------------------------------------------


def test_time14_ac2_an_event_entered_by_another_person_carries_her_picture_or_initial(client: TestClient,
                                                                                     account: Account) -> None:
    anna, andreas = make_account("anna"), make_account("andreas")
    familie = make_calendar(client, "Familie")
    share(familie, anna.id, RIGHT_EDIT)
    share(familie, andreas.id, RIGHT_EDIT)
    _set(anna.id, avatar=b"RIFF", avatar_at=datetime(2026, 11, 20, 12, 0, tzinfo=UTC))
    with new_client(anna) as browser:
        make_event(browser, "Zahnarzt Ben", "2026-11-26T16:30", "2026-11-26T17:15", calendar_id=familie)
    with new_client(andreas) as browser:
        make_event(browser, "Einkaufen", "2026-11-27T10:00", "2026-11-27T11:00", calendar_id=familie)
    make_event(client, "Telefonat mit Oma", "2026-11-26T20:30", "2026-11-26T21:00", calendar_id=familie)
    people = {row["title"]: row["person"] for row in window(client)}
    assert people["Zahnarzt Ben"] == {"id": anna.id, "name": "Anna", "color": None, "avatar": "2026-11-20T12:00:00+00:00"}
    # EC-9: two with the same initial: the full name comes along for the panel.
    assert people["Einkaufen"]["name"] == "Andreas" and people["Einkaufen"]["avatar"] is None
    # What the member entered itself stays calm, without a picture.
    assert people["Telefonat mit Oma"] is None
    # The picture is there for those who see a calendar with her.
    assert client.get(f"/api/avatars/{anna.id}?v=1").status_code == 200
    with new_client(andreas) as browser:
        assert browser.get(f"/api/avatars/{anna.id}?v=1").status_code == 200
        assert {row["title"]: row["person"] for row in window(browser)}["Telefonat mit Oma"]["name"] == "Tester"


def test_time14_ec7_an_event_of_a_calendar_nobody_else_sees_has_no_person(client: TestClient,
                                                                          account: Account) -> None:
    make_event(client, "Werkstatt", "2026-11-27T08:15", "2026-11-27T09:00")
    assert [row["person"] for row in window(client)] == [None]


def test_time14_ec8_a_deleted_or_blocked_person_shows_only_her_initial(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    share(familie, anna.id, RIGHT_EDIT)
    _set(anna.id, avatar=b"RIFF", avatar_at=datetime(2026, 11, 20, 12, 0, tzinfo=UTC))
    with new_client(anna) as browser:
        make_event(browser, "Schwimmen", calendar_id=familie)
    assert window(client)[0]["person"]["avatar"] is not None
    _set(anna.id, blocked_at=datetime(2026, 11, 25, tzinfo=UTC))
    assert window(client)[0]["person"] == {"id": anna.id, "name": "Anna", "color": None, "avatar": None}
    _set(anna.id, blocked_at=None, deleted_at=datetime(2026, 11, 25, tzinfo=UTC))
    assert window(client)[0]["person"]["avatar"] is None


def test_time14_ec11_a_name_and_a_title_with_code_come_as_plain_text(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    _set(anna.id, display_name="<img src=x onerror=alert(1)>")
    familie = make_calendar(client, "Familie")
    share(familie, anna.id, RIGHT_EDIT)
    with new_client(anna) as browser:
        make_event(browser, "<b>Fest</b>", calendar_id=familie)
    row = window(client)[0]
    assert (row["title"], row["person"]["name"]) == ("<b>Fest</b>", "<img src=x onerror=alert(1)>")


# --- AC-3, AC-4, EC-1, EC-2, EC-3, EC-5, EC-6, EC-12 ----------------------------------------------------------------


def test_time14_ac3_the_same_uid_in_two_own_calendars_comes_once_with_both_sources(client: TestClient,
                                                                                  account: Account) -> None:
    personal = named(client, "Personal")["id"]
    elternbeirat = make_calendar(client, "Elternbeirat")
    made = make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00", calendar_id=elternbeirat)
    _copy_into(made["id"], personal)
    rows = window(client)
    assert len(rows) == 1
    # The first in the order of the list stands in front ("Personal" before "Elternbeirat").
    assert rows[0]["calendar_id"] == personal
    assert [source["calendar_id"] for source in rows[0]["sources"]] == [personal, elternbeirat]
    assert all(source["writable"] for source in rows[0]["sources"])


def test_time14_ac3_a_series_in_two_calendars_merges_occurrence_by_occurrence(client: TestClient,
                                                                             account: Account) -> None:
    verein = make_calendar(client, "Verein")
    made = make_event(client, "Training", "2026-11-02T18:30", "2026-11-02T20:00", calendar_id=verein,
                      rule={"freq": "weekly", "weekdays": ["MO", "TH"]})
    _copy_into(made["id"], named(client, "Personal")["id"])
    rows = window(client, "2026-11-23T00:00:00Z", "2026-11-30T00:00:00Z")
    assert [(row["start"], len(row["sources"])) for row in rows] == [("2026-11-23T17:30:00Z", 2),
                                                                      ("2026-11-26T17:30:00Z", 2)]


def test_time14_ec6_the_same_instant_in_utc_and_in_berlin_is_the_same_time(client: TestClient,
                                                                          account: Account) -> None:
    google = make_calendar(client, "Elternbeirat")
    make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00", calendar_id=google)
    make_event(client, "Elternabend", "2026-11-25T18:30", "2026-11-25T20:00", zone="UTC")
    rows = window(client)
    assert len(rows) == 1 and len(rows[0]["sources"]) == 2


def test_time14_ec12_titles_alike_but_for_case_and_edges_merge_another_end_does_not(client: TestClient,
                                                                                   account: Account) -> None:
    familie = make_calendar(client, "Familie")
    make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00")
    with SessionLocal() as db:
        made = make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00", calendar_id=familie)
        row = db.get(Event, made["id"])
        assert row is not None
        text = row.ical.replace("SUMMARY:Elternabend", "SUMMARY:elternabend ")
        events.write(db, row, text, ical.parse(text), row.updated_by)
        db.commit()
    assert [len(row.get("sources", [])) for row in window(client)] == [2]
    make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:30", calendar_id=familie)
    assert sorted(len(row.get("sources", [])) for row in window(client)) == [0, 2]


def test_time14_ec1_two_persons_sport_at_the_same_time_never_merges(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        annas = make_calendar(browser, "Anna")
        make_event(browser, "Sport", "2026-11-26T18:00", "2026-11-26T19:00", calendar_id=annas)
    share(annas, account.id, RIGHT_READ)
    make_event(client, "Sport", "2026-11-26T18:00", "2026-11-26T19:00")
    rows = window(client)
    assert len(rows) == 2 and all("sources" not in row for row in rows)


def test_time14_ac4_merging_switched_off_shows_both(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00")
    make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00", calendar_id=familie)
    assert client.get("/api/auth/me").json()["display"]["merge_duplicates"] is True
    assert len(window(client)) == 1
    assert client.put("/api/me/display", json={"merge_duplicates": False}).status_code == 200
    assert len(window(client)) == 2
    assert client.put("/api/me/display", json={"merge_duplicates": "nein"}).status_code == 422


def test_time14_ec2_ec5_changing_one_source_changes_only_it_and_then_they_part(client: TestClient,
                                                                              account: Account) -> None:
    familie = make_calendar(client, "Familie")
    first = make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00")
    second = make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00", calendar_id=familie)
    merged = window(client)[0]
    chosen = next(source for source in merged["sources"] if source["calendar_id"] == familie)
    answer = client.patch(f"/api/events/{chosen['id']}", json={"title": "Elternabend Klasse 2", "etag": chosen["etag"]})
    assert answer.status_code == 200
    assert client.get(f"/api/events/{first['id']}").json()["title"] == "Elternabend"
    assert client.get(f"/api/events/{second['id']}").json()["title"] == "Elternabend Klasse 2"
    rows = window(client)
    assert sorted(row["title"] for row in rows) == ["Elternabend", "Elternabend Klasse 2"]
    assert all("sources" not in row for row in rows)


def _move_copy(href: str, minutes: int) -> None:
    """The copy (same UID) starts and ends ``minutes`` later, as when one source moved it and the other not yet."""
    with SessionLocal() as db:
        row = db.scalar(select(Event).where(Event.href == href))
        assert row is not None
        parsed = ical.parse(row.ical)
        master = ical.master_of(parsed)
        start = master.decoded("DTSTART")
        length = ical.duration_of(master)
        ical.set_times(master, start + timedelta(minutes=minutes), start + timedelta(minutes=minutes) + length)
        events.write(db, row, ical.serialize(parsed), parsed, row.updated_by)
        db.commit()


def test_time14_ec5_the_same_uid_at_another_time_comes_as_two_events(client: TestClient, account: Account) -> None:
    elternbeirat = make_calendar(client, "Elternbeirat")
    made = make_event(client, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00", calendar_id=elternbeirat)
    _copy_into(made["id"], named(client, "Personal")["id"])
    assert [len(row.get("sources", [])) for row in window(client)] == [2]
    _move_copy("copy-" + _href(made["id"]), 30)
    rows = window(client)
    assert len(rows) == 2 and all("sources" not in row for row in rows)


def test_time14_ec5_busy_blocks_with_the_same_uid_at_another_time_part(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        arbeit = make_calendar(browser, "Arbeit")
        projekte = make_calendar(browser, "Projekte")
        made = make_event(browser, "Kundentermin", "2026-11-26T14:00", "2026-11-26T16:00", calendar_id=arbeit)
    _copy_into(made["id"], projekte)
    share(arbeit, account.id, RIGHT_BUSY)
    share(projekte, account.id, RIGHT_BUSY)
    assert [len(row.get("sources", [])) for row in window(client)] == [2]
    _move_copy("copy-" + _href(made["id"]), 60)
    rows = window(client)
    assert len(rows) == 2 and all("sources" not in row for row in rows)


def _href(event_id: int) -> str:
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        assert row is not None
        return row.href


def test_time14_ec3_a_read_only_source_is_marked_so(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        arbeit = make_calendar(browser, "Anna Arbeit")
        privat = make_calendar(browser, "Anna Privat")
        made = make_event(browser, "Elternabend", "2026-11-25T19:30", "2026-11-25T21:00", calendar_id=arbeit)
    _copy_into(made["id"], privat)
    share(arbeit, account.id, RIGHT_READ)
    share(privat, account.id, RIGHT_EDIT)
    rows = window(client)
    assert len(rows) == 1
    assert {source["calendar_id"]: source["writable"] for source in rows[0]["sources"]} == {arbeit: False,
                                                                                            privat: True}
    assert client.delete(f"/api/events/{made['id']}").status_code == 403


# --- AC-5 ---------------------------------------------------------------------------------------------------------------


def test_time14_ac5_busy_only_shows_the_busy_events_without_title_with_the_owner(client: TestClient,
                                                                                account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        arbeit = make_calendar(browser, "Arbeit")
        secret = make_event(browser, "Kundentermin", "2026-11-26T14:00", "2026-11-26T16:00", calendar_id=arbeit,
                            notes="geheim")
        make_event(browser, "Mittag frei", "2026-11-26T12:00", "2026-11-26T13:00", calendar_id=arbeit, busy=False)
        make_event(browser, "Messe", "2026-11-27", "2026-11-27", zone="", all_day=True, calendar_id=arbeit)
    _locate(secret["id"], "Raum Nord")
    share(arbeit, account.id, RIGHT_BUSY)
    rows = window(client)
    assert len(rows) == 1
    row = rows[0]
    assert (row["title"], row["location"], row["busy_only"], row["start"]) == ("", "", True, "2026-11-26T13:00:00Z")
    assert row["person"]["name"] == "Anna"
    assert calendars_of(client)["calendars"][-1]["right"] == "busy"


def test_time14_the_list_brings_place_and_version_for_the_views(client: TestClient, account: Account) -> None:
    made = make_event(client, "Zahnarzt")
    _locate(made["id"], "Lindenstraße 4")
    row = window(client)[0]
    with SessionLocal() as db:
        etag = db.scalar(select(Event.etag).where(Event.id == made["id"]))
    assert (row["location"], row["etag"], row["writable"]) == ("Lindenstraße 4", etag, True)


# --- Nacharbeit nach der Prüfung (2026-11-22) -------------------------------------------------------------------------


def test_time14_ac3_two_busy_blocks_with_the_same_uid_come_once_others_stay_apart(client: TestClient,
                                                                                  account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        arbeit = make_calendar(browser, "Arbeit")
        projekte = make_calendar(browser, "Projekte")
        made = make_event(browser, "Kundentermin", "2026-11-26T14:00", "2026-11-26T16:00", calendar_id=arbeit)
        make_event(browser, "Anderes", "2026-11-26T14:00", "2026-11-26T16:00", calendar_id=projekte)
    _copy_into(made["id"], projekte)
    share(arbeit, account.id, RIGHT_BUSY)
    share(projekte, account.id, RIGHT_BUSY)
    rows = window(client)
    # The same UID once; the other block of the same time has no title to compare and stays on its own.
    assert sorted(len(row.get("sources", [])) for row in rows) == [0, 2]
    assert all(row["title"] == "" for row in rows)


def _picture(account_id: int) -> None:
    _set(account_id, avatar=b"RIFF-picture", avatar_at=datetime(2026, 11, 20, 12, 0, tzinfo=UTC))


def test_time14_ac2_every_signed_in_person_sees_the_picture_shared_calendar_or_not(client: TestClient,
                                                                                    account: Account) -> None:
    """Since the shared account module (konten 1.0.0, the rule of every nex app): a picture is for every account
    signed in on the server, not only for those who see a calendar together (the rule of 0.4.0)."""
    anna, bob = make_account("anna"), make_account("bob")
    _picture(anna.id)
    with new_client(anna) as browser:
        annas = make_calendar(browser, "Anna")
    with new_client(bob) as browser:
        make_calendar(browser, "Bob")
        address = f"/api/avatars/{anna.id}?v=1"
        assert browser.get(address).status_code == 200
        share(annas, bob.id, RIGHT_READ)
        assert browser.get(address).status_code == 200
        unshare(annas, bob.id)
        assert browser.get(address).status_code == 200


def test_time14_ec8_the_picture_of_a_blocked_or_deleted_person_reaches_nobody_else(client: TestClient,
                                                                                    account: Account) -> None:
    anna, bob = make_account("anna"), make_account("bob")
    _picture(anna.id)
    with new_client(anna) as browser:
        annas = make_calendar(browser, "Anna")
    share(annas, bob.id, RIGHT_READ)
    address = f"/api/avatars/{anna.id}?v=1"
    with new_client(bob) as browser:
        assert browser.get(address).status_code == 200
        _set(anna.id, blocked_at=datetime(2026, 11, 25, tzinfo=UTC))
        assert browser.get(address).status_code == 404
        # The operator keeps seeing it in the list of accounts.
        assert client.get(address).status_code == 200
        _set(anna.id, blocked_at=None, deleted_at=datetime(2026, 11, 25, tzinfo=UTC))
        assert browser.get(address).status_code == 404
