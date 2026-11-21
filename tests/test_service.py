import asyncio
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from matrix_stickers.packs import PACK_KEY, PERSONAL_PACK_TYPE
from matrix_stickers.service import (
    SELECTION_TYPE,
    SOURCE_KEY,
    PackFullError,
    PartialUpdateError,
    StickerService,
)
from tests.helpers import settings


class MemoryMatrix:
    def __init__(self, room_id="!stickers"):
        self.settings = settings(room_id)
        self.room_name = "pawz.hu stickers"
        self.packs = {}
        self.personal = {}
        self.preferences = {}
        self.suffix = None
        self.suffix_writes = []
        self.upload = AsyncMock(return_value="mxc://host/new")
        self.writes = []

    async def get_room_library(self):
        await asyncio.sleep(0)
        return self.room_name if self.suffix is None else self.suffix, deepcopy(self.packs)

    async def get_room_name(self):
        return self.room_name

    async def set_room_suffix(self, value):
        self.suffix = value
        self.suffix_writes.append(value)

    async def put_room_pack(self, data, key=PACK_KEY):
        self.packs[key] = deepcopy(data)
        self.writes.append(key)

    async def get_account_data(self, event_type):
        if event_type == SELECTION_TYPE:
            return deepcopy(self.preferences)
        self.assert_personal(event_type)
        return deepcopy(self.personal)

    async def put_account_data(self, event_type, data):
        if event_type == SELECTION_TYPE:
            self.preferences = deepcopy(data)
            return
        self.assert_personal(event_type)
        self.personal = deepcopy(data)

    def assert_personal(self, event_type):
        if self.settings.room_id or event_type != PERSONAL_PACK_TYPE:
            raise AssertionError("Room mode must not access account data")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = MemoryMatrix()
        self.service = StickerService(self.api)

    async def install(self, shortcode="new", name="Happy fox", **kwargs):
        return await self.service.install(shortcode, b"image", 32, 16, name, emoji="🦊", **kwargs)

    async def test_named_room_import_has_emoji_room_and_source_metadata(self):
        self.api.packs = {PACK_KEY: {"images": {"old": {"url": "mxc://host/old"}}, "custom": True}}
        item, added = await self.install(set_name="FoxPack")
        self.assertTrue(added)
        self.assertEqual(item.name, "🦊 Happy fox · pawz.hu stickers")
        entry = self.api.packs[PACK_KEY]["images"]["new"]
        self.assertEqual(
            entry[SOURCE_KEY],
            {
                "name": "Happy fox",
                "emoji": "🦊",
                "room_id": "!stickers",
                "telegram_set": "FoxPack",
                "telegram_id": "new",
            },
        )
        self.assertIn("old", self.api.packs[PACK_KEY]["images"])
        self.assertTrue(self.api.packs[PACK_KEY]["custom"])
        self.assertEqual(self.api.personal, {})

    async def test_reimport_does_not_duplicate_prefix_or_upload(self):
        await self.install()
        item, added = await self.install(name=None)
        self.assertFalse(added)
        self.assertEqual(item.name, "🦊 Happy fox · pawz.hu stickers")
        self.api.upload.assert_awaited_once()

    async def test_named_reimport_updates_base_label(self):
        await self.install()
        item, _ = await self.install(name="New label")
        self.assertEqual(item.name, "🦊 New label · pawz.hu stickers")
        self.api.upload.assert_awaited_once()

    async def test_refresh_duplicate_needs_no_download_data(self):
        await self.install()
        self.api.room_name = "Fox room"
        item = await self.service.refresh("new", emoji="😴")
        self.assertEqual(item.name, "😴 Happy fox · Fox room")
        self.api.upload.assert_awaited_once()
        self.assertIsNone(await self.service.refresh("absent"))

    async def test_rename_preserves_emoji_room_media_and_id(self):
        await self.install()
        self.api.packs[PACK_KEY]["images"]["new"]["custom"] = True
        item = await self.service.rename("new", "Sleepy fox")
        self.assertEqual(item.name, "🦊 Sleepy fox · pawz.hu stickers")
        self.assertEqual(item.shortcode, "new")
        self.assertEqual(item.uri, "mxc://host/new")
        self.assertTrue(self.api.packs[PACK_KEY]["images"]["new"]["custom"])

    async def test_room_rename_reflected_in_list_and_persisted_on_reimport(self):
        await self.install()
        self.api.room_name = "Renamed room"
        self.assertEqual(
            (await self.service.list_stickers())[0].name, "🦊 Happy fox · Renamed room"
        )
        await self.install(name=None)
        self.assertEqual(
            self.api.packs[PACK_KEY]["images"]["new"]["body"], "🦊 Happy fox · Renamed room"
        )

    async def test_no_emoji_uses_no_invented_prefix(self):
        item, _ = await self.service.install("id", b"image", 1, 1, "Fox")
        self.assertEqual(item.name, "Fox · pawz.hu stickers")

    async def test_room_inventory_excludes_other_rooms_and_personal_pack(self):
        self.api.personal = {"images": {"personal": {"url": "mxc://host/old"}}}
        await self.install()
        self.assertEqual([item.shortcode for item in await self.service.list_stickers()], ["new"])
        self.api.packs = {}
        self.api.settings = settings("!different-room")
        self.assertEqual(await self.service.list_stickers(), [])

    async def test_personal_fallback_remains_available(self):
        api = MemoryMatrix("")
        service = StickerService(api)
        item, _ = await service.install("new", b"png", 1, 1, "Fox", emoji="🦊")
        self.assertEqual(item.name, "🦊 Fox")
        await service.rename("new", "Renamed")
        self.assertTrue(await service.remove("new"))
        self.assertEqual(api.packs, {})

    async def test_animated_media_mimetype_is_stored_and_sent(self):
        await self.install(mimetype="image/webp")
        self.api.upload.assert_awaited_once_with(b"image", "image/webp")
        self.assertEqual(
            self.api.packs[PACK_KEY]["images"]["new"]["info"]["mimetype"], "image/webp"
        )

    async def test_full_pack_never_creates_another_or_uploads_media(self):
        await self.install()
        before = deepcopy(self.api.packs)
        self.api.upload.reset_mock()
        with patch("matrix_stickers.service.ADMISSION_BYTES", 100):
            with self.assertRaisesRegex(PackFullError, "No extra pack"):
                await self.install("another")
        self.assertEqual(self.api.packs, before)
        self.api.upload.assert_not_awaited()

    async def test_manual_packs_preserve_names_and_selection_across_restart(self):
        await self.install()
        key = await self.service.create_pack("Fox collection")
        self.assertEqual(await self.service.active_pack_key(), key)
        restarted = StickerService(self.api)
        self.assertEqual(await restarted.active_pack_key(), key)
        self.assertEqual(await restarted.list_stickers(), [])
        await self.install("second")
        self.assertEqual(self.api.packs[key]["pack"]["display_name"], "Fox collection")
        await self.service.rename_pack(key, "New collection")
        self.assertEqual(self.api.packs[key]["pack"]["display_name"], "New collection")
        await self.service.select_pack(PACK_KEY)
        self.assertEqual([item.shortcode for item in await self.service.list_stickers()], ["new"])
        self.assertFalse(await self.service.remove("second"))
        self.assertIn("second", self.api.packs[key]["images"])

    async def test_selected_pack_operations_do_not_change_other_copies(self):
        await self.install()
        key = await self.service.create_pack("Other")
        copied = await self.service.copy_existing("new", emoji="🦊", pack_key=key)
        self.assertEqual(copied.pack_key, key)
        self.api.upload.assert_awaited_once()
        await self.service.rename("new", "Renamed")
        self.assertNotIn("Renamed", self.api.packs[PACK_KEY]["images"]["new"]["body"])
        self.assertTrue(await self.service.remove("new"))
        self.assertIn("new", self.api.packs[PACK_KEY]["images"])

    async def test_explicit_import_destination_does_not_follow_selection_changes(self):
        await self.install()
        key = await self.service.create_pack("Other")
        await self.service.select_pack(PACK_KEY)
        await self.service.install("second", b"png", 1, 1, "Second", pack_key=key)
        self.assertNotIn("second", self.api.packs[PACK_KEY]["images"])
        self.assertIn("second", self.api.packs[key]["images"])

    async def test_full_destination_rejects_copy_without_mutating_either_pack(self):
        await self.install()
        key = await self.service.create_pack("Other")
        before = deepcopy(self.api.packs)
        with patch("matrix_stickers.service.ADMISSION_BYTES", 100):
            with self.assertRaises(PackFullError):
                await self.service.copy_existing("new", pack_key=key)
        self.assertEqual(self.api.packs, before)

    async def test_preferences_preserve_other_room_selections(self):
        self.api.preferences = {"rooms": {"!other": "keep"}, "custom": True}
        await self.service.create_pack("New")
        self.assertEqual(self.api.preferences["rooms"]["!other"], "keep")
        self.assertTrue(self.api.preferences["custom"])

    async def test_packs_can_be_listed_when_previous_selection_was_removed(self):
        await self.install()
        self.api.preferences = {"rooms": {"!stickers": "telegram_imports_missing"}}
        packs = await self.service.list_packs()
        self.assertEqual([pack.key for pack in packs], [PACK_KEY])
        await self.service.select_pack(PACK_KEY)
        self.assertEqual(await self.service.active_pack_key(), PACK_KEY)

    async def test_legacy_entries_keep_media_and_gain_provenance(self):
        self.api.packs = {
            PACK_KEY: {"images": {"new": {"url": "mxc://host/old", "body": "Old name"}}}
        }
        item, added = await self.install(name=None)
        self.assertFalse(added)
        self.assertEqual(item.name, "🦊 Old name · pawz.hu stickers")
        self.assertEqual(item.uri, "mxc://host/old")
        self.api.upload.assert_not_awaited()

    async def test_invalid_data_is_not_overwritten(self):
        for pack in ({"images": []}, {"images": {"bad": "invalid"}}, {"pack": []}):
            self.api.packs = {PACK_KEY: deepcopy(pack)}
            with self.assertRaises(ValueError):
                await self.install()
            self.assertEqual(self.api.packs[PACK_KEY], pack)
        self.api.upload.assert_not_awaited()

    async def test_invalid_name_and_missing_rename(self):
        with self.assertRaises(ValueError):
            await self.install(name=" ")
        with self.assertRaisesRegex(ValueError, "not found"):
            await self.service.rename("missing", "Name")

    async def test_concurrent_imports_preserve_all_entries(self):
        await asyncio.gather(self.install("first"), self.install("second"))
        self.assertEqual(set(self.api.packs[PACK_KEY]["images"]), {"first", "second"})

    async def test_edit_shortcode_preserves_media_and_telegram_duplicate_detection(self):
        await self.install()
        item = await self.service.set_id(":new:", ":happy_fox:")
        self.assertEqual(item.shortcode, "happy_fox")
        self.assertNotIn("new", self.api.packs[PACK_KEY]["images"])
        self.assertEqual(
            self.api.packs[PACK_KEY]["images"]["happy_fox"][SOURCE_KEY]["telegram_id"], "new"
        )
        refreshed = await self.service.refresh("new", emoji="🦊")
        self.assertEqual(refreshed.shortcode, "happy_fox")
        duplicate, added = await self.install(name=None)
        self.assertFalse(added)
        self.assertEqual(duplicate.shortcode, "happy_fox")
        self.api.upload.assert_awaited_once()
        self.assertEqual(len(self.api.packs[PACK_KEY]["images"]), 1)

    async def test_reply_reference_still_works_after_shortcode_is_reused(self):
        await self.install()
        await self.service.set_id("new", "fox")
        await self.install("other")
        await self.service.set_id("other", "new")
        self.assertEqual((await self.service.get_sticker("telegram:new")).shortcode, "fox")
        self.assertEqual((await self.service.refresh("new")).shortcode, "fox")
        await self.service.rename("telegram:new", "Sleepy")
        self.assertNotIn("Sleepy", self.api.packs[PACK_KEY]["images"]["new"]["body"])
        await self.service.remove("telegram:new")
        self.assertIn("new", self.api.packs[PACK_KEY]["images"])

    async def test_shortcode_collision_or_invalid_id_cannot_overwrite_entries(self):
        await self.install()
        await self.install("other")
        before = deepcopy(self.api.packs)
        for new_id in ("other", "has spaces", "bad/id", "x" * 101):
            with self.assertRaises(ValueError):
                await self.service.set_id("new", new_id)
        self.assertEqual(self.api.packs, before)

    async def test_custom_shortcode_copy_reuses_media_in_another_pack(self):
        await self.install()
        await self.service.set_id("new", "happy_fox")
        key = await self.service.create_pack("Another")
        item = await self.service.copy_existing("new", pack_key=key)
        self.assertEqual(item.shortcode, "happy_fox")
        self.api.upload.assert_awaited_once()

    async def test_custom_shortcode_copy_collision_is_not_overwritten(self):
        await self.install()
        await self.service.set_id("new", "fox")
        key = await self.service.create_pack("Another")
        await self.install("other")
        await self.service.set_id("other", "fox")
        before = deepcopy(self.api.packs)
        with self.assertRaisesRegex(ValueError, "already used"):
            await self.service.copy_existing("new", pack_key=key)
        self.assertEqual(self.api.packs, before)

    async def test_suffix_updates_all_room_packs_existing_and_future_entries(self):
        await self.install()
        key = await self.service.create_pack("Another")
        await self.install("second")
        self.assertEqual(await self.service.set_suffix("Fox den"), 2)
        self.assertEqual(self.api.room_name, "pawz.hu stickers")
        for pack in self.api.packs.values():
            for entry in pack["images"].values():
                self.assertTrue(entry["body"].endswith(" · Fox den"))
                self.assertEqual(entry[SOURCE_KEY]["room_id"], "!stickers")
        await self.install("third")
        self.assertTrue(self.api.packs[key]["images"]["third"]["body"].endswith(" · Fox den"))
        restarted = StickerService(self.api)
        self.assertEqual(await restarted.get_suffix(), "Fox den")

    async def test_suffix_can_be_hidden_and_reset_to_actual_room_name(self):
        await self.install()
        await self.service.set_suffix("")
        self.assertEqual(self.api.packs[PACK_KEY]["images"]["new"]["body"], "🦊 Happy fox")
        await self.service.set_suffix(None)
        self.assertEqual(
            self.api.packs[PACK_KEY]["images"]["new"]["body"], "🦊 Happy fox · pawz.hu stickers"
        )

    async def test_suffix_preflights_every_pack_before_any_writes(self):
        await self.install()
        before = deepcopy(self.api.packs)
        with patch("matrix_stickers.service.MAX_PACK_BYTES", 100):
            with self.assertRaises(PackFullError):
                await self.service.set_suffix("New suffix")
        self.assertEqual(self.api.packs, before)
        self.assertEqual(self.api.suffix_writes, [])

    async def test_partial_suffix_update_can_be_retried(self):
        await self.install()
        real_save = self.api.put_room_pack
        with patch.object(self.api, "put_room_pack", AsyncMock(side_effect=ValueError("offline"))):
            with self.assertRaises(PartialUpdateError):
                await self.service.set_suffix("Fox den")
        self.api.put_room_pack = real_save
        await self.service.set_suffix("Fox den")
        self.assertTrue(self.api.packs[PACK_KEY]["images"]["new"]["body"].endswith(" · Fox den"))
