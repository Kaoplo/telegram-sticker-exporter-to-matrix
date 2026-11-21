"""Private Telegram commands and presentation."""

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from io import BytesIO

import httpx
from PIL import Image, UnidentifiedImageError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from matrix_stickers.config import Settings
from matrix_stickers.conversion import convert_sticker
from matrix_stickers.images import MAX_STICKER_BYTES, make_shortcode, validate_name
from matrix_stickers.matrix import MatrixProtocolError, describe_http_error, public_http_error
from matrix_stickers.service import ADMISSION_BYTES, PackFullError, StickerService
from matrix_stickers.telegram_packs import sticker_set_name

LOG = logging.getLogger(__name__)
PAGE_SIZE = 8
HELP_TEXT = """Matrix sticker library

Send a static, animated, or video sticker to import it.
Reply to a sticker with /add Happy fox to import it with a name.
Names keep the Telegram emoji and destination room name.

/packs [page] — list your Matrix packs and capacity
/newpack Name — create and select a pack
/usepack ID — select the destination pack
/renamepack ID New name — rename a pack
/importpack PackName — import a whole Telegram pack (or reply to a sticker)
/cancelimport — stop a pack import after the current sticker
/stickers [page] — browse the current library
/view ID — preview a sticker
/rename ID New name — change its display name
/setid ID New_id — change its :shortcode:
/suffix Text — change the suffix across this room's packs
/suffix off — hide the suffix; /suffix default — use the room name
/remove ID — remove it from your configured packs
/help — show this guide

You can also reply to the original Telegram sticker with /rename New name,
/view, or /remove. IDs are shown by /stickers.
Animations are converted to animated WebP for Matrix."""


def private_command(function: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """Enforce owner/private-chat checks on every entry point and report safe errors."""

    @wraps(function)
    async def wrapped(self: "TelegramHandlers", update: Update, context: ContextTypes.DEFAULT_TYPE):
        if (
            not update.effective_user
            or update.effective_user.id != self.settings.telegram_user_id
            or not update.effective_chat
            or update.effective_chat.type != "private"
            or not update.effective_message
        ):
            return
        if self.stopping:
            await update.effective_message.reply_text(
                "The bot is restarting. Please try again shortly."
            )
            return
        try:
            await function(self, update, context)
        except httpx.HTTPStatusError as exc:
            description = describe_http_error(exc, self.settings)
            LOG.warning("%s", description)
            await update.effective_message.reply_text(public_http_error(exc))
        except MatrixProtocolError:
            LOG.warning("Matrix returned invalid response data")
            await update.effective_message.reply_text(
                "Matrix returned invalid data. Check the server configuration."
            )
        except KeyError:
            LOG.warning("Sticker pack data is incomplete")
            await update.effective_message.reply_text(
                "This pack contains incomplete sticker data. Check it in Sable."
            )
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            await update.effective_message.reply_text(
                "That sticker is not a valid supported image."
            )
        except httpx.HTTPError:
            LOG.warning("Matrix connection failed")
            await update.effective_message.reply_text("Could not reach Matrix. Please try again.")
        except TelegramError:
            LOG.warning("Telegram request failed in %s", function.__name__)
            await update.effective_message.reply_text(
                "Telegram could not complete the request. If this was a preview, "
                "The media may be unavailable or unsupported. "
                "Check /stickers for the current state."
            )

    return wrapped


class TelegramHandlers:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.pack_lock = asyncio.Lock()
        self.conversion_lock = asyncio.Lock()
        self.cancel_pack = asyncio.Event()
        self.pending_import = None
        self.stopping = False

    @staticmethod
    def service(context: ContextTypes.DEFAULT_TYPE) -> StickerService:
        return context.application.bot_data["stickers"]

    @staticmethod
    def target(update: Update, args: list[str]) -> tuple[str, list[str]]:
        reply = update.effective_message.reply_to_message
        if reply and reply.sticker:
            return "telegram:" + make_shortcode(reply.sticker.file_unique_id), args
        if not args:
            raise ValueError(
                "Specify a sticker ID from /stickers, or reply to its Telegram sticker."
            )
        return args[0], args[1:]

    @private_command
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(HELP_TEXT)

    @private_command
    async def sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._import(update, context, update.effective_message.sticker, None)

    @private_command
    async def add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reply = update.effective_message.reply_to_message
        if not reply or not reply.sticker or not context.args:
            raise ValueError("Reply to a Telegram sticker with /add Name of sticker")
        await self._import(update, context, reply.sticker, validate_name(" ".join(context.args)))

    async def _import(self, update: Update, context, sticker, name: str | None) -> None:
        item, added = await self._import_one(context, sticker, name)
        verb = "Added" if added else "Already in this pack"
        await update.effective_message.reply_text(f"{verb}: {item.name}\n:{item.shortcode}:")

    async def _import_one(
        self,
        context,
        sticker,
        name: str | None = None,
        *,
        default_name=None,
        pack_name=None,
        target_pack_key=None,
    ):
        shortcode = make_shortcode(sticker.file_unique_id)
        service = self.service(context)
        if target_pack_key is None:
            target_pack_key = await service.active_pack_key()
        emoji = getattr(sticker, "emoji", None) or ""
        set_name = getattr(sticker, "set_name", None) or pack_name
        options = dict(name=name, emoji=emoji, set_name=set_name, pack_key=target_pack_key)
        existing = await service.refresh(shortcode, **options)
        if existing is not None:
            return existing, False
        copied = await service.copy_existing(shortcode, **options)
        if copied is not None:
            return copied, True
        if sticker.file_size and sticker.file_size > MAX_STICKER_BYTES:
            raise ValueError("That sticker is too large to import")
        async with self.conversion_lock:
            file = await context.bot.get_file(sticker.file_id)
            raw = bytes(await file.download_as_bytearray())
            media = await asyncio.to_thread(
                convert_sticker,
                raw,
                animated=sticker.is_animated,
                video=sticker.is_video,
            )
        return await service.install(
            shortcode,
            media.data,
            media.width,
            media.height,
            name or default_name,
            emoji=emoji,
            set_name=set_name,
            mimetype=media.mimetype,
            pack_key=target_pack_key,
        )

    @private_command
    async def import_pack(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.pack_lock.locked():
            raise ValueError("A pack import is already running. Use /cancelimport to stop it.")
        reply = update.effective_message.reply_to_message
        if len(context.args) == 1:
            name = sticker_set_name(context.args[0])
        elif not context.args and reply and reply.sticker and reply.sticker.set_name:
            name = reply.sticker.set_name
        else:
            raise ValueError(
                "Usage: /importpack PackName or t.me/addstickers/PackName; or reply to a sticker"
            )
        pack = await context.bot.get_sticker_set(name)
        key = await self.service(context).active_pack_key()
        destination = await self._pack_name(context, key)
        token = secrets.token_hex(8)
        buttons = []
        if self.settings.room_id:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "Create its own Matrix pack", callback_data=f"packimport:{token}:new"
                    )
                ]
            )
        buttons.append(
            [
                InlineKeyboardButton(
                    "Use the selected pack", callback_data=f"packimport:{token}:current"
                )
            ]
        )
        buttons.append([InlineKeyboardButton("Cancel", callback_data=f"packimport:{token}:cancel")])
        # One outstanding prompt; older buttons become invalid. No remote writes yet.
        self.pending_import = (token, pack, key, time.monotonic())
        await update.effective_message.reply_text(
            f"Import {pack.title} ({len(pack.stickers)} stickers) into its own Matrix pack?\n"
            f"Selected pack: {destination}\nNo stickers are imported until you choose. "
            "The import stops if its destination fills up.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    @private_command
    async def choose_import(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":")
        if len(parts) != 3 or parts[2] not in {"new", "current", "cancel"}:
            raise ValueError("Invalid import choice. Run /importpack again.")
        pending = self.pending_import
        if pending is None or pending[0] != parts[1] or time.monotonic() - pending[3] > 600:
            raise ValueError("This import choice expired or was replaced. Run /importpack again.")
        if self.pack_lock.locked():
            raise ValueError("A pack import is already running. Use /cancelimport first.")
        self.pending_import = None  # consume before any await; buttons cannot be replayed
        if parts[2] == "cancel":
            await query.edit_message_text("Import cancelled. No stickers or packs were created.")
            return
        await self._run_pack(update, context, pending[1], pending[2], own_pack=parts[2] == "new")

    async def _run_pack(self, update, context, pack, key, *, own_pack=False):
        async with self.pack_lock:
            if self.stopping:
                await update.effective_message.reply_text(
                    "The bot is restarting. Please retry the import shortly."
                )
                return
            self.cancel_pack.clear()
            if own_pack:
                key = await self.service(context).create_pack((pack.title or pack.name)[:80])
            destination = await self._pack_name(context, key)
            if getattr(update, "callback_query", None):
                try:
                    await update.callback_query.edit_message_reply_markup(reply_markup=None)
                except TelegramError:
                    pass
            progress = await update.effective_message.reply_text(
                f"Importing {pack.title}: {len(pack.stickers)} stickers into {destination}. "
                "Use /cancelimport to stop."
            )
            added = existing = failed = 0
            errors = []
            last_progress = asyncio.get_running_loop().time()
            for index, sticker in enumerate(pack.stickers, start=1):
                if self.cancel_pack.is_set():
                    break
                stop = False
                try:
                    _, created = await self._import_one(
                        context,
                        sticker,
                        default_name=f"{pack.title[:64]} {index}",
                        pack_name=pack.name,
                        target_pack_key=key,
                    )
                    added += int(created)
                    existing += int(not created)
                except PackFullError as exc:
                    errors.append(str(exc))
                    stop = True
                except httpx.HTTPStatusError as exc:
                    failed += 1
                    LOG.warning("%s", describe_http_error(exc, self.settings))
                    errors.append(public_http_error(exc))
                    # Stop on API errors rather than uploading the rest of an unusable pack.
                    stop = True
                except (httpx.HTTPError, TelegramError):
                    failed += 1
                    errors.append(f"Sticker {index}: network or download failure; retry this pack.")
                    stop = True
                except MatrixProtocolError:
                    failed += 1
                    errors.append("Matrix returned invalid data. Check the server configuration.")
                    LOG.warning("Pack import stopped on invalid Matrix response data")
                    stop = True
                except (ValueError, OSError, Image.DecompressionBombError):
                    failed += 1
                    errors.append(
                        f"Sticker {index}: conversion failed or pack metadata is invalid."
                    )
                now = asyncio.get_running_loop().time()
                if index % 10 == 0 or now - last_progress >= 15:
                    try:
                        await progress.edit_text(
                            f"{pack.title}: {index}/{len(pack.stickers)} processed · "
                            f"{added} added · {existing} already present · {failed} failed"
                        )
                    except TelegramError:
                        pass
                    last_progress = now
                if stop:
                    break
            processed = added + existing + failed
            remaining = len(pack.stickers) - processed
            status = "Stopped" if remaining or self.cancel_pack.is_set() else "Finished"
            summary = (
                f"{status}: {pack.title}\n{added} added · {existing} already present · "
                f"{failed} failed · {remaining} unprocessed\n"
                f"Pack: {destination}"
            )
            if remaining:
                summary += (
                    "\nSelect this pack, repeat /importpack, "
                    "and choose the selected pack to resume."
                )
            if errors:
                summary += "\n\nFirst error:\n" + errors[0]
            await update.effective_message.reply_text(summary)

    @private_command
    async def cancel_import(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.cancel_pack.set()
        pending = self.pending_import is not None
        self.pending_import = None
        await update.effective_message.reply_text(
            "Stopping after the current sticker. Imported stickers will be kept."
            if self.pack_lock.locked()
            else "Pending import cancelled."
            if pending
            else "No pack import is running."
        )

    @private_command
    async def packs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            page = int(context.args[0]) if context.args else 1
        except ValueError:
            raise ValueError("Usage: /packs [page]") from None
        packs = await self.service(context).list_packs()
        pages = max(1, (len(packs) + PAGE_SIZE - 1) // PAGE_SIZE)
        if len(context.args) > 1 or not 1 <= page <= pages:
            raise ValueError(f"Usage: /packs [page], between 1 and {pages}")
        lines = [f"Matrix packs · page {page}/{pages}"]
        for pack in packs[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]:
            percent = min(100, round(pack.used_bytes * 100 / ADMISSION_BYTES))
            mark = " · selected" if pack.selected else ""
            lines.append(
                f"\n{pack.name[:80]}{mark}\nID: {pack.key}\n"
                f"{pack.count} stickers · {percent}% capacity"
            )
        lines.append("\n/newpack Name · /usepack ID · /renamepack ID New name")
        if page < pages:
            lines.append(f"Next: /packs {page + 1}")
        await update.effective_message.reply_text("\n".join(lines))

    @private_command
    async def new_pack(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.service(context).create_pack(validate_name(" ".join(context.args)))
        await update.effective_message.reply_text(
            "Pack created and selected. Enable it in Sable to use it globally."
        )

    @private_command
    async def use_pack(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) != 1:
            raise ValueError("Usage: /usepack ID")
        await self.service(context).select_pack(context.args[0])
        await update.effective_message.reply_text("Pack selected. New stickers will be added here.")

    @private_command
    async def rename_pack(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) < 2:
            raise ValueError("Usage: /renamepack ID New name")
        await self.service(context).rename_pack(context.args[0], " ".join(context.args[1:]))
        await update.effective_message.reply_text("Pack renamed.")

    @private_command
    async def list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) > 1:
            raise ValueError("Usage: /stickers [page]")
        try:
            page = int(context.args[0]) if context.args else 1
        except ValueError:
            raise ValueError("Page must be a positive number. Example: /stickers 2") from None
        items = await self.service(context).list_stickers()
        pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
        if not 1 <= page <= pages:
            raise ValueError(f"Page must be between 1 and {pages}")
        if not items:
            await update.effective_message.reply_text(
                "Your sticker library is empty. Send a sticker!"
            )
            return
        name = await self._pack_name(context, items[0].pack_key)
        lines = [f"{name}\n{len(items)} stickers · page {page}/{pages}"]
        for item in items[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]:
            # Bound existing third-party metadata to Telegram's message limit.
            label = " ".join(item.name.split())[:220]
            lines.append(f"\n{label}\n:{item.shortcode[:100]}:")
        lines.append("\n/view ID · /rename ID Name · /setid ID New_id · /remove ID")
        if page < pages:
            lines.append(f"Next: /stickers {page + 1}")
        await update.effective_message.reply_text("\n".join(lines))

    @private_command
    async def view(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        shortcode, remaining = self.target(update, context.args)
        if remaining:
            raise ValueError("Usage: /view ID (or reply to a sticker with /view)")
        service = self.service(context)
        item = await service.get_sticker(shortcode)
        data = await service.api.download(item.uri)
        # Send the actual Matrix media as a document to preserve transparency,
        # dimensions, and animated formats already present in an existing pack.
        with Image.open(BytesIO(data)) as image:
            extension = {"PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "JPEG": ".jpg"}.get(
                image.format,
                ".image",
            )
        await update.effective_message.reply_document(
            document=BytesIO(data),
            filename=f"matrix-sticker{extension}",
            caption=f"{item.name[:220]}\n:{item.shortcode[:100]}:",
        )

    @private_command
    async def rename(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        shortcode, words = self.target(update, context.args)
        if not words:
            raise ValueError("Usage: /rename ID New name (or reply with /rename New name)")
        item = await self.service(context).rename(shortcode, " ".join(words))
        await update.effective_message.reply_text(f"Renamed to {item.name}\n:{item.shortcode}:")

    async def _pack_name(self, context, key):
        packs = await self.service(context).list_packs()
        return next((pack.name for pack in packs if pack.key == key), "Selected pack")

    @private_command
    async def set_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reference, words = self.target(update, context.args)
        if len(words) != 1:
            raise ValueError("Usage: /setid ID New_id, or reply to a sticker with /setid New_id")
        item = await self.service(context).set_id(reference, words[0])
        await update.effective_message.reply_text(f"Sticker ID changed to :{item.shortcode}:")

    @private_command
    async def suffix(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        service = self.service(context)
        if not context.args:
            value = await service.get_suffix()
            await update.effective_message.reply_text(
                f"Current suffix: {value}" if value else "The sticker suffix is hidden."
            )
            return
        text = " ".join(context.args)
        value = None if text.lower() == "default" else "" if text.lower() == "off" else text
        count = await service.set_suffix(value)
        await update.effective_message.reply_text(
            f"Suffix updated across the room ({count} packs changed)."
        )

    @private_command
    async def remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        shortcode, remaining = self.target(update, context.args)
        if remaining:
            raise ValueError("Usage: /remove ID (or reply to a sticker with /remove)")
        removed = await self.service(context).remove(shortcode)
        await update.effective_message.reply_text(
            "Removed from your configured Matrix packs. Existing messages keep their media."
            if removed
            else "That sticker is already absent from your configured packs."
        )
