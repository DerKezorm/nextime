"""Places over HTTP (TIME-8, ``services/places.py``): suggestions while typing, where a place lies, the small map.

| Method | Path | What |
|---|---|---|
| GET | ``/api/places?q=`` | up to five addresses for the typed place (409 ``places_off`` when switched off) |
| GET | ``/api/places/lookup?q=`` | where a place without a point lies, for text that looks like an address |
| GET | ``/api/places/map?lat=&lon=`` | the small map around a point (WebP); 503 when the tiles do not come |

Only for a signed-in account: nextime is nobody's open door to these services. Too many maps or suggestions in a
minute: 429 ``places_busy`` with ``Retry-After``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from ..deps import Account, DbSession
from ..errors import detail, error
from ..services import places

router = APIRouter(prefix="/api/places", tags=["places"])


def _busy(exc: places.Busy) -> HTTPException:
    return HTTPException(status_code=429, detail=detail("places_busy", "Too many places at once, a moment please.",
                                                        retry_after=exc.seconds),
                         headers={"Retry-After": str(exc.seconds)})


def _language(request: Request, account: Any) -> str:
    return account.language or account.browser_language or request.headers.get("accept-language", "en")


@router.get("", summary="Address suggestions for a typed place")
async def suggestions(request: Request, account: Account, db: DbSession,
                      q: Annotated[str, Query(max_length=1000)] = "") -> dict[str, Any]:
    try:
        return await run_in_threadpool(places.suggest, db, q, _language(request, account), account.id)
    except places.Busy as exc:
        raise _busy(exc) from None


@router.get("/lookup", summary="Where a place without a point lies")
async def lookup(request: Request, account: Account, db: DbSession,
                 q: Annotated[str, Query(max_length=3000)] = "") -> dict[str, Any]:
    return await run_in_threadpool(places.lookup, db, q, _language(request, account))


@router.get("/map", summary="The small map around a place")
async def map_image(account: Account, db: DbSession, lat: Annotated[float, Query(ge=-90, le=90)],
                    lon: Annotated[float, Query(ge=-180, le=180)]) -> Response:
    try:
        data = await run_in_threadpool(places.map_image, db, lat, lon, account.id)
    except places.Busy as exc:
        raise _busy(exc) from None
    except places.Unavailable as exc:
        places._warn("map", exc.reason)
        raise error("map_unavailable", "The map is not available right now.", 503) from None
    return Response(content=data, media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})
