"""What nextime tells nexfolk about a person (verbinden 0.1.13, ``events.about``, ``services/about_person.py``): an
event concerns the person when the person takes part (an attendee's mail address) or its title names the person;
only in the calendars given, never from the trash or a declined invitation, "busy only" without a title, the past
only as ``last``. A connection that has nothing but ``events.about`` opens nothing of ``/api/v1``."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import RIGHT_BUSY, Account
from app.services import events, ical
from app.vendor.nexverbinden import PREFIX

from .calendar_helpers import make_calendar, share
from .conftest import make_account, new_client
from .test_verbinden_host import hosted

PAT = {"name": "Pat Example", "mail": ["pat@example.com"]}


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def put(account_id: int, calendar_id: int, title: str, start: datetime, *, attendees: tuple[str, ...] = (),
        weekly: bool = False, free: bool = False) -> int:
    """An event straight into a calendar (nextime's own calendars take no attendees from the page)."""
    uid = f"{secrets.token_hex(8)}@example.com"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//EN", "BEGIN:VEVENT", f"UID:{uid}",
             "DTSTAMP:20261101T000000Z", f"DTSTART:{_utc(start)}", f"DTEND:{_utc(start + timedelta(hours=1))}",
             f"SUMMARY:{title}"]
    if weekly:
        lines.append("RRULE:FREQ=WEEKLY")
    if free:
        lines.append("TRANSP:TRANSPARENT")
    if attendees:
        lines.append("ORGANIZER:mailto:lead@example.com")
        lines += [f"ATTENDEE;PARTSTAT=ACCEPTED:mailto:{mail}" for mail in attendees]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    with SessionLocal() as db:
        row = events.add_new(db, calendar_id, ical.parse("\r\n".join(lines) + "\r\n"), uid, account_id)
        db.commit()
        return row.id


def ask(token: str, person: dict[str, Any] = PAT) -> dict[str, Any]:
    with TestClient(app, base_url="http://testserver", headers={"Authorization": f"Bearer {token}"}) as nexfolk:
        found = nexfolk.get(f"{PREFIX}/about/events.about", params=person)
    assert found.status_code == 200, found.text
    return found.json()


def _soon(days: int) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)


def test_about_a_person_who_takes_part(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    event_id = put(account.id, familie, "Planung", _soon(10), attendees=("PAT@Example.com", "lead@example.com"))
    put(account.id, familie, "Planung ohne Pat", _soon(11), attendees=("sam@example.com",))
    token = hosted(account.id, "nexfolk", [familie])
    found = ask(token)
    assert found["count"] == 1
    assert found["next"]["title"] == "Planung"
    assert found["next"]["at"] == _soon(10).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert found["last"] is None
    assert found["open"].startswith("/day?day=") and found["open"].endswith(f"&event={event_id}")
    # By mail only (nexfolk sends no name): the same.
    assert ask(token, {"mail": "pat@example.com"})["count"] == 1


def test_about_a_person_named_in_the_title_as_whole_words(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    put(account.id, familie, "Kaffee mit PAT example", _soon(3))
    put(account.id, familie, "Patricia Examples Geburtstag", _soon(4))
    put(account.id, familie, "Pat und Example", _soon(5))
    token = hosted(account.id, "nexfolk", [familie])
    found = ask(token)
    assert found["count"] == 1 and found["next"]["title"] == "Kaffee mit PAT example"
    # Without a name the title is not looked at.
    assert ask(token, {"mail": "pat@example.com"})["count"] == 0


def test_about_a_person_only_in_the_calendars_given(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    arbeit = make_calendar(client, "Arbeit")
    put(account.id, arbeit, "Pat Example zu Besuch", _soon(2), attendees=("pat@example.com",))
    token = hosted(account.id, "nexfolk", [familie])
    assert ask(token) == {"count": 0, "next": None, "last": None, "open": None}
    put(account.id, familie, "Pat Example zu Hause", _soon(4))
    found = ask(token)
    assert found["count"] == 1 and found["next"]["title"] == "Pat Example zu Hause"


def test_about_a_person_never_from_the_trash(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    event_id = put(account.id, familie, "Pat Example zu Besuch", _soon(2))
    past_id = put(account.id, familie, "Mit Pat Example essen", _soon(-3))
    token = hosted(account.id, "nexfolk", [familie])
    assert ask(token)["count"] == 1
    for gone in (event_id, past_id):
        assert client.delete(f"/api/events/{gone}").status_code in (200, 204)
    assert ask(token) == {"count": 0, "next": None, "last": None, "open": None}


def test_about_a_person_busy_only_counts_without_a_title(client: TestClient, account: Account) -> None:
    other = make_account("sam")
    with new_client(other) as sam:
        theirs = make_calendar(sam, "Sams Kalender")
    share(theirs, account.id, RIGHT_BUSY)
    put(other.id, theirs, "Arzt mit Pat Example", _soon(6))
    put(other.id, theirs, "Pat Example frei", _soon(1), free=True)
    found = ask(hosted(account.id, "nexfolk", [theirs]))
    assert found["count"] == 1
    assert found["next"] == {"title": "", "at": _soon(6).strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert "event=" not in found["open"]


def test_about_a_person_the_past_only_as_last(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    put(account.id, familie, "Pat Example zu Besuch", _soon(-20))
    put(account.id, familie, "Wieder Pat Example", _soon(-5))
    found = ask(hosted(account.id, "nexfolk", [familie]))
    assert found["count"] == 0 and found["next"] is None
    assert found["last"] == {"title": "Wieder Pat Example", "at": _soon(-5).strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert found["open"] is not None and found["open"].startswith("/day?day=")


def test_about_a_person_a_series_once_with_its_next_and_last(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    start = _soon(-15)
    put(account.id, familie, "Lauftreff", start, attendees=("pat@example.com",), weekly=True)
    found = ask(hosted(account.id, "nexfolk", [familie]))
    assert found["count"] == 1
    upcoming = datetime.fromisoformat(found["next"]["at"])
    before = datetime.fromisoformat(found["last"]["at"])
    assert before < datetime.now(UTC) < upcoming
    assert upcoming - before == timedelta(days=7) and (upcoming - start).days % 7 == 0
    assert "&occurrence=" in found["open"]


def _invitation(title: str, day: datetime) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example Mail//EN\r\nMETHOD:REQUEST\r\n"
            f"BEGIN:VEVENT\r\nUID:{secrets.token_hex(6)}@example.com\r\nSEQUENCE:0\r\nDTSTAMP:20261120T090000Z\r\n"
            f"DTSTART:{_utc(day)}\r\nDTEND:{_utc(day + timedelta(hours=1))}\r\n"
            f"SUMMARY:{title}\r\nORGANIZER;CN=Pat:mailto:pat@example.com\r\n"
            "ATTENDEE;CN=Kim;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:kim@example.com\r\n"
            "ATTENDEE;CN=Pat;PARTSTAT=ACCEPTED:mailto:pat@example.com\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


def test_about_a_person_not_from_a_declined_invitation(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    with TestClient(app, base_url="http://testserver",
                    headers={"Authorization": f"Bearer {hosted(account.id, 'nexmail', [familie])}"}) as nexmail:
        for title, days, answer in (("Zugesagt", 9, "accepted"), ("Abgesagt", 2, "declined")):
            made = nexmail.post("/api/v1/events/ical", json={"ical": _invitation(title, _soon(days)),
                                                             "calendar_id": familie, "answer": answer,
                                                             "attendee": "kim@example.com"})
            assert made.status_code in (200, 201), made.text
    found = ask(hosted(account.id, "nexfolk", [familie]))
    assert found["count"] == 1 and found["next"]["title"] == "Zugesagt"


def test_a_connection_with_nothing_but_events_about_opens_nothing_of_the_api(client: TestClient,
                                                                              account: Account) -> None:
    familie = make_calendar(client, "Familie")
    for partner in ("nexfolk", "nexpaper"):
        with TestClient(app, base_url="http://testserver",
                        headers={"Authorization": f"Bearer {hosted(account.id, partner, [familie])}"}) as program:
            refused = program.get("/api/v1/calendars")
            assert refused.status_code == 403, (partner, refused.text)
            assert refused.json()["detail"]["code"] == "purpose_missing"
            assert program.get("/api/v1/events", params={"start": "2026-11-01T00:00:00Z",
                                                         "end": "2026-12-01T00:00:00Z"}).status_code == 403
