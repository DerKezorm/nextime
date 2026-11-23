"""Collections of the signed-in account (TIME-13). "All" is not among them: it is every calendar the account shows.

| Method | Path | What |
|---|---|---|
| GET | ``/api/collections`` | the own collections by name, each with its calendars |
| POST | ``/api/collections`` | a new one (201; the same ``client_ref`` again: 200 with the first one) |
| PATCH | ``/api/collections/{id}`` | name and calendars, as saved |
| DELETE | ``/api/collections/{id}`` | gone for good; calendars and events stay |
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from ..deps import Account, DbSession
from ..services import collections

router = APIRouter(prefix="/api/collections", tags=["collections"])


class CollectionIn(BaseModel):
    name: str = Field(max_length=400)
    calendar_ids: list[int] = Field(default_factory=list, max_length=500)
    client_ref: str | None = Field(default=None, max_length=64)
    with_tasks: bool = False
    #: The areas of nextasks in it; ``with_tasks`` alone means every area.
    task_areas: list[str] | None = Field(default=None, max_length=200)


class CollectionChange(BaseModel):
    name: str | None = Field(default=None, max_length=400)
    calendar_ids: list[int] | None = Field(default=None, max_length=500)
    with_tasks: bool | None = None
    task_areas: list[str] | None = Field(default=None, max_length=200)


def _listing(db: DbSession, account: Account, made: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"collections": collections.view(db, account.id)}
    if made is not None:
        result["created"] = made
    return result


@router.get("", summary="The account's collections")
def list_collections(account: Account, db: DbSession) -> dict[str, Any]:
    return _listing(db, account)


@router.post("", status_code=201, summary="A new collection")
def create_collection(payload: CollectionIn, response: Response, account: Account, db: DbSession) -> dict[str, Any]:
    row, new = collections.create(db, account, name=payload.name, calendar_ids=payload.calendar_ids,
                                  client_ref=payload.client_ref, with_tasks=payload.with_tasks,
                                  task_areas=payload.task_areas)
    if not new:
        response.status_code = 200
    return _listing(db, account, row.id)


@router.patch("/{collection_id}", summary="Change a collection")
def change_collection(collection_id: int, payload: CollectionChange, account: Account,
                      db: DbSession) -> dict[str, Any]:
    collections.change(db, account, collection_id, name=payload.name, calendar_ids=payload.calendar_ids,
                       with_tasks=payload.with_tasks, task_areas=payload.task_areas)
    return _listing(db, account)


@router.delete("/{collection_id}", summary="Delete a collection")
def delete_collection(collection_id: int, account: Account, db: DbSession) -> dict[str, Any]:
    collections.remove(db, account, collection_id)
    return _listing(db, account)
