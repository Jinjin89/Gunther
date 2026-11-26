import type { AgentStep, ConversationContext, OutputSection, ResearchUsed } from "@gunther/contracts";
import { BookOpen, ChevronRight, CircleAlert, ClipboardCheck, Globe2, Library, ListChecks, LoaderCircle, PenLine } from "lucide-react";
import type { Root } from "mdast";
import { useMemo, useRef, type ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import { CitationButton } from "../components/evidence/CitationButton";
import { MarkdownLink, markdownUrlTransform, richComponents } from "../components/markdown/rich/components";
import { richRemarkPlugins } from "../components/markdown/rich/plugins";
import { remarkCitations } from "../components/markdown/rich/remarkCitations";
import type { LiveStep } from "./liveAnswer";
import { BrandMark } from "../design/BrandMark";
import type { LiveAnswerState } from "./liveAnswer";
import { stepResult } from "./stepResult";

const CITE_LINK = /^#cite-(\d+)$/;
const UNSOURCED_LINK = "#unverified";
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

export { withoutMarks } from "../components/evidence/citationMarks";

export function verificationNotice(coverage: OutputSection["coverage"]): string | undefined {
  if (coverage?.audit?.status === "unavailable") return "Source audit unavailable";
  if (coverage?.status === "unavailable" || coverage?.status === "failed") return "Source check unavailable";
  if (coverage?.status === "partial") {
    const unchecked = Math.max(coverage.unchecked, coverage.support?.unchecked ?? 0);
    return unchecked ? `Partially checked · ${unchecked} unchecked` : "Partially checked";
  }
}

// A nonbreaking space starts a paragraph while preserving inline delimiter boundaries.
// Remove it after the citation pass has used the parser's source offsets.
const INLINE_BOUNDARY = "\u00a0";

function remarkInlineMarkdown() {
  return (tree: Root) => {
    const paragraph = tree.children[0];
    const first = paragraph?.type === "paragraph" ? paragraph.children[0] : undefined;
    if (first?.type === "text" && first.value.startsWith(INLINE_BOUNDARY)) first.value = first.value.slice(INLINE_BOUNDARY.length);
  };
}

/** An answer, as Markdown, with its [n] citations as buttons that open the evidence. */
export function AnswerBody({ content, numbers, onCitation, supportNotes, coverage, hideCitations = false, tightCitations = false, inline = false, table }: {
  content: string;
  numbers: number[];
  onCitation?: ((index: number) => void) | undefined;
  supportNotes?: string[] | undefined;
  coverage?: ConversationContext["coverage"];
  hideCitations?: boolean;
  tightCitations?: boolean;
  inline?: boolean;
  table?: Components["table"];
}) {
  const current = useRef({ numbers, onCitation, supportNotes });
  current.current = { numbers, onCitation, supportNotes };
  // Keep the Markdown element types stable when the reader reports focus or scroll changes.
  const components = useMemo<Components>(() => {
    const withTail = (mark: ReactNode, tail: string | undefined, key?: string) =>
      tail ? <span className="gx-citation-tail" key={key}>{mark}{tail}</span> : mark;
    const citation = (number: string, tail?: string, className?: string, note?: string) => {
      const label = `[${number}]`;
      const { onCitation, numbers } = current.current;
      return withTail(<sup key={number}>{onCitation
        ? <CitationButton number={number} index={numbers.indexOf(Number(number))} className={className} note={note} onCite={onCitation}>{label}</CitationButton>
        : <span className={className} title={note}>{label}</span>}</sup>, tail, number);
    };
    const citationRun = (numbers: string, tail: string | undefined) =>
      numbers.split("-").map((number, index, all) => citation(number, index === all.length - 1 ? tail : undefined));
    return {
      ...richComponents,
      ...(table && { table }),
      a({ node, ...props }) {
        if (node?.properties.dataCitation !== true) return <MarkdownLink {...props} />;
        const { href } = props;
        const suffix = node.properties.dataCitationTail;
        const tail = typeof suffix === "string" ? suffix : undefined;
        if (href === UNSOURCED_LINK) return withTail(<sup className="unverified-mark" title="From the model's own knowledge; no source was found for it">unverified</sup>, tail);
        const infer = href ? INFER_LINK.exec(href) : null;
        if (infer?.[1]) return <span className="inference-mark" title="Follows from these sources; they do not state it">inference · from {citationRun(infer[1], tail)}</span>;
        const disputed = href ? DISPUTED_LINK.exec(href) : null;
        if (disputed?.[1]) return <span className="inference-mark disputed-mark" title="Sources disagree with this statement from the model's own knowledge">disputed · by {citationRun(disputed[1], tail)}</span>;
        const partly = href ? PARTLY_LINK.exec(href) : null;
        if (partly?.[1] && partly[2]) {
          const note = current.current.supportNotes?.[Number(partly[2])];
          return citation(partly[1], tail, "is-partly", note ? `Partly supported: ${note}` : "Partly supported");
        }
        const cite = href ? CITE_LINK.exec(href) : null;
        if (cite?.[1]) return citation(cite[1], tail);
        return <MarkdownLink {...props} />;
      },
    };
  }, [table]);
  const notice = verificationNotice(coverage);
  return <div className="answer-markdown">
    <Markdown remarkPlugins={[
      ...richRemarkPlugins,
      [remarkCitations, { numbers, hide: hideCitations, tight: tightCitations }],
      ...(inline ? [remarkInlineMarkdown] : []),
    ]} components={components} urlTransform={markdownUrlTransform}>{inline ? INLINE_BOUNDARY + content : content}</Markdown>
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
      ? <div className="message-body"><AnswerBody content={state.text} numbers={[]} hideCitations /></div>
      : <div className="live-wait"><span><i /><i /><i /></span><small>{state.steps.length ? "Reading what it found…" : "Working out what to look up…"}</small></div>}
  </article>;
}
