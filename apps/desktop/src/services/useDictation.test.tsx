import { act, cleanup, renderHook } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { log } from "../log";
import { useDictatedField } from "./useDictatedField";
import { useDictation } from "./useDictation";

class Socket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  static instances: Socket[] = [];
  readyState = Socket.CONNECTING;
  bufferedAmount = 0;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: Array<Record<string, unknown>> = [];
  constructor(public url: string) { Socket.instances.push(this); }
  send = vi.fn((data: string) => {
    if (this.readyState !== Socket.OPEN) throw new Error("Socket is not open.");
    this.sent.push(JSON.parse(data));
  });
  close = vi.fn(() => { this.readyState = Socket.CLOSED; });
  disconnect() {
    this.readyState = Socket.CLOSED;
    this.onclose?.();
  }
  receive(event: Record<string, unknown>) {
    if (this.readyState === Socket.CONNECTING) this.readyState = Socket.OPEN;
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(event) }));
  }
}

function microphone() {
  const track = { readyState: "live" as MediaStreamTrackState, muted: false, onended: null as (() => void) | null, stop: vi.fn() };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
  return { track, stream };
}

function audioContext() {
  const processor = { onaudioprocess: null as ((event: AudioProcessingEvent) => void) | null, connect: vi.fn(), disconnect: vi.fn() };
  const source = { connect: vi.fn(), disconnect: vi.fn() };
  return {
    processor,
    source,
    sampleRate: 16_000,
    destination: {},
    resume: vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
    close: vi.fn().mockResolvedValue(undefined),
    createScriptProcessor: vi.fn(() => processor),
    createMediaStreamSource: vi.fn(() => source),
  };
}

const getUserMedia = vi.fn<MediaDevices["getUserMedia"]>();
let mic: ReturnType<typeof microphone>;
let context: ReturnType<typeof audioContext>;
const phrase = (text: string, final = false) => ({ type: "transcript.piece", id: "sentence", start_ms: 0, text, final });
const ready = { type: "service.ready", stream: true, protocol: 2 };
const flushed = (until_ms: number) => ({ type: "transcript.flushed", until_ms });

beforeEach(() => {
  Socket.instances = [];
  mic = microphone();
  context = audioContext();
  getUserMedia.mockReset().mockResolvedValue(mic.stream);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("AudioContext", vi.fn(function () { return context; }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function audioEvent(sample = 0) {
  return { inputBuffer: { getChannelData: () => new Float32Array(1_024).fill(sample) } } as unknown as AudioProcessingEvent;
}

function audio(sample = 0) {
  context.processor.onaudioprocess?.(audioEvent(sample));
}

function appendedPcm(socket: Socket) {
  return socket.sent.flatMap(({ audio }) => typeof audio === "string" ? [window.atob(audio)] : []);
}

function renderDictation() {
  const onText = vi.fn(), onStart = vi.fn(), onComplete = vi.fn(), onError = vi.fn();
  const hook = renderHook(() => useDictation(onText, { onStart, onComplete, onError }));
  return { ...hook, onText, onStart, onComplete, onError };
}

async function setup() {
  const take = renderDictation();
  await act(async () => { await take.result.current.start(); });
  return { ...take, socket: Socket.instances[0]! };
}

describe("streaming dictation lifecycle", () => {
  it("starts both operations immediately and listens only after the first live unmuted block", async () => {
    const permission = deferred<MediaStream>();
    getUserMedia.mockReturnValueOnce(permission.promise);
    const { result, onStart } = renderDictation();
    let starting!: Promise<void>;
    act(() => { starting = result.current.start(); });

    expect(onStart).toHaveBeenCalledTimes(1);
    expect(getUserMedia).toHaveBeenCalledExactlyOnceWith({ audio: { echoCancellation: { ideal: false } } });
    expect(Socket.instances).toHaveLength(1);
    const socket = Socket.instances[0]!;
    expect(socket.url).toContain("purpose=dictation&protocol=2&sample_rate=16000&offset_ms=0&stream=true");
    expect(result.current.state).toBe("starting");
    act(() => socket.receive(ready));
    expect(result.current.state).toBe("starting");

    await act(async () => { permission.resolve(mic.stream); await starting; });
    expect(context.createScriptProcessor).toHaveBeenCalledExactlyOnceWith(1_024, 1, 1);
    expect(result.current.state).toBe("starting");
    mic.track.muted = true;
    act(() => audio());
    expect(result.current.state).toBe("starting");
    expect(socket.sent).toHaveLength(0);

    mic.track.muted = false;
    act(() => audio());
    expect(result.current.state).toBe("listening");
    expect(appendedPcm(socket)).toEqual(["\0".repeat(2_048)]);
  });

  it("captures before service readiness and drains queued audio before subsequent live audio", async () => {
    const { result, socket } = await setup();
    expect(result.current.state).toBe("starting");
    act(() => audio(0.25));
    expect(result.current.state).toBe("listening");
    expect(result.current.level).toBeGreaterThan(0);
    expect(socket.sent).toHaveLength(0);
    act(() => {
      socket.receive(ready);
      audio(0.5);
      socket.receive(ready);
    });
    expect(socket.sent.map(({ type }) => type)).toEqual(["input_audio_buffer.append", "input_audio_buffer.append"]);
    expect(appendedPcm(socket).map((frame) => frame.charCodeAt(1))).toEqual([0x1f, 0x3f]);
  });

  it("shows revisions before stopping and keeps the confirmed final", async () => {
    const { result, socket, onText, onComplete } = await setup();
    act(() => {
      socket.receive(ready);
      audio();
      socket.receive(phrase("今天"));
    });
    expect(result.current.transcript).toBe("今天");
    act(() => socket.receive(phrase("今天测试流式输入。", true)));
    expect(onText.mock.lastCall?.[0]).toBe("今天测试流式输入。");
    const updates = onText.mock.calls.length;
    act(() => {
      socket.receive(phrase("今天测试流式输入。", true));
      socket.receive(phrase("今天"));
      result.current.stop();
    });
    expect(onText).toHaveBeenCalledTimes(updates);
    expect(onComplete).not.toHaveBeenCalled();
    expect(result.current.state).toBe("finishing");
    act(() => socket.receive(flushed(64)));
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("今天测试流式输入。", "completed");
    expect(result.current.state).toBe("idle");
  });

  it("releases capture on Stop, drains the delivered blocks in order and commits once", async () => {
    const { result, socket, onComplete } = await setup();
    const deliverBlock = context.processor.onaudioprocess!;
    act(() => { audio(0.25); audio(0.5); result.current.stop(); });
    expect(socket.sent).toHaveLength(0);
    expect(result.current.state).toBe("finishing");
    expect(result.current.level).toBe(0);
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
    expect(context.source.disconnect).toHaveBeenCalledTimes(1);
    expect(context.processor.disconnect).toHaveBeenCalledTimes(1);
    expect(context.processor.onaudioprocess).toBeNull();
    expect(context.close).toHaveBeenCalledTimes(1);
    act(() => {
      deliverBlock(audioEvent(0.75));
      socket.receive(flushed(128));
    });
    expect(onComplete).not.toHaveBeenCalled();
    act(() => {
      socket.receive(ready);
      socket.receive(ready);
      result.current.stop();
      deliverBlock(audioEvent(0.75));
    });
    expect(socket.sent.map(({ type }) => type)).toEqual([
      "input_audio_buffer.append", "input_audio_buffer.append", "input_audio_buffer.commit",
    ]);
    expect(appendedPcm(socket).map((frame) => frame.charCodeAt(1))).toEqual([0x1f, 0x3f]);
    act(() => {
      socket.receive(phrase("最后一句。", true));
      socket.receive(flushed(128));
      socket.receive(flushed(128));
    });
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("最后一句。", "completed");
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
    expect(context.processor.disconnect).toHaveBeenCalledTimes(1);
    expect(context.close).toHaveBeenCalledTimes(1);
    expect(socket.close).toHaveBeenCalledTimes(1);
  });

  it.each(["stop", "cancel", "unmount"] as const)("closes the connecting socket on %s during permission and releases the late stream", async (action) => {
    const permission = deferred<MediaStream>();
    getUserMedia.mockReturnValueOnce(permission.promise);
    const { result, onComplete, onError, unmount } = renderDictation();
    let starting!: Promise<void>;
    act(() => { starting = result.current.start(); });
    const socket = Socket.instances[0]!;
    expect(socket.readyState).toBe(Socket.CONNECTING);
    if (action === "unmount") unmount();
    else act(() => result.current[action]());
    expect(socket.close).toHaveBeenCalledTimes(1);
    expect(socket.sent).toHaveLength(0);
    if (action !== "unmount") expect(result.current.state).toBe("idle");
    if (action === "stop") expect(onComplete).toHaveBeenCalledExactlyOnceWith("", "cancelled");
    else expect(onComplete).not.toHaveBeenCalled();

    await act(async () => { permission.resolve(mic.stream); await starting; });
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
    expect(AudioContext).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(socket.sent).toHaveLength(0);
  });

  it("cancels during resume without wiring the graph after resume settles", async () => {
    const resume = deferred<void>();
    context.resume.mockReturnValueOnce(resume.promise);
    const { result, onComplete, onError } = renderDictation();
    let starting!: Promise<void>;
    await act(async () => { starting = result.current.start(); });
    const socket = Socket.instances[0]!;
    expect(context.resume).toHaveBeenCalledTimes(1);
    expect(result.current.state).toBe("starting");
    act(() => result.current.stop());
    expect(result.current.state).toBe("idle");
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
    expect(context.close).toHaveBeenCalledTimes(1);
    expect(socket.close).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("", "cancelled");

    await act(async () => { resume.resolve(); await starting; });
    expect(context.createMediaStreamSource).not.toHaveBeenCalled();
    expect(context.createScriptProcessor).not.toHaveBeenCalled();
    expect(result.current.state).toBe("idle");
    expect(socket.sent).toHaveLength(0);
    expect(onError).not.toHaveBeenCalled();
  });

  it.each([
    ["NotAllowedError", "Microphone access was denied"],
    ["NotFoundError", "No microphone was found"],
  ])("closes the connecting socket if permission fails with %s", async (name, message) => {
    getUserMedia.mockRejectedValueOnce(new DOMException("Microphone unavailable", name));
    const { result, socket, onComplete, onError } = await setup();
    expect(result.current.state).toBe("idle");
    expect(onError).toHaveBeenCalledExactlyOnceWith(expect.stringContaining(message));
    expect(onComplete).not.toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledTimes(1);
    expect(socket.sent).toHaveLength(0);
    expect(AudioContext).not.toHaveBeenCalled();
  });

  it.each([
    ["a service error", (socket: Socket) => socket.receive({ type: "service.error", message: "Connection refused." }), "Connection refused"],
    ["a transport error", (socket: Socket) => socket.onerror?.(), "Could not reach"],
    ["a disconnect", (socket: Socket) => socket.disconnect(), "closed before it finished"],
  ] as const)("releases a late permission grant after %s", async (_event, failSocket, message) => {
    const permission = deferred<MediaStream>();
    getUserMedia.mockReturnValueOnce(permission.promise);
    const { result, onError, onComplete } = renderDictation();
    let starting!: Promise<void>;
    act(() => { starting = result.current.start(); });
    const socket = Socket.instances[0]!;
    act(() => failSocket(socket));
    expect(result.current.state).toBe("idle");
    expect(socket.readyState).toBe(Socket.CLOSED);
    expect(onError).toHaveBeenCalledExactlyOnceWith(expect.stringContaining(message));
    expect(onComplete).not.toHaveBeenCalled();

    await act(async () => { permission.resolve(mic.stream); await starting; });
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
    expect(AudioContext).not.toHaveBeenCalled();
    expect(socket.sent).toHaveLength(0);
  });

  it.each(["permission", "resume", "first input"] as const)("keeps the microphone deadline after early service readiness while waiting for %s", async (phase) => {
    vi.useFakeTimers();
    const permission = deferred<MediaStream>();
    const resume = deferred<void>();
    if (phase === "permission") getUserMedia.mockReturnValueOnce(permission.promise);
    if (phase === "resume") context.resume.mockReturnValueOnce(resume.promise);
    const { result, onError, onComplete } = renderDictation();
    let starting!: Promise<void>;
    await act(async () => { starting = result.current.start(); });
    const socket = Socket.instances[0]!;
    act(() => socket.receive(ready));
    expect(result.current.state).toBe("starting");
    act(() => vi.advanceTimersByTime(30_001));
    expect(result.current.state).toBe("idle");
    expect(onError).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("microphone could not start"));
    expect(onComplete).not.toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledTimes(1);

    await act(async () => { permission.resolve(mic.stream); resume.resolve(); await starting; });
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
    if (phase !== "first input") expect(context.createScriptProcessor).not.toHaveBeenCalled();
  });

  it("keeps the socket readiness deadline after the microphone starts capturing", async () => {
    vi.useFakeTimers();
    const { result, socket, onError, onComplete } = await setup();
    act(() => audio());
    expect(result.current.state).toBe("listening");
    act(() => vi.advanceTimersByTime(30_001));
    expect(result.current.state).toBe("idle");
    expect(onError).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("Could not connect to live transcription"));
    expect(onComplete).not.toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledTimes(1);
    expect(socket.sent).toHaveLength(0);
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
  });

  it("also releases capture if the microphone disconnects", async () => {
    const { result, socket, onError, onComplete } = await setup();
    act(() => {
      audio();
      mic.track.readyState = "ended";
      mic.track.onended?.();
    });
    expect(result.current.state).toBe("idle");
    expect(onError).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("microphone disconnected"));
    expect(onComplete).not.toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledTimes(1);
    expect(context.close).toHaveBeenCalledTimes(1);
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
  });

  it.each([16_000, 44_100, 48_000])("commits the cumulative 16 kHz boundary at a source rate of %d Hz", async (rate) => {
    context.sampleRate = rate;
    const { result, socket, onComplete } = await setup();
    const sampleCount = Math.round(2 * 1_024 * 16_000 / rate);
    const until = Math.floor(sampleCount * 1000 / 16_000);
    act(() => {
      socket.receive(ready);
      audio();
      audio();
      socket.receive(flushed(until));
      result.current.stop();
    });
    expect(appendedPcm(socket).reduce((bytes, frame) => bytes + frame.length, 0) / 2).toBe(sampleCount);
    act(() => {
      socket.receive(flushed(until - 1));
      socket.receive({ type: "transcript.flushed", until_ms: String(until) });
      socket.receive({ type: "transcript.flushed" });
      socket.onmessage?.(new MessageEvent("message", { data: '{"type":"transcript.flushed","until_ms":1e400}' }));
    });
    expect(result.current.state).toBe("finishing");
    expect(onComplete).not.toHaveBeenCalled();
    act(() => {
      socket.receive(phrase("Hello", true));
      socket.receive(flushed(until));
    });
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("Hello", "completed");
  });

  it.each([65, 64.5])("ignores a %s ms flush while waiting for the exact 64 ms flush", async (until) => {
    const { result, socket, onComplete } = await setup();
    act(() => {
      socket.receive(ready);
      audio();
      socket.receive(phrase("Preview"));
      result.current.stop();
    });
    act(() => socket.receive(flushed(until)));
    expect(result.current.state).toBe("finishing");
    expect(onComplete).not.toHaveBeenCalled();

    act(() => {
      socket.receive(phrase("Final correction.", true));
      socket.receive(flushed(64));
    });
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("Final correction.", "completed");
  });

  it("commits at zero after Stop on a wired graph with no delivered blocks", async () => {
    const { result, socket, onComplete } = await setup();
    act(() => result.current.stop());
    expect(result.current.state).toBe("finishing");
    expect(socket.sent).toHaveLength(0);
    act(() => socket.receive(ready));
    expect(socket.sent).toEqual([{ type: "input_audio_buffer.commit" }]);
    expect(onComplete).not.toHaveBeenCalled();
    act(() => socket.receive(flushed(0)));
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("", "no_speech");
  });

  it.each([1, 0.5])("ignores a %s ms flush while waiting for the exact zero-sample flush", async (until) => {
    const { result, socket, onComplete } = await setup();
    act(() => {
      socket.receive(ready);
      result.current.stop();
      socket.receive(flushed(until));
    });
    expect(result.current.state).toBe("finishing");
    expect(onComplete).not.toHaveBeenCalled();

    act(() => socket.receive(flushed(0)));
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("", "no_speech");
  });

  it("reports no speech when a captured take's draft is finalized as empty", async () => {
    const { result, socket, onComplete } = await setup();
    act(() => {
      socket.receive(ready);
      audio();
      socket.receive(phrase("Draft"));
      result.current.stop();
      socket.receive(phrase(" ", true));
      socket.receive(flushed(64));
    });
    expect(result.current.transcript).toBe("");
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("", "no_speech");
  });

  it("returns the latest recognized text without adding a sentence-confirmation gate", async () => {
    const { result, socket, onComplete } = await setup();
    act(() => { socket.receive(ready); socket.receive(phrase("收到的语音文字")); result.current.stop(); });
    act(() => socket.receive(flushed(0)));
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("收到的语音文字", "completed");
    expect(result.current.error).toBeNull();
  });

  it("keeps the post-commit deadline after a duplicate ready and mismatched flushes", async () => {
    vi.useFakeTimers();
    const { result, socket, onComplete } = await setup();
    act(() => { socket.receive(ready); audio(); socket.receive(phrase("已收到。", true)); });
    act(() => vi.advanceTimersByTime(30_001));
    expect(result.current.state).toBe("listening");
    act(() => result.current.stop());
    act(() => vi.advanceTimersByTime(29_999));
    act(() => {
      socket.receive(ready);
      socket.receive(flushed(65));
      socket.receive(flushed(64.5));
    });
    expect(result.current.state).toBe("finishing");
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.state).toBe("idle");
    expect(result.current.transcript).toBe("已收到。");
    expect(result.current.error).toContain("timed out");
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("bounds the audio retained before the service is ready", async () => {
    const { result, socket, onComplete } = await setup();
    act(() => {
      for (let block = 0; block < Math.floor(30 * 16_000 / 1_024); block += 1) audio();
    });
    expect(result.current.state).toBe("listening");
    act(() => audio());
    expect(result.current.state).toBe("idle");
    expect(result.current.error).toContain("did not connect in time");
    expect(socket.sent).toHaveLength(0);
    expect(socket.close).toHaveBeenCalledTimes(1);
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("fails before sending the rest of the queue if draining raises socket backpressure", async () => {
    const { result, socket, onError, onComplete } = await setup();
    act(() => { audio(); audio(); result.current.stop(); });
    const send = socket.send.getMockImplementation()!;
    socket.send.mockImplementationOnce((data) => {
      send(data);
      socket.bufferedAmount = 2 * 1024 * 1024 + 1;
    });
    act(() => socket.receive(ready));
    expect(socket.sent.map(({ type }) => type)).toEqual(["input_audio_buffer.append"]);
    expect(result.current.state).toBe("idle");
    expect(onError).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("too slow"));
    expect(onComplete).not.toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledTimes(1);
  });

  it("applies the same backpressure limit to live audio", async () => {
    const { result, socket, onError } = await setup();
    act(() => socket.receive(ready));
    socket.bufferedAmount = 2 * 1024 * 1024 + 1;
    act(() => audio());
    expect(socket.sent).toHaveLength(0);
    expect(result.current.state).toBe("idle");
    expect(onError).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("too slow"));
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
  });

  it.each(["live audio", "queued audio", "commit"] as const)("releases the take if sending %s throws", async (message) => {
    const { result, socket, onError, onComplete } = await setup();
    socket.send.mockImplementationOnce(() => { throw new Error("Send failed."); });
    act(() => {
      if (message === "queued audio") audio();
      socket.receive(ready);
      if (message === "live audio") audio();
      if (message === "commit") result.current.stop();
    });
    expect(socket.send).toHaveBeenCalledTimes(1);
    expect(result.current.state).toBe("idle");
    expect(onError).toHaveBeenCalledExactlyOnceWith("Send failed.");
    expect(onComplete).not.toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledTimes(1);
    expect(mic.track.stop).toHaveBeenCalledTimes(1);
  });

  it("ignores old audio and socket callbacks after cancel and restart", async () => {
    const { result, socket, onText, onError } = await setup();
    const oldAudio = context.processor.onaudioprocess!;
    const oldMessage = socket.onmessage!;
    const oldError = socket.onerror!;
    const oldClose = socket.onclose!;
    act(() => result.current.cancel());
    mic = microphone();
    context = audioContext();
    getUserMedia.mockResolvedValueOnce(mic.stream);
    await act(async () => { await result.current.start(); });
    act(() => {
      oldAudio(audioEvent());
      oldMessage(new MessageEvent("message", { data: JSON.stringify(phrase("Old words", true)) }));
      oldError();
      oldClose();
    });
    expect(result.current.state).toBe("starting");
    expect(result.current.transcript).toBe("");
    expect(onText).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(Socket.instances[1]!.sent).toHaveLength(0);
  });

  it("does not discard a segment failure as if the take were complete", async () => {
    const { result, socket, onComplete } = await setup();
    act(() => { socket.receive(ready); socket.receive(phrase("Earlier words.", true)); });
    act(() => socket.receive({ type: "service.error", code: "segment_failed", message: "A segment failed." }));
    expect(result.current.error).toBe("A segment failed.");
    expect(result.current.transcript).toBe("Earlier words.");
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("keeps the latest text when the transport receives a malformed message", async () => {
    const { result, socket, onComplete } = await setup();
    const warn = vi.spyOn(log, "warn").mockImplementation(() => undefined);
    act(() => { socket.receive(ready); socket.receive(phrase("Earlier words.", true)); });
    act(() => socket.onmessage?.(new MessageEvent("message", { data: '["Partial transcript"]' })));
    expect(warn).toHaveBeenCalledExactlyOnceWith("transcription", "Received an invalid live transcription frame.");
    expect(result.current.state).toBe("idle");
    expect(result.current.error).toBe("The transcription service returned an invalid response.");
    expect(result.current.transcript).toBe("Earlier words.");
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("preserves manual field edits when a final arrives during finishing", async () => {
    const { result } = renderHook(() => {
      const [value, setValue] = useState("Question:");
      const dictation = useDictatedField(value, setValue);
      return { value, setValue, dictation };
    });
    await act(async () => { await result.current.dictation.start(); });
    const socket = Socket.instances[0]!;
    act(() => { socket.receive(ready); audio(); socket.receive(phrase("hello")); });
    expect(result.current.value).toBe("Question: hello");
    act(() => result.current.setValue("Question: edited"));
    act(() => {
      result.current.dictation.stop();
      socket.receive(phrase("hello there", true));
      socket.receive({ ...phrase("New words.", true), id: "next", start_ms: 32 });
      socket.receive(flushed(64));
    });
    expect(result.current.value).toBe("Question: edited New words.");
    expect(result.current.dictation.state).toBe("idle");
  });
});
