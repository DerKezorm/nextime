"""Places of events (TIME-8): address suggestions while typing, and the small map of a place.

**Only the typed place goes out** (AC-5). The server asks, not the browser: the address service sees the server and
the text typed into the place field, never a title, a time, notes, a person or a session. Looking at an event asks
nobody: its map comes from its stored point (``GEO``, Apple's place) or from a place typed here before, whose point
the server keeps from the suggestions (review of TIME-8, finding 2). The service is Photon
(OpenStreetMap data, made for searching while typing; Nominatim's rules forbid that), ``NEXTIME_PLACES_URL`` names
another one. The map is drawn by the server from OpenStreetMap tiles (``NEXTIME_TILES_URL``) with a marker in slate:
the browser never talks to a foreign service, and the page keeps its strict content policy.

**Switched off by the operator** (AC-6, ``address_suggestions``): nothing is asked, neither suggestions nor a map;
the place stays free text, and "Open in Maps" still works (the page builds that link).

**Never a door into the network.** Every call has a deadline (connect 3 s, everything 5 s), follows no redirect, takes
no proxy from the environment, sends no cookie and a plain name for itself. To the services nextime brings along
(Photon, OpenStreetMap) it goes only over https and only to public addresses after the name was resolved (as Web
Push, ``webpush._public``), and connects to the address it checked (the certificate still for the name).
An address the operator set (an own Photon) may be in the home network: the operator chose it, nobody typing a
place can change it.

**Braked** (review of TIME-8, finding 1): an account gets ``MAPS_PER_MINUTE`` maps and ``SUGGESTIONS_PER_MINUTE``
suggestions a minute (then 429 ``places_busy``), and the whole server sends at most ``OUT_PER_MINUTE`` calls a minute
to each service, so that the fair use of Photon and of the OpenStreetMap tiles holds; tiles and maps are kept.

**When a service fails** (EC-1, EC-3): unreachable, slow, nonsense, another version of its answers: there are no
suggestions or no map, everything else goes on, and the log says so now and then (never what was typed).
"""

from __future__ import annotations

import io
import logging
import math
import re
import socket
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..errors import error
from . import settings_service, webpush

logger = logging.getLogger("nextime.places")

DEFAULT_PLACES = "https://photon.komoot.io/api/"
DEFAULT_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
CONNECT_SECONDS = 3.0
TOTAL_SECONDS = 5.0
#: Suggestions from three characters on, at most five (AC-1).
MIN_CHARS = 3
MAX_RESULTS = 5
MAX_QUERY = 200
#: The longest answer read from a service.
MAX_ANSWER = 2 * 1024 * 1024
#: The map: its size in pixels and its zoom.
MAP_WIDTH = 640
MAP_HEIGHT = 256
ZOOM = 16
TILE = 256
#: How often the log says a service does not answer.
WARN_EVERY = 600.0
#: The brakes: per account and minute, and per service for the whole server.
MAPS_PER_MINUTE = 60
SUGGESTIONS_PER_MINUTE = 120
OUT_PER_MINUTE = {"address": 300, "tiles": 600}
#: How much is kept: tiles (about 20 kB each), maps (about 30 kB), suggestions and points of typed places.
TILES_KEPT = 1024
MAPS_KEPT = 256
SUGGESTIONS_KEPT = 1000
POINTS_KEPT = 5000
AGENT = f"nextime/{__version__} (self-hosted calendar)"


class Unavailable(Exception):
    """The service did not answer usefully; ``reason`` for the log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _resolve(host: str) -> list[str]:
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


#: How a name becomes addresses, and the transport of every call; the tests put their own here (nothing of a test
#: reaches the network).
resolve: Callable[[str], list[str]] = _resolve
transport: httpx.BaseTransport | None = None

_warned: dict[str, float] = {}
_cache_lock = threading.Lock()
_suggestions: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
_points: OrderedDict[str, dict[str, float] | None] = OrderedDict()
_tiles: OrderedDict[str, bytes] = OrderedDict()
_maps: OrderedDict[tuple[float, float], bytes] = OrderedDict()
_brake_lock = threading.Lock()
_calls: dict[tuple[str, int | str], deque[float]] = {}

#: The clock of the brakes; the tests put their own here.
monotonic: Callable[[], float] = time.monotonic


def forget() -> None:
    with _cache_lock:
        for cache in (_suggestions, _points, _tiles, _maps):
            cache.clear()
    with _brake_lock:
        _calls.clear()
    _warned.clear()


def _wait(kind: str, who: int | str, limit: int) -> int:
    """Counts a call of ``who`` against ``limit`` a minute: 0 when it may go, else the seconds until it may."""
    now = monotonic()
    with _brake_lock:
        calls = _calls.setdefault((kind, who), deque())
        while calls and calls[0] <= now - 60.0:
            calls.popleft()
        if len(calls) >= limit:
            return max(1, math.ceil(calls[0] + 60.0 - now))
        calls.append(now)
        return 0


class Busy(Exception):
    """An account asked too often; ``seconds`` until it may again."""

    def __init__(self, seconds: int) -> None:
        super().__init__(seconds)
        self.seconds = seconds


def _braked(kind: str, account_id: int | None, limit: int) -> None:
    if account_id is None:
        return
    seconds = _wait(kind, account_id, limit)
    if seconds:
        raise Busy(seconds)


def _keep(cache: OrderedDict, key: Any, value: Any, size: int) -> None:
    with _cache_lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > size:
            cache.popitem(last=False)


def _kept(cache: OrderedDict, key: Any) -> Any:
    with _cache_lock:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
    return _MISSING


_MISSING = object()


def _warn(what: str, reason: str) -> None:
    now = time.monotonic()
    if now - _warned.get(what, -WARN_EVERY * 2) >= WARN_EVERY:
        _warned[what] = now
        logger.warning("The %s service gave no usable answer reason=%s", what, reason)


def on(db: Session) -> bool:
    return bool(settings_service.get(db, "address_suggestions"))


def _checked(url: str, default: str) -> str:
    """The address of a call, as long as it may be called: https to public addresses for the services nextime brings
    along; what the operator set is the operator's."""
    return _checked_at(url, default)[0]


def _checked_at(url: str, default: str) -> tuple[str, str]:
    """``_checked`` and, for the services nextime brings along, the checked address to connect to."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise Unavailable("address")
    if urlsplit(default).hostname != parts.hostname:
        return url, ""
    if parts.scheme != "https" or parts.port not in (None, 443):
        raise Unavailable("address")
    try:
        found = resolve(parts.hostname)
    except OSError as exc:
        raise Unavailable("dns") from exc
    if not found or not all(webpush._public(address) for address in found):
        raise Unavailable("not_public")
    return url, found[0]


def _at(url: str, address: str) -> str:
    """``url`` with the checked address in place of the name: no second lookup between the check and the call."""
    parts = urlsplit(url)
    host = f"[{address}]" if ":" in address else address
    return urlunsplit(parts._replace(netloc=host + (f":{parts.port}" if parts.port else "")))


def _get(url: str, default: str, params: dict[str, str] | None = None, accept: str = "application/json",
         service: str = "address") -> bytes:
    target, address = _checked_at(url, default)
    if _wait("out", service, OUT_PER_MINUTE[service]):
        raise Unavailable("busy")
    headers = {"User-Agent": AGENT, "Accept": accept}
    extensions: dict[str, Any] = {}
    if address:
        name = urlsplit(target).hostname or ""
        target = _at(target, address)
        headers["Host"] = name
        extensions["sni_hostname"] = name
    try:
        with httpx.Client(timeout=httpx.Timeout(TOTAL_SECONDS, connect=CONNECT_SECONDS), follow_redirects=False,
                          trust_env=False, transport=transport) as client:
            started = time.monotonic()
            with client.stream("GET", target, params=params, headers=headers, extensions=extensions) as answer:
                if answer.status_code != 200:
                    raise Unavailable(f"http_{answer.status_code}")
                body = bytearray()
                for chunk in answer.iter_bytes():
                    body += chunk
                    if len(body) > MAX_ANSWER:
                        raise Unavailable("too_large")
                    if time.monotonic() - started > TOTAL_SECONDS:
                        raise Unavailable("timeout")
                return bytes(body)
    except httpx.TimeoutException as exc:
        raise Unavailable("timeout") from exc
    except httpx.HTTPError as exc:
        raise Unavailable(type(exc).__name__.lower()[:40]) from exc


# --- Suggestions (AC-1, AC-5, EC-3, EC-5) ----------------------------------------------------------------------


def clean_query(raw: str) -> str:
    return " ".join((raw or "").split())[:MAX_QUERY]


def _text(value: Any) -> str:
    return " ".join(str(value).split())[:200] if isinstance(value, str | int | float) else ""


def _suggestion(feature: Any) -> dict[str, Any] | None:
    """One Photon feature as a suggestion: the address, below it the place's name where it has one."""
    if not isinstance(feature, dict):
        return None
    geometry, properties = feature.get("geometry"), feature.get("properties")
    if not isinstance(geometry, dict) or not isinstance(properties, dict):
        return None
    point = geometry.get("coordinates")
    if not isinstance(point, list) or len(point) < 2:
        return None
    try:
        lon, lat = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or math.isnan(lat) or math.isnan(lon):
        return None
    get = {key: _text(properties.get(key, "")) for key in ("name", "street", "housenumber", "postcode", "city",
                                                           "district", "state", "country")}
    street = " ".join(part for part in (get["street"], get["housenumber"]) if part)
    town = " ".join(part for part in (get["postcode"], get["city"] or get["district"]) if part)
    address = ", ".join(part for part in (street, town) if part)
    name = get["name"] if get["name"] and get["name"] != get["street"] else ""
    if not address:
        address = ", ".join(part for part in (name, get["state"], get["country"]) if part)
        name = ""
    if not address:
        return None
    value = f"{name}, {address}" if name else address
    return {"address": address, "name": name, "value": value, "lat": round(lat, 6), "lon": round(lon, 6)}


def _ask(query: str, language: str, limit: int) -> list[dict[str, Any]]:
    settings = get_settings()
    body = _get(settings.places_url or DEFAULT_PLACES, DEFAULT_PLACES,
                {"q": query, "limit": str(limit), "lang": language})
    try:
        import json

        answer = json.loads(body)
    except ValueError as exc:
        raise Unavailable("not_json") from exc
    features = answer.get("features") if isinstance(answer, dict) else None
    if not isinstance(features, list):
        raise Unavailable("shape")
    found: list[dict[str, Any]] = []
    for feature in features:
        item = _suggestion(feature)
        if item is not None and all(item["value"] != other["value"] for other in found):
            found.append(item)
        if len(found) >= limit:
            break
    return found


def suggest(db: Session, raw: str, language: str, account_id: int | None = None) -> dict[str, Any]:
    """``GET /api/places``: up to five addresses for the typed text; ``unavailable`` when the service did not answer
    (the place stays free text, EC-3). Where the typed text and each suggestion lie is kept for ``lookup``."""
    if not on(db):
        raise error("places_off", "Address suggestions are switched off.", 409)
    _braked("suggest", account_id, SUGGESTIONS_PER_MINUTE)
    query = clean_query(raw)
    language = "de" if language.lower().startswith("de") else "en"
    if len(query) < MIN_CHARS:
        return {"suggestions": [], "unavailable": False}
    key = (query.casefold(), language)
    kept = _kept(_suggestions, key)
    if kept is not _MISSING:
        return {"suggestions": kept, "unavailable": False}
    try:
        found = _ask(query, language, MAX_RESULTS)
    except Unavailable as exc:
        _warn("address", exc.reason)
        return {"suggestions": [], "unavailable": True}
    _keep(_suggestions, key, found, SUGGESTIONS_KEPT)
    _keep(_points, query.casefold(), {"lat": found[0]["lat"], "lon": found[0]["lon"]} if found else None,
          POINTS_KEPT)
    for item in found:
        _keep(_points, item["value"].casefold(), {"lat": item["lat"], "lon": item["lon"]}, POINTS_KEPT)
    return {"suggestions": found, "unavailable": False}


_ADDRESS_LIKE = re.compile(r"\d")


def looks_like_address(text: str) -> bool:
    """A place worth a map without a point: a number in it and two words at least ("Lindenstraße 4", not "bei Oma")."""
    words = [word for word in re.split(r"[\s,]+", text) if word]
    return bool(_ADDRESS_LIKE.search(text)) and len(words) >= 2 and not re.match(r"(?i)^[a-z][a-z0-9+.-]*:", text)


def lookup(db: Session, raw: str, language: str = "") -> dict[str, Any]:
    """``GET /api/places/lookup``: where a place without a point lies (typed here without choosing a suggestion), only
    for text that looks like an address (EC-2), and only from what the suggestions for typed places brought: looking
    at an event sends nothing out (AC-5, review of TIME-8, finding 2)."""
    if not on(db):
        raise error("places_off", "Address suggestions are switched off.", 409)
    query = clean_query(raw)
    if not looks_like_address(query):
        return {"place": None, "unavailable": False}
    kept = _kept(_points, query.casefold())
    return {"place": None if kept is _MISSING else kept, "unavailable": False}


# --- The map (AC-2, EC-1) ----------------------------------------------------------------------------------------


def _pixel(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    scale = TILE * (2 ** zoom)
    x = (lon + 180.0) / 360.0 * scale
    lat = max(min(lat, 85.0511), -85.0511)
    rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(rad) + 1.0 / math.cos(rad)) / math.pi) / 2.0 * scale
    return x, y


def _tile(z: int, x: int, y: int) -> Any:
    from PIL import Image

    count = 2 ** z
    key = f"{z}/{x % count}/{y}"
    data = _kept(_tiles, key)
    if data is _MISSING:
        settings = get_settings()
        template = settings.tiles_url or DEFAULT_TILES
        url = template.replace("{z}", str(z)).replace("{x}", str(x % count)).replace("{y}", str(y))
        data = _get(url, DEFAULT_TILES, accept="image/png,image/*", service="tiles")
        _keep(_tiles, key, data, TILES_KEPT)
    try:
        with Image.open(io.BytesIO(data)) as picture:
            if picture.size != (TILE, TILE):
                raise Unavailable("tile_size")
            return picture.convert("RGB")
    except Unavailable:
        raise
    except Exception as exc:
        raise Unavailable("tile") from exc


def map_image(db: Session, lat: float, lon: float, account_id: int | None = None) -> bytes:
    """The small map around a place with a marker (WebP). ``Unavailable`` when a tile does not come (EC-1)."""
    if not on(db):
        raise error("places_off", "Address suggestions are switched off.", 409)
    _braked("map", account_id, MAPS_PER_MINUTE)
    from PIL import Image, ImageDraw

    key = (round(lat, 5), round(lon, 5))
    kept = _kept(_maps, key)
    if kept is not _MISSING:
        return kept
    center_x, center_y = _pixel(key[0], key[1], ZOOM)
    left, top = center_x - MAP_WIDTH / 2, center_y - MAP_HEIGHT / 2
    canvas = Image.new("RGB", (MAP_WIDTH, MAP_HEIGHT), (230, 230, 236))
    for tile_x in range(int(left // TILE), int((left + MAP_WIDTH) // TILE) + 1):
        for tile_y in range(int(top // TILE), int((top + MAP_HEIGHT) // TILE) + 1):
            if not 0 <= tile_y < 2 ** ZOOM:
                continue
            canvas.paste(_tile(ZOOM, tile_x, tile_y), (round(tile_x * TILE - left), round(tile_y * TILE - top)))
    draw = ImageDraw.Draw(canvas)
    middle_x, middle_y = MAP_WIDTH / 2, MAP_HEIGHT / 2
    draw.ellipse((middle_x - 11, middle_y - 11, middle_x + 11, middle_y + 11), fill=(255, 255, 255))
    draw.ellipse((middle_x - 8, middle_y - 8, middle_x + 8, middle_y + 8), fill=(74, 100, 145))
    out = io.BytesIO()
    canvas.save(out, "WEBP", quality=80, method=4)
    data = out.getvalue()
    _keep(_maps, key, data, MAPS_KEPT)
    return data
