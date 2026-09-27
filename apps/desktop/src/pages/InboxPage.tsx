import type { InboxItem } from "@gunther/contracts";
import {
  Archive,
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  CircleAlert,
  FileText,
  Inbox,
  LoaderCircle,
  Mic2,
  NotebookPen,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { knowledgeApi } from "../api";
import type { KnowledgeBase } from "../atlas";

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
  if (item.sourceKind === "recording" || item.sourceKind === "course") return Mic2;
  return FileText;
};

const itemLabel = (item: InboxItem) => {
  if (item.itemType === "quick_note") return "Quick note";
  if (item.itemType === "knowledge_suggestion") return "Knowledge suggestion";
  return ({ note: "Note", paper: "Paper", link: "Web page", file: "Document", image: "Photo or scan", table: "Dataset", recording: "Recording", course: "Course" } as const)[item.sourceKind ?? "file"];
};

const stateCopy = (item: InboxItem) => item.state === "unfiled"
  ? { label: "Choose a home", description: "The original is preserved. Decide which knowledge base it belongs to." }
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
      onNotify("Create a knowledge base before filing this source, or leave it safely in Inbox.");
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

  return <div className="unified-inbox page-enter">
    <header className="unified-inbox-intro">
      <div><span className="atlas-eyebrow">Inbox</span><h1>Captured now. Organized with intention.</h1><p>Unfiled sources need a home. AI suggestions need a decision. They stay separate, so you always know what you are reviewing.</p></div>
      <button className="primary-button" onClick={onCapture}>Capture something</button>
    </header>

    <nav className="inbox-filter-tabs" aria-label="Inbox filters">
      {(["all", "unfiled", "needs_review", "held"] as InboxFilter[]).map((id) => <button key={id} className={filter === id ? "is-active" : ""} onClick={() => setFilter(id)}><span>{id === "all" ? "Needs attention" : id === "unfiled" ? "To organize" : id === "needs_review" ? "To review" : "Held"}</span><i>{counts[id]}</i></button>)}
    </nav>

    {error && <div className="inbox-service-error"><CircleAlert size={16} /><span><strong>Inbox is temporarily unavailable</strong><small>{error}</small></span><button onClick={() => { setLoading(true); void refresh(); }}>Retry</button></div>}
    {loading && <div className="inbox-loading"><LoaderCircle className="spin" size={18} />Opening your durable Inbox…</div>}

    {!loading && !error && visible.length > 0 && <div className="unified-inbox-list">{visible.map((item) => {
      const Icon = itemIcon(item);
      const copy = stateCopy(item);
      const isWorking = workingId === item.id;
      return <article key={`${item.itemType}-${item.id}`} className={`inbox-card state-${item.state}`}>
        <div className="inbox-card-icon"><Icon size={17} /></div>
        <div className="inbox-card-body">
          <header><span>{itemLabel(item)}</span><i>·</i><small>{new Date(item.updatedAt).toLocaleDateString()}</small>{item.knowledgeBases.map((base) => <button key={base.id} onClick={() => onOpenBase(base.id)}>{base.title}<ChevronRight size={11} /></button>)}</header>
          <h2>{item.title}</h2>
          <p>{item.preview || "No preview yet. The original item remains preserved."}</p>
          <div className="inbox-card-state"><strong>{copy.label}</strong><span>{copy.description}</span></div>
        </div>
        <footer>
          {item.state === "unfiled" && <>
            {item.itemType === "quick_note" && <button className="quiet-button" onClick={() => item.noteId && onOpenNote(item.noteId)}>Edit note</button>}
            <label><span>File to</span><select value={selectedTarget(item)} disabled={isWorking || bases.length === 0} onChange={(event) => setTargets((current) => ({ ...current, [item.id]: event.target.value }))}>{bases.map((base) => <option value={base.id} key={base.id}>{base.title}</option>)}</select></label>
            <button className="primary-button" disabled={isWorking || bases.length === 0} onClick={() => void fileItem(item)}>{isWorking ? <LoaderCircle className="spin" size={13} /> : <BookOpen size={13} />}File source</button>
          </>}
          {item.state === "needs_review" && item.itemType === "source" && <>
            {item.knowledgeBases[0] && <button className="text-button" onClick={() => onOpenBase(item.knowledgeBases[0]!.id)}>Review in context <ArrowRight size={12} /></button>}
            <span />
            <button className="quiet-button" disabled={isWorking} onClick={() => void reviewSource(item, "disputed")}>Dispute</button>
            <button className="primary-button" disabled={isWorking || item.assertionCount === 0} onClick={() => void reviewSource(item, "verified")}><Check size={13} />Accept {item.assertionCount || "claims"}</button>
          </>}
          {item.state === "needs_review" && item.itemType === "knowledge_suggestion" && <>
            {item.knowledgeBases[0] && <button className="text-button" onClick={() => onOpenBase(item.knowledgeBases[0]!.id)}>Review in context <ArrowRight size={12} /></button>}
            <span />
            <button className="quiet-button" disabled={isWorking} onClick={() => void reviewProposal(item, "held")}><Archive size={13} />Hold</button>
            <button className="primary-button" disabled={isWorking} onClick={() => void reviewProposal(item, "accepted")}><Check size={13} />Accept suggestion</button>
          </>}
          {item.state === "held" && <><span /><button className="quiet-button" disabled={isWorking} onClick={() => void reviewProposal(item, "pending")}><RotateCcw size={13} />Return to review</button></>}
        </footer>
      </article>;
    })}</div>}

    {!loading && !error && visible.length === 0 && <div className="unified-inbox-empty"><span><Inbox size={22} /></span><h2>{filter === "held" ? "Nothing is held aside." : "You’re all caught up."}</h2><p>{filter === "held" ? "Items you deliberately postpone will stay here." : "New sources can wait here without being forced into a knowledge base."}</p><button className="quiet-button" onClick={onCapture}>Capture something</button></div>}
  </div>;
}
