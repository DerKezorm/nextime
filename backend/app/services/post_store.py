"""nextime's side of the shared post module ``vendor/nexpost``: the ``Store`` over the operator's settings
(``smtp_*`` as 0.4.0 kept them, the password sealed as before) and the table ``mail_outbox``, and the worker that
sends what waits.

System mail (an invitation, a link for a new password, a confirmation link, notices about the account) goes into the
outbox with the request that causes it and out within a minute (``run_due``, from the minute loop in ``main``); a
mail that does not go out is tried again after 1, 5, 15, 60 and 180 minutes, then given up. An invitation and the
test mail still go out at once: the operator sees the server's answer.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Account, utcnow
from ..models_accounts import MailOutbox
from ..security import decrypt_secret, encrypt_secret
from ..vendor import nexpost
from ..vendor.nexpost import Letter, MailSettings, MailStatus, OutboxItem, Recipient
from . import settings_service

logger = logging.getLogger("nextime.mail")

#: Mails that went out or were given up stay this long for the operator's page, then go.
KEEP_DAYS = 14


#: The logo in every system mail (``cid:logo``): the app's icon, 192 pixels.
LOGO = Path(__file__).resolve().parents[1] / "mail-logo.png"


def _logo() -> bytes | None:
    return LOGO.read_bytes() if LOGO.is_file() else None


def configure() -> None:
    """The module set up for nextime: its name, logo and the round of the outbox, run at once after a commit that
    queued a mail (post 1.1.0, ``outbox.nudge``); without the background (the tests) the minute loop's round sends."""
    from ..config import get_settings

    runner = None if get_settings().disable_background else (lambda: run_due())
    nexpost.configure(nexpost.AppConfig(app_name="nextime", display_name="nextime", logo=_logo(),
                                        logger_name="nextime.mail", outbox_runner=runner))


configure()


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _moment(text: object) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(str(text))) if text else None
    except ValueError:
        return None


class PostStore:
    """``nexpost.Store`` over ``settings`` and ``mail_outbox``, inside one session."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._listening = False
        self._pending: list[Callable[[], None]] = []

    def mail_settings(self) -> MailSettings:
        values = settings_service.get_all(self.db)
        return MailSettings(
            host=str(values["smtp_host"] or ""),
            port=int(values["smtp_port"] or 587),
            security=nexpost.settings.security_of(str(values["smtp_security"] or "starttls")),
            user=str(values["smtp_user"] or ""),
            password=decrypt_secret(str(values["smtp_password_enc"] or "")),
            sender=str(values["smtp_from"] or ""),
            sender_name=str(values.get("smtp_sender_name") or ""),
        )

    def save_mail_settings(self, settings: MailSettings, password: str | None) -> None:
        changes: dict[str, object] = {
            "smtp_host": settings.host,
            "smtp_port": settings.port,
            "smtp_security": settings.security,
            "smtp_user": settings.user,
            "smtp_from": settings.sender,
            "smtp_sender_name": settings.sender_name,
        }
        if password is not None:
            changes["smtp_password_enc"] = encrypt_secret(password) if password else ""
        settings_service.save(self.db, changes, commit=False)

    def _keyed(self, key: str) -> int | None:
        return self.db.scalar(select(MailOutbox.id).where(MailOutbox.key == key).limit(1))

    def add_to_outbox(self, letter: Letter, now: datetime) -> int:
        """The whole letter, with its headers and key (post 1.1.0); a key the outbox has already (waiting, sent or
        given up) makes no second row."""
        if letter.key:
            held = self._keyed(letter.key)
            if held is not None:
                return held
        row = MailOutbox(letter=json.dumps(nexpost.letter_data(letter)), created_at=now, next_at=now,
                         key=letter.key or None)
        try:
            # Two requests with the same key at once: the unique index lets one in, the other takes its row.
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
        except IntegrityError:
            held = self._keyed(letter.key)
            if held is None:
                raise
            return held
        return row.id

    def waiting(self) -> int:
        return int(self.db.scalar(select(func.count()).select_from(MailOutbox)
                                  .where(MailOutbox.sent_at.is_(None), MailOutbox.next_at.is_not(None))) or 0)

    def on_commit(self, callback: Callable[[], None]) -> None:
        """Once, after this transaction's commit; a rollback drops it. The listeners stay on the session (removing a
        listener while SQLAlchemy runs its listeners breaks the commit); what they call waits in ``_pending``."""
        if not self._listening:
            event.listen(self.db, "after_commit", self._committed)
            event.listen(self.db, "after_soft_rollback", self._rolled_back)
            self._listening = True
        self._pending.append(callback)

    def _committed(self, session: Session) -> None:
        pending, self._pending = self._pending, []
        for callback in pending:
            callback()

    def _rolled_back(self, session: Session, previous: Any) -> None:
        if not getattr(previous, "nested", False):  # a savepoint's rollback leaves the transaction alive
            self._pending = []

    def recipient(self, account: object) -> Recipient | None:
        """Where nextime writes to an account: its address while it counts, in its language and its time zone."""
        from . import display, mailer

        row = self.db.get(Account, int(account)) if str(account).isdigit() else None  # type: ignore[call-overload]
        if row is None or not row.email or not row.email_confirmed:
            return None
        zone = str(getattr(display.zone_of(row), "key", "") or "")
        # The account's own language first (the app writes the words, ``app_letters.write`` names it in the mail);
        # without one the language nextime's mails take (the browser's at the last sign-in, the operator's).
        return Recipient(row.email, row.language or mailer.language_for(self.db, row), row.shown_name, zone)

    @staticmethod
    def _item(row: MailOutbox) -> OutboxItem:
        return OutboxItem(
            id=row.id,
            letter=nexpost.letter_from(json.loads(row.letter)),
            created_at=_aware(row.created_at) or utcnow(),
            next_at=_aware(row.next_at) or utcnow(),
            attempts=row.attempts,
            error=row.error,
        )

    def due(self, now: datetime, limit: int) -> list[OutboxItem]:
        rows = self.db.scalars(
            select(MailOutbox)
            .where(MailOutbox.sent_at.is_(None), MailOutbox.next_at.is_not(None), MailOutbox.next_at <= now)
            .order_by(MailOutbox.created_at, MailOutbox.id)
            .limit(limit)
        )
        return [self._item(row) for row in rows]

    def claim(self, item_id: int | str, now: datetime, until: datetime) -> bool:
        result = self.db.execute(
            update(MailOutbox)
            .where(MailOutbox.id == int(item_id), MailOutbox.sent_at.is_(None), MailOutbox.next_at.is_not(None),
                   MailOutbox.next_at <= now)
            .values(next_at=until)
        )
        return int(result.rowcount or 0) == 1

    def mark_sent(self, item_id: int | str) -> None:
        self.db.execute(update(MailOutbox).where(MailOutbox.id == int(item_id)).values(sent_at=utcnow(), next_at=None))

    def mark_failed(self, item_id: int | str, attempts: int, next_at: datetime | None, error: str) -> None:
        self.db.execute(
            update(MailOutbox).where(MailOutbox.id == int(item_id))
            .values(attempts=attempts, next_at=next_at, error=error[:500])
        )

    def note_result(self, status: MailStatus) -> None:
        settings_service.save(self.db, {
            "mail_last_ok_at": status.last_ok_at.isoformat() if status.last_ok_at else "",
            "mail_last_error": status.last_error or "",
            "mail_last_error_at": status.last_error_at.isoformat() if status.last_error_at else "",
        }, commit=False)

    def mail_status(self) -> MailStatus:
        values = settings_service.get_all(self.db)
        return MailStatus(
            last_ok_at=_moment(values.get("mail_last_ok_at")),
            last_error=str(values.get("mail_last_error") or ""),
            last_error_at=_moment(values.get("mail_last_error_at")),
        )

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


@contextmanager
def own_store() -> Iterator[PostStore]:
    from ..db import SessionLocal

    with SessionLocal() as db:
        yield PostStore(db)


def run_due() -> int:
    """What the minute loop calls: send what is due, and forget what went out or was given up long ago."""
    with own_store() as store:
        sent = nexpost.outbox.run_once(store)
        limit = utcnow() - timedelta(days=KEEP_DAYS)
        store.db.execute(delete(MailOutbox).where(MailOutbox.created_at < limit, MailOutbox.next_at.is_(None)))
        store.commit()
    return sent
