"""Addresses a member types for a source of their own: a CalDAV server (TIME-15), a subscription link (TIME-17), an
attachment of such a source (TIME-9). Checked as the addresses of the other nex apps are (``nextasks.address_rule``):

* **Never** the server itself (loopback), link-local, multicast, "any", or a cloud's metadata service, also where they
  hide inside an IPv6 address (mapped, NAT64, 6to4; ``outbound.never``).
* **The own network** (LAN, VPN) only for the operator, or for every account when the operator allows it under
  Server › Verbindungen › Andere nex-Apps, "Verbindungen ins eigene Netz" (``connections_local_network``, the same
  switch as for nextasks and nexpaper).
* **Checked where the connection is made.** The addresses checked are the ones connected to, never a second lookup
  that could answer differently (DNS rebinding); every redirect is a new connection and checked again. ``transport``
  does so for httpx (subscriptions, attachments), ``remote_dav`` for the library ``caldav``.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable
from typing import Any

import httpcore
import httpx
from sqlalchemy.orm import Session

from ..models import OPERATOR, Account
from . import outbound

Rule = Callable[[outbound.IP], None]

#: Where the tests' own servers stand (127.0.0.1): let through by the tests only, nothing in the running app.
TESTS_ALLOWED: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()


def local_allowed(db: Session, account_id: int | None) -> bool:
    """Whether the account may have nextime ask addresses in the own network: the operator always, everybody else when
    the operator allows it."""
    from . import nextasks

    account = db.get(Account, account_id) if account_id is not None else None
    return (account is not None and account.role == OPERATOR) or nextasks.local_allowed(db)


def rule(local: bool) -> Rule:
    """Raises ``outbound.Refused`` for an address never asked (``refused``) or one in the own network while that is
    not allowed (``local``)."""
    from . import nextasks

    base = nextasks.address_rule(local)

    def checked(ip: outbound.IP) -> None:
        if any(ip.version == net.version and ip in net for net in TESTS_ALLOWED):
            return
        if outbound.never(ip):
            raise outbound.Refused("refused")
        base(ip)

    return checked


def for_account(db: Session, account_id: int | None) -> Rule:
    return rule(local_allowed(db, account_id))


def check(addresses: Iterable[str], checked: Rule) -> None:
    """Every address past the rule; ``outbound.Refused`` for the first that is not."""
    for address in addresses:
        try:
            ip = outbound.ip_of(address)
        except ValueError as exc:
            raise outbound.Refused("refused") from exc
        checked(ip)


def literal(host: str) -> str | None:
    """The host when it is an address already (``[::1]`` without its brackets), else None."""
    bare = host.strip("[]")
    try:
        outbound.ip_of(bare)
    except ValueError:
        return None
    return bare


class CheckedBackend(httpcore.NetworkBackend):
    """httpx's sockets, each to an address resolved here once and checked: the connection goes to exactly what was
    checked (tried in turn), the name stays in the Host header and the TLS name."""

    def __init__(self, checked: Rule, resolver: Callable[[str], list[str]],
                 inner: httpcore.NetworkBackend | None = None) -> None:
        self._rule = checked
        self._resolver = resolver
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                    socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        given = literal(host)
        addresses = [given] if given is not None else self._resolver(host)
        if not addresses:
            raise outbound.Unreachable(host)
        check(addresses, self._rule)
        last: Exception | None = None
        for address in addresses:
            try:
                return self._inner.connect_tcp(address, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                # The next address checked: a name may give ::1 first while the service listens on IPv4.
                last = exc
        assert last is not None
        raise last

    def connect_unix_socket(self, path: str, timeout: float | None = None,
                            socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        raise httpcore.ConnectError("no unix sockets here")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def transport(checked: Rule, resolver: Callable[[str], list[str]]) -> httpx.HTTPTransport:
    """httpx's own transport, with sockets only to checked addresses (``CheckedBackend``)."""
    made = httpx.HTTPTransport(verify=outbound.tls(), trust_env=False)
    pool = made._pool
    if not hasattr(pool, "_network_backend"):
        raise RuntimeError("httpcore changed: the addresses cannot be checked in the socket")
    pool._network_backend = CheckedBackend(checked, resolver)
    return made


def refusal(exc: BaseException | None) -> str | None:
    """``refused`` or ``local`` when the exception, or one it came from, is a refused address; else None."""
    seen = 0
    while exc is not None and seen < 20:
        if isinstance(exc, outbound.Refused):
            return "local" if exc.reason == "local" else "refused"
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return None
