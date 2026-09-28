import type { KnowledgeProposal } from "@gunther/contracts";
import { Archive, ArrowRight, Check, LoaderCircle, MessageSquareText, RotateCcw, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import type { KnowledgeBase } from "../atlas";
import { MarkdownView } from "../components/markdown/MarkdownView";
import { comboKeys, useShortcut } from "../shortcuts/shortcuts";
import { DecisionCard, LibraryLink } from "./Decision";
import { AsideSection, DetailsList, formatDate, ItemHeader, ItemLayout, type ItemNavigation } from "./ItemLayout";

export interface SuggestionItemProps {
  proposal: KnowledgeProposal;
  bases: KnowledgeBase[];
  nav: ItemNavigation;
  onResolved: (message: string) => void;
  onOpenBase: (id: string) => void;
  onOpenSession: (baseId: string, sessionId: string, messageId: string | null) => void;
  onNotify: (message: string) => void;
  onTitle: (title: string) => void;
}

export function SuggestionItem({ proposal, bases, nav, onResolved, onOpenBase, onOpenSession, onNotify, onTitle }: SuggestionItemProps) {
  const [working, setWorking] = useState<"accepted" | "held" | "pending" | null>(null);
  const base = bases.find((item) => item.id === proposal.knowledgeBaseId);
  const chapter = base?.chapters.find((item) => item.id === proposal.targetChapterId);
  useEffect(() => onTitle(proposal.title), [onTitle, proposal.title]);

  const decide = async (status: "accepted" | "held" | "pending") => {
    if (working) return;
    setWorking(status);
    try {
      await knowledgeApi.updateProposal(proposal.id, {
        status,
        reason: status === "accepted" ? "Accepted from the suggestion page" : status === "held" ? "Held for later from the suggestion page" : "Returned to review from the suggestion page",
      });
      window.dispatchEvent(new CustomEvent("gunther:inbox-updated"));
      onResolved(status === "accepted" ? `“${proposal.title}” is now trusted knowledge.` : status === "held" ? "Suggestion held for later." : "Suggestion returned for review.");
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "The decision could not be saved.");
    } finally {
      setWorking(null);
    }
  };
  useShortcut("mod+enter", () => void decide("accepted"), { enabled: proposal.status === "pending" && !working, allowInInputs: false });

  const libraryRef = { id: proposal.knowledgeBaseId, title: base?.title ?? "Library" };
  const aside = (
    <>
      {proposal.status === "pending" && (
        <DecisionCard tone="review" status="Waiting for your decision" title="Add to trusted knowledge?" note="Accepting creates a knowledge unit with its evidence. Held suggestions stay in Inbox, out of the way.">
          <div className="gx-decision-row">
            <button type="button" className="gx-btn gx-btn-primary gx-decision-primary" disabled={Boolean(working)} onClick={() => void decide("accepted")}>
              {working === "accepted" ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}<span>Accept</span>
              <span className="gx-keys" aria-hidden="true">{comboKeys("mod+enter").map((key) => <kbd key={key}>{key}</kbd>)}</span>
            </button>
            <button type="button" className="gx-btn gx-btn-quiet" disabled={Boolean(working)} onClick={() => void decide("held")}>{working === "held" ? <LoaderCircle className="spin" size={14} /> : <Archive size={14} />}Hold</button>
          </div>
        </DecisionCard>
      )}
      {proposal.status === "held" && (
        <DecisionCard tone="held" status="Held for later" title="Set aside for now">
          <div className="gx-decision-row">
            <button type="button" className="gx-btn gx-btn-primary" disabled={Boolean(working)} onClick={() => void decide("accepted")}><Check size={14} />Accept</button>
            <button type="button" className="gx-btn gx-btn-quiet" disabled={Boolean(working)} onClick={() => void decide("pending")}><RotateCcw size={14} />Return to review</button>
          </div>
        </DecisionCard>
      )}
      {(proposal.status === "accepted" || proposal.status === "rejected") && (
        <DecisionCard tone="settled" status={proposal.status === "accepted" ? "Accepted" : "Rejected"} title={proposal.status === "accepted" ? "Trusted knowledge" : "Not added"}>
          {proposal.decisionReason && <p className="gx-decision-note">{proposal.decisionReason}</p>}
        </DecisionCard>
      )}
      <AsideSection title="Details">
        <DetailsList rows={[
          { label: "Library", value: <LibraryLink base={libraryRef} known={base} onOpen={onOpenBase} /> },
          chapter ? { label: "Chapter", value: chapter.title } : null,
          { label: "Proposed", value: formatDate(proposal.createdAt, true) },
        ]} />
      </AsideSection>
    </>
  );

  return (
    <ItemLayout
      nav={nav}
      header={<ItemHeader icon={Sparkles} tone="brand" kicker="Suggested knowledge" title={proposal.title} meta={[base?.title ?? null, formatDate(proposal.createdAt)]} />}
      aside={aside}
    >
      <article className="gx-suggestion-card">
        <p className="gx-suggestion-label"><Sparkles size={13} />Proposed knowledge unit</p>
        <MarkdownView source={proposal.content} />
      </article>
      <section className="gx-provenance">
        <h2 className="gx-section-title">Where this came from</h2>
        <button type="button" className="gx-provenance-row" onClick={() => onOpenSession(proposal.knowledgeBaseId, proposal.sessionId, proposal.messageId)}>
          <span className="gx-kind-icon"><MessageSquareText size={15} /></span>
          <span>
            <strong>{proposal.sourceSessionTitle || "Ask conversation"}</strong>
            <small>An answer in {base?.title ?? "this library"}’s Ask, grounded in its sources</small>
          </span>
          <ArrowRight size={14} className="gx-nudge" />
        </button>
      </section>
    </ItemLayout>
  );
}
