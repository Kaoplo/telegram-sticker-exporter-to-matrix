import os
import unittest
from unittest.mock import patch

import httpx

# Never load real credentials or contact a real server in regression tests.
with patch.dict(os.environ, {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_USER_ID": "1",
    "MATRIX_HOMESERVER": "https://matrix.example.org",
    "MATRIX_USER_ID": "@test:example.org",
    "MATRIX_ACCESS_TOKEN": "test-token",
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


if __name__ == "__main__":
    unittest.main()
