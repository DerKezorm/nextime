"""Profile pictures, as in the other nex apps: what comes in is a picture of any kind a phone or camera makes; what is
kept is a square WebP of ``SIZE`` pixels, drawn anew. Nothing of the file that came survives but its pixels: no place,
no device, no hidden second picture, no script in an SVG (an SVG is not taken at all).

Kept in the database next to the account (a few kilobytes), so every backup carries it, and an account deleted for
good takes it along.

Seen by the account itself and the operator (its list of accounts). Whoever shares a calendar with the account sees it
too: sharing (TIME-19) adds its rule to ``SEE_RULES`` (TIME-2 AC-5).
"""

from __future__ import annotations

import io
import logging
import struct
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..models import OPERATOR, Account

logger = logging.getLogger("nextime.accounts")

SIZE = 256
#: Larger than any photo a phone takes; above it the file is not even opened.
MAX_BYTES = 20 * 1024 * 1024
#: Pixels of the picture that came (a 1-bit PNG can be tiny on disk and huge in memory).
MAX_PIXELS = 50_000_000
#: What phones and cameras make. The decoder opens more (TIFF, PSD, EPS, which would start Ghostscript): not here.
KINDS = {"jpeg", "png", "webp", "gif", "bmp", "heic", "avif"}
_AVIF = {b"avif", b"avis"}
_HEIF = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}

#: The English sentence for each code, for whoever uses the API directly; the page builds its own from the code.
MESSAGES = {
    "avatar_too_large": "The picture is too large.",
    "avatar_not_a_picture": "That is not a picture nextime accepts (JPEG, PNG, WebP, GIF, HEIC or AVIF).",
}

#: Who else may see the picture of ``owner_id``: ``rule(db, viewer, owner_id)`` is true for a viewer it lets in.
#: Sharing (TIME-19) adds "shares a calendar with it".
SeeRule = Callable[[Session, Account, int], bool]
SEE_RULES: list[SeeRule] = []


class AvatarError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = MESSAGES[code]


def sniff(head: bytes) -> str | None:
    """The kind of a file by its first bytes, never by its name."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"BM") and len(head) > 14:
        return "bmp"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        size = struct.unpack(">I", head[:4])[0]
        brands = {head[8:12]} | {head[at : at + 4] for at in range(16, min(size, len(head)) - 3, 4)}
        if brands & _AVIF:
            return "avif"
        if brands & _HEIF:
            return "heic"
    return None


def make(data: bytes) -> bytes:
    """The square WebP of a picture, upright and cut to its middle. ``AvatarError`` for anything else."""
    if len(data) > MAX_BYTES:
        raise AvatarError("avatar_too_large")
    if sniff(data[:64]) not in KINDS:
        raise AvatarError("avatar_not_a_picture")
    try:
        import pillow_heif
        from PIL import Image, ImageOps

        pillow_heif.register_heif_opener()
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise AvatarError("avatar_too_large")
            upright = ImageOps.exif_transpose(image)
            square = ImageOps.fit(upright.convert("RGBA"), (SIZE, SIZE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            square.save(out, "WEBP", quality=85, method=4, exif=b"", xmp=b"")
            return out.getvalue()
    except AvatarError:
        raise
    except Exception as exc:
        # A broken or hostile picture fails in the decoder's own way.
        logger.info("A profile picture could not be read: %s", type(exc).__name__)
        raise AvatarError("avatar_not_a_picture") from exc


def may_see(db: Session, viewer: Account, owner_id: int) -> bool:
    """The account itself, the operator, and whoever a rule of ``SEE_RULES`` lets in."""
    if viewer.id == owner_id or viewer.role == OPERATOR:
        return True
    return any(rule(db, viewer, owner_id) for rule in SEE_RULES)
