import type { Artifact, StudioMemo, StudioReview } from "@gunther/contracts";
import { Check, CircleAlert, CircleDashed, LoaderCircle } from "lucide-react";
import type { LiveOutputState } from "./liveOutput";
import { qualityReport, spoken } from "./studioReport";

/** The critic's verdict in one line. */
export function criticLine(review: StudioReview): string {
  const ready = review.verdict.trim().toLowerCase() === "ready" || review.problems.length === 0;
  if (ready) return review.round && review.round > 1 ? `The critic accepted the storyline after ${review.round} rounds.` : "The critic accepted the storyline.";
  return `The critic asked for ${review.problems.length === 1 ? "1 change" : `${review.problems.length} changes`}${review.round ? ` (round ${review.round})` : ""}.`;
}

/** Source numbers as buttons when the version can show them, otherwise as plain [n]. */
function Refs({ refs, map, onCite }: { refs: number[]; map?: Record<string, number> | null | undefined; onCite?: ((index: number) => void) | undefined }) {
  // Without the version's own numbering the pool's numbers would mislead: show none.
  if (refs.length === 0 || !map) return null;
  const numbers = refs.flatMap((ref) => (map[String(ref)] === undefined ? [] : [map[String(ref)]!]));
  if (numbers.length === 0 || !onCite) return null;
  return <span className="studio-refs">{numbers.map((n) => <button type="button" key={n} className="studio-ref" aria-label={`Source ${n}`} onClick={() => onCite(n - 1)}>[{n}]</button>)}</span>;
}

/** The answer and the insights behind it, with where each comes from. */
export function MemoView({ memo, map, onCite }: { memo: StudioMemo; map?: Record<string, number> | null; onCite?: (index: number) => void }) {
  return <div className="studio-memo">
    <p className="studio-answer">{memo.answer}</p>
    {memo.insights.length > 0 && <ol className="studio-insights" aria-label="Insights">
      {memo.insights.map((item, index) => <li key={index}>
        <strong>{item.claim}</strong>
        {item.so_what && <small>{item.so_what}</small>}
        <Refs refs={item.refs} map={map} onCite={onCite} />
      </li>)}
    </ol>}
  </div>;
}

/** While a studio artifact is made: the steps with their time, the memo, the critic and the planned titles. */
export function StudioLive({ state }: { state: LiveOutputState }) {
  const critic = state.reviews.at(-1);
  if (!state.memo && !critic && state.stages.length === 0) return null;
  return <section className="studio-live" aria-label="What the studio has worked out">
    {state.stages.length > 0 && <ol className="studio-stages" aria-label="Progress">
      {state.stages.map((stage) => <li key={stage.id} className={`is-${stage.state}`} title={stage.detail ?? undefined}>
        {stage.state === "done" ? <Check size={11} aria-hidden="true" /> : stage.state === "failed" ? <CircleAlert size={11} aria-hidden="true" /> : <LoaderCircle className="spin" size={11} aria-hidden="true" />}
        {stage.label}{stage.seconds !== null && stage.state !== "running" && <small>{spoken(stage.seconds)}</small>}
      </li>)}
    </ol>}
    {state.memo && <MemoView memo={state.memo} />}
    {critic && <p className="studio-critic">{criticLine(critic)}</p>}
    {state.memo && state.sections.length > 0 && <ol className="studio-planned" aria-label="Planned">
      {state.sections.map((section, index) => <li key={index} className={section.state === "done" ? "is-done" : ""}>
        {section.state === "done" ? <Check size={11} aria-hidden="true" /> : section.state === "waiting" ? <CircleDashed size={11} aria-hidden="true" /> : <LoaderCircle className="spin" size={11} aria-hidden="true" />}
        <span>{section.heading || `Part ${index + 1}`}</span>
      </li>)}
    </ol>}
  </section>;
}

/** A saved studio artifact: how it was made, and what the checker still flags. */
export function StudioBrief({ artifact, onCite }: { artifact: Artifact; onCite?: ((index: number) => void) | undefined }) {
  const approach = artifact.provenance.approach!;
  const report = qualityReport(artifact);
  const unit = artifact.kind === "slides" ? "Slide" : "Section";
  const reviews = approach.reviews ?? [];
  const last = reviews.at(-1);
  const tensions = (approach.memo?.tensions ?? []).map((item) => item.issue);
  const gaps = [...new Set([...(approach.gaps ?? []), ...tensions])];
  return <div className="studio-brief">
    {approach.memo ? <MemoView memo={approach.memo} map={approach.refMap ?? null} {...(onCite ? { onCite } : {})} /> : approach.answer ? <p className="studio-answer">{approach.answer}</p> : null}
    {gaps.length > 0 && <div><h4>Where the material is thin or disagrees</h4><ul>{gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></div>}
    {last && <p className="studio-critic">{criticLine(last)}</p>}
    <div className="studio-quality" aria-label="Quality report">
      <h4>Quality report{report.seconds !== null && <small>made in {spoken(report.seconds)}</small>}</h4>
      <p>{report.flagged === 0 ? "The checker found nothing left to flag." : `The checker still flags ${report.flagged} of ${report.rows.length}.`}{report.unchecked > 0 && ` ${report.unchecked} not checked yet.`}</p>
      {report.flagged > 0 && <ul>{report.rows.filter((row) => row.notes.length > 0).map((row) => <li key={row.index}>
        <strong>{unit} {row.index + 1}{row.heading ? ` · ${row.heading}` : ""}</strong>
        <ul>{row.notes.map((note, at) => <li key={at}>{note}</li>)}</ul>
      </li>)}</ul>}
      {report.formatNotes.length > 0 && <><h4>Format notes</h4><ul>{report.formatNotes.map((note, index) => <li key={index}>{note}</li>)}</ul></>}
    </div>
  </div>;
}
