"""The operator's settings: address, sign-in, limits, the switches of the features, invitation mail, backups,
the space in the data folder (TIME-1 AC-4, AC-5, AC-8, EC-11).

Secrets (the mail password) are written encrypted and never read back: the answer only says whether one is set.
The sign-in providers live in ``routers/oidc.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StrictBool, StrictInt
from sqlalchemy import func, select

from ..config import get_settings
from ..deps import DbSession, OperatorAccount, unknown_proxy_seen
from ..errors import error
from ..models import SIGN_IN_PASSWORD
from ..models import Account as AccountRow
from ..security import encrypt_secret
from ..services import accounts, backups, mailer, mounts, settings_service, storage
from ..services.oidc_store import SqlStore
from ..vendor.nexoidc import authentik, providers

logger = logging.getLogger("nextime.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    public_url: str
    password_login: bool
    two_factor_required: bool
    backup_schedule: str
    backup_time: str
    backup_keep: int
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_user: str
    smtp_password_set: bool
    smtp_from: str
    upload_max_mb: int
    trash_days: int
    share_links: bool
    #: The switch as the operator set it, and whether Google is set up; Google is on when both are.
    google_enabled: bool
    google_configured: bool
    api_enabled: bool
    mcp_enabled: bool
    address_suggestions: bool
    connections_local_network: bool = False
    update_check: bool
    #: The disk under the data folder, in bytes.
    data_total: int = 0
    data_used: int = 0
    data_free: int = 0
    #: What nextime itself takes in the data folder, in bytes: the sum and its parts. The disk above may hold much
    #: else besides.
    nextime_used: int = 0
    nextime_parts: dict[str, int] = Field(default_factory=dict)
    #: The data folder lies on a network share.
    data_on_share: bool = False
    #: Requests come with proxy headers, but no trusted proxy is configured (only in the answer of GET).
    proxy_unknown: bool = False


class SettingsIn(BaseModel):
    public_url: str | None = Field(default=None, max_length=255)
    #: The operator answered the question that came back as 409 ``public_url_change`` (authentik knows the old
    #: address). Not a setting; it only goes with ``public_url``.
    confirm_address: bool = False
    password_login: StrictBool | None = None
    two_factor_required: StrictBool | None = None
    backup_schedule: Literal["off", "daily"] | None = None
    backup_time: str | None = Field(default=None, max_length=8)
    backup_keep: StrictInt | None = None
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: Literal["starttls", "tls", "none"] | None = None
    smtp_user: str | None = Field(default=None, max_length=255)
    #: Empty removes the password; left out keeps it.
    smtp_password: str | None = Field(default=None, max_length=500)
    smtp_from: str | None = Field(default=None, max_length=255)
    upload_max_mb: StrictInt | None = None
    trash_days: StrictInt | None = None
    share_links: StrictBool | None = None
    google_enabled: StrictBool | None = None
    api_enabled: StrictBool | None = None
    mcp_enabled: StrictBool | None = None
    address_suggestions: StrictBool | None = None
    connections_local_network: StrictBool | None = None
    update_check: StrictBool | None = None


#: The numbers the operator types, with their bounds: outside them the field says so and nothing is kept (EC-11).
NUMBER_BOUNDS = {
    "upload_max_mb": settings_service.UPLOAD_MAX_MB,
    "trash_days": settings_service.TRASH_DAYS,
    "backup_keep": (1, 365),
}


class TestMailIn(BaseModel):
    to: str = Field(max_length=255)
    #: The language the operator's page shows, for the mail when neither the receiver nor the operator has one.
    language: str = Field(default="", max_length=16)


def _view(db: DbSession) -> SettingsOut:
    values = settings_service.get_all(db)
    folder = get_settings().data_dir
    used = storage.usage(folder)
    own = storage.footprint(folder)
    return SettingsOut(
        public_url=values["public_url"],
        password_login=values["password_login"],
        two_factor_required=bool(values["two_factor_required"]),
        backup_schedule=backups.schedule(db),
        backup_time=backups.time_of_day(db),
        backup_keep=backups.keep(db),
        smtp_host=values["smtp_host"],
        smtp_port=values["smtp_port"],
        smtp_security=values["smtp_security"],
        smtp_user=values["smtp_user"],
        smtp_password_set=bool(values["smtp_password_enc"]),
        smtp_from=values["smtp_from"],
        upload_max_mb=settings_service.upload_max_mb(db),
        trash_days=settings_service.trash_days(db),
        share_links=bool(values["share_links"]),
        google_enabled=settings_service.google_on(db),
        google_configured=settings_service.google_configured(db),
        api_enabled=bool(values["api_enabled"]),
        mcp_enabled=bool(values["mcp_enabled"]),
        address_suggestions=bool(values["address_suggestions"]),
        connections_local_network=bool(values["connections_local_network"]),
        update_check=bool(values["update_check"]),
        data_total=used.total,
        data_used=used.used,
        data_free=used.free,
        nextime_used=own.total,
        nextime_parts={"database": own.database, "attachments": own.attachments, "backups": own.backups,
                       "other": own.exports + own.other},
        data_on_share=mounts.locate(folder).network,
    )


@router.get("", response_model=SettingsOut)
def read(request: Request, _operator: OperatorAccount, db: DbSession) -> SettingsOut:
    # Requests come through a proxy nextime was not told about: the sign-in brake sees one sender for everybody.
    # The page names the setting.
    return _view(db).model_copy(update={"proxy_unknown": unknown_proxy_seen(request)})


@router.put("", response_model=SettingsOut)
def save(payload: SettingsIn, request: Request, operator: OperatorAccount, db: DbSession) -> SettingsOut:
    changes: dict[str, Any] = {}
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None or key == "confirm_address":
            continue
        if key == "public_url":
            try:
                value = settings_service.normalize_public_url(value)
            except ValueError as exc:
                raise error("invalid_url", "Enter an address starting with http:// or https://.", 422) from exc
            # Saving the address changes the return address of the sign-in: authentik, set up by the button, knows
            # the old one. Ask first, save nothing.
            question = authentik.public_url_guard(SqlStore(db), value, str(request.base_url),
                                                  confirm=payload.confirm_address)
            if question is not None:
                raise error("public_url_change", "The address changes what authentik knows.", 409, **question)
        elif key == "smtp_from":
            value = value.strip()
            if value and not accounts.EMAIL_PATTERN.match(value):
                raise error("invalid_email", "This is not a mail address.", 422)
        elif key == "two_factor_required" and value and operator.sign_in == SIGN_IN_PASSWORD and not (
            operator.totp_secret_enc
        ):
            # Else the operator would be the first one sent to the account page, with nothing else in reach.
            raise error("own_second_factor_first", "Set up your own second factor first.", 409)
        elif key == "password_login" and not value:
            if not providers.public_list(SqlStore(db)):
                # Without an active provider nobody but the operator could sign in any more, and invitations would
                # fail.
                raise error("provider_first", "Set up a sign-in provider first.", 409)
        elif key in NUMBER_BOUNDS:
            lowest, highest = NUMBER_BOUNDS[key]
            if not lowest <= value <= highest:
                raise error("invalid_number", f"Enter a whole number from {lowest} to {highest}.", 422,
                            fields=[key], field=key, lowest=lowest, highest=highest)
        elif key == "backup_time":
            value = value.strip()
            if not backups.TIME_PATTERN.match(value):
                raise error("invalid_time", "Enter a time such as 03:00.", 422, fields=[key], field=key)
        elif key == "google_enabled" and not settings_service.google_configured(db):
            # The switch stands on "Server" and on "Set up Google" alike; without a set-up Google there is nothing
            # to switch (TIME-16).
            raise error("google_not_configured", "Set up Google first.", 409, field=key)
        elif key == "smtp_password":
            changes["smtp_password_enc"] = encrypt_secret(value)
            continue
        elif isinstance(value, str):
            value = value.strip()
        changes[key] = value
    settings_service.save(db, changes)
    logger.info("Settings changed keys=%s by=%s", ",".join(sorted(changes)), operator.name)
    return _view(db)


@router.post("/mail-test", status_code=204, summary="Send a test mail through the configured server")
def mail_test(payload: TestMailIn, operator: OperatorAccount, db: DbSession) -> None:
    to = payload.to.strip()
    if not accounts.EMAIL_PATTERN.match(to):
        raise error("invalid_email", "This is not a mail address.", 422)
    # In the language of whoever receives it, as the invitation: an account with this address, else the operator's
    # own language, else the page's, else English.
    known = db.scalar(select(AccountRow).where(func.lower(AccountRow.email) == to.lower()).limit(1))
    language = mailer.language_for(db, known, *mailer.languages(operator, payload.language))
    try:
        mailer.send_test(db, to, language)
    except mailer.MailError as exc:
        # No mail server is a setting, not a failing server: no gateway error, no ERROR in the log.
        raise error(exc.code, str(exc), 409 if exc.code == "mail_off" else 502) from exc
