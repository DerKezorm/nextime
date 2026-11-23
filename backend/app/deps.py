"""Who is asking.

Every route under ``/api`` except setup, sign-in, invitations, the public reading pages, languages and health needs
the session cookie. Changing requests need ``X-Nextime-Client`` on top (``GuardMiddleware``): a page on another site
can make a browser send a form with the cookie, but not a request with a header of its own.
"""

from __future__ import annotations

import ipaddress
import logging
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal, get_db
from .errors import detail, error
from .models import OPERATOR, SIGN_IN_PASSWORD, AuthSession, OidcLink, OidcProvider, utcnow
from .models import Account as AccountRow
from .security import SESSION_COOKIE, account_out, brake, hash_token, session_account, session_owner
from .services import accounts, logs, totp

DbSession = Annotated[Session, Depends(get_db)]

logger = logging.getLogger("nextime.auth")


@lru_cache(maxsize=4)
def _trusted_networks(spec: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def parse_address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address in a peer or a forwarded hop, without port, brackets or zone; IPv4 in IPv6 dress unwrapped."""
    text = text.strip()
    candidates = [text]
    if text.startswith("[") and "]" in text:
        candidates.insert(0, text[1 : text.index("]")])
    elif text.count(":") == 1:
        candidates.insert(0, text.split(":")[0])
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate.split("%")[0])
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            return address.ipv4_mapped
        return address
    return None


def normal_address(text: str) -> str:
    """One sender, one spelling, for the brake: IPv6 as its /64 (one connection usually holds a whole /64). Before,
    every port and every way of writing an address counted as a new sender."""
    address = parse_address(text)
    if address is None:
        return text.strip()[:64]
    if isinstance(address, ipaddress.IPv6Address) and not address.is_loopback:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _is_trusted_proxy(text: str) -> bool:
    networks = _trusted_networks(get_settings().trusted_proxies)
    address = parse_address(text) if networks else None
    return address is not None and any(address in network for network in networks)


def is_trusted_proxy(text: str) -> bool:
    """The address belongs to a reverse proxy configured in ``NEXTIME_TRUSTED_PROXIES``."""
    return _is_trusted_proxy(text)


_warned_unknown_proxy = False
#: Headers a reverse proxy adds; any of them without a trusted proxy configured means one stands in front unnamed.
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")


def behind_unknown_proxy(request: Request) -> bool:
    """A request that came through a proxy nextime was not told to believe: all senders look alike then. Noted once
    in the log, and remembered for the hint under Settings, Server (``unknown_proxy_seen``)."""
    global _warned_unknown_proxy
    if _trusted_networks(get_settings().trusted_proxies) or not any(request.headers.get(h) for h in PROXY_HEADERS):
        return False
    if not _warned_unknown_proxy:
        _warned_unknown_proxy = True
        logger.warning("Requests arrive with proxy headers, but NEXTIME_TRUSTED_PROXIES is not set: every sender "
                       "looks like the proxy. Set it to the proxy's address so the sign-in brake can tell them apart.")
    return True


def unknown_proxy_seen(request: Request) -> bool:
    """Whether this request or one before came through a proxy that is not configured (while none is)."""
    return behind_unknown_proxy(request) or (
        _warned_unknown_proxy and not _trusted_networks(get_settings().trusted_proxies))


def client_ip(request: Request) -> str:
    """The sender's address, for the brake. ``X-Forwarded-For`` counts only from a configured trusted proxy, and
    then its rightmost hop that is not itself a trusted proxy: everything left of it the sender wrote itself."""
    peer = request.client.host if request.client else "-"
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded or not _is_trusted_proxy(peer):
        return normal_address(peer)
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted_proxy(hop):
            return normal_address(hop)
    return normal_address(hops[0] if hops else peer)


#: What an account that must set up its second factor may still reach: itself, the way out, and the setup.
SETUP_ONLY_PATHS = {"/api/auth/me", "/api/auth/logout", "/api/auth/totp/begin", "/api/auth/totp/confirm"}


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else ""


def require_account(request: Request) -> AccountRow:
    """The signed-in account, detached from the database session (routes open their own). A browser brings its
    session cookie.

    A blocked or deleted account is out at once on every device: an open window hears ``account_blocked`` (and shows
    "Your account is blocked")."""
    with SessionLocal() as db:
        cookie = request.cookies.get(SESSION_COOKIE)
        account = session_account(db, cookie)
        if account is None:
            owner = session_owner(db, cookie)
            if owner is not None and account_out(owner):
                raise error("account_blocked", "This account is blocked. Ask the operator to unblock it.", 401)
            raise error("sign_in_required", "Sign in first.", 401)
        if request.url.path not in SETUP_ONLY_PATHS and totp.setup_required(db, account):
            raise error("second_factor_setup_required", "Set up your second factor first.", 403)
        db.expunge(account)
    logs.set_actor(account.name)
    return account


def require_operator(request: Request, account: Annotated[AccountRow, Depends(require_account)]) -> AccountRow:
    if account.role != OPERATOR:
        raise error("operator_only", "Only the operator may do this.", 403)
    networks = _trusted_networks(get_settings().operator_networks)
    if networks:
        try:
            sender = ipaddress.ip_address(client_ip(request).split("/")[0])
        except ValueError:
            sender = None
        # An IPv6 sender stands as its /64 here: its network's first address is inside the operator's networks
        # exactly when the /64 is (they are not smaller than /64 in a home network).
        if sender is None or not any(sender in network for network in networks):
            raise error("operator_network", "The operator's settings are open only from the home network.", 403)
    return account


Account = Annotated[AccountRow, Depends(require_account)]
OperatorAccount = Annotated[AccountRow, Depends(require_operator)]


# --- The password once more, while signed in ------------------------------------------------------------------------
#
# Changing the password or linking the account to a provider asks for the password again, and a wrong answer counts
# the way it does at sign-in: otherwise a stolen cookie would be a place to guess without limit.


def _reauth_key(request: Request) -> str:
    return "reauth:" + client_ip(request)


def reauth_guard(request: Request, account: AccountRow) -> None:
    if accounts.is_locked(account):
        raise error("account_locked", "Too many failed attempts. Try again later.", 429)
    wait = brake.wait_seconds(_reauth_key(request))
    if wait:
        raise HTTPException(
            status_code=429,
            detail=detail("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )


def reauth_failed(request: Request, db: Session, account: AccountRow) -> None:
    brake.failed(_reauth_key(request))
    accounts.note_failure(db, account)


def reauth_succeeded(request: Request, db: Session, account: AccountRow) -> None:
    brake.succeeded(_reauth_key(request))
    accounts.note_success(db, account)


#: How old the session of an account that signs in through a provider may be for an operator act or another act a
#: stolen session must not be enough for.
OPERATOR_FRESH_MINUTES = 10


def _fresh_from_provider(request: Request, db: Session, row: AccountRow) -> None:
    """An operator without a password here proves it is still them by a sign-in at the provider younger than
    ``OPERATOR_FRESH_MINUTES``: the browser session must be that new. Otherwise the refusal names the operator's
    providers, and the page sends the browser there with ``fresh=1`` (``prompt=login``, ``max_age=0``) and back.
    A token is never enough."""
    cookie = request.cookies.get(SESSION_COOKIE)
    session = None
    if cookie and not _bearer(request):
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(cookie)))
    if (session is not None and session.account_id == row.id
            and utcnow() - session.created_at <= timedelta(minutes=OPERATOR_FRESH_MINUTES)):
        return
    providers = [
        {"slug": entry.slug, "label": entry.label}
        for entry in db.scalars(
            select(OidcProvider)
            .join(OidcLink, OidcLink.provider_id == OidcProvider.id)
            .where(OidcLink.account_id == row.id, OidcProvider.enabled.is_(True))
            .order_by(OidcProvider.position, OidcProvider.id)
        )
    ]
    raise error("fresh_sign_in_required", "Sign in at your provider again first: this needs a sign-in from the "
                f"last {OPERATOR_FRESH_MINUTES} minutes.", 403, minutes=OPERATOR_FRESH_MINUTES, providers=providers)


def confirm_operator(request: Request, db: Session, operator: AccountRow, password: str) -> None:
    """The operator's password once more, before an act a stolen session must not be enough for: carrying a backup
    away or bringing one back, making a reset link for another account, taking its second factor, changing a role,
    blocking or deleting an account. An operator who signs in through a provider has no password here; it needs a
    sign-in at the provider from the last few minutes instead (``_fresh_from_provider``)."""
    row = db.get(AccountRow, operator.id)
    assert row is not None
    if row.sign_in != SIGN_IN_PASSWORD:
        _fresh_from_provider(request, db, row)
        return
    reauth_guard(request, row)
    if not accounts.check_password(row, password):
        reauth_failed(request, db, row)
        raise error("wrong_password", "The current password is wrong.", 401)
    reauth_succeeded(request, db, row)
