import type { Artifact, OutputIssue } from "@gunther/contracts";

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/** What the checker still says about a section, in plain words. Nothing when it found nothing. */
const VERDICT_WORDS: Record<OutputIssue["verdict"], (count: number) => string> = {
  unsupported: (n) => `${plural(n, "claim", "claims")} could not be confirmed in ${n === 1 ? "its" : "their"} sources`,
  unverified: (n) => `${plural(n, "claim comes", "claims come")} from the model's own knowledge, with no source found`,
  unsourced: (n) => `${plural(n, "claim has", "claims have")} no source cited`,
  contradicted: (n) => `${plural(n, "claim was", "claims were")} left out because sources disagree`,
  unchecked: (n) => `${plural(n, "claim was", "claims were")} not checked`,
};

export interface QualityRow {
  index: number;
  heading: string;
  /** What is still flagged, one plain sentence each. Empty when the section is clean. */
  notes: string[];
  checked: boolean;
}

export interface QualityReport {
  rows: QualityRow[];
  /** How many sections have something flagged, and how many were not checked at all. */
  flagged: number;
  unchecked: number;
  /** Format notes the studio left on the artifact (steps it skipped), as the person should read them. */
  formatNotes: string[];
  /** Seconds the whole build took, from its trail; null when not recorded. */
  seconds: number | null;
}

export function qualityReport(artifact: Artifact): QualityReport {
  const rows = artifact.sections.map((section): QualityRow => {
    const counts = new Map<OutputIssue["verdict"], number>();
    for (const issue of section.issues) counts.set(issue.verdict, (counts.get(issue.verdict) ?? 0) + 1);
    const notes = [...counts].map(([verdict, count]) => VERDICT_WORDS[verdict](count));
    if (!section.checked && notes.length === 0) notes.push("Not checked against its sources yet");
    return { index: section.index, heading: section.heading, notes, checked: section.checked };
  });
  const skill = artifact.provenance.skill;
  const trail = skill?.trail ?? [];
  return {
    rows,
    flagged: rows.filter((row) => row.notes.length > 0).length,
    unchecked: rows.filter((row) => !row.checked).length,
    formatNotes: (skill?.skipped ?? []).map((item) => [item.label || item.step, item.reason].filter(Boolean).join(": ")),
    seconds: trail.length ? trail.reduce((sum, item) => sum + (item.seconds || 0), 0) : null,
  };
}

/** "3 min 20 s" / "45 s". */
export function spoken(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min${seconds % 60 ? ` ${seconds % 60} s` : ""}`;
}
