import { ArrowRight, Check, CheckCircle2, GitCompareArrows, Merge, Quote, ShieldAlert, Sparkles, Tags, X } from "lucide-react";
import type { ReviewProposal } from "../prototype";

interface ReviewPageProps {
  proposals: ReviewProposal[];
  onDecision: (id: string, status: "accepted" | "disputed") => void;
}

const proposalIcons = { merge: Merge, qualifier: Tags, contradiction: GitCompareArrows };
const proposalLabels = { merge: "Entity resolution", qualifier: "Scope qualifier", contradiction: "Conflicting knowledge" };

export function ReviewPage({ proposals, onDecision }: ReviewPageProps) {
  const pending = proposals.filter((proposal) => proposal.status === "pending");
  return (
    <div className="page-stack prototype-review-page">
      <section className="library-heading review-heading">
        <div><span className="eyebrow">Human judgment layer</span><h1>Review knowledge changes</h1><p>DeepSeek proposes structured changes. Evidence and impact stay visible before anything becomes trusted.</p></div>
        <div className="review-summary"><span><strong>{pending.length}</strong><small>waiting</small></span><span><strong>6</strong><small>reviewed today</small></span></div>
      </section>

      <section className="review-workspace">
        <aside className="panel review-queue-summary">
          <header><span>Review queue</span><em>{pending.length}</em></header>
          <button className="is-active"><Sparkles size={14} /><span><strong>All proposals</strong><small>{pending.length} need judgment</small></span></button>
          <button><Tags size={14} /><span><strong>Scope and schema</strong><small>1 proposal</small></span></button>
          <button><GitCompareArrows size={14} /><span><strong>Contradictions</strong><small>1 proposal</small></span></button>
          <button><Merge size={14} /><span><strong>Duplicates</strong><small>1 proposal</small></span></button>
          <footer><ShieldAlert size={14} /><span><strong>Nothing changes silently</strong><small>Every accepted proposal creates a revision.</small></span></footer>
        </aside>

        <div className="prototype-review-list">
          {proposals.map((proposal) => {
            const Icon = proposalIcons[proposal.type];
            if (proposal.status !== "pending") {
              return <article className={`panel decided-proposal is-${proposal.status}`} key={proposal.id}><CheckCircle2 size={18} /><span><strong>{proposal.title}</strong><small>{proposal.status === "accepted" ? "Accepted as a new trusted revision" : "Preserved as disputed knowledge"}</small></span></article>;
            }
            return (
              <article className="panel proposal-card" key={proposal.id}>
                <header>
                  <span className={`proposal-type proposal-${proposal.type}`}><Icon size={13} />{proposalLabels[proposal.type]}</span>
                  <span>Proposed by DeepSeek · just now</span>
                </header>
                <div className="proposal-copy"><h2>{proposal.title}</h2><p>{proposal.explanation}</p></div>
                <div className="semantic-diff">
                  <div><span><X size={11} />Current</span><p>{proposal.before}</p></div>
                  <ArrowRight size={18} />
                  <div><span><Check size={11} />Proposed</span><p>{proposal.after}</p></div>
                </div>
                <div className="proposal-evidence"><Quote size={15} /><span><strong>{proposal.evidence}</strong><small>{proposal.source}</small></span><button>Open evidence <ArrowRight size={12} /></button></div>
                <footer><button className="button button-secondary" onClick={() => onDecision(proposal.id, "disputed")}><ShieldAlert size={14} />Keep as disputed</button><button className="button button-primary" onClick={() => onDecision(proposal.id, "accepted")}><Check size={14} />Accept proposal</button></footer>
              </article>
            );
          })}
          {pending.length === 0 && <article className="panel review-complete"><CheckCircle2 size={30} /><h2>Review complete</h2><p>Every proposed change has a recorded decision.</p></article>}
        </div>
      </section>
    </div>
  );
}
