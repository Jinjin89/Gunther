# Library folders

Every library is also an ordinary folder on disk, so it can be browsed, copied
and backed up with any tool. This document is the contract for that folder,
format 1.

## Where it lives

The backend reads the folder from `LIBRARY_ROOT` in its environment and nowhere
else. Who sets it depends on how Gunther runs:

| How Gunther runs | Default | Chosen in |
|---|---|---|
| Desktop app | `~/Gunther` | **Settings → Library & storage → Change…** The app remembers the choice (`library-location.json` in its data folder) and starts its bundled backend with `LIBRARY_ROOT` set. |
| Development (`npm run dev`) | off | the repository's `.env`; restart the backend after changing it |

Change… moves the folder while the app's backend is stopped: a rename on one
disk, otherwise a full copy before the original is removed. A folder that
already has files in it gets a `Gunther` folder inside it, so nothing of yours
is mixed in, and moving is refused while a recording is running.

`~/Gunther` sits outside Documents on purpose: macOS asks no permission for it,
and iCloud does not sync recordings while they are still growing.

The database never moves here. It stays in the application's private data
folder, because SQLite must not live in a folder that iCloud or Dropbox syncs.
Keys, tokens and logs stay there too.

## Layout

```text
<LIBRARY_ROOT>/
├── README.md                     what this folder is
├── Inbox/
│   ├── Sources/<date> <title>/   sources waiting for a library
│   └── Notes/<title>.md          quick notes waiting in Inbox
├── Libraries/<title>/
│   ├── library.json              the library and where each source is
│   ├── Overview.md               the library at a glance: topics, then papers by year
│   ├── Topics/<topic>.md         a topic's overview, or its list of papers
│   └── Sources/<date> <title>/
│       ├── source.json           what it is, where it came from, your decisions
│       ├── summary.md            its summary; for a paper also title, authors, abstract…
│       ├── content.md            its text: note, transcript or extracted text
│       ├── original.<ext>        the captured file, when there is one
│       └── recording.<ext>       the audio, for a recording
├── Trash/                        the same shapes, for what rests in Trash
└── .gunther/                     Gunther's own store; leave it alone
    ├── root.json                 which workspace owns this root
    ├── folders.json              what Gunther wrote, so it removes only that
    ├── assets/                   originals, stored once by fingerprint
    └── recordings/               recording audio
```

- **One home per source.** A source filed in several libraries lives in the
  first; each other library holds a relative symlink to it (an alias in Finder).
- **No extra space.** `original.*` and `recording.*` are hard links to the single
  copy under `.gunther/`, which is read-only so an editor cannot change it in
  place. On a volume without hard links (exFAT) they are copies.
- **Names.** `<date> <title>` with characters that are unsafe on any system
  replaced, at most 80 characters, and ` 2`, ` 3`… added when two would clash.
- **Trash.** A trashed library appears as `Trash/<title>/` with the sources that
  went with it; a source or note trashed on its own under `Trash/Sources/` or
  `Trash/Notes/`.

### `source.json` (`gunther.source/1`)

`id`, `title`, `kind`, `capturedAt`, `fingerprint`; `libraries` (home first);
`original` (`file`, `name`, `mediaType`, `sizeBytes`, `sha256`) or null;
`recording` (`id`, `file`, `durationSeconds`) or null; `web` (`url`, `finalUrl`,
`capturedAt`) or null; `claims` with each claim's `subject`, `predicate`,
`object` and your `status`; `trashedAt` or null.

### `library.json` (`gunther.library/1`)

`id`, `title`, `question`, `description`, `color`, `createdAt`, `trashedAt`, and
`sources`: each with `id`, `title`, its `folder` relative to the library, and
`alias: true` when the folder is an alias to its home elsewhere.

### `summary.md`, `Overview.md` and `Topics/`

Markdown for people, not a data format. `summary.md` is the capture's summary
(see [Capture: AI summaries](CAPTURE_AI_SUMMARIES.md)): study notes for a
lecture, actions for a meeting, what a photo shows, and so on, with the passages
each key point cites. For a paper or document it follows what the source says
about itself, read without a model (see the backend doc, "Papers at scale").
`Overview.md` links each topic and each source's `summary.md`, newest first.
A topic's file is its written overview when there is one, else the list of its
papers. Links are relative, so the folder can be moved or opened in any
Markdown editor.

### Notes (`gunther.note/1`)

Markdown with front matter: `format`, `id`, `title`, `created`, `updated`.

## Guarantees

- Gunther rewrites these folders as things change, shortly after each change
  and every ten minutes.
- It removes only files it wrote that nobody has changed since, so anything
  added by hand stays. A folder is removed only once it is empty.
- Edits made here are **not read back** in format 1. Change things in Gunther.
- A root belongs to one workspace (`.gunther/root.json`). Pointed at a root that
  another workspace owns, Gunther refuses it, leaves every original where it
  was, and says so in Settings.

## Moving originals into the root

When a root is first configured, originals kept in the data folder
(`assets/`, `recordings/`) move into `.gunther/` at startup, before anything
opens them, one file at a time: a rename on the same volume, otherwise a copy
verified byte for byte before the old file goes. A file already in the root with
different content is left where it was. Interrupted uploads (`.incoming/`) stay
behind. On the same disk this is instant; a root on another disk means copying
everything once, so that first start takes longer. In the desktop app the folder
is moved later with Settings → Library & storage → Change…; in development,
stop the backend, move the folder, set the new `LIBRARY_ROOT` and start it again.

## Backups

Pass the root to the backup tool so it copies the originals from `.gunther/`:

```sh
npm run backup:data -- --data-dir <data folder> --library-root <LIBRARY_ROOT>
```

`LIBRARY_ROOT` in the environment is used when the flag is left out. A backup
that is missing any original is never published.

## Later formats

Topic pages, per-source summaries, a library overview and outputs as Markdown
(`Topics/`, `summary.md`, `Overview.md`, `Outputs/`) are planned for the
combining work and will arrive as additive changes to this layout.
