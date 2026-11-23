"""TIME-22 Search: events by title, place and notes in the calendars shown of the chosen collection, past and future
without a limit in time; coming (the next on top) and past (the latest on top); series once; never "busy only", the
trash or hidden calendars; duplicates once; literal words, upper and lower case alike; and quick with 20,000 events.
"Now" is the device's, fixed here on Thursday, 26 November 2026, 16:05 in Berlin."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from app.db import SessionLocal
from app.models import RIGHT_BUSY, RIGHT_EDIT, RIGHT_READ, Account, Event
from app.routers.search import fill_search_text
from app.services import ical, search

from .calendar_helpers import code, make_calendar, make_event, share
from .conftest import make_account, new_client
from .remote_helpers import (
    SWITCHES,
    Remote,
    connected,
    dav_server,  # noqa: F401 - the fixture
    event_text,
    quiet_world,  # noqa: F401 - the fixture
    remote_account,
    round_now,
)
from .test_time18 import invitation
from .test_time20 import calendar_url, device_for, raw

NOW = "2026-11-26T15:05:00Z"
ZONE = "Europe/Berlin"


def find(client: TestClient, q: str, **query: Any) -> dict[str, Any]:
    answer = client.get("/api/search", params={"q": q, "now": NOW, "zone": ZONE, **query})
    assert answer.status_code == 200, answer.text
    return answer.json()


def titles(found: dict[str, Any]) -> list[str]:
    return [item["title"] for item in found["items"]]


def coming(found: dict[str, Any]) -> list[str]:
    return [item["title"] for item in found["items"] if item["coming"]]


def past(found: dict[str, Any]) -> list[str]:
    return [item["title"] for item in found["items"] if not item["coming"]]


@pytest.fixture
def anna(client: TestClient) -> Iterator[tuple[Account, TestClient]]:
    person = make_account("anna")
    with new_client(person) as browser:
        yield person, browser


def _years(client: TestClient) -> dict[str, int]:
    """An account with events from 2016 to 2036, "Zahnarzt" in title, place and notes."""
    familie = make_calendar(client, "Familie")
    made = {
        "2016": make_event(client, "Zahnarzt Ben", "2016-03-02T09:00", "2016-03-02T09:30", calendar_id=familie),
        "2025": make_event(client, "Kontrolle", "2025-10-14T10:00", "2025-10-14T10:30", calendar_id=familie,
                           location="Zahnarztpraxis Sommer"),
        "today": make_event(client, "Abholen", "2026-11-26T16:30", "2026-11-26T17:00", calendar_id=familie,
                            notes="Vorher zum Zahnarzt"),
        "running": make_event(client, "Zahnarzt Mia", "2026-11-26T16:00", "2026-11-26T16:45",
                              calendar_id=familie),
        "2036": make_event(client, "Zahnarzt Jahrescheck", "2036-01-08T08:00", "2036-01-08T09:00",
                           calendar_id=familie),
        "earlier": make_event(client, "Zahnarzt heute früh", "2026-11-26T08:00", "2026-11-26T08:30",
                              calendar_id=familie),
    }
    make_event(client, "Elternabend", "2026-12-15T19:30", "2026-12-15T21:00", calendar_id=familie)
    return {name: row["id"] for name, row in made.items()} | {"familie": familie}


# --- AC-1, AC-2, AC-5, AC-7, EC-10, EC-12 -------------------------------------------------------------------------


def test_time22_ac1_ac7_title_place_and_notes_from_2016_to_2036(client: TestClient, account: Account) -> None:
    _years(client)
    found = find(client, "zahnarzt")
    assert sorted(titles(found)) == sorted(["Zahnarzt Ben", "Kontrolle", "Abholen", "Zahnarzt Mia",
                                            "Zahnarzt Jahrescheck", "Zahnarzt heute früh"])
    assert "Elternabend" not in titles(found)


def test_time22_ac2_ec10_coming_the_next_on_top_past_the_latest_on_top_running_counts_as_coming(
        client: TestClient, account: Account) -> None:
    _years(client)
    found = find(client, "zahnarzt")
    assert coming(found) == ["Zahnarzt Mia", "Abholen", "Zahnarzt Jahrescheck"]
    assert past(found) == ["Zahnarzt heute früh", "Kontrolle", "Zahnarzt Ben"]
    first = found["items"][0]
    assert first["start"] == "2026-11-26T15:00:00Z" and first["calendar_id"]
    # Another device's clock: an hour later the running one is past.
    later = client.get("/api/search", params={"q": "zahnarzt mia", "now": "2026-11-26T16:05:00Z"}).json()
    assert [item["coming"] for item in later["items"]] == [False]


def test_time22_ac5_the_count_is_all_hits_and_the_page_brings_the_rest(client: TestClient, account: Account) -> None:
    _years(client)
    found = find(client, "zahnarzt", limit=2)
    assert found["total"] == 6 and found["future_total"] == 3 and len(found["items"]) == 2
    rest = find(client, "zahnarzt", offset=2, limit=10)
    assert titles(found) + titles(rest) == coming(find(client, "zahnarzt")) + past(find(client, "zahnarzt"))


def test_time22_ec12_times_are_instants_with_their_zone(client: TestClient, account: Account) -> None:
    make_event(client, "Zahnarzt New York", "2026-12-03T09:00", "2026-12-03T10:00", zone="America/New_York")
    item = find(client, "new york")["items"][0]
    assert item["start"] == "2026-12-03T14:00:00Z" and item["end"] == "2026-12-03T15:00:00Z"
    assert item["time_zone"] == "America/New_York" and item["all_day"] is False


def test_time22_ac7_whole_days_are_dates_and_today_counts_by_the_device_s_day(client: TestClient,
                                                                             account: Account) -> None:
    make_event(client, "Ferientag Kita", "2026-11-26", "2026-11-26", all_day=True)
    item = find(client, "kita")["items"][0]
    assert item["all_day"] is True and item["start"] == "2026-11-26" and item["end"] == "2026-11-26"
    assert item["coming"] is True
    # Shortly after midnight in Berlin the day is over, though in UTC it still runs.
    late = client.get("/api/search", params={"q": "kita", "now": "2026-11-26T23:30:00Z", "zone": ZONE}).json()
    assert late["items"][0]["coming"] is False


# --- EC-1: series --------------------------------------------------------------------------------------------------


def test_time22_ec1_a_series_without_end_comes_once_with_its_next_occurrence(client: TestClient,
                                                                            account: Account) -> None:
    rule = {"freq": "weekly", "weekdays": ["TU"]}
    made = make_event(client, "Zahnspange tragen", "2019-01-01T19:00", "2019-01-01T19:15", rule=rule)
    ended = make_event(client, "Zahnspange alt", "2018-01-02T19:00", "2018-01-02T19:15",
                       rule={"freq": "weekly", "weekdays": ["TU"], "end": "count", "count": 10})
    daily = make_event(client, "Zahnseide", "2016-01-01T21:00", "2016-01-01T21:05", rule={"freq": "daily"})
    found = find(client, "zahn")
    assert found["total"] == 3
    rows = {item["title"]: item for item in found["items"]}
    assert rows["Zahnspange tragen"]["occurrence"] == "20261201T180000Z" and rows["Zahnspange tragen"]["coming"]
    assert rows["Zahnspange tragen"]["start"] == "2026-12-01T18:00:00Z" and rows["Zahnspange tragen"]["recurring"]
    assert rows["Zahnseide"]["start"] == "2026-11-26T20:00:00Z"
    assert rows["Zahnspange alt"]["coming"] is False and rows["Zahnspange alt"]["start"] == "2018-03-06T18:00:00Z"
    assert {made["id"], ended["id"], daily["id"]} == {item["id"] for item in found["items"]}


# --- AC-4, AC-6, EC-6, EC-7, EC-8, EC-15: where it looks -----------------------------------------------------------


def test_time22_ac4_busy_only_calendars_are_neither_searched_nor_shown(
        client: TestClient, account: Account, anna: tuple[Account, TestClient]) -> None:
    _person, browser = anna
    arbeit = make_calendar(browser, "Arbeit")
    familie = make_calendar(browser, "Annas Familie")
    make_event(browser, "Zahnarzt Anna", calendar_id=arbeit, location="Praxis", notes="Zahnarzt")
    make_event(browser, "Zahnarzt Familie", calendar_id=familie)
    share(arbeit, account.id, RIGHT_BUSY)
    share(familie, account.id, RIGHT_READ)
    assert titles(find(client, "zahnarzt")) == ["Zahnarzt Familie"]
    assert titles(find(client, "praxis")) == [] and find(client, "praxis")["total"] == 0
    # Searching for a calendar it sees only as busy finds nothing there either.
    assert find(client, "zahnarzt", calendars=str(arbeit))["total"] == 0


def test_time22_ac6_no_hit_in_the_collection_but_in_all_calendars(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    arbeit = make_calendar(client, "Arbeit")
    make_event(client, "Steuerberater", calendar_id=arbeit)
    assert find(client, "steuerberater", calendars=str(familie))["total"] == 0
    assert titles(find(client, "steuerberater")) == ["Steuerberater"]


def test_time22_ec6_neither_the_trash_nor_hidden_calendars_also_not_in_all_calendars(
        client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    versteckt = make_calendar(client, "Versteckt")
    alt = make_calendar(client, "Alt")
    gone = make_event(client, "Zahnarzt gelöscht", calendar_id=familie)
    make_event(client, "Zahnarzt versteckt", calendar_id=versteckt)
    make_event(client, "Zahnarzt im alten Kalender", calendar_id=alt)
    make_event(client, "Zahnarzt bleibt", calendar_id=familie)
    assert client.delete(f"/api/events/{gone['id']}").status_code == 200
    assert client.patch(f"/api/calendars/{versteckt}", json={"hidden": True}).status_code == 200
    assert client.delete(f"/api/calendars/{alt}").status_code == 200
    assert titles(find(client, "zahnarzt")) == ["Zahnarzt bleibt"]
    assert titles(find(client, "zahnarzt", calendars=f"{versteckt},{alt},{familie}")) == ["Zahnarzt bleibt"]
    # Back from the trash, found again.
    assert client.post(f"/api/trash/events/{gone['id']}/restore").status_code == 200
    assert sorted(titles(find(client, "zahnarzt"))) == ["Zahnarzt bleibt", "Zahnarzt gelöscht"]


def test_time22_ec7_the_same_event_in_two_calendars_comes_once(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    privat = make_calendar(client, "Privat")
    make_event(client, "Zahnarzt Ben", calendar_id=familie)
    make_event(client, "zahnarzt ben ", calendar_id=privat)
    found = find(client, "zahnarzt")
    assert found["total"] == 1 and found["items"][0]["calendar_id"] == familie
    assert client.put("/api/me/display", json={"merge_duplicates": False}).status_code == 200
    assert find(client, "zahnarzt")["total"] == 2


def test_time22_ec7_two_persons_events_never_merge(client: TestClient, account: Account,
                                                  anna: tuple[Account, TestClient]) -> None:
    _person, browser = anna
    familie = make_calendar(browser, "Annas")
    share(familie, account.id, RIGHT_EDIT)
    make_event(browser, "Zahnarzt", calendar_id=familie)
    make_event(client, "Zahnarzt")
    assert find(client, "zahnarzt")["total"] == 2


@pytest.fixture
def remote() -> Iterator[Remote]:
    yield from remote_account()


def test_time22_ec8_a_source_that_does_not_answer_is_searched_as_last_known_with_its_state(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "zahn.ics", event_text("zahn@example.com", "Zahnarzt Team"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    assert find(client, "zahnarzt")["behind"] == []
    SWITCHES.down = True
    with SessionLocal() as db:
        from app.services import sync

        sync.run_source(source, force=True)
        db.commit()
    found = find(client, "zahnarzt")
    assert titles(found) == ["Zahnarzt Team"]
    assert found["behind"] and found["behind"][0]["kind"] == "caldav" and found["behind"][0]["synced_at"]


def test_time22_ec15_a_declined_invitation_is_found_marked_and_counted(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    SWITCHES.schedules = True
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "i0.ics", invitation("plan@x", "Planung Weihnachtsfeier", me="DECLINED"))
    remote.put(arbeit, "i1.ics", invitation("team@x", "Planung Team", me="ACCEPTED"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    found = find(client, "planung")
    assert found["total"] == 2
    assert {item["title"]: item["declined"] for item in found["items"]} == {
        "Planung Weihnachtsfeier": True, "Planung Team": False}


# --- EC-2, EC-3, EC-4, EC-5: the words ---------------------------------------------------------------------------


def test_time22_ec2_upper_and_lower_case_alike_umlauts_as_typed(client: TestClient, account: Account) -> None:
    make_event(client, "KiTa Fest")
    make_event(client, "ÄRZTE-Treffen")
    assert titles(find(client, "Kita")) == ["KiTa Fest"]
    assert titles(find(client, "KITA")) == ["KiTa Fest"]
    assert titles(find(client, "ärzte")) == ["ÄRZTE-Treffen"]
    assert find(client, "arzt")["total"] == 0


def test_time22_ec3_an_empty_question_has_no_list_and_blanks_at_the_edges_do_not_count(
        client: TestClient, account: Account) -> None:
    make_event(client, "Zahnarzt")
    for blank in ("", "   ", "\t \n"):
        found = find(client, blank)
        assert found["total"] == 0 and found["items"] == []
    assert titles(find(client, "  zahnarzt  ")) == ["Zahnarzt"]


def test_time22_ec4_special_characters_emoji_and_quotes_literally(client: TestClient, account: Account) -> None:
    make_event(client, "C++ Kurs")
    make_event(client, "Geburtstag 🎂 Oma")
    make_event(client, 'Party "Neon"')
    make_event(client, "100% Rabatt_Tag")
    make_event(client, "Cpp Kurs")
    assert titles(find(client, " C++ ")) == ["C++ Kurs"]
    assert titles(find(client, "🎂")) == ["Geburtstag 🎂 Oma"]
    assert titles(find(client, '"Neon"')) == ['Party "Neon"']
    assert titles(find(client, "%")) == ["100% Rabatt_Tag"]
    assert titles(find(client, "_")) == ["100% Rabatt_Tag"]


def test_time22_ec5_code_in_a_title_comes_as_it_was_typed(client: TestClient, account: Account) -> None:
    hostile = "<img src=x onerror=alert(1)> Zahnarzt"
    make_event(client, hostile)
    assert titles(find(client, "zahnarzt")) == [hostile]


def test_time22_several_words_must_all_be_there_in_any_field(client: TestClient, account: Account) -> None:
    make_event(client, "Zahnarzt Ben", location="Praxis Sommer")
    make_event(client, "Zahnarzt Mia", location="Praxis Winter")
    assert titles(find(client, "zahnarzt sommer")) == ["Zahnarzt Ben"]
    assert titles(find(client, "sommer ben zahnarzt")) == ["Zahnarzt Ben"]
    assert find(client, "zahnarzt herbst")["total"] == 0


def test_time22_too_many_words_or_a_word_too_long_say_so(client: TestClient, account: Account) -> None:
    many = client.get("/api/search", params={"q": " ".join(["a"] * 21)})
    assert many.status_code == 422 and code(many) == "search_too_many_words"
    long = client.get("/api/search", params={"q": "x" * 201})
    assert long.status_code == 422 and code(long) == "search_word_too_long"
    zone = client.get("/api/search", params={"q": "x", "zone": "Mars/Olympus"})
    assert zone.status_code == 422


# --- The index follows every change; nobody else's ----------------------------------------------------------------


def test_time22_the_index_follows_changes_devices_sources_and_old_rows(
        dav_server: str, client: TestClient, account: Account, remote: Remote) -> None:
    made = make_event(client, "Zahnarzt", notes="Karte")
    etag = client.get(f"/api/events/{made['id']}").json()["etag"]
    assert client.patch(f"/api/events/{made['id']}", json={"title": "Kieferorthopäde", "etag": etag}).status_code == 200
    assert find(client, "zahnarzt")["total"] == 0 and titles(find(client, "kiefer karte")) == ["Kieferorthopäde"]
    # From a device through the calendar account.
    familie = make_calendar(client, "Familie")
    password = device_for(account)
    sent = raw("PUT", calendar_url(account, familie) + "geraet.ics", account, password,
               event_text("geraet@example.com", "Elternabend vom iPhone", extra="LOCATION:Aula\\, Raum 3\r\n"),
               **{"Content-Type": "text/calendar"})
    assert sent.status_code == 201, sent.text
    assert titles(find(client, "aula, raum")) == ["Elternabend vom iPhone"]
    # From a source, and changed there.
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "q.ics", event_text("q@example.com", "Quartalsplanung"))
    source = connected(client, dav_server, remote, ["Arbeit"])
    round_now(source)
    assert titles(find(client, "quartal")) == ["Quartalsplanung"]
    remote.put(arbeit, "q.ics", event_text("q@example.com", "Jahresplanung"))
    round_now(source)
    assert find(client, "quartal")["total"] == 0 and titles(find(client, "jahres")) == ["Jahresplanung"]
    # A row written before the index: found all the same, and filled in at the start.
    with SessionLocal() as db:
        db.execute(update(Event).values(search_text=None))
        db.commit()
    assert titles(find(client, "kiefer")) == ["Kieferorthopäde"]
    with SessionLocal() as db:
        rows = len(db.scalars(select(Event.id)).all())
    assert fill_search_text() == rows and fill_search_text() == 0
    with SessionLocal() as db:
        row = db.get(Event, made["id"])
        assert row is not None and row.search_text == ical.fold_text("Kieferorthopäde\nKarte")


def test_time22_the_alarms_own_texts_are_not_searched(client: TestClient, account: Account) -> None:
    text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART:20261126T100000Z\r\n"
            "SUMMARY:Treffen\r\nDESCRIPTION:Mit\r\n  Kuchen\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Reminder\r\n"
            "TRIGGER:-PT15M\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ical.search_text(text) == "treffen\nmit kuchen"


def test_time22_another_account_s_events_are_never_found(client: TestClient, account: Account,
                                                         anna: tuple[Account, TestClient]) -> None:
    _person, browser = anna
    familie = make_calendar(browser, "Annas")
    make_event(browser, "Geheimer Zahnarzt", calendar_id=familie)
    assert find(client, "zahnarzt")["total"] == 0
    assert find(client, "zahnarzt", calendars=str(familie))["total"] == 0
    with TestClient(client.app, base_url="http://testserver") as nobody:
        assert nobody.get("/api/search", params={"q": "zahnarzt"}).status_code == 401


# --- EC-11: 20,000 events -----------------------------------------------------------------------------------------


def _many(account: Account, calendar_ids: list[int], count: int = 20_000) -> None:
    start = datetime(2016, 1, 4, 8, 0, tzinfo=UTC)
    rows = []
    for number in range(count):
        title = f"Termin {number}" if number % 7 else f"Besprechung {number}"
        begin = start + timedelta(hours=number * 9)
        text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//t//EN\r\nBEGIN:VEVENT\r\n"
                f"UID:m{number}\r\nDTSTAMP:20261120T090000Z\r\nDTSTART:{begin:%Y%m%dT%H%M%SZ}\r\n"
                f"DTEND:{begin + timedelta(hours=1):%Y%m%dT%H%M%SZ}\r\nSUMMARY:{title}\r\n"
                f"LOCATION:Raum {number % 40}\r\nDESCRIPTION:Notiz zu {title}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        rows.append({"calendar_id": calendar_ids[number % len(calendar_ids)], "uid": f"m{number}",
                     "href": f"m{number}.ics", "ical": text, "etag": ical.etag_of(text), "title": title,
                     "starts_at": begin, "ends_at": begin + timedelta(hours=1), "location": f"Raum {number % 40}",
                     "search_text": ical.search_text(text), "created_by": account.id})
    with SessionLocal() as db:
        db.execute(insert(Event), rows)
        db.commit()


@pytest.mark.timing
def test_time22_ec11_twenty_thousand_events_a_single_letter_without_a_wait_and_the_right_count(
        client: TestClient, account: Account) -> None:
    calendars = [make_calendar(client, f"Kalender {number}") for number in range(20)]
    _many(account, calendars)
    for number in range(30):
        make_event(client, f"Serie {number}", "2018-01-01T07:00", "2018-01-01T07:30", calendar_id=calendars[0],
                   rule={"freq": "daily"})
    # The first search after a start (nothing kept), the same one again, and one a minute and a day later: each
    # without a wait (review of TIME-22, finding 1).
    search.forget()
    took = []
    for moment in (NOW, NOW, "2026-11-26T15:06:00Z", "2026-11-27T15:06:00Z"):
        began = time.perf_counter()
        answer = client.get("/api/search", params={"q": "e", "now": moment, "zone": ZONE})
        took.append(time.perf_counter() - began)
        assert answer.status_code == 200
        found = answer.json()
        assert found["total"] == 20_030 and len(found["items"]) == 100
    assert max(took) < 1.0, took
    began = time.perf_counter()
    narrow = find(client, "besprechung")
    assert narrow["total"] == len([n for n in range(20_000) if n % 7 == 0])
    assert time.perf_counter() - began < 1.0
    with SessionLocal() as db:
        assert db.scalar(select(Event.id).where(Event.title == "Termin 1")) is not None


def _occurrences_alike(client: TestClient, monkeypatch: pytest.MonkeyPatch, moments: list[str]) -> None:
    """Every hit the same with the series' start moved on as rolled out from its very first start."""
    for moment in moments:
        search.forget()
        moved = client.get("/api/search", params={"q": "serie", "now": moment, "zone": ZONE}).json()["items"]
        with monkeypatch.context() as patch:
            patch.setattr(search, "_near", lambda parsed, _begin, _first: parsed)
            search.forget()
            plain = client.get("/api/search", params={"q": "serie", "now": moment, "zone": ZONE}).json()["items"]
        assert moved == plain, moment
        assert len(moved) == 5


def test_time22_ec1_ec11_moving_a_series_start_on_changes_no_occurrence(client: TestClient, account: Account,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    daily = make_event(client, "Serie täglich", "2018-03-20T07:00", "2018-03-20T07:30", rule={"freq": "daily"})
    make_event(client, "Serie zwei Wochen", "2017-01-03T18:30", "2017-01-03T20:00",
               rule={"freq": "weekly", "interval": 2, "weekdays": ["TU", "TH"]})
    make_event(client, "Serie New York", "2016-06-01T09:00", "2016-06-01T10:00", zone="America/New_York",
               rule={"freq": "daily", "interval": 3})
    make_event(client, "Serie ganztags", "2015-02-02", "2015-02-02", all_day=True,
               rule={"freq": "weekly", "weekdays": ["MO"]})
    make_event(client, "Serie bis 2026", "2019-01-07T12:00", "2019-01-07T13:00",
               rule={"freq": "weekly", "weekdays": ["MO"], "end": "until", "until": "2026-11-23"})
    # A cancelled and a moved occurrence right where the next one would be.
    assert client.delete(f"/api/events/{daily['id']}", params={"scope": "this", "occurrence": "20261127T060000Z"}
                         ).status_code == 200
    moved = client.patch(f"/api/events/{daily['id']}", json={
        "scope": "this", "occurrence": "20261128T060000Z", "all_day": False, "start": "2026-11-28T09:15",
        "end": "2026-11-28T09:45", "time_zone": "Europe/Berlin",
        "etag": client.get(f"/api/events/{daily['id']}").json()["etag"]})
    assert moved.status_code == 200, moved.text
    _occurrences_alike(client, monkeypatch, ["2026-11-26T15:05:00Z", "2026-11-27T07:00:00Z",
                                             "2027-03-28T00:30:00Z", "2027-10-31T00:30:00Z"])
    found = {item["title"]: item for item in find(client, "serie", now="2026-11-26T15:05:00Z")["items"]}
    assert found["Serie täglich"]["start"] == "2026-11-28T08:15:00Z"


def test_time22_ec1_what_is_kept_of_a_series_follows_the_clock(client: TestClient, account: Account) -> None:
    make_event(client, "Serie", "2026-11-02T17:00", "2026-11-02T18:00", rule={"freq": "weekly", "weekdays": ["MO"]})
    search.forget()

    def next_at(moment: str) -> str:
        return client.get("/api/search", params={"q": "serie", "now": moment, "zone": ZONE}).json()["items"][0]["start"]

    assert next_at("2026-11-26T15:05:00Z") == "2026-11-30T16:00:00Z"
    # Running still counts; over, the next one; an earlier "now" again the one before.
    assert next_at("2026-11-30T16:30:00Z") == "2026-11-30T16:00:00Z"
    assert next_at("2026-11-30T17:01:00Z") == "2026-12-07T16:00:00Z"
    assert next_at("2026-11-26T15:05:00Z") == "2026-11-30T16:00:00Z"
