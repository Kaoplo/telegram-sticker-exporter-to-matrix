# Telegram → Matrix sticker library

Import Telegram stickers and whole packs into Matrix and manage them from
a private Telegram bot. Name stickers, browse your library, preview Matrix
media, and rename or remove entries without opening developer tools.

Publish a shared sticker library in a Matrix room, then enable its packs in
Sable to use them across rooms and DMs. Static, TGS animated, and WebM video
stickers are supported.

## Quick start

1. Create a bot through **@BotFather** and get its token.
2. Get your numeric Telegram user ID. Only this user can use the bot, in private chat.
3. Create a dedicated Matrix bot account. On ESS/MAS, issue a personal token
   in Element Admin for that account with Matrix Client-Server API access.
   A token copied from Sable can expire within minutes and is only useful for testing.
4. Copy `.env.example` to `.env` and fill in your credentials.
5. Set `MATRIX_HOMESERVER` to the Matrix API URL, rather than your web client's URL.
   For `pawz.hu`, use `https://matrix.pawz.hu`.
6. Add the bot and your personal account to a private room. Accept the invitation
   as the bot and give it permission to update room state. Set
   `MATRIX_PACK_ROOM_ID` to that room's exact internal ID.
   Version 12 room IDs have no `:server` suffix; do not add one.
7. Enable the room's sticker packs from your personal account in Sable. The bot
   does not need your personal account's access token.

Keep `.env` private. Bot credentials are supplied at runtime and never built into images.
Room uploads are the default (`MATRIX_PACK_MODE=room`); a missing room ID stops
startup. Legacy personal packs require explicit `MATRIX_PACK_MODE=personal`.
See [configuration](docs/configuration.md) for all settings and troubleshooting.

### Run with uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
these commands from the repository root on Windows, Linux, or macOS:

```sh
uv sync --locked
uv run --locked matrix-stickers
```

The application loads `.env` automatically. Existing environment variables take
precedence. Python 3.12 or newer is required. Dependencies are declared in
`pyproject.toml` and resolved in the committed `uv.lock`.
Install FFmpeg on your system and ensure `ffmpeg` is on `PATH` for WebM imports.
The Docker image includes it. TGS rendering uses the locked `rlottie-python` package.

### Run with Docker Compose

```sh
docker compose up -d --build
docker compose logs -f sticker-importer
```

No writable volume or local database is needed. Pack metadata lives on Matrix.
Compose uses a read-only filesystem, temporary conversion storage, dropped
Linux capabilities, and a graceful shutdown window. See [deployment](docs/deployment.md).
After changing `.env`, recreate the container:

```sh
docker compose up -d --force-recreate
```

## Commands

| Action | Telegram message |
| --- | --- |
| Import with a default name | Send a static, animated, or video sticker |
| Import with a chosen name | Reply to the sticker with `/add Happy fox` |
| List Matrix packs and capacity | `/packs` |
| Create and select a Matrix pack | `/newpack Fox collection` |
| Select a pack | `/usepack ID` |
| Rename a pack | `/renamepack ID New name` |
| Import a whole Telegram pack | `/importpack PackName` or `/importpack https://t.me/addstickers/PackName` |
| Import a sticker's whole pack | Reply to it with `/importpack` |
| Stop a bulk import | `/cancelimport` |
| Browse the current Matrix library | `/stickers` or `/stickers 2` |
| Preview an uploaded sticker | `/view ID` |
| Rename a sticker | `/rename ID Sleepy fox` |
| Change its `:shortcode:` | `/setid ID happy_fox` |
| Change the trailing label throughout the room | `/suffix Fox den` |
| Hide the trailing label | `/suffix off` |
| Restore the room name as trailing label | `/suffix default` |
| Remove a sticker from your packs | `/remove ID` |
| Show help | `/help` |

`/stickers` shows the IDs. You can also reply to an original Telegram sticker
with `/view`, `/rename Sleepy fox`, `/setid happy_fox`, or `/remove`. Base names
support spaces and emoji, up to 80 characters. Labels initially use the Telegram
emoji, base name, and room name: **🦊 Happy fox · pawz.hu stickers**.
`/setid` changes `:tg_…:` into a shortcode you choose while retaining Telegram
duplicate detection. `/suffix` changes or hides the trailing label across every
importer pack in this room, including future imports; it does not rename the room.

See [the command guide](docs/commands.md) for examples, preview behavior, and scope.

Whole-pack imports ask whether to create a pack named after the Telegram pack,
use the selected Matrix pack, or cancel. Nothing is imported until you choose.
Pack selection is saved per room. Sticker commands operate on that selection.

## Compatibility and data

- Static stickers become transparent PNG. Animated `.tgs` and video `.webm`
  become looping animated WebP. See [animation support](docs/animations.md).
- Existing entries and unrelated pack metadata are preserved. No migration is
  required for stickers imported by earlier versions of this bot.
- In room mode, listing, imports, renames and removals use the selected importer
  pack in the configured room. The bot's personal sticker pack is left untouched.
  Its selected-pack preference is stored in its own Matrix account data.
  Legacy personal packs are available only with `MATRIX_PACK_MODE=personal`.
- Removal does not purge
  Matrix media or alter stickers already sent in messages.
- Media uploads are unencrypted. A private pack room does not make uploaded
  sticker media private or end-to-end encrypted.
- Whole-pack imports report progress, preserve custom names on repeat runs,
  skip existing media downloads, and can be cancelled after the current sticker.
  Completed imports survive cancellation and process restarts. Re-run to resume.
- Packs are never split automatically. A full pack triggers a warning and
  stops the import before uploading the next image. Create/select another
  pack or remove entries yourself, then retry. `/packs` shows approximate
  capacity usage. Enable newly created packs in Sable to use them globally.
- Run only one bot instance and avoid editing the same pack in another client
  at the same time. Multi-sticker imports are not one atomic transaction.
- Refresh Sable or reopen its sticker picker if changes do not appear immediately.

## Development and CI

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m unittest discover -s tests -v
```

GitHub Actions runs lint, formatting, and offline tests on Python 3.12 and 3.13
for PRs targeting `main` and pushes to `main`. It then builds and smoke-tests
the container as a non-root user. Successful pushes to `main` publish:

```text
ghcr.io/kaoplo/telegram-sticker-exporter-to-matrix:latest
ghcr.io/kaoplo/telegram-sticker-exporter-to-matrix:sha-<full-commit-sha>
```

The image name is derived from the repository automatically. GHCR publishing
uses `GITHUB_TOKEN` with `packages: write`; no bot or Matrix credentials are
needed in CI. GitHub Actions and organization package policies must permit
publishing. Manual workflow runs validate without publishing.

To run the published image (stop any existing instance first):

```sh
docker run -d --name sticker-importer --restart unless-stopped --env-file .env ghcr.io/kaoplo/telegram-sticker-exporter-to-matrix:latest
```

Private GHCR packages require registry authentication before pulling.

## More documentation

- [Command guide](docs/commands.md)
- [Configuration and troubleshooting](docs/configuration.md)
- [Application architecture](docs/architecture.md)
- [Animation conversion](docs/animations.md)
- [Deployment guide](docs/deployment.md)
- [Development and contribution guide](CONTRIBUTING.md)

Upgrading from the single-file application? Use `uv run matrix-stickers` instead
of `python bot.py`. Docker Compose commands and environment variable names stay
the same. `requirements.txt` is replaced by `pyproject.toml` and `uv.lock`.
Legacy `backups/` files remain ignored; the application does not use them.
