"""TIME-28 Phone and installable web app: what the installed app keeps for reading without a connection
(``GET /api/offline``). The next four weeks as the views draw them, with notes for the panel and the search without a
connection; never what "busy only" hides, never hidden calendars or the trash; one query for the notes, whatever the
number of events; never kept by the browser's cache; only for the account signed in. The window is fixed in the
week of Thursday, 26 November 2026."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.db import engine
from app.models import RIGHT_BUSY, RIGHT_READ, Account

from .calendar_helpers import code, make_calendar, make_event, share, window
from .conftest import make_account, new_client

START = "2026-11-25T23:00:00Z"
END = "2026-12-24T23:00:00Z"


def snapshot(client: TestClient, start: str = START, end: str = END) -> dict[str, Any]:
    answer = client.get("/api/offline", params={"start": start, "end": end})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_time28_ac3_the_next_four_weeks_come_as_the_views_draw_them_with_notes(client: TestClient,
                                                                                 account: Account) -> None:
    familie = make_calendar(client, "Familie")
    make_event(client, "Zahnarzt", "2026-11-27T09:00", "2026-11-27T09:30", calendar_id=familie,
               location="Praxis Sommer", notes="Karte mitnehmen")
    series = make_event(client, "Training", "2026-11-26T18:30", "2026-11-26T20:00", calendar_id=familie,
                        notes="Halle 2", rule={"freq": "weekly", "weekdays": ["TH"]})
    make_event(client, "Später", "2027-01-15T10:00", "2027-01-15T11:00", calendar_id=familie)
    rows = window(client, START, END)
    one = next(row for row in rows if row["title"] == "Training" and row["start"].startswith("2026-12-03"))
    changed = client.patch(f"/api/events/{series['id']}", json={
        "scope": "this", "occurrence": one["occurrence"], "notes": "Halle 3, nur diesmal"})
    assert changed.status_code == 200, changed.text

    taken = snapshot(client)
    occurrences = taken["occurrences"]
    # The same occurrences as the views, field by field, plus the notes.
    views = window(client, START, END)
    assert [{key: value for key, value in row.items() if key != "notes"} for row in occurrences] == views
    assert [row["title"] for row in occurrences].count("Training") == 5
    assert "Später" not in [row["title"] for row in occurrences]
    dentist = next(row for row in occurrences if row["title"] == "Zahnarzt")
    assert (dentist["location"], dentist["notes"]) == ("Praxis Sommer", "Karte mitnehmen")
    notes = {row["start"][:10]: row["notes"] for row in occurrences if row["title"] == "Training"}
    assert notes["2026-11-26"] == "Halle 2"
    assert notes["2026-12-03"] == "Halle 3, nur diesmal"
    assert taken["start"] == START and taken["end"] == END and taken["taken_at"].endswith("Z")


def test_time28_ac3_busy_only_stays_a_block_without_title_place_or_notes(client: TestClient,
                                                                         account: Account) -> None:
    anna = make_account("anna")
    arbeit = make_calendar(client, "Arbeit")
    make_event(client, "Teamrunde", "2026-11-27T09:00", "2026-11-27T10:00", calendar_id=arbeit,
               location="Raum Nord", notes="Geheime Notiz")
    make_event(client, "Homeoffice", "2026-11-28T09:00", "2026-11-28T17:00", calendar_id=arbeit, busy=False)
    share(arbeit, anna.id, RIGHT_BUSY)
    with new_client(anna) as browser:
        answer = browser.get("/api/offline", params={"start": START, "end": END})
        assert answer.status_code == 200
        text = answer.text
        for secret in ("Teamrunde", "Raum Nord", "Geheime Notiz", "Homeoffice"):
            assert secret not in text
        rows = answer.json()["occurrences"]
        assert [(row["title"], row["location"], row["notes"], row["busy_only"]) for row in rows] == [("", "", "", True)]


def test_time28_ac3_only_calendars_shown_never_hidden_ones_the_trash_or_another_persons(client: TestClient,
                                                                                         account: Account) -> None:
    shown = make_calendar(client, "Familie")
    hidden = make_calendar(client, "Verein")
    gone = make_calendar(client, "Alt")
    make_event(client, "Sichtbar", "2026-11-27T09:00", "2026-11-27T10:00", calendar_id=shown)
    make_event(client, "Versteckt", "2026-11-27T11:00", "2026-11-27T12:00", calendar_id=hidden)
    make_event(client, "Im Papierkorb", "2026-11-27T13:00", "2026-11-27T14:00", calendar_id=gone)
    deleted = make_event(client, "Gelöscht", "2026-11-27T15:00", "2026-11-27T16:00", calendar_id=shown)
    assert client.patch(f"/api/calendars/{hidden}", json={"hidden": True}).status_code == 200
    assert client.delete(f"/api/calendars/{gone}").status_code in (200, 204)
    gone_event = client.delete(f"/api/events/{deleted['id']}")
    assert gone_event.status_code == 200 and gone_event.json()["trash"]["kind"] == "event"
    assert [row["title"] for row in snapshot(client)["occurrences"]] == ["Sichtbar"]
    ben = make_account("ben")
    with new_client(ben) as browser:
        assert snapshot(browser)["occurrences"] == []
    # Shared to read: the member's own snapshot has it, with its notes.
    share(shown, ben.id, RIGHT_READ)
    with new_client(ben) as browser:
        assert [row["title"] for row in snapshot(browser)["occurrences"]] == ["Sichtbar"]


def test_time28_ac3_notes_for_many_events_come_with_one_query(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    for day in range(1, 21):
        make_event(client, f"Termin {day}", f"2026-12-{day:02d}T09:00", f"2026-12-{day:02d}T10:00",
                   calendar_id=familie, notes=f"Notiz {day}")
    statements: list[str] = []

    def count(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT") and "events.ical" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        rows = snapshot(client)["occurrences"]
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(rows) == 20 and all(row["notes"] == f"Notiz {int(row['title'].split()[1])}" for row in rows)
    # The events' texts for the notes: one query, not one per event.
    notes_queries = [text for text in statements if "events.id IN" in text]
    assert len(notes_queries) == 1


def test_time28_ac3_at_most_40_days_and_only_for_someone_signed_in(client: TestClient, account: Account) -> None:
    too_long = client.get("/api/offline", params={"start": "2026-11-01T00:00:00Z", "end": "2026-12-11T00:00:01Z"})
    assert too_long.status_code == 422 and code(too_long) == "invalid_range"
    backwards = client.get("/api/offline", params={"start": END, "end": START})
    assert backwards.status_code == 422
    no_zone = client.get("/api/offline", params={"start": "2026-11-26T00:00:00", "end": END})
    assert no_zone.status_code == 422
    with new_client() as stranger:
        assert stranger.get("/api/offline", params={"start": START, "end": END}).status_code == 401


def test_time28_ac3_the_browser_never_keeps_the_answer_in_its_cache(client: TestClient, account: Account) -> None:
    answer = client.get("/api/offline", params={"start": START, "end": END})
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
