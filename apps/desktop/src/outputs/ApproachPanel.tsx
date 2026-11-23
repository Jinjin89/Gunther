import type { Artifact } from "@gunther/contracts";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { StudioBrief } from "./StudioBrief";

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

/** The saved approach and evidence behind an artifact. */
export function ApproachPanel({ artifact, onCite }: {
  artifact: Artifact;
  /** Opens source n (counting from 0) of the version, for the studio's cited insights. */
  onCite?: (index: number) => void;
}) {
  const approach = artifact.provenance.approach ?? null;
  const skipped = artifact.provenance.skill?.skipped ?? [];
  const [open, setOpen] = useState(false);

  if (!approach) return null;
  if (approach.method === "studio") {
    return <section className="outputs-outline outputs-approach is-studio" aria-label="How it was made">
      <header>
        <button type="button" className="outputs-outline-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          <ChevronRight size={14} aria-hidden="true" />
          <strong>How it was made</strong>
          <small>{approach.answer}</small>
        </button>
      </header>
      {open && <StudioBrief artifact={artifact} {...(onCite ? { onCite } : {})} />}
    </section>;
  }
  const use = artifact.provenance.use;
  const deck = artifact.kind === "slides"
    ? [use === "read" ? "To be read" : use === "talk" ? "For a talk" : "", approach.pages ? `about ${approach.pages} slides` : ""].filter(Boolean).join(", ")
    : "";

  return <section className="outputs-outline outputs-approach" aria-label="Approach">
    <header>
      <button type="button" className="outputs-outline-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={14} aria-hidden="true" />
        <strong>Approach</strong>
        <small>{approach.question}</small>
      </button>
    </header>
    {open && <dl className="outputs-approach-list">
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
  </section>;
}
