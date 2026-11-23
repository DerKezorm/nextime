"""TIME-25 nextasks in the calendar: connecting the member's own nextasks (checked at once, the key sealed, never in
the log), the tasks due that are the member's or assigned to it in its zone, ticking off and taking back (also a
repeating task), waiting while nextasks is away (also across a restart), refusals for good with a notice, a task gone,
the address rules (own network only with the operator's switch, never the machine itself), and that nobody else sees
the tasks. The other side is a stand-in of nextasks' ``/api/v1`` behind ``httpx.MockTransport``; the name resolves
to what each test says, nothing reaches the network."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Account, NextasksConnection, TaskTick, utcnow
from app.services import display, logs, nextasks, settings_service

from .conftest import make_account, new_client

TOKEN = "ntk_richtig-und-geheim-1234567890"
ME = {"id": 1, "name": "tester", "display_name": "Tester"}
OTHER = {"id": 2, "name": "mara", "display_name": "Mara"}
PUBLIC = "93.184.216.34"


class Fake:
    """nextasks as far as nextime uses it: ``/me``, ``/tasks`` with its filters and pages, one task, PATCH, DELETE."""

    def __init__(self) -> None:
        self.token = TOKEN
        self.level = "write"
        self.features = ["tasks", "dashboard", "external_ref"]
        self.zone = "Europe/Berlin"
        self.up = True
        self.tasks: dict[str, dict[str, Any]] = {}
        #: Areas (spaces) and lists; none: a nextasks before areas.
        self.spaces: list[dict[str, Any]] | None = None
        self.lists: list[dict[str, Any]] = []
        #: The filters of every listing asked.
        self.asked: list[dict[str, str]] = []
        self.calls: list[tuple[str, str, Any]] = []
        self.counter = 100
        #: Called after a PATCH took effect (a round running meanwhile, a time-out after it arrived).
        self.after_patch: Any = None

    def add(self, task_id: str, title: str, due: str | None, time: str | None = None, *,
            assignees: list[dict[str, Any]] | None = None, repeat: str | None = None,
            link: str | None = None) -> dict[str, Any]:
        row = {"id": task_id, "title": title, "due_date": due, "due_time": time, "repeat": repeat,
               "assignees": assignees or [], "completed": False, "trashed": False,
               "link": link if link is not None else f"https://tasks.example.com/lists/1?task={task_id}"}
        self.tasks[task_id] = row
        return row

    def writes(self) -> list[tuple[str, str, Any]]:
        return [call for call in self.calls if call[0] != "GET"]

    @staticmethod
    def answer(status: int, body: Any = None) -> httpx.Response:
        if body is None:
            return httpx.Response(status)
        return httpx.Response(status, json=body)

    def refuse(self, status: int, code: str) -> httpx.Response:
        return self.answer(status, {"detail": {"code": code, "message": code}})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("nobody there")
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        if request.headers.get("origin"):
            return self.refuse(403, "origin_refused")
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return self.refuse(401, "token_invalid")
        if not path.startswith("/api/v1/") and "/api/v1/" not in path:
            return self.refuse(404, "not_found")
        tail = path.split("/api/v1/", 1)[1]
        if tail == "me":
            return self.answer(200, {"name": "tester", "display_name": "Tester", "level": self.level,
                                     "time_zone": self.zone, "spaces": [], "version": "0.4.3",
                                     "features": self.features})
        if tail in ("spaces", "lists") and self.spaces is not None:
            return self.answer(200, {"spaces": self.spaces} if tail == "spaces" else {"lists": self.lists})
        if tail == "tasks" and request.method == "GET":
            return self.listing(request)
        found = re.fullmatch(r"tasks/([A-Za-z0-9-]+)", tail)
        if not found:
            return self.refuse(404, "not_found")
        task = self.tasks.get(found.group(1))
        if task is None:
            return self.refuse(404, "not_found")
        if request.method == "GET":
            return self.answer(200, task)
        if self.level != "write":
            return self.refuse(403, "token_reads_only")
        if request.method == "DELETE":
            task["trashed"] = True
            return self.answer(204)
        changed = self.change(task, body or {})
        if self.after_patch is not None and request.method == "PATCH":
            self.after_patch()
        return changed

    def listing(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        self.asked.append(dict(params))
        done = params.get("state") == "completed"
        rows = [task for task in self.tasks.values()
                if task["completed"] == done and not task["trashed"] and task["due_date"]
                and params.get("due_after", "") < task["due_date"] < params.get("due_before", "9999-12-31")]
        if params.get("list_id"):
            rows = [task for task in rows if task.get("list_id") == params["list_id"]]
        if params.get("assigned") == "me":
            rows = [task for task in rows if any(person["id"] == ME["id"] for person in task["assignees"])]
        rows.sort(key=lambda task: (task["due_date"], task["due_time"] or ""))
        start = int(params.get("cursor") or 0)
        size = int(params.get("limit") or 100)
        page = rows[start:start + size]
        more = start + size < len(rows)
        return self.answer(200, {"tasks": page, "next_cursor": str(start + size) if more else None,
                                 "as_of": "2026-11-26T10:00:00Z"})

    def change(self, task: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        answer = dict(task)
        if body.get("completed") is True and task["repeat"]:
            self.counter += 1
            copy = {**task, "id": f"copy-{self.counter}", "completed": True, "repeat": None}
            self.tasks[copy["id"]] = copy
            day = (utcnow().date().fromisoformat(task["due_date"]) + timedelta(days=7)).isoformat()
            task["due_date"] = day
            answer = {**task, "next": task["id"], "completed_copy": copy["id"]}
        elif "completed" in body:
            task["completed"] = bool(body["completed"])
            answer = dict(task)
        for name in ("due_date", "due_time"):
            if name in body:
                task[name] = body[name]
                answer[name] = body[name]
        return self.answer(200, answer)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[Fake]:
    made = Fake()
    monkeypatch.setattr(nextasks, "TRANSPORT", httpx.MockTransport(made))
    monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port: [PUBLIC])
    yield made


def connect(client: TestClient, url: str = "https://tasks.example.com", token: str = TOKEN) -> httpx.Response:
    return client.put("/api/nextasks", json={"url": url, "token": token})


def tasks(client: TestClient, to: str = "2026-11-30") -> dict[str, Any]:
    answer = client.get("/api/tasks", params={"to": to})
    assert answer.status_code == 200, answer.text
    return answer.json()


def tick(client: TestClient, task_id: str, done: bool, ref: str) -> httpx.Response:
    return client.post(f"/api/tasks/{task_id}/tick", json={"done": done, "ref": ref, "title": "Müll rausbringen"})


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """nextasks' clock in this file: 26.11.2026 10:00 UTC, moved by ``now["at"]``."""
    now = {"at": utcnow().replace(year=2026, month=11, day=26, hour=10, minute=0, second=0, microsecond=0)}
    monkeypatch.setattr(nextasks, "clock", lambda: now["at"])
    return now


# --- Connecting (AC-1, AC-5, AC-8, EC-4 to EC-6, EC-13) -------------------------------------------------------------


def test_time25_ac1_ec6_connecting_checks_at_once_and_keeps_the_address_without_blanks(
        client: TestClient, account: Account, fake: Fake) -> None:
    answer = connect(client, "  https://tasks.example.com/team/  ")
    assert answer.status_code == 200, answer.text
    shown = answer.json()
    assert shown["connected"] is True and shown["url"] == "https://tasks.example.com/team"
    assert shown["host"] == "tasks.example.com" and shown["level"] == "write" and shown["name"] == "Tester"
    assert fake.calls[0][:2] == ("GET", "/team/api/v1/me")
    assert client.get("/api/nextasks").json()["connected"] is True


def test_time25_ac5_without_a_connection_there_is_nothing(client: TestClient, account: Account, fake: Fake) -> None:
    assert client.get("/api/nextasks").json() == {"connected": False, "local_allowed": False}
    assert tasks(client) == {"connected": False, "tasks": [], "problem": None, "hidden": False, "areas": []}
    assert fake.calls == []


@pytest.mark.parametrize(("url", "code"), [
    ("tasks.example.com", "nextasks_address_invalid"),
    ("ftp://tasks.example.com", "nextasks_address_invalid"),
    ("https://robin:geheim@tasks.example.com", "nextasks_address_invalid"),
    ("https://tasks.example.com/?x=1", "nextasks_address_invalid"),
    ("https://tasks.example.com:22", "nextasks_address_port"),
    ("http://localhost:8510", "nextasks_address_refused"),
])
def test_time25_ec6_a_wrong_address_says_what_does_not_fit_and_keeps_nothing(
        client: TestClient, account: Account, fake: Fake, url: str, code: str) -> None:
    answer = connect(client, url)
    assert answer.status_code == 422 and answer.json()["detail"]["code"] == code
    assert fake.calls == []
    with SessionLocal() as db:
        assert db.scalar(select(NextasksConnection)) is None


def test_time25_ec6_an_address_that_is_not_nextasks_is_refused(client: TestClient, account: Account,
                                                              fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nextasks, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(
        200, text="<html>Willkommen</html>", headers={"content-type": "text/html"})))
    answer = connect(client)
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "nextasks_unreachable"
    with SessionLocal() as db:
        assert db.scalar(select(NextasksConnection)) is None


def test_time25_ec4_a_wrong_key_is_refused_a_reading_key_taken_and_ticking_says_why(
        client: TestClient, account: Account, fake: Fake) -> None:
    answer = connect(client, token="ntk_falsch")
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "nextasks_token_invalid"
    with SessionLocal() as db:
        assert db.scalar(select(NextasksConnection)) is None
    fake.level = "read"
    assert connect(client).json()["level"] == "read"
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    refused = tick(client, "t1", True, "ref-1")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "nextasks_read_only"
    assert fake.tasks["t1"]["completed"] is False and fake.writes() == []
    # The key runs out in nextasks: "Verbindungen" says so.
    fake.token = "ntk_neu"
    assert tasks(client)["problem"] == "nextasks_token_invalid"
    assert client.get("/api/nextasks").json()["problem"] == "nextasks_token_invalid"


def test_time25_ec5_another_api_is_named_when_connecting_and_after_an_update(
        client: TestClient, account: Account, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    fake.features = ["dashboard"]
    answer = connect(client)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "nextasks_too_old"
    fake.features = ["tasks"]
    assert connect(client).status_code == 200
    # After an update of nextasks ``/tasks`` is gone.
    monkeypatch.setattr(Fake, "listing", lambda self, request: self.refuse(404, "not_found"))
    assert tasks(client)["problem"] == "nextasks_too_old"
    assert client.get("/api/nextasks").json()["problem"] == "nextasks_too_old"


def test_time25_ec13_the_key_is_sealed_never_answered_never_logged(client: TestClient, account: Account,
                                                                  fake: Fake) -> None:
    fake.add("t1", "Geheime Aufgabe", "2026-11-26")
    assert connect(client).status_code == 200
    tasks(client)
    with SessionLocal() as db:
        row = db.scalar(select(NextasksConnection))
        assert row is not None and TOKEN not in row.token_sealed and row.token_sealed
    assert TOKEN not in client.get("/api/nextasks").text
    text = logs.log_file().read_text(encoding="utf-8")
    assert "nextasks connected" in text
    assert TOKEN not in text and "Geheime Aufgabe" not in text


def test_time25_ac8_disconnecting_takes_tasks_and_key_away(client: TestClient, account: Account, fake: Fake) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    assert len(tasks(client)["tasks"]) == 1
    assert client.delete("/api/nextasks").status_code == 204
    assert tasks(client)["connected"] is False
    with SessionLocal() as db:
        assert db.scalar(select(NextasksConnection)) is None
    assert fake.writes() == [] and "t1" in fake.tasks


# --- The address (decided while building: like nexnotes and nexmail) ------------------------------------------------


def test_time25_own_network_only_with_the_operators_switch_the_machine_itself_never(
        client: TestClient, account: Account, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port: ["192.168.1.20"])
    member = make_account("mara")
    with new_client(member) as theirs:
        answer = connect(theirs, "https://tasks.home.example")
    # Outside one code for everything on the way: where a name points stays in the operator's log (review).
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "nextasks_unreachable"
    assert fake.calls == []
    assert client.put("/api/settings", json={"connections_local_network": True}).status_code == 200
    assert connect(client, "https://tasks.home.example").status_code == 200
    for address in ("127.0.0.1", "169.254.169.254", "::1"):
        monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port, found=address: [found])
        refused = connect(client, "https://tasks.home.example")
        assert refused.json()["detail"]["code"] == "nextasks_unreachable", address
    # Connected in the own network, the switch turned off: the tasks say only "cannot be reached".
    monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port: ["192.168.1.20"])
    assert client.put("/api/settings", json={"connections_local_network": False}).status_code == 200
    assert tasks(client)["problem"] == "nextasks_unreachable"


def test_time25_every_failure_on_the_way_is_one_code_and_the_details_go_to_the_log(
        client: TestClient, account: Account, fake: Fake) -> None:
    fake.up = False
    answer = connect(client)
    assert answer.status_code == 502
    assert answer.json()["detail"] == {"code": "nextasks_unreachable",
                                       "message": "The nextasks server cannot be reached or gave no usable answer."}
    assert "nextasks gave no usable answer: no connection" in logs.log_file().read_text(encoding="utf-8")


# --- Tasks (AC-2, AC-4, AC-6, EC-1, EC-2, EC-8, EC-9, EC-11, EC-14) -------------------------------------------------


def test_time25_ac2_ac4_own_and_assigned_tasks_with_overdue_ones_never_those_only_for_others(
        client: TestClient, account: Account, fake: Fake) -> None:
    fake.add("own", "Eigene", "2026-11-26")
    fake.add("mine", "Zugewiesen", "2026-11-27", "09:00", assignees=[ME, OTHER])
    fake.add("late", "Überfällig", "2026-11-23")
    fake.add("theirs", "Nur für Mara", "2026-11-26", assignees=[OTHER])
    fake.add("later", "Später", "2026-12-05")
    fake.add("undated", "Ohne Tag", None)
    connect(client)
    found = tasks(client, "2026-11-30")
    assert [task["id"] for task in found["tasks"]] == ["late", "own", "mine"]
    timed = found["tasks"][2]
    assert timed["day"] == "2026-11-27" and timed["time"] is not None and timed["done"] is False
    assert found["tasks"][0]["time"] is None and found["problem"] is None


def test_time25_ec11_times_in_the_members_zone_also_at_midnight(client: TestClient, account: Account,
                                                               fake: Fake) -> None:
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        display.save(db, row, {"time_zone": "America/New_York"})
    fake.add("morning", "Früh in Berlin", "2026-11-27", "03:00")
    fake.add("midnight", "Mitternacht in Berlin", "2026-11-27", "00:00")
    fake.add("day", "Ganzer Tag", "2026-11-27")
    connect(client)
    shown = {task["id"]: (task["day"], task["time"]) for task in tasks(client)["tasks"]}
    assert shown == {"morning": ("2026-11-26", "21:00"), "midnight": ("2026-11-26", "18:00"),
                     "day": ("2026-11-27", None)}


def test_time25_ec9_titles_are_text_shortened_and_links_only_http(client: TestClient, account: Account,
                                                                  fake: Fake) -> None:
    fake.add("x", "<script>alert(1)</script>  " + "lang " * 200, "2026-11-26", link="javascript:alert(1)")
    connect(client)
    task = tasks(client)["tasks"][0]
    assert task["title"].startswith("<script>alert(1)</script> lang")
    assert len(task["title"]) == nextasks.MAX_TITLE and task["link"] == ""


def test_time25_ec8_two_hundred_overdue_tasks_all_come_over_pages(client: TestClient, account: Account,
                                                                  fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nextasks, "PAGE", 50)
    for number in range(200):
        fake.add(f"t{number}", f"Aufgabe {number}", "2026-11-01")
    connect(client)
    assert len(tasks(client)["tasks"]) == 200


def test_time25_ec1_unreachable_gives_no_tasks_and_a_quiet_problem_events_untouched(
        client: TestClient, account: Account, fake: Fake) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    fake.up = False
    found = tasks(client)
    assert found["tasks"] == [] and found["problem"] == "nextasks_unreachable" and found["connected"] is True
    events = client.get("/api/events", params={"start": "2026-11-23T00:00:00Z", "end": "2026-11-30T00:00:00Z"})
    assert events.status_code == 200


def test_time25_ec2_a_change_in_nextasks_shows_after_a_short_while(client: TestClient, account: Account,
                                                                  fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    fake.add("t1", "Alt", "2026-11-26")
    connect(client)
    assert tasks(client)["tasks"][0]["title"] == "Alt"
    fake.tasks["t1"]["title"] = "Neu"
    fake.add("t2", "Dazu", "2026-11-26")
    asked = len(fake.calls)
    assert tasks(client)["tasks"][0]["title"] == "Alt" and len(fake.calls) == asked
    monkeypatch.setattr(nextasks, "CACHE_SECONDS", 0)
    assert [task["title"] for task in tasks(client)["tasks"]] == ["Dazu", "Neu"]
    fake.tasks["t2"]["completed"] = True
    del fake.tasks["t1"]
    assert tasks(client)["tasks"] == []


def test_time25_ec14_nobody_else_sees_the_tasks(client: TestClient, account: Account, fake: Fake) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    other = make_account("mara")
    with new_client(other) as theirs:
        assert theirs.get("/api/tasks", params={"to": "2026-11-30"}).json()["tasks"] == []
        assert theirs.get("/api/nextasks").json()["connected"] is False
        refused = theirs.post("/api/tasks/t1/tick", json={"done": True, "ref": "r1"})
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "nextasks_not_connected"
    assert fake.writes() == []


def test_time25_ac7_my_tasks_hide_like_a_calendar_and_go_into_collections(client: TestClient, account: Account,
                                                                         fake: Fake) -> None:
    connect(client)
    assert client.patch("/api/nextasks", json={"hidden": True}).json()["hidden"] is True
    assert tasks(client)["hidden"] is True
    made = client.post("/api/collections", json={"name": "Alltag", "calendar_ids": [], "with_tasks": True})
    assert made.status_code == 201, made.text
    listed = client.get("/api/collections").json()["collections"]
    assert listed[0]["with_tasks"] is True
    client.patch(f"/api/collections/{listed[0]['id']}", json={"with_tasks": False})
    assert client.get("/api/collections").json()["collections"][0]["with_tasks"] is False


# --- Ticking (AC-3, AC-9, EC-3, EC-7, EC-10, EC-12) -----------------------------------------------------------------


def test_time25_ac3_ac9_ticking_off_and_taking_back_reach_nextasks(client: TestClient, account: Account,
                                                                  fake: Fake) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    tasks(client)
    done = tick(client, "t1", True, "ref-1")
    assert done.status_code == 200 and done.json()["state"] == "sent"
    assert fake.tasks["t1"]["completed"] is True
    assert fake.writes()[-1] == ("PATCH", "/api/v1/tasks/t1", {"completed": True})
    # Until it reloads the task stays, struck through (the page keeps it); taken back it is open again.
    back = tick(client, "t1", False, "ref-2")
    assert back.json()["state"] == "sent" and fake.tasks["t1"]["completed"] is False
    assert fake.writes()[-1] == ("PATCH", "/api/v1/tasks/t1", {"completed": False})


def test_time25_ac9_taking_back_a_repeating_task_brings_its_day_back_and_trashes_the_copy(
        client: TestClient, account: Account, fake: Fake) -> None:
    fake.add("r1", "Pflanzen gießen", "2026-11-26", "08:00", repeat="weekly")
    connect(client)
    tick(client, "r1", True, "ref-1")
    assert fake.tasks["r1"]["due_date"] == "2026-12-03"
    copy = next(task for task in fake.tasks.values() if task["id"].startswith("copy-"))
    tick(client, "r1", False, "ref-2")
    assert fake.tasks["r1"]["due_date"] == "2026-11-26" and fake.tasks["r1"]["due_time"] == "08:00"
    assert fake.tasks["r1"]["repeat"] == "weekly" and fake.tasks[copy["id"]]["trashed"] is True


def test_time25_ec10_a_double_click_is_one_tap(client: TestClient, account: Account, fake: Fake) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    first = tick(client, "t1", True, "ref-same")
    second = tick(client, "t1", True, "ref-same")
    assert first.json()["id"] == second.json()["id"]
    assert [call for call in fake.writes() if call[0] == "PATCH"] == [("PATCH", "/api/v1/tasks/t1",
                                                                       {"completed": True})]


def test_time25_ec3_a_tick_waits_while_nextasks_is_away_and_goes_later(client: TestClient, account: Account,
                                                                      fake: Fake, clock: dict[str, Any]) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    fake.up = False
    waiting = tick(client, "t1", True, "ref-1").json()
    assert waiting["state"] == "waiting" and waiting["waiting"] is True
    fake.up = True
    shown = tasks(client)["tasks"][0]
    assert shown["done"] is True and shown["waiting"] is True
    with SessionLocal() as db:
        assert nextasks.run_waiting(db) == 0
    clock["at"] += timedelta(seconds=nextasks.RETRY_SECONDS)
    with SessionLocal() as db:
        assert nextasks.run_waiting(db) == 1
    assert fake.tasks["t1"]["completed"] is True


def test_time25_ec3_taken_back_while_it_waits_nothing_goes(client: TestClient, account: Account, fake: Fake,
                                                          clock: dict[str, Any]) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    fake.up = False
    tick(client, "t1", True, "ref-1")
    back = tick(client, "t1", False, "ref-2").json()
    assert back["state"] == "dropped"
    fake.up = True
    fake.calls.clear()
    clock["at"] += timedelta(minutes=5)
    with SessionLocal() as db:
        assert nextasks.run_waiting(db) == 0
    assert fake.writes() == [] and fake.tasks["t1"]["completed"] is False


def test_time25_ec3_a_refusal_for_good_ends_waiting_with_one_notice(client: TestClient, account: Account,
                                                                    fake: Fake, clock: dict[str, Any]) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    fake.add("t2", "Anderes", "2026-11-26")
    connect(client)
    fake.up = False
    tick(client, "t1", True, "ref-1")
    del fake.tasks["t1"]
    fake.up = True
    clock["at"] += timedelta(minutes=1)
    with SessionLocal() as db:
        nextasks.run_waiting(db)
        row = db.scalar(select(TaskTick).where(TaskTick.ref == "ref-1"))
        assert row is not None and row.state == "failed" and row.problem == "nextasks_not_found"
    notices = tasks(client)["notices"]
    assert notices == [{"id": row.id, "title": "Müll rausbringen", "problem": "nextasks_not_found"}]
    assert client.post(f"/api/tasks/notices/{row.id}/seen").status_code == 204
    assert tasks(client)["notices"] == []


def test_time25_ec12_a_waiting_tick_survives_a_restart(client: TestClient, account: Account, fake: Fake,
                                                      clock: dict[str, Any]) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    fake.up = False
    tick(client, "t1", True, "ref-1")
    # A restart: nothing of the process is left but the database.
    nextasks.forget()
    fake.up = True
    clock["at"] += timedelta(minutes=1)
    assert nextasks.run_round() == 1
    assert fake.tasks["t1"]["completed"] is True
    with SessionLocal() as db:
        assert db.scalar(select(TaskTick)).state == "sent"


def test_time25_ec7_a_task_gone_in_nextasks_says_so_on_opening(client: TestClient, account: Account,
                                                                fake: Fake) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    fake.add("t2", "Im Papierkorb", "2026-11-26")
    connect(client)
    assert client.get("/api/tasks/t1/open").json() == {
        "gone": False, "link": "https://tasks.example.com/lists/1?task=t1"}
    del fake.tasks["t1"]
    fake.tasks["t2"]["trashed"] = True
    assert client.get("/api/tasks/t1/open").json() == {"gone": True, "link": ""}
    assert client.get("/api/tasks/t2/open").json() == {"gone": True, "link": ""}


def test_time25_no_origin_goes_to_nextasks_and_the_brake_holds(client: TestClient, account: Account, fake: Fake,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nextasks, "CONNECTS_PER_MINUTE", 2)
    assert connect(client).status_code == 200
    assert connect(client).status_code == 200
    braked = connect(client)
    assert braked.status_code == 429 and braked.json()["detail"]["code"] == "slow_down"
    assert braked.headers["Retry-After"]


def test_time25_the_operators_switch_is_off_from_the_start(client: TestClient, account: Account) -> None:
    with SessionLocal() as db:
        assert settings_service.get(db, "connections_local_network") is False
    assert client.get("/api/settings").json()["connections_local_network"] is False


def test_time25_ec2_one_answer_of_nextasks_serves_every_view_within_the_minute(
        client: TestClient, account: Account, fake: Fake) -> None:
    fake.add("t1", "Heute", "2026-11-26")
    fake.add("t2", "Freitag", "2026-11-27")
    connect(client)
    assert [task["id"] for task in tasks(client, "2026-11-30")["tasks"]] == ["t1", "t2"]
    asked = len(fake.calls)
    # The sidebar asks for today only: from what came a moment ago, nothing new to nextasks.
    assert [task["id"] for task in tasks(client, "2026-11-26")["tasks"]] == ["t1"]
    assert len(fake.calls) == asked
    # A later day than nextasks was asked for (some months ahead) goes there again.
    tasks(client, "2027-06-07")
    assert len(fake.calls) > asked


# --- After the review (2026-11-23) -------------------------------------------------------------------------------------


def test_time25_ac9_ec10_ec12_a_round_at_the_moment_of_a_tap_sends_one_tick(client: TestClient, account: Account,
                                                                            fake: Fake,
                                                                            clock: dict[str, Any]) -> None:
    """A weekly task ticked while the round of 30 s runs: one PATCH, a week on, one copy; taken back, it is back."""
    fake.add("r1", "Pflanzen gießen", "2026-11-26", "08:00", repeat="weekly")
    connect(client)
    rounds: list[int] = []

    def round_meanwhile() -> None:
        if not rounds:
            # The round starts while the PATCH is on its way, a minute later on the clock.
            clock["at"] += timedelta(minutes=1)
            rounds.append(nextasks.run_round())

    fake.after_patch = round_meanwhile
    assert tick(client, "r1", True, "ref-1").json()["state"] == "sent"
    fake.after_patch = None
    patches = [call for call in fake.writes() if call[0] == "PATCH"]
    assert patches == [("PATCH", "/api/v1/tasks/r1", {"completed": True})] and rounds == [0]
    assert fake.tasks["r1"]["due_date"] == "2026-12-03"
    copies = [task for task in fake.tasks.values() if task["id"].startswith("copy-")]
    assert len(copies) == 1
    assert tick(client, "r1", False, "ref-2").json()["state"] == "sent"
    assert fake.tasks["r1"]["due_date"] == "2026-11-26" and fake.tasks[copies[0]["id"]]["trashed"] is True


def test_time25_ec12_a_time_out_after_the_patch_arrived_sends_it_not_again(client: TestClient, account: Account,
                                                                          fake: Fake,
                                                                          clock: dict[str, Any]) -> None:
    fake.add("r1", "Pflanzen gießen", "2026-11-26", repeat="weekly")
    connect(client)

    def lost_answer() -> None:
        raise httpx.ReadTimeout("the answer did not come back")

    fake.after_patch = lost_answer
    assert tick(client, "r1", True, "ref-1").json()["state"] == "waiting"
    fake.after_patch = None
    assert fake.tasks["r1"]["due_date"] == "2026-12-03"
    clock["at"] += timedelta(minutes=1)
    assert nextasks.run_round() == 1
    assert [call for call in fake.writes() if call[0] == "PATCH"] == [("PATCH", "/api/v1/tasks/r1",
                                                                       {"completed": True})]
    assert fake.tasks["r1"]["due_date"] == "2026-12-03"
    with SessionLocal() as db:
        assert db.scalar(select(TaskTick).where(TaskTick.ref == "ref-1")).state == "sent"
    # Taken back: its day comes back (the copy of the lost answer is not known and stays in nextasks).
    tick(client, "r1", False, "ref-2")
    assert fake.tasks["r1"]["due_date"] == "2026-11-26"


def test_time25_ec12_a_claim_left_by_a_stopped_process_is_taken_up_again(client: TestClient, account: Account,
                                                                         fake: Fake,
                                                                         clock: dict[str, Any]) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    with SessionLocal() as db:
        db.add(TaskTick(account_id=account.id, task_id="t1", ref="ref-left", done=True, state="sending",
                        next_at=clock["at"] + timedelta(seconds=nextasks.LEASE_SECONDS)))
        db.commit()
    assert nextasks.run_round() == 0
    clock["at"] += timedelta(seconds=nextasks.LEASE_SECONDS + 1)
    assert nextasks.run_round() == 1
    assert fake.tasks["t1"]["completed"] is True


def test_time25_ec6_another_nextasks_drops_ticks_waiting_for_the_old_one(client: TestClient, account: Account,
                                                                        fake: Fake,
                                                                        clock: dict[str, Any]) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    fake.up = False
    tick(client, "t1", True, "ref-1")
    fake.up = True
    assert connect(client, "https://other.example.com").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(TaskTick)) is None
    # The same address again keeps what waits.
    fake.up = False
    tick(client, "t1", True, "ref-2")
    fake.up = True
    connect(client, "https://other.example.com")
    with SessionLocal() as db:
        assert db.scalar(select(TaskTick)).ref == "ref-2"


@pytest.mark.parametrize("status", [403, 404, 500])
def test_time25_ec6_connecting_to_something_else_says_only_cannot_be_reached(
        client: TestClient, account: Account, fake: Fake, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setattr(nextasks, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(
        status, json={"detail": "nope"})))
    answer = connect(client)
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "nextasks_unreachable"


def test_time25_ec6_a_second_address_never_asked_or_a_redirect_stops_everything(
        client: TestClient, account: Account, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port: [PUBLIC, "127.0.0.1"])
    answer = connect(client)
    assert answer.json()["detail"]["code"] == "nextasks_unreachable" and fake.calls == []
    monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port: [PUBLIC])
    asked: list[str] = []

    def moved(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    monkeypatch.setattr(nextasks, "TRANSPORT", httpx.MockTransport(moved))
    answer = connect(client)
    assert answer.json()["detail"]["code"] == "nextasks_unreachable"
    assert len(asked) == 1 and "169.254" not in asked[0]


def test_time25_ec12_a_round_sends_only_what_it_claimed(client: TestClient, account: Account, fake: Fake,
                                                       clock: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    fake.add("t1", "Müll rausbringen", "2026-11-26")
    connect(client)
    fake.up = False
    tick(client, "t1", True, "ref-1")
    fake.up = True
    clock["at"] += timedelta(minutes=1)
    with SessionLocal() as db:
        tick_id = db.scalar(select(TaskTick.id))
        # Two rounds at once: only the first claim holds.
        assert nextasks.claim(db, tick_id) is True
        assert nextasks.claim(db, tick_id) is False
    monkeypatch.setattr(nextasks, "claim", lambda db, tick_id: False)
    clock["at"] += timedelta(seconds=nextasks.LEASE_SECONDS + 1)
    assert nextasks.run_round() == 0
    assert fake.writes() == []


@pytest.mark.parametrize("addresses", [["10.0.0.5"], ["10.0.0.5", "192.168.1.20"]])
def test_time25_ec6_operator_hint_only_the_switch_stands_in_the_way(client: TestClient, account: Account,
                                                                    fake: Fake, monkeypatch: pytest.MonkeyPatch,
                                                                    addresses: list[str]) -> None:
    """The operator hears that the switch for the own network is all that is missing; a member hears "cannot be
    reached" as for anything on the way; an address never asked (the machine itself, metadata) stays general for
    the operator too."""
    monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port: addresses)
    told = connect(client, "https://nextasks.home.example")
    assert told.status_code == 422 and told.json()["detail"]["code"] == "nextasks_address_local"
    assert "Connections into the local network" in told.json()["detail"]["message"]
    member = make_account("mara")
    with new_client(member) as theirs:
        general = connect(theirs, "https://nextasks.home.example")
    assert general.status_code == 502 and general.json()["detail"]["code"] == "nextasks_unreachable"
    for never in (["10.0.0.5", "127.0.0.1"], ["10.0.0.5", "169.254.169.254"]):
        monkeypatch.setattr(nextasks, "RESOLVER", lambda host, port, found=never: found)
        refused = connect(client, "https://nextasks.home.example")
        assert refused.json()["detail"]["code"] == "nextasks_unreachable", never
    assert fake.calls == []


# --- Asked for while testing (2026-11-23): later, repeating, assigned, areas -------------------------------------------


def window(client: TestClient, start: str, end: str, previews: bool = False) -> list[dict[str, Any]]:
    answer = client.get("/api/tasks", params={"from": start, "to": end, "previews": str(previews).lower()})
    assert answer.status_code == 200, answer.text
    return answer.json()["tasks"]


def test_time25_later_a_task_months_ahead_shows_in_the_month_shown(client: TestClient, account: Account,
                                                                   fake: Fake) -> None:
    fake.add("far", "Steuererklärung", "2027-03-15")
    connect(client)
    assert [task["id"] for task in window(client, "2027-03-01", "2027-04-11")] == ["far"]
    assert tasks(client, "2026-11-30")["tasks"] == []


def test_time25_repeating_previews_on_their_coming_days_lists_once(client: TestClient, account: Account,
                                                                    fake: Fake) -> None:
    fake.add("r1", "Pflanzen gießen", "2026-12-01", repeat="FREQ=WEEKLY;BYDAY=TU")
    connect(client)
    shown = window(client, "2026-11-30", "2026-12-20", previews=True)
    assert [(task["day"], task["preview"]) for task in shown] == [
        ("2026-12-01", False), ("2026-12-08", True), ("2026-12-15", True)]
    assert shown[0]["repeat"] == "FREQ=WEEKLY;BYDAY=TU"
    assert [task["day"] for task in window(client, "2026-11-30", "2026-12-20")] == ["2026-12-01"]


def test_time25_assigned_own_and_assigned_to_me_subtasks_too(client: TestClient, account: Account,
                                                             fake: Fake) -> None:
    fake.add("own", "Eigene", "2026-11-26")
    fake.add("mine", "Zugewiesen", "2026-11-26", assignees=[ME, OTHER])
    fake.add("theirs", "Nur für Mara", "2026-11-26", assignees=[OTHER])
    connect(client)
    found = tasks(client)["tasks"]
    assert [task["id"] for task in found] == ["own", "mine"]
    assert found[1]["assignees"] == [ME["display_name"], OTHER["display_name"]]
    assert all(asked.get("subtasks") == "true" for asked in fake.asked)
    assert any(asked.get("assigned") == "me" for asked in fake.asked)


def test_time25_areas_lists_whole_areas_and_single_lists_eye_collections_move(client: TestClient, account: Account,
                                                                              fake: Fake) -> None:
    fake.spaces = [{"id": "s-home", "name": "Zuhause", "icon": "home"}, {"id": "s-work", "name": "Arbeit"}]
    fake.lists = [{"id": "l-home", "space_id": "s-home", "space_name": "Zuhause", "name": "Haus",
                   "sections": [{"id": "x1", "name": "Garten"}]},
                  {"id": "l-one", "space_id": "s-work", "space_name": "Arbeit", "name": "Büro", "sections": []},
                  {"id": "l-two", "space_id": "s-work", "space_name": "Arbeit", "name": "Reisen", "sections": []}]
    fake.add("h", "Rasen mähen", "2026-11-26").update(list_id="l-home", section_id="x1")
    fake.add("w1", "Bericht", "2026-11-26").update(list_id="l-one")
    fake.add("w2", "Flug", "2026-11-26").update(list_id="l-two")
    connect(client)
    tree = client.get("/api/nextasks/areas").json()["areas"]
    assert [(area["name"], area["whole"], [one["chosen"] for one in area["lists"]]) for area in tree] == [
        ("Zuhause", True, [True]), ("Arbeit", True, [True, True])]
    found = tasks(client)
    assert {task["id"]: (task["area_name"], task["section"], task["row"]) for task in found["tasks"]} == {
        "h": ("Zuhause", "Garten", "s-home"), "w1": ("Arbeit", "", "s-work"), "w2": ("Arbeit", "", "s-work")}
    assert client.put("/api/nextasks/areas", json={"areas": ["s-home"], "lists": ["l-one"]}).status_code == 200
    found = tasks(client)
    assert [task["id"] for task in found["tasks"]] == ["w1", "h"] or [task["id"] for task in found["tasks"]] == [
        "h", "w1"]
    assert [(row["kind"], row["name"], row["area_name"]) for row in found["areas"]] == [
        ("area", "Zuhause", ""), ("list", "Büro", "Arbeit")]
    assert found["areas"][1]["link"] == "https://tasks.example.com/lists/l-one"
    # TIME-25 area rows: the eye hides one row; a collection from before areas holds every row.
    assert client.patch("/api/nextasks/areas/l-one", json={"hidden": True}).status_code == 204
    assert tasks(client)["areas"][1]["hidden"] is True
    client.post("/api/collections", json={"name": "Alltag", "calendar_ids": [], "with_tasks": True})
    made = client.get("/api/collections").json()["collections"][0]
    assert made["task_areas"] == ["*"]
    client.patch(f"/api/collections/{made['id']}", json={"task_areas": ["l-one"]})
    changed = client.get("/api/collections").json()["collections"][0]
    assert changed["task_areas"] == ["l-one"] and changed["with_tasks"] is True
    # Areas switched off before lists could be chosen move once at the start: every area but those.
    with SessionLocal() as db:
        db.execute(update(NextasksConnection).values(task_choice="", areas_off=json.dumps(["s-work"])))
        db.commit()
        assert nextasks.move_choices(db) == 1
    tree = client.get("/api/nextasks/areas").json()["areas"]
    assert [(area["id"], area["whole"]) for area in tree] == [("s-home", True), ("s-work", False)]


def test_time25_done_tasks_stay_struck_through_in_the_views_a_copy_taken_back_on_its_task(
        client: TestClient, account: Account, fake: Fake) -> None:
    fake.add("r1", "Bänder", "2026-11-24", repeat="FREQ=WEEKLY")
    fake.add("d1", "Erledigt schon", "2026-11-25")["completed"] = True
    connect(client)
    assert tick(client, "r1", True, "x1").json()["state"] == "sent"
    shown = window(client, "2026-11-23", "2026-12-06", previews=True)
    copy = next(task for task in shown if task["id"].startswith("copy-"))
    assert (copy["day"], copy["done"]) == ("2026-11-24", True)
    assert [(task["id"], task["done"]) for task in shown if task["id"] in ("r1", "d1")] == [
        ("d1", True), ("r1", False)]
    assert all(task["id"] != "d1" for task in tasks(client, "2026-11-30")["tasks"])
    assert tick(client, copy["id"], False, "x2").json()["state"] == "sent"
    assert fake.tasks["r1"]["due_date"] == "2026-11-24" and fake.tasks[copy["id"]]["trashed"] is True


def test_time25_area_rows_an_area_in_part_with_two_lists_and_one_whole_are_three_rows(
        client: TestClient, account: Account, fake: Fake) -> None:
    fake.spaces = [{"id": "s-private", "name": "Privat"}, {"id": "s-family", "name": "Familie"}]
    fake.lists = [{"id": "l-general", "space_id": "s-private", "name": "Allgemein", "icon": "inbox", "color": "teal"},
                  {"id": "l-ha", "space_id": "s-private", "name": "HA Wartung"},
                  {"id": "l-work", "space_id": "s-private", "name": "Arbeit; ZA31"},
                  {"id": "l-shop", "space_id": "s-family", "name": "Einkauf"}]
    for task_id, list_id in (("g", "l-general"), ("h", "l-ha"), ("w", "l-work"), ("s", "l-shop")):
        fake.add(task_id, task_id, "2026-11-26").update(list_id=list_id)
    connect(client)
    client.put("/api/nextasks/areas", json={"areas": ["s-family"], "lists": ["l-general", "l-ha"]})
    found = tasks(client)
    assert [(row["name"], row["area_name"]) for row in found["areas"]] == [
        ("Allgemein", "Privat"), ("HA Wartung", "Privat"), ("Familie", "")]
    assert (found["areas"][0]["icon"], found["areas"][0]["color"]) == ("inbox", "teal")
    assert sorted((task["id"], task["row"]) for task in found["tasks"]) == [
        ("g", "l-general"), ("h", "l-ha"), ("s", "s-family")]


@pytest.mark.parametrize(("now_due", "one_on"), [("2026-12-01", True), ("2026-12-08", False)])
def test_time25_done_a_copy_ticked_in_nextasks_taken_back_one_repetition_on_or_further(
        client: TestClient, account: Account, fake: Fake, now_due: str, one_on: bool) -> None:
    fake.add("r1", "Bänder", now_due, repeat="FREQ=WEEKLY").update(list_id="l1")
    copy = fake.add("c1", "Bänder", "2026-11-24")
    copy.update(list_id="l1", completed=True)
    connect(client)
    answer = tick(client, "c1", False, "u1").json()
    assert answer["state"] == "sent" and answer["moved_on"] is (not one_on)
    if one_on:
        assert fake.tasks["r1"]["due_date"] == "2026-11-24" and fake.tasks["c1"]["trashed"] is True
    else:
        assert fake.tasks["r1"]["due_date"] == now_due and fake.tasks["c1"]["completed"] is False
