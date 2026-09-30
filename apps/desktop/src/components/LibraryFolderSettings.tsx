import type { StorageStatus } from "@gunther/contracts";
import { CircleAlert, Copy, FolderInput, FolderOpen, HardDrive } from "lucide-react";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import { type LibraryLocation, libraryLocation, moveLibraries, pickLibraryDestination } from "../libraryLocation";
import { isMac } from "../shortcuts/shortcuts";

interface LibraryFolderSettingsProps {
  onNotify: (message: string) => void;
  onCopy: (label: string, value: string) => void;
}

const updatedAt = (value: string) => new Date(value).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : typeof reason === "string" ? reason : fallback;

/** Where libraries live on disk: open it, or move it somewhere else (the desktop app owns the choice). */
export function LibraryFolderSettings({ onNotify, onCopy }: LibraryFolderSettingsProps) {
  const [storage, setStorage] = useState<StorageStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [location, setLocation] = useState<LibraryLocation | null>(null);
  const [moving, setMoving] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void knowledgeApi.storage().then((status) => {
      if (active) setStorage(status);
    }).catch(() => {
      if (active) setUnavailable(true);
    });
    void libraryLocation().then((found) => { if (active) setLocation(found); });
    return () => { active = false; };
  }, []);

  const reveal = async () => {
    try {
      await knowledgeApi.revealLibraryFolder();
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "The library folder could not be opened.");
    }
  };

  const change = async () => {
    const from = storage?.libraryRoot;
    if (!from) return;
    let to: string | null;
    try {
      to = await pickLibraryDestination(from);
    } catch (reason) {
      onNotify(message(reason, "That folder cannot hold your libraries."));
      return;
    }
    if (!to) return;
    if (!window.confirm(`Move your libraries to ${to}?\n\nGunther pauses its local service while the folder moves, then carries on from there.`)) return;
    setMoving(to);
    try {
      const moved = await moveLibraries(from, to);
      setStorage(await knowledgeApi.storage());
      setLocation(await libraryLocation());
      onNotify(`Your libraries now live in ${moved}.`);
    } catch (reason) {
      onNotify(message(reason, "Your libraries could not be moved; they are where they were."));
      void knowledgeApi.storage().then(setStorage).catch(() => setUnavailable(true));
    } finally {
      setMoving(null);
    }
  };

  const canChange = Boolean(location?.canChange);
  return <section className="library-folder-settings" aria-labelledby="library-folder-heading">
    <div className="setting-heading"><HardDrive size={16} /><span><strong id="library-folder-heading">Library folder</strong><small>Where your libraries live on disk</small></span></div>

    {!storage && !unavailable && <div className="setting-row"><span><strong>Checking…</strong><small>Asking the local service where your libraries are kept.</small></span></div>}
    {unavailable && !moving && <div className="setting-row"><span><strong>Unavailable</strong><small>Reconnect the local service to see where your libraries are kept.</small></span></div>}

    {storage?.foldersEnabled && storage.libraryRoot && <>
      <div className="setting-row">
        <span><strong>Location</strong><code className="library-folder-path">{moving ? `Moving to ${moving}…` : storage.libraryRoot}</code></span>
        <span className="setting-actions">
          <button type="button" className="gx-icon-button" aria-label="Copy library folder path" title="Copy path" disabled={Boolean(moving)} onClick={() => onCopy("Library folder path", storage.libraryRoot ?? "")}><Copy size={14} /></button>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={Boolean(moving)} onClick={() => void reveal()}><FolderOpen size={13} />{isMac() ? "Show in Finder" : "Open folder"}</button>
          {canChange && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={Boolean(moving)} onClick={() => void change()}><FolderInput size={13} />{moving ? "Moving…" : "Change…"}</button>}
        </span>
      </div>
      <div className="setting-row">
        <span><strong>Readable folders</strong><small>{storage.lastError ? `The last update did not finish: ${storage.lastError}` : "Each library is a folder of ordinary files: the originals, their text as Markdown, and what Gunther knows about each source. Gunther keeps it up to date; edits made there are not read back."}</small></span>
        <span className={`setting-state ${storage.lastError ? "is-muted" : ""}`}><i />{storage.lastError ? "Needs attention" : storage.lastSyncedAt ? `Updated ${updatedAt(storage.lastSyncedAt)}` : "Updating…"}</span>
      </div>
      {!canChange && import.meta.env.DEV && <p className="library-folder-note">In development the backend runs on its own: set <code>LIBRARY_ROOT</code> for it and restart it to move the folder. The installed app moves it from here.</p>}
    </>}

    {storage && !storage.foldersEnabled && storage.problem && <div className="setting-row is-problem">
      <span><strong><CircleAlert size={13} />Not in use</strong><small>{storage.problem} Until then, originals stay in Gunther’s data folder.</small></span>
    </div>}
    {storage && !storage.foldersEnabled && !storage.problem && <div className="setting-row">
      <span><strong>Not set</strong><small>Originals stay in Gunther’s data folder. Set <code>LIBRARY_ROOT</code> for the backend, for example <code>~/Gunther</code>, and restart it to get one readable folder per library.</small></span>
    </div>}
  </section>;
}
