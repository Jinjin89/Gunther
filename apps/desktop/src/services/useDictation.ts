import { useCallback, useEffect, useRef, useState } from "react";
import { flushAudio } from "./audioQueue";
import {
  appendLiveAudio,
  commitLiveAudio,
  openLiveTranscription,
  readLiveTranscriptionEvent,
} from "./liveTranscriptionTransport";
import { encodePcm16, pcm16Length } from "./pcm";
import { dictationText, updateDictation, type DictationText } from "./dictationText";

export type DictationState = "idle" | "starting" | "listening" | "finishing";
export type DictationCompletion = "completed" | "no_speech" | "cancelled";
const WAIT_MS = 30_000;
const RATE = 16_000;
const MAX_PENDING_SAMPLES = RATE * 30;
const MAX_BUFFERED_BYTES = 2 * 1024 * 1024;
const LEVEL_INTERVAL_MS = 60;
const MIC_DISCONNECTED = "The microphone disconnected. Please try again.";
const SOCKET_UNAVAILABLE = "Could not reach the transcription service. Your text has been kept.";
const SOCKET_CLOSED = "The transcription connection closed before it finished. Your text has been kept.";

interface Session {
  stream: MediaStream | null;
  track: MediaStreamTrack | null;
  context: AudioContext | null;
  source: MediaStreamAudioSourceNode | null;
  processor: ScriptProcessorNode | null;
  socket: WebSocket | null;
  graphReady: boolean;
  captured: boolean;
  ready: boolean;
  stopping: boolean;
  committed: boolean;
  queue: string[];
  submittedSamples: number;
  pieces: DictationText;
  micTimer: number | undefined;
  socketTimer: number | undefined;
}
export interface DictationOptions {
  onStart?: () => void;
  onComplete?: (text: string, outcome: DictationCompletion) => void;
  onError?: (message: string) => void;
}
let owner: Session | null = null;

function micMessage(reason: unknown): string {
  const name = reason instanceof DOMException ? reason.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "Microphone access was denied. Allow Gunther in System Settings → Privacy & Security → Microphone.";
  if (name === "NotFoundError") return "No microphone was found.";
  return "The microphone is busy or unavailable.";
}

function stopCapture(session: Session) {
  window.clearTimeout(session.micTimer);
  session.micTimer = undefined;
  if (session.processor) {
    session.processor.onaudioprocess = null;
    session.processor.disconnect();
    session.processor = null;
  }
  session.source?.disconnect();
  session.source = null;
  if (session.track) session.track.onended = null;
  session.track = null;
  session.stream?.getTracks().forEach((track) => track.stop());
  session.stream = null;
  if (session.context) void session.context.close().catch(() => undefined);
  session.context = null;
}

/** A short, live dictation. Updates contain the whole take. Captured takes complete after the flush. */
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
    if (current.current !== session) return;
    current.current = null;
    if (owner === session) owner = null;
    stopCapture(session);
    window.clearTimeout(session.socketTimer);
    session.socketTimer = undefined;
    if (session.socket) {
      session.socket.onmessage = null;
      session.socket.onclose = null;
      session.socket.onerror = null;
      if (session.socket.readyState <= WebSocket.OPEN) session.socket.close();
      session.socket = null;
    }
    session.queue.length = 0;
    if (mounted.current) { setLevel(0); setState("idle"); }
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

  const drain = useCallback((session: Session) => {
    const socket = session.socket;
    if (!session.ready || session.committed || !socket) return;
    if (socket.readyState !== WebSocket.OPEN) { fail(session, SOCKET_CLOSED); return; }
    try {
      flushAudio(session.queue, (audio) => {
        if (socket.bufferedAmount > MAX_BUFFERED_BYTES) {
          throw new Error("The transcription connection is too slow. Your text has been kept.");
        }
        appendLiveAudio(socket, audio);
      });
      if (session.stopping) {
        commitLiveAudio(socket);
        session.committed = true;
        session.socketTimer = window.setTimeout(() => {
          fail(session, "The voice-input connection timed out. Your text is still here.");
        }, WAIT_MS);
      }
    } catch (reason) {
      fail(session, reason instanceof Error ? reason.message : "Could not send voice input. Your text has been kept.");
    }
  }, [fail]);

  const stop = useCallback(() => {
    const session = current.current;
    if (!session || session.stopping) return;
    session.stopping = true;
    if (!session.graphReady) {
      release(session);
      callbacks.current.onComplete?.("", "cancelled");
      return;
    }
    // Each delivered block is already queued or sent. ScriptProcessor cannot expose a partial block.
    stopCapture(session);
    setLevel(0);
    setState("finishing");
    drain(session);
  }, [drain, release]);

  const start = useCallback(async () => {
    if (!mounted.current || current.current) return;
    if (owner) {
      const message = "Voice input is already active in another field. Stop it first.";
      setError(message);
      callbacks.current.onError?.(message);
      return;
    }
    const session: Session = {
      stream: null, track: null, context: null, source: null, processor: null, socket: null,
      graphReady: false, captured: false, ready: false, stopping: false, committed: false,
      queue: [], submittedSamples: 0, pieces: [], micTimer: undefined, socketTimer: undefined,
    };
    current.current = session;
    owner = session;
    setError(null);
    setTranscript("");
    setState("starting");
    callbacks.current.onStart?.();
    if (current.current !== session || session.stopping) return;

    let socket: WebSocket;
    try {
      socket = openLiveTranscription("", "dictation", { offsetMs: 0 });
    } catch {
      fail(session, SOCKET_UNAVAILABLE);
      return;
    }
    session.socket = socket;
    socket.onmessage = (message) => {
      if (current.current !== session) return;
      const event = readLiveTranscriptionEvent(message);
      if (!event) { fail(session, "The transcription service returned an invalid response."); return; }
      if (event.type === "service.ready") {
        if (session.ready) return;
        session.ready = true;
        window.clearTimeout(session.socketTimer);
        session.socketTimer = undefined;
        drain(session);
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
        const untilMs = event.until_ms;
        const expectedMs = Math.floor((session.submittedSamples * 1000) / RATE);
        if (typeof untilMs !== "number" || !Number.isInteger(untilMs) || untilMs !== expectedMs) return;
        const text = dictationText(session.pieces);
        release(session);
        callbacks.current.onComplete?.(text, text.trim() ? "completed" : "no_speech");
      }
    };
    socket.onerror = () => fail(session, SOCKET_UNAVAILABLE);
    socket.onclose = () => fail(session, SOCKET_CLOSED);
    session.socketTimer = window.setTimeout(() => {
      fail(session, "Could not connect to live transcription. Please try again.");
    }, WAIT_MS);

    session.micTimer = window.setTimeout(() => {
      fail(session, "The microphone could not start. Please try again.");
    }, WAIT_MS);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: { ideal: false } } });
    } catch (reason) { fail(session, micMessage(reason)); return; }
    if (current.current !== session || session.stopping) {
      // A pending permission request can still grant a stream after the take ends.
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    session.stream = stream;
    try {
      const track = stream.getAudioTracks()[0];
      if (!track || track.readyState !== "live") { fail(session, MIC_DISCONNECTED); return; }
      session.track = track;
      track.onended = () => {
        if (current.current === session && !session.stopping) fail(session, MIC_DISCONNECTED);
      };
      const context = new AudioContext();
      session.context = context;
      await context.resume();
      if (current.current !== session || session.stopping) return;
      const source = context.createMediaStreamSource(stream);
      session.source = source;
      const processor = context.createScriptProcessor(1_024, 1, 1);
      session.processor = processor;
      let sourceSamples = 0;
      let lastLevel = -Infinity;
      processor.onaudioprocess = (event) => {
        if (current.current !== session || session.stopping || track.readyState !== "live") return;
        if (!session.captured && track.muted) return;
        const samples = event.inputBuffer.getChannelData(0);
        if (!samples.length) return;
        if (!session.captured) {
          session.captured = true;
          window.clearTimeout(session.micTimer);
          session.micTimer = undefined;
          setState("listening");
        }
        const now = performance.now();
        if (now - lastLevel >= LEVEL_INTERVAL_MS) {
          lastLevel = now;
          let sum = 0;
          for (const sample of samples) sum += sample * sample;
          setLevel(Math.min(1, Math.sqrt(sum / samples.length) * 8));
        }
        const length = pcm16Length(samples.length, context.sampleRate, RATE, sourceSamples);
        if (!session.ready && session.submittedSamples + length > MAX_PENDING_SAMPLES) {
          fail(session, "The transcription service did not connect in time. Please try again.");
          return;
        }
        if (length > 0) {
          session.queue.push(encodePcm16(samples, context.sampleRate, RATE, sourceSamples));
          session.submittedSamples += length;
        }
        sourceSamples += samples.length;
        drain(session);
      };
      source.connect(processor);
      processor.connect(context.destination);
      session.graphReady = true;
    } catch (reason) {
      fail(session, reason instanceof Error ? reason.message : "Voice input is unavailable.");
    }
  }, [drain, fail, release]);

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
