"""Matrix HTTP operations. No Telegram dependencies or application state."""

import re
from urllib.parse import quote, urlsplit

import httpx

from matrix_stickers.config import Settings
from matrix_stickers.images import MAX_STICKER_BYTES
from matrix_stickers.packs import LABEL_SETTINGS_TYPE, PACK_KEY, ROOM_PACK_TYPE


class MatrixProtocolError(ValueError):
    """The homeserver returned data that cannot be safely used by the application."""


def describe_http_error(error: httpx.HTTPStatusError, settings: Settings) -> str:
    """Expose the failed operation and Matrix error without credentials or query strings."""
    response = error.response
    details = []
    try:
        payload = response.json()
    except (ValueError, httpx.ResponseNotRead):
        payload = {}
    if isinstance(payload, dict):
        for key in ("errcode", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                details.append(value)
    text = f"Matrix HTTP {response.status_code}\n{error.request.method} {error.request.url.path}"
    if details:
        text += "\n" + " — ".join(details)
    # Redact before truncating: a long echoed token must not be partially exposed.
    for secret in (settings.matrix_token, settings.telegram_token):
        if secret:
            text = text.replace(secret, "[redacted]").replace(quote(secret, safe=""), "[redacted]")
    return "\n".join(" ".join(line.split()) for line in text.splitlines())[:1500]


def public_http_error(error: httpx.HTTPStatusError) -> str:
    """Actionable Telegram text, without server internals or reflected response bodies."""
    status = error.response.status_code
    if status == 401:
        return "The Matrix session expired. Update the bot's access token and restart it."
    if status == 403:
        return (
            "Matrix denied access. Check the bot's room membership, permissions and token access."
        )
    if status == 413:
        return (
            "Matrix rejected this update as too large. "
            "Try a smaller sticker or free space in the pack."
        )
    if status == 429:
        return "Matrix is limiting requests. Wait a moment and try again."
    return (
        "Matrix could not complete the request. Please try again; details are in the server logs."
    )


class MatrixAPI:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.homeserver,
            headers={"Authorization": f"Bearer {settings.matrix_token}"},
            timeout=40.0,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def path_segment(value: str) -> str:
        return quote(value, safe="")

    def account_url(self, event_type: str) -> str:
        return (
            "/_matrix/client/v3/user/"
            + self.path_segment(self.settings.matrix_user_id)
            + "/account_data/"
            + self.path_segment(event_type)
        )

    @staticmethod
    def json_object(response: httpx.Response) -> dict:
        response.raise_for_status()
        try:
            content = response.json()
        except ValueError:
            raise MatrixProtocolError(
                f"Matrix returned an empty or non-JSON response for {response.request.url.path} "
                f"(HTTP {response.status_code}). Check MATRIX_HOMESERVER: use the Matrix API "
                "base URL from /.well-known/matrix/client under m.homeserver.base_url."
            ) from None
        if not isinstance(content, dict):
            raise MatrixProtocolError("Matrix returned a JSON value instead of an object")
        return content

    async def get_account_data(self, event_type: str) -> dict:
        response = await self.client.get(self.account_url(event_type))
        if response.status_code == 404:
            return {}
        return self.json_object(response)

    async def put_account_data(self, event_type: str, data: dict) -> None:
        response = await self.client.put(self.account_url(event_type), json=data)
        response.raise_for_status()

    async def upload(self, png: bytes, mimetype: str = "image/png") -> str:
        extension = "webp" if mimetype == "image/webp" else "png"
        response = await self.client.post(
            "/_matrix/media/v3/upload",
            params={"filename": f"telegram-sticker.{extension}"},
            content=png,
            headers={"Content-Type": mimetype},
        )
        uri = self.json_object(response).get("content_uri")
        if not isinstance(uri, str) or not uri.startswith("mxc://"):
            raise MatrixProtocolError("Matrix media upload returned an invalid MXC URI")
        return uri

    def state_url(self, key: str = PACK_KEY) -> str:
        return (
            "/_matrix/client/v3/rooms/"
            + self.path_segment(self.settings.room_id)
            + "/state/"
            + self.path_segment(ROOM_PACK_TYPE)
            + "/"
            + self.path_segment(key)
        )

    async def get_room_pack(self) -> dict:
        response = await self.client.get(self.state_url())
        if response.status_code == 404:
            return {}
        return self.json_object(response)

    async def put_room_pack(self, data: dict, key: str = PACK_KEY) -> None:
        response = await self.client.put(self.state_url(key), json=data)
        response.raise_for_status()

    async def get_room_library(self) -> tuple[str, dict[str, dict]]:
        """Read the room name and user-managed importer packs from current state."""
        response = await self.client.get(
            "/_matrix/client/v3/rooms/" + self.path_segment(self.settings.room_id) + "/state"
        )
        response.raise_for_status()
        try:
            events = response.json()
        except ValueError:
            raise MatrixProtocolError("Matrix room state returned a non-JSON response") from None
        if not isinstance(events, list):
            raise MatrixProtocolError("Matrix room state must be a list")
        name = self.settings.room_id
        suffix = None
        packs = {}
        for event in events:
            if not isinstance(event, dict):
                raise MatrixProtocolError("Matrix room state contains an invalid event")
            content = event.get("content", {})
            if not isinstance(content, dict):
                raise MatrixProtocolError("Matrix room state event has invalid content")
            key = event.get("state_key", "")
            if event.get("type") == LABEL_SETTINGS_TYPE and key == "":
                suffix = content.get("suffix")
                if suffix is not None and (not isinstance(suffix, str) or len(suffix) > 80):
                    raise ValueError("Room label settings are invalid")
            if event.get("type") == "m.room.name" and key == "":
                if isinstance(content.get("name"), str) and content["name"].strip():
                    name = " ".join(content["name"].split())[:80]
            if (
                event.get("type") == ROOM_PACK_TYPE
                and isinstance(key, str)
                and re.fullmatch(r"telegram_imports(?:_[A-Za-z0-9_-]+)?", key)
            ):
                packs[key] = content
        return suffix if suffix is not None else name, packs

    async def get_room_name(self) -> str:
        path = "/_matrix/client/v3/rooms/" + self.path_segment(self.settings.room_id)
        response = await self.client.get(path + "/state/m.room.name/")
        if response.status_code == 404:
            return self.settings.room_id
        name = self.json_object(response).get("name")
        return (
            " ".join(name.split())[:80]
            if isinstance(name, str) and name.strip()
            else self.settings.room_id
        )

    async def set_room_suffix(self, suffix: str | None) -> None:
        path = "/_matrix/client/v3/rooms/" + self.path_segment(self.settings.room_id)
        response = await self.client.put(
            path + "/state/" + LABEL_SETTINGS_TYPE + "/",
            json={} if suffix is None else {"suffix": suffix},
        )
        response.raise_for_status()

    async def verify_user(self) -> None:
        response = await self.client.get("/_matrix/client/v3/account/whoami")
        if response.status_code == 401:
            raise ValueError(
                "Matrix rejected MATRIX_ACCESS_TOKEN (401 Unauthorized). Obtain a fresh token "
                "for the bot account (ESS/MAS: Element Admin > Personal tokens), update .env, "
                "and recreate the container."
            )
        actual = self.json_object(response).get("user_id")
        if not isinstance(actual, str) or not actual:
            raise ValueError("Matrix whoami response is missing a valid user_id")
        if actual != self.settings.matrix_user_id:
            raise ValueError(
                f"MATRIX_ACCESS_TOKEN belongs to {actual!r}, not {self.settings.matrix_user_id!r}"
            )

    async def download(self, uri: str) -> bytes:
        """Fetch MXC media through our homeserver, with a strict memory limit."""
        parsed = urlsplit(uri)
        media_id = parsed.path.removeprefix("/")
        if (
            parsed.scheme != "mxc"
            or not parsed.netloc
            or not media_id
            or "/" in media_id
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Sticker has an invalid MXC URI")
        path = (
            "/_matrix/client/v1/media/download/"
            + self.path_segment(parsed.netloc)
            + "/"
            + self.path_segment(media_id)
        )
        async with self.client.stream("GET", path) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_STICKER_BYTES:
                    raise ValueError("Sticker preview is too large")
        return bytes(content)
