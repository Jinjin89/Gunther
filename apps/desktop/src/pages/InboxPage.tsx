import type { InboxItem } from "@gunther/contracts";
import {
  Archive,
  ArrowRight,
  BookOpen,
  Camera,
  Check,
  ChevronRight,
  CircleAlert,
  FileText,
  Inbox,
  Link2,
  LoaderCircle,
  Mic,
  NotebookPen,
  Plus,
  RotateCcw,
  Sparkles,
  Table2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { knowledgeApi } from "../api";
import type { KnowledgeBase } from "../atlas";
import { LibraryGlyph } from "../design/LibraryGlyph";

type InboxFilter = "all" | "unfiled" | "needs_review" | "held";

interface InboxPageProps {
  bases: KnowledgeBase[];
  onOpenBase: (id: string) => void;
  onOpenNote: (id: string) => void;
  onCapture: () => void;
  onCountChange?: (count: number) => void;
  onNotify: (message: string) => void;
}

const itemIcon = (item: InboxItem) => {
  if (item.itemType === "quick_note") return NotebookPen;
  if (item.itemType === "knowledge_suggestion") return Sparkles;
  if (item.sourceKind === "recording" || item.sourceKind === "course") return Mic;
  if (item.sourceKind === "link") return Link2;
  if (item.sourceKind === "image") return Camera;
  if (item.sourceKind === "table") return Table2;
  return FileText;
};

const itemLabel = (item: InboxItem) => {
  if (item.itemType === "quick_note") return "Quick note";
  if (item.itemType === "knowledge_suggestion") return "Knowledge suggestion";
  return ({ note: "Note", paper: "Paper", link: "Web page", file: "Document", image: "Photo or scan", table: "Dataset", recording: "Recording", course: "Course" } as const)[item.sourceKind ?? "file"];
};

const stateCopy = (item: InboxItem) => item.state === "unfiled"
  ? { label: "Choose a home", description: "The original is preserved. Decide which library it belongs to." }
  : item.state === "needs_review"
    ? { label: "Review suggestion", description: item.itemType === "knowledge_suggestion" ? "This came from a grounded conversation and needs your decision." : `${item.assertionCount} candidate claim${item.assertionCount === 1 ? "" : "s"} need your review.` }
    : { label: "Held for later", description: "Preserved without becoming trusted knowledge." };

export function InboxPageV3({ bases, onOpenBase, onOpenNote, onCapture, onCountChange, onNotify }: InboxPageProps) {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [filter, setFilter] = useState<InboxFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [targets, setTargets] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    try {
      const next = await knowledgeApi.inbox();
      setItems(next);
      setError(null);
      onCountChange?.(next.filter((item) => item.state !== "held").length);
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
    all: items.filter((item) => item.state !== "held").length,
    unfiled: items.filter((item) => item.state === "unfiled").length,
    needs_review: items.filter((item) => item.state === "needs_review").length,
    held: items.filter((item) => item.state === "held").length,
  }), [items]);
  const visible = items.filter((item) => filter === "all" ? item.state !== "held" : item.state === filter);

  const selectedTarget = (item: InboxItem) => targets[item.id] ?? bases[0]?.id ?? "";
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

  const reviewProposal = async (item: InboxItem, status: "accepted" | "held" | "pending") => {
    if (!item.proposalId) return;
    setWorkingId(item.id);
    try {
      await knowledgeApi.updateProposal(item.proposalId, {
        status,
        reason: status === "accepted" ? "Accepted from unified Inbox" : status === "held" ? "Held for later from unified Inbox" : "Returned to unified Inbox",
      });
      await refresh();
      onNotify(status === "accepted" ? "Suggestion added to trusted knowledge." : status === "held" ? "Suggestion held for later." : "Suggestion returned for review.");
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
    { id: "held", label: "Held" },
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

    {!loading && !error && visible.length > 0 && <div className="gx-inbox-list">{visible.map((item, index) => {
      const Icon = itemIcon(item);
      const copy = stateCopy(item);
      const isWorking = workingId === item.id;
      return <article key={`${item.itemType}-${item.id}`} className={`gx-inbox-item state-${item.state} ${isWorking ? "is-working" : ""}`} style={{ "--i": index } as CSSProperties}>
        <div className="gx-inbox-icon"><Icon size={16} /></div>
        <div className="gx-inbox-body">
          <header>
            <span className="gx-inbox-kind">{itemLabel(item)}</span>
            <time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</time>
            {item.knowledgeBases.map((base) => {
              const known = bases.find((candidate) => candidate.id === base.id);
              return <button type="button" key={base.id} className="gx-library-pill" onClick={() => onOpenBase(base.id)}>{known && <LibraryGlyph base={known} size="xs" />}{base.title}<ChevronRight size={11} /></button>;
            })}
          </header>
          <h2>{item.title}</h2>
          <p>{item.preview || "No preview yet. The original item remains preserved."}</p>
          <div className="gx-inbox-state"><i /><strong>{copy.label}</strong><span>{copy.description}</span></div>
        </div>
        <footer>
          {item.state === "unfiled" && <>
            {item.itemType === "quick_note" && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => item.noteId && onOpenNote(item.noteId)}><NotebookPen size={13} />Edit note</button>}
            <span className="gx-footer-spacer" />
            <label className="gx-select-label"><span>File to</span><select value={selectedTarget(item)} disabled={isWorking || bases.length === 0} onChange={(event) => setTargets((current) => ({ ...current, [item.id]: event.target.value }))}>{bases.map((base) => <option value={base.id} key={base.id}>{base.title}</option>)}</select></label>
            <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={isWorking || bases.length === 0} onClick={() => void fileItem(item)}>{isWorking ? <LoaderCircle className="spin" size={13} /> : <BookOpen size={13} />}File source</button>
          </>}
          {item.state === "needs_review" && item.itemType === "source" && <>
            {item.knowledgeBases[0] && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => onOpenBase(item.knowledgeBases[0]!.id)}>Review in context <ArrowRight size={12} /></button>}
            <span className="gx-footer-spacer" />
            <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={isWorking} onClick={() => void reviewSource(item, "disputed")}>Dispute</button>
            <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={isWorking || item.assertionCount === 0} onClick={() => void reviewSource(item, "verified")}><Check size={13} />Accept {item.assertionCount || "claims"}</button>
          </>}
          {item.state === "needs_review" && item.itemType === "knowledge_suggestion" && <>
            {item.knowledgeBases[0] && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => onOpenBase(item.knowledgeBases[0]!.id)}>Review in context <ArrowRight size={12} /></button>}
            <span className="gx-footer-spacer" />
            <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={isWorking} onClick={() => void reviewProposal(item, "held")}><Archive size={13} />Hold</button>
            <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={isWorking} onClick={() => void reviewProposal(item, "accepted")}><Check size={13} />Accept suggestion</button>
          </>}
          {item.state === "held" && <><span className="gx-footer-spacer" /><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={isWorking} onClick={() => void reviewProposal(item, "pending")}><RotateCcw size={13} />Return to review</button></>}
        </footer>
      </article>;
    })}</div>}

    {!loading && !error && visible.length === 0 && <div className="gx-empty-state">
      <span className="gx-empty-icon"><Inbox size={20} /></span>
      <h2>{filter === "held" ? "Nothing is held aside." : "You’re all caught up."}</h2>
      <p>{filter === "held" ? "Items you deliberately postpone will stay here." : "New sources can wait here without being forced into a library."}</p>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onCapture}><Plus size={13} />Capture something</button>
    </div>}
  </div>;
}
