import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.ext import Application

from matrix_stickers.app import build_application, main
from tests.helpers import settings


class PollingTests(unittest.TestCase):
    def test_inline_button_updates_are_requested(self):
        app = SimpleNamespace(run_polling=Mock())
        with (
            patch("matrix_stickers.app.load_dotenv"),
            patch("matrix_stickers.app.Settings.from_env", return_value=settings()),
            patch("matrix_stickers.app.build_application", return_value=app),
        ):
            main()
        self.assertEqual(
            set(app.run_polling.call_args.kwargs["allowed_updates"]), {"message", "callback_query"}
        )


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_stops_bulk_work_before_waiting_for_handlers(self):
        app = build_application(settings())
        handlers = app.bot_data["telegram_handlers"]
        handlers.pending_import = ("pending",)
        with patch.object(Application, "stop", AsyncMock()) as parent_stop:
            await app.stop()
        self.assertTrue(handlers.cancel_pack.is_set())
        self.assertTrue(handlers.stopping)
        self.assertIsNone(handlers.pending_import)
        parent_stop.assert_awaited_once()
