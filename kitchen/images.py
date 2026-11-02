"""Recipe image storage under ``$DATA_DIR/images``.

Every stored file gets a server-generated name and is re-encoded as JPEG, so
nothing a client or a scraped page sent us is ever served back byte for byte.
"""

from __future__ import annotations

import io
import os
import uuid

from helpers import setup_logger
from kitchen.db import IMAGES_DIR

logger = setup_logger(__name__)

MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_SIDE = 1600

_SIGNATURES = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),
    (b"GIF8", "gif"),
)


class ImageError(ValueError):
    pass


def sniff(data: bytes) -> str | None:
    for magic, kind in _SIGNATURES:
        if data.startswith(magic):
            if kind == "webp" and data[8:12] != b"WEBP":
                continue
            return kind
    return None


def _reencode(data: bytes) -> bytes:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        # Without Pillow we can still refuse anything that is not a JPEG.
        if sniff(data) != "jpeg":
            raise ImageError("Only JPEG images are supported on this server")
        return data
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue()
    except ImageError:
        raise
    except Exception as exc:
        raise ImageError(f"Not a readable image: {type(exc).__name__}") from exc


def store_image_bytes(data: bytes) -> str:
    """Validate, re-encode and store. Returns the stored filename."""
    if not data:
        raise ImageError("Empty image")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError("Image is larger than 15 MB")
    if sniff(data) is None:
        raise ImageError("Unsupported image type")
    encoded = _reencode(data)
    os.makedirs(IMAGES_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    with open(os.path.join(IMAGES_DIR, filename), "wb") as fh:
        fh.write(encoded)
    return filename


def store_image_file(path: str) -> str | None:
    """Store an image from a local path (pipeline artifacts). None on failure."""
    try:
        if not path or not os.path.isfile(path):
            return None
        if os.path.getsize(path) > MAX_IMAGE_BYTES:
            return None
        with open(path, "rb") as fh:
            return store_image_bytes(fh.read())
    except (OSError, ImageError) as exc:
        logger.info("[Kitchen] image not stored from %s: %s", path, exc)
        return None


def image_path(filename: str) -> str | None:
    """Absolute path for a stored filename, refusing anything outside IMAGES_DIR."""
    if not filename or os.path.basename(filename) != filename or not filename.endswith(".jpg"):
        return None
    full = os.path.realpath(os.path.join(IMAGES_DIR, filename))
    root = os.path.realpath(IMAGES_DIR)
    if os.path.dirname(full) != root or not os.path.isfile(full):
        return None
    return full


def delete_image(filename: str) -> None:
    full = image_path(filename)
    if full:
        try:
            os.remove(full)
        except OSError:
            pass
