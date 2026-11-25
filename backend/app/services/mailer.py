"""System mail through the operator's SMTP server, written and sent by the shared post module (``vendor/nexpost``,
post 1.1.0): nextime has no mailer of its own any more, only the language a mail goes out in and its codes of before.

Off until the operator enters a server: the link to copy is always enough. The invitation goes out at once
(``nexpost.outbox.deliver_now``, the page shows the server's answer), and so does the operator's test mail (the
module's, ``nexpost.outbox.test_mail``); the mails about an account wait in the outbox and go out at once after the
commit (``services/kontenpost``, ``services/post_store``). All go out in the language of whoever receives it: the
receiver's account language, else their browser's at the last sign-in; else whoever sends the mail off (their account,
their page, their browser); else the instance's, which is its first operator's; else English. The invitation names who
invites and carries the link.
"""

from __future__ import annotations

import logging
import re
import unicodedata

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


def one_line(text: str) -> str:
    """A name in a mail: one line, no control characters (C0, DEL, C1, line and paragraph separators)."""
    return " ".join("".join(" " if unicodedata.category(char) in {"Cc", "Zl", "Zp"} else char for char in text).split())


#: The codes the post module raises, as nextime's own routes answered them before post 1.1.0 (the page and scripts of
#: before read ``mail_off``, ``mail_failed`` and ``invalid_email``); the module's own code goes along as ``mail_code``.
OLD_CODES = {"mail_off": "mail_off", "mail_address_invalid": "invalid_email"}


def old_code(code: str) -> str:
    return OLD_CODES.get(code, "mail_failed")


def deliver_now(db: Session, letter: object) -> None:
    """A letter of the post module at once, outside the outbox (``nexpost.outbox.deliver_now``): an invitation,
    "Forgot your password?". ``MailError`` with nextime's code of before when it did not go out; the result stands on
    the operator's page like an outbox mail's."""
    from ..vendor import nexpost
    from .post_store import PostStore

    store = PostStore(db)
    if not store.mail_settings().configured:
        raise MailError("mail_off", "No mail server is set up.")
    try:
        nexpost.outbox.deliver_now(store, letter)  # type: ignore[arg-type]
    except nexpost.MailError as exc:
        logger.warning("Mail not sent code=%s", exc.code)
        raise MailError(old_code(exc.code), "The mail server did not take the mail.") from exc
    logger.info("Mail sent kind=%s", getattr(letter, "kind", ""))


def send_invite(db: Session, to: str, link: str, *, by: str, language: str = "en", days: int = 0) -> None:
    """The invitation, at once: the shared post module's letter (logo, text and HTML) in the receiver's language."""
    from ..vendor import nexpost

    try:
        letter = nexpost.letters.compose(
            "invite", to, language=language,
            values={"by": one_line(by), "valid": nexpost.letters.validity(language, days=days) if days else ""},
            link=link,
        )
    except nexpost.MailError as exc:
        raise MailError("invalid_email", "This is not an email address.") from exc
    deliver_now(db, letter)
