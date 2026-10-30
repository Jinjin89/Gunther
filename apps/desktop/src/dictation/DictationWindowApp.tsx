import { useEffect, useRef, useState } from "react";
import { Square } from "lucide-react";
import { useDictation } from "../services/useDictation";
import { copyDictation, dragDictation, finishDictation, listenForDictation, pendingDictationRequest, stopDictation, type DictationRequest } from "./bridge";
import "./dictation.css";

export default function DictationWindowApp() {
  const [copyError, setCopyError] = useState<string | null>(null);
  const alive = useRef(true);
  const seen = useRef(0);
  const running = useRef(0);
  const active = useRef(false);
  const pending = useRef<DictationRequest | null>(null);
  const latest = useRef<ReturnType<typeof useDictation> | null>(null);
  const preview = useRef<HTMLDivElement>(null);

  function begin(request: DictationRequest) {
    if (active.current) { pending.current = request; return; }
    running.current = request.serial;
    active.current = true;
    setCopyError(null);
    void latest.current?.start();
  }
  function ended() {
    const serial = running.current;
    active.current = false;
    void finishDictation(serial).catch(() => undefined);
    const next = pending.current;
    pending.current = null;
    if (next?.listening) begin(next);
  }
  const dictation = useDictation(() => undefined, {
    onComplete: (text) => {
      if (text.trim()) {
        void copyDictation(text).catch(() => {
          if (alive.current) setCopyError("Could not copy. Your text is still available here.");
        });
      }
      // The native stop request already hid the window; neither completion nor copying raises it.
      ended();
    },
    onError: () => ended(),
  });
  latest.current = dictation;

  useEffect(() => {
    if (preview.current) preview.current.scrollTop = preview.current.scrollHeight;
  }, [dictation.transcript]);

  useEffect(() => {
    alive.current = true;
    document.documentElement.classList.add("dictation-window");
    let disposed = false;
    const unlisteners: Array<() => void> = [];
    const request = (next: DictationRequest) => {
      if (disposed || next.serial <= seen.current) return;
      seen.current = next.serial;
      if (next.listening) begin(next);
      else { pending.current = null; latest.current?.stop(); }
    };
    void (async () => {
      const stopRequests = await listenForDictation(request);
      if (disposed) { stopRequests(); return; }
      unlisteners.push(stopRequests);
      request(await pendingDictationRequest());
    })().catch(() => {
      unlisteners.splice(0).forEach((stop) => stop());
      if (!disposed) setCopyError("Could not connect voice input. Reopen Gunther and try again.");
    });
    return () => {
      disposed = true;
      alive.current = false;
      unlisteners.forEach((stop) => stop());
      document.documentElement.classList.remove("dictation-window");
    };
  }, []);

  const error = dictation.error ?? copyError;
  return <main className="dictation-panel" aria-label="Voice input">
    <header onMouseDown={(event) => {
      if (event.button === 0 && !(event.target as Element).closest("button")) void dragDictation().catch(() => undefined);
    }}>
      <span className="dictation-signal" aria-hidden style={{ opacity: .45 + dictation.level * .55, transform: `scale(${1 + dictation.level * .35})` }} />
      <span className="dictation-label" role={error ? "alert" : "status"} title={error ?? undefined}>{error ?? "语音输入"}</span>
      <span className="dictation-key" aria-hidden>⌥ Space</span>
      <button type="button" className="dictation-stop" aria-label="Stop voice input" title="停止并收起 · ⌥Space" onClick={() => { void stopDictation(); }}><Square size={11} fill="currentColor" /></button>
    </header>
    <div ref={preview} className={`dictation-preview${dictation.transcript ? "" : " is-empty"}`} aria-label="Transcribed text" aria-live="polite">{dictation.transcript || "正在听…"}</div>
  </main>;
}
