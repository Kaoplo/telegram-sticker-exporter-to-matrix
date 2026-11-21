# Sticker commands

All commands work only in a private chat with the bot and only for the numeric
`TELEGRAM_USER_ID` configured by the owner. Commands also appear in Telegram's
command menu. `/start` and `/help` show the guide; `/list` aliases `/stickers`.

## Import and name

Send a static, TGS animated, or WebM video Telegram sticker. The bot imports it
and returns its stable `tg_…` ID. Labels use **emoji + base name + room name**,
for example `🦊 Telegram sticker · pawz.hu stickers`.

To choose a name, reply to the original sticker with:

```text
/add Happy fox
```

If it was already imported, `/add` changes its name and synchronizes its pack
entries without uploading the image again. Names are trimmed, repeated spaces
are collapsed, and the result must contain 1–80 characters. Spaces, Unicode,
and emoji are supported; quotes are unnecessary.

Sending an already imported sticker again without a name preserves its label.
The emoji comes from Telegram; it need not be included in the typed name.
If Telegram supplies no emoji, the prefix is omitted. Existing entries without
emoji metadata gain it when their Telegram sticker or pack is imported again.

## Change sticker IDs and the trailing room label

```text
/setid tg_0123456789abcdef0123 happy_fox
/setid :happy_fox: sleepy_fox
```

Or reply to the original Telegram sticker with `/setid happy_fox`. The new
shortcode appears as `:happy_fox:` in clients. It may contain 1–100 ASCII
letters, digits, underscores, or hyphens; colons around command arguments are
optional. IDs are case-sensitive. An ID already used in the selected pack is
rejected without overwriting anything. Other packs are unaffected.

The original Telegram identifier remains in entry metadata, so re-importing
or replying to the original Telegram sticker continues to find the renamed
entry. Media is not uploaded again. Edits and removals can use the new ID.

```text
/suffix
/suffix Fox den
/suffix off
/suffix default
```

`/suffix` shows the current trailing label. A custom value changes
`🦊 Happy fox · pawz.hu stickers` to `🦊 Happy fox · Fox den`. `off` hides the
suffix entirely; `default` restores the actual Matrix room name. Custom suffixes
can contain up to 80 characters. `off` and `default` are reserved command values.

Suffix changes apply to all importer packs in the configured room and future
imports, and survive restarts. They change sticker labels, not the room's name,
pack names, shortcode IDs, media, or destination room. Room identity remains in
metadata even with a hidden suffix. Every pack is checked for size before the
change is saved. Matrix cannot update multiple state events atomically; if a
write fails part-way, retry the same `/suffix` command to finish.

## Import a whole pack

```text
/importpack FoxPack
/importpack https://t.me/addstickers/FoxPack
```

Or reply to any sticker with `/importpack`. The bot fetches the sticker set
through Telegram's Bot API and asks you to choose:

- **Create its own Matrix pack:** creates and selects a pack named after the
  Telegram pack, then imports into it.
- **Use the selected pack:** imports into the pack shown in the prompt.
- **Cancel:** creates no packs and imports nothing.

Prompts expire after ten minutes; a newer prompt replaces the older one.
Only the configured owner can confirm, and a confirmed choice cannot be replayed.
Personal-only mode offers the existing pack and Cancel, since room packs need a room.
Once confirmed, the bot imports stickers sequentially and reports
progress every ten stickers or after fifteen seconds between completed stickers,
plus a final added/existing/failed/unprocessed count.
New entries get names such as `🦊 Fox Pack 1 · pawz.hu stickers`. Existing
custom names are preserved, and duplicates skip media download/conversion.

One pack import can run at a time. Other commands remain available. Use
`/cancelimport` to stop after the current sticker. Completed entries remain
in Matrix; repeating the same command resumes without duplicate uploads.
Conversion failures are counted and skipped. API/authentication/network failures
stop the batch early, with the first error included in the summary. Capacity
failures stop immediately and leave the remaining stickers unprocessed.

No additional pack is created when one fills up. Remove entries or use
`/newpack` and `/usepack` to choose another destination. To resume into the
same pack, select it, repeat `/importpack`, and choose **Use the selected pack**.
Choosing **Create its own Matrix pack** again intentionally creates another pack.

## Manage Matrix packs

```text
/packs
/packs 2
/newpack Fox collection
/usepack telegram_imports_0123456789ab
/renamepack telegram_imports_0123456789ab Sleepy foxes
```

`/packs` lists names, IDs, sticker counts, approximate capacity usage, and the
selected pack. IDs are generated independently of names, so renaming preserves
subscriptions. `/newpack` creates an empty room pack and selects it. `/usepack`
selects an existing pack; `/renamepack` updates its display name without
changing its stickers. Legacy numbered packs remain available in `/packs`.

The selection is stored in the bot's Matrix account data separately for each
room and survives restarts. Single-sticker imports and `/stickers`, `/view`,
`/rename`, and `/remove` operate on this selection. A running bulk import keeps
the destination confirmed in its prompt even if `/usepack` changes meanwhile.
The same sticker can exist in multiple packs; edits affect only the selected copy.
When adding it to another pack, media already present in this room is reused.

Capacity is measured in serialized pack JSON bytes rather than a fixed sticker
count. New entries stop at a conservative 46,000-byte content budget; 48,000
bytes are allowed for editing existing entries. This reserves space below
Matrix's total event-size limit. A new media upload is not made after a local
capacity check fails. The pack is not split or silently switched.

Enable each newly created pack in Sable to use it globally. The bot does not
change your personal account's subscriptions. Existing packs can also be
managed in Sable; this bot's commands cover creating, selecting, and renaming.

## Browse and view

```text
/stickers
/stickers 2
```

Each page contains up to eight entries, sorted by name, with an ID for each
sticker. The data is fetched from Matrix on each command, rather than kept in
a separate local catalog. Existing stickers imported by older bot versions
and entries added by other clients are included.

To preview an entry, copy its complete ID from the list:

```text
/view tg_0123456789abcdef0123
```

Or reply to the original Telegram sticker with `/view`. The bot downloads the
actual Matrix media through the authenticated homeserver API and sends it as
an image document, preserving the original bytes and transparency. Downloads
are capped at 10 MiB. Deleted, inaccessible, or unsupported media can fail to
preview even if the pack entry still exists.

With `MATRIX_PACK_ROOM_ID` configured, `/packs` discovers `telegram_imports`
and other importer keys prefixed `telegram_imports_` in that room. `/stickers`
shows the selected pack only. Other rooms, unrelated packs, personal packs,
and loose homeserver media are not included. With explicit `MATRIX_PACK_MODE=personal`, the library uses
the account's legacy `im.ponies.user_emotes` personal pack.

## Rename

```text
/rename tg_0123456789abcdef0123 Sleepy fox
```

Or reply to the original Telegram sticker with:

```text
/rename Sleepy fox
```

This changes the base name in the entry's Matrix `body` label. It
preserves the shortcode, media URI, image information, and unrelated metadata.
The Telegram emoji and configured suffix remain attached. This command leaves
the ID unchanged; use `/setid` to edit it separately. How the label
is displayed depends on the Matrix client.

For ID-based commands, send a new message rather than replying to another
sticker. A reply always selects the replied-to Telegram sticker.

## Remove

```text
/remove tg_0123456789abcdef0123
```

Or reply to the original Telegram sticker with `/remove`. Removal takes effect
immediately in the selected pack (or the personal pack when
personal mode is explicitly configured). Repeating it is safe.
An already absent entry produces an informational reply.

Removal deletes the pack references, not the uploaded media. Matrix messages
and other packs may still reference the image. The bot never calls a media
purge endpoint. Sending the Telegram sticker again imports it again.

## Failures and retries

Imports are not a transaction spanning an entire Telegram pack. Correct any
permissions or network failure and retry the pack. Entries already saved in
Matrix are reused. If media upload succeeds but saving its pack entry fails,
the unreferenced upload cannot be recovered automatically and a retry may
upload that sticker again. Reload the Sable picker to refresh client caches.
