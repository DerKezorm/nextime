"""TIME-12 EC-9 after the review of stage 5: single events come from their stored columns, without reading the
iCalendar object (``events.QUICK``). Both ways give the very same entries for a mixture of events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import RIGHT_BUSY, RIGHT_READ, Account, Event
from app.services import events, ical

from .calendar_helpers import make_calendar, make_event, share
from .conftest import make_account, new_client


def _stored(calendar_id: int, body: str, by: int | None = None) -> None:
    text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//tests//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uuid.uuid4().hex}\r\nDTSTAMP:20261120T100000Z\r\n{body}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    with SessionLocal() as db:
        parsed = ical.parse(text)
        uid = ical.check(parsed)
        row = Event(calendar_id=calendar_id, uid=uid, href=f"{uid}.ics", ical="", etag="", created_by=by,
                    starts_at=datetime.now(UTC))
        db.add(row)
        events.write(db, row, text, parsed, by)
        db.commit()


def _window(account: Account) -> list[dict]:
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        return events.occurrence_list(db, row, datetime(2026, 11, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))


def test_time12_ec9_single_events_from_their_columns_equal_the_ones_read_from_the_object(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    with new_client(anna) as browser:
        arbeit = make_calendar(browser, "Arbeit")
        garten = make_calendar(browser, "Garten")
        make_event(browser, "Kundentermin", "2026-11-26T14:00", "2026-11-26T16:00", calendar_id=arbeit)
        make_event(browser, "Rasen", "2026-11-28T10:00", "2026-11-28T11:00", calendar_id=garten)
    share(arbeit, account.id, RIGHT_BUSY)
    share(garten, account.id, RIGHT_READ)
    share(familie, anna.id, RIGHT_READ)
    make_event(client, "Training", "2026-11-26T18:30", "2026-11-26T20:00", calendar_id=familie)
    make_event(client, "Training", "2026-11-26T18:30", "2026-11-26T20:00")  # the same in two calendars: merged
    make_event(client, "Flug", "2026-11-27T09:00", "2026-11-27T13:00", zone="Asia/Tokyo")
    make_event(client, "Ferien", "2026-12-21", "2026-12-24", all_day=True)
    make_event(client, "Markt", "2026-11-29", "2026-11-29", all_day=True, busy=True)
    make_event(client, "Lesen", "2026-11-30T20:00", "2026-11-30T21:00", busy=False)
    _stored(familie, "DTSTART;TZID=Europe/Berlin:20261201T170000\r\nDTEND;TZID=Europe/Berlin:20261201T180000\r\n"
                     "SUMMARY:Elternsprechtag\r\nLOCATION:Schule\\, Raum 4\r\nTRANSP:OPAQUE")
    _stored(familie, "DTSTART:20261202T080000\r\nDURATION:PT45M\r\nSUMMARY:Gleitend ohne Zone")
    _stored(familie, "DTSTART:20261202T100000Z\r\nDTEND:20261202T110000Z\r\nSUMMARY: Mit Rand  \r\nLOCATION:  Hof ")
    _stored(familie, "DTSTART;VALUE=DATE:20261203\r\nSUMMARY:Ohne Ende")
    _stored(familie, "DTSTART:20261204T090000Z\r\nDTEND:20261204T090000Z\r\nSUMMARY:Ohne Dauer")
    _stored(familie, "DTSTART;VALUE=DATE:20261205\r\nDTEND;VALUE=DATE:20261205\r\nSUMMARY:Null Tage")
    _stored(familie, "DTSTART:20261031T230000Z\r\nDTEND:20261101T010000Z\r\nSUMMARY:Am Rand")
    _stored(familie, "DTSTART;TZID=Europe/Berlin:20261206T100000\r\nDTEND;TZID=America/New_York:20261206T080000\r\n"
                     "SUMMARY:Zwei Zonen")
    make_event(client, "Laufen", "2026-11-24T07:00", "2026-11-24T08:00",
               rule={"freq": "weekly", "weekdays": ["TU"], "end": "count", "count": 4})
    quick = _window(account)
    monkeypatch.setattr(events, "QUICK", False)
    read = _window(account)
    assert len(quick) >= 15
    assert quick == read
    with new_client(anna) as browser:
        monkeypatch.setattr(events, "QUICK", True)
        quick_anna = _window(anna)
        monkeypatch.setattr(events, "QUICK", False)
        assert quick_anna == _window(anna)
    assert [row["busy_only"] for row in quick_anna if row["calendar_id"] == familie] == [False] * len(
        [row for row in quick_anna if row["calendar_id"] == familie])


def test_time12_ec9_only_series_invitations_and_holidays_are_read_from_the_object(client: TestClient,
                                                                                  account: Account) -> None:
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Training", "2026-11-26T18:30", "2026-11-26T20:00", calendar_id=familie)
    with SessionLocal() as db:
        row = db.get(Event, made["id"])
        assert row is not None
        assert events._plain_event(row, set())
        series = type("Series", (), {"recurring": True, "ical": row.ical})()
        assert not events._plain_event(series, set())  # type: ignore[arg-type]
        holiday = type("Holiday", (), {"recurring": False, "ical": row.ical.replace(
            "SUMMARY:", "X-NEXTIME-SUMMARY-DE:Neujahr\r\nSUMMARY:")})()
        assert not events._plain_event(holiday, set())  # type: ignore[arg-type]
        invited = type("Invited", (), {"recurring": False, "ical": row.ical.replace(
            "SUMMARY:", "ATTENDEE:mailto:tester@example.com\r\nSUMMARY:")})()
        assert not events._plain_event(invited, {"mailto:tester@example.com"})  # type: ignore[arg-type]
        assert events._plain_event(invited, set())  # type: ignore[arg-type]
        changed = type("Changed", (), {"recurring": False, "ical": row.ical.replace(
            "SUMMARY:", "RECURRENCE-ID:20261126T173000Z\r\nSUMMARY:")})()
        assert not events._plain_event(changed, set())  # type: ignore[arg-type]
