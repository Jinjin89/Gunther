import type { StorageStatus } from "@gunther/contracts";
import { CircleAlert, Copy, FolderOpen, HardDrive } from "lucide-react";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import { isMac } from "../shortcuts/shortcuts";

interface LibraryFolderSettingsProps {
  onNotify: (message: string) => void;
  onCopy: (label: string, value: string) => void;
}

const updatedAt = (value: string) => new Date(value).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

/** Where libraries live on disk, and a way to open that folder. */
export function LibraryFolderSettings({ onNotify, onCopy }: LibraryFolderSettingsProps) {
  const [storage, setStorage] = useState<StorageStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let active = true;
    void knowledgeApi.storage().then((status) => {
      if (active) setStorage(status);
    }).catch(() => {
      if (active) setUnavailable(true);
    });
    return () => { active = false; };
  }, []);

  const reveal = async () => {
    try {
      await knowledgeApi.revealLibraryFolder();
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "The library folder could not be opened.");
    }
  };

  return <section className="library-folder-settings" aria-labelledby="library-folder-heading">
    <div className="setting-heading"><HardDrive size={16} /><span><strong id="library-folder-heading">Library folder</strong><small>Where your libraries live on disk</small></span></div>

    {!storage && !unavailable && <div className="setting-row"><span><strong>Checking…</strong><small>Asking the local service where your libraries are kept.</small></span></div>}
    {unavailable && <div className="setting-row"><span><strong>Unavailable</strong><small>Reconnect the local service to see where your libraries are kept.</small></span></div>}

    {storage?.foldersEnabled && storage.libraryRoot && <>
      <div className="setting-row">
        <span><strong>Location</strong><code className="library-folder-path">{storage.libraryRoot}</code></span>
        <span className="setting-actions">
          <button type="button" className="gx-icon-button" aria-label="Copy library folder path" title="Copy path" onClick={() => onCopy("Library folder path", storage.libraryRoot ?? "")}><Copy size={14} /></button>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => void reveal()}><FolderOpen size={13} />{isMac() ? "Show in Finder" : "Open folder"}</button>
        </span>
      </div>
      <div className="setting-row">
        <span><strong>Readable folders</strong><small>{storage.lastError ? `The last update did not finish: ${storage.lastError}` : "Each library is a folder of ordinary files: the originals, their text as Markdown, and what Gunther knows about each source. Gunther keeps it up to date; edits made there are not read back."}</small></span>
        <span className={`setting-state ${storage.lastError ? "is-muted" : ""}`}><i />{storage.lastError ? "Needs attention" : storage.lastSyncedAt ? `Updated ${updatedAt(storage.lastSyncedAt)}` : "Updating…"}</span>
      </div>
      <p className="library-folder-note">To move it, quit Gunther, move the folder, then set <code>LIBRARY_ROOT</code> in the backend configuration to its new place.</p>
    </>}

    {storage && !storage.foldersEnabled && storage.problem && <div className="setting-row is-problem">
      <span><strong><CircleAlert size={13} />Not in use</strong><small>{storage.problem} Until then, originals stay in Gunther’s data folder.</small></span>
    </div>}
    {storage && !storage.foldersEnabled && !storage.problem && <div className="setting-row">
      <span><strong>Not set</strong><small>Originals stay in Gunther’s data folder. Set <code>LIBRARY_ROOT</code> in the backend configuration, for example <code>~/Gunther</code>, and restart to get one readable folder per library.</small></span>
    </div>}
  </section>;
}
