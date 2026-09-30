import type { AgentStep } from "@gunther/contracts";
import { CircleAlert, Globe2, Library, LoaderCircle } from "lucide-react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LiveStep } from "./liveAnswer";
import { BrandMark } from "../design/BrandMark";
import type { LiveAnswerState } from "./liveAnswer";

const CITATION = /\[(\d+)\]/g;
const CITE_LINK = /^#cite-(\d+)$/;
const UNSOURCED = /\[\?\]/g;
const UNSOURCED_LINK = "#unverified";

/**
 * The number each citation goes by in the answer: its place in the conversation's
 * source pool, or, for answers from before the pool, its place in the list.
 */
export function citationNumbers(citations: Array<{ ref?: number | null | undefined }>): number[] {
  return citations.map((citation, index) => citation.ref ?? index + 1);
}

/**
 * `[2]` becomes a link to `#cite-2`, so the Markdown renderer can turn it into a button, and
 * `[?]` (a claim from the model's own knowledge that no source backs) a marker.
 */
export function withCitationLinks(text: string, numbers: number[]): string {
  return text
    .replace(CITATION, (whole, digits: string) => numbers.includes(Number(digits)) ? `[${digits}](#cite-${digits})` : whole)
    .replace(UNSOURCED, `[?](${UNSOURCED_LINK})`);
}

const SAFE_WEB = /^https?:\/\//i;

/** An answer, as Markdown, with its [n] citations as buttons that open the evidence. */
export function AnswerBody({ content, numbers, onCitation }: { content: string; numbers: number[]; onCitation?: ((index: number) => void) | undefined }) {
  const components: Components = {
    a({ href, children }) {
      if (href === UNSOURCED_LINK) return <sup className="unverified-mark" title="From the model's own knowledge; no source was found for it">unverified</sup>;
      const cite = href ? CITE_LINK.exec(href) : null;
      if (cite) {
        const label = `[${cite[1]}]`;
        return <sup>{onCitation ? <button type="button" onClick={(event) => { event.stopPropagation(); onCitation(numbers.indexOf(Number(cite[1]))); }} aria-label={`Inspect citation ${cite[1]}`}>{label}</button> : label}</sup>;
      }
      if (href && SAFE_WEB.test(href)) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
      return <span>{children}</span>;
    },
    // Answers are prose; raw images in them would be remote loads.
    img: () => null,
  };
  return <div className="answer-markdown"><Markdown remarkPlugins={[remarkGfm]} components={components} urlTransform={(url) => (CITE_LINK.test(url) || url === UNSOURCED_LINK || SAFE_WEB.test(url) ? url : "")}>{withCitationLinks(content, numbers)}</Markdown></div>;
}

/** What the agent did to answer: each search, with what it found. */
export function AgentSteps({ steps }: { steps: Array<AgentStep | LiveStep> }) {
  if (!steps.length) return null;
  return <ol className="message-steps" aria-label="How this was answered">
    {steps.map((step, index) => {
      const Icon = step.tool === "search_web" ? Globe2 : Library;
      const running = "running" in step && step.running;
      return <li key={index} className={step.error ? "is-error" : ""}>
        {running ? <LoaderCircle size={12} className="spin" /> : step.error ? <CircleAlert size={12} /> : <Icon size={12} />}
        <span>{running ? step.label.replace(/^Searched/, "Searching") : step.label}</span>
        {!running && <small>{step.error ?? (step.found ? `${step.found} ${step.found === 1 ? "result" : "results"}` : "nothing found")}</small>}
      </li>;
    })}
  </ol>;
}

/** The answer while it is being written: the searches so far, then the text as it arrives. */
export function LiveAnswer({ state, className = "" }: { state: LiveAnswerState; className?: string }) {
  return <article className={`conversation-message role-assistant is-live ${className}`} aria-live="polite" aria-busy="true">
    <div className="message-author"><span className="assistant-mark"><BrandMark size={14} busy /></span><span>Gunther</span></div>
    <AgentSteps steps={state.steps} />
    {state.text
      ? <div className="message-body"><AnswerBody content={state.text} numbers={[]} /></div>
      : <div className="live-wait"><span><i /><i /><i /></span><small>{state.steps.length ? "Reading what it found…" : "Working out what to look up…"}</small></div>}
  </article>;
}
