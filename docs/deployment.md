# Deployment

## Required configuration

Use one importer instance per Telegram bot token. Create a dedicated Matrix
account, join it to the destination room, and give it permission to write
`m.room.image_pack` and `io.github.kaoplo.telegram_stickers.settings` state.
For ESS/MAS, issue a dedicated personal access token with Matrix Client-Server
API access. No Matrix server-administration scope is needed.

Copy `.env.example` to `.env` and configure credentials and the exact room ID:

```dotenv
MATRIX_PACK_MODE=room
MATRIX_HOMESERVER=https://matrix.pawz.hu
MATRIX_USER_ID=@stickers:pawz.hu
MATRIX_PACK_ROOM_ID=!your_exact_room_id
```

Room mode is the default. A missing room ID or failed identity/room check
prevents startup. Do not append a domain to a version 12 room ID. Personal
pack mode is available only through explicit `MATRIX_PACK_MODE=personal`.
Keep `.env` private and outside Git. The image contains no credentials.

## Build and start

```sh
docker compose up -d --build
docker compose logs --tail=30 -f sticker-importer
```

Compose runs the image as its unprivileged user with a read-only filesystem,
all Linux capabilities dropped, and privilege escalation disabled. Temporary
conversion files go to a writable `/tmp` tmpfs capped at 256 MiB. No persistent
filesystem volume is required: packs, selection, identity metadata and suffix
settings live in Matrix.

Telegram uses outbound polling, so no inbound application port is needed.
Use an external deployment platform or normal Docker monitoring for process
restart/status. `restart: unless-stopped` is configured in Compose.

To use CI's published image, replace the service's `build: .` with:

```yaml
image: ghcr.io/kaoplo/telegram-sticker-exporter-to-matrix:sha-FULL_COMMIT_SHA
```

Choose a published commit tag, keeping the other Compose settings. Then:

```sh
docker compose pull
docker compose up -d
```

The first package may be private; authenticate to GHCR or change its visibility.
The pipeline publishes only after tests and image smoke checks pass on `main`.

## Configuration and updates

Changing `.env` requires container recreation, rather than just a restart:

```sh
docker compose up -d --force-recreate
```

After source changes, rebuild with `docker compose up -d --build`. The image
includes FFmpeg and rlottie, and installs dependencies from `uv.lock`.

Compose allows three minutes for graceful shutdown. A running batch stops
after the current sticker; pending confirmation buttons expire when the
process restarts. Re-run `/importpack` with the same destination to resume.
Already stored stickers, custom shortcodes and pack selection are retained.

## Operations

Normal Telegram replies contain names, useful shortcodes, progress and concise
error explanations. They do not expose HTTP paths, raw Matrix error bodies,
MXC URLs, or repeated diagnostic instructions. Sanitized request diagnostics
remain in the container logs for troubleshooting. Tokens and Authorization
headers are never intentionally logged.

The app uses the configured Matrix token as-is; it does not refresh OAuth
sessions. Replace the dedicated token before its configured expiry and
recreate the container. Startup validates identity and room access before polling.

Enable each pack in Sable from your personal account under
**Settings → Emojis & Stickers → Favorite Packs → Select Pack → Select**.
Choose the pack, click **Save**, then **Apply Changes**. Updates to the same
pack appear without subscribing again.
