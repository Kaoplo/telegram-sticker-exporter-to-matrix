import io
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

# Never load real credentials or contact a real server in regression tests.
with patch.dict(os.environ, {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_USER_ID": "1",
    "MATRIX_HOMESERVER": "https://matrix.example.org",
    "MATRIX_USER_ID": "@test:example.org",
    "MATRIX_ACCESS_TOKEN": "test-token",
    "MATRIX_PACK_ROOM_ID": "",
}):
    import bot


class MatrixResponseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = bot.MatrixAPI()
        await self.api.close()
        self.response = httpx.Response(200, json={"user_id": bot.MATRIX_USER_ID})
        self.api.client = httpx.AsyncClient(
            base_url="https://matrix.example.org",
            transport=httpx.MockTransport(lambda request: self.response),
        )

    async def asyncTearDown(self):
        await self.api.close()

    async def test_valid_identity(self):
        await self.api.verify_user()

    async def test_empty_or_html_success_explains_configuration(self):
        for body in (b"", b"<html>Website</html>"):
            with self.subTest(body=body):
                self.response = httpx.Response(200, content=body)
                with self.assertRaisesRegex(ValueError, "Check MATRIX_HOMESERVER"):
                    await self.api.verify_user()

    async def test_non_object_json_is_rejected(self):
        self.response = httpx.Response(200, json=[])
        with self.assertRaisesRegex(ValueError, "instead of an object"):
            await self.api.verify_user()

    async def test_missing_identity_is_rejected(self):
        self.response = httpx.Response(200, json={})
        with self.assertRaisesRegex(ValueError, "user_id"):
            await self.api.verify_user()

    async def test_wrong_identity_is_rejected(self):
        self.response = httpx.Response(200, json={"user_id": "@other:example.org"})
        with self.assertRaises(SystemExit):
            await self.api.verify_user()

    async def test_http_error_is_preserved(self):
        self.response = httpx.Response(401, json={"errcode": "M_UNKNOWN_TOKEN"})
        with self.assertRaises(httpx.HTTPStatusError):
            await self.api.verify_user()

    async def test_missing_pack_remains_empty(self):
        self.response = httpx.Response(404, json={"errcode": "M_NOT_FOUND"})
        self.assertEqual(await self.api.get_account_data(bot.PERSONAL_PACK_TYPE), {})
        self.assertEqual(await self.api.get_room_pack(), {})

    async def test_invalid_pack_response_is_not_treated_as_empty(self):
        self.response = httpx.Response(200, content=b"")
        with self.assertRaisesRegex(ValueError, "MATRIX_HOMESERVER"):
            await self.api.get_account_data(bot.PERSONAL_PACK_TYPE)
        with self.assertRaisesRegex(ValueError, "MATRIX_HOMESERVER"):
            await self.api.get_room_pack()

    async def test_invalid_upload_uri_is_rejected(self):
        for payload in ({}, {"content_uri": None}, {"content_uri": "https://example.org"}):
            with self.subTest(payload=payload):
                self.response = httpx.Response(200, json=payload)
                with self.assertRaisesRegex(ValueError, "invalid MXC URI"):
                    await self.api.upload(b"png")


class StickerTests(unittest.TestCase):
    def test_conversion_preserves_dimensions_and_transparency(self):
        source = io.BytesIO()
        Image.new("RGBA", (32, 16), (255, 0, 0, 0)).save(source, "WEBP", lossless=True)
        png, width, height = bot.to_png(source.getvalue())
        self.assertEqual((width, height), (32, 16))
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_pack_update_preserves_existing_data_without_mutating_input(self):
        old = {"images": {"existing": {"url": "mxc://example.org/old"}},
               "pack": {"display_name": "Custom", "usage": ["emoticon"]},
               "custom": {"keep": True}}
        result = bot.updated_pack(old, "new", {"url": "mxc://example.org/new"})
        self.assertEqual(result["images"]["existing"], old["images"]["existing"])
        self.assertEqual(result["pack"]["display_name"], "Custom")
        self.assertEqual(result["pack"]["usage"], ["emoticon", "sticker"])
        self.assertEqual(result["custom"], {"keep": True})
        self.assertNotIn("new", old["images"])
        self.assertEqual(old["pack"]["usage"], ["emoticon"])


class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_import_preserves_pack_and_duplicate_skips_upload(self):
        api = AsyncMock()
        api.get_account_data.return_value = {"images": {"old": {"url": "mxc://host/old"}}}
        api.upload.return_value = "mxc://host/new"
        with patch.object(bot, "MATRIX_PACK_ROOM_ID", ""):
            self.assertEqual(await bot.install_sticker(api, "new", b"png", 32, 16, "Sticker"),
                             ("mxc://host/new", True))
            event_type, pack = api.put_account_data.call_args.args
            self.assertEqual(event_type, bot.PERSONAL_PACK_TYPE)
            self.assertIn("old", pack["images"])
            self.assertEqual(pack["images"]["new"]["info"]["size"], 3)
            api.get_account_data.return_value = pack
            api.upload.reset_mock()
            api.put_account_data.reset_mock()
            self.assertEqual(await bot.install_sticker(api, "new", b"png", 32, 16, "Sticker"),
                             ("mxc://host/new", False))
            api.upload.assert_not_awaited()
            api.put_account_data.assert_not_awaited()

    async def test_room_import_reuses_media_and_preserves_other_enabled_rooms(self):
        api = AsyncMock()
        api.get_account_data.side_effect = [
            {}, {"rooms": {"!other:host": {"existing": {}}}}
        ]
        api.get_room_pack.return_value = {"images": {"new": {"url": "mxc://host/reused"}}}
        with patch.object(bot, "MATRIX_PACK_ROOM_ID", "!stickers:host"):
            result = await bot.install_sticker(api, "new", b"png", 32, 16, "Sticker")
        self.assertEqual(result, ("mxc://host/reused", True))
        api.upload.assert_not_awaited()
        api.put_room_pack.assert_awaited_once()
        event_type, enabled = api.put_account_data.call_args.args
        self.assertEqual(event_type, bot.ENABLED_PACKS_TYPE)
        self.assertEqual(enabled["rooms"]["!other:host"], {"existing": {}})
        self.assertEqual(enabled["rooms"]["!stickers:host"], {bot.PACK_KEY: {}})


if __name__ == "__main__":
    unittest.main()
