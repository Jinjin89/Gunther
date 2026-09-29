import { FolderInput } from "lucide-react";
import { useRef, useState } from "react";
import { knowledgeApi } from "../api";
import { importable, importFiles, type ImportProgress } from "../import/bulkImport";

const count = (value: number) => value.toLocaleString();
// Browsers pick folders through this non-standard attribute.
const FOLDER_PICKER = { webkitdirectory: "", directory: "" } as Record<string, string>;
// Refresh the source list this often while a large import runs.
const REFRESH_EVERY = 10;

/** Import a whole folder (or many files) of papers into one library. */
export function BulkImport({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const folderInput = useRef<HTMLInputElement>(null);
  const filesInput = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [skipped, setSkipped] = useState(0);
  const [running, setRunning] = useState(false);

  const start = async (list: FileList | null) => {
    if (!list?.length || running) return;
    const { files, skipped: left } = importable(Array.from(list));
    setSkipped(left);
    setProgress({ total: files.length, done: 0, added: 0, duplicates: 0, failed: [], cancelled: false });
    if (!files.length) return;
    controller.current = new AbortController();
    setRunning(true);
    const announce = () => window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
    try {
      const done = await importFiles(files, {
        knowledgeBaseId,
        upload: knowledgeApi.captureAsset,
        signal: controller.current.signal,
        onProgress: (next) => {
          setProgress(next);
          if (next.done % REFRESH_EVERY === 0) announce();
        },
      });
      setProgress(done);
    } finally {
      setRunning(false);
      controller.current = null;
      announce();
    }
  };

  const summary = progress && [
    `${count(progress.added)} added`,
    progress.duplicates ? `${count(progress.duplicates)} already here` : "",
    progress.failed.length ? `${count(progress.failed.length)} failed` : "",
    skipped ? `${count(skipped)} skipped (hidden or unreadable)` : "",
  ].filter(Boolean).join(" · ");

  return <div className="bulk-import">
    <input ref={folderInput} type="file" hidden multiple {...FOLDER_PICKER} aria-label="Folder to import" onChange={(event) => { void start(event.target.files); event.target.value = ""; }} />
    <input ref={filesInput} type="file" hidden multiple aria-label="Files to import" onChange={(event) => { void start(event.target.files); event.target.value = ""; }} />
    <div className="bulk-import-actions">
      <span><strong>Many papers at once</strong><small>Pick a folder; Gunther keeps each file, then reads and indexes them in the background.</small></span>
      <button type="button" disabled={running} onClick={() => folderInput.current?.click()}><FolderInput size={13} />Import folder</button>
      <button type="button" disabled={running} onClick={() => filesInput.current?.click()}>Import files</button>
    </div>
    {progress && <div className="bulk-import-status" role="status">
      {running
        ? <><progress max={progress.total} value={progress.done} /><span>Importing {count(progress.done)} of {count(progress.total)}{summary ? ` · ${summary}` : ""}</span><button type="button" onClick={() => controller.current?.abort()}>Stop</button></>
        : <span>{progress.total === 0 ? `Nothing to import${skipped ? `: ${count(skipped)} hidden or unreadable files` : ""}.` : `${progress.cancelled ? "Stopped" : "Imported"} · ${summary}. Gunther reads and indexes new papers in the background.`}</span>}
      {!running && progress.failed.length > 0 && <details><summary>Show failures</summary><ul>{progress.failed.map((item) => <li key={item.name}><code>{item.name}</code> — {item.reason}</li>)}</ul></details>}
    </div>}
  </div>;
}
