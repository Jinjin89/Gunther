import type { TranscribeAgainStatus } from "@gunther/contracts";
import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { knowledgeApi } from "../../api";

const POLL_MS = 2000;
const busy = (status: TranscribeAgainStatus | null) => status?.state === "waiting" || status?.state === "running";

/** The Transcribe again button's state: what the service says, and a refusal to start. */
export function useTranscribeAgain(recordingId: string | null, onDone?: () => void) {
  const [status, setStatus] = useState<TranscribeAgainStatus | null>(null);
  const [refused, setRefused] = useState("");
  const [look, setLook] = useState(0);
  // Seen waiting or running on this page: a later "done" brings the new transcript in.
  const watching = useRef(false);
  const done = useRef(onDone);
  done.current = onDone;

  useEffect(() => {
    setStatus(null);
    setRefused("");
    watching.current = false;
  }, [recordingId]);

  useEffect(() => {
    if (!recordingId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = async () => {
      let next: TranscribeAgainStatus | undefined;
      try { next = await knowledgeApi.transcribeAgainStatus(recordingId); } catch { /* the line is optional: say nothing */ }
      if (!alive || !next) return;
      setStatus(next);
      if (busy(next)) {
        watching.current = true;
        timer = setTimeout(() => void check(), POLL_MS);
      } else if (next.state === "done" && watching.current) {
        watching.current = false;
        done.current?.();
      }
    };
    void check();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [recordingId, look]);

  const start = useCallback(async () => {
    if (!recordingId) return;
    setRefused("");
    try {
      setStatus(await knowledgeApi.transcribeAgain(recordingId));
    } catch (reason) {
      setRefused(reason instanceof Error ? reason.message : "Couldn’t start transcribing again.");
      return;
    }
    watching.current = true;
    setLook((value) => value + 1);
  }, [recordingId]);

  return { status, refused, start };
}

type TranscribeAgain = ReturnType<typeof useTranscribeAgain>;

export function TranscribeAgainButton({ again }: { again: TranscribeAgain }) {
  const running = busy(again.status);
  return (
    <button
      type="button"
      className="gx-btn gx-btn-ghost gx-btn-sm gx-again-button"
      onClick={() => void again.start()}
      disabled={running}
      title="Hear the saved audio again with your current Recording model and replace this transcript. The old one is kept as an earlier version."
    >
      <RefreshCw size={13} className={running ? "is-spinning" : undefined} />
      {running ? "Transcribing…" : "Transcribe again"}
    </button>
  );
}

/** One grey line under the button: how far it is, or why it stopped. */
export function TranscribeAgainLine({ again }: { again: TranscribeAgain }) {
  const { status, refused } = again;
  if (refused) return <p className="gx-again-line is-failed" role="status">{refused}</p>;
  if (status?.state === "failed") return <p className="gx-again-line is-failed" role="status">Couldn’t transcribe again: {status.error ?? "unknown problem"}</p>;
  if (status?.state === "waiting") return <p className="gx-again-line" role="status">Waiting for another recording to finish transcribing…</p>;
  if (status?.state !== "running") return null;
  if (status.totalMs > 0 && status.heardMs >= status.totalMs) return <p className="gx-again-line" role="status">Making paragraphs…</p>;
  const total = Math.max(1, Math.round(status.totalMs / 60000));
  const heard = Math.min(Math.round(status.heardMs / 60000), total);
  return <p className="gx-again-line" role="status">{status.totalMs > 0 ? `Transcribing again… ${heard} of ${total} min heard` : "Transcribing again…"}</p>;
}
