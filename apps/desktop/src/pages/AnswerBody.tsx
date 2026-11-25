import type { AgentStep, ConversationContext, OutputSection, ResearchUsed } from "@gunther/contracts";
import { BookOpen, ChevronRight, CircleAlert, ClipboardCheck, Globe2, Library, ListChecks, LoaderCircle, PenLine } from "lucide-react";
import { useMemo, useRef } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { sourceAssetUrl } from "../api";
import { CitationButton } from "../components/evidence/CitationButton";
import { withoutMarks } from "../components/evidence/citationMarks";
import type { LiveStep } from "./liveAnswer";
import { BrandMark } from "../design/BrandMark";
import type { LiveAnswerState } from "./liveAnswer";
import { stepResult } from "./stepResult";

const CITATION = /\[(\d+)\]/g;
const CITE_LINK = /^#cite-(\d+)$/;
const UNSOURCED = /\[\?\]/g;
const UNSOURCED_LINK = "#unverified";
// What the checker adds: [i:3] is an inference from source 3, [p:3] is partly supported by it, [d:3] is disputed by it.
const INFERENCE_RUN = /\[i:\d+\](?:[ \t]*\[i:\d+\])*/g;
const DISPUTED_RUN = /\[d:\d+\](?:[ \t]*\[d:\d+\])*/g;
const PARTLY = /\[p:(\d+)\]/g;
const INFER_LINK = /^#infer-(\d+(?:-\d+)*)$/;
const DISPUTED_LINK = /^#disputed-(\d+(?:-\d+)*)$/;
const PARTLY_LINK = /^#partly-(\d+)-(\d+)$/;

/** An answer numbers its sources 1, 2, 3 in the order the text first cites them, and lists them the same way. */
export function citationNumbers(citations: unknown[]): number[] {
  return citations.map((_, index) => index + 1);
}

/** A conversational reply with no searches or citations did not need sources. */
export function noSourcesNeeded(context: ConversationContext): boolean {
  if (context.research?.state === "asking") return true;
  if (context.intent) return context.intent === "chat" || context.intent === "followup" || context.intent === "clarify";
  return !(context.steps?.length);
}

export { withoutMarks };

/**
 * `[2]` becomes a link to `#cite-2`, so the Markdown renderer can turn it into a button, and
 * `[?]` (a claim from the model's own knowledge that no source backs) a marker. A run of
 * `[i:n]` becomes one "inference" label, `[d:n]` one "disputed" label; `[p:n]` a hollow number that knows its place (`k`)
 * among the partly supported ones, which is where its note is.
 */
export function withCitationLinks(text: string, numbers: number[]): string {
  let partly = 0;
  return text
    .replace(CITATION, (whole, digits: string) => numbers.includes(Number(digits)) ? `[${digits}](#cite-${digits})` : whole)
    .replace(INFERENCE_RUN, (run) => {
      const found = [...run.matchAll(/\d+/g)].map((m) => Number(m[0])).filter((n) => numbers.includes(n));
      return found.length ? `[inference](#infer-${[...new Set(found)].join("-")})` : run;
    })
    .replace(DISPUTED_RUN, (run) => {
      const found = [...run.matchAll(/\d+/g)].map((m) => Number(m[0])).filter((n) => numbers.includes(n));
      return found.length ? `[disputed](#disputed-${[...new Set(found)].join("-")})` : run;
    })
    .replace(PARTLY, (whole, digits: string) => {
      const k = partly++;
      return numbers.includes(Number(digits)) ? `[${digits}](#partly-${digits}-${k})` : whole;
    })
    .replace(UNSOURCED, `[?](${UNSOURCED_LINK})`);
}

const SAFE_WEB = /^https?:\/\//i;
const RETAINED_IMAGE = /^gunther-asset:([A-Za-z0-9_-]{1,160})$/;

export function verificationNotice(coverage: OutputSection["coverage"]): string | undefined {
  if (coverage?.audit?.status === "unavailable") return "Source audit unavailable";
  if (coverage?.status === "unavailable" || coverage?.status === "failed") return "Source check unavailable";
  if (coverage?.status === "partial") {
    const unchecked = Math.max(coverage.unchecked, coverage.support?.unchecked ?? 0);
    return unchecked ? `Partially checked · ${unchecked} unchecked` : "Partially checked";
  }
}

/** An answer, as Markdown, with its [n] citations as buttons that open the evidence. */
export function AnswerBody({ content, numbers, onCitation, supportNotes, coverage }: { content: string; numbers: number[]; onCitation?: ((index: number) => void) | undefined; supportNotes?: string[] | undefined; coverage?: ConversationContext["coverage"] }) {
  const current = useRef({ numbers, onCitation, supportNotes });
  current.current = { numbers, onCitation, supportNotes };
  // Keep the Markdown element types stable when the reader reports focus or scroll changes.
  const components = useMemo<Components>(() => {
    const citation = (number: string, className?: string, note?: string) => {
      const label = `[${number}]`;
      const { onCitation, numbers } = current.current;
      return <sup key={number}>{onCitation
        ? <CitationButton number={number} index={numbers.indexOf(Number(number))} className={className} note={note} onCite={onCitation}>{label}</CitationButton>
        : <span className={className} title={note}>{label}</span>}</sup>;
    };
    return {
      a({ href, children }) {
        if (href === UNSOURCED_LINK) return <sup className="unverified-mark" title="From the model's own knowledge; no source was found for it">unverified</sup>;
        const infer = href ? INFER_LINK.exec(href) : null;
        if (infer?.[1]) return <span className="inference-mark" title="Follows from these sources; they do not state it">inference · from {infer[1].split("-").map((n) => citation(n))}</span>;
        const disputed = href ? DISPUTED_LINK.exec(href) : null;
        if (disputed?.[1]) return <span className="inference-mark disputed-mark" title="Sources disagree with this statement from the model's own knowledge">disputed · by {disputed[1].split("-").map((n) => citation(n))}</span>;
        const partly = href ? PARTLY_LINK.exec(href) : null;
        if (partly?.[1] && partly[2]) {
          const note = current.current.supportNotes?.[Number(partly[2])];
          return citation(partly[1], "is-partly", note ? `Partly supported: ${note}` : "Partly supported");
        }
        const cite = href ? CITE_LINK.exec(href) : null;
        if (cite?.[1]) return citation(cite[1]);
        if (href && SAFE_WEB.test(href)) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
        return <span>{children}</span>;
      },
      img: ({ src, alt }) => {
        const retained = typeof src === "string" ? RETAINED_IMAGE.exec(src) : null;
        return retained ? <img src={sourceAssetUrl(retained[1]!)} alt={alt ?? ""} loading="lazy" style={{ maxWidth: "100%", height: "auto" }} /> : null;
      },
    };
  }, []);
  const notice = verificationNotice(coverage);
  return <div className="answer-markdown">
    <Markdown remarkPlugins={[remarkGfm]} components={components} urlTransform={(url, key) => ((key === "src" && RETAINED_IMAGE.test(url)) || CITE_LINK.test(url) || INFER_LINK.test(url) || DISPUTED_LINK.test(url) || PARTLY_LINK.test(url) || url === UNSOURCED_LINK || SAFE_WEB.test(url) ? url : "")}>{withCitationLinks(content, numbers)}</Markdown>
    {notice && <p role="status" className="outputs-flag">{notice}</p>}
  </div>;
}

/** "Used: Compare sources": the skill an answer followed, beside the model line. */
export function usedSkill(skill: NonNullable<ConversationContext["skill"]>): string {
  return `Used: ${skill.title}${skill.auto ? " (picked by Ask)" : ""}`;
}

/** What the agent did to answer: each search, with what it found. */
export function AgentSteps({ steps }: { steps: Array<AgentStep | LiveStep> }) {
  if (!steps.length) return null;
  return <ol className="message-steps" aria-label="How this was answered">
    {steps.map((step, index) => {
      const Icon = step.tool === "search_web" ? Globe2 : step.tool === "read_source" ? BookOpen : step.tool === "check" ? ClipboardCheck : step.tool === "revise" ? PenLine : step.tool === "frame" ? ListChecks : Library;
      const running = "running" in step && step.running;
      const result = running ? null : stepResult(step);
      const detail = result === 0 ? "nothing found" : typeof result === "number"
        ? `${result} ${result === 1 ? "result" : "results"}` : result;
      return <li key={index} className={step.error ? "is-error" : ""}>
        {running ? <LoaderCircle size={12} className="spin" /> : step.error ? <CircleAlert size={12} /> : <Icon size={12} />}
        <span>{running ? step.label.replace(/^Searched/, "Searching").replace(/^Read /, "Reading ").replace(/^Revised /, "Revising ") : step.label}</span>
        {detail !== null && <small>{detail}</small>}
      </li>;
    })}
  </ol>;
}

/** "Searches 5/8 · Reads 2/4": how much of a research budget is used. */
export function researchUsage(used: { searches: readonly [number, number]; reads: readonly [number, number] }): string {
  return `Searches ${used.searches[0]}/${used.searches[1]} · Reads ${used.reads[0]}/${used.reads[1]}`;
}

/** What a research answer set out to find, above its steps while it runs. */
function LiveResearch({ research }: { research: NonNullable<LiveAnswerState["research"]> }) {
  const used: ResearchUsed = research.used ?? { searches: [0, research.limits.searches], reads: [0, research.limits.reads] };
  return <section className="research-plan is-live" aria-label="Research plan">
    {research.coreQuestion && <p className="research-core">{research.coreQuestion}</p>}
    <ol>{research.subQuestions.map((item) => <li key={item.id}>{item.text}</li>)}</ol>
    <small>{researchUsage(used)}</small>
  </section>;
}

export function savedResearchPlan(context: ConversationContext) {
  const research = context.research;
  return research && research.state !== "asking" && (research.coreQuestion || context.work?.subQuestions?.length)
    ? research : null;
}

/** A saved research answer's plan, folded away: its core question, sub-questions and what each found. */
export function ResearchPlan({ context }: { context: ConversationContext }) {
  const research = savedResearchPlan(context);
  if (!research) return null;
  const subQuestions = context.work?.subQuestions ?? [];
  const found = (id: string) => (context.work?.findings ?? []).filter((item) => item.serves === id).length;
  return <details className="research-plan" onClick={(event) => event.stopPropagation()}>
    <summary><ChevronRight size={12} />Research plan</summary>
    {research.coreQuestion && <p className="research-core">{research.coreQuestion}</p>}
    <ol>{subQuestions.map((item) => <li key={item.id}>{item.text}{found(item.id) > 0 && <small>{found(item.id)} {found(item.id) === 1 ? "source" : "sources"}</small>}</li>)}</ol>
    <small>{researchUsage({ searches: [research.used.searches, research.limits.searches], reads: [research.used.reads, research.limits.reads] })}</small>
  </details>;
}

/** The answer while it is being written: the searches so far, then the text as it arrives. */
export function LiveAnswer({ state, className = "" }: { state: LiveAnswerState; className?: string }) {
  return <article className={`conversation-message role-assistant is-live ${className}`} aria-live="polite" aria-busy="true">
    <div className="message-author"><span className="assistant-mark"><BrandMark size={14} busy /></span><span>Gunther</span></div>
    {state.research && <LiveResearch research={state.research} />}
    <AgentSteps steps={state.steps} />
    {state.text
      ? <div className="message-body"><AnswerBody content={withoutMarks(state.text)} numbers={[]} /></div>
      : <div className="live-wait"><span><i /><i /><i /></span><small>{state.steps.length ? "Reading what it found…" : "Working out what to look up…"}</small></div>}
  </article>;
}
