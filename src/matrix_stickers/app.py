"""Application composition, startup, and shutdown."""

import logging

import httpx
from dotenv import load_dotenv
from telegram import BotCommand
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from matrix_stickers.config import Settings
from matrix_stickers.handlers import TelegramHandlers
from matrix_stickers.matrix import MatrixAPI, describe_http_error
from matrix_stickers.service import StickerService

LOG = logging.getLogger(__name__)


class StickerApplication(Application):
    async def stop(self) -> None:
        handlers = self.bot_data.get("telegram_handlers")
        if handlers:
            handlers.stopping = True
            handlers.cancel_pack.set()
            handlers.pending_import = None
        await super().stop()


def build_application(settings: Settings) -> Application:
    async def startup(app: Application) -> None:
        api = MatrixAPI(settings)
        try:
            await api.verify_user()
            if settings.room_id:
                await api.get_room_library()
            await app.bot.set_my_commands(
                [
                    BotCommand("stickers", "Browse your Matrix sticker library"),
                    BotCommand("add", "Reply to a sticker to import it with a name"),
                    BotCommand("importpack", "Import an entire Telegram sticker pack"),
                    BotCommand("cancelimport", "Stop the current pack import"),
                    BotCommand("packs", "List Matrix packs and capacity"),
                    BotCommand("newpack", "Create and select a Matrix pack"),
                    BotCommand("usepack", "Select a Matrix pack"),
                    BotCommand("renamepack", "Rename a Matrix pack"),
                    BotCommand("view", "Preview a Matrix sticker by ID"),
                    BotCommand("rename", "Rename a sticker by ID or reply"),
                    BotCommand("setid", "Change a sticker shortcode"),
                    BotCommand("suffix", "Change or hide this room's sticker suffix"),
                    BotCommand("remove", "Remove a sticker from your Matrix packs"),
                    BotCommand("help", "Show commands and examples"),
                ]
            )
        except BaseException:
            await api.close()
            raise
        app.bot_data["stickers"] = StickerService(api)
        LOG.info("Matrix identity verified: %s", settings.matrix_user_id)

    async def shutdown(app: Application) -> None:
        service = app.bot_data.get("stickers")
        if service:
            await service.api.close()

    async def error_handler(update: object, context) -> None:
        # Exception strings can contain Telegram request URLs and tokens.
        LOG.error("Unhandled update error: %s", type(context.error).__name__)

    app = (
        Application.builder()
        .application_class(StickerApplication)
        .token(settings.telegram_token)
        .concurrent_updates(4)
        .post_init(startup)
        .post_shutdown(shutdown)
        .build()
    )
    handlers = TelegramHandlers(settings)
    app.bot_data["telegram_handlers"] = handlers
    for command, callback in (
        ("start", handlers.help),
        ("help", handlers.help),
        ("add", handlers.add),
        ("importpack", handlers.import_pack),
        ("cancelimport", handlers.cancel_import),
        ("packs", handlers.packs),
        ("newpack", handlers.new_pack),
        ("usepack", handlers.use_pack),
        ("renamepack", handlers.rename_pack),
        ("stickers", handlers.list),
        ("list", handlers.list),
        ("view", handlers.view),
        ("rename", handlers.rename),
        ("setid", handlers.set_id),
        ("suffix", handlers.suffix),
        ("remove", handlers.remove),
    ):
        app.add_handler(CommandHandler(command, callback, filters=filters.ChatType.PRIVATE))
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.Sticker.ALL, handlers.sticker)
    )
    app.add_error_handler(error_handler)
    app.add_handler(CallbackQueryHandler(handlers.choose_import, pattern=r"^packimport:"))
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    load_dotenv()
    try:
        app = build_application(Settings.from_env())
        LOG.info("Starting Telegram sticker library")
        app.run_polling(allowed_updates=["message", "callback_query"])
    except httpx.HTTPStatusError as exc:
        LOG.error("%s", describe_http_error(exc, Settings.from_env()))
        raise SystemExit("Matrix setup failed. Check the bot's access and server logs.") from None
    except httpx.HTTPError:
        raise SystemExit(
            "Could not connect to Matrix. Check the server address and connectivity."
        ) from None
    except TelegramError as exc:
        LOG.error("Telegram startup failed: %s", type(exc).__name__)
        raise SystemExit("Telegram startup failed. Check the bot token and connectivity.") from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
