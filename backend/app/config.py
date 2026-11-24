"""Settings from the environment, prefix ``NEXTIME_``.

What the operator changes at runtime (log level and, later, sign-in rules and backups) lives in the database, see
``services/settings_service.py``. Only what must be known before the first start is here.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import PrivateAttr, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

#: The log levels ``NEXTIME_LOG_LEVEL`` takes: the interface's four, and the usual names for three of them.
LOG_LEVELS = ("quiet", "normal", "detailed", "trace")
LOG_LEVEL_ALIASES = {"warning": "quiet", "warn": "quiet", "info": "normal", "debug": "detailed"}
#: Bounds of the numbers, (lowest, highest). Below or above them nextime does not start.
BOUNDS = {
    "session_days": (1, 3650),
    "argon2_time": (1, 20),
    "argon2_memory_kib": (1024, 4 * 1024 * 1024),
    "argon2_parallelism": (1, 16),
}
_COOKIE_SUFFIX = re.compile(r"^[A-Za-z0-9_]{0,32}$")


def _not_allowed(allowed: str) -> PydanticCustomError:
    """A refusal that ``_explain`` turns into "Allowed: <allowed>"."""
    return PydanticCustomError("not_allowed", "not allowed", {"allowed": allowed})


def _web_address(value: str, *, path: bool) -> bool:
    """``http://`` or ``https://`` with a host (and a port that is a number); without a path unless ``path``."""
    parts = urlsplit(value)
    try:
        parts.port  # noqa: B018 - raises ValueError when the port is not a number
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return False
    return path or (parts.path in ("", "/") and not parts.query and not parts.fragment)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXTIME_",
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    #: Database, attachments, logs, backups and the operator's own language files.
    data_dir: Path = PROJECT_DIR / "data"
    #: Extra languages as JSON files, one per language (``es.json``). Empty: ``<data_dir>/locales``.
    locales_dir: Path | None = None
    disable_background: bool = False
    #: Where the update check asks for the newest release. Empty: GitHub's API. The tests point it elsewhere.
    update_url: str = ""
    frontend_dist: Path = PROJECT_DIR / "frontend" / "dist"
    #: Overrides the stored log level; the emergency exit when the app does not even start.
    log_level: str = ""
    #: What the box "State" asks to tell whether the server reaches the internet (TIME-1 AC-9). Empty: it does not
    #: ask and says the internet is not reachable.
    internet_probe_url: str = "https://api.github.com"
    #: Serves /api/docs and /api/openapi.json. Off by default.
    api_docs: bool = False
    #: Signs the sign-in attempt cookie of OIDC and encrypts what the server must read on its own (the OIDC client
    #: secret, the mail password). Empty: a random key in ``<data_dir>/secret.key``, made at the first start.
    secret_key: str = ""
    #: A browser session ends after this many days, whatever happens.
    session_days: int = 30
    #: Argon2id for passwords. The tests lower the cost.
    argon2_time: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 2
    #: ``auto``: the session cookie is ``Secure`` when the request came over HTTPS (or a trusted proxy says so).
    cookie_secure: Literal["auto", "true", "false"] = "auto"
    #: The address people reach nextime under, for links in invitations and the OIDC return. Empty: the address of
    #: the request. The setting in the interface wins over this.
    public_url: str = ""
    #: Appended to every cookie name. Two instances on one host under different ports share their cookies (a browser
    #: does not tell ports apart); a suffix keeps their sign-ins apart. Letters, digits and "_" only.
    cookie_suffix: str = ""
    #: Addresses or networks of reverse proxies whose ``X-Forwarded-For`` and ``X-Forwarded-Proto`` may be believed,
    #: comma separated. Empty: the headers are ignored, so that nobody dodges the sign-in brake with made-up
    #: addresses or makes the app see a scheme it was not reached with.
    trusted_proxies: str = ""
    #: The code the first account must bring. Empty: nextime makes one at every start until set up and writes it to
    #: the log, so that whoever reaches a fresh instance first does not become its operator.
    setup_token: str = ""
    #: Addresses or networks the operator's settings may be changed from, comma separated (e.g. 192.168.0.0/16).
    #: Empty: from anywhere. Behind a reverse proxy only with ``trusted_proxies`` set.
    operator_networks: str = ""
    #: The calendar account answers over HTTPS only, so a device password never crosses the network in the clear
    #: (TIME-20). For development and tests only: true lets it answer over plain HTTP too.
    dav_allow_http: bool = False
    #: The address search the place field asks while typing (TIME-8): a Photon service (OpenStreetMap data). Only the
    #: typed place goes there. Another one (an own Photon) here; it is the operator's choice, so it may be in the home
    #: network.
    places_url: str = "https://photon.komoot.io/api/"
    #: The map tiles the small map of a place is drawn from, by the server (TIME-8), ``{z}/{x}/{y}`` in it.
    tiles_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    #: The push relay the iPhone app's silent nudges go through (TIME-29): it sees only the device's ticket and what is
    #: encrypted for the device. Empty: nothing goes to iPhones and iPads.
    push_relay: str = "https://push.nexapps.dev"

    @field_validator("data_dir", "frontend_dist", "locales_dir")
    @classmethod
    def _relative_to_project(cls, value: Path | None) -> Path | None:
        # A relative path means the project, not whatever directory the process was started from.
        if value is None or value == Path(""):
            return None
        return value if value.is_absolute() else PROJECT_DIR / value

    @field_validator(*BOUNDS)
    @classmethod
    def _within_bounds(cls, value: int, info: object) -> int:
        lowest, highest = BOUNDS[info.field_name]  # type: ignore[attr-defined]
        if not lowest <= value <= highest:
            raise _not_allowed(f"a whole number from {lowest} to {highest}")
        return value

    @field_validator("trusted_proxies", "operator_networks")
    @classmethod
    def _networks(cls, value: str) -> str:
        # A typo here must stop the start: skipped, a broken entry left the operator's pages open to everybody.
        for entry in value.split(","):
            if not entry.strip():
                continue
            try:
                ipaddress.ip_network(entry.strip(), strict=False)
            except ValueError:
                raise _not_allowed(
                    "empty, or addresses and networks separated by commas, such as 192.168.1.0/24, 10.0.0.5 or "
                    "fd00::/8"
                ) from None
        return value

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, value: str) -> str:
        if value.strip() and value.strip().lower() not in (*LOG_LEVELS, *LOG_LEVEL_ALIASES):
            raise _not_allowed("empty (the level set in the interface), " + ", ".join(LOG_LEVELS)
                               + " (or warning, info, debug)")
        return value

    @field_validator("public_url")
    @classmethod
    def _public_url(cls, value: str) -> str:
        if value.strip() and not _web_address(value.strip(), path=False):
            raise _not_allowed("empty, or an address with http:// or https:// and a host, without a path, such as "
                               "https://time.example.com")
        return value

    @field_validator("update_url", "internet_probe_url", "push_relay")
    @classmethod
    def _update_url(cls, value: str) -> str:
        if value.strip() and not _web_address(value.strip(), path=True):
            raise _not_allowed("empty, or an address with http:// or https://")
        return value

    @field_validator("cookie_suffix")
    @classmethod
    def _cookie_suffix(cls, value: str) -> str:
        if not _COOKIE_SUFFIX.match(value):
            raise _not_allowed("letters, digits and _ only, at most 32 characters")
        return value

    @model_validator(mode="after")
    def _defaults_inside_data(self) -> Settings:
        if self.locales_dir is None:
            self.locales_dir = self.data_dir / "locales"
        return self

    _remembered_key: str = PrivateAttr(default="")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "nextime.db"

    def cookie_name_suffix(self) -> str:
        """The suffix; letters, digits and "_" only (checked at start, a cookie name allows little)."""
        return "".join(ch for ch in self.cookie_suffix if ch.isascii() and (ch.isalnum() or ch == "_"))

    def resolved_secret_key(self) -> str:
        """``NEXTIME_SECRET_KEY``, else ``secret.key`` in the data folder, made once with a random value."""
        if self.secret_key:
            return self.secret_key
        if self._remembered_key:
            return self._remembered_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key_file = self.data_dir / "secret.key"
        if key_file.exists():
            self._remembered_key = key_file.read_text(encoding="utf-8").strip()
        else:
            self._remembered_key = secrets.token_urlsafe(48)
            # Made only the owner's from the first byte: written first and narrowed after, it stood open a moment.
            descriptor = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(self._remembered_key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return self._remembered_key


#: What each kind of value may be, for the message when a variable holds something else.
_ALLOWED = {
    "int_parsing": "a whole number",
    "bool_parsing": "true or false",
    "literal_error": None,
    "path_type": "a folder path",
}


class StartupError(SystemExit):
    """nextime cannot run like this; the log says why. Raised before anything is half started."""


def _explain(error: ValidationError) -> list[str]:
    lines = []
    for item in error.errors():
        field = str(item.get("loc", ("?",))[0])
        variable = "NEXTIME_" + field.upper()
        value = item.get("input")
        kind = str(item.get("type"))
        allowed = (item.get("ctx") or {}).get("allowed") if kind == "not_allowed" else _ALLOWED.get(kind)
        if allowed is None:
            expected = (item.get("ctx") or {}).get("expected", "")
            allowed = str(expected).replace("'", "") or "see the README"
        lines.append(f"{variable} has the value {value!r}, which is not valid. Allowed: {allowed}.")
    return lines


@dataclass(frozen=True)
class ServerZone:
    """The server's time zone: what backups and deadlines go by (TIME-1 AC-2, EC-3), and its name for the page."""

    zone: tzinfo
    name: str


def _zone_name_from_system() -> str:
    """The IANA name of the system's zone: ``TZ`` (Docker sets it), ``/etc/timezone``, the ``/etc/localtime`` link."""
    candidates = [os.environ.get("TZ", "").lstrip(":")]
    try:
        candidates.append(Path("/etc/timezone").read_text(encoding="utf-8").strip())
    except OSError:
        pass
    try:
        target = os.readlink("/etc/localtime")
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in candidates:
        if not name:
            continue
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
        return name
    return ""


def server_zone() -> ServerZone:
    name = _zone_name_from_system()
    if name:
        return ServerZone(ZoneInfo(name), name)
    local = datetime.now().astimezone()
    return ServerZone(local.tzinfo or ZoneInfo("UTC"), local.tzname() or "UTC")


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        logger = logging.getLogger("nextime")
        if not logging.getLogger().handlers:
            logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
        for line in _explain(error):
            logger.critical(line)
        raise StartupError(2) from error
