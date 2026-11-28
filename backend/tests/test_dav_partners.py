"""The tasks of nextasks and the deadlines of nexpaper on the calendar account (asked for while testing, 2026-11-28):
"Meine Aufgaben" and "Meine Fristen" as calendars only to be read, built from the connections at each request, hidden
with the eye in nextime, kept while the other app is away. Both other apps are the stand-ins of ``test_time25`` and
``test_nexpaper``; a real CalDAV client (the library ``caldav``) reads them as Apple Calendar would."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import httpx
import icalendar
import pytest
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import update

from app.db import SessionLocal
from app.models import Account, utcnow
from app.services import dav_partners, nexpaper, nextasks

from . import test_nexpaper as paper
from . import test_time25 as tasks_side
from .test_time20 import connect as dav_connect
from .test_time20 import device_for, raw, server  # noqa: F401 - the threaded server for the caldav client

D, C, CS = "DAV:", "urn:ietf:params:xml:ns:caldav", "http://calendarserver.org/ns/"
LISTING = ('<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/"><d:prop><d:displayname/>'
           '<d:current-user-privilege-set/><cs:getctag/></d:prop></d:propfind>')


@pytest.fixture
def both(monkeypatch: pytest.MonkeyPatch, client: TestClient, account: Account) -> Iterator[dict[str, Any]]:
    """nextasks and nexpaper connected, their clocks on 26.11.2026, the account speaking German."""
    tasks_fake, paper_fake = tasks_side.Fake(), paper.Fake()
    monkeypatch.setattr(nextasks, "TRANSPORT", httpx.MockTransport(tasks_fake))
    monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port: [tasks_side.PUBLIC])
    monkeypatch.setattr(nextasks, "clock", lambda: utcnow().replace(year=2026, month=11, day=26, hour=10, minute=0,
                                                                    second=0, microsecond=0))
    monkeypatch.setattr(nexpaper, "TRANSPORT", httpx.MockTransport(paper_fake))
    monkeypatch.setattr(nexpaper, "RESOLVER", lambda host, port: [paper.PUBLIC])
    monkeypatch.setattr(nexpaper, "clock", lambda: paper.TODAY)
    with SessionLocal() as db:
        db.execute(update(Account).where(Account.id == account.id).values(language="de"))
        db.commit()
    assert tasks_side.connect(client).status_code == 200
    assert paper.connect(client).status_code == 200
    yield {"tasks": tasks_fake, "paper": paper_fake, "password": device_for(account)}


def listing(account: Account, password: str) -> dict[str, dict[str, Any]]:
    """The calendars of the home by their name on the account: display name, writable, collection tag."""
    answer = raw("PROPFIND", f"/dav/calendars/{account.name}/", account, password, LISTING, Depth="1")
    assert answer.status_code == 207, answer.text
    out = {}
    for node in etree.fromstring(answer.content).findall(f"{{{D}}}response"):
        path = node.findtext(f"{{{D}}}href").rstrip("/").rsplit("/", 1)[-1]
        if path == account.name:
            continue
        out[path] = {"name": node.findtext(f".//{{{D}}}displayname"),
                     "writable": node.find(f".//{{{D}}}privilege/{{{D}}}write") is not None,
                     "ctag": node.findtext(f".//{{{CS}}}getctag")}
    return out


def objects(account: Account, password: str, dav_name: str) -> dict[str, icalendar.Event]:
    """The entries of one of the two calendars, by their UID, as a calendar-query brings them."""
    body = ('<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/>'
            '<c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"/>'
            '</c:comp-filter></c:filter></c:calendar-query>')
    answer = raw("REPORT", f"/dav/calendars/{account.name}/{dav_name}/", account, password, body, Depth="1")
    assert answer.status_code == 207, answer.text
    out = {}
    for node in etree.fromstring(answer.content).iter(f"{{{C}}}calendar-data"):
        event = next(iter(icalendar.Calendar.from_ical(node.text).walk("VEVENT")))
        out[str(event["UID"])] = event
    return out


def test_tasks_and_deadlines_come_to_the_device_as_calendars_only_to_be_read(
        client: TestClient, account: Account, both: dict[str, Any]) -> None:
    fake: tasks_side.Fake = both["tasks"]
    fake.add("bin", "Müll rausbringen", "2026-11-27")
    fake.add("doc", "Arzt anrufen", "2026-11-28", "09:00", assignees=[tasks_side.ME])
    fake.add("plants", "Blumen gießen", "2026-11-29", repeat="FREQ=WEEKLY;UNTIL=20270131")
    fake.add("theirs", "Nur für Mara", "2026-11-27", assignees=[tasks_side.OTHER])
    fake.add("done", "Schon erledigt", "2026-11-25")["completed"] = True
    both["paper"].soon = [paper.deadline(7, "2026-12-01", "pay", title="Stromrechnung")]
    both["paper"].later = [paper.deadline(8, "2027-03-31", "cancel", kind="contract_end", status="inbox",
                                          title="Handyvertrag", amount=None)]

    shown = listing(account, both["password"])
    assert shown[dav_partners.TASKS]["name"] == "Meine Aufgaben"
    assert shown[dav_partners.DEADLINES]["name"] == "Meine Fristen"
    assert not shown[dav_partners.TASKS]["writable"] and not shown[dav_partners.DEADLINES]["writable"]

    entries = objects(account, both["password"], dav_partners.TASKS)
    assert set(entries) == {"nextime-nextasks-bin", "nextime-nextasks-doc", "nextime-nextasks-plants"}
    whole = entries["nextime-nextasks-bin"]
    assert whole.decoded("DTSTART") == date(2026, 11, 27) and str(whole["SUMMARY"]) == "Müll rausbringen"
    assert str(whole["TRANSP"]) == "TRANSPARENT" and "tasks.example.com" in str(whole["URL"])
    # A task with a time stands at the time the views show, on the clock of the device (no zone).
    view = next(task for task in tasks_side.tasks(client, "2026-11-30")["tasks"] if task["id"] == "doc")
    timed = entries["nextime-nextasks-doc"].decoded("DTSTART")
    assert isinstance(timed, datetime) and timed.tzinfo is None
    assert timed.strftime("%Y-%m-%d %H:%M") == f"{view['day']} {view['time']}"
    rule = entries["nextime-nextasks-plants"]["RRULE"]
    assert rule["FREQ"] == ["WEEKLY"] and rule["UNTIL"] == [date(2027, 1, 31)]

    deadlines = objects(account, both["password"], dav_partners.DEADLINES)
    pay = deadlines["nextime-nexpaper-7-due"]
    assert str(pay["SUMMARY"]) == "Zahlen: Stromrechnung" and pay.decoded("DTSTART") == date(2026, 12, 1)
    assert "von Stadtwerke" in str(pay["DESCRIPTION"]) and "123,45 EUR" in str(pay["DESCRIPTION"])
    assert str(pay["URL"]) == "https://paper.example.com/dokument/7"
    cancel = deadlines["nextime-nexpaper-8-contract_end"]
    assert str(cancel["SUMMARY"]) == "Kündigen bis: Handyvertrag"
    assert "liegt noch im Eingang" in str(cancel["DESCRIPTION"])


def test_a_real_calendar_client_reads_them_and_cannot_write(server: str, client: TestClient,
                                                            account: Account, both: dict[str, Any]) -> None:
    both["tasks"].add("bin", "Müll rausbringen", "2026-11-27")
    principal = dav_connect(server, account, both["password"]).principal()
    mine = next(item for item in principal.calendars() if item.get_display_name() == "Meine Aufgaben")
    assert [str(item.icalendar_component["SUMMARY"]) for item in mine.events()] == ["Müll rausbringen"]
    base = f"/dav/calendars/{account.name}/{dav_partners.TASKS}/"
    text = mine.events()[0].data
    assert raw("PUT", base + "neu.ics", account, both["password"], text).status_code == 403
    assert raw("DELETE", base + "nextime-nextasks-bin.ics", account, both["password"]).status_code == 403
    assert raw("DELETE", base, account, both["password"]).status_code == 403
    assert raw("MKCALENDAR", f"/dav/calendars/{account.name}/{dav_partners.DEADLINES}/", account,
               both["password"]).status_code == 405
    # The library hands the text on with its own line ends.
    assert raw("GET", base + "nextime-nextasks-bin.ics", account, both["password"]).text.replace("\r\n", "\n") == \
        text.replace("\r\n", "\n")


def test_the_eye_in_nextime_hides_them_on_the_device_and_without_a_connection_there_is_none(
        client: TestClient, account: Account, both: dict[str, Any]) -> None:
    assert client.patch("/api/nexpaper", json={"hidden": True}).status_code == 200
    shown = listing(account, both["password"])
    assert dav_partners.TASKS in shown and dav_partners.DEADLINES not in shown
    assert raw("GET", f"/dav/calendars/{account.name}/{dav_partners.DEADLINES}/x.ics", account,
               both["password"]).status_code == 404
    assert client.delete("/api/nextasks").status_code == 204
    assert dav_partners.TASKS not in listing(account, both["password"])


def test_a_change_moves_the_collection_tag_and_an_app_away_empties_nothing(
        client: TestClient, account: Account, both: dict[str, Any]) -> None:
    fake: tasks_side.Fake = both["tasks"]
    fake.add("bin", "Müll rausbringen", "2026-11-27")
    before = listing(account, both["password"])[dav_partners.TASKS]["ctag"]
    assert listing(account, both["password"])[dav_partners.TASKS]["ctag"] == before
    fake.add("doc", "Arzt anrufen", "2026-11-28")
    nextasks.forget()  # the minute of nextasks' answer is over
    after = listing(account, both["password"])[dav_partners.TASKS]["ctag"]
    assert after != before
    fake.up = False
    nextasks.forget()
    assert listing(account, both["password"])[dav_partners.TASKS]["ctag"] == after
    assert set(objects(account, both["password"], dav_partners.TASKS)) == {"nextime-nextasks-bin",
                                                                           "nextime-nextasks-doc"}


def test_sync_collection_says_nothing_new_or_starts_the_device_over(client: TestClient, account: Account,
                                                                    both: dict[str, Any]) -> None:
    both["tasks"].add("bin", "Müll rausbringen", "2026-11-27")
    path = f"/dav/calendars/{account.name}/{dav_partners.TASKS}/"

    def sync(token: str) -> Any:
        body = (f'<d:sync-collection xmlns:d="DAV:"><d:sync-token>{token}</d:sync-token><d:sync-level>1'
                '</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>')
        return raw("REPORT", path, account, both["password"], body, Depth="1")

    first = etree.fromstring(sync("").content)
    token = first.findtext(f"{{{D}}}sync-token")
    assert len(first.findall(f"{{{D}}}response")) == 1 and token
    again = etree.fromstring(sync(token).content)
    assert again.findall(f"{{{D}}}response") == [] and again.findtext(f"{{{D}}}sync-token") == token
    assert sync("urn:x-nextime:partner:old").status_code == 403


def test_a_hidden_area_a_ticked_task_and_a_series_at_a_time_come_as_in_nextime(
        client: TestClient, account: Account, both: dict[str, Any]) -> None:
    fake: tasks_side.Fake = both["tasks"]
    fake.spaces = [{"id": "s-home", "name": "Zuhause"}, {"id": "s-work", "name": "Arbeit"}]
    fake.lists = [{"id": "l-home", "space_id": "s-home", "space_name": "Zuhause", "name": "Haus",
                   "sections": [{"id": "x1", "name": "Garten"}]},
                  {"id": "l-work", "space_id": "s-work", "space_name": "Arbeit", "name": "Büro", "sections": []}]
    fake.add("lawn", "Rasen mähen", "2026-11-27", "08:00", repeat="FREQ=WEEKLY;UNTIL=20270131").update(
        list_id="l-home", section_id="x1")
    fake.add("bin", "Müll rausbringen", "2026-11-27").update(list_id="l-home")
    fake.add("report", "Bericht", "2026-11-27").update(list_id="l-work")
    assert client.patch("/api/nextasks/areas/s-work", json={"hidden": True}).status_code == 204
    # Ticked off in nextime while nextasks was away: the tick waits, the task is done all the same.
    fake.up = False
    assert tasks_side.tick(client, "bin", True, "ref-bin-1").json()["waiting"] is True
    fake.up = True
    nextasks.forget()
    entries = objects(account, both["password"], dav_partners.TASKS)
    assert set(entries) == {"nextime-nextasks-lawn"}
    lawn = entries["nextime-nextasks-lawn"]
    assert "Zuhause › Haus › Garten" in str(lawn["DESCRIPTION"])
    # A series with a time ends with a time too (RFC 5545), on the last day of nextasks' rule.
    assert lawn["RRULE"]["UNTIL"] == [datetime(2027, 1, 31, 23, 59, 59)]  # noqa: DTZ001 - floating, as the start


def test_on_iphone_and_co_names_them_under_what_comes_along_without_asking_the_other_apps(
        client: TestClient, account: Account, both: dict[str, Any]) -> None:
    asked = len(both["tasks"].calls), len(both["paper"].calls)
    coming = client.get("/api/devices").json()["calendars"]
    assert [(one["id"], one["name"], one["right"]) for one in coming[-2:]] == [
        (dav_partners.TASKS, "Meine Aufgaben", "read"), (dav_partners.DEADLINES, "Meine Fristen", "read")]
    assert (len(both["tasks"].calls), len(both["paper"].calls)) == asked
    client.patch("/api/nextasks", json={"hidden": True})
    assert dav_partners.TASKS not in [one["id"] for one in client.get("/api/devices").json()["calendars"]]
