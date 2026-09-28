import type { NotebookNote } from "@gunther/contracts";
import { Archive, ArchiveRestore, BookOpen, CircleAlert, LoaderCircle, NotebookPen, PenLine, Pin, PinOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { knowledgeApi } from "../api";
import type { KnowledgeBase } from "../atlas";
import { MarkdownEditor, preloadMarkdownEditor } from "../components/markdown/MarkdownEditor";
import { countWords, setTaskCheckedAt } from "../components/markdown/markdownEditing";
import { MarkdownView } from "../components/markdown/MarkdownView";
import { useShortcut, withShortcut } from "../shortcuts/shortcuts";
import { DecisionCard, FileDecision, SettledDecision } from "./Decision";
import { AsideSection, DetailsList, formatDate, ItemHeader, ItemLayout, type ItemNavigation } from "./ItemLayout";

type SaveState = "saved" | "editing" | "saving" | "error";

const relative = (value: string) => {
  const elapsed = Date.now() - new Date(value).getTime();
  if (elapsed < 60_000) return "just now";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} min ago`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} h ago`;
  return formatDate(value) ?? "";
};

export interface NoteItemProps {
  note: NotebookNote;
  bases: KnowledgeBase[];
  nav: ItemNavigation;
  onResolved: (message: string) => void;
  onOpenBase: (id: string) => void;
  onOpenNotebook: (id: string) => void;
  onCreateBase: () => void;
  onNotify: (message: string) => void;
  onTitle: (title: string) => void;
}

export function NoteItem({ note: initial, bases, nav, onResolved, onOpenBase, onOpenNotebook, onCreateBase, onNotify, onTitle }: NoteItemProps) {
  const [note, setNote] = useState(initial);
  const [title, setTitle] = useState(initial.title);
  const [content, setContent] = useState(initial.content);
  const [mode, setMode] = useState<"read" | "write">(initial.content.trim() ? "read" : "write");
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [error, setError] = useState<string | null>(null);
  const saved = useRef({ title: initial.title, content: initial.content });
  const draft = useRef({ title: initial.title, content: initial.content });
  draft.current = { title, content };
  const readOnly = note.status === "filed";
  const dirty = title !== saved.current.title || content !== saved.current.content;

  useEffect(() => preloadMarkdownEditor(), []);
  useEffect(() => onTitle(title.trim() || "Untitled note"), [onTitle, title]);

  const save = useCallback(async () => {
    const pending = { ...draft.current };
    if (pending.title === saved.current.title && pending.content === saved.current.content) return;
    setSaveState("saving");
    try {
      const updated = await knowledgeApi.updateNote(note.id, { title: pending.title.trim() || "Untitled note", content: pending.content });
      saved.current = { title: pending.title, content: updated.content };
      setNote(updated);
      setError(null);
      const stillDirty = draft.current.title !== pending.title || draft.current.content !== pending.content;
      setSaveState(stillDirty ? "editing" : "saved");
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
    } catch (reason) {
      setSaveState("error");
      setError(reason instanceof Error ? reason.message : "This note could not be saved.");
      throw reason;
    }
  }, [note.id]);

  // Autosave shortly after typing stops.
  useEffect(() => {
    if (readOnly || !dirty) return undefined;
    setSaveState((current) => (current === "saving" ? current : "editing"));
    const timer = window.setTimeout(() => void save().catch(() => undefined), 650);
    return () => window.clearTimeout(timer);
  }, [content, dirty, readOnly, save, title]);

  // Leaving the page (Back, next item) never drops the last keystrokes.
  useEffect(() => () => {
    const pending = draft.current;
    if (pending.title !== saved.current.title || pending.content !== saved.current.content) {
      void knowledgeApi.updateNote(initial.id, { title: pending.title.trim() || "Untitled note", content: pending.content })
        .then(() => window.dispatchEvent(new CustomEvent("gunther:inbox-updated")))
        .catch(() => undefined);
    }
  }, [initial.id]);

  const toggleMode = useCallback(() => {
    if (readOnly) return;
    setMode((current) => (current === "read" ? "write" : "read"));
  }, [readOnly]);
  useShortcut("mod+e", toggleMode, { enabled: !readOnly });
  useShortcut("e", () => setMode("write"), { enabled: !readOnly && mode === "read" });

  const toggleTask = useCallback((offset: number, checked: boolean) => {
    if (readOnly) return;
    setContent((current) => setTaskCheckedAt(current, offset, checked));
  }, [readOnly]);

  const updateNote = async (patch: Parameters<typeof knowledgeApi.updateNote>[1], message: string) => {
    try {
      const updated = await knowledgeApi.updateNote(note.id, patch);
      setNote(updated);
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
      onNotify(message);
      return updated;
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "The note could not be changed.");
      return null;
    }
  };

  const archive = async () => {
    await save().catch(() => undefined);
    const updated = await updateNote({ status: "archived" }, "Note archived. Restore it any time from the Notebook.");
    if (updated) onResolved("Note archived.");
  };

  const file = async (baseId: string) => {
    try {
      await save();
      const result = await knowledgeApi.fileNote(note.id, baseId);
      setNote(result.note);
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
      window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
      const base = bases.find((item) => item.id === baseId);
      onResolved(`Filed “${result.note.title}” into ${base?.title ?? "the library"}.`);
    } catch (reason) {
      onNotify(reason instanceof Error ? `Not filed: ${reason.message}` : "This note could not be filed.");
    }
  };

  const words = countWords(content);
  const statusLabel = note.status === "inbox" ? "Unsorted" : note.status === "filed" ? "Filed" : "Archived";
  const saveLabel = saveState === "saving" ? "Saving…" : saveState === "editing" ? "Editing" : saveState === "error" ? "Not saved" : `Saved ${relative(note.updatedAt)}`;

  const actions = (
    <>
      {!readOnly && (
        <span className={`gx-save-indicator is-${saveState}`} role="status">
          {saveState === "saving" ? <LoaderCircle className="spin" size={12} /> : saveState === "error" ? <CircleAlert size={12} /> : <i />}
          {saveLabel}
        </span>
      )}
      {!readOnly && (
        <div className="gx-segmented" role="group" aria-label="Note view">
          <button type="button" className={mode === "read" ? "is-active" : ""} aria-pressed={mode === "read"} onClick={() => setMode("read")} title={withShortcut("Read", "note-preview")}><BookOpen size={14} />Read</button>
          <button type="button" className={mode === "write" ? "is-active" : ""} aria-pressed={mode === "write"} onClick={() => setMode("write")} title={withShortcut("Write", "note-edit")}><PenLine size={14} />Write</button>
        </div>
      )}
      {note.status !== "filed" && (
        <button type="button" className="gx-icon-button" onClick={() => void updateNote({ pinned: !note.pinned }, note.pinned ? "Note unpinned." : "Note pinned to the top of the Notebook.")} aria-label={note.pinned ? "Unpin note" : "Pin note"} title={note.pinned ? "Unpin" : "Pin"}>
          {note.pinned ? <PinOff size={15} /> : <Pin size={15} />}
        </button>
      )}
      {note.status === "inbox" && <button type="button" className="gx-icon-button" onClick={() => void archive()} aria-label="Archive note" title="Archive"><Archive size={15} /></button>}
    </>
  );

  const titleSlot = mode === "write" && !readOnly
    ? <input className="gx-item-title is-editable" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Untitled note" maxLength={160} aria-label="Note title" />
    : undefined;

  const aside = (
    <>
      {note.status === "inbox" && (
        <FileDecision
          bases={bases}
          defaultBaseId={note.knowledgeBaseId}
          what="note"
          disabledReason={!content.trim() ? "Write something before filing this note." : saveState === "error" ? "Save the latest edit before filing." : null}
          onFile={file}
          onCreateBase={onCreateBase}
        />
      )}
      {note.status === "filed" && (
        <SettledDecision bases={bases} knowledgeBases={note.knowledgeBaseId ? [{ id: note.knowledgeBaseId, title: bases.find((base) => base.id === note.knowledgeBaseId)?.title ?? "Library" }] : []} onOpenBase={onOpenBase}>
          <p className="gx-decision-note">This note was filed as a read-only snapshot. Continue the thought in a new note.</p>
        </SettledDecision>
      )}
      {note.status === "archived" && (
        <DecisionCard tone="held" status="Archived" title="Set aside, not deleted">
          <button type="button" className="gx-btn gx-btn-quiet" onClick={() => void updateNote({ status: note.promotedSourceId ? "filed" : "inbox" }, "Note restored to Inbox.")}><ArchiveRestore size={14} />Restore</button>
        </DecisionCard>
      )}
      <AsideSection title="Details" action={<button type="button" className="gx-link" onClick={() => onOpenNotebook(note.id)}>All notes</button>}>
        <DetailsList rows={[
          { label: "Status", value: statusLabel },
          { label: "Created", value: formatDate(note.createdAt, true) },
          { label: "Edited", value: relative(note.updatedAt) },
          { label: "Length", value: `${words.toLocaleString()} ${words === 1 ? "word" : "words"}${words > 200 ? ` · ${Math.max(1, Math.round(words / 220))} min read` : ""}` },
        ]} />
      </AsideSection>
    </>
  );

  return (
    <ItemLayout
      nav={nav}
      actions={actions}
      header={<ItemHeader icon={NotebookPen} tone="clay" kicker={`Note · ${statusLabel}`} title={title.trim() || "Untitled note"} titleSlot={titleSlot} meta={[`Edited ${relative(note.updatedAt)}`, note.pinned ? "Pinned" : null]} />}
      aside={aside}
    >
      {error && <p className="gx-reader-warning" role="alert"><CircleAlert size={13} />{error}</p>}
      {mode === "write" && !readOnly ? (
        <MarkdownEditor
          value={content}
          onChange={setContent}
          ariaLabel="Note text"
          placeholder="Write in Markdown — # heading, - list, - [ ] task, **bold**…"
          autoFocus
          maxLength={50_000}
          onTogglePreview={() => setMode("read")}
          onSubmit={() => setMode("read")}
          onEscape={() => setMode("read")}
        />
      ) : (
        <div className="gx-note-read" onDoubleClick={() => { if (!readOnly) setMode("write"); }}>
          <MarkdownView
            source={content}
            onToggleTask={readOnly ? undefined : toggleTask}
            empty={(
              <div className="gx-note-empty">
                <p>This note is empty.</p>
                {!readOnly && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setMode("write")}><PenLine size={13} />Start writing</button>}
              </div>
            )}
          />
        </div>
      )}
    </ItemLayout>
  );
}
