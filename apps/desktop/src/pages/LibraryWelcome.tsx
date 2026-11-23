import type { LibraryOverview } from "@gunther/contracts";
import { useEffect, useRef, useState } from "react";
import { knowledgeApi } from "../api";
import type { KnowledgeBase } from "../atlas";
import { openSettings } from "../components/SettingsLayout";
import { MODELS_CHANGED_EVENT } from "../models/askModel";

const POLL_MS = 2_500;

export function LibraryWelcome({ base, visible, canAsk, onAsk }: {
  base: KnowledgeBase;
  visible: boolean;
  canAsk: boolean;
  onAsk: (question: string) => void;
}) {
  const { id, sourceCount } = base;
  const [overview, setOverview] = useState<LibraryOverview | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState({ retry: false });
  const lastRequest = useRef<typeof refresh | null>(null);

  useEffect(() => {
    if (!visible) return;
    setOverview(null);
    setError("");
    if (sourceCount === 0) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async (retry: boolean) => {
      try {
        const result = await knowledgeApi.libraryOverview(id, retry, controller.signal);
        if (controller.signal.aborted) return;
        setOverview(result);
        if (result.state === "writing") timer = setTimeout(() => void read(false), POLL_MS);
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "The library overview could not be loaded.");
        }
      }
    };
    const reload = () => setRefresh({ retry: false });
    window.addEventListener(MODELS_CHANGED_EVENT, reload);
    window.addEventListener("gunther:sources-updated", reload);
    const retry = refresh.retry && lastRequest.current !== refresh;
    lastRequest.current = refresh;
    void read(retry);
    return () => {
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener(MODELS_CHANGED_EVENT, reload);
      window.removeEventListener("gunther:sources-updated", reload);
    };
  }, [id, refresh, sourceCount, visible]);

  const ready = overview?.state === "ready" && overview.summary;
  const problem = error || (overview?.state === "failed"
    ? overview.error || "The library overview could not be written."
    : "");
  return <div className="chat-welcome">
    <h1>{base.title}</h1>
    {ready ? <>
      <p>{overview.summary}</p>
      {overview.suggestedQuestions.length > 0 && <ul className="chat-welcome-questions" aria-label="Suggested questions">
        {overview.suggestedQuestions.map((question) => <li key={question}>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={!canAsk} onClick={() => onAsk(question)}>
            {question}
          </button>
        </li>)}
      </ul>}
    </> : <>
      <p>{base.question}</p>
      {base.description && <p>{base.description}</p>}
      <small>{sourceCount.toLocaleString()} {sourceCount === 1 ? "source" : "sources"}</small>
      {overview?.state === "writing" && !problem && <small role="status">Writing library overview…</small>}
      {overview?.state === "needs_model" && <small>
        The library overview needs a model.{" "}
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => openSettings("models")}>Open Models settings</button>
      </small>}
      {problem && <small role="alert">
        {problem}{" "}
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={() => setRefresh({ retry: true })}>Retry</button>
      </small>}
    </>}
  </div>;
}
