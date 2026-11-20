"""Telegram sticker importer for Sable / Matrix.

Static Telegram stickers are copied as PNG to the configured Matrix account's
personal image pack. Optionally mirrors them into a private room's standard
Matrix image pack, enabled for this account in all rooms.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
from urllib.parse import quote

import httpx
from PIL import Image, UnidentifiedImageError
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

LOG = logging.getLogger(__name__)
PERSONAL_PACK_TYPE = "im.ponies.user_emotes"
ROOM_PACK_TYPE = "m.room.image_pack"
ENABLED_PACKS_TYPE = "m.image_pack.rooms"
PACK_KEY = "telegram_imports"
MAX_STICKER_BYTES = 10 * 1024 * 1024


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


TG_TOKEN = required_env("TELEGRAM_BOT_TOKEN")
TG_USER_ID = int(required_env("TELEGRAM_USER_ID"))
MATRIX_HOMESERVER = required_env("MATRIX_HOMESERVER").rstrip("/")
MATRIX_USER_ID = required_env("MATRIX_USER_ID")
MATRIX_ACCESS_TOKEN = required_env("MATRIX_ACCESS_TOKEN")
MATRIX_PACK_ROOM_ID = os.environ.get("MATRIX_PACK_ROOM_ID", "").strip()

if not MATRIX_HOMESERVER.startswith("https://") and os.environ.get("ALLOW_HTTP") != "1":
    raise SystemExit("MATRIX_HOMESERVER must be https:// (set ALLOW_HTTP=1 only for trusted local testing)")

matrix_lock = asyncio.Lock()


class MatrixAPI:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            base_url=MATRIX_HOMESERVER,
            headers={"Authorization": f"Bearer {MATRIX_ACCESS_TOKEN}"},
            timeout=40.0,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def path_segment(s: str) -> str:
        return quote(s, safe="")

    def account_url(self, event_type: str) -> str:
        return ("/_matrix/client/v3/user/" + self.path_segment(MATRIX_USER_ID)
                + "/account_data/" + self.path_segment(event_type))

    @staticmethod
    def json_object(response: httpx.Response) -> dict:
        response.raise_for_status()
        try:
            content = response.json()
        except ValueError:
            raise ValueError(
                f"Matrix returned an empty or non-JSON response for {response.request.url.path} "
                f"(HTTP {response.status_code}). Check MATRIX_HOMESERVER: it must point "
                "to the Matrix client-server API, not the website. Find the API base URL "
                "in /.well-known/matrix/client under m.homeserver.base_url."
            ) from None
        if not isinstance(content, dict):
            raise ValueError(
                f"Matrix returned a JSON value instead of an object for {response.request.url.path}"
            )
        return content

    async def get_account_data(self, event_type: str) -> dict:
        response = await self.client.get(self.account_url(event_type))
        if response.status_code == 404:
            return {}
        return self.json_object(response)

    async def put_account_data(self, event_type: str, data: dict) -> None:
        response = await self.client.put(self.account_url(event_type), json=data)
        response.raise_for_status()

    async def upload(self, png: bytes) -> str:
        response = await self.client.post(
            "/_matrix/media/v3/upload",
            params={"filename": "telegram-sticker.png"},
            content=png,
            headers={"Content-Type": "image/png"},
        )
        uri = self.json_object(response).get("content_uri")
        if not isinstance(uri, str) or not uri.startswith("mxc://"):
            raise ValueError("Matrix media upload returned an invalid MXC URI")
        return uri

    def state_url(self) -> str:
        return ("/_matrix/client/v3/rooms/" + self.path_segment(MATRIX_PACK_ROOM_ID)
                + "/state/" + self.path_segment(ROOM_PACK_TYPE)
                + "/" + self.path_segment(PACK_KEY))

    async def get_room_pack(self) -> dict:
        response = await self.client.get(self.state_url())
        if response.status_code == 404:
            return {}
        return self.json_object(response)

    async def put_room_pack(self, data: dict) -> None:
        response = await self.client.put(self.state_url(), json=data)
        response.raise_for_status()

    async def verify_user(self) -> None:
        response = await self.client.get("/_matrix/client/v3/account/whoami")
        actual = self.json_object(response).get("user_id")
        if not isinstance(actual, str) or not actual:
            raise ValueError("Matrix whoami response is missing a valid user_id")
        if actual != MATRIX_USER_ID:
            raise SystemExit(
                f"MATRIX_ACCESS_TOKEN belongs to {actual!r}, not {MATRIX_USER_ID!r}. "
                "The account must match the account logged into Sable."
            )


def make_shortcode(file_unique_id: str) -> str:
    return "tg_" + hashlib.sha256(file_unique_id.encode("utf-8")).hexdigest()[:20]


def to_png(data: bytes) -> tuple[bytes, int, int]:
    with Image.open(io.BytesIO(data)) as source:
        # Work only with static image stickers. Animated Telegram formats need
        # a separate transcoding pipeline and must not be silently flattened.
        if getattr(source, "is_animated", False):
            raise ValueError("Animated image stickers are not supported yet")
        source.load()
        if source.width > 4096 or source.height > 4096 or source.width * source.height > 16_777_216:
            raise ValueError("Sticker dimensions are too large")
        converted = source.convert("RGBA")
        out = io.BytesIO()
        converted.save(out, format="PNG", optimize=True)
        return out.getvalue(), converted.width, converted.height


def updated_pack(old: dict, shortcode: str, sticker: dict) -> dict:
    result = json.loads(json.dumps(old))  # preserve unrelated fields
    images = result.setdefault("images", {})
    if not isinstance(images, dict):
        raise ValueError("Existing image pack has invalid images field")
    images[shortcode] = sticker
    info = result.setdefault("pack", {})
    if not isinstance(info, dict):
        raise ValueError("Existing image pack has invalid pack field")
    info.setdefault("display_name", "My Stickers")
    usage = info.get("usage", [])
    if usage and "sticker" not in usage:
        info["usage"] = list(dict.fromkeys([*usage, "sticker"]))
    # An omitted or empty usage list already includes both sticker and emote.
    return result


async def install_sticker(api: MatrixAPI, shortcode: str, png: bytes,
                          width: int, height: int, description: str) -> tuple[str, bool]:
    async with matrix_lock:
        personal = await api.get_account_data(PERSONAL_PACK_TYPE)
        if shortcode in personal.get("images", {}):
            return personal["images"][shortcode]["url"], False

        # Prevent accidental duplicate upload when the pack was created only
        # in the modern room-pack format.
        room_pack = await api.get_room_pack() if MATRIX_PACK_ROOM_ID else {}
        if shortcode in room_pack.get("images", {}):
            mxc = room_pack["images"][shortcode]["url"]
        else:
            mxc = await api.upload(png)

        sticker = {"url": mxc, "body": description,
                   "info": {"mimetype": "image/png", "w": width,
                            "h": height, "size": len(png)}}
        await api.put_account_data(PERSONAL_PACK_TYPE,
                                   updated_pack(personal, shortcode, sticker))

        if MATRIX_PACK_ROOM_ID:
            await api.put_room_pack(updated_pack(room_pack, shortcode, sticker))
            enabled = await api.get_account_data(ENABLED_PACKS_TYPE)
            new_enabled = json.loads(json.dumps(enabled))
            rooms = new_enabled.setdefault("rooms", {})
            if not isinstance(rooms, dict):
                raise ValueError("Existing enabled room packs malformed")
            rooms.setdefault(MATRIX_PACK_ROOM_ID, {})[PACK_KEY] = {}
            await api.put_account_data(ENABLED_PACKS_TYPE, new_enabled)
        return mxc, True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != TG_USER_ID:
        return
    await update.effective_message.reply_text(
        "Send me a static Telegram sticker (.webp), and I'll add it to your "
        "Matrix personal sticker pack for use in Sable, including DMs. "
        "Animated .tgs and video .webm stickers are not supported in this version."
    )


async def sticker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != TG_USER_ID:
        return
    sticker = update.effective_message.sticker if update.effective_message else None
    if sticker is None:
        return
    if sticker.is_animated or sticker.is_video:
        await update.effective_message.reply_text(
            "This is an animated/video sticker. This first version supports static .webp stickers only."
        )
        return
    if sticker.file_size and sticker.file_size > MAX_STICKER_BYTES:
        await update.effective_message.reply_text("That sticker is too large to import.")
        return

    shortcode = make_shortcode(sticker.file_unique_id)
    description = f"Telegram sticker {sticker.emoji or ''}".strip()
    try:
        file = await context.bot.get_file(sticker.file_id)
        raw = bytes(await file.download_as_bytearray())
        if len(raw) > MAX_STICKER_BYTES:
            raise ValueError("Sticker file is too large")
        png, width, height = await asyncio.to_thread(to_png, raw)
        api: MatrixAPI = context.application.bot_data["matrix_api"]
        uri, added = await install_sticker(api, shortcode, png, width, height, description)
        if added:
            await update.effective_message.reply_text(
                f"✅ Added to your Matrix sticker pack as {shortcode}.\n{uri}\n"
                "Open the sticker picker in Sable (a reload may be needed)."
            )
        else:
            await update.effective_message.reply_text(f"Already imported as {shortcode}.")
    except (httpx.HTTPError, ValueError, KeyError, UnidentifiedImageError) as exc:
        LOG.exception("Failed importing Telegram sticker")
        await update.effective_message.reply_text(f"Import failed: {type(exc).__name__}: {exc}")


async def on_startup(app: Application) -> None:
    api = MatrixAPI()
    try:
        await api.verify_user()
    except BaseException:
        await api.close()
        raise
    app.bot_data["matrix_api"] = api
    LOG.info("Matrix identity verified: %s", MATRIX_USER_ID)


async def on_shutdown(app: Application) -> None:
    api = app.bot_data.get("matrix_api")
    if api:
        await api.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # HTTP request URLs include the Telegram bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app = (Application.builder().token(TG_TOKEN)
           .post_init(on_startup).post_shutdown(on_shutdown).build())
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Sticker.ALL,
                                   sticker_handler))
    LOG.info("Starting Telegram sticker importer")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
