import { useCallback, useEffect, useRef, useState } from "react";
import { recordingSocketUrl } from "../api";
import { encodePcm16 } from "./pcm";

export type DictationState = "idle" | "starting" | "listening" | "finishing";

const FINISH_WAIT_MS = 4_000;
// A take sent whole is written only after it ends, which takes longer.
const WHOLE_TAKE_WAIT_MS = 45_000;

const micMessage = (reason: unknown) => {
  const name = reason instanceof DOMException ? reason.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "Microphone access was denied. Allow Gunther in System Settings → Privacy & Security → Microphone.";
  if (name === "NotFoundError") return "No microphone was found.";
  return "The microphone is busy or unavailable. If a recording is running, stop it first.";
};

/**
 * Speak into a text field: microphone audio goes to the same live transcription
 * model chosen for Ask dictation in Settings → Transcription, and each
 * finished phrase is handed to `onText`.
 */
export function useDictation(onText: (text: string) => void) {
  const [state, setState] = useState<DictationState>("idle");
  const [error, setError] = useState<string | null>(null);
  const handler = useRef(onText);
  handler.current = onText;
  const parts = useRef<{ stream: MediaStream; context: AudioContext; processor: ScriptProcessorNode; socket: WebSocket; ready: boolean; streaming: boolean; queue: string[]; timer: number | null } | null>(null);
  const cancelled = useRef(false);

  const release = useCallback(() => {
    const live = parts.current;
    parts.current = null;
    if (!live) return;
    if (live.timer) window.clearTimeout(live.timer);
    live.processor.disconnect();
    void live.context.close().catch(() => undefined);
    live.stream.getTracks().forEach((track) => track.stop());
    live.socket.onmessage = null;
    live.socket.onclose = null;
    live.socket.onerror = null;
    if (live.socket.readyState <= WebSocket.OPEN) live.socket.close();
    setState("idle");
  }, []);

  /** Hang up at once, without waiting for a last phrase, so nothing more is typed. */
  const cancel = useCallback(() => {
    cancelled.current = true;
    release();
  }, [release]);

  const stop = useCallback(() => {
    const live = parts.current;
    if (!live) { setState("idle"); return; }
    live.processor.disconnect();
    live.stream.getTracks().forEach((track) => track.stop());
    if (live.socket.readyState === WebSocket.OPEN && live.ready) {
      setState("finishing");
      live.socket.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
      live.timer = window.setTimeout(release, live.streaming ? FINISH_WAIT_MS : WHOLE_TAKE_WAIT_MS);
    } else release();
  }, [release]);

  const start = useCallback(async () => {
    if (parts.current) return;
    cancelled.current = false;
    setError(null);
    setState("starting");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (reason) {
      setError(micMessage(reason));
      setState("idle");
      return;
    }
    if (cancelled.current) { stream.getTracks().forEach((track) => track.stop()); setState("idle"); return; }
    const context = new AudioContext();
    const processor = context.createScriptProcessor(4_096, 1, 1);
    const socket = new WebSocket(recordingSocketUrl("", "dictation"));
    const live = { stream, context, processor, socket, ready: false, streaming: true, queue: [] as string[], timer: null as number | null };
    parts.current = live;
    context.createMediaStreamSource(stream).connect(processor);
    processor.connect(context.destination);
    processor.onaudioprocess = (event) => {
      const audio = encodePcm16(event.inputBuffer.getChannelData(0), context.sampleRate);
      if (live.ready && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input_audio_buffer.append", audio }));
      else if (live.queue.length < 120) live.queue.push(audio);
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(String(message.data)) as Record<string, unknown>;
      if (event.type === "service.ready") {
        live.ready = true;
        live.streaming = event.stream !== false;
        live.queue.splice(0).forEach((audio) => socket.send(JSON.stringify({ type: "input_audio_buffer.append", audio })));
        setState((current) => (current === "starting" ? "listening" : current));
      } else if (event.type === "service.error") {
        // A missed segment of a live take is skipped; a whole take that failed is all there is.
        if (live.streaming && (event.code === "segment_failed" || event.code === "sensevoice_segment_failed")) return;
        setError(String(event.message ?? "Voice input is unavailable."));
        release();
      } else if (event.type === "conversation.item.input_audio_transcription.completed") {
        const text = String(event.transcript ?? "").trim();
        if (text) handler.current(text);
        if (parts.current?.timer && socket.readyState === WebSocket.OPEN) {
          // After the last phrase of a finished take, close on a short quiet gap.
          window.clearTimeout(parts.current.timer);
          parts.current.timer = window.setTimeout(release, 800);
        }
      }
    };
    socket.onerror = () => { setError("Could not reach the transcription service."); release(); };
    socket.onclose = () => { if (parts.current === live) release(); };
  }, [release]);

  useEffect(() => () => { cancelled.current = true; release(); }, [release]);

  return { state, error, start, stop, cancel: release, clearError: () => setError(null) };
}
