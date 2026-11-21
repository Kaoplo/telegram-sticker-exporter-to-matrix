# Application architecture

```text
src/matrix_stickers/
├── app.py              Composition, command registration, lifecycle
├── config.py           Validated immutable settings
├── handlers.py         Owner checks, commands, bulk imports and cancellation
├── images.py           Static PNG conversion, names and stable IDs
├── conversion.py       Media model and bounded conversion subprocess
├── convert_worker.py   Native TGS/WebM rendering in an isolated process
├── matrix.py           Matrix HTTP client, room-state discovery and media
├── packs.py            Pure pack transformations
├── service.py          Pack management, selection, labels and capacity
└── telegram_packs.py   Telegram pack-name and URL parsing
tests/                  Offline unit and real renderer integration tests
docs/                   Commands, configuration, conversion and architecture
```

## Room libraries

With `MATRIX_PACK_ROOM_ID` set, the configured room is the only source of truth.
The bot reads current room state to obtain its name and all `m.room.image_pack`
events with keys `telegram_imports` and importer keys prefixed `telegram_imports_`.
Legacy numbered packs remain discoverable. Unrelated packs and personal sticker
data are not modified; only the bot's selected-pack preference is stored in
its namespaced account-data event, with a separate selection per room.
Switching the configured room switches the library. Legacy personal-only
mode requires explicit `MATRIX_PACK_MODE=personal`; old personal copies are never deleted
automatically.

New entries are admitted below 46,000 bytes of pack JSON and pack writes are
limited to 48,000 bytes, reserving space below Matrix's 64 KiB event envelope
limit. A full pack raises `PackFullError` before uploading new media. It never
creates or switches packs automatically. User-created packs have random stable
keys and editable names. Selection persists in
`io.github.kaoplo.telegram_stickers.selection` account data. Each user enables
packs globally through their own client; the bot never edits another user's subscriptions.

## Identity and labels

IDs remain `tg_` plus 20 SHA-256 hex characters derived from Telegram's
`file_unique_id`. This keeps older imports addressable and makes re-imports
idempotent within the destination room. An image entry stores provenance in
the namespaced `io.github.kaoplo.telegram_stickers` object:

```json
{
  "name": "Happy fox",
  "emoji": "🦊",
  "room_id": "!exact-room-id",
  "telegram_set": "FoxPack"
}
```

The standard `body` field is rendered as `🦊 Happy fox · pawz.hu stickers`.
Rename only changes the base name, preserving the emoji and destination.
Listing reflects the latest room name; importing or renaming persists it.
The ID and media URI stay unchanged on display-name rename. `/setid` moves an
entry to a new key in the selected pack and retains `telegram_id` in source
metadata. Import matching uses that identity separately from editable shortcodes;
replies to original Telegram stickers use explicit identity references so a reused
old shortcode cannot target another sticker by accident. Collisions are rejected.
Old entries gain provenance when re-imported from Telegram; media is reused.

The trailing label defaults to the actual room name. A room-state override at
`io.github.kaoplo.telegram_stickers.settings` can replace or hide it. `/suffix`
preflights the size of every affected pack, saves the override, then rewrites
stored `body` labels while preserving metadata and media. A partial failure is
reported with a retry instruction; Matrix has no transaction across these events.

## Concurrency and bulk imports

Telegram processing permits four concurrent updates so `/cancelimport` and
library commands remain available during a bulk import. A handler-owned lock
allows one bulk import at a time. Another lock limits media conversions to one
at a time, while the service's lock serializes remote read/modify/write cycles.

A bulk import fetches the sticker set and waits for an owner-only inline choice
between a new dedicated pack, the selected pack shown in the prompt, and cancel.
Prompts are single-use and expire after ten minutes. A new prompt supersedes
the previous prompt; restart drops pending prompts. No pack is created or media
imported before confirmation. The destination is captured at confirmation and
passed explicitly to each entry operation, so selection changes do not redirect a batch.
Confirmed imports process stickers in order,
and reports added, existing, failed and unprocessed counts. Existing entries
are refreshed without downloading or converting their media. Cancellation
takes effect before the next sticker and leaves completed entries in Matrix.
Conversion failures are counted; capacity and API/authentication/network failures
stop the batch early. Repeating the command and choosing the same destination
resumes using stored IDs. Cross-pack copies reuse existing MXC media while
renames/removals affect only the selected pack.

An entire pack is not a transaction. A failed state write after a successful
media upload can leave an orphan upload; a retry may upload that one sticker
again. The service validates proposed pack JSON and size before uploading.
Room mode has no personal-pack mirroring or global-enablement write to fail.
The lock does not protect against another process or simultaneous client edits.

## Rendering and errors

Static inputs use Pillow. Animations use a disposable Python subprocess to
contain renderer crashes and enforce a 60-second deadline. The worker renders
TGS with rlottie and Pillow or WebM with FFmpeg, returning animated WebP.
See [conversion limits](animations.md). Temporary files are removed; no local
catalog or media cache is kept.

Every command checks both the private chat and numeric owner ID. Telegram
replies use short action messages; HTTP failures show a safe explanation.
Operator logs retain method, path and Matrix error with credentials redacted.
Query strings and raw HTTP exceptions are not sent to Telegram. Startup verifies
the configured account and room access. Shutdown marks handlers as stopping,
cancels pending imports, stops a batch after its current sticker, then drains
in-flight updates and closes the client.

## Build and verification

`uv.lock` pins Python dependencies. The Docker image installs FFmpeg and the
native rlottie wheel, builds the package non-editably, and runs as an
unprivileged user. CI installs FFmpeg, runs lint/format checks and tests on
Python 3.12 and 3.13, builds and smoke-tests the image, then publishes to GHCR
only on successful pushes to `main`.
