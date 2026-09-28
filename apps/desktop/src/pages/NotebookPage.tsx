import type { NotebookNote } from "@gunther/contracts";
import {
  Archive,
  ArchiveRestore,
  ArrowRight,
  BookOpen,
  Check,
  CircleAlert,
  Clock3,
  FileInput,
  Inbox,
  LoaderCircle,
  Pin,
  PinOff,
  Plus,
  Search,
  StickyNote,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KnowledgeBase } from "../atlas";
import { knowledgeApi } from "../api";

type NotebookTab = NotebookNote["status"];

interface NotebookPageProps {
  bases: KnowledgeBase[];
  focusNoteId?: string | null;
  onFocused: () => void;
  onFiled: (note: NotebookNote, baseId: string, claimCount: number) => void;
  onNotify: (message: string) => void;
}

const statusLabel: Record<NotebookTab, string> = {
  inbox: "Unsorted",
  filed: "Filed",
  archived: "Archive",
};

const relativeTime = (value: string) => {
  const elapsed = Date.now() - new Date(value).getTime();
  if (elapsed < 60_000) return "just now";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m ago`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}h ago`;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
};

const notePreview = (note: NotebookNote) =>
  note.content.replace(/\s+/g, " ").trim() || "Empty note — start writing when the thought arrives.";

export function NotebookPage({ bases, focusNoteId, onFocused, onFiled, onNotify }: NotebookPageProps) {
  const [notes, setNotes] = useState<NotebookNote[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<NotebookTab>("inbox");
  const [query, setQuery] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [targetBaseId, setTargetBaseId] = useState(bases[0]?.id ?? "");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [filing, setFiling] = useState(false);
  const [saveState, setSaveState] = useState<"saved" | "editing" | "saving" | "error">("saved");
  const [error, setError] = useState<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  const lastLoadedDraft = useRef({ title: "", content: "" });
  const skipNextAutosave = useRef(false);

  const loadNotes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const loaded = await knowledgeApi.notes();
      setNotes(loaded);
      setSelectedId((current) => current && loaded.some((note) => note.id === current && note.status === tab)
        ? current
        : loaded.find((note) => note.status === tab)?.id ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The notebook could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => { void loadNotes(); }, [loadNotes]);

  useEffect(() => {
    if (!focusNoteId || !notes.length) return;
    const focused = notes.find((note) => note.id === focusNoteId);
    if (!focused) {
      void loadNotes();
      return;
    }
    setTab(focused.status);
    setSelectedId(focused.id);
    onFocused();
  }, [focusNoteId, loadNotes, notes, onFocused]);

  const selected = notes.find((note) => note.id === selectedId) ?? null;
  useEffect(() => {
    skipNextAutosave.current = true;
    selectedIdRef.current = selected?.id ?? null;
    const next = { title: selected?.title ?? "", content: selected?.content ?? "" };
    lastLoadedDraft.current = next;
    setTitle(next.title);
    setContent(next.content);
    setSaveState("saved");
    setError(null);
    if (selected?.knowledgeBaseId) setTargetBaseId(selected.knowledgeBaseId);
  }, [selected?.id]);

  useEffect(() => {
    if (!selected || selected.status === "filed") return;
    if (skipNextAutosave.current) {
      skipNextAutosave.current = false;
      return;
    }
    if (title === lastLoadedDraft.current.title && content === lastLoadedDraft.current.content) return;
    setSaveState("editing");
    const noteId = selected.id;
    const timer = window.setTimeout(() => {
      setSaveState("saving");
      void knowledgeApi.updateNote(noteId, {
        title: title.trim() || "Untitled note",
        content,
      }).then((saved) => {
        if (selectedIdRef.current === noteId) {
          lastLoadedDraft.current = { title: saved.title, content: saved.content };
          setSaveState("saved");
        }
        setNotes((current) => current.map((note) => note.id === saved.id ? saved : note));
      }).catch((reason) => {
        setSaveState("error");
        setError(reason instanceof Error ? reason.message : "This note could not be saved.");
      });
    }, 650);
    return () => window.clearTimeout(timer);
  }, [content, selected, title]);

  const visibleNotes = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return notes.filter((note) => note.status === tab && (!needle || `${note.title} ${note.content}`.toLocaleLowerCase().includes(needle)));
  }, [notes, query, tab]);

  const counts = useMemo(() => ({
    inbox: notes.filter((note) => note.status === "inbox").length,
    filed: notes.filter((note) => note.status === "filed").length,
    archived: notes.filter((note) => note.status === "archived").length,
  }), [notes]);

  const chooseTab = (next: NotebookTab) => {
    setTab(next);
    const first = notes.find((note) => note.status === next);
    setSelectedId(first?.id ?? null);
  };

  const createNote = async () => {
    setCreating(true);
    setError(null);
    try {
      const created = await knowledgeApi.createNote();
      setNotes((current) => [created, ...current]);
      setTab("inbox");
      setSelectedId(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "A new note could not be created.");
    } finally {
      setCreating(false);
    }
  };

  const updatePin = async () => {
    if (!selected) return;
    try {
      const updated = await knowledgeApi.updateNote(selected.id, { pinned: !selected.pinned });
      setNotes((current) => current.map((note) => note.id === updated.id ? updated : note));
      onNotify(updated.pinned ? "Note pinned to the top." : "Note unpinned.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The pin could not be changed.");
    }
  };

  const moveToStatus = async (status: NotebookTab) => {
    if (!selected) return;
    try {
      const updated = await knowledgeApi.updateNote(selected.id, { status });
      setNotes((current) => current.map((note) => note.id === updated.id ? updated : note));
      const next = notes.find((note) => note.id !== selected.id && note.status === tab);
      setSelectedId(next?.id ?? null);
      onNotify(status === "archived" ? "Note moved to the archive." : "Note restored.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The note could not be moved.");
    }
  };

  const fileNote = async () => {
    if (!selected || !targetBaseId || filing) return;
    if (title !== lastLoadedDraft.current.title || content !== lastLoadedDraft.current.content) {
      setError("Wait a moment for the latest edit to save before filing.");
      return;
    }
    setFiling(true);
    setError(null);
    try {
      const result = await knowledgeApi.fileNote(selected.id, targetBaseId);
      setNotes((current) => current.map((note) => note.id === result.note.id ? result.note : note));
      setTab("filed");
      setSelectedId(result.note.id);
      window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
      onFiled(result.note, targetBaseId, result.importResult.source.assertionCount);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "This note could not be filed.");
    } finally {
      setFiling(false);
    }
  };

  return <div className="notebook-page page-enter">
    <header className="notebook-header">
      <span><h1>Notebook</h1><p>Catch a thought before it has a home. Notes stay lightweight and searchable until their place becomes clear.</p></span>
      <button className="primary-button" onClick={() => void createNote()} disabled={creating}>{creating ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />}New note <kbd>⌘N</kbd></button>
    </header>

    {error && <div className="notebook-error" role="alert"><CircleAlert size={14} /><span>{error}</span><button onClick={() => setError(null)}>Dismiss</button></div>}

    <section className="notebook-workspace">
      <aside className="notebook-index">
        <div className="notebook-tabs">
          {(["inbox", "filed", "archived"] as const).map((status) => <button key={status} className={tab === status ? "is-active" : ""} onClick={() => chooseTab(status)}>{status === "inbox" ? <Inbox size={13} /> : status === "filed" ? <BookOpen size={13} /> : <Archive size={13} />}<span>{statusLabel[status]}</span><i>{counts[status]}</i></button>)}
        </div>
        <label className="notebook-search"><Search size={13} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find a note…" /></label>
        <div className="notebook-list">
          {loading && <div className="notebook-list-state"><LoaderCircle className="spin" size={16} />Loading notebook…</div>}
          {!loading && visibleNotes.map((note) => <button key={note.id} className={selectedId === note.id ? "is-active" : ""} onClick={() => setSelectedId(note.id)}>
            <span><strong>{note.title}</strong>{note.pinned && <Pin size={10} />}</span>
            <p>{notePreview(note)}</p>
            <small>{relativeTime(note.updatedAt)}{note.knowledgeBaseId ? " · filed" : " · no home yet"}</small>
          </button>)}
          {!loading && visibleNotes.length === 0 && <div className="notebook-list-state"><StickyNote size={18} /><strong>{query ? "No matching notes" : `No ${statusLabel[tab].toLowerCase()} notes`}</strong><p>{tab === "inbox" ? "Create one without deciding where it belongs." : "This space is clear."}</p></div>}
        </div>
      </aside>

      <article className="notebook-editor">
        {selected ? <>
          <div className="note-editor-toolbar">
            <span className={`note-save-state is-${saveState}`}>{saveState === "saving" ? <LoaderCircle className="spin" size={11} /> : saveState === "error" ? <CircleAlert size={11} /> : <Check size={11} />}{saveState === "editing" ? "Unsaved changes" : saveState === "saving" ? "Saving…" : saveState === "error" ? "Save failed" : `Saved ${relativeTime(selected.updatedAt)}`}</span>
            <button onClick={() => void updatePin()} title={selected.pinned ? "Unpin note" : "Pin note"}>{selected.pinned ? <PinOff size={14} /> : <Pin size={14} />}</button>
            {selected.status === "archived" ? <button onClick={() => void moveToStatus(selected.promotedSourceId ? "filed" : "inbox")} title="Restore note"><ArchiveRestore size={14} /></button> : <button onClick={() => void moveToStatus("archived")} title="Archive note"><Archive size={14} /></button>}
          </div>
          <input className="note-title" value={title} disabled={selected.status === "filed"} onChange={(event) => setTitle(event.target.value)} placeholder="Untitled note" maxLength={160} />
          <textarea className="note-body" value={content} disabled={selected.status === "filed"} onChange={(event) => setContent(event.target.value)} placeholder="Write the thought as it is. You can decide where it belongs later…" maxLength={50_000} />
          <footer className="note-editor-meta"><span><Clock3 size={11} />Created {new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(selected.createdAt))}</span><span>{content.trim() ? content.trim().split(/\s+/).length : 0} words</span><span>{selected.status === "filed" ? "Read-only source snapshot" : "Private local draft"}</span></footer>
        </> : <div className="notebook-editor-empty"><span><StickyNote size={24} /></span><h2>{loading ? "Opening your notebook…" : "A place for thoughts without a category"}</h2><p>Choose a note from the left, or start one before the idea disappears.</p>{!loading && <button className="primary-button" onClick={() => void createNote()}><Plus size={14} />Create a note</button>}</div>}
      </article>

      <aside className="notebook-filing">
        {selected ? selected.status === "filed" ? <div className="filed-note-card"><span><Check size={18} /></span><small>Filed knowledge</small><h2>{bases.find((base) => base.id === selected.knowledgeBaseId)?.title ?? "Library"}</h2><p>This note is now a preserved source snapshot. Its extracted claims enter the normal review workflow.</p><div><FileInput size={13} /><span><strong>Source created</strong><small>{selected.promotedSourceId}</small></span></div></div> : selected.status === "archived" ? <div className="filing-empty"><Archive size={20} /><h2>Archived, not deleted</h2><p>Restore this note whenever it becomes useful again.</p></div> : <>
          <div className="filing-intro"><span><FileInput size={18} /></span><small>Optional next step</small><h2>Give this thought a home</h2><p>Keep writing freely. When the idea is mature enough, file a snapshot into a library.</p></div>
          <label className="filing-target"><span>Library</span><select value={targetBaseId} onChange={(event) => setTargetBaseId(event.target.value)}>{bases.map((base) => <option key={base.id} value={base.id}>{base.title}</option>)}</select></label>
          <div className="filing-promise"><span><Check size={11} />Original note preserved</span><span><Check size={11} />Claims remain reviewable</span><span><Check size={11} />Nothing silently accepted</span></div>
          <button className="file-note-button" disabled={!content.trim() || !targetBaseId || filing || saveState !== "saved"} onClick={() => void fileNote()}>{filing ? <LoaderCircle className="spin" size={13} /> : <FileInput size={13} />}File into knowledge <ArrowRight size={12} /></button>
          <p className="filing-hint">Filing creates a read-only source snapshot. Continue the thought later in a fresh note.</p>
        </> : <div className="filing-empty"><BookOpen size={20} /><h2>Structure can wait</h2><p>The Notebook is the calm space between noticing something and knowing what it means.</p></div>}
      </aside>
    </section>
  </div>;
}
