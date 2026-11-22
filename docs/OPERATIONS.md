# Operations

## Data and logs

Find the actual workspace before backing up or diagnosing it. A development backend defaults to `data/gunther.sqlite`, `data/assets/` and `data/recordings/`. Check `DATABASE_URL`, `ASSETS_DIR`, `RECORDINGS_DIR` and `LIBRARY_ROOT` for overrides. The installed desktop defaults to `~/Gunther/.gunther/` for the database, originals, pending captures and snapshots. On macOS, private settings default to `~/.config/gunther/`. Back up provider credentials and any private mobile gateway keys separately using protected storage. Readable folders and workbook exports serve exchange and reading. [Library folders](LIBRARY.md#library-folders)

Open **Settings → Developer → Logs → Log files → Show** or use **Show log files** on the startup error screen. Packaged logs live in `.gunther/logs/`, with a system log folder fallback. Daily files are named `YYYY-MM-DD.app.log` and `YYYY-MM-DD.backend.log`. They rotate at 20 MiB and the app prunes them using a 14-day and 500 MiB limit. Logs mask credentials and can still contain filenames and operation details. Look for `FATAL` in the backend log when the Helper exits during startup. Development also logs to the terminals. [Native log writer](../apps/desktop/src-tauri/src/app_log.rs) · [Backend logging](../apps/backend/gunther/app_log.py)

## Backup and restore

1. Finish or pause active capture and processing. Quit the app and stop any development backend writing the workspace.
2. Make the backup in a private directory outside the workspace. For an installed default workspace, pass both the database directory and the library root.
3. Verify the completed backup. Restore it to an absent directory.
4. Open the restored copy with an isolated backend. Check workspace bootstrap, libraries, Inbox, downloads, audio playback and saved output versions. Verify a new backup of that copy before switching over. Keep the original workspace during the switch.

```sh
npm run backup:data -- --data-dir "$HOME/Gunther/.gunther" \
  --library-root "$HOME/Gunther" --output-dir /path/to/private-backups
npm run verify:backup -- /path/to/private-backups/gunther-backup-ID
npm run restore:data -- /path/to/private-backups/gunther-backup-ID \
  --data-dir /path/to/new-workspace
```

For a default checkout without a library root, `npm run backup:data -- --output-dir /path/to/private-backups` uses `data/`. The tool makes a consistent SQLite snapshot, copies and hashes managed originals, verifies them and publishes the backup atomically. Verification checks the manifest, file sizes and hashes, SQLite and migration integrity, recording ledgers and saved version seals. Restore rechecks the copy before publishing it and requires a new destination. [Backup tool](../scripts/backup_gunther.py)

The standard backup holds application data. Keep `.env`, private provider settings and mobile gateway identity in their own protected backup when needed. A launch token is issued fresh. Recovery without the gateway's private identity produces a new certificate.

## Recovery

When an existing workspace database is missing, startup checks `.gunther/backups/` newest first and restores the newest valid snapshot that matches the workspace. These snapshots hold the database and depend on the originals still being present. Use a full backup to recover a lost workspace or disk. If startup cannot recover a matching database, it reports a recovery error and leaves the files in place. [Workspace recovery](../apps/backend/gunther/workspace_storage.py)

| Symptom | Check and action |
| --- | --- |
| Workspace appears empty | Check the actual database and library root. Keep that directory intact until you find the matching database or a verified snapshot. |
| Database is locked | Stop duplicate service processes and check the Helper's instance lock and log. Keep WAL and SHM with the live database. Run SQLite integrity checks on the backup or an isolated copy. |
| Original or saved artifact will not open | Check the matching Asset, file or seal in a verified backup. Preserve the stored record and the original bytes. |
| Reading or OCR failed | Check `/api/health` and the reading's provider and warning. The original is available. Use Read again after fixing the reader or setting. |
| A write fails on size or space | `413` means the request exceeds its size limit. `507` means the storage budget or free-space guard blocked the write. Assets and recordings share a default 20 GiB budget and 1 GiB free-space reserve. Restore space before retrying. |
| Recording upload is interrupted | Reopen the draft and retry from the acknowledged offset. The backend checks the ledger and actual files on metadata reads. Preserve `.part` files and spooled audio for recovery. A partial import may require selecting its file again. |
| Phone capture is pending | Check the workspace, paired profile and outbox state. Retry the upload. In maintenance, keep staged originals until you know they are safely saved. |
| Audio is saved and live text is missing | Check the provider and live socket. With a default local SenseVoice service, probe `http://127.0.0.1:8765/health`. Use Transcribe again with the saved recording. |
| The packaged Helper cannot start | Check the private data path, lock and log. It prefers loopback port 28787 and can select a free port. Use the current launch credentials. |
| A phone's connection changes | Verify the certificate and workspace shown by pairing. Revoke stale device credentials and pair again after a gateway identity change. A physical phone connects to the HTTPS gateway. |

## Release

`npm run build:desktop` packages for the host platform. Outputs are under `apps/desktop/src-tauri/target/release/bundle/`. The release workflow checks that the tag matches the Tauri version, builds the pinned parser runtime, then attaches macOS DMG, Windows NSIS/MSI and Linux AppImage/deb installers. The release stays draft until all builds finish. [Release workflow](../.github/workflows/release.yml)

For a macOS distribution build, configure a Developer ID certificate and the Apple notarization credentials used by the workflow. The workflow supports ad-hoc builds when signing secrets are absent. Verify the installer and packaged Helper on the target platform. The macOS package declares microphone access. Permission and long recording behavior require a device run, as do the phone's share and pairing paths. [Packaging checks](DEVELOPER.md#packaging)
