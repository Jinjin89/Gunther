import type { TranscriptUpgradeStatus } from "@gunther/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { knowledgeApi } from "../../api";

const POLL_MS = { running: 5000, waiting: 30000, failed: 30000 } as const;

/** Follows the background "improve this transcript" job of an old recording. Quiet for recordings that need nothing. */
export function useTranscriptUpgrade(recordingId: string | null, onUpgraded?: () => void) {
  const [status, setStatus] = useState<TranscriptUpgradeStatus | null>(null);
  const [tick, setTick] = useState(0);
  const last = useRef<TranscriptUpgradeStatus["state"] | null>(null);
  const upgraded = useRef(onUpgraded);
  upgraded.current = onUpgraded;
  useEffect(() => {
    last.current = null;
    setStatus(null);
    if (!recordingId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const look = async () => {
      let next: TranscriptUpgradeStatus | null = null;
      try { next = await knowledgeApi.transcriptUpgrade(recordingId); } catch { /* the line is optional: say nothing */ }
      if (!alive || !next) return;
      const before = last.current;
      last.current = next.state;
      setStatus(next);
      // A short recording can go from waiting to done between two looks.
      if (before && before !== "none" && before !== "done" && next.state === "done") upgraded.current?.();
      // A failed one is tried again by itself after a while, so keep looking.
      const wait = next.state === "running" || next.state === "waiting" || next.state === "failed" ? POLL_MS[next.state] : null;
      if (wait) timer = setTimeout(() => void look(), wait);
    };
    void look();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [recordingId, tick]);
  const retry = useCallback(async () => {
    if (!recordingId) return;
    try { await knowledgeApi.retryTranscriptUpgrade(recordingId); } catch { /* the next look shows what is true */ }
    setTick((value) => value + 1);
  }, [recordingId]);
  return { status, retry };
}

function waitingText(status: TranscriptUpgradeStatus): string {
  if (status.reason === "model") return "Will improve this transcript once a Recording model is set in Settings → Transcription.";
  if (status.reason === "service") return "Will improve this transcript when the transcription service answers again.";
  if (status.reason === "recording") return "Waiting until the current recording ends.";
  if (status.reason === "queue" && status.ahead > 0) return `Improving this transcript after ${status.ahead} other ${status.ahead === 1 ? "recording" : "recordings"}.`;
  return "Improving this transcript…";
}

export function TranscriptUpgradeLine({ upgrade }: { upgrade: ReturnType<typeof useTranscriptUpgrade> }) {
  const { status, retry } = upgrade;
  if (!status || status.state === "none" || status.state === "done") return null;
  if (status.state === "failed") {
    return <p className="gx-upgrade-line is-failed" role="status">Couldn’t improve this transcript{status.error ? `: ${status.error}` : "."} · <button type="button" onClick={() => void retry()}>Retry</button></p>;
  }
  if (status.state === "running") {
    const total = Math.round(status.totalMs / 60000);
    return <p className="gx-upgrade-line" role="status">{total > 0 ? `Improving this transcript… ${Math.min(Math.round(status.heardMs / 60000), total)} of ${total} min` : "Improving this transcript…"}</p>;
  }
  return <p className="gx-upgrade-line" role="status">{waitingText(status)}</p>;
}
