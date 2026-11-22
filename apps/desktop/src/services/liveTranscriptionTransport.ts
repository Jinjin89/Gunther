import { recordingSocketUrl } from "../api";
import { log } from "../log";

export function openLiveTranscription(...options: Parameters<typeof recordingSocketUrl>): WebSocket {
  return new WebSocket(recordingSocketUrl(...options));
}

export function appendLiveAudio(socket: WebSocket, audio: string, offsetMs?: number) {
  socket.send(JSON.stringify({ type: "input_audio_buffer.append", audio, offset_ms: offsetMs }));
}

export function commitLiveAudio(socket: WebSocket) {
  socket.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
}

export function readLiveTranscriptionEvent(message: MessageEvent): Record<string, unknown> | null {
  let event: unknown;
  try {
    event = JSON.parse(String(message.data));
  } catch {
    event = null;
  }
  if (event && typeof event === "object" && !Array.isArray(event)) {
    return event as Record<string, unknown>;
  }
  log.warn("transcription", "Received an invalid live transcription frame.");
  return null;
}
