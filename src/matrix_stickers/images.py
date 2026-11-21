"""Static sticker conversion and stable identifiers."""

import hashlib
import io
import re

from PIL import Image

MAX_STICKER_BYTES = 10 * 1024 * 1024
MAX_NAME_LENGTH = 80


def make_shortcode(file_unique_id: str) -> str:
    return "tg_" + hashlib.sha256(file_unique_id.encode()).hexdigest()[:20]


def validate_shortcode(value: str) -> str:
    value = value.strip()
    if value.startswith(":") and value.endswith(":"):
        value = value[1:-1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Sticker IDs need 1–100 letters, numbers, underscores or hyphens")
    return value


def validate_name(name: str) -> str:
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Sticker names must contain 1–{MAX_NAME_LENGTH} characters")
    if any(ord(character) < 32 for character in name):
        raise ValueError("Sticker names cannot contain control characters")
    return name


def to_png(data: bytes) -> tuple[bytes, int, int]:
    if len(data) > MAX_STICKER_BYTES:
        raise ValueError("Sticker file is too large")
    with Image.open(io.BytesIO(data)) as source:
        if getattr(source, "is_animated", False):
            raise ValueError("Animated stickers are not supported")
        if source.width > 4096 or source.height > 4096 or source.width * source.height > 16_777_216:
            raise ValueError("Sticker dimensions are too large")
        source.load()
        converted = source.convert("RGBA")
        output = io.BytesIO()
        converted.save(output, format="PNG", optimize=True)
        return output.getvalue(), converted.width, converted.height
