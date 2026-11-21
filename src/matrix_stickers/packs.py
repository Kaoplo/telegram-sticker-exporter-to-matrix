"""Pure pack transformations that preserve unrelated Matrix metadata."""

from copy import deepcopy

PERSONAL_PACK_TYPE = "im.ponies.user_emotes"
ROOM_PACK_TYPE = "m.room.image_pack"
PACK_KEY = "telegram_imports"
LABEL_SETTINGS_TYPE = "io.github.kaoplo.telegram_stickers.settings"


def pack_images(pack: dict) -> dict:
    images = pack.get("images", {})
    if not isinstance(images, dict):
        raise ValueError("Existing image pack has an invalid images field")
    for key, image in images.items():
        if not isinstance(image, dict) or not isinstance(image.get("url"), str):
            raise ValueError(f"Existing image pack has an invalid entry: {key}")
    return images


def updated_pack(old: dict, shortcode: str, sticker: dict) -> dict:
    pack_images(old)
    result = deepcopy(old)
    result.setdefault("images", {})[shortcode] = deepcopy(sticker)
    info = result.setdefault("pack", {})
    if not isinstance(info, dict):
        raise ValueError("Existing image pack has an invalid pack field")
    info.setdefault("display_name", "My Stickers")
    usage = info.get("usage", [])
    if not isinstance(usage, list):
        raise ValueError("Existing image pack has an invalid usage field")
    if usage and "sticker" not in usage:
        info["usage"] = [*usage, "sticker"]
    return result


def removed_from_pack(old: dict, shortcode: str) -> dict:
    pack_images(old)
    result = deepcopy(old)
    result.get("images", {}).pop(shortcode, None)
    return result
