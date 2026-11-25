"""System mail through the operator's SMTP server, written and sent by the shared post module (``vendor/nexpost``).

Off until the operator enters a server: the link to copy is always enough. The invitation and the operator's test mail
go out at once (the page shows the server's answer); the mails about an account wait in the outbox
(``services/kontenpost``, ``services/post_store``). All go out in the language of whoever receives it: the
receiver's account language, else their browser's at the last sign-in; else whoever sends the mail off (their account,
their page, their browser); else the instance's, which is its first operator's; else English. The invitation names who
invites and carries the link.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OPERATOR, Account
from . import settings_service

logger = logging.getLogger("nextime.mail")

TIMEOUT = 15
SECURITY = ("starttls", "tls", "none")


class MailError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def configured(db: Session) -> bool:
    values = settings_service.get_all(db)
    return bool(values["smtp_host"] and values["smtp_from"])


#: The languages nextime writes mail in (the shared post module has the texts); any other gets English.
MAIL_LANGUAGES = ("de", "en")


#: The first language of an ``Accept-Language`` header: ``de`` from ``de-DE,de;q=0.9,en;q=0.8``.
_FIRST_LANGUAGE = re.compile(r"\s*([A-Za-z]{2,3})(?:[-_][A-Za-z0-9]{1,8})*\s*(?:[;,]|$)")


def note_browser_language(account: Account, header: str) -> None:
    """Remembers the browser's language at a sign-in, for a mail to the account while it has no language of its own.
    Stored with the session the sign-in starts; a header without a language keeps what was known."""
    found = _FIRST_LANGUAGE.match(header or "")
    if found:
        account.browser_language = found.group(1).lower()


def mail_language(*choices: str | None) -> str:
    """The first of ``choices`` nextime has mail texts for (``de-AT`` counts as ``de``); English otherwise."""
    for choice in choices:
        code = (choice or "").split("-")[0].split("_")[0].strip().lower()
        if code in MAIL_LANGUAGES:
            return code
    return "en"


def languages(account: Account | None, page: str = "") -> tuple[str, ...]:
    """What a person reads, best first: the account's language, the page they are on, their browser at the last
    sign-in."""
    if account is None:
        return (page,)
    return account.language, page, account.browser_language


def instance_languages(db: Session) -> tuple[str, ...]:
    """nextime has no language of its own: the first operator's stands for it (chosen when setting it up)."""
    first = db.scalar(select(Account).where(Account.role == OPERATOR).order_by(Account.id).limit(1))
    return languages(first)


def language_for(db: Session, receiver: Account | None, *fallback: str) -> str:
    """The language of a mail: the receiver's account, else ``fallback`` (whoever sends it off and their page), else
    the instance's, else English."""
    return mail_language(*languages(receiver), *fallback, *instance_languages(db))


def _one_line(text: str) -> str:
    """A name in a mail: one line, no control characters (C0, DEL, C1, line and paragraph separators)."""
    return " ".join("".join(" " if unicodedata.category(char) in {"Cc", "Zl", "Zp"} else char for char in text).split())


def deliver_letter(db: Session, letter: object) -> None:
    """A letter of the post module at once (``MailError`` when it did not go out): "Forgot your password?"."""
    _deliver(db, letter)


def _deliver(db: Session, letter: object) -> None:
    """At once, through the shared post module (the operator waits for the answer: an invitation, the test mail)."""
    from ..vendor import nexpost
    from .post_store import PostStore

    store = PostStore(db)
    settings = store.mail_settings()
    if not settings.configured:
        raise MailError("mail_off", "No mail server is set up.")
    try:
        nexpost.send.deliver(settings, letter)  # type: ignore[arg-type]
    except nexpost.MailError as exc:
        logger.warning("Mail not sent host=%s code=%s", settings.host, exc.code)
        nexpost.outbox._note(store, ok=False, error=exc.code)
        db.commit()
        raise MailError("mail_off" if exc.code == "mail_off" else "mail_failed",
                        "The mail server did not take the mail.") from exc
    nexpost.outbox._note(store, ok=True)
    db.commit()
    logger.info("Mail sent host=%s", settings.host)


def send_invite(db: Session, to: str, link: str, *, by: str, until: date | None = None, language: str = "en",
                days: int = 0) -> None:
    """The invitation, at once: the shared post module's letter (logo, text and HTML) in the receiver's language."""
    from ..vendor import nexpost

    del until
    try:
        letter = nexpost.letters.compose(
            "invite", to, language=language,
            values={"by": _one_line(by), "valid": nexpost.letters.validity(language, days=days) if days else ""},
            link=link,
        )
    except nexpost.MailError as exc:
        raise MailError("invalid_email", "This is not an email address.") from exc
    _deliver(db, letter)


def send_test(db: Session, to: str, language: str = "en") -> None:
    from ..vendor import nexpost

    try:
        letter = nexpost.letters.compose("test", to, language=language, values={})
    except nexpost.MailError as exc:
        raise MailError("invalid_email", "This is not an email address.") from exc
    _deliver(db, letter)
