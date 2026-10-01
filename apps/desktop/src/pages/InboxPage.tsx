import type { InboxItem, TrashItem } from "@gunther/contracts";
import {
  AudioLines,
  Camera,
  Check,
  ChevronRight,
  CircleAlert,
  FileText,
  FolderInput,
  Globe,
  Inbox,
  LoaderCircle,
  NotebookPen,
  Plus,
  Table2,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { knowledgeApi } from "../api";
import type { KnowledgeBase } from "../atlas";
import { LibraryGlyph } from "../design/LibraryGlyph";
import { itemKey, refFromInbox, type ItemRef } from "../items/itemRef";
import { LibraryPicker } from "../items/LibraryPicker";
import { inboxPreview } from "../items/sourceContent";
import { comboKeys, useShortcut, withShortcut } from "../shortcuts/shortcuts";
import { moveToTrash } from "../trash/trash";

type InboxFilter = "all" | "unfiled" | "needs_review";

interface InboxPageProps {
  bases: KnowledgeBase[];
  onOpenBase: (id: string) => void;
  onOpenNote: (id: string) => void;
  /** Open an item in its own page; `queue` is the visible list, for J / K. */
  onOpenItem?: (item: ItemRef, queue: ItemRef[]) => void;
  /** The item last opened from here, focused again on return. */
  focusItemKey?: string | null;
  onCapture: () => void;
  onCreateBase?: () => void;
  onCountChange?: (count: number) => void;
  onNotify: (message: string) => void;
  /** An item went to Trash; the host offers Undo. */
  onTrashed?: (entry: TrashItem) => void;
}

const KIND = {
  note: { icon: NotebookPen, tone: "clay", label: "Note" },
  paper: { icon: FileText, tone: "blue", label: "Paper" },
  file: { icon: FileText, tone: "blue", label: "Document" },
  link: { icon: Globe, tone: "amber", label: "Web page" },
  image: { icon: Camera, tone: "violet", label: "Photo or scan" },
  table: { icon: Table2, tone: "green", label: "Table" },
  recording: { icon: AudioLines, tone: "rose", label: "Recording" },
  course: { icon: AudioLines, tone: "rose", label: "Course recording" },
} as const;

const itemKind = (item: InboxItem) => {
  if (item.itemType === "quick_note") return { icon: NotebookPen, tone: "clay", label: "Quick note" };
  return KIND[item.sourceKind ?? "file"];
};

const stateCopy = (item: InboxItem) => item.state === "unfiled"
  ? { label: "Choose a home", description: "The original is preserved until you decide where it belongs." }
  : { label: `${item.assertionCount} ${item.assertionCount === 1 ? "claim" : "claims"} to review`, description: "Nothing becomes trusted knowledge until you accept it." };

const formatDay = (value: string) => {
  const date = new Date(value);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

export function InboxPageV3({ bases, onOpenBase, onOpenNote, onOpenItem, focusItemKey = null, onCapture, onCreateBase, onCountChange, onNotify, onTrashed }: InboxPageProps) {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [filter, setFilter] = useState<InboxFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [targets, setTargets] = useState<Record<string, string>>({});
  const list = useRef<HTMLDivElement>(null);
  const restoredFocus = useRef(false);
  const refocusIndex = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await knowledgeApi.inbox();
      setItems(next);
      setError(null);
      onCountChange?.(next.length);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Inbox could not be opened.");
    } finally {
      setLoading(false);
    }
  }, [onCountChange]);

  useEffect(() => {
    void refresh();
    const handleUpdate = () => void refresh();
    window.addEventListener("gunther:inbox-updated", handleUpdate);
    return () => window.removeEventListener("gunther:inbox-updated", handleUpdate);
  }, [refresh]);

  const counts = useMemo(() => ({
    all: items.length,
    unfiled: items.filter((item) => item.state === "unfiled").length,
    needs_review: items.filter((item) => item.state === "needs_review").length,
  }), [items]);
  const visible = useMemo(() => items.filter((item) => filter === "all" || item.state === filter), [filter, items]);
  const queue = useMemo(() => visible.map(refFromInbox), [visible]);

  const openButtons = () => Array.from(list.current?.querySelectorAll<HTMLButtonElement>(".gx-inbox-open") ?? []);
  const moveFocus = (step: number) => {
    const buttons = openButtons();
    if (!buttons.length) return;
    const current = buttons.findIndex((button) => button === document.activeElement || button.closest("article")?.contains(document.activeElement));
    const next = current === -1 ? (step > 0 ? 0 : buttons.length - 1) : Math.min(buttons.length - 1, Math.max(0, current + step));
    buttons[next]!.focus();
    buttons[next]!.closest("article")?.scrollIntoView({ block: "nearest" });
  };
  useShortcut("j", () => moveFocus(1), { enabled: visible.length > 0 });
  useShortcut("k", () => moveFocus(-1), { enabled: visible.length > 0 });
  const onListKeyDown = (event: KeyboardEvent) => {
    if (event.target instanceof HTMLElement && event.target.closest(".gx-picker")) return;
    if (event.key === "ArrowDown") { event.preventDefault(); moveFocus(1); }
    if (event.key === "ArrowUp") { event.preventDefault(); moveFocus(-1); }
  };

  // Back from an item: put focus on the row it was opened from.
  useEffect(() => {
    if (loading || restoredFocus.current || !focusItemKey) return;
    restoredFocus.current = true;
    const index = queue.findIndex((ref) => itemKey(ref) === focusItemKey);
    const buttons = openButtons();
    const target = buttons[index >= 0 ? index : 0];
    target?.focus({ preventScroll: true });
    target?.closest("article")?.scrollIntoView({ block: "nearest" });
  }, [focusItemKey, loading, queue]);

  const open = (item: InboxItem) => {
    if (onOpenItem) onOpenItem(refFromInbox(item), queue);
    else if (item.noteId) onOpenNote(item.noteId);
  };

  const selectedTarget = (item: InboxItem) => {
    const chosen = targets[item.id] ?? item.knowledgeBases[0]?.id;
    return chosen && bases.some((base) => base.id === chosen) ? chosen : bases[0]?.id ?? "";
  };
  const fileItem = async (item: InboxItem) => {
    const knowledgeBaseId = selectedTarget(item);
    if (!knowledgeBaseId) {
      onNotify("Create a library before filing this source, or leave it safely in Inbox.");
      return;
    }
    setWorkingId(item.id);
    try {
      if (item.sourceId) await knowledgeApi.fileSource(item.sourceId, knowledgeBaseId);
      else if (item.noteId) await knowledgeApi.fileNote(item.noteId, knowledgeBaseId);
      await refresh();
      window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
      onNotify(`Filed “${item.title}”. Any AI suggestions remain here for review.`);
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "This item could not be filed.");
    } finally {
      setWorkingId(null);
    }
  };

  const trashTarget = (item: InboxItem) => item.sourceId
    ? { kind: "source" as const, id: item.sourceId }
    : item.noteId ? { kind: "note" as const, id: item.noteId } : null;
  const trashItem = async (item: InboxItem) => {
    const target = trashTarget(item);
    if (!target) return;
    setWorkingId(item.id);
    try {
      // moveToTrash announces the change, which refreshes this list.
      onTrashed?.(await moveToTrash(target));
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "This item could not be moved to Trash.");
    } finally {
      setWorkingId(null);
    }
  };
  // ⌘⌫ on a row moves it to Trash; focus then lands on the row that took its place.
  const trashFocused = () => {
    const index = openButtons().findIndex((button) => button.closest("article")?.contains(document.activeElement));
    const item = visible[index];
    if (!item || !trashTarget(item)) return;
    refocusIndex.current = index;
    void trashItem(item);
  };
  useShortcut("mod+backspace", trashFocused, { enabled: visible.length > 0, allowInInputs: false });
  useEffect(() => {
    if (refocusIndex.current === null) return;
    const buttons = openButtons();
    buttons[Math.min(refocusIndex.current, buttons.length - 1)]?.focus();
    refocusIndex.current = null;
  }, [visible]);

  const reviewSource = async (item: InboxItem, status: "verified" | "disputed") => {
    if (!item.sourceId) return;
    setWorkingId(item.id);
    try {
      await knowledgeApi.updateSourceAssertionStatuses(item.sourceId, {
        status,
        reason: status === "verified" ? "Accepted from unified Inbox" : "Marked disputed from unified Inbox",
      });
      await refresh();
      onNotify(status === "verified" ? "Claims accepted as trusted knowledge." : "Claims preserved as disputed evidence.");
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "The review decision could not be saved.");
    } finally {
      setWorkingId(null);
    }
  };

  const tabs: Array<{ id: InboxFilter; label: string }> = [
    { id: "all", label: "Needs attention" },
    { id: "unfiled", label: "To organize" },
    { id: "needs_review", label: "To review" },
  ];

  return <div className="gx-inbox page-enter">
    <header className="gx-page-header">
      <div>
        <h1>Inbox</h1>
        <p>Captures without a home, and AI suggestions waiting for your decision. Nothing here is trusted until you say so.</p>
      </div>
      <div className="gx-page-actions">
        <button type="button" className="gx-btn gx-btn-quiet" onClick={onCapture}><Plus size={15} />Capture</button>
      </div>
    </header>

    <nav className="gx-tabs" aria-label="Inbox filters">
      {tabs.map(({ id, label }) => <button type="button" key={id} className={filter === id ? "is-active" : ""} aria-pressed={filter === id} onClick={() => setFilter(id)}><span>{label}</span><i>{counts[id]}</i></button>)}
    </nav>

    {error && <div className="gx-banner is-error" role="alert"><CircleAlert size={16} /><span><strong>Inbox is temporarily unavailable</strong><small>{error}</small></span><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => { setLoading(true); void refresh(); }}>Retry</button></div>}
    {loading && <div className="gx-inbox-list is-loading" aria-label="Opening your Inbox">{[0, 1, 2].map((index) => <div className="gx-inbox-skeleton" key={index} aria-hidden="true"><i /><span><b /><b /><b /></span></div>)}</div>}

    {!loading && !error && visible.length > 0 && <>
      <div className="gx-inbox-list" ref={list} onKeyDown={onListKeyDown}>{visible.map((item, index) => {
        const kind = itemKind(item);
        const Icon = kind.icon;
        const copy = stateCopy(item);
        const preview = inboxPreview(item);
        const isWorking = workingId === item.id;
        return <article key={`${item.itemType}-${item.id}`} className={`gx-inbox-item state-${item.state} ${isWorking ? "is-working" : ""}`} style={{ "--i": index } as CSSProperties}>
          <span className={`gx-kind-tile tone-${kind.tone}`} aria-hidden="true"><Icon size={15} /></span>
          <div className="gx-inbox-body">
            <header>
              <span className="gx-inbox-kind">{kind.label}</span>
              <time dateTime={item.updatedAt}>{formatDay(item.updatedAt)}</time>
              {preview.detail && <span className="gx-inbox-detail">{preview.detail}</span>}
              {item.knowledgeBases.map((base) => {
                const known = bases.find((candidate) => candidate.id === base.id);
                return <button type="button" key={base.id} className="gx-library-pill" onClick={() => onOpenBase(base.id)}>{known && <LibraryGlyph base={known} size="xs" />}{base.title}<ChevronRight size={11} /></button>;
              })}
            </header>
            <h2><button type="button" className="gx-inbox-open" onClick={() => open(item)} aria-describedby={`inbox-state-${item.id}`}>{item.title}</button></h2>
            {preview.text ? <p>{preview.text}</p> : <p className="is-empty">{item.itemType === "quick_note" ? "An empty note, ready for a thought." : "Open it to read the preserved original."}</p>}
            <div className="gx-inbox-state" id={`inbox-state-${item.id}`}><i /><strong>{copy.label}</strong><span>{copy.description}</span></div>
          </div>
          <span className="gx-inbox-go" aria-hidden="true">Open<ChevronRight size={13} /></span>
          <footer>
            {trashTarget(item) && <button type="button" className="gx-icon-button gx-inbox-trash" disabled={isWorking} onClick={() => void trashItem(item)} aria-label={`Move “${item.title}” to Trash`} title={withShortcut("Move to Trash", "item-trash")}><Trash2 size={14} /></button>}
            {item.state === "unfiled" && <>
              <span className="gx-footer-spacer" />
              {bases.length > 0 && <div className="gx-inbox-picker"><LibraryPicker bases={bases} value={selectedTarget(item)} onChange={(id) => setTargets((current) => ({ ...current, [item.id]: id }))} onCreate={onCreateBase} disabled={isWorking} label="File to library" placement={index > 2 && index === visible.length - 1 ? "above" : "below"} /></div>}
              <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={isWorking || bases.length === 0} onClick={() => void fileItem(item)}>{isWorking ? <LoaderCircle className="spin" size={13} /> : <FolderInput size={13} />}File source</button>
            </>}
            {item.state === "needs_review" && item.itemType === "source" && <>
              <span className="gx-footer-spacer" />
              <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={isWorking} onClick={() => void reviewSource(item, "disputed")}>Dispute</button>
              <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={isWorking || item.assertionCount === 0} onClick={() => void reviewSource(item, "verified")}><Check size={13} />Accept {item.assertionCount || "claims"}</button>
            </>}
          </footer>
        </article>;
      })}</div>
      <p className="gx-list-hint" aria-hidden="true">
        <span><kbd>J</kbd><kbd>K</kbd> move</span>
        <span><kbd>↵</kbd> open</span>
        <span>{comboKeys("mod+enter").map((key) => <kbd key={key}>{key}</kbd>)} file or accept, inside an item</span>
        <span>{comboKeys("mod+backspace").map((key) => <kbd key={key}>{key}</kbd>)} move to Trash</span>
      </p>
    </>}

    {!loading && !error && visible.length === 0 && <div className="gx-empty-state">
      <span className="gx-empty-icon"><Inbox size={20} /></span>
      <h2>You’re all caught up.</h2>
      <p>New sources can wait here without being forced into a library.</p>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onCapture}><Plus size={13} />Capture something</button>
    </div>}
  </div>;
}
