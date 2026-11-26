"""Storage and backups over HTTP, operator only: the routes of the shared module ``sicherung``.

- ``/api/server/storage``: the module's own ways (the card "Speicher", the schedule with its time, the list, check,
  download, restore, delete, upload; the settings page uses these);
- ``/api/backups``: the ways nextime had before the module, in their old shapes and with the old password header
  ``x-nextime-password`` (the list as an array of flat entries, the check flat with ``usable``, a backup of a newer
  version refused with 400 ``backup_too_new``, a full disk 507 ``disk_full``), so scripts and older pages keep
  working; ``/api/backups/summary`` (TIME-1 AC-8) stays nextime's own, in its old shape, from the module's summary.

Download, restore, delete and upload ask for the operator's password again (``confirm_operator``: an account from a
provider confirms with a fresh sign-in there; wrong tries count like a sign-in).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..config import server_zone
from ..db import SessionLocal
from ..deps import OperatorAccount, confirm_operator
from ..errors import error
from ..services import backups
from ..vendor.nexsicherung import routes

#: The codes nextime's own routes answered before the module, for the old ways only.
FORMER_CODES = {"backup_disk_full": "disk_full", "backup_write_failed": "write_failed"}


def _confirm(request: Request, account: Any, password: str) -> None:
    with SessionLocal() as db:
        confirm_operator(request, db, account, password)


def _error(code: str, text: str, status: int) -> Exception:
    return error(code, text, status)


def _former_error(code: str, text: str, status: int) -> Exception:
    return error(FORMER_CODES.get(code, code), text, status)


summary_router = APIRouter(prefix="/api/backups", tags=["backups"])


@summary_router.get("/summary", summary="Rhythm, last backup, how many are kept, a failure, whether one is running")
def summary(_operator: OperatorAccount) -> dict[str, Any]:
    """The box "Backups" as nextime answered it before the module (TIME-1 AC-8, EC-7): the time is the server's, and
    so is its zone."""
    found = backups.summary()
    failure = found.get("failure")
    return {
        "schedule": found["schedule"]["every"],
        "time": found["schedule"]["time"],
        "zone": server_zone().name,
        "keep": found["schedule"]["keep"],
        "kept": found["kept"],
        "count": found["count"],
        "last": {"created": found["last"]["created"], "size": found["last"]["size"]} if found["last"] else None,
        "failure": {"at": failure.get("at"), "reason": failure.get("reason")} if isinstance(failure, dict) else None,
        "running": found["running"],
    }


router = APIRouter()
# E53: the description keeps the names nextime's own backup routes had (operationIds, summaries, schemas).
router.include_router(routes.router(operator=OperatorAccount, confirm=_confirm, error=_error,
                                    prefix="/api/server/storage", schema_names=routes.FORMER_SCHEMAS))
# Before the module's old ways, so that ``/summary`` is not taken for the name of a backup.
router.include_router(summary_router)
router.include_router(routes.legacy_router(prefix="/api/backups", password_header="x-nextime-password",
                                           operator=OperatorAccount, confirm=_confirm, error=_former_error,
                                           check_raises_too_new=True, **routes.former_names("/api/backups")))
