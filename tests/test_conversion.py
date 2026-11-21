import gzip
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

from matrix_stickers.conversion import convert_sticker
from tests.animation_helpers import moving_tgs


class ConversionTests(unittest.TestCase):
    def test_real_tgs_conversion_preserves_motion_and_transparency(self):
        media = convert_sticker(moving_tgs(), animated=True)
        self.assertEqual(media.mimetype, "image/webp")
        self.assertTrue(media.animated)
        self.assertEqual((media.width, media.height), (64, 64))
        with Image.open(io.BytesIO(media.data)) as image:
            self.assertGreater(image.n_frames, 1)
            first = image.convert("RGBA")
            self.assertEqual(first.getpixel((0, 0))[3], 0)
            image.seek(image.n_frames - 1)
            last = image.convert("RGBA")
            self.assertIsNotNone(ImageChops.difference(first, last).getbbox())
            self.assertAlmostEqual(image.info["duration"], 100, delta=2)

    def test_invalid_tgs_rejected(self):
        for data in (
            b"not gzip",
            gzip.compress(b"not json"),
            gzip.compress(b"x" * (2 * 1024 * 1024 + 1)),
        ):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                convert_sticker(data, animated=True)

    def test_oversized_tgs_dimensions_rejected(self):
        document = json.loads(gzip.decompress(moving_tgs()))
        document["w"] = 100000
        with self.assertRaises(ValueError):
            convert_sticker(gzip.compress(json.dumps(document).encode()), animated=True)

    def test_static_conversion_stays_png(self):
        source = io.BytesIO()
        Image.new("RGBA", (8, 8)).save(source, "WEBP", lossless=True)
        media = convert_sticker(source.getvalue())
        self.assertEqual(media.mimetype, "image/png")
        self.assertFalse(media.animated)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for video conversion")
    def test_real_webm_conversion_preserves_motion(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "sample.webm"
            subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=64x64:rate=10:duration=1",
                    "-c:v",
                    "libvpx-vp9",
                    "-pix_fmt",
                    "yuva420p",
                    "-threads",
                    "1",
                    "-y",
                    str(target),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            media = convert_sticker(target.read_bytes(), video=True)
        self.assertEqual(media.mimetype, "image/webp")
        with Image.open(io.BytesIO(media.data)) as image:
            self.assertGreater(image.n_frames, 1)
            self.assertLessEqual(max(image.size), 512)
