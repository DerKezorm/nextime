"""The page "Server" beyond the settings: version, a newer one, and the state of HTTPS and the internet
(TIME-1 AC-6, AC-7, AC-9, AC-11, EC-9). Operator only (EC-10)."""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Request

from .. import __version__
from ..config import server_zone
from ..db import SessionLocal
from ..deps import OperatorAccount
from ..services import internet, settings_service, updates

router = APIRouter(prefix="/api/server", tags=["server"])


def https_active(request: Request) -> bool:
    """The request came over HTTPS: directly, or through a trusted proxy that ended it (``ForwardedProtoMiddleware``
    has put its scheme in place, EC-9)."""
    return request.url.scheme == "https"


@router.get("/state", summary="Version, a newer version, HTTPS and internet")
async def state(request: Request, _operator: OperatorAccount) -> dict[str, Any]:
    with SessionLocal() as db:
        check_on = bool(settings_service.get(db, "update_check"))
    # The switch "Look for new versions" (TIME-1 AC-11): off, nothing is asked and no hint is shown.
    known = await anyio.to_thread.run_sync(lambda: updates.state(on=check_on))
    online = await anyio.to_thread.run_sync(internet.reachable)
    return {
        "version": __version__,
        # Without an answer (no internet, the check off) there is no hint and no error (EC-13).
        "newer": known.latest if known.newer and check_on else None,
        "update_check": check_on,
        "https": https_active(request),
        "internet": online,
        "zone": server_zone().name,
    }
