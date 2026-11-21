"""Load configuration at startup, without side effects when modules are imported."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    telegram_token: str = field(repr=False)
    telegram_user_id: int
    homeserver: str
    matrix_user_id: str
    matrix_token: str = field(repr=False)
    room_id: str = ""
    pack_mode: str = "room"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ

        def required(key: str) -> str:
            value = env.get(key, "").strip()
            if not value:
                raise ValueError(f"Missing required environment variable: {key}")
            return value

        homeserver = required("MATRIX_HOMESERVER").rstrip("/")
        url = urlsplit(homeserver)
        schemes = {"https", "http"} if env.get("ALLOW_HTTP") == "1" else {"https"}
        if (
            url.scheme not in schemes
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("MATRIX_HOMESERVER must be an HTTPS Matrix API base URL")
        user_id = required("MATRIX_USER_ID")
        if not user_id.startswith("@") or ":" not in user_id:
            raise ValueError("MATRIX_USER_ID must be a full Matrix ID, such as @alice:example.org")
        try:
            telegram_user_id = int(required("TELEGRAM_USER_ID"))
        except ValueError:
            raise ValueError("TELEGRAM_USER_ID must be a positive numeric user ID") from None
        if telegram_user_id <= 0:
            raise ValueError("TELEGRAM_USER_ID must be a positive numeric user ID")
        mode = env.get("MATRIX_PACK_MODE", "room").strip().lower()
        if mode not in {"room", "personal"}:
            raise ValueError("MATRIX_PACK_MODE must be room or personal")
        room_id = required("MATRIX_PACK_ROOM_ID") if mode == "room" else ""
        if room_id and (not room_id.startswith("!") or any(c.isspace() for c in room_id)):
            raise ValueError("MATRIX_PACK_ROOM_ID must be the exact internal !room ID")
        return cls(
            telegram_token=required("TELEGRAM_BOT_TOKEN"),
            telegram_user_id=telegram_user_id,
            homeserver=homeserver,
            matrix_user_id=user_id,
            matrix_token=required("MATRIX_ACCESS_TOKEN"),
            room_id=room_id,
            pack_mode=mode,
        )
