import { useCallback, useEffect, useRef, useState } from "react";
import { recordingSocketUrl } from "../api";
import { encodePcm16, pcm16Length } from "./pcm";
import { dictationText, updateDictation, type DictationText } from "./dictationText";

export type DictationState = "idle" | "starting" | "listening" | "finishing";
const WAIT_MS = 30_000;
const RATE = 16_000;
const MAX_PENDING_SAMPLES = RATE * 30;

interface Session {
  stream: MediaStream | null;
  context: AudioContext | null;
  processor: ScriptProcessorNode | null;
  socket: WebSocket | null;
  ready: boolean;
  stopping: boolean;
  committed: boolean;
  queue: string[];
  samples: number;
  sourceSamples: number;
  pieces: DictationText;
  timer: number | null;
}
interface DictationOptions {
  onStart?: () => void;
  onComplete?: (text: string) => void;
  onError?: (message: string) => void;
}
let owner: Session | null = null;

function micMessage(reason: unknown): string {
  const name = reason instanceof DOMException ? reason.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "Microphone access was denied. Allow Gunther in System Settings → Privacy & Security → Microphone.";
  if (name === "NotFoundError") return "No microphone was found.";
  return "The microphone is busy or unavailable.";
}

/** A short, live dictation. Updates contain the whole take; completion follows the flush acknowledgement. */
export function useDictation(onText: (text: string, pieces: DictationText) => void, options: DictationOptions = {}) {
  const [state, setState] = useState<DictationState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const [transcript, setTranscript] = useState("");
  const handler = useRef(onText);
  const callbacks = useRef(options);
  handler.current = onText;
  callbacks.current = options;
  const current = useRef<Session | null>(null);
  const mounted = useRef(true);

  const release = useCallback((session: Session) => {
    if (session.timer !== null) window.clearTimeout(session.timer);
    session.timer = null;
    if (session.processor) {
      session.processor.onaudioprocess = null;
      session.processor.disconnect();
    }
    session.stream?.getTracks().forEach((track) => track.stop());
    if (session.context) void session.context.close().catch(() => undefined);
    if (session.socket) {
      session.socket.onmessage = null;
      session.socket.onclose = null;
      session.socket.onerror = null;
      if (session.socket.readyState <= WebSocket.OPEN) session.socket.close();
    }
    if (owner === session) owner = null;
    if (current.current === session) {
      current.current = null;
      if (mounted.current) { setLevel(0); setState("idle"); }
    }
  }, []);

  const fail = useCallback((session: Session, message: string) => {
    if (current.current !== session) return;
    if (mounted.current) setError(message);
    release(session);
    callbacks.current.onError?.(message);
  }, [release]);

  const cancel = useCallback(() => {
    const session = current.current;
    if (session) release(session);
  }, [release]);

  const armTimeout = useCallback((session: Session, message: string) => {
    if (session.timer !== null) window.clearTimeout(session.timer);
    session.timer = window.setTimeout(() => fail(session, message), WAIT_MS);
  }, [fail]);

  const commit = useCallback((session: Session) => {
    if (!session.ready || session.committed || session.socket?.readyState !== WebSocket.OPEN) return;
    session.committed = true;
    session.socket.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
    armTimeout(session, "The voice-input connection timed out. Your text is still here.");
  }, [armTimeout]);

  const stop = useCallback(() => {
    const session = current.current;
    if (!session || session.stopping) return;
    // A permission request cannot be aborted, but its late stream is released by start().
    if (!session.stream) { release(session); callbacks.current.onComplete?.(""); return; }
    session.stopping = true;
    if (session.processor) {
      session.processor.onaudioprocess = null;
      session.processor.disconnect();
    }
    session.stream.getTracks().forEach((track) => track.stop());
    setLevel(0);
    setState("finishing");
    armTimeout(session, "Voice input could not finish connecting. Your text has been kept.");
    // While opening, keep the queued audio until service.ready, then commit it in order.
    commit(session);
  }, [armTimeout, commit, release]);

  const start = useCallback(async () => {
    if (current.current) return;
    if (owner) {
      const message = "Voice input is already active in another field. Stop it first.";
      setError(message);
      callbacks.current.onError?.(message);
      return;
    }
    const session: Session = {
      stream: null, context: null, processor: null, socket: null,
      ready: false, stopping: false, committed: false, queue: [],
      samples: 0, sourceSamples: 0, pieces: [], timer: null,
    };
    current.current = session;
    owner = session;
    setError(null);
    setTranscript("");
    setState("starting");
    callbacks.current.onStart?.();
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (reason) { fail(session, micMessage(reason)); return; }
    if (current.current !== session) { stream.getTracks().forEach((track) => track.stop()); return; }
    session.stream = stream;
    try {
      const context = new AudioContext();
      session.context = context;
      armTimeout(session, "The microphone could not start. Please try again.");
      await context.resume();
      if (current.current !== session) return;
      const processor = context.createScriptProcessor(4_096, 1, 1);
      session.processor = processor;
      const socket = new WebSocket(recordingSocketUrl("", "dictation", { offsetMs: 0 }));
      session.socket = socket;
      context.createMediaStreamSource(stream).connect(processor);
      processor.connect(context.destination);
      // Capture and show listening immediately; service setup runs while audio is queued.
      setState("listening");
      let lastLevel = 0;
      const send = (audio: string) => socket.send(JSON.stringify({ type: "input_audio_buffer.append", audio }));
      processor.onaudioprocess = (event) => {
        if (current.current !== session || session.stopping) return;
        const samples = event.inputBuffer.getChannelData(0);
        const now = performance.now();
        if (now - lastLevel > 90) {
          lastLevel = now;
          let sum = 0;
          for (const sample of samples) sum += sample * sample;
          setLevel(Math.min(1, Math.sqrt(sum / samples.length) * 8));
        }
        const audio = encodePcm16(samples, context.sampleRate, RATE, session.sourceSamples);
        session.samples += pcm16Length(samples.length, context.sampleRate, RATE, session.sourceSamples);
        session.sourceSamples += samples.length;
        if (session.ready && socket.readyState === WebSocket.OPEN) {
          if (socket.bufferedAmount > 2 * 1024 * 1024) { fail(session, "The transcription connection is too slow. Your text has been kept."); return; }
          send(audio);
        } else if (session.samples <= MAX_PENDING_SAMPLES) session.queue.push(audio);
        else fail(session, "The transcription service did not connect in time. Please try again.");
      };
      armTimeout(session, "Could not connect to live transcription. Please try again.");
      socket.onmessage = (message) => {
        if (current.current !== session) return;
        let event: Record<string, unknown>;
        try {
          event = JSON.parse(String(message.data)) as Record<string, unknown>;
          if (!event || typeof event !== "object" || Array.isArray(event)) throw new Error("Invalid event");
        }
        catch { fail(session, "The transcription service returned an invalid response."); return; }
        if (event.type === "service.ready") {
          if (session.ready) return;
          session.ready = true;
          if (session.timer !== null) window.clearTimeout(session.timer);
          session.timer = null;
          session.queue.splice(0).forEach(send);
          if (session.stopping) commit(session);
          else setState("listening");
        } else if (event.type === "service.error" || event.type === "transcript.gap") {
          fail(session, String(event.message ?? "Some audio could not be transcribed. Your text has been kept."));
        } else if (event.type === "transcript.piece") {
          const next = updateDictation(session.pieces, event);
          if (next === session.pieces) return;
          session.pieces = next;
          const text = dictationText(next);
          setTranscript(text);
          handler.current(text, next);
        } else if (event.type === "transcript.flushed" && session.stopping && session.committed) {
          // The service has finished the submitted audio. Use its latest text directly;
          // do not add a separate per-sentence confidence/confirmation gate.
          const text = dictationText(session.pieces);
          release(session);
          callbacks.current.onComplete?.(text);
        }
      };
      socket.onerror = () => fail(session, "Could not reach the transcription service. Your text has been kept.");
      socket.onclose = () => fail(session, "The transcription connection closed before it finished. Your text has been kept.");
    } catch (reason) {
      fail(session, reason instanceof Error ? reason.message : "Voice input is unavailable.");
    }
  }, [armTimeout, commit, fail, release]);

  const toggle = useCallback(() => {
    if (current.current) stop();
    else void start();
  }, [start, stop]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; cancel(); };
  }, [cancel]);

  return { state, error, level, transcript, start, stop, toggle, cancel, clearError: () => setError(null) };
}
