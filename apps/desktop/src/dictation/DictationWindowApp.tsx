import { useEffect, useRef, useState } from "react";
import { Square, X } from "lucide-react";
import { useDictation } from "../services/useDictation";
import {
  completeDictation,
  dragDictation,
  listenForDictation,
  pendingDictationRequest,
  stopDictation,
  type DictationEvent,
  type DictationOutcome,
  type DictationRequest,
  type VisibleOutcome,
} from "./bridge";
import "./dictation.css";

type Presentation =
  | { phase: "active" }
  | { phase: "finishing"; text?: string }
  | { phase: "outcome"; outcome: VisibleOutcome };

interface PendingOutcome {
  takeId: number;
  outcome: DictationOutcome;
  inFlight: boolean;
  retriedRevision: number;
}

interface PendingNotice {
  event: DictationEvent;
  inFlight: boolean;
  failed: boolean;
}

export default function DictationWindowApp() {
  const [presentation, setPresentation] = useState<Presentation>({ phase: "active" });
  const [displayedRequest, setDisplayedRequest] = useState<DictationEvent | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);
  const [outcomeReadFailed, setOutcomeReadFailed] = useState(false);
  const alive = useRef(true);
  const lastRequest = useRef<DictationRequest | null>(null);
  const pendingNotice = useRef<PendingNotice | null>(null);
  const captureTakeId = useRef<number | null>(null);
  const pendingOutcome = useRef<PendingOutcome | null>(null);
  const capturedText = useRef("");
  const preview = useRef<HTMLDivElement>(null);
  const latest = useRef<ReturnType<typeof useDictation> | null>(null);

  function retryPendingOutcome(explicitStop = false) {
    const pending = pendingOutcome.current;
    const request = lastRequest.current;
    if (pendingNotice.current || !pending || pending.inFlight || request?.takeId !== pending.takeId
        || (request.action !== "start" && request.action !== "stop")
        || (!explicitStop && pending.retriedRevision >= request.revision)) return;

    pending.retriedRevision = request.revision;
    void submitOutcome(pending);
  }

  async function submitOutcome(pending: PendingOutcome) {
    const isCurrent = () => alive.current && pendingOutcome.current === pending;
    pending.inFlight = true;
    try {
      const result = await completeDictation(pending.takeId, pending.outcome);
      if (!isCurrent()) return;

      if (result.status !== "ignored") {
        pendingOutcome.current = null;
        if (result.request) applyRequest(result.request);
        return;
      }

      // Keep the same payload through reconciliation. A Stop event can arrive while
      // this read is pending, so retry against the latest applied revision.
      const snapshot = await pendingDictationRequest();
      if (!isCurrent()) return;
      applyRequest(snapshot, true);
    } catch {
      if (isCurrent()) setCommandError("无法结束语音输入，请重试停止。");
      return;
    } finally {
      pending.inFlight = false;
    }
    retryPendingOutcome();
  }

  function finish(takeId: number, outcome: DictationOutcome) {
    const pending = { takeId, outcome, inFlight: false, retriedRevision: -1 };
    pendingOutcome.current = pending;
    setCommandError(null);
    setPresentation(outcome.kind === "completed" || outcome.kind === "error"
      ? { phase: "finishing", text: outcome.text }
      : { phase: "finishing" });
    return submitOutcome(pending);
  }

  function finishCapture(outcome: DictationOutcome) {
    const originatingTakeId = captureTakeId.current;
    captureTakeId.current = null;
    if (originatingTakeId !== null && originatingTakeId === lastRequest.current?.takeId) {
      return finish(originatingTakeId, outcome);
    }
  }

  const dictation = useDictation((text) => {
    if (captureTakeId.current !== null) capturedText.current = text;
  }, {
    onComplete: (text, outcome) =>
      finishCapture(outcome === "completed" ? { kind: outcome, text } : { kind: outcome }),
    onError: (message) => finishCapture({ kind: "error", text: capturedText.current, message }),
  });
  latest.current = dictation;

  function cancelCapture() {
    if (captureTakeId.current !== null) {
      captureTakeId.current = null;
      latest.current?.cancel();
    }
  }

  function applyRequest(next: DictationRequest, reconciling = false) {
    if (!alive.current || (pendingNotice.current && next.revision < pendingNotice.current.event.revision)) return;
    const previous = lastRequest.current;
    if (previous && next.revision < previous.revision) return;
    // An ignored retry can leave the same visible outcome authoritative.
    if (next.revision === previous?.revision
        && !(reconciling && (next.action === "idle" || next.action === "outcome"))) return;
    pendingNotice.current = null;
    setOutcomeReadFailed(false);
    lastRequest.current = next;
    const { takeId, revision, action } = next;
    setDisplayedRequest({ takeId, revision, action });
    setCommandError(null);

    switch (next.action) {
      case "start":
        if (previous?.takeId === takeId && previous.action !== "idle") return;
        cancelCapture();
        pendingOutcome.current = null;
        capturedText.current = "";
        setPresentation({ phase: "active" });
        captureTakeId.current = takeId;
        void latest.current?.start();
        break;
      case "stop":
        if (captureTakeId.current === takeId) {
          latest.current?.stop();
        } else if (previous?.takeId === takeId && previous.action !== "idle") {
          retryPendingOutcome();
        } else {
          cancelCapture();
          void finish(takeId, { kind: "cancelled" });
        }
        break;
      case "outcome":
        cancelCapture();
        pendingOutcome.current = null;
        capturedText.current = "";
        setPresentation({ phase: "outcome", outcome: next.outcome });
        break;
      case "idle":
        cancelCapture();
        pendingOutcome.current = null;
        capturedText.current = "";
        setPresentation({ phase: "active" });
        break;
    }
  }

  function fetchOutcomeSnapshot(event: DictationEvent) {
    const notice: PendingNotice = { event, inFlight: true, failed: false };
    pendingNotice.current = notice;
    setOutcomeReadFailed(false);
    setCommandError(null);
    void pendingDictationRequest().then((snapshot) => {
      if (pendingNotice.current === notice) applyRequest(snapshot);
    }).catch(() => {
      if (pendingNotice.current === notice) {
        notice.inFlight = false;
        notice.failed = true;
        setOutcomeReadFailed(true);
        setCommandError("无法读取语音输入结果。");
      }
    });
  }

  function receiveNotification(event: DictationEvent) {
    if (event.action !== "outcome") {
      applyRequest(event);
      return;
    }
    if (event.revision <= (lastRequest.current?.revision ?? -1)) return;
    const currentNotice = pendingNotice.current;
    if (currentNotice) {
      if (event.revision < currentNotice.event.revision) return;
      if (event.revision === currentNotice.event.revision && currentNotice.inFlight) return;
    }

    cancelCapture();
    setDisplayedRequest(event);
    setPresentation((current) => current.phase === "active" ? { phase: "finishing" } : current);
    fetchOutcomeSnapshot(event);
  }

  useEffect(() => {
    alive.current = true;
    document.documentElement.classList.add("dictation-window");
    let disposed = false;
    let unlisten: (() => void) | undefined;

    void (async () => {
      const cleanup = await listenForDictation((event) => {
        if (!disposed) receiveNotification(event);
      });
      if (disposed) {
        cleanup();
        return;
      }
      unlisten = cleanup;
      const snapshot = await pendingDictationRequest();
      if (!disposed) applyRequest(snapshot);
    })().catch(() => {
      if (!disposed && lastRequest.current === null) {
        setCommandError("无法连接语音输入，请重新打开 Gunther 后再试。");
      }
    });

    return () => {
      disposed = true;
      alive.current = false;
      cancelCapture();
      pendingOutcome.current = null;
      pendingNotice.current = null;
      lastRequest.current = null;
      unlisten?.();
      document.documentElement.classList.remove("dictation-window");
    };
  }, []);

  async function stopPanel(request: DictationEvent) {
    const { takeId, revision } = request;
    const dismissing = request.action === "outcome";
    setCommandError(null);
    try {
      await stopDictation(takeId, revision);
      if (alive.current && lastRequest.current?.revision === revision && captureTakeId.current !== takeId) {
        retryPendingOutcome(true);
      }
    } catch {
      if (!alive.current) return;
      const latestRequest = lastRequest.current;
      const latestRevision = Math.max(latestRequest?.revision ?? -1, pendingNotice.current?.event.revision ?? -1);
      const restoredOutcome = dismissing
        && latestRequest?.action === "outcome" && latestRequest.takeId === takeId;
      if (latestRevision !== revision && !restoredOutcome) return;
      if (dismissing) {
        setCommandError("无法收起语音输入窗口，请重试。");
      } else if (captureTakeId.current === takeId) {
        setCommandError("停止失败，语音输入仍在进行。请重试。");
      } else {
        setCommandError("无法停止语音输入，请重试。");
      }
    }
  }

  const active = presentation.phase === "active";
  const listening = active && dictation.state === "listening";
  const starting = active && dictation.state === "starting";
  const finishing = presentation.phase === "finishing" || (active && dictation.state === "finishing");
  const outcome = presentation.phase === "outcome" ? presentation.outcome : null;
  const copyRetryText = outcome?.kind === "copy_failed" ? outcome.text ?? null : null;
  const message = commandError ?? outcome?.message;

  let text = "";
  let placeholder = "正在启动…";
  if (outcome) {
    text = outcome.text ?? "";
    placeholder = outcome.kind === "no_speech" ? "未识别到语音" : "";
  } else if (listening) {
    text = dictation.transcript;
    placeholder = "正在听…";
  } else if (finishing) {
    text = (presentation.phase === "finishing" ? presentation.text : undefined) ?? dictation.transcript;
    placeholder = "正在整理…";
  }

  useEffect(() => {
    if (preview.current) preview.current.scrollTop = preview.current.scrollHeight;
  }, [text]);

  return (
    <main className="dictation-panel" aria-label="Voice input">
      <header onMouseDown={(event) => {
        if (event.button === 0 && !(event.target as Element).closest("button")) {
          void dragDictation().catch(() => undefined);
        }
      }}>
        <span
          className={`dictation-signal${listening ? " is-listening" : starting ? " is-starting" : ""}`}
          aria-hidden
          style={listening ? {
            opacity: 0.45 + dictation.level * 0.55,
            transform: `scale(${1 + dictation.level * 0.35})`,
          } : undefined}
        />
        <span className="dictation-label" role={message ? "alert" : "status"} title={message ?? undefined}>
          {message ?? (finishing ? "正在整理…" : "语音输入")}
        </span>
        {copyRetryText !== null && (
          <button type="button" className="dictation-retry" onClick={() => {
            if (displayedRequest && !pendingNotice.current
                && displayedRequest.revision === lastRequest.current?.revision) {
              void finish(displayedRequest.takeId, { kind: "completed", text: copyRetryText });
            }
          }}>重试复制</button>
        )}
        {outcomeReadFailed && pendingNotice.current && (
          <button type="button" className="dictation-retry" onClick={() => {
            const notice = pendingNotice.current;
            if (notice && !notice.inFlight) fetchOutcomeSnapshot(notice.event);
          }}>重试读取</button>
        )}
        <span className="dictation-key" aria-hidden>⌥ Space</span>
        <button
          type="button"
          className="dictation-stop"
          aria-label={outcome || displayedRequest?.action === "outcome" ? "Dismiss voice input" : "Stop voice input"}
          title={outcome || displayedRequest?.action === "outcome" ? "关闭语音输入" : "停止语音输入 · ⌥Space"}
          onClick={() => { if (displayedRequest) void stopPanel(displayedRequest); }}
        >
          {outcome || displayedRequest?.action === "outcome" ? <X size={13} /> : <Square size={11} fill="currentColor" />}
        </button>
      </header>
      <div
        ref={preview}
        className={`dictation-preview${text ? "" : " is-empty"}`}
        aria-label="Transcribed text"
        aria-live="polite"
      >
        {text || placeholder}
      </div>
    </main>
  );
}
