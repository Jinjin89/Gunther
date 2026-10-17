import type { ArtifactBriefState, ArtifactLine, ArtifactMark, EditArtifactBriefRequest } from "@gunther/contracts";
import { useState } from "react";
import { Line } from "../pages/ConversationBrief";
import { STRUCTURE_LABEL } from "./ApproachPanel";

const MARK_WORDS: Record<ArtifactMark, string> = { you: "you said", edited: "edited", guessed: "guessed" };
type Part = "goal" | "audience" | "constraint" | "open";
const PART_LABEL: Record<Part, string> = { goal: "Goal", audience: "Audience", constraint: "Constraint", open: "Open question" };

/** Whose a line is: said by the person, typed in this panel, or the agent's guess. Words, not colour. */
function Mark({ line }: { line: ArtifactLine }) {
  return <small className={`brief-mark is-${line.mark}`} title={line.mark === "guessed" ? "The agent's guess: say so and it will change" : "Kept as you wrote it"}>{MARK_WORDS[line.mark]}</small>;
}

/**
 * What the conversation is for, and what the artifact being worked on should be: the goal, the
 * audience, the constraints and what is still open, then the artifact's use, length and
 * structure. Each line says whose it is. Every line can be changed here; what is typed here is
 * the person's, and the agent does not change it by guessing. Locked while the agent works on a
 * message, so that its turn does not overwrite what is typed.
 */
export function ArtifactBriefPanel({ state, disabled, error, onEdit }: {
  state: ArtifactBriefState | null;
  disabled: boolean;
  error?: string | null;
  onEdit: (change: EditArtifactBriefRequest) => void;
}) {
  const [part, setPart] = useState<Part>("constraint");
  const [adding, setAdding] = useState("");
  if (!state) return null;
  const { brief, spec } = state;
  const filled = Boolean(brief.goal || brief.audience || brief.constraints.length || brief.open.length || spec);
  if (!filled && !error) return null;
  const texts = (lines: ArtifactLine[]) => lines.map((line) => line.text);
  const without = (lines: ArtifactLine[], at: number) => texts(lines).filter((_, index) => index !== at);
  const replace = (lines: ArtifactLine[], at: number, next: string) => texts(lines).map((text, index) => index === at ? next : text);
  const add = () => {
    const text = adding.trim();
    if (!text) return;
    setAdding("");
    if (part === "goal") onEdit({ goal: text });
    else if (part === "audience") onEdit({ audience: text });
    else if (part === "constraint") onEdit({ constraints: [...texts(brief.constraints), text] });
    else onEdit({ open: [...texts(brief.open), text] });
  };
  const single = (key: "goal" | "audience", label: string, line: ArtifactLine | null) => line && <div className="brief-part"><small>{label}</small>
    <Line text={line.text} label={label.toLowerCase()} readOnly={disabled} onSave={(text) => onEdit({ [key]: text })} onRemove={() => onEdit({ [key]: "" })}><Mark line={line} /></Line></div>;
  const list = (key: "constraints" | "open", label: string, lines: ArtifactLine[], noun: string) => lines.length > 0 && <div className="brief-part"><small>{label}</small>
    <ul>{lines.map((line, at) => <li key={`${at}-${line.text}`}>
      <Line text={line.text} label={noun} readOnly={disabled} onSave={(text) => onEdit({ [key]: replace(lines, at, text) })} onRemove={() => onEdit({ [key]: without(lines, at) })}><Mark line={line} /></Line>
    </li>)}</ul></div>;
  return <section className="brief-section artifact-brief" aria-label="Brief">
    <header><span className="section-label">Brief</span>{disabled && <small role="status">Locked while the agent works</small>}</header>
    {error && <p className="brief-error" role="alert">{error}</p>}
    {single("goal", "Goal", brief.goal)}
    {single("audience", "Audience", brief.audience)}
    {list("constraints", "Constraints", brief.constraints, "constraint")}
    {list("open", "Open", brief.open, "open question")}
    {spec && <div className="brief-part artifact-spec" role="group" aria-label={spec.kind === "slides" ? "This deck" : "This report"}>
      <small>{spec.kind === "slides" ? "This deck" : "This report"}</small>
      <ul>
        {spec.kind === "slides" && <li className="brief-row"><span className="brief-key">Use</span>
          <select aria-label="Use" value={spec.use?.text ?? ""} disabled={disabled} onChange={(event) => onEdit({ spec: { use: event.target.value } })}>
            <option value="">Not decided</option><option value="talk">A talk</option><option value="read">To be read</option>
          </select>{spec.use && <Mark line={spec.use} />}</li>}
        <li className="brief-row"><span className="brief-key">Length</span>
          {spec.length
            ? <Line text={spec.length.text} label="length" readOnly={disabled} onSave={(text) => onEdit({ spec: { length: text } })} onRemove={() => onEdit({ spec: { length: "" } })}><Mark line={spec.length} /></Line>
            : <LengthAdd disabled={disabled} onSave={(text) => onEdit({ spec: { length: text } })} />}</li>
        {spec.kind === "report" && <li className="brief-row"><span className="brief-key">Structure</span>
          <select aria-label="Structure" value={spec.structure?.text ?? ""} disabled={disabled} onChange={(event) => onEdit({ spec: { structure: event.target.value } })}>
            <option value="">Not decided</option>{Object.entries(STRUCTURE_LABEL).map(([id, words]) => <option key={id} value={id}>{words}</option>)}
          </select>{spec.structure && <Mark line={spec.structure} />}</li>}
      </ul>
    </div>}
    {!disabled && <form className="brief-add" onSubmit={(event) => { event.preventDefault(); add(); }}>
      <select aria-label="Brief part" value={part} onChange={(event) => setPart(event.target.value as Part)}>{(Object.keys(PART_LABEL) as Part[]).map((key) => <option key={key} value={key}>{PART_LABEL[key]}</option>)}</select>
      <input aria-label="Add to the brief" placeholder="Add a line" maxLength={300} value={adding} onChange={(event) => setAdding(event.target.value)} />
    </form>}
  </section>;
}

/** A length not yet set: "10 minutes", "about 12 slides", "3 pages". */
function LengthAdd({ disabled, onSave }: { disabled: boolean; onSave: (text: string) => void }) {
  const [value, setValue] = useState("");
  return <input className="brief-input" aria-label="Length" placeholder="e.g. 10 minutes" disabled={disabled} maxLength={300} value={value} onChange={(event) => setValue(event.target.value)}
    onKeyDown={(event) => { if (event.key === "Enter" && value.trim()) { event.preventDefault(); onSave(value.trim()); setValue(""); } }} />;
}
