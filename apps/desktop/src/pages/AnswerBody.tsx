import type { AgentStep } from "@gunther/contracts";
import { CircleAlert, Globe2, Library, LoaderCircle } from "lucide-react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LiveStep } from "./liveAnswer";
import { BrandMark } from "../design/BrandMark";
import type { LiveAnswerState } from "./liveAnswer";

const CITATION = /\[(\d+)\]/g;
const CITE_LINK = /^#cite-(\d+)$/;

/** `[2]` becomes a link to `#cite-2`, so the Markdown renderer can turn it into a button. */
export function withCitationLinks(text: string, citationCount: number): string {
  return text.replace(CITATION, (whole, digits: string) => Number(digits) >= 1 && Number(digits) <= citationCount ? `[${digits}](#cite-${digits})` : whole);
}

const SAFE_WEB = /^https?:\/\//i;

/** An answer, as Markdown, with its [n] citations as buttons that open the evidence. */
export function AnswerBody({ content, citationCount, onCitation }: { content: string; citationCount: number; onCitation?: ((index: number) => void) | undefined }) {
  const components: Components = {
    a({ href, children }) {
      const cite = href ? CITE_LINK.exec(href) : null;
      if (cite) {
        const label = `[${cite[1]}]`;
        return <sup>{onCitation ? <button type="button" onClick={(event) => { event.stopPropagation(); onCitation(Number(cite[1]) - 1); }} aria-label={`Inspect citation ${cite[1]}`}>{label}</button> : label}</sup>;
      }
      if (href && SAFE_WEB.test(href)) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
      return <span>{children}</span>;
    },
    // Answers are prose; raw images in them would be remote loads.
    img: () => null,
  };
  return <div className="answer-markdown"><Markdown remarkPlugins={[remarkGfm]} components={components} urlTransform={(url) => (CITE_LINK.test(url) || SAFE_WEB.test(url) ? url : "")}>{withCitationLinks(content, citationCount)}</Markdown></div>;
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
      ? <div className="message-body"><AnswerBody content={state.text} citationCount={0} /></div>
      : <div className="live-wait"><span><i /><i /><i /></span><small>{state.steps.length ? "Reading what it found…" : "Working out what to look up…"}</small></div>}
  </article>;
}
