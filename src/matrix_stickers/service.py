"""User-managed room packs, source labels, and capacity limits."""

import asyncio
import json
import logging
import secrets
from copy import deepcopy
from dataclasses import dataclass

from matrix_stickers.images import validate_name, validate_shortcode
from matrix_stickers.matrix import MatrixAPI
from matrix_stickers.packs import (
    PACK_KEY,
    PERSONAL_PACK_TYPE,
    pack_images,
    removed_from_pack,
    updated_pack,
)

SOURCE_KEY = "io.github.kaoplo.telegram_stickers"
SELECTION_TYPE = "io.github.kaoplo.telegram_stickers.selection"
MAX_PACK_BYTES = 48_000
ADMISSION_BYTES = 46_000
LOG = logging.getLogger(__name__)


class PackFullError(ValueError):
    """The chosen pack cannot accept this sticker without user intervention."""


class PackUnavailableError(ValueError):
    """A previously selected pack is no longer present in the room."""


class PartialUpdateError(ValueError):
    """A room-wide change needs another attempt after a remote write failed."""


@dataclass(frozen=True)
class Sticker:
    shortcode: str
    name: str
    uri: str
    pack_key: str = PACK_KEY


@dataclass(frozen=True)
class Pack:
    key: str
    name: str
    count: int
    used_bytes: int
    selected: bool


class StickerService:
    def __init__(self, api: MatrixAPI):
        self.api = api
        self.lock = asyncio.Lock()

    async def _load(self) -> tuple[str, dict[str, dict]]:
        if self.api.settings.room_id:
            name, packs = await self.api.get_room_library()
        else:
            name = ""
            packs = {PACK_KEY: await self.api.get_account_data(PERSONAL_PACK_TYPE)}
        for pack in packs.values():
            pack_images(pack)
            if not isinstance(pack.get("pack", {}), dict):
                raise ValueError("Pack metadata is invalid")
        return name, packs or {PACK_KEY: {}}

    async def _selected(self, packs: dict, requested: str | None = None) -> str:
        if requested is None and self.api.settings.room_id:
            preferences = await self.api.get_account_data(SELECTION_TYPE)
            rooms = preferences.get("rooms", {})
            if not isinstance(rooms, dict):
                raise ValueError("Stored pack selections are invalid")
            requested = rooms.get(self.api.settings.room_id, PACK_KEY)
        key = requested if requested is not None else PACK_KEY
        if not isinstance(key, str):
            raise ValueError("Stored pack selection is invalid")
        if key not in packs:
            if key == PACK_KEY:
                packs[PACK_KEY] = {}
            else:
                raise PackUnavailableError(
                    "Selected pack is unavailable. Use /packs and /usepack ID."
                )
        return key

    async def _select(self, key: str) -> None:
        preferences = deepcopy(await self.api.get_account_data(SELECTION_TYPE))
        rooms = preferences.setdefault("rooms", {})
        if not isinstance(rooms, dict):
            raise ValueError("Stored pack selections are invalid")
        rooms[self.api.settings.room_id] = key
        await self.api.put_account_data(SELECTION_TYPE, preferences)

    async def active_pack_key(self) -> str:
        async with self.lock:
            _, packs = await self._load()
            return await self._selected(packs)

    async def list_packs(self) -> list[Pack]:
        async with self.lock:
            room_name, packs = await self._load()
            try:
                selected = await self._selected(packs)
            except PackUnavailableError:
                selected = ""
            return [
                Pack(
                    key,
                    str(pack.get("pack", {}).get("display_name") or room_name or "My Stickers"),
                    len(pack_images(pack)),
                    self._size(pack),
                    key == selected,
                )
                for key, pack in sorted(packs.items())
            ]

    async def select_pack(self, key: str) -> None:
        async with self.lock:
            _, packs = await self._load()
            if key not in packs:
                raise ValueError("Pack not found. Use /packs to find its ID.")
            if self.api.settings.room_id:
                await self._select(key)

    async def create_pack(self, name: str) -> str:
        name = validate_name(name)
        if not self.api.settings.room_id:
            raise ValueError("Configure MATRIX_PACK_ROOM_ID to create multiple packs")
        async with self.lock:
            _, packs = await self._load()
            key = f"{PACK_KEY}_{secrets.token_hex(6)}"
            while key in packs:
                key = f"{PACK_KEY}_{secrets.token_hex(6)}"
            await self._save(
                {"images": {}, "pack": {"display_name": name, "usage": ["sticker"]}}, key
            )
            await self._select(key)
            return key

    async def rename_pack(self, key: str, name: str) -> None:
        name = validate_name(name)
        async with self.lock:
            _, packs = await self._load()
            if key not in packs:
                raise ValueError("Pack not found. Use /packs to find its ID.")
            pack = deepcopy(packs[key])
            pack.setdefault("pack", {})["display_name"] = name
            if self._size(pack) > MAX_PACK_BYTES:
                raise PackFullError("Pack is full; shorten its name or remove a sticker first")
            await self._save(pack, key)

    def _decorate(
        self, entry: dict, room_name: str, *, name=None, emoji=None, set_name=None
    ) -> dict:
        result = deepcopy(entry)
        source = deepcopy(result.get(SOURCE_KEY, {}))
        if not isinstance(source, dict):
            raise ValueError("Sticker source metadata is invalid")
        base = (
            name
            if name is not None
            else source.get("name", result.get("body") or "Telegram sticker")
        )
        source["name"] = validate_name(str(base)[:80])
        if emoji is not None:
            source["emoji"] = emoji[:32]
        if set_name is not None:
            source["telegram_set"] = set_name[:100]
        source["room_id"] = self.api.settings.room_id
        result[SOURCE_KEY] = source
        label = f"{source.get('emoji', '')} {source['name']}".strip()
        result["body"] = f"{label} · {room_name}" if room_name else label
        return result

    def _sticker(self, shortcode: str, entry: dict, room_name: str, key: str) -> Sticker:
        return Sticker(shortcode, self._decorate(entry, room_name)["body"], entry["url"], key)

    @staticmethod
    def _find(images: dict, reference: str, *, telegram=False) -> str | None:
        if reference.startswith("telegram:"):
            reference = reference.removeprefix("telegram:")
            telegram = True
        if not telegram:
            reference = validate_shortcode(reference)
            if reference in images:
                return reference
        for code, entry in images.items():
            source = entry.get(SOURCE_KEY, {})
            if isinstance(source, dict) and source.get("telegram_id", code) == reference:
                return code
        return None

    async def set_id(self, reference: str, new_id: str) -> Sticker:
        new_id = validate_shortcode(new_id)
        async with self.lock:
            suffix, packs = await self._load()
            key = await self._selected(packs)
            images = pack_images(packs[key])
            old_id = self._find(images, reference)
            if old_id is None:
                raise ValueError("Sticker not found in the selected pack. Use /stickers.")
            if new_id != old_id and new_id in images:
                raise ValueError("That sticker ID is already used in this pack. Choose another.")
            entry = self._decorate(images[old_id], suffix)
            entry[SOURCE_KEY].setdefault("telegram_id", old_id)
            updated = removed_from_pack(packs[key], old_id)
            updated = self._updated(updated, new_id, entry, suffix, key)
            await self._save(updated, key)
            return self._sticker(new_id, entry, suffix, key)

    async def get_suffix(self) -> str:
        async with self.lock:
            suffix, _ = await self._load()
            return suffix

    async def set_suffix(self, value: str | None) -> int:
        if not self.api.settings.room_id:
            raise ValueError("Room suffix settings require room mode")
        if value:
            value = validate_name(value)
        async with self.lock:
            suffix = await self.api.get_room_name() if value is None else value
            _, packs = await self._load()
            updates = {}
            for key, pack in packs.items():
                updated = deepcopy(pack)
                for code, entry in pack_images(pack).items():
                    updated.setdefault("images", {})[code] = self._decorate(entry, suffix)
                if self._size(updated) > MAX_PACK_BYTES:
                    raise PackFullError(
                        "A pack is too full for this suffix. Shorten it or remove stickers first."
                    )
                if updated != pack:
                    updates[key] = updated
            # Preferences and pack labels are separate Matrix state events.
            # Preflight every pack before changing any remote state.
            await self.api.set_room_suffix(value)
            try:
                for key, pack in updates.items():
                    await self._save(pack, key)
            except Exception as exc:
                LOG.warning("Room suffix update stopped: %s", type(exc).__name__)
                raise PartialUpdateError(
                    "The suffix was saved, but some stickers could not be updated. "
                    "Retry the same /suffix command to finish."
                ) from exc
            return len(updates)

    @staticmethod
    def _size(pack: dict) -> int:
        return len(json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode())

    def _updated(
        self, old: dict, shortcode: str, entry: dict, room_name: str, key: str, *, adding=False
    ) -> dict:
        result = updated_pack(old, shortcode, entry)
        if room_name and not old.get("pack", {}).get("display_name"):
            result["pack"]["display_name"] = room_name
        limit = ADMISSION_BYTES if adding else MAX_PACK_BYTES
        if self._size(result) > limit:
            raise PackFullError(
                f"Pack {key} is full. Remove stickers or create/select another pack "
                "with /newpack Name or /packs and /usepack ID. No extra pack was created."
            )
        return result

    async def _save(self, pack: dict, key: str) -> None:
        if self.api.settings.room_id:
            await self.api.put_room_pack(pack, key)
        else:
            await self.api.put_account_data(PERSONAL_PACK_TYPE, pack)

    async def list_stickers(self) -> list[Sticker]:
        async with self.lock:
            room_name, packs = await self._load()
            key = await self._selected(packs)
            return sorted(
                [
                    self._sticker(code, entry, room_name, key)
                    for code, entry in pack_images(packs[key]).items()
                ],
                key=lambda item: (item.name.casefold(), item.shortcode),
            )

    async def get_sticker(self, shortcode: str) -> Sticker:
        async with self.lock:
            suffix, packs = await self._load()
            key = await self._selected(packs)
            code = self._find(pack_images(packs[key]), shortcode)
            if code is None:
                raise ValueError("Sticker not found in the selected pack. Use /stickers.")
            return self._sticker(code, packs[key]["images"][code], suffix, key)

    async def install(
        self,
        shortcode: str,
        png: bytes,
        width: int,
        height: int,
        name: str | None = None,
        *,
        emoji=None,
        set_name=None,
        mimetype="image/png",
        pack_key=None,
    ) -> tuple[Sticker, bool]:
        if name is not None:
            name = validate_name(name)
        async with self.lock:
            room_name, packs = await self._load()
            key = await self._selected(packs, pack_key)
            images = pack_images(packs[key])
            actual_code = self._find(images, shortcode, telegram=True)
            entry = images.get(actual_code)
            added = actual_code is None
            if added and shortcode in images:
                raise ValueError(
                    "This generated ID is already assigned to another sticker. "
                    "Change it with /setid first."
                )
            code = actual_code if actual_code is not None else shortcode
            if added:
                entry = {
                    "url": "mxc://" + "x" * 500,
                    "body": name or "Telegram sticker",
                    "info": {"mimetype": mimetype, "w": width, "h": height, "size": len(png)},
                }
            entry = self._decorate(entry, room_name, name=name, emoji=emoji, set_name=set_name)
            entry[SOURCE_KEY].setdefault("telegram_id", shortcode)
            self._updated(packs[key], code, entry, room_name, key, adding=added)
            if added:
                entry["url"] = await self.api.upload(png, mimetype)
            updated = self._updated(packs[key], code, entry, room_name, key, adding=added)
            if updated != packs[key]:
                await self._save(updated, key)
            return self._sticker(code, entry, room_name, key), added

    async def _reuse(
        self, shortcode, *, name=None, emoji=None, set_name=None, pack_key=None, copy=False
    ):
        if name is not None:
            name = validate_name(name)
        async with self.lock:
            room_name, packs = await self._load()
            key = await self._selected(packs, pack_key)
            source = key
            code = self._find(pack_images(packs[key]), shortcode, telegram=True)
            entry = packs[key].get("images", {}).get(code)
            if entry is None and copy:
                for other in sorted(packs):
                    found = self._find(pack_images(packs[other]), shortcode, telegram=True)
                    if found is not None:
                        source, code = other, found
                        entry = packs[other]["images"][code]
                        break
            if entry is None:
                return None
            if source != key and code in pack_images(packs[key]):
                raise ValueError(
                    "This sticker ID is already used in the destination pack. "
                    "Change it with /setid first."
                )
            entry = self._decorate(entry, room_name, name=name, emoji=emoji, set_name=set_name)
            entry[SOURCE_KEY].setdefault("telegram_id", shortcode)
            updated = self._updated(packs[key], code, entry, room_name, key, adding=source != key)
            if updated != packs[key]:
                await self._save(updated, key)
            return self._sticker(code, entry, room_name, key)

    async def refresh(self, shortcode: str, **kwargs) -> Sticker | None:
        return await self._reuse(shortcode, **kwargs)

    async def copy_existing(self, shortcode: str, **kwargs) -> Sticker | None:
        return await self._reuse(shortcode, copy=True, **kwargs)

    async def rename(self, shortcode: str, name: str) -> Sticker:
        name = validate_name(name)
        async with self.lock:
            suffix, packs = await self._load()
            key = await self._selected(packs)
            code = self._find(pack_images(packs[key]), shortcode)
            if code is None:
                raise ValueError("Sticker not found in the selected pack. Use /stickers.")
            entry = self._decorate(packs[key]["images"][code], suffix, name=name)
            await self._save(self._updated(packs[key], code, entry, suffix, key), key)
            return self._sticker(code, entry, suffix, key)

    async def remove(self, shortcode: str) -> bool:
        async with self.lock:
            _, packs = await self._load()
            key = await self._selected(packs)
            code = self._find(pack_images(packs[key]), shortcode)
            if code is None:
                return False
            await self._save(removed_from_pack(packs[key], code), key)
            return True
