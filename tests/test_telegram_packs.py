import unittest

from matrix_stickers.telegram_packs import sticker_set_name


class PackNameTests(unittest.TestCase):
    def test_names_and_telegram_links(self):
        for value in (
            "FoxPack",
            "https://t.me/addstickers/FoxPack",
            "t.me/addstickers/FoxPack",
            "https://telegram.me/addstickers/FoxPack/",
            "tg://addstickers?set=FoxPack",
        ):
            with self.subTest(value=value):
                self.assertEqual(sticker_set_name(value), "FoxPack")

    def test_invalid_urls_and_names_rejected(self):
        for value in (
            "",
            "bad name",
            "https://evil.example/addstickers/FoxPack",
            "https://t.me/addemoji/FoxPack",
            "https://user:password@t.me/addstickers/FoxPack",
            "tg://other?set=FoxPack",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sticker_set_name(value)
