"""„Was ist neu“: the shared module wasneu (``vendor/nexwasneu``, bauplaene/wasneu 0.1.0) in nextime.

The texts per version and language lie in ``app/whatsnew/`` (``de.json``, ``en.json``; what is released is frozen in
``released.json``, the guard ``tests/nexwasneu/test_nexwasneu_texts.py`` checks them before every release). The account
keeps what it read in ``whats_new_seen``; the module reads and sets it through the ``Viewer`` of each request. Routes
under ``/api/whats-new``; the route of before ``POST /api/me/whats-new/seen`` stays and calls ``mark_seen``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends

from .. import __version__
from ..db import SessionLocal
from ..deps import require_account
from ..models import OPERATOR, Account
from ..vendor import nexwasneu

FOLDER = Path(__file__).resolve().parent.parent / "whatsnew"
LANGUAGES = ("de", "en")


def configure() -> nexwasneu.AppConfig:
    return nexwasneu.configure(nexwasneu.AppConfig(app_name="nextime", version=lambda: __version__, folder=FOLDER,
                                                   languages=LANGUAGES))


def viewer_of(account: Account) -> nexwasneu.Viewer:
    def mark(version: str) -> None:
        with SessionLocal() as db:
            row = db.get(Account, account.id)
            if row is not None:
                row.whats_new_seen = version
                db.commit()
        account.whats_new_seen = version

    return nexwasneu.Viewer(seen=account.whats_new_seen or "", operator=account.role == OPERATOR, mark=mark,
                            language=account.language or None)


def viewer(account: Annotated[Account, Depends(require_account)]) -> nexwasneu.Viewer:
    """The dependency of the module's routes: the signed-in account (401 without a session)."""
    return viewer_of(account)


configure()
