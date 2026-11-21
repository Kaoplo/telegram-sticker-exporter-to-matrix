"""Parse pack names locally; never fetch arbitrary user-supplied URLs."""

import re
from urllib.parse import parse_qs, urlsplit


def sticker_set_name(value: str) -> str:
    value = value.strip()
    if value.startswith(("t.me/", "telegram.me/")):
        value = "https://" + value
    if value.startswith(("https://", "http://")):
        url = urlsplit(value)
        if url.hostname not in {"t.me", "telegram.me"} or url.username or url.password:
            raise ValueError("Use a Telegram t.me/addstickers/ link or a sticker pack name")
        match = re.fullmatch(r"/addstickers/([A-Za-z0-9_]+)/?", url.path)
        if not match:
            raise ValueError("Use a t.me/addstickers/PackName link")
        value = match[1]
    elif value.startswith("tg://"):
        url = urlsplit(value)
        if url.netloc != "addstickers":
            raise ValueError("Use a sticker pack link")
        value = parse_qs(url.query).get("set", [""])[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", value):
        raise ValueError("Sticker pack names must contain letters, numbers and underscores")
    return value
