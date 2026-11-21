import io
import unittest

from PIL import Image

from matrix_stickers.images import make_shortcode, to_png, validate_name
from matrix_stickers.packs import updated_pack


class ImageTests(unittest.TestCase):
    def test_conversion_preserves_size_and_transparency(self):
        source = io.BytesIO()
        Image.new("RGBA", (32, 16), (255, 0, 0, 0)).save(source, "WEBP", lossless=True)
        png, width, height = to_png(source.getvalue())
        self.assertEqual((width, height), (32, 16))
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_animated_image_rejected(self):
        data = io.BytesIO()
        frames = [Image.new("RGB", (2, 2), color) for color in ("red", "blue")]
        frames[0].save(data, "GIF", save_all=True, append_images=frames[1:])
        with self.assertRaisesRegex(ValueError, "Animated"):
            to_png(data.getvalue())

    def test_oversized_dimensions_rejected(self):
        data = io.BytesIO()
        Image.new("RGB", (4097, 1)).save(data, "PNG")
        with self.assertRaisesRegex(ValueError, "dimensions"):
            to_png(data.getvalue())

    def test_names_are_normalized_and_bounded(self):
        self.assertEqual(validate_name("  Happy   fox 🦊  "), "Happy fox 🦊")
        for name in (" ", "x" * 81, "bad\x00name"):
            with self.assertRaises(ValueError):
                validate_name(name)

    def test_stable_identifiers(self):
        self.assertEqual(make_shortcode("same"), make_shortcode("same"))
        self.assertNotEqual(make_shortcode("same"), make_shortcode("different"))

    def test_pack_update_preserves_data_and_input(self):
        old = {
            "images": {"old": {"url": "mxc://host/old"}},
            "pack": {"display_name": "Custom", "usage": ["emoticon"]},
            "custom": True,
        }
        result = updated_pack(old, "new", {"url": "mxc://host/new"})
        self.assertIn("old", result["images"])
        self.assertNotIn("new", old["images"])
        self.assertEqual(result["pack"]["usage"], ["emoticon", "sticker"])
        self.assertEqual(old["pack"]["usage"], ["emoticon"])
        self.assertEqual(result["pack"]["display_name"], "Custom")
        self.assertTrue(result["custom"])
