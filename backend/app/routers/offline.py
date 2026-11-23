"""What the installed web app keeps for reading without a connection (TIME-28, ``services/offline.py``).

| Method | Path | What |
|---|---|---|
| GET | ``/api/offline?start=&end=`` | the occurrences of at most 40 days with their notes, as the views draw them |

The answer is never kept by the browser's cache (``no-store``): the page keeps it itself, for the account that asked,
and forgets it when that account signs out or another one signs in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Response

from ..deps import Account, DbSession
from ..errors import error
from ..services import offline

router = APIRouter(prefix="/api/offline", tags=["offline"])


def _moment(text: str, name: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=[name]) from None
    if value.tzinfo is None:
        raise error("invalid_input", "The input is not valid.", 422, fields=[name])
    return value.astimezone(UTC)


@router.get("", summary="The next weeks for reading without a connection")
def snapshot(account: Account, db: DbSession, response: Response, start: str = Query(max_length=40),
             end: str = Query(max_length=40)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return offline.snapshot(db, account, _moment(start, "start"), _moment(end, "end"))
