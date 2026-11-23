"""Small helpers for the tests of calendars, events and the calendar account."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import RIGHT_EDIT
from app.services import shares


def calendars_of(client: TestClient) -> dict[str, Any]:
    answer = client.get("/api/calendars")
    assert answer.status_code == 200, answer.text
    return answer.json()


def named(client: TestClient, name: str) -> dict[str, Any]:
    return next(row for row in calendars_of(client)["calendars"] if row["name"] == name)


def make_calendar(client: TestClient, name: str = "Familie", **values: Any) -> int:
    answer = client.post("/api/calendars", json={"name": name, **values})
    assert answer.status_code in (200, 201), answer.text
    return int(answer.json()["created"])


def share(calendar_id: int, account_id: int, right: str = RIGHT_EDIT) -> None:
    """A share as TIME-19 will make it, through the service its pages will use."""
    with SessionLocal() as db:
        shares.set_right(db, calendar_id, account_id, right)


def unshare(calendar_id: int, account_id: int) -> None:
    with SessionLocal() as db:
        shares.end(db, calendar_id, account_id)


def make_event(client: TestClient, title: str = "Training", start: str = "2026-11-26T18:30",
               end: str | None = "2026-11-26T20:00", zone: str = "Europe/Berlin", **values: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"title": title, "start": start, "time_zone": zone, **values}
    if end is not None:
        body["end"] = end
    answer = client.post("/api/events", json=body)
    assert answer.status_code in (200, 201), answer.text
    return answer.json()


def window(client: TestClient, start: str = "2026-11-01T00:00:00Z", end: str = "2027-01-01T00:00:00Z",
           **query: Any) -> list[dict[str, Any]]:
    answer = client.get("/api/events", params={"start": start, "end": end, **query})
    assert answer.status_code == 200, answer.text
    return answer.json()


def code(answer: Any) -> str:
    return str(answer.json()["detail"]["code"])
