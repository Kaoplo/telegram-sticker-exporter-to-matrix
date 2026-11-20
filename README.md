# Telegram → Matrix sticker importer for Sable

Send **static Telegram stickers** to your private Telegram bot. The bot converts
WEBP to PNG, uploads to your Matrix homeserver, and updates your **own Matrix
account**'s personal image pack (`im.ponies.user_emotes`) for use in Sable,
including direct messages. Optionally also creates and globally enables a
standard `m.room.image_pack` stored in a private Matrix room.

## Setup

1. Create a Telegram bot by messaging **@BotFather** and running `/newbot`.
2. Get your **numeric Telegram user ID** (for example, via an ID bot). The bot
   ignores all messages not sent from this ID, and only accepts private chats.
3. Obtain a Matrix access token for **the same Matrix account used in Sable**.
   Use a dedicated login session/token if possible; store it securely. Never
   send it in Telegram or commit it to Git.
4. Copy `.env.example` to `.env` and fill it in. `MATRIX_HOMESERVER` should
   be your HTTPS Matrix client-server API origin (not the web UI URL).
5. **Recommended:** Create a private unencrypted Matrix room with only your
   account as a member. Enable Developer Tools in your client if needed to copy
   its internal `!roomid:server` (not its `#alias`). Add it to
   `MATRIX_PACK_ROOM_ID`. This room holds a standard room-scoped image pack;
   the bot also adds it to `m.image_pack.rooms`, enabling it across Matrix rooms
   and DMs in clients implementing the standard. You must have permission to
   set state events in the room (the room creator normally does).
6. Start the bot, then DM it a static Telegram sticker.

### Local Python

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
set -a; . ./.env; set +a
python bot.py
```

On Windows use the corresponding virtualenv activation and environment loading
commands. Do not use `source .env` if your secrets contain shell-metacharacters;
use a dotenv loader instead. (The Docker option below handles `.env` directly.)

### Docker Compose

```bash
cp .env.example .env
# Edit .env first; keep it secret
docker compose up -d --build
docker compose logs -f sticker-importer
```

## Behavior / compatibility

- **Static `.webp` only** in this version. Animated `.tgs` and video `.webm`
  stickers receive a clear unsupported-format reply, not a broken upload.
- Uses a deterministic shortcode derived from Telegram's `file_unique_id` to
  detect imports. Imports from the same Telegram file twice are idempotent.
- Updates the existing personal sticker pack instead of overwriting its other
  image entries. Pack metadata lives on Matrix; no local volume is needed.
- Optional modern room pack mirrors imports, letting clients that implement
  Matrix's `m.image_pack.rooms` use them across all chats.
- Personal account packs are legacy compatibility behavior; Matrix v1.19's
  standardized solution is room image packs plus global enablement.
- Other people can see stickers you send in conversations; **pack media is
  uploaded unencrypted**. Treat this as a public/non-secret asset collection.
  A private room holding pack metadata does NOT make the uploaded media E2EE.
- After import, Sable may require a reload, or a newly opened sticker picker,
  to notice changed account data.
- If you edit the same pack in Sable while importing, the Matrix API does not
  offer compare-and-swap for account data; the last writer may overwrite
  simultaneous changes. Use a single importer and avoid concurrent edits.

## Troubleshooting

- `M_FORBIDDEN` writing `m.room.image_pack`: verify you're a member and allowed
  to edit room state. Or comment out `MATRIX_PACK_ROOM_ID` to use personal mode.
- `M_UNKNOWN_TOKEN`: Matrix access token has expired / is invalid.
- Empty or non-JSON Matrix response (even with HTTP 200): the homeserver URL
  may point at a website instead of the Matrix API. Open
  `https://YOUR_ACCOUNT_DOMAIN/.well-known/matrix/client` and use the
  `m.homeserver.base_url` value as `MATRIX_HOMESERVER`. For `pawz.hu`, this is
  `https://matrix.pawz.hu`. Recreate the container after editing `.env`:
  `docker compose up -d --build`.
- `M_NOT_FOUND` on upload: check homeserver URL or reverse proxy media routes.
- Not visible in Sable: refresh Sable, check whether your version reads
  `im.ponies.user_emotes`; use the recommended private-room mode if needed.
- Do not place `.env` in a public repository.

## Tests and container publishing

Run tests locally after installing the dependencies:

```bash
python -m unittest discover -v
```

GitHub Actions tests Python 3.12 and 3.13 on pull requests targeting `main`
and pushes to `main`. After the tests pass, it builds and smoke-tests the
container using dummy credentials. Only pushes to `main` publish to GHCR:

- `ghcr.io/kaoplo/telegram-sticker-exporter-to-matrix:latest`
- `ghcr.io/kaoplo/telegram-sticker-exporter-to-matrix:sha-<full-commit-sha>`

The image name follows the repository name automatically (lowercase).
Publishing uses the built-in `GITHUB_TOKEN` with `packages: write`; no registry
secret or bot credentials are needed in CI. Enable GitHub Actions in the
repository. Organization policies must allow package publishing. A manual
workflow run performs checks without publishing.

To run a published image with your local configuration:

```bash
docker run -d --name sticker-importer --restart unless-stopped --env-file .env \
  ghcr.io/kaoplo/telegram-sticker-exporter-to-matrix:latest
```

Run only one importer for a given Telegram bot token. For private GHCR packages,
authenticate to GHCR before pulling, or change the package visibility to public.

Local metadata backups were removed: they only retained one prior version,
had no restore command, and required a writable bind mount. Existing `backups/`
files remain ignored and can be archived or deleted separately.

See: https://spec.matrix.org/ and
https://github.com/matrix-org/matrix-spec-proposals/blob/main/proposals/2545-emotes.md
