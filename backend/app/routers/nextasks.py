"""The member's own nextasks in the calendar (TIME-25). Everything here is for the signed-in account alone: nobody
else ever sees its tasks (EC-14), and the key never comes back (EC-13).

| Method | Path | What |
|---|---|---|
| GET | ``/api/nextasks`` | the connection: address, name, right, problem; whether the own network is allowed |
| PUT | ``/api/nextasks`` | connect (or a new key): checked at once, kept only when it holds |
| PATCH | ``/api/nextasks`` | "Meine Aufgaben" shown or hidden, like a calendar |
| DELETE | ``/api/nextasks`` | disconnect; in nextasks everything stays |
| GET | ``/api/nextasks/areas`` | every area of nextasks with its lists: chosen whole, or which lists alone |
| PUT | ``/api/nextasks/areas`` | the areas chosen whole and the single lists (``areas``, ``lists``) |
| PATCH | ``/api/nextasks/areas/{id}`` | the eye of one row of the sidebar (an area or a list) |
| GET | ``/api/tasks?from=&to=&previews=`` | the open tasks due in those days (no ``from``: overdue ones too), repeating
ones again as previews when asked |
| POST | ``/api/tasks/{id}/tick`` | tick off or take back; waits while nextasks is away |
| GET | ``/api/tasks/{id}/open`` | the link of the task, or that it is gone |
| POST | ``/api/tasks/notices/{id}/seen`` | a notice "Abhaken ist nicht angekommen" read |
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response
from pydantic import BaseModel, Field

from ..deps import Account, DbSession
from ..errors import error
from ..models import OPERATOR
from ..services import nextasks

router = APIRouter(tags=["nextasks"])

TaskId = Annotated[str, Path(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9-]+$")]
#: The farthest day a view asks for.
MAX_DAYS = 400


class ConnectIn(BaseModel):
    url: str = Field(max_length=nextasks.MAX_URL + 100)
    token: str = Field(max_length=nextasks.MAX_TOKEN + 100)


class ConnectionChange(BaseModel):
    hidden: bool


class AreasIn(BaseModel):
    #: Areas chosen whole (lists added later included) and single lists of other areas.
    areas: list[str] = Field(default_factory=list, max_length=nextasks.MAX_AREAS)
    lists: list[str] = Field(default_factory=list, max_length=nextasks.MAX_AREAS)


class AreaChange(BaseModel):
    hidden: bool


class TickIn(BaseModel):
    done: bool
    #: What the page sends with a tap: the same tap twice (a double click) is one (EC-10).
    ref: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=2000)


def _guard(call: Any, *args: Any, **values: Any) -> Any:
    try:
        return call(*args, **values)
    except nextasks.NextasksError as exc:
        raise exc.http() from exc


@router.get("/api/nextasks", summary="The connection to nextasks")
def connection(account: Account, db: DbSession) -> dict[str, Any]:
    return nextasks.connection_out(db, nextasks.connection_of(db, account.id))


@router.put("/api/nextasks", summary="Connect nextasks")
def connect(payload: ConnectIn, account: Account, db: DbSession) -> dict[str, Any]:
    try:
        row = nextasks.connect(db, account.id, payload.url, payload.token)
    except nextasks.NextasksError as exc:
        if exc.local_only and account.role == OPERATOR:
            # The operator can change it, and is told how; a member hears "cannot be reached" as for anything else
            # on the way (TIME-25 EC-6), so nobody maps the network from the form.
            raise error("nextasks_address_local", "This nextasks is in the local network. Allow that under Settings, "
                        "Server, General: 'Connections into the local network'.", 422) from exc
        raise exc.http() from exc
    return nextasks.connection_out(db, row)


@router.patch("/api/nextasks", summary="Show or hide the tasks")
def change(payload: ConnectionChange, account: Account, db: DbSession) -> dict[str, Any]:
    if nextasks.connection_of(db, account.id) is None:
        raise error("nextasks_not_connected", "No nextasks is connected.", 409)
    nextasks.set_hidden(db, account.id, payload.hidden)
    return nextasks.connection_out(db, nextasks.connection_of(db, account.id))


@router.delete("/api/nextasks", status_code=204, summary="Disconnect nextasks")
def disconnect(account: Account, db: DbSession) -> Response:
    nextasks.disconnect(db, account.id)
    return Response(status_code=204)


@router.get("/api/nextasks/areas", summary="The areas of nextasks")
def areas(account: Account, db: DbSession) -> dict[str, Any]:
    return {"areas": _guard(nextasks.areas_out, db, account)}


@router.put("/api/nextasks/areas", summary="Choose the areas to see")
def choose_areas(payload: AreasIn, account: Account, db: DbSession) -> dict[str, Any]:
    if nextasks.connection_of(db, account.id) is None:
        raise error("nextasks_not_connected", "No nextasks is connected.", 409)
    nextasks.set_choice(db, account.id, payload.areas, payload.lists)
    return {"areas": _guard(nextasks.areas_out, db, account)}


@router.patch("/api/nextasks/areas/{area_id}", status_code=204, summary="Show or hide one area")
def change_area(area_id: TaskId, payload: AreaChange, account: Account, db: DbSession) -> Response:
    _guard(nextasks.set_area_hidden, db, account, area_id, payload.hidden)
    return Response(status_code=204)


@router.get("/api/tasks", summary="The tasks due")
def tasks(account: Account, db: DbSession,
          to: Annotated[date, Query(description="The last day shown")],
          start: Annotated[date | None, Query(alias="from", description="The first day; none: overdue too")] = None,
          previews: Annotated[bool, Query(description="Repeating tasks again on their coming days")] = False,
          ) -> dict[str, Any]:
    today = nextasks.clock().date()
    if abs((to - today).days) > MAX_DAYS:
        to = today + timedelta(days=MAX_DAYS if to > today else 0)
    if start is not None and (start > to or (to - start).days > MAX_DAYS):
        start = to - timedelta(days=MAX_DAYS) if start <= to else to
    return nextasks.tasks_in(db, account, start, to, previews=previews)


@router.post("/api/tasks/{task_id}/tick", summary="Tick a task off or take it back")
def tick(task_id: TaskId, payload: TickIn, account: Account, db: DbSession) -> dict[str, Any]:
    return _guard(nextasks.tick, db, account, task_id, done=payload.done, ref=payload.ref, title=payload.title)


@router.get("/api/tasks/{task_id}/open", summary="Where a task opens in nextasks")
def open_task(task_id: TaskId, account: Account, db: DbSession) -> dict[str, Any]:
    return _guard(nextasks.opened, db, account, task_id)


@router.post("/api/tasks/notices/{notice_id}/seen", status_code=204, summary="A notice read")
def seen(notice_id: Annotated[int, Path(ge=1, le=2**62)], account: Account, db: DbSession) -> Response:
    nextasks.notice_seen(db, account.id, notice_id)
    return Response(status_code=204)
