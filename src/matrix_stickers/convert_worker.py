"""Isolate native renderers from Telegram polling. Invoked by conversion.py."""

import gzip
import io
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from rlottie_python import LottieAnimation

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_FRAMES = 180
MAX_SECONDS = 3.1


def render_tgs(source: Path, target: Path) -> None:
    with gzip.GzipFile(fileobj=io.BytesIO(source.read_bytes())) as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("TGS animation data is too large")
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("Invalid TGS document")
    width, height = document.get("w", 0), document.get("h", 0)
    fps = document.get("fr", 0)
    start, end = document.get("ip", 0), document.get("op", 0)
    numbers = (width, height, fps, start, end)
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in numbers):
        raise ValueError("Invalid TGS animation dimensions or timing")
    if not (
        0 < width <= 512
        and 0 < height <= 512
        and 0 < fps <= 60
        and 1 < end - start <= MAX_FRAMES
        and (end - start) / fps <= MAX_SECONDS
    ):
        raise ValueError("TGS must be at most 512×512, 60 fps and 3 seconds")

    # Telegram TGS uses vector assets only; reject paths/data URLs before native rendering.
    def validate_assets(value):
        if isinstance(value, dict):
            if "p" in value and "u" in value:
                raise ValueError("External image assets are not supported in TGS stickers")
            for child in value.values():
                validate_assets(child)
        elif isinstance(value, list):
            for child in value:
                validate_assets(child)

    validate_assets(document)
    frames = []
    with LottieAnimation.from_data(json.dumps(document)) as animation:
        count = animation.lottie_animation_get_totalframe()
        if not 1 < count <= MAX_FRAMES:
            raise ValueError("Invalid TGS frame count")
        # Preserve all Telegram frames, including their original frame rate.
        for frame in range(count):
            frames.append(animation.render_pillow_frame(frame_num=frame).convert("RGBA"))
    frames[0].save(
        target,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=round(1000 / fps),
        loop=0,
        lossless=True,
        method=2,
    )


def render_webm(source: Path, target: Path) -> None:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise ValueError("Video sticker conversion requires FFmpeg; use the provided Docker image")
    # Force libvpx to preserve VP9 alpha, restrict protocols and cap output duration/frames.
    result = subprocess.run(
        [
            executable,
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-c:v",
            "libvpx-vp9",
            "-i",
            str(source),
            "-an",
            "-t",
            "3.1",
            "-vf",
            "scale=512:512:force_original_aspect_ratio=decrease,fps=30",
            "-frames:v",
            "94",
            "-c:v",
            "libwebp_anim",
            "-lossless",
            "1",
            "-loop",
            "0",
            "-threads",
            "1",
            "-y",
            str(target),
        ],
        capture_output=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise ValueError("FFmpeg could not decode this WebM sticker")


def main() -> None:
    kind, source, target = sys.argv[1:]
    try:
        if kind == "tgs":
            render_tgs(Path(source), Path(target))
        elif kind == "webm":
            render_webm(Path(source), Path(target))
        else:
            raise ValueError("Unsupported animation format")
    except ValueError as exc:
        print(str(exc)[:200])
        raise SystemExit(1) from None
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        print("Could not convert this animation; check its format, size and renderer dependencies")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
