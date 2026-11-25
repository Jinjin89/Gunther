import type { AgentStep, ContentsSource, ConversationCitation, ConversationContext, SourceKind } from "@gunther/contracts";
import { BookOpen, Check, ChevronRight, ClipboardCheck, FileText, Globe2, Library, ListChecks, PenLine, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { isWebCitation } from "../components/evidence/citationKind";
import { withoutMarks } from "../components/evidence/citationMarks";
import { kindIcon } from "../contents/ContentsRow";
import { SOURCE_KIND_LABEL } from "../items/itemRef";
import { hostOf } from "../items/sourceContent";
import { stepResult } from "./stepResult";
import "./evidencePrimitives.css";

export function sourceKindMeta(kind: SourceKind | "web" | undefined) {
  if (kind === "web") return { icon: Globe2, tone: "link", kindLabel: "Web" } as const;
  if (kind) return { icon: kindIcon[kind], tone: kind, kindLabel: SOURCE_KIND_LABEL[kind] };
  return { icon: FileText, tone: undefined, kindLabel: "Source" };
}

export function evidenceLocator(value: string, url?: string | null) {
  const host = hostOf(url) ?? "";
  const locator = value.trim();
  // These are fallback locators written by the passage readers.
  const placeholder = locator === "Source text" || (host && locator.toLowerCase() === "public web");
  return placeholder ? host : locator || host;
}

export function citationMeta(citation: ConversationCitation, sources: readonly ContentsSource[]) {
  const web = isWebCitation(citation);
  const kind = web ? "web" : sources.find((source) => source.id === citation.sourceId)?.kind;
  return { ...sourceKindMeta(kind), locator: evidenceLocator(citation.locator, web ? citation.url : null) };
}

export function SourceKindMark({ icon: Icon, tone }: Pick<ReturnType<typeof sourceKindMeta>, "icon" | "tone">) {
  return <span className={`gx-kind-icon evidence-kind-mark${tone ? ` kind-${tone}` : ""}`} aria-hidden="true"><Icon size={13} /></span>;
}

type CoverageSegment = { count: number; tone: "verified" | "warning" | "danger" | "neutral" };
type Claim = NonNullable<ConversationContext["coverage"]>["claims"][number];
type ClaimVerdict = { tone: "verified" | "neutral" }
  | { tone: "warning" | "danger"; label: string; tally: string; markCitation?: boolean };
const UNCHECKED = { tone: "warning", label: "Unchecked", tally: "unchecked" } as const;
const CLAIM_VERDICTS = new Map<string, ClaimVerdict>([
  ["supported", { tone: "verified" }], ["nonfactual", { tone: "neutral" }],
  ["partly", { tone: "warning", label: "Partly supported", tally: "partial", markCitation: true }],
  ["inference", { tone: "warning", label: "Inference", tally: "inferred" }],
  ["disputed", { tone: "danger", label: "Disputed", tally: "disputed", markCitation: true }],
  ["not", { tone: "danger", label: "Unsupported", tally: "unsupported" }],
  ["unsourced", { tone: "warning", label: "No source cited", tally: "unsourced" }],
  ["unchecked", UNCHECKED],
]);

export function claimVerdict(verdict: string): ClaimVerdict {
  return CLAIM_VERDICTS.get(verdict) ?? UNCHECKED;
}

export function CoverageMeter({ summary, segments, claims = [] }: {
  summary: string; segments: readonly CoverageSegment[]; claims?: readonly Claim[];
}) {
  const filled = segments.filter((segment) => segment.count > 0);
  const verified = filled.some((segment) => segment.tone === "verified")
    && filled.every((segment) => segment.tone === "verified" || segment.tone === "neutral");
  const flagged = claims.flatMap((claim) => {
    const verdict = claimVerdict(claim.verdict);
    return "label" in verdict ? [{ ...claim, label: verdict.label }] : [];
  });
  const meter = <>
    <span className="evidence-coverage-line">
      {verified && <Check size={14} aria-hidden="true" />}
      <span>{summary}</span>
      {flagged.length > 0 && <ChevronRight size={13} className="evidence-chevron" aria-hidden="true" />}
    </span>
    {filled.length > 0 && <span className="evidence-coverage-track" aria-hidden="true">
      {filled.map(({ tone, count }, index) => <span key={index} className={`is-${tone}`} style={{ flexGrow: count }} />)}
    </span>}
  </>;
  if (!flagged.length) return <div className="evidence-coverage">{meter}</div>;
  return <details className="evidence-coverage">
    <summary>{meter}</summary>
    <ul>{flagged.map(({ id, label, text, reason }) => <li key={id}>
      <strong>{label}</strong> {withoutMarks(text)}{reason && <small>{reason}</small>}
    </li>)}</ul>
  </details>;
}

const STEP_ICONS: ReadonlyMap<string, LucideIcon> = new Map([
  ["search_web", Globe2], ["read_source", BookOpen], ["check", ClipboardCheck],
  ["revise", PenLine], ["frame", ListChecks],
]);

export function StepTimeline({ steps }: { steps: readonly AgentStep[] }) {
  if (!steps.length) return null;
  return <ol className="evidence-timeline" aria-label="How this was answered">
    {steps.map((step, index) => {
      const Icon = STEP_ICONS.get(step.tool) ?? Library;
      const result = stepResult(step);
      return <li key={index}>
        <span className="evidence-timeline-node"><Icon size={11} aria-hidden="true" /></span>
        <span className="evidence-timeline-label">{step.label}
          {step.query && !step.label.includes(step.query) && <small>{step.query}</small>}
        </span>
        {result !== null && <span className={`evidence-timeline-result${step.error ? " is-error" : ""}`}>{result}</span>}
      </li>;
    })}
  </ol>;
}

export function EvidenceDisclosure({ title, count, subtitle, className, children }: {
  title: string; count?: number; subtitle?: string; className?: string; children: ReactNode;
}) {
  return <details className={`evidence-disclosure${className ? ` ${className}` : ""}`}>
    <summary>
      <ChevronRight size={13} className="evidence-chevron" aria-hidden="true" />
      <span>{title}</span>
      {count !== undefined && <span className="evidence-count">{count}</span>}
      {subtitle && <small className="evidence-disclosure-subtitle">{subtitle}</small>}
    </summary>
    <div className="evidence-disclosure-content">{children}</div>
  </details>;
}
