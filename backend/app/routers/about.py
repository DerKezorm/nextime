"""Version and license (open to anybody), and whether a newer nextime is out: the routes of the shared module updates
(``vendor/nexupdates``, bauplaene/updates 0.1.1) under nextime's ``/api/about``.

An operator sees the state, switches the daily check and asks now, because the question goes out for the whole
installation; a member gets the same form with every field empty and ``hidden`` true, and never asks (U15, U21). The
summaries stay nextime's (E53); the operation ids and schema names were the module's already.
"""

from __future__ import annotations

from ..deps import OPERATOR, require_account, require_operator
from ..services import updates
from ..vendor.nexupdates import api as updates_api

router = updates_api.router(
    account=require_account,
    operator=require_operator,
    is_operator=lambda account: account.role == OPERATOR,
    prefix="/api/about",
    summaries={
        "about": "About nextime",
        "update_state": "Whether a newer nextime is out (asked once a day)",
        "check_now": "Ask now, also with the daily check off",
        "switch": "Switch the daily check",
    },
)

REPO_URL = updates.REPO_URL
