"""TIME-12 Day, week, month on the server: dragging an event someone changed meanwhile (the later change counts, the
other hears of it), and twenty calendars with 20,000 events come quickly for a week and a month."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app.db import SessionLocal
from app.models import RIGHT_EDIT, Account, Event
from app.services import ical

from .calendar_helpers import make_calendar, make_event, named, share, window
from .conftest import make_account, new_client


def test_time12_ec7_a_drag_over_a_change_made_meanwhile_counts_and_the_other_hears_of_it(client: TestClient,
                                                                                         account: Account) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    share(familie, anna.id, RIGHT_EDIT)
    made = make_event(client, "Schwimmen", "2026-11-28T11:00", "2026-11-28T12:00", calendar_id=familie)
    seen = window(client)[0]
    with new_client(anna) as browser:
        assert browser.patch(f"/api/events/{made['id']}", json={"title": "Schwimmen mit Oma",
                                                                 "etag": seen["etag"]}).status_code == 200
    # The grid still holds the version from before and drags it to 11:30.
    answer = client.patch(f"/api/events/{made['id']}", json={
        "all_day": False, "start": "2026-11-28T11:30", "end": "2026-11-28T12:30", "time_zone": "Europe/Berlin",
        "etag": seen["etag"], "scope": "all"})
    assert answer.status_code == 200
    assert (answer.json()["start"], answer.json()["title"]) == ("2026-11-28T11:30", "Schwimmen mit Oma")
    with new_client(anna) as browser:
        clash = browser.get(f"/api/events/{made['id']}").json()["conflict"]
    assert clash is not None and clash["by"]["name"] == "Tester"


EVENT = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//nextime//tests//EN\r\nBEGIN:VEVENT\r\nUID:{uid}\r\n"
    "DTSTAMP:20261101T000000Z\r\nDTSTART:{start}\r\nDTEND:{end}\r\nSUMMARY:{title}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def _fill(calendars: list[int], count: int) -> None:
    """``count`` events over two years in ``calendars``, a few each day from 7 to 21 o'clock, every 50th a duplicate
    in the next calendar (TIME-14 merges them, which counts into the time)."""
    begin = datetime(2026, 1, 1, 7, tzinfo=UTC)
    now = datetime.now(UTC)
    rows = []
    for index in range(count):
        start = begin + timedelta(hours=(index * 37) % (730 * 24))
        start = start.replace(hour=7 + index % 14)
        end = start + timedelta(minutes=45)
        uid = f"bulk-{index // 2 if index % 50 == 1 else index}"
        text = EVENT.format(uid=uid, start=start.strftime("%Y%m%dT%H%M%SZ"), end=end.strftime("%Y%m%dT%H%M%SZ"),
                            title=f"Termin {index}")
        rows.append({"calendar_id": calendars[index % len(calendars)], "uid": uid, "href": f"{uid}-{index}.ics",
                     "ical": text, "etag": ical.etag_of(text), "title": f"Termin {index}", "starts_at": start,
                     "ends_at": end, "size": len(text), "created_at": now, "updated_at": now, "busy": True,
                     "time_zone": "UTC", "location": "", "prev_etag": "", "prev_ical": ""})
    with SessionLocal() as db:
        db.execute(insert(Event), rows)
        db.commit()


@pytest.mark.timing
def test_time12_ec9_twenty_calendars_and_20000_events_come_without_a_noticeable_wait(client: TestClient,
                                                                                     account: Account) -> None:
    ids = [named(client, "Personal")["id"]] + [make_calendar(client, f"K{number}") for number in range(19)]
    _fill(ids, 20_000)
    make_event(client, "Training", "2026-01-05T18:30", "2026-01-05T20:00",
               rule={"freq": "weekly", "weekdays": ["MO", "TH"]})
    windows = [
        ("2026-11-23T00:00:00Z", "2026-11-30T00:00:00Z"),  # a week
        ("2026-11-30T00:00:00Z", "2026-12-07T00:00:00Z"),  # the next week
        ("2026-10-26T00:00:00Z", "2026-12-07T00:00:00Z"),  # a month with its six rows
        ("2027-02-01T00:00:00Z", "2027-03-08T00:00:00Z"),  # another month
    ]
    for start, end in windows:
        began = time.perf_counter()
        rows = window(client, start, end)
        took = time.perf_counter() - began
        assert rows, (start, end)
        assert took < 1.0, f"{start}–{end}: {took:.2f} s for {len(rows)} occurrences"
