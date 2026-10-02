import type { Artifact } from "@gunther/contracts";
import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

const STRUCTURE_LABEL: Record<string, string> = {
  what: "Its parts, the most important first",
  why: "Causes, the main one first",
  how: "Steps, in order",
  guide: "When and how to use it, and the pitfalls",
  which: "Options compared, then a recommendation",
  change: "How it changed, stage by stage",
  learn: "From the basics up, the answer last",
};
const ROLE_LABEL: Record<string, string> = {
  background: "Background",
  comparison: "Comparison",
  update: "Latest",
  third_party: "Another view",
};

export interface ApproachEdit {
  question: string;
  answer: string;
  purpose: string;
}

/**
 * What the skill decided before it planned: the question it answers, its answer, who it is for,
 * how it unfolds, what the web added and what the material cannot answer. Collapsed to one row;
 * "Edit approach" changes the question, answer or purpose, and a rebuild keeps those words.
 */
export function ApproachPanel({ artifact, busy, onRebuild }: {
  artifact: Artifact;
  busy: boolean;
  onRebuild: (approach: ApproachEdit) => void;
}) {
  const approach = artifact.provenance.approach ?? null;
  const skipped = artifact.provenance.skill?.skipped ?? [];
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ApproachEdit>({ question: "", answer: "", purpose: "" });
  useEffect(() => {
    setDraft({ question: approach?.question ?? "", answer: approach?.answer ?? "", purpose: approach?.purpose ?? "" });
    setEditing(false);
  }, [artifact.id]);

  if (!approach) return null;
  const use = artifact.provenance.use;
  const deck = artifact.kind === "slides"
    ? [use === "read" ? "To be read" : use === "talk" ? "For a talk" : "", approach.pages ? `about ${approach.pages} slides` : ""].filter(Boolean).join(", ")
    : "";
  const field = (key: keyof ApproachEdit, label: string) => <label>
    <span>{label}</span>
    <textarea value={draft[key]} rows={2} maxLength={600} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} />
  </label>;

  return <section className="outputs-outline outputs-approach" aria-label="Approach">
    <header>
      <button type="button" className="outputs-outline-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={14} aria-hidden="true" />
        <strong>Approach</strong>
        <small>{approach.question}</small>
      </button>
      {open && !editing && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy} onClick={() => setEditing(true)}>Edit approach</button>}
    </header>
    {open && !editing && <dl className="outputs-approach-list">
      <dt>Question</dt><dd>{approach.question}</dd>
      <dt>Answer</dt><dd>{approach.answer}</dd>
      <dt>For</dt><dd>{approach.purpose}</dd>
      <dt>Structure</dt><dd>{STRUCTURE_LABEL[approach.structure] ?? approach.structure}{approach.structureReason && <small>{approach.structureReason}</small>}</dd>
      {deck && <><dt>Deck</dt><dd>{deck}</dd></>}
      {approach.supplements.length > 0 && <><dt>From the web</dt><dd><ul>{approach.supplements.map((item, index) => <li key={index}>
        <strong>{item.title}</strong> · {ROLE_LABEL[item.role] ?? item.role}{item.why && <small>{item.why}</small>}
      </li>)}</ul></dd></>}
      {approach.gaps.length > 0 && <><dt>Not in the material</dt><dd><ul>{approach.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></dd></>}
      {skipped.length > 0 && <><dt>Skipped</dt><dd><ul>{skipped.map((item, index) => <li key={index}>{item.label || item.step}<small>{item.reason}</small></li>)}</ul></dd></>}
    </dl>}
    {open && editing && <div className="outputs-approach-edit">
      {field("question", "Question")}
      {field("answer", "Answer")}
      {field("purpose", "For")}
      <footer>
        <small>A rebuild keeps these words and plans the outline again.</small>
        <span>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => { setDraft({ question: approach.question, answer: approach.answer, purpose: approach.purpose }); setEditing(false); }}>Cancel</button>
          <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={busy || !draft.question.trim()} onClick={() => onRebuild({ question: draft.question.trim(), answer: draft.answer.trim(), purpose: draft.purpose.trim() })}>Rebuild with this approach</button>
        </span>
      </footer>
    </div>}
  </section>;
}
