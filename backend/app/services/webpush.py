"""Web Push: notifications to a browser or the installed web app, also with nextime closed (TIME-21, and TIME-7 for
reminders later). The same way as nexnotes (``services/webpush.py`` there).

A browser signs up in the interface (``PUT /api/notify/webpush``): the address at its push service (Google, Apple,
Mozilla, Microsoft) and two keys. nextime encrypts every message for that browser (RFC 8291, ``aes128gcm``, RFC 8188)
and signs the request with its own key pair (VAPID, RFC 8292): the push service sees that something arrives and for
whom, never what.

**The key pair** is made at the first need and kept in the settings sealed with the server's secret
(``security.encrypt_secret``, context ``operator:vapid``). A new pair would mean every browser signing up again; that
happens only when the secret is lost (a backup restored without its ``secret.key``).

**A browser belongs to its session.** Signing out, a new password and the session's end remove it with the session
(TIME-21 EC-12). Signing in again on the same browser signs it up again (the page does that by itself while the
browser's permission stands).

**Only to push services out in the internet.** The address comes from a browser, so a member could name any: it must
be https, and every address its name resolves to must be a public one; checked at every send, never a redirect
followed (``POST`` is what the tests replace: nothing goes into the network from a test).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import socket
import struct
import threading
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import Setting, WebPushSubscription, utcnow
from ..security import decrypt_secret, encrypt_secret
from . import settings_service

logger = logging.getLogger("nextime.notify")

#: How long a push service keeps a message for a browser that is offline: a change of the day before is old news.
TTL_SECONDS = 24 * 3600
VAPID_SECONDS = 12 * 3600
KEY_SETTING = "webpush_key_enc"
KEY_CONTEXT = "operator:vapid"
#: Where nobody set a public https address: well formed, and plainly a placeholder. Apple refuses ``localhost``.
PLACEHOLDER_CONTACT = "mailto:admin@example.com"
#: How many browsers one account may sign up.
MAX_PER_ACCOUNT = 20
#: The longest endpoint taken (the services' own are far shorter).
MAX_ENDPOINT = 1000
#: Seconds one request to a push service may take, answer included.
SEND_SECONDS = 10.0
#: The record size of the encrypted body (RFC 8188): one record carries the whole message.
RECORD_SIZE = 4096
#: The longest message: RFC 8291 section 4 allows 4,096 bytes for all of it, with the 86 bytes of the header (salt,
#: record size, key) and 17 bytes of padding delimiter and authentication tag.
MAX_PLAINTEXT = RECORD_SIZE - 86 - 17
#: The longest wait a push service may ask for with ``Retry-After``.
LONGEST_WAIT = 24 * 3600
#: Addresses that translate to IPv4 behind a NAT64 gateway: they can lead into the own network (TIME-21 review).
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))

_lock = threading.Lock()


class Failed(Exception):
    """A browser could not be reached for a reason that may pass (``code`` for the log and the outbox); the push
    service may have said how long to wait (``retry_after``, seconds)."""

    def __init__(self, code: str, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class Refused(Exception):
    """The push service refused the message itself (400, 413): sending it again changes nothing."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Gone(Exception):
    """The push service says the subscription is gone (404, 410): the row is removed (TIME-21 EC-10)."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _load(stored: str) -> ec.EllipticCurvePrivateKey | None:
    pem = decrypt_secret(stored, KEY_CONTEXT)
    if not pem:
        return None
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    return key if isinstance(key, ec.EllipticCurvePrivateKey) else None


def _sealed_new_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")
    return encrypt_secret(pem, KEY_CONTEXT)


def server_key(db: Session) -> ec.EllipticCurvePrivateKey:
    """The key pair, made at the first need and kept sealed. Two at their first need make one."""
    with _lock:
        stored = str(settings_service.get(db, KEY_SETTING) or "")
        key = _load(stored) if stored else None
        if key is not None:
            return key
        db.execute(sqlite_insert(Setting).values(key=KEY_SETTING, value=_sealed_new_key())
                   .on_conflict_do_nothing(index_elements=[Setting.key]))
        db.commit()
        key = _load(str(settings_service.get(db, KEY_SETTING) or ""))
        if key is None:
            logger.warning("The Web Push key could not be read (another secret key); a new one is made and every "
                           "browser signs up again")
            settings_service.save(db, {KEY_SETTING: _sealed_new_key()})
            db.execute(delete(WebPushSubscription))
            db.commit()
            key = _load(str(settings_service.get(db, KEY_SETTING)))
        assert key is not None
        return key


def public_key(db: Session) -> str:
    return b64url(public_bytes(server_key(db)))


# --- The message, encrypted for one browser (RFC 8291) --------------------------------------------------------------


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(plaintext: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            sender: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """The body for one browser: ``aes128gcm`` with a fresh key pair and salt per message (only tests give them)."""
    receiver_bytes = unb64url(p256dh)
    secret = unb64url(auth)
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver_bytes)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    sender_bytes = public_bytes(sender)
    salt = salt if salt is not None else os.urandom(16)
    shared = sender.exchange(ec.ECDH(), receiver)
    # RFC 8291 3.4: the input keying material from the shared secret and the browser's auth secret.
    prk_key = _hmac(secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + receiver_bytes + sender_bytes + b"\x01")
    # RFC 8188 2.2: content key and nonce.
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(plaintext) > MAX_PLAINTEXT:
        raise ValueError("message too long")
    sealed = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + struct.pack("!IB", RECORD_SIZE, len(sender_bytes)) + sender_bytes + sealed


def vapid_header(key: ec.EllipticCurvePrivateKey, endpoint: str, subject: str) -> str:
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.hostname}", "exp": int(time.time() + VAPID_SECONDS), "sub": subject}
    token = jwt.encode(claims, key, algorithm="ES256", headers={"typ": "JWT"})
    return f"vapid t={token}, k={b64url(public_bytes(key))}"


def subject(db: Session) -> str:
    public = settings_service.public_url(db)
    return public if public.startswith("https://") else PLACEHOLDER_CONTACT


def valid_keys(p256dh: str, auth: str) -> bool:
    """A point on P-256, uncompressed, and an auth secret of 16 bytes."""
    try:
        point = unb64url(p256dh)
        secret = unb64url(auth)
    except (ValueError, TypeError):
        return False
    if len(point) != 65 or point[0] != 4 or len(secret) != 16:
        return False
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError:
        return False
    return True


def endpoint_valid(endpoint: str) -> bool:
    """An https address with a name and nothing else in front: the push service's. Where it leads is checked when
    something is sent (``_post``)."""
    if len(endpoint) > MAX_ENDPOINT:
        return False
    parts = urlsplit(endpoint.strip())
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == "https" and bool(host) and not parts.username and not parts.password
            and port in (None, 443) and host != "localhost" and not host.endswith((".localhost", ".local")))


# --- The browsers of an account --------------------------------------------------------------------------------------


#: What the page may say it runs on (TIME-7 AC-7).
PLATFORMS = ("iphone", "ipad", "mac", "other")


def subscribe(db: Session, account_id: int, session_id: int, endpoint: str, p256dh: str, auth: str,
              user_agent: str = "", installed: bool = False, reminders: bool = True,
              platform: str = "") -> WebPushSubscription:
    """One browser, one row: signing up again (also as another account on the same browser) replaces it."""
    db.execute(delete(WebPushSubscription).where(WebPushSubscription.endpoint == endpoint))
    db.execute(delete(WebPushSubscription).where(WebPushSubscription.session_id == session_id))
    row = WebPushSubscription(account_id=account_id, session_id=session_id, endpoint=endpoint, p256dh=p256dh,
                              auth=auth, user_agent=user_agent[:300], installed=installed, created_at=utcnow(),
                              reminders=reminders, platform=platform if platform in PLATFORMS else "")
    db.add(row)
    db.flush()
    rows = list(db.scalars(select(WebPushSubscription).where(WebPushSubscription.account_id == account_id)
                           .order_by(WebPushSubscription.id.desc())))
    for old in rows[MAX_PER_ACCOUNT:]:
        db.delete(old)
    db.commit()
    return row


def unsubscribe(db: Session, account_id: int, session_id: int) -> bool:
    result = db.execute(delete(WebPushSubscription).where(WebPushSubscription.account_id == account_id,
                                                          WebPushSubscription.session_id == session_id))
    db.commit()
    return bool(getattr(result, "rowcount", 0))


def of_session(db: Session, session_id: int) -> WebPushSubscription | None:
    return db.scalar(select(WebPushSubscription).where(WebPushSubscription.session_id == session_id))


def subscriptions(db: Session, account_id: int) -> list[WebPushSubscription]:
    return list(db.scalars(select(WebPushSubscription).where(WebPushSubscription.account_id == account_id)
                           .order_by(WebPushSubscription.id)))


# --- Sending ---------------------------------------------------------------------------------------------------------


def _resolve(host: str) -> list[str]:
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


#: How a name becomes addresses; the tests put their own here.
resolve: Callable[[str], list[str]] = _resolve


def _public(address: str) -> bool:
    try:
        found = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(found, ipaddress.IPv6Address):
        if any(found in network for network in _NAT64):
            return False
        if found.ipv4_mapped is not None:
            return found.ipv4_mapped.is_global
    return found.is_global


def retry_after(value: str | None) -> float | None:
    """``Retry-After`` as seconds: a number, or a date (RFC 9110); at most a day."""
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return float(min(int(text), LONGEST_WAIT))
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return float(max(0, min(LONGEST_WAIT, (when - utcnow()).total_seconds())))


def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, float | None]:
    """One request to a push service: port 443, public addresses only (never the own network, also not through
    NAT64), no redirect, a deadline for all of it. The status and how long the service asks to wait."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or parts.port not in (None, 443):
        raise Failed("refused")
    try:
        addresses = resolve(host)
    except OSError as exc:
        raise Failed("unreachable") from exc
    if not addresses or not all(_public(address) for address in addresses):
        raise Failed("refused")
    with httpx.Client(timeout=httpx.Timeout(SEND_SECONDS, connect=5.0), follow_redirects=False,
                      trust_env=False) as client:
        answer = client.post(url, content=body, headers=headers)
    return answer.status_code, retry_after(answer.headers.get("retry-after"))


#: What sends to a push service; the tests put a recorder in its place (nothing goes into the network). It answers
#: the status, or the status and the seconds of ``Retry-After``.
POST: Callable[[str, bytes, dict[str, str]], int | tuple[int, float | None]] = _post


def deliver(db: Session, row: WebPushSubscription, plaintext: bytes, topic: str = "") -> None:
    """One message to one browser. Only a 2xx answer counts as delivered. Raises ``Gone`` (the row is to go),
    ``Refused`` (the message itself is wrong, give it up) or ``Failed`` (try again later)."""
    headers = {"Authorization": vapid_header(server_key(db), row.endpoint, subject(db)),
               "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(TTL_SECONDS),
               "Urgency": "normal"}
    if topic:
        headers["Topic"] = "".join(char for char in topic if char.isalnum() or char in "-_")[:32]
    try:
        answer = POST(row.endpoint, encrypt(plaintext, row.p256dh, row.auth), headers)
    except Failed:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise Failed(type(exc).__name__.lower()[:40]) from exc
    except ValueError as exc:
        raise Failed("bad_keys") from exc
    status, wait = answer if isinstance(answer, tuple) else (answer, None)
    if status in (404, 410):
        raise Gone
    if 200 <= status < 300:
        return
    if status in (400, 413):
        raise Refused(f"http_{status}")
    # A redirect was not followed, so nothing arrived; anything else may pass (429, 5xx).
    raise Failed(f"http_{status}", wait)


_BROWSERS = (("Edg/", "Edge"), ("EdgA/", "Edge"), ("EdgiOS/", "Edge"), ("OPR/", "Opera"),
             ("SamsungBrowser/", "Samsung Internet"), ("Firefox/", "Firefox"), ("FxiOS/", "Firefox"),
             ("CriOS/", "Chrome"), ("Chrome/", "Chrome"), ("Chromium/", "Chromium"), ("Safari/", "Safari"))
_SYSTEMS = (("iPhone", "iOS"), ("iPad", "iPadOS"), ("Android", "Android"), ("Windows", "Windows"),
            ("CrOS", "ChromeOS"), ("Mac OS X", "macOS"), ("Macintosh", "macOS"), ("Linux", "Linux"))


def describe(user_agent: str) -> tuple[str, str]:
    """Browser and system from a ``User-Agent`` (``("Chrome", "Windows")``); empty where it says nothing known."""
    browser = next((name for mark, name in _BROWSERS if mark in user_agent), "")
    system = next((name for mark, name in _SYSTEMS if mark in user_agent), "")
    return browser, system
