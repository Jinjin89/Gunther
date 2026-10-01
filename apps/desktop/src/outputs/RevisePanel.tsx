import type { Artifact } from "@gunther/contracts";
import { Sparkles } from "lucide-react";
import { useState } from "react";

/**
 * "Ask Gunther to change…": say what should be different, for the whole output or one
 * section or slide. The agents write it again, check it, and save it as the next version.
 */
export function RevisePanel({ artifact, busy, onRevise, onCancel }: {
  artifact: Artifact;
  busy: boolean;
  onRevise: (instruction: string, sectionIndex: number | null) => void;
  onCancel: () => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [part, setPart] = useState<number | null>(null);
  const unit = artifact.kind === "slides" ? "Slide" : "Section";
  return <section className="outputs-revise" aria-label="Ask Gunther to change it">
    <label>
      <span className="outputs-label">Which part</span>
      <select value={part ?? ""} onChange={(event) => setPart(event.target.value === "" ? null : Number(event.target.value))} disabled={busy}>
        <option value="">The whole {artifact.kind === "slides" ? "deck" : "report"}</option>
        {artifact.sections.map((section) => <option key={section.index} value={section.index}>{unit} {section.index + 1}: {section.heading}</option>)}
      </select>
    </label>
    <label>
      <span className="outputs-label">What should change</span>
      <textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="For example: make it shorter, add the 2024 results, explain it for a first-year student" rows={3} maxLength={2000} disabled={busy} autoFocus />
    </label>
    <div>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
      <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={busy || !instruction.trim()} onClick={() => onRevise(instruction.trim(), part)}><Sparkles size={13} />Change it</button>
    </div>
  </section>;
}
