import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from matrix_stickers.app import build_application
from matrix_stickers.conversion import ConvertedSticker
from matrix_stickers.handlers import TelegramHandlers
from matrix_stickers.images import make_shortcode
from matrix_stickers.packs import PACK_KEY
from matrix_stickers.service import PackFullError, Sticker
from tests.helpers import settings


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handlers = TelegramHandlers(settings())
        self.message = SimpleNamespace(
            reply_text=AsyncMock(),
            reply_document=AsyncMock(),
            reply_to_message=None,
        )
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(type="private"),
            effective_message=self.message,
        )
        self.service = AsyncMock()
        self.service.refresh.return_value = None
        self.service.copy_existing.return_value = None
        self.service.active_pack_key.return_value = PACK_KEY
        self.service.create_pack.return_value = "telegram_imports_own"
        self.context = SimpleNamespace(
            args=[],
            application=SimpleNamespace(bot_data={"stickers": self.service}),
            bot=SimpleNamespace(get_file=AsyncMock()),
        )
        self.context.bot.get_sticker_set = AsyncMock()

    async def test_every_command_rejects_other_users_and_groups(self):
        for name in (
            "help",
            "sticker",
            "add",
            "list",
            "view",
            "rename",
            "remove",
            "import_pack",
            "cancel_import",
            "packs",
            "new_pack",
            "use_pack",
            "rename_pack",
            "choose_import",
            "set_id",
            "suffix",
        ):
            for user_id, chat_type in ((99, "private"), (1, "group")):
                with self.subTest(command=name, user_id=user_id, chat_type=chat_type):
                    self.update.effective_user.id = user_id
                    self.update.effective_chat.type = chat_type
                    await getattr(self.handlers, name)(self.update, self.context)
        self.message.reply_text.assert_not_awaited()
        self.message.reply_document.assert_not_awaited()
        self.assertEqual(self.service.mock_calls, [])

    async def test_list_pagination_and_names_are_bounded(self):
        self.service.list_stickers.return_value = [
            Sticker(f"id-{i}", "x" * 10000, "mxc://host/id") for i in range(17)
        ]
        self.context.args = ["2"]
        await self.handlers.list(self.update, self.context)
        text = self.message.reply_text.call_args.args[0]
        self.assertIn("page 2/3", text)
        self.assertIn(":id-8:", text)
        self.assertNotIn(":id-7:", text)
        self.assertIn("/stickers 3", text)
        self.assertLess(len(text), 4096)

    async def test_list_invalid_page(self):
        self.service.list_stickers.return_value = []
        for page in ("bad", "0", "-1", "100"):
            self.context.args = [page]
            await self.handlers.list(self.update, self.context)
        self.assertEqual(self.message.reply_text.await_count, 4)

    async def test_list_reports_the_failed_read_and_matrix_reason(self):
        request = httpx.Request(
            "GET", "https://matrix.example.org/_matrix/client/v3/user/test/account_data/pack"
        )
        response = httpx.Response(
            403,
            request=request,
            json={"errcode": "M_FORBIDDEN", "error": "Missing scope"},
        )
        self.service.list_stickers.side_effect = httpx.HTTPStatusError(
            "forbidden",
            request=request,
            response=response,
        )
        with self.assertLogs("matrix_stickers.handlers", level="WARNING"):
            await self.handlers.list(self.update, self.context)
        reply = self.message.reply_text.call_args.args[0]
        self.assertIn("Matrix denied access", reply)
        self.assertNotIn("/_matrix/", reply)
        self.assertNotIn("M_FORBIDDEN", reply)
        self.assertNotIn("Missing scope", reply)

    async def test_rename_by_id_and_by_reply(self):
        self.service.rename.return_value = Sticker("id", "Happy fox", "mxc://host/id")
        self.context.args = ["id", "Happy", "fox"]
        await self.handlers.rename(self.update, self.context)
        self.service.rename.assert_awaited_with("id", "Happy fox")
        self.message.reply_to_message = SimpleNamespace(
            sticker=SimpleNamespace(file_unique_id="telegram-id"),
        )
        self.context.args = ["Happy", "fox"]
        await self.handlers.rename(self.update, self.context)
        self.service.rename.assert_awaited_with(
            "telegram:" + make_shortcode("telegram-id"), "Happy fox"
        )

    async def test_remove_by_id(self):
        self.context.args = ["id"]
        self.service.remove.return_value = True
        await self.handlers.remove(self.update, self.context)
        self.service.remove.assert_awaited_once_with("id")
        self.assertIn("Removed", self.message.reply_text.call_args.args[0])

    async def test_preview_downloads_matrix_media(self):
        self.context.args = ["id"]
        self.service.get_sticker.return_value = Sticker("id", "Fox", "mxc://host/id")
        image = io.BytesIO()
        Image.new("RGBA", (2, 2)).save(image, "PNG")
        self.service.api.download.return_value = image.getvalue()
        await self.handlers.view(self.update, self.context)
        self.service.api.download.assert_awaited_once_with("mxc://host/id")
        self.message.reply_document.assert_awaited_once()
        self.assertEqual(
            self.message.reply_document.call_args.kwargs["filename"], "matrix-sticker.png"
        )

    async def test_add_named_sticker(self):
        sticker = SimpleNamespace(
            file_unique_id="tg",
            file_id="file",
            is_animated=False,
            is_video=False,
            file_size=100,
        )
        self.message.reply_to_message = SimpleNamespace(sticker=sticker)
        self.context.args = ["Happy", "fox"]
        image = io.BytesIO()
        Image.new("RGB", (2, 2)).save(image, "WEBP")
        file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=image.getvalue()))
        self.context.bot.get_file.return_value = file
        self.service.install.return_value = (Sticker("id", "Happy fox", "mxc://host/id"), True)
        await self.handlers.add(self.update, self.context)
        self.assertEqual(self.service.install.call_args.args[-1], "Happy fox")
        self.assertIn("Happy fox", self.message.reply_text.call_args.args[0])

    async def test_animated_import_passes_flags_emoji_and_mimetype(self):
        self.message.sticker = SimpleNamespace(
            file_unique_id="id",
            file_id="file",
            file_size=100,
            is_animated=True,
            is_video=False,
            emoji="🦊",
            set_name="FoxPack",
        )
        self.context.bot.get_file.return_value = SimpleNamespace(
            download_as_bytearray=AsyncMock(return_value=b"tgs"),
        )
        self.service.install.return_value = (Sticker("id", "Fox", "mxc://host/id"), True)
        with patch(
            "matrix_stickers.handlers.convert_sticker",
            return_value=ConvertedSticker(b"webp", 64, 64, "image/webp", True),
        ) as convert:
            await self.handlers.sticker(self.update, self.context)
        convert.assert_called_once_with(b"tgs", animated=True, video=False)
        self.assertEqual(self.service.install.call_args.kwargs["emoji"], "🦊")
        self.assertEqual(self.service.install.call_args.kwargs["mimetype"], "image/webp")

    async def test_existing_sticker_skips_download_and_conversion(self):
        self.message.sticker = SimpleNamespace(file_unique_id="id", emoji="🦊", set_name="FoxPack")
        self.service.refresh.return_value = Sticker("id", "Fox", "mxc://host/id")
        await self.handlers.sticker(self.update, self.context)
        self.context.bot.get_file.assert_not_awaited()

    async def test_shortcode_can_be_changed_by_id_and_original_sticker_reply(self):
        self.service.set_id.return_value = Sticker("happy_fox", "Happy", "mxc://h/id")
        self.context.args = ["old", "happy_fox"]
        await self.handlers.set_id(self.update, self.context)
        self.service.set_id.assert_awaited_with("old", "happy_fox")
        self.message.reply_to_message = SimpleNamespace(
            sticker=SimpleNamespace(file_unique_id="tg")
        )
        self.context.args = ["happy_fox"]
        await self.handlers.set_id(self.update, self.context)
        self.service.set_id.assert_awaited_with("telegram:" + make_shortcode("tg"), "happy_fox")

    async def test_suffix_commands_change_hide_and_reset(self):
        self.service.set_suffix.return_value = 2
        for args, expected in ((["Fox", "den"], "Fox den"), (["off"], ""), (["default"], None)):
            self.context.args = args
            await self.handlers.suffix(self.update, self.context)
            self.service.set_suffix.assert_awaited_with(expected)

    async def test_normal_upload_reply_has_no_debug_details_or_repeated_instructions(self):
        self.message.sticker = SimpleNamespace(file_unique_id="id")
        self.service.refresh.return_value = Sticker("happy", "🦊 Happy · Fox den", "mxc://host/id")
        await self.handlers.sticker(self.update, self.context)
        text = self.message.reply_text.call_args.args[0]
        self.assertIn(":happy:", text)
        for internal in ("mxc://", "HTTP", "/rename", "telegram_imports", "reload"):
            self.assertNotIn(internal, text)

    def pack(self, count=3):
        return SimpleNamespace(
            name="FoxPack",
            title="Fox Pack",
            stickers=[SimpleNamespace(file_unique_id=str(i)) for i in range(count)],
        )

    async def confirm(self, action="current"):
        token = self.handlers.pending_import[0]
        self.update.callback_query = SimpleNamespace(
            data=f"packimport:{token}:{action}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        await self.handlers.choose_import(self.update, self.context)

    async def prepare_prompt(self):
        self.handlers.settings = settings("!room")
        self.context.args = ["FoxPack"]
        self.context.bot.get_sticker_set.return_value = self.pack()
        await self.handlers.import_pack(self.update, self.context)

    async def test_pack_import_waits_for_choice_before_any_writes(self):
        await self.prepare_prompt()
        self.service.create_pack.assert_not_awaited()
        self.service.refresh.assert_not_awaited()
        self.service.install.assert_not_awaited()
        keyboard = self.message.reply_text.call_args.kwargs["reply_markup"]
        self.assertEqual(len(keyboard.inline_keyboard), 3)
        self.assertIn("No stickers are imported", self.message.reply_text.call_args.args[0])

    async def test_own_pack_choice_creates_one_named_pack_and_pins_destination(self):
        await self.prepare_prompt()
        with patch.object(
            self.handlers,
            "_import_one",
            AsyncMock(return_value=(Sticker("id", "Fox", "mxc://h/id"), True)),
        ) as importer:
            await self.confirm("new")
        self.service.create_pack.assert_awaited_once_with("Fox Pack")
        for call in importer.call_args_list:
            self.assertEqual(call.kwargs["target_pack_key"], "telegram_imports_own")

    async def test_selected_choice_uses_prompt_destination_even_if_selection_changes(self):
        await self.prepare_prompt()
        self.service.active_pack_key.return_value = "telegram_imports_other"
        with patch.object(
            self.handlers,
            "_import_one",
            AsyncMock(return_value=(Sticker("id", "Fox", "mxc://h/id"), True)),
        ) as importer:
            await self.confirm()
        self.service.create_pack.assert_not_awaited()
        self.assertEqual(importer.call_args.kwargs["target_pack_key"], PACK_KEY)

    async def test_cancel_choice_creates_nothing_and_cannot_be_replayed(self):
        await self.prepare_prompt()
        await self.confirm("cancel")
        self.assertIsNone(self.handlers.pending_import)
        self.service.create_pack.assert_not_awaited()
        await self.handlers.choose_import(self.update, self.context)
        self.assertIn("expired", self.message.reply_text.call_args.args[0])

    async def test_expired_and_unauthorized_choices_cannot_create_packs(self):
        await self.prepare_prompt()
        pending = self.handlers.pending_import
        self.handlers.pending_import = (*pending[:3], pending[3] - 601)
        await self.confirm("new")
        self.assertIn("expired", self.message.reply_text.call_args.args[0])
        self.service.create_pack.assert_not_awaited()
        await self.prepare_prompt()
        self.update.effective_user.id = 99
        await self.confirm("new")
        self.update.callback_query.answer.assert_not_awaited()
        self.service.create_pack.assert_not_awaited()

    async def test_full_pack_stops_without_automatic_creation(self):
        await self.prepare_prompt()
        with patch.object(
            self.handlers,
            "_import_one",
            AsyncMock(side_effect=PackFullError("Pack is full; use /newpack")),
        ) as importer:
            await self.confirm()
        self.assertEqual(importer.await_count, 1)
        self.service.create_pack.assert_not_awaited()
        text = self.message.reply_text.call_args.args[0]
        self.assertIn("0 failed · 3 unprocessed", text)
        self.assertIn("Pack is full", text)

    async def test_pack_import_counts_results_and_keeps_going_after_conversion_failure(self):
        self.context.args = ["https://t.me/addstickers/FoxPack"]
        self.context.bot.get_sticker_set.return_value = self.pack()
        with patch.object(
            self.handlers,
            "_import_one",
            AsyncMock(
                side_effect=[
                    (Sticker("a", "A", "mxc://host/a"), True),
                    ValueError("invalid animation"),
                    (Sticker("b", "B", "mxc://host/b"), False),
                ]
            ),
        ):
            await self.handlers.import_pack(self.update, self.context)
            await self.confirm()
        self.context.bot.get_sticker_set.assert_awaited_once_with("FoxPack")
        summary = self.message.reply_text.call_args.args[0]
        self.assertIn("1 added · 1 already present · 1 failed · 0 unprocessed", summary)

    async def test_pack_stops_on_permission_failure(self):
        self.context.args = ["FoxPack"]
        self.context.bot.get_sticker_set.return_value = self.pack()
        request = httpx.Request(
            "PUT", "https://matrix.example.org/_matrix/client/v3/rooms/room/state/type"
        )
        response = httpx.Response(403, request=request, json={"errcode": "M_FORBIDDEN"})
        error = httpx.HTTPStatusError("forbidden", request=request, response=response)
        with patch.object(self.handlers, "_import_one", AsyncMock(side_effect=error)) as import_one:
            await self.handlers.import_pack(self.update, self.context)
            await self.confirm()
        self.assertEqual(import_one.await_count, 1)
        self.assertIn("2 unprocessed", self.message.reply_text.call_args.args[0])
        self.assertIn("Matrix denied access", self.message.reply_text.call_args.args[0])
        self.assertNotIn("M_FORBIDDEN", self.message.reply_text.call_args.args[0])

    async def test_pack_can_be_selected_by_reply(self):
        self.message.reply_to_message = SimpleNamespace(sticker=SimpleNamespace(set_name="FoxPack"))
        self.context.bot.get_sticker_set.return_value = self.pack(0)
        await self.handlers.import_pack(self.update, self.context)
        self.context.bot.get_sticker_set.assert_awaited_once_with("FoxPack")

    async def test_cancel_stops_after_current_sticker_and_lock_is_released(self):
        self.context.args = ["FoxPack"]
        self.context.bot.get_sticker_set.return_value = self.pack()
        started, release = asyncio.Event(), asyncio.Event()

        async def import_one(*args, **kwargs):
            started.set()
            await release.wait()
            return Sticker("a", "A", "mxc://host/a"), True

        with patch.object(self.handlers, "_import_one", side_effect=import_one) as mocked:
            await self.handlers.import_pack(self.update, self.context)
            task = asyncio.create_task(self.confirm())
            await started.wait()
            await self.handlers.cancel_import(self.update, self.context)
            release.set()
            await task
        self.assertEqual(mocked.call_count, 1)
        self.assertFalse(self.handlers.pack_lock.locked())
        self.assertIn("2 unprocessed", self.message.reply_text.call_args.args[0])

    async def test_second_pack_is_rejected_while_one_runs(self):
        async with self.handlers.pack_lock:
            await self.handlers.import_pack(self.update, self.context)
        self.assertIn("already running", self.message.reply_text.call_args.args[0])
        self.context.bot.get_sticker_set.assert_not_awaited()

    async def test_invalid_command_arguments_do_not_mutate(self):
        for command in ("add", "rename", "remove", "view"):
            await getattr(self.handlers, command)(self.update, self.context)
        self.assertEqual(self.service.mock_calls, [])

    async def test_all_commands_registered(self):
        app = build_application(settings())
        registered = set()
        for handler in app.handlers[0]:
            if isinstance(handler, CommandHandler):
                registered.update(handler.commands)
        self.assertEqual(
            registered,
            {
                "start",
                "help",
                "add",
                "stickers",
                "list",
                "view",
                "rename",
                "remove",
                "importpack",
                "cancelimport",
                "packs",
                "newpack",
                "usepack",
                "renamepack",
                "setid",
                "suffix",
            },
        )
        self.assertTrue(any(isinstance(handler, MessageHandler) for handler in app.handlers[0]))
        self.assertTrue(
            any(isinstance(handler, CallbackQueryHandler) for handler in app.handlers[0])
        )
