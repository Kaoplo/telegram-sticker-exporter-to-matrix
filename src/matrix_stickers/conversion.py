"""Bounded media conversion in a disposable subprocess."""

import io
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from matrix_stickers.images import MAX_STICKER_BYTES, to_png


@dataclass(frozen=True)
class ConvertedSticker:
    data: bytes
    width: int
    height: int
    mimetype: str = "image/png"
    animated: bool = False


def convert_sticker(
    data: bytes, *, animated: bool = False, video: bool = False
) -> ConvertedSticker:
    if len(data) > MAX_STICKER_BYTES:
        raise ValueError("Sticker file is too large")
    if not animated and not video:
        png, width, height = to_png(data)
        return ConvertedSticker(png, width, height)
    kind = "webm" if video else "tgs"
    with tempfile.TemporaryDirectory(prefix="matrix-sticker-") as directory:
        source = Path(directory) / f"input.{kind}"
        target = Path(directory) / "output.webp"
        source.write_bytes(data)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "matrix_stickers.convert_worker",
                    kind,
                    str(source),
                    str(target),
                ],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ValueError("Animated sticker conversion exceeded 60 seconds") from None
        if result.returncode or not target.exists():
            # The worker only prints controlled error messages, not input data.
            reason = result.stdout.decode("utf-8", errors="replace").strip()[:200]
            raise ValueError(reason or "Animated sticker conversion failed")
        if target.stat().st_size > MAX_STICKER_BYTES:
            raise ValueError("Converted animation is too large")
        output = target.read_bytes()
    with Image.open(io.BytesIO(output)) as image:
        if not getattr(image, "is_animated", False):
            raise ValueError("The animation contained no changing frames")
        return ConvertedSticker(output, image.width, image.height, "image/webp", True)
