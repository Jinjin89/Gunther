import type { SourceKind, TrashItem } from "@gunther/contracts";
import { AudioLines, Camera, CircleAlert, FileText, Globe, LoaderCircle, NotebookPen, RotateCcw, Table2, Trash2, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { knowledgeApi } from "../api";
import { LibraryGlyph } from "../design/LibraryGlyph";
import { SOURCE_KIND_LABEL } from "../items/itemRef";
import type { IdentityTone } from "../items/ItemLayout";
import { announceTrashChange, daysLeft, restoreFromTrash } from "../trash/trash";

const KIND_IDENTITY: Record<SourceKind, { icon: LucideIcon; tone: IdentityTone }> = {
  note: { icon: NotebookPen, tone: "clay" },
  paper: { icon: FileText, tone: "blue" },
  file: { icon: FileText, tone: "blue" },
  link: { icon: Globe, tone: "amber" },
  image: { icon: Camera, tone: "violet" },
  table: { icon: Table2, tone: "green" },
  recording: { icon: AudioLines, tone: "rose" },
  course: { icon: AudioLines, tone: "rose" },
};

interface TrashPageProps {
  onNotify: (message: string) => void;
  /** Something came back from Trash; the host refreshes what it shows. */
  onRestored?: (entry: TrashItem) => void;
}

const plural = (count: number, singular: string) => `${count} ${singular}${count === 1 ? "" : "s"}`;

const trashedOn = (value: string) => new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });

function describe(entry: TrashItem): string {
  if (entry.kind === "library") return entry.itemCount > 0 ? `Library · with ${plural(entry.itemCount, "source")}` : "Library";
  const label = entry.kind === "note" ? "Quick note" : SOURCE_KIND_LABEL[entry.sourceKind ?? "file"];
  return [label, ...entry.libraryTitles].join(" · ");
}

function EntryIcon({ entry }: { entry: TrashItem }) {
  if (entry.kind === "library") return <LibraryGlyph base={{ title: entry.title, color: entry.color ?? "green" }} size="md" />;
  const { icon: Icon, tone } = KIND_IDENTITY[entry.kind === "note" ? "note" : entry.sourceKind ?? "file"];
  return <span className={`gx-kind-tile tone-${tone}`} aria-hidden="true"><Icon size={15} /></span>;
}

export function TrashPage({ onNotify, onRestored }: TrashPageProps) {
  const [entries, setEntries] = useState<TrashItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [confirmEmpty, setConfirmEmpty] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setEntries(await knowledgeApi.trash());
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Trash could not be opened.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onUpdate = () => void refresh();
    window.addEventListener("gunther:trash-updated", onUpdate);
    return () => window.removeEventListener("gunther:trash-updated", onUpdate);
  }, [refresh]);

  const keyOf = (entry: TrashItem) => `${entry.kind}:${entry.id}`;
  // The service sets how long Trash keeps things; every entry carries it.
  const first = entries[0];
  const retentionDays = first ? Math.round((Date.parse(first.expiresAt) - Date.parse(first.trashedAt)) / 86_400_000) : 30;

  const restore = async (entry: TrashItem) => {
    setWorkingId(keyOf(entry));
    try {
      await restoreFromTrash(entry);
      onRestored?.(entry);
      onNotify(`Restored “${entry.title}”.`);
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "This item could not be restored.");
    } finally {
      setWorkingId(null);
    }
  };

  const deleteForever = async (entry: TrashItem) => {
    setWorkingId(keyOf(entry));
    try {
      await knowledgeApi.deleteForever(entry.kind, entry.id);
      setConfirming(null);
      announceTrashChange();
      onNotify(`Deleted “${entry.title}” for good.`);
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "This item could not be deleted.");
    } finally {
      setWorkingId(null);
    }
  };

  const emptyTrash = async () => {
    setWorkingId("all");
    try {
      const { deleted } = await knowledgeApi.emptyTrash();
      setConfirmEmpty(false);
      announceTrashChange();
      onNotify(deleted === 0 ? "Trash was already empty." : `Deleted ${plural(deleted, "item")} for good.`);
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "Trash could not be emptied.");
    } finally {
      setWorkingId(null);
    }
  };

  return <div className="gx-trash page-enter">
    <header className="gx-page-header">
      <div>
        <h1>Trash</h1>
        <p>Hidden from Inbox, search and Ask. Deleted for good after {plural(retentionDays, "day")}.</p>
      </div>
      {entries.length > 0 && <div className="gx-page-actions">
        <button type="button" className="gx-btn gx-btn-quiet" disabled={confirmEmpty} aria-expanded={confirmEmpty} onClick={() => setConfirmEmpty(true)}><Trash2 size={15} />Empty Trash</button>
      </div>}
    </header>

    {confirmEmpty && entries.length > 0 && <div className="gx-trash-confirm is-bar" role="group" aria-label="Confirm emptying Trash">
      <span>Delete all {plural(entries.length, "item")} for good? This can’t be undone.</span>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setConfirmEmpty(false)}>Cancel</button>
      <button type="button" className="gx-btn gx-btn-danger gx-btn-sm" disabled={workingId === "all"} onClick={() => void emptyTrash()}>{workingId === "all" ? <LoaderCircle className="spin" size={13} /> : <Trash2 size={13} />}Empty Trash</button>
    </div>}

    {error && <div className="gx-banner is-error" role="alert"><CircleAlert size={16} /><span><strong>Trash is temporarily unavailable</strong><small>{error}</small></span><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => { setLoading(true); void refresh(); }}>Retry</button></div>}
    {loading && <div className="gx-trash-list is-loading" aria-label="Opening Trash">{[0, 1, 2].map((index) => <div className="gx-inbox-skeleton" key={index} aria-hidden="true"><i /><span><b /><b /></span></div>)}</div>}

    {!loading && !error && entries.length > 0 && <ul className="gx-trash-list" aria-label="Items in Trash">
      {entries.map((entry, index) => {
        const key = keyOf(entry);
        const working = workingId === key;
        const left = daysLeft(entry);
        return <li key={key} className={`gx-trash-row ${working ? "is-working" : ""}`} style={{ "--i": index } as CSSProperties}>
          <EntryIcon entry={entry} />
          <div className="gx-trash-body">
            <strong>{entry.title}</strong>
            <small>{describe(entry)}</small>
          </div>
          <span className="gx-trash-when">
            <time dateTime={entry.trashedAt}>Trashed {trashedOn(entry.trashedAt)}</time>
            <span className={left <= 3 ? "is-soon" : ""}>{left === 0 ? "Deleted today" : `${plural(left, "day")} left`}</span>
          </span>
          {confirming === key
            ? <div className="gx-trash-confirm" role="group" aria-label={`Confirm deleting “${entry.title}”`}>
              <span>{entry.kind === "library" ? "Delete the library, its sources, topics and conversations for good?" : "Delete for good? This can’t be undone."}</span>
              <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setConfirming(null)}>Cancel</button>
              <button type="button" className="gx-btn gx-btn-danger gx-btn-sm" disabled={working} onClick={() => void deleteForever(entry)}>{working ? <LoaderCircle className="spin" size={13} /> : <Trash2 size={13} />}Delete forever</button>
            </div>
            : <div className="gx-trash-actions">
              <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={working} onClick={() => void restore(entry)} aria-label={`Restore “${entry.title}”`}>{working ? <LoaderCircle className="spin" size={13} /> : <RotateCcw size={13} />}Restore</button>
              <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={working} onClick={() => setConfirming(key)} aria-label={`Delete “${entry.title}” forever`}>Delete forever…</button>
            </div>}
        </li>;
      })}
    </ul>}

    {!loading && !error && entries.length === 0 && <div className="gx-empty-state">
      <span className="gx-empty-icon"><Trash2 size={20} /></span>
      <h2>Trash is empty.</h2>
      <p>Things you move to Trash wait here for {plural(retentionDays, "day")}, so you can change your mind.</p>
    </div>}
  </div>;
}
