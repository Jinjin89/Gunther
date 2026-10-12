import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { ArrowDownAZ, ArrowUpRight, ChevronRight, Folder, FolderInput, FolderOutput, FolderPlus, MessageSquareText, PenLine, Plus, Settings2, Trash2, X } from "lucide-react";
import type { ContentsSource, LibraryContents, SourceDigestState } from "@gunther/contracts";
import { knowledgeApi } from "../api";
import { kindIcon } from "./ContentsRow";
import { addedOn, describeSources, fileDetail, kindCounts, readableLength, recordingLength, sectionPath, shortAuthors, sourcesIn, totalSeconds, type ContentsNode, type ContentsTree } from "./contentsTree";
import { SectionOverview } from "./SectionPanels";

type SectionNode = Extract<ContentsNode, { type: "section" }>;
const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

export interface LibraryInfo { title: string; eyebrow?: string; description?: string }

/**
 * Text that is edited where it is shown: a click puts the cursor in it, Enter or leaving
 * keeps the change, Esc puts the old text back. Titles cannot be left empty.
 */
export function EditableText({ value, label, placeholder, className = "", required = false, maxLength, inputRef, onSave }: {
  value: string; label: string; placeholder?: string; className?: string; required?: boolean; maxLength: number;
  inputRef?: RefObject<HTMLTextAreaElement | null>; onSave: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const own = useRef<HTMLTextAreaElement>(null);
  const field = inputRef ?? own;
  useEffect(() => { setDraft(value); }, [value]);
  // Grow with the text, so a long title wraps instead of scrolling inside a box.
  useLayoutEffect(() => {
    const element = field.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [draft, field]);
  const commit = () => {
    const next = draft.replace(/\s+/g, " ").trim();
    if ((required && !next) || next === value) { setDraft(value); return; }
    onSave(next);
  };
  return <textarea ref={field} rows={1} className={`contents-editable ${className}`} aria-label={label} placeholder={placeholder} maxLength={maxLength} value={draft} spellCheck={false}
    onChange={(event) => setDraft(event.target.value)}
    onBlur={commit}
    onKeyDown={(event) => {
      event.stopPropagation();
      if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); }
      if (event.key === "Escape") { event.preventDefault(); setDraft(value); requestAnimationFrame(() => field.current?.blur()); }
    }} />;
}

/** Part A › Lecture 1: the sections above, each a button to it (the library is the × above). */
function Crumbs({ path, onSection, unplaced = false }: { path: Array<{ id: string; title: string }>; onSection: (id: string) => void; unplaced?: boolean }) {
  if (!unplaced && path.length === 0) return null;
  return <nav className="contents-crumbs" aria-label="Where this is">
    {unplaced && <span className="contents-crumb"><span>Not placed yet</span></span>}
    {path.map((step, index) => <span key={step.id} className="contents-crumb"><button type="button" onClick={() => onSection(step.id)}>{step.title}</button>{index < path.length - 1 && <ChevronRight size={12} aria-hidden="true" />}</span>)}
  </nav>;
}

/** Sources counted by kind, each with what adds up for that kind (hours, pages, years). */
function KindList({ sources }: { sources: ContentsSource[] }) {
  const kinds = kindCounts(sources);
  if (kinds.length === 0) return <p className="contents-panel-quiet">Nothing yet.</p>;
  return <ul className="contents-kinds">{kinds.map((kind) => {
    const Icon = kindIcon[kind.sources[0]!.kind];
    const seconds = totalSeconds(kind.sources);
    const pages = kind.sources.reduce((sum, source) => sum + (source.pages ?? 0), 0);
    const years = kind.sources.map((source) => source.year).filter((year): year is number => typeof year === "number");
    const extra = seconds > 0 ? readableLength(seconds)
      : pages > 0 ? plural(pages, "page")
        : years.length ? (Math.min(...years) === Math.max(...years) ? String(years[0]) : `${Math.min(...years)}–${Math.max(...years)}`) : "";
    return <li key={kind.word}><span className={`gx-kind-icon kind-${kind.sources[0]!.kind}`}><Icon size={13} /></span><span>{kind.label}</span>{extra && <small>{extra}</small>}</li>;
  })}</ul>;
}

function PanelHead({ eyebrow, icon, onClose, closeLabel = "Back to the library" }: { eyebrow: string; icon?: ReactNode; onClose?: () => void; closeLabel?: string }) {
  return <header className="contents-panel-head">
    <p className="contents-eyebrow">{icon}{eyebrow}</p>
    {onClose && <button type="button" className="contents-icon-btn is-visible" aria-label={closeLabel} title={closeLabel} onClick={onClose}><X size={15} /></button>}
  </header>;
}

/** Nothing chosen: the library as a whole — what is in it, what is still being read, what came in last. */
export function LibraryPanel({ library, contents, tree, onAsk, onEdit, onReveal, onShowUnplaced }: {
  library: LibraryInfo; contents: LibraryContents; tree: ContentsTree;
  onAsk?: (() => void) | undefined; onEdit?: (() => void) | undefined; onReveal: (id: string) => void; onShowUnplaced: () => void;
}) {
  const busy = contents.sources.filter((source) => source.processing !== "ready");
  const recent = [...contents.sources].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, 5);
  const where = (id: string) => {
    const path = sectionPath(tree, id);
    return path.length ? path.map((step) => step.title).join(" › ") : "Not placed yet";
  };
  return <div className="contents-panel-body">
    <PanelHead eyebrow={library.eyebrow || "Library"} />
    <h2 className="contents-panel-title is-static">{library.title}</h2>
    {library.description && <p className="contents-panel-desc">{library.description}</p>}
    <div className="contents-panel-grid">
      {onAsk && <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" onClick={onAsk}><MessageSquareText size={13} />Ask this library</button>}
      {onEdit && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onEdit}><Settings2 size={13} />Edit details</button>}
    </div>

    <h3 className="contents-panel-label">In this library</h3>
    <KindList sources={contents.sources} />
    <p className="contents-panel-line">
      {plural(contents.sections.length, "section")}
      {tree.unplaced.length > 0 && <> · <button type="button" className="contents-inline-btn" onClick={onShowUnplaced}>{tree.unplaced.length.toLocaleString()} not placed yet</button></>}
    </p>

    {busy.length > 0 && <>
      <h3 className="contents-panel-label">Being read</h3>
      <ul className="contents-panel-list">{busy.map((source) => <li key={source.id}><SourceButton source={source} detail={source.processing === "failed" ? "Couldn’t read" : "Reading…"} failed={source.processing === "failed"} onClick={() => onReveal(source.id)} /></li>)}</ul>
    </>}

    {recent.length > 0 && <>
      <h3 className="contents-panel-label">Recently added</h3>
      <ul className="contents-panel-list">{recent.map((source) => <li key={source.id}><SourceButton source={source} detail={where(source.id)} onClick={() => onReveal(source.id)} /></li>)}</ul>
    </>}
  </div>;
}

function SourceButton({ source, detail, failed = false, onClick }: { source: ContentsSource; detail: string; failed?: boolean; onClick: () => void }) {
  const Icon = kindIcon[source.kind];
  return <button type="button" className="contents-panel-source" onClick={onClick}>
    <span className={`gx-kind-icon kind-${source.kind}`}><Icon size={13} /></span>
    <span><strong>{source.title}</strong><small className={failed ? "is-failed" : ""}>{detail}</small></span>
  </button>;
}

export interface SectionPanelActions {
  onRename: (title: string) => void;
  onNote: (note: string) => void;
  onAddSubsection: () => void;
  onAddSources: () => void;
  onAsk: () => void;
  onSort: () => void;
  onMove: () => void;
  onDelete: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
}

/** A section: its name and note (edited here), where it sits, what it holds, and what can be done with it. */
export function SectionPanel({ baseId, tree, node, confirming, confirmText, actions, onLibrary, onSection, onOpenSource }: {
  baseId: string; tree: ContentsTree; node: SectionNode; confirming: boolean; confirmText: string;
  actions: SectionPanelActions; onLibrary: () => void; onSection: (id: string) => void; onOpenSource: (id: string) => void;
}) {
  const title = useRef<HTMLTextAreaElement>(null);
  const sources = sourcesIn(node);
  const subsections = node.children.filter((child) => child.type === "section").length;
  const seconds = totalSeconds(sources);
  const papers = sources.filter((source) => source.kind === "paper").length;
  const canAsk = node.children.length > 0 || node.section.passageCount > 0;
  const summary = [subsections ? plural(subsections, "sub-section") : "", sources.length ? plural(sources.length, "source") : "Nothing in it yet", seconds ? `${readableLength(seconds)} of recordings` : ""].filter(Boolean);
  return <div className="contents-panel-body" aria-label={`Section ${node.section.title}`}>
    <PanelHead eyebrow="Section" icon={<Folder size={13} />} onClose={onLibrary} />
    <Crumbs path={sectionPath(tree, node.id)} onSection={onSection} />
    <EditableText key={`title:${node.id}`} inputRef={title} className="contents-panel-title" value={node.section.title} label="Name" required maxLength={160} onSave={actions.onRename} />
    <EditableText key={`note:${node.id}`} className="contents-panel-note" value={node.section.description} label="Note" placeholder="Add a note about this section…" maxLength={4000} onSave={actions.onNote} />
    <p className="contents-panel-line">{summary.map((part, index) => <span key={part}>{index > 0 && " · "}<span className="contents-nowrap">{part}</span></span>)}</p>

    <div className="contents-panel-grid">
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={actions.onAddSubsection}><FolderPlus size={13} />Sub-section</button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={actions.onAddSources}><Plus size={13} />Add sources</button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={!canAsk} title={canAsk ? undefined : "Put something in this section first"} onClick={actions.onAsk}><MessageSquareText size={13} />Ask</button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => { title.current?.focus(); title.current?.select(); }}><PenLine size={13} />Rename</button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={node.children.length < 2} onClick={actions.onSort}><ArrowDownAZ size={13} />Sort by name</button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={actions.onMove}><FolderInput size={13} />Move to…</button>
    </div>

    {sources.length > 0 && <>
      <h3 className="contents-panel-label">In this section</h3>
      <KindList sources={sources} />
    </>}
    {papers > 0 && <>
      <h3 className="contents-panel-label">Overview of its papers</h3>
      <SectionOverview key={node.id} baseId={baseId} section={{ id: node.id, title: node.section.title }} paperCount={papers} onOpenSource={onOpenSource} />
    </>}

    <div className="contents-panel-foot">
      {confirming
        ? <div className="contents-confirm" role="alertdialog" aria-label={`Delete ${node.section.title}`}>
          <p>{confirmText}</p>
          <span><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm is-danger" onClick={actions.onConfirmDelete}>Delete section</button><button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={actions.onCancelDelete}>Cancel</button></span>
        </div>
        : <div className="contents-panel-tools"><button type="button" className="is-danger" onClick={actions.onDelete}><Trash2 size={13} />Delete section</button></div>}
    </div>
  </div>;
}

const KIND_NAMES: Record<ContentsSource["kind"], string> = {
  recording: "Recording", course: "Recording", paper: "Paper", file: "Document", link: "Web page", note: "Note", image: "Photo", table: "Table",
};

/** One source: its name (edited here), where it is placed, its details and summary, and Open. */
export function SourcePanel({ tree, source, placed, onRename, onOpen, onMove, onTakeOut, onLibrary, onSection }: {
  tree: ContentsTree; source: ContentsSource; placed: boolean;
  onRename: (title: string) => void; onOpen: () => void; onMove: () => void; onTakeOut: () => void; onLibrary: () => void; onSection: (id: string) => void;
}) {
  const Icon = kindIcon[source.kind];
  const title = useRef<HTMLTextAreaElement>(null);
  const added = new Date(source.createdAt);
  const facts: Array<[string, string]> = [];
  if (source.durationSeconds) facts.push(["Length", recordingLength(source.durationSeconds)]);
  if (source.authors) facts.push(["Authors", source.authors]);
  if (source.year) facts.push(["Year", String(source.year)]);
  if (source.kind !== "paper" && fileDetail(source)) facts.push(["File", fileDetail(source)]);
  if (source.host) facts.push(["Site", source.host]);
  facts.push(["Added", Number.isNaN(added.getTime()) ? addedOn(source) : added.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })]);
  facts.push(["Status", source.processing === "reading" ? "Being read…" : source.processing === "failed" ? "Couldn’t be read" : "Ready"]);
  return <div className="contents-panel-body" aria-label={`Source ${source.title}`}>
    <PanelHead eyebrow={KIND_NAMES[source.kind]} icon={<span className={`contents-eyebrow-icon kind-${source.kind}`}><Icon size={13} /></span>} onClose={onLibrary} />
    <Crumbs path={sectionPath(tree, source.id)} unplaced={!placed} onSection={onSection} />
    <EditableText key={`source:${source.id}`} inputRef={title} className="contents-panel-title" value={source.title} label="Name" required maxLength={160} onSave={onRename} />
    {source.kind === "paper" && source.authors && <p className="contents-panel-line">{shortAuthors(source.authors)}{source.year ? ` · ${source.year}` : ""}</p>}
    <div className="contents-panel-grid">
      <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" onClick={onOpen}><ArrowUpRight size={13} />Open</button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => { title.current?.focus(); title.current?.select(); }}><PenLine size={13} />Rename</button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onMove}><FolderInput size={13} />{placed ? "Move to…" : "Place in…"}</button>
      {placed && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onTakeOut}><FolderOutput size={13} />Take out</button>}
    </div>
    <dl className="contents-facts">{facts.map(([name, value]) => <div key={name}><dt>{name}</dt><dd className={name === "Status" && source.processing === "failed" ? "is-failed" : ""}>{value}</dd></div>)}</dl>
    <h3 className="contents-panel-label">Summary</h3>
    <SourceSummary key={source.id} id={source.id} />
  </div>;
}

/** The source's written summary, or plainly why there is none (no model, still reading, failed). */
function SourceSummary({ id }: { id: string }) {
  const [state, setState] = useState<SourceDigestState | null>(null);
  const [failed, setFailed] = useState("");
  const [asking, setAsking] = useState(false);
  useEffect(() => {
    let active = true;
    void knowledgeApi.sourceDigest(id).then((next) => { if (active) setState(next); }).catch((reason) => { if (active) setFailed(message(reason, "The summary could not be loaded.")); });
    return () => { active = false; };
  }, [id]);
  const write = async () => {
    setAsking(true); setFailed("");
    try { setState(await knowledgeApi.writeSourceDigest(id)); } catch (reason) { setFailed(message(reason, "The summary could not be started.")); } finally { setAsking(false); }
  };
  if (failed) return <p className="contents-panel-quiet" role="alert">{failed}</p>;
  if (!state) return <p className="contents-panel-quiet">Loading…</p>;
  if (state.digest && state.digest.overview) {
    return <div className="contents-summary">
      <p>{state.digest.overview}</p>
      <small>{state.digest.engine ? `Written by ${state.digest.engine}` : "Written by a model"}{state.stale ? " · the source changed since" : ""}</small>
    </div>;
  }
  if (state.state === "reading") return <p className="contents-panel-quiet">Gunther is still reading this source.</p>;
  if (state.state === "writing") return <p className="contents-panel-quiet">The summary is being written…</p>;
  if (state.state === "off") return <p className="contents-panel-quiet">{state.offReason === "no_key" ? "Summaries need a model. Add a key in Settings → Models." : "Summaries are turned off in Settings."}</p>;
  return <div className="contents-panel-quiet">
    <p>{state.state === "failed" ? `The summary could not be written${state.error ? `: ${state.error}` : "."}` : "No summary yet."}</p>
    <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={asking} onClick={() => void write()}>{state.state === "failed" ? "Try again" : "Write a summary"}</button>
  </div>;
}

/** Several sources chosen with ⌘ or Shift, or their boxes: move them together. */
export function SelectionPanel({ sources, placedCount, onMove, onTakeOut, onClear }: { sources: ContentsSource[]; placedCount: number; onMove: () => void; onTakeOut: () => void; onClear: () => void }) {
  return <div className="contents-panel-body" role="region" aria-label="Selected sources">
    <PanelHead eyebrow={`${sources.length.toLocaleString()} selected`} onClose={onClear} closeLabel="Clear the selection" />
    <h2 className="contents-panel-title is-static">{describeSources(sources)}</h2>
    <div className="contents-panel-grid">
      <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" onClick={onMove}><FolderInput size={13} />Move to…</button>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={placedCount === 0} title="Take out of section" onClick={onTakeOut}><FolderOutput size={13} />Take out</button>
    </div>
    <ul className="contents-panel-list is-plain">{sources.map((source) => {
      const Icon = kindIcon[source.kind];
      return <li key={source.id} className="contents-panel-source is-static"><span className={`gx-kind-icon kind-${source.kind}`}><Icon size={13} /></span><span><strong>{source.title}</strong></span></li>;
    })}</ul>
    <p className="contents-panel-hint">Shift-click selects a run of rows; ⌘-click adds or removes one. You can also drag them together.</p>
  </div>;
}

