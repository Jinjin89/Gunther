import type { SourceDigest, SourceDigestState } from "@gunther/contracts";
import { CircleAlert, LoaderCircle, RefreshCw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { knowledgeApi } from "../../api";

type Profile = SourceDigest["profile"];

/** What each kind of capture is summarized as, and what its sections are called. */
const PROFILE_TEXT: Record<Profile, { heading: string; points: string; actions: string }> = {
  lecture: { heading: "Study notes", points: "Key ideas", actions: "Homework and next steps" },
  meeting: { heading: "Meeting notes", points: "Decisions and key points", actions: "Action items" },
  memo: { heading: "The thought, clearly", points: "Ideas", actions: "To do" },
  image: { heading: "What it shows", points: "Key points", actions: "To do" },
  table: { heading: "About this table", points: "What stands out", actions: "To do" },
  paper: { heading: "Summary", points: "Contributions and findings", actions: "To do" },
  document: { heading: "Summary", points: "Key points", actions: "To do" },
  web: { heading: "Summary", points: "Key points", actions: "To do" },
  note: { heading: "Summary", points: "Key points", actions: "To do" },
};

const POLL_MS = 2_500;
const POLL_LIMIT = 120;

function engineLabel(digest: SourceDigest): string {
  return `Written by ${digest.engine === "openai" ? "OpenAI" : "DeepSeek"}${digest.model ? ` ${digest.model}` : ""}`;
}

/**
 * A capture's summary, above the original. Each key point can open the
 * passages it came from, so it can be checked before it is trusted.
 */
export function DigestCard({ sourceId, onWritten }: { sourceId: string; onWritten?: () => void }) {
  const [status, setStatus] = useState<SourceDigestState | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  // Bumped to follow a summary being written again.
  const [follow, setFollow] = useState(0);
  const polls = useRef(0);
  const wasWaiting = useRef(false);
  const onWrittenRef = useRef(onWritten);
  onWrittenRef.current = onWritten;

  const load = useCallback(async () => {
    const next = await knowledgeApi.sourceDigest(sourceId);
    setStatus(next);
    const waiting = next.state === "reading" || next.state === "writing";
    // A summary may retitle a capture that still had a placeholder name.
    if (wasWaiting.current && next.state === "ready") onWrittenRef.current?.();
    wasWaiting.current = waiting;
    return next;
  }, [sourceId]);

  useEffect(() => {
    setStatus(null);
    setOpen(null);
    wasWaiting.current = false;
  }, [sourceId]);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    polls.current = 0;
    const tick = () => {
      void load().then((next) => {
        if (!active) return;
        if ((next.state === "reading" || next.state === "writing") && polls.current < POLL_LIMIT) {
          polls.current += 1;
          timer = window.setTimeout(tick, POLL_MS);
        }
      }).catch(() => undefined);
    };
    tick();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [follow, load]);

  const writeAgain = async () => {
    setAsking(true);
    setError(null);
    try {
      setStatus(await knowledgeApi.writeSourceDigest(sourceId));
      wasWaiting.current = true;
      setFollow((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The summary could not be written.");
    } finally {
      setAsking(false);
    }
  };

  if (!status || (status.state === "off" && status.offReason !== "no_key")) return null;
  if (status.state === "off") {
    return <section className="gx-digest is-empty" aria-label="Summary">
      <p className="gx-digest-waiting"><Sparkles size={14} />Summaries need an OpenAI or DeepSeek API key. Add one to the local backend (OPENAI_API_KEY or DEEPSEEK_API_KEY).</p>
    </section>;
  }
  const digest = status.digest;
  const waiting = status.state === "reading" || status.state === "writing";

  if (!digest) {
    if (waiting) {
      return <section className="gx-digest is-waiting" aria-label="Summary" aria-busy="true">
        <p className="gx-digest-waiting"><LoaderCircle className="spin" size={14} />{status.state === "reading" ? "Reading it first, then writing a summary…" : "Writing a summary…"}</p>
      </section>;
    }
    const failed = status.state === "failed";
    return <section className={`gx-digest is-empty ${failed ? "is-failed" : ""}`} aria-label="Summary">
      <p className="gx-digest-waiting" role={failed ? "alert" : undefined}>{failed ? <CircleAlert size={14} /> : <Sparkles size={14} />}{failed ? status.error ?? "The model could not write the summary." : "No summary yet."}</p>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={asking} onClick={() => void writeAgain()}>{asking ? <LoaderCircle className="spin" size={13} /> : failed ? <RefreshCw size={13} /> : <Sparkles size={13} />}{failed ? "Try again" : "Summarize"}</button>
      {error && <p className="gx-digest-error" role="alert">{error}</p>}
    </section>;
  }

  const text = PROFILE_TEXT[digest.profile] ?? PROFILE_TEXT.document;
  return <section className="gx-digest" aria-label={text.heading}>
    <header className="gx-digest-head">
      <Sparkles size={14} />
      <h2>{text.heading}</h2>
      <span className="gx-digest-fill" />
      {waiting
        ? <span className="gx-digest-status"><LoaderCircle className="spin" size={12} />Writing again…</span>
        : <button type="button" className="gx-icon-button" disabled={asking} onClick={() => void writeAgain()} title={status.stale ? "The source changed. Write the summary again" : "Write the summary again"} aria-label="Write the summary again"><RefreshCw size={13} /></button>}
    </header>
    <p className="gx-digest-overview">{digest.overview}</p>
    {digest.keyPoints.length > 0 && <>
      <h3>{text.points}</h3>
      <ul className="gx-digest-points">
        {digest.keyPoints.map((point, index) => {
          const id = `${index}`;
          const expanded = open === id;
          return <li key={id}>
            <span>{point.text}</span>
            {point.citations.length > 0 && <button
              type="button"
              className={`gx-digest-cite ${expanded ? "is-open" : ""}`}
              aria-expanded={expanded}
              aria-label={`${expanded ? "Hide" : "Show"} the ${point.citations.length === 1 ? "passage" : "passages"} this comes from`}
              title="Where this comes from"
              onClick={() => setOpen(expanded ? null : id)}
            >{point.citations.map((citation) => citation.number).join(", ")}</button>}
            {expanded && point.citations.map((citation) => <blockquote key={citation.blockId} className="gx-digest-quote">
              <p>{citation.quote}</p>
              {citation.locator && <cite>{citation.locator}</cite>}
            </blockquote>)}
          </li>;
        })}
      </ul>
    </>}
    {digest.actionItems.length > 0 && <>
      <h3>{text.actions}</h3>
      <ul className="gx-digest-actions">{digest.actionItems.map((item) => <li key={item}>{item}</li>)}</ul>
    </>}
    {digest.openQuestions.length > 0 && <>
      <h3>Open questions</h3>
      <ul className="gx-digest-list">{digest.openQuestions.map((item) => <li key={item}>{item}</li>)}</ul>
    </>}
    {digest.terms.length > 0 && <p className="gx-digest-terms">{digest.terms.map((term) => <span key={term}>{term}</span>)}</p>}
    <footer className="gx-digest-foot">
      {engineLabel(digest)} from the saved original{status.stale ? " · the source has changed since" : ""}. The original below is the evidence.
    </footer>
    {(error ?? status.error) && <p className="gx-digest-error" role="alert">{error ?? `Writing it again failed: ${status.error}`}</p>}
  </section>;
}
