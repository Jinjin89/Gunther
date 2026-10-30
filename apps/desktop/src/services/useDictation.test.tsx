import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDictation } from "./useDictation";

class Socket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: Socket[] = [];
  readyState = 0;
  bufferedAmount = 0;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: Array<Record<string, unknown>> = [];
  constructor(public url: string) { Socket.instances.push(this); }
  send(data: string) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; }
  receive(event: Record<string, unknown>) {
    if (event.type === "service.ready") this.readyState = 1;
    this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent);
  }
}

const track = { stop: vi.fn() };
const stream = { getTracks: () => [track] } as unknown as MediaStream;
const getUserMedia = vi.fn();
let processor: { onaudioprocess: ((event: AudioProcessingEvent) => void) | null; connect: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> };
const phrase = (text: string, final = false) => ({ type: "transcript.piece", id: "sentence", start_ms: 0, text, final });
const ready = { type: "service.ready", stream: true, protocol: 2 };

beforeEach(() => {
  Socket.instances = [];
  track.stop.mockClear();
  getUserMedia.mockReset().mockResolvedValue(stream);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  processor = { onaudioprocess: null, connect: vi.fn(), disconnect: vi.fn() };
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("AudioContext", vi.fn(function () {
    return {
      sampleRate: 16_000, destination: {}, resume: vi.fn().mockResolvedValue(undefined), close: vi.fn().mockResolvedValue(undefined),
      createScriptProcessor: () => processor,
      createMediaStreamSource: () => ({ connect: vi.fn() }),
    };
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

function audio(milliseconds = 600) {
  processor.onaudioprocess?.({ inputBuffer: { getChannelData: () => new Float32Array(milliseconds * 16) } } as unknown as AudioProcessingEvent);
}

async function setup() {
  const onText = vi.fn(), onComplete = vi.fn();
  const hook = renderHook(() => useDictation(onText, { onComplete }));
  await act(async () => { await hook.result.current.start(); });
  return { ...hook, onText, onComplete, socket: Socket.instances[0]! };
}

describe("streaming dictation lifecycle", () => {
  it("captures immediately while the transcription connection is still opening", async () => {
    const { result, socket } = await setup();
    expect(result.current.state).toBe("listening");
    act(() => audio());
    expect(socket.sent).toHaveLength(0);
    act(() => socket.receive(ready));
    expect(socket.sent[0]?.type).toBe("input_audio_buffer.append");
  });

  it("shows revisions before stopping and completes when the service finishes the take", async () => {
    const { result, socket, onText, onComplete } = await setup();
    expect(socket.url).toContain("purpose=dictation&protocol=2&sample_rate=16000");
    expect(socket.url).toContain("stream=true");
    act(() => {
      socket.receive(ready);
      audio();
      socket.receive(phrase("今天"));
    });
    expect(result.current.state).toBe("listening");
    expect(result.current.transcript).toBe("今天");
    act(() => socket.receive(phrase("今天测试流式输入。", true)));
    expect(onText.mock.lastCall?.[0]).toBe("今天测试流式输入。");
    const updates = onText.mock.calls.length;
    act(() => socket.receive(phrase("今天测试流式输入。", true)));
    expect(onText).toHaveBeenCalledTimes(updates);
    act(() => result.current.stop());
    expect(onComplete).not.toHaveBeenCalled();
    expect(result.current.state).toBe("finishing");
    act(() => socket.receive({ type: "transcript.flushed", until_ms: 600 }));
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("今天测试流式输入。");
    expect(result.current.state).toBe("idle");
    expect(track.stop).toHaveBeenCalled();
  });

  it("stops during connection setup without losing queued audio", async () => {
    const { result, socket, onComplete } = await setup();
    act(() => { audio(); result.current.stop(); });
    expect(socket.sent).toHaveLength(0);
    expect(result.current.state).toBe("finishing");
    act(() => socket.receive(ready));
    expect(socket.sent.map((event) => event.type)).toEqual(["input_audio_buffer.append", "input_audio_buffer.commit"]);
    act(() => {
      socket.receive(phrase("最后一句。", true));
      socket.receive({ type: "transcript.flushed", until_ms: 600 });
    });
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("最后一句。");
  });

  it("releases a late permission result after cancellation without opening a socket", async () => {
    let allow!: (stream: MediaStream) => void;
    getUserMedia.mockImplementation(() => new Promise<MediaStream>((resolve) => { allow = resolve; }));
    const hook = renderHook(() => useDictation(vi.fn()));
    let starting!: Promise<void>;
    act(() => { starting = hook.result.current.start(); });
    act(() => hook.result.current.cancel());
    await act(async () => { allow(stream); await starting; });
    expect(Socket.instances).toHaveLength(0);
    expect(track.stop).toHaveBeenCalled();
    expect(hook.result.current.state).toBe("idle");
  });

  it("returns the latest recognized text without adding a sentence-confirmation gate", async () => {
    const { result, socket, onComplete } = await setup();
    act(() => { socket.receive(ready); socket.receive(phrase("收到的语音文字")); result.current.stop(); });
    act(() => socket.receive({ type: "transcript.flushed", until_ms: 0 }));
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("收到的语音文字");
    expect(result.current.error).toBeNull();
  });

  it("times out accurately instead of copying a partial take", async () => {
    vi.useFakeTimers();
    const { result, socket, onComplete } = await setup();
    act(() => { socket.receive(ready); socket.receive(phrase("已收到。", true)); result.current.stop(); });
    act(() => vi.advanceTimersByTime(30_001));
    expect(result.current.state).toBe("idle");
    expect(result.current.transcript).toBe("已收到。");
    expect(result.current.error).toContain("timed out");
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("ignores old callbacks after cancel and restart", async () => {
    const { result, socket, onText } = await setup();
    const oldMessage = socket.onmessage!;
    act(() => result.current.cancel());
    await act(async () => { await result.current.start(); });
    act(() => oldMessage({ data: JSON.stringify(phrase("Old words", true)) } as MessageEvent));
    expect(onText).not.toHaveBeenCalled();
    expect(result.current.transcript).toBe("");
  });

  it("does not discard a segment failure as if the take were complete", async () => {
    const { result, socket, onComplete } = await setup();
    act(() => { socket.receive(ready); socket.receive(phrase("Earlier words.", true)); });
    act(() => socket.receive({ type: "service.error", code: "segment_failed", message: "A segment failed." }));
    expect(result.current.error).toBe("A segment failed.");
    expect(result.current.transcript).toBe("Earlier words.");
    expect(onComplete).not.toHaveBeenCalled();
  });
});
