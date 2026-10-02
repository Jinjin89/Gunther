import type { AgentStep, ConversationContext, ResearchUsed } from "@gunther/contracts";
import { BookOpen, ChevronRight, CircleAlert, ClipboardCheck, Globe2, Library, ListChecks, LoaderCircle, PenLine } from "lucide-react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LiveStep } from "./liveAnswer";
import { BrandMark } from "../design/BrandMark";
import type { LiveAnswerState } from "./liveAnswer";

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

/** While an answer is written its numbers are not final, so they and the unverified marks wait for the finished text. */
export function withoutMarks(text: string): string {
  return text.replace(/\s?\[(?:(?:[ipd]:)?\d+(?:\s*[,，、]\s*\d+)*|\?)\]/g, "");
}

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

/** An answer, as Markdown, with its [n] citations as buttons that open the evidence. */
export function AnswerBody({ content, numbers, onCitation, supportNotes }: { content: string; numbers: number[]; onCitation?: ((index: number) => void) | undefined; supportNotes?: string[] | undefined }) {
  const citation = (number: string, className?: string, title?: string) => {
    const label = `[${number}]`;
    return <sup key={number}>{onCitation ? <button type="button" className={className} title={title} onClick={(event) => { event.stopPropagation(); onCitation(numbers.indexOf(Number(number))); }} aria-label={`Inspect citation ${number}`}>{label}</button> : <span className={className} title={title}>{label}</span>}</sup>;
  };
  const components: Components = {
    a({ href, children }) {
      if (href === UNSOURCED_LINK) return <sup className="unverified-mark" title="From the model's own knowledge; no source was found for it">unverified</sup>;
      const infer = href ? INFER_LINK.exec(href) : null;
      if (infer?.[1]) return <span className="inference-mark" title="Follows from these sources; they do not state it">inference · from {infer[1].split("-").map((n) => citation(n))}</span>;
      const disputed = href ? DISPUTED_LINK.exec(href) : null;
      if (disputed?.[1]) return <span className="inference-mark disputed-mark" title="Sources disagree with this statement from the model's own knowledge">disputed · by {disputed[1].split("-").map((n) => citation(n))}</span>;
      const partly = href ? PARTLY_LINK.exec(href) : null;
      if (partly?.[1] && partly[2]) {
        const note = supportNotes?.[Number(partly[2])];
        return citation(partly[1], "is-partly", note ? `Partly supported: ${note}` : "Partly supported");
      }
      const cite = href ? CITE_LINK.exec(href) : null;
      if (cite?.[1]) return citation(cite[1]);
      if (href && SAFE_WEB.test(href)) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
      return <span>{children}</span>;
    },
    // Answers are prose; raw images in them would be remote loads.
    img: () => null,
  };
  return <div className="answer-markdown"><Markdown remarkPlugins={[remarkGfm]} components={components} urlTransform={(url) => (CITE_LINK.test(url) || INFER_LINK.test(url) || DISPUTED_LINK.test(url) || PARTLY_LINK.test(url) || url === UNSOURCED_LINK || SAFE_WEB.test(url) ? url : "")}>{withCitationLinks(content, numbers)}</Markdown></div>;
}

/** "Used: Compare sources": the skill an answer followed, beside the model line. */
export function usedSkill(skill: NonNullable<ConversationContext["skill"]>): string {
  return `Used: ${skill.title}${skill.auto ? " (picked by Ask)" : ""}`;
}

/** Steps that are not searches: they have no results to count. */
const METHOD_STEPS = ["check", "revise", "frame"];

/** What the agent did to answer: each search, with what it found. */
export function AgentSteps({ steps }: { steps: Array<AgentStep | LiveStep> }) {
  if (!steps.length) return null;
  return <ol className="message-steps" aria-label="How this was answered">
    {steps.map((step, index) => {
      const Icon = step.tool === "search_web" ? Globe2 : step.tool === "read_source" ? BookOpen : step.tool === "check" ? ClipboardCheck : step.tool === "revise" ? PenLine : step.tool === "frame" ? ListChecks : Library;
      const running = "running" in step && step.running;
      return <li key={index} className={step.error ? "is-error" : ""}>
        {running ? <LoaderCircle size={12} className="spin" /> : step.error ? <CircleAlert size={12} /> : <Icon size={12} />}
        <span>{running ? step.label.replace(/^Searched/, "Searching").replace(/^Read /, "Reading ").replace(/^Revised /, "Revising ") : step.label}</span>
        {!running && (step.error || !METHOD_STEPS.includes(step.tool)) && <small>{step.error ?? (step.found ? `${step.found} ${step.found === 1 ? "result" : "results"}` : "nothing found")}</small>}
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

/** A saved research answer's plan, folded away: its core question, sub-questions and what each found. */
export function ResearchPlan({ context }: { context: ConversationContext }) {
  const research = context.research;
  // Questions back to the user have no plan yet.
  if (!research || research.state === "asking") return null;
  const subQuestions = context.work?.subQuestions ?? [];
  if (!research.coreQuestion && !subQuestions.length) return null;
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
