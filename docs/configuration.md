# Configuration and troubleshooting

Copy `.env.example` to `.env` in the repository root. `uv run matrix-stickers`
loads it automatically without overriding existing environment variables.
Docker Compose supplies it through `env_file`; a published container can use
`docker run --env-file .env …`.

| Variable | Required | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | BotFather token for your Telegram bot |
| `TELEGRAM_USER_ID` | Yes | Positive numeric ID of the only authorized Telegram user |
| `MATRIX_HOMESERVER` | Yes | HTTPS Matrix client-server API base URL |
| `MATRIX_USER_ID` | Yes | Full Matrix bot account ID, such as `@stickers:example.org` |
| `MATRIX_ACCESS_TOKEN` | Yes | Raw access token belonging to that account; omit the `Bearer ` prefix |
| `MATRIX_PACK_MODE` | No | `room` by default; `personal` explicitly enables legacy personal packs |
| `MATRIX_PACK_ROOM_ID` | In room mode | Exact internal room ID copied from the client; preserve it unchanged |
| `ALLOW_HTTP` | No | Set to `1` only for trusted local HTTP testing |

No backup directory, database, or persistent filesystem volume is required.
Keep credentials outside Git. HTTP request logging is suppressed because
Telegram request URLs contain the bot token.

## Matrix bot token

For ESS/MAS, create a dedicated bot user and issue a personal access token
through Element Admin → Personal tokens. Select the bot as acting user and
enable Matrix Client-Server API access. It does not need MAS/Synapse admin
scopes. Give it an appropriate lifetime and replace it before expiry.
See [MAS personal sessions](https://element-hq.github.io/matrix-authentication-service/topics/authorization.html#personal-sessions-personal-access-tokens).

For a quick test, a client token can also be copied from Sable:

Open **Settings → Developer Tools**, enable **Enable Developer Tools**, and
click **Copy** beside **Access Token**. Paste the raw value into
`MATRIX_ACCESS_TOKEN`. This menu is present in the Cinny-based Sable client;
other Sable versions may organize settings differently.

The token must belong to `MATRIX_USER_ID`. Access tokens can expire or be
revoked, particularly when using OAuth or ending the client session. The bot
uses the configured token as-is and does not refresh OAuth tokens. Use a
dedicated session where practical and replace expired tokens in `.env`.

## Shared room library

Create a private room containing your account and the bot. The bot must accept
its invitation; the importer does not automatically join rooms. Copy the internal room
ID, rather than its `#alias`, into `MATRIX_PACK_ROOM_ID`. Your account needs
permission to set `m.room.image_pack` state. The bot uses state key
`telegram_imports` by default. Additional packs are created only with `/newpack`
or by confirming the own-pack option on a Telegram pack import. Full packs
trigger a warning; they are never split automatically. In Sable, enable each
pack globally from your personal account. Other room state, personal sticker
packs, and subscription settings are left untouched. The bot's active pack
selection is saved in its own account data under
`io.github.kaoplo.telegram_stickers.selection`, scoped by room ID.

Room IDs are identical for every member of the same room. Older rooms use
`!opaque:server`; version 12 rooms use `!hash` without a domain suffix.
Copy the ID exactly as shown. Appending `:your-domain` to a version 12 ID
addresses a different, nonexistent room and can produce a misleading 403
claiming the user is not a member. See [room version 12](https://spec.matrix.org/latest/rooms/v12/).

Room mode is the default and requires `MATRIX_PACK_ROOM_ID`. Missing or invalid
room configuration stops startup; there is no silent personal-pack fallback.
Set `MATRIX_PACK_MODE=personal` for the legacy personal-pack-only mode. Existing
personal copies from earlier versions are not deleted or migrated automatically.

`/suffix` stores a room-wide label override in
`io.github.kaoplo.telegram_stickers.settings` room state. The bot needs permission
to write this event type as well as `m.room.image_pack`. `/suffix off` stores an
empty suffix; `/suffix default` clears the override and uses the actual room name.

## Animation dependencies

The Docker image bundles FFmpeg and the rlottie renderer. Native installations
need FFmpeg on `PATH` for WebM video stickers; TGS rendering is provided by
`rlottie-python` installed with `uv sync`. See [animation support](animations.md).

## Common failures

| Symptom | Action |
| --- | --- |
| Empty/non-JSON response, sometimes HTTP 200 | Use the homeserver API URL from `https://ACCOUNT_DOMAIN/.well-known/matrix/client`, under `m.homeserver.base_url`. For `pawz.hu`, use `https://matrix.pawz.hu`. |
| `401 Unauthorized` / `M_UNKNOWN_TOKEN` | Replace the Matrix token and recreate the container. |
| Token belongs to another user | Set the token and `MATRIX_USER_ID` to the same bot account. |
| `403` while updating a room | Check membership and state-event permissions, or use personal-only mode. |
| Matrix denied access | Check bot membership and token permissions. Detailed request paths and Matrix error codes are available in container logs, not Telegram replies. |
| Pack import stops part-way | Fix the reported API/network failure and repeat `/importpack`; saved entries are reused. |
| Pack is full | Remove entries or create/select a pack with `/newpack`, `/packs`, and `/usepack`. Retry the Telegram import and choose its destination. |
| Preview fails | Media may be missing, inaccessible, larger than 10 MiB, or unsupported by Pillow/Telegram. |
| Changes absent in Sable | Reopen the sticker picker or reload the client. |
| Telegram polling conflict | Stop duplicate bot instances using the same token. |

After editing `.env`:

```sh
docker compose up -d --force-recreate
docker compose logs --tail=30 -f sticker-importer
```

After changing application code, also rebuild:

```sh
docker compose up -d --build
```

## References

- [Sable's access-token settings source](https://github.com/SableClient/Sable-Legacy/blob/dev/src/app/features/settings/developer-tools/DevelopTools.tsx)
- [Matrix client-server API](https://spec.matrix.org/latest/client-server-api/)
- [Image pack proposal and compatibility details](https://github.com/matrix-org/matrix-spec-proposals/blob/main/proposals/2545-emotes.md)
