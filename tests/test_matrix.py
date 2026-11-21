import unittest

import httpx

from matrix_stickers.images import MAX_STICKER_BYTES
from matrix_stickers.matrix import MatrixAPI, describe_http_error
from matrix_stickers.packs import LABEL_SETTINGS_TYPE, PERSONAL_PACK_TYPE
from tests.helpers import settings


class ErrorDescriptionTests(unittest.TestCase):
    def describe(self, payload=None, content=None):
        request = httpx.Request(
            "GET",
            "https://matrix.example.org/_matrix/client/v3/rooms/room/state/type"
            "?access_token=should-not-appear",
        )
        response = httpx.Response(403, request=request, json=payload, content=content)
        error = httpx.HTTPStatusError("unused", request=request, response=response)
        return describe_http_error(error, settings())

    def test_operation_and_server_reason_are_included(self):
        description = self.describe({"errcode": "M_FORBIDDEN", "error": "Insufficient scope"})
        self.assertIn("Matrix HTTP 403", description)
        self.assertIn("GET /_matrix/client/v3/rooms/room/state/type", description)
        self.assertIn("M_FORBIDDEN", description)
        self.assertIn("Insufficient scope", description)
        self.assertNotIn("access_token", description)
        self.assertNotIn("should-not-appear", description)

    def test_tokens_echoed_by_server_are_redacted(self):
        description = self.describe({"error": "matrix-test-token and test-token rejected"})
        self.assertNotIn("matrix-test-token", description)
        self.assertNotIn("test-token", description)
        self.assertIn("[redacted]", description)

    def test_non_json_and_non_object_responses_still_identify_request(self):
        for payload, content in ((None, b"<html>Forbidden</html>"), ([], None)):
            description = self.describe(payload, content)
            self.assertIn("Matrix HTTP 403", description)
            self.assertNotIn("<html>", description)

    def test_long_responses_are_bounded(self):
        self.assertLessEqual(len(self.describe({"error": "x" * 10000})), 1500)


class MatrixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.response = httpx.Response(200, json={"user_id": "@test:example.org"})
        self.requests = []

        def respond(request):
            self.requests.append(request)
            return self.response

        self.api = MatrixAPI(settings(), transport=httpx.MockTransport(respond))

    async def asyncTearDown(self):
        await self.api.close()

    async def test_valid_identity(self):
        await self.api.verify_user()

    async def test_version_12_room_id_is_sent_without_a_domain(self):
        room_id = "!i0PoSr5uJZx8iYlOrbAn_wDUCqOxDmttXDCAirFLgno"
        api = MatrixAPI(
            settings(room_id), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
        try:
            self.assertEqual(
                api.state_url(),
                f"/_matrix/client/v3/rooms/%21{room_id[1:]}/state/m.room.image_pack/telegram_imports",
            )
        finally:
            await api.close()

    async def test_wrong_or_missing_identity(self):
        for payload in ({}, {"user_id": "@other:example.org"}):
            self.response = httpx.Response(200, json=payload)
            with self.assertRaises(ValueError):
                await self.api.verify_user()

    async def test_invalid_token_has_actionable_message(self):
        self.response = httpx.Response(401, json={"errcode": "M_UNKNOWN_TOKEN"})
        with self.assertRaisesRegex(ValueError, "fresh token"):
            await self.api.verify_user()

    async def test_empty_or_html_success_explains_configuration(self):
        for body in (b"", b"<html>Website</html>"):
            self.response = httpx.Response(200, content=body)
            with self.assertRaisesRegex(ValueError, "Check MATRIX_HOMESERVER"):
                await self.api.verify_user()

    async def test_non_object_json_rejected(self):
        self.response = httpx.Response(200, json=[])
        with self.assertRaisesRegex(ValueError, "instead of an object"):
            await self.api.verify_user()

    async def test_http_error_preserved(self):
        self.response = httpx.Response(403, json={"errcode": "M_FORBIDDEN"})
        with self.assertRaises(httpx.HTTPStatusError):
            await self.api.verify_user()

    async def test_missing_packs_are_empty(self):
        self.response = httpx.Response(404, json={"errcode": "M_NOT_FOUND"})
        self.assertEqual(await self.api.get_account_data(PERSONAL_PACK_TYPE), {})
        self.assertEqual(await self.api.get_room_pack(), {})

    async def test_invalid_pack_is_not_empty(self):
        self.response = httpx.Response(200, content=b"")
        with self.assertRaises(ValueError):
            await self.api.get_account_data(PERSONAL_PACK_TYPE)

    async def test_upload_validation(self):
        for payload in ({}, {"content_uri": None}, {"content_uri": "https://example.org"}):
            self.response = httpx.Response(200, json=payload)
            with self.assertRaisesRegex(ValueError, "invalid MXC URI"):
                await self.api.upload(b"png")
        self.response = httpx.Response(200, json={"content_uri": "mxc://example.org/id"})
        self.assertEqual(await self.api.upload(b"png"), "mxc://example.org/id")

    async def test_animated_upload_uses_webp_content_type_and_filename(self):
        self.response = httpx.Response(200, json={"content_uri": "mxc://example.org/id"})
        await self.api.upload(b"animation", "image/webp")
        request = self.requests[-1]
        self.assertEqual(request.headers["Content-Type"], "image/webp")
        self.assertEqual(request.url.params["filename"], "telegram-sticker.webp")

    async def test_room_library_reads_name_and_only_importer_shards(self):
        self.response = httpx.Response(
            200,
            json=[
                {"type": "m.room.name", "state_key": "", "content": {"name": " Fox room "}},
                {"type": "m.room.image_pack", "state_key": "telegram_imports", "content": {}},
                {"type": "m.room.image_pack", "state_key": "telegram_imports_12", "content": {}},
                {
                    "type": "m.room.image_pack",
                    "state_key": "telegram_imports_a1b2c3",
                    "content": {},
                },
                {"type": "m.room.image_pack", "state_key": "someone_else", "content": {}},
            ],
        )
        name, packs = await self.api.get_room_library()
        self.assertEqual(name, "Fox room")
        self.assertEqual(
            set(packs), {"telegram_imports", "telegram_imports_12", "telegram_imports_a1b2c3"}
        )

    async def test_room_library_invalid_shape_and_forbidden_preserved(self):
        self.response = httpx.Response(200, json={})
        with self.assertRaisesRegex(ValueError, "list"):
            await self.api.get_room_library()
        self.response = httpx.Response(403, json={"errcode": "M_FORBIDDEN"})
        with self.assertRaises(httpx.HTTPStatusError):
            await self.api.get_room_library()

    async def test_room_suffix_override_and_hidden_suffix(self):
        for suffix in ("Fox den", ""):
            self.response = httpx.Response(
                200,
                json=[
                    {"type": "m.room.name", "state_key": "", "content": {"name": "Original room"}},
                    {"type": LABEL_SETTINGS_TYPE, "state_key": "", "content": {"suffix": suffix}},
                ],
            )
            name, _ = await self.api.get_room_library()
            self.assertEqual(name, suffix)

    async def test_suffix_reset_uses_room_name_regardless_of_state_order(self):
        self.response = httpx.Response(
            200,
            json=[
                {"type": LABEL_SETTINGS_TYPE, "state_key": "", "content": {}},
                {"type": "m.room.name", "state_key": "", "content": {"name": "Original room"}},
            ],
        )
        name, _ = await self.api.get_room_library()
        self.assertEqual(name, "Original room")

    async def test_suffix_preference_writes_room_state_and_reset_clears_it(self):
        self.response = httpx.Response(200, json={})
        await self.api.set_room_suffix("Fox den")
        self.assertIn(LABEL_SETTINGS_TYPE, self.requests[-1].url.path)
        self.assertEqual(self.requests[-1].method, "PUT")
        self.assertEqual(self.requests[-1].content, b'{"suffix":"Fox den"}')
        await self.api.set_room_suffix(None)
        self.assertEqual(self.requests[-1].content, b"{}")

    async def test_download_uses_authenticated_matrix_endpoint(self):
        self.response = httpx.Response(200, content=b"image-data")
        self.assertEqual(await self.api.download("mxc://remote.example/id"), b"image-data")
        request = self.requests[-1]
        self.assertEqual(
            str(request.url),
            "https://matrix.example.org/_matrix/client/v1/media/download/remote.example/id",
        )
        self.assertEqual(request.headers["Authorization"], "Bearer matrix-test-token")

    async def test_download_rejects_external_urls_and_invalid_mxc(self):
        for uri in (
            "https://evil.example/file",
            "mxc://host/",
            "mxc://host/a/b",
            "mxc://user:password@host/file",
            "mxc://host/id?query=true",
        ):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                await self.api.download(uri)
        self.assertEqual(self.requests, [])

    async def test_download_size_limit(self):
        self.response = httpx.Response(200, content=b"x" * (MAX_STICKER_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "too large"):
            await self.api.download("mxc://host/id")
