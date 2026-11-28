import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { DictationCompletion, DictationState } from "../services/useDictation";
import type { CompleteResult, DictationEvent, DictationOutcome, DictationRequest, VisibleOutcome } from "./bridge";
import DictationWindowApp from "./DictationWindowApp";

type HookOptions = {
  onComplete: (text: string, outcome: DictationCompletion) => void;
  onError: (message: string) => void;
};

const mock = vi.hoisted(() => ({
  options: null as HookOptions | null,
  onText: null as ((text: string) => void) | null,
  request: null as ((event: DictationEvent) => void) | null,
  realCapture: false,
  capture: { state: "idle" as DictationState, transcript: "", level: 0 },
  snapshot: vi.fn<() => Promise<DictationRequest>>(),
  start: vi.fn(),
  stop: vi.fn(),
  cancel: vi.fn(),
  complete: vi.fn<(takeId: number, outcome: DictationOutcome) => Promise<CompleteResult>>(),
  stopWindow: vi.fn<(takeId: number, revision: number) => Promise<void>>(),
  drag: vi.fn(),
}));

vi.mock("../services/useDictation", async (importOriginal) => {
  const real = await importOriginal<typeof import("../services/useDictation")>();
  return {
    ...real,
    useDictation: (onText: (text: string) => void, options: HookOptions) => {
      if (mock.realCapture) return real.useDictation(onText, options);
      mock.options = options;
      mock.onText = onText;
      return { ...mock.capture, error: null, start: mock.start, stop: mock.stop, cancel: mock.cancel };
    },
  };
});

vi.mock("./bridge", () => ({
  listenForDictation: async (handler: NonNullable<typeof mock.request>) => {
    mock.request = handler;
    return vi.fn();
  },
  pendingDictationRequest: () => mock.snapshot(),
  completeDictation: (takeId: number, outcome: DictationOutcome) => mock.complete(takeId, outcome),
  stopDictation: (takeId: number, revision: number) => mock.stopWindow(takeId, revision),
  dragDictation: () => mock.drag(),
}));

const startRequest: DictationRequest = { revision: 1, takeId: 1, action: "start" };
const stopRequest: DictationRequest = { revision: 2, takeId: 1, action: "stop" };
const nextStart: DictationRequest = { revision: 5, takeId: 5, action: "start" };
const ignored: CompleteResult = { status: "ignored", copied: false };

function outcomeRequest(outcome: VisibleOutcome, revision = 3) {
  return { revision, takeId: 1, action: "outcome" as const, outcome };
}

function reported(request: ReturnType<typeof outcomeRequest>, status: CompleteResult["status"] = "applied"): CompleteResult {
  return { status, copied: request.outcome.copied, request };
}

const idleRequest: DictationRequest = { revision: 3, takeId: 1, action: "idle" };
const copyRefused = outcomeRequest({
  kind: "copy_failed", copied: false, text: "保留下来的文字。", message: "无法完成复制，文字已保留。",
});

beforeEach(() => {
  mock.options = null;
  mock.onText = null;
  mock.request = null;
  mock.realCapture = false;
  mock.capture = { state: "starting", transcript: "", level: 0 };
  mock.snapshot.mockReset().mockResolvedValue(startRequest);
  mock.start.mockReset().mockResolvedValue(undefined);
  mock.stop.mockReset();
  mock.cancel.mockReset();
  mock.complete.mockReset().mockResolvedValue({ status: "applied", copied: false });
  mock.stopWindow.mockReset().mockResolvedValue(undefined);
  mock.drag.mockReset().mockResolvedValue(undefined);
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function openPanel() {
  const view = render(<DictationWindowApp />);
  await waitFor(() => expect(mock.start).toHaveBeenCalledTimes(1));
  return {
    unmount: view.unmount,
    signal: view.container.querySelector<HTMLElement>(".dictation-signal")!,
    capture(state: DictationState, transcript = "", level = 0) {
      mock.capture = { state, transcript, level };
      mock.onText!(transcript);
      view.rerender(<DictationWindowApp />);
    },
  };
}

function deliver(event: DictationEvent) {
  act(() => mock.request!(event));
}

async function deliverOutcome(snapshot: ReturnType<typeof outcomeRequest>) {
  const { takeId, revision, action } = snapshot;
  mock.snapshot.mockResolvedValueOnce(snapshot);
  await act(async () => { mock.request!({ takeId, revision, action }); });
}

const preview = () => screen.getByLabelText("Transcribed text");
const stopButton = () => screen.getByRole("button", { name: "Stop voice input" });
const dismissButton = () => screen.getByRole("button", { name: "Dismiss voice input" });

it("shows startup, capture and finishing using the actual microphone state", async () => {
  const panel = await openPanel();
  expect(screen.getByRole("status")).toHaveTextContent("语音输入");
  expect(preview()).toHaveTextContent("正在启动…");
  expect(preview()).toHaveClass("is-empty");
  expect(panel.signal).toHaveClass("is-starting");
  expect(panel.signal).not.toHaveAttribute("style");

  panel.capture("listening", "", 0.6);
  expect(preview()).toHaveTextContent("正在听…");
  expect(panel.signal).toHaveClass("is-listening");
  expect(panel.signal).toHaveStyle({ opacity: "0.78" });

  deliver(stopRequest);
  expect(mock.stop).toHaveBeenCalledTimes(1);
  expect(mock.snapshot).toHaveBeenCalledTimes(1);
  panel.capture("finishing", "听到的文字", 0.6);
  expect(screen.getByRole("status")).toHaveTextContent("正在整理…");
  expect(preview()).toHaveTextContent("听到的文字");
  expect(panel.signal).not.toHaveClass("is-starting");
  expect(panel.signal).not.toHaveClass("is-listening");
  expect(panel.signal.style.opacity).toBe("");
  expect(panel.signal.style.transform).toBe("");
});

it("keeps the terminal presentation until native completion and clears on native dismissal", async () => {
  const panel = await openPanel();
  const terminal = deferred<CompleteResult>();
  mock.complete.mockReturnValueOnce(terminal.promise);
  fireEvent.click(stopButton());
  expect(mock.stopWindow).toHaveBeenCalledExactlyOnceWith(1, 1);
  expect(mock.complete).not.toHaveBeenCalled();
  deliver(stopRequest);
  panel.capture("finishing", "语音预览。");

  act(() => { mock.options!.onComplete("完整的语音文字。", "completed"); });
  panel.capture("idle", "完整的语音文字。");
  expect(mock.complete).toHaveBeenCalledExactlyOnceWith(1, { kind: "completed", text: "完整的语音文字。" });
  expect(screen.getByRole("status")).toHaveTextContent("正在整理…");
  expect(preview()).toHaveTextContent("完整的语音文字。");

  await act(async () => terminal.resolve({ status: "applied", copied: true }));
  deliver({ revision: 3, takeId: 1, action: "idle" });
  expect(preview()).not.toHaveTextContent("完整的语音文字。");
});

it("settles a no-speech take through native closure without asking for dismissal", async () => {
  const panel = await openPanel();
  const terminal = deferred<CompleteResult>();
  mock.complete.mockReturnValueOnce(terminal.promise);
  deliver(stopRequest);
  panel.capture("idle");

  act(() => { mock.options!.onComplete("", "no_speech"); });
  expect(mock.complete).toHaveBeenCalledExactlyOnceWith(1, { kind: "no_speech" });
  expect(screen.getByRole("status")).toHaveTextContent("正在整理…");
  await act(async () => terminal.resolve({ status: "applied", copied: false, request: idleRequest }));

  expect(screen.getByRole("status")).toHaveTextContent("语音输入");
  expect(preview()).toHaveTextContent("正在启动…");
  expect(preview()).toHaveClass("is-empty");
  expect(screen.queryByRole("button", { name: "Dismiss voice input" })).not.toBeInTheDocument();
  expect(mock.stopWindow).not.toHaveBeenCalled();
  deliver(nextStart);
  expect(mock.start).toHaveBeenCalledTimes(2);
});

it("keeps a settled empty take idle when an older initial snapshot arrives", async () => {
  const initial = deferred<DictationRequest>();
  mock.snapshot.mockReturnValueOnce(initial.promise);
  mock.complete.mockResolvedValueOnce({ status: "applied", copied: false, request: idleRequest });
  render(<DictationWindowApp />);
  await waitFor(() => expect(mock.snapshot).toHaveBeenCalledTimes(1));
  deliver(startRequest);
  deliver(stopRequest);
  await act(async () => mock.options!.onComplete("", "no_speech"));
  expect(preview()).toHaveTextContent("正在启动…");

  await act(async () => initial.resolve(stopRequest));
  expect(preview()).toHaveTextContent("正在启动…");
  expect(mock.start).toHaveBeenCalledTimes(1);
  expect(mock.stop).toHaveBeenCalledTimes(1);
  expect(mock.complete).toHaveBeenCalledTimes(1);
});

it("reports cancellation before capture for the current take", async () => {
  await openPanel();
  deliver(stopRequest);
  await act(async () => mock.options!.onComplete("", "cancelled"));
  expect(mock.complete).toHaveBeenCalledExactlyOnceWith(1, { kind: "cancelled" });
});

it("settles an unseen stop snapshot without starting or calling the capture hook", async () => {
  mock.snapshot.mockResolvedValue({ revision: 8, takeId: 7, action: "stop" });
  render(<DictationWindowApp />);
  await waitFor(() => expect(mock.complete).toHaveBeenCalledExactlyOnceWith(7, { kind: "cancelled" }));
  expect(mock.start).not.toHaveBeenCalled();
  expect(mock.stop).not.toHaveBeenCalled();
  deliver({ revision: 7, takeId: 7, action: "start" });
  expect(mock.start).not.toHaveBeenCalled();
});

it.each([
  { revision: 4, takeId: 1, action: "idle" } as const,
  nextStart,
])("cancels a locally owned capture when $action supersedes it", async (event) => {
  await openPanel();
  mock.cancel.mockImplementationOnce(() => {
    mock.options!.onComplete("Retired take", "completed");
  });
  deliver(event);

  expect(mock.cancel).toHaveBeenCalledTimes(1);
  expect(mock.complete).not.toHaveBeenCalled();
  expect(mock.start).toHaveBeenCalledTimes(event.action === "start" ? 2 : 1);
});

it("cancels on an outcome notice before fetching its details and ignores the stale reply", async () => {
  await openPanel();
  const details = deferred<DictationRequest>();
  mock.snapshot.mockReturnValueOnce(details.promise);
  deliver({ revision: copyRefused.revision, takeId: copyRefused.takeId, action: "outcome" });

  expect(mock.cancel).toHaveBeenCalledTimes(1);
  expect(mock.snapshot).toHaveBeenCalledTimes(2);
  deliver(nextStart);
  await act(async () => details.resolve(copyRefused));
  expect(mock.start).toHaveBeenCalledTimes(2);
  expect(mock.cancel).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(preview()).toHaveTextContent("正在启动…");
});

it("sends the displayed take and revision when Dismiss races a new Start", async () => {
  await openPanel();
  const failure = outcomeRequest({ kind: "error", copied: false, message: "Service disconnected" });
  await deliverOutcome(failure);
  const dismiss = dismissButton();

  act(() => {
    mock.request!(nextStart);
    fireEvent.click(dismiss);
  });

  expect(mock.stopWindow).toHaveBeenCalledExactlyOnceWith(failure.takeId, failure.revision);
  expect(mock.start).toHaveBeenCalledTimes(2);
});

it("retains the latest preview and reports capture and transcription errors", async () => {
  const panel = await openPanel();
  const terminal = deferred<CompleteResult>();
  mock.complete.mockReturnValueOnce(terminal.promise);
  panel.capture("idle");
  const text = "保留的预览";
  const message = "The transcription service timed out.";
  act(() => {
    mock.onText!(text);
    mock.options!.onError(message);
  });
  expect(mock.complete).toHaveBeenCalledExactlyOnceWith(1, { kind: "error", text, message });
  expect(screen.getByRole("status")).toHaveTextContent("正在整理…");
  expect(preview()).toHaveTextContent(text);

  await act(async () => terminal.resolve(reported(outcomeRequest({ kind: "error", copied: false, text, message }))));
  expect(screen.getByRole("alert")).toHaveTextContent(message);
  expect(preview()).toHaveTextContent(text);
});

it("resubmits an ignored ASR error after the next Stop without calling the released hook", async () => {
  const panel = await openPanel();
  const reconciliation = deferred<DictationRequest>();
  mock.snapshot.mockReturnValueOnce(reconciliation.promise);
  const text = "语音预览。";
  const message = "ASR disconnected.";
  mock.complete.mockResolvedValueOnce(ignored).mockResolvedValueOnce(reported(
    outcomeRequest({ kind: "error", copied: false, text, message }, 4),
  ));

  mock.stopWindow.mockRejectedValueOnce(new Error("Stop delivery failed"));
  fireEvent.click(stopButton());
  panel.capture("idle", text);
  act(() => { mock.options!.onError(message); });
  await waitFor(() => expect(mock.snapshot).toHaveBeenCalledTimes(2));

  const retriedStop: DictationRequest = { revision: 3, takeId: 1, action: "stop" };
  deliver(retriedStop);
  expect(mock.stop).not.toHaveBeenCalled();
  await act(async () => reconciliation.resolve(startRequest));
  expect(mock.complete.mock.calls).toEqual([
    [1, { kind: "error", text, message }],
    [1, { kind: "error", text, message }],
  ]);
  expect(screen.getByRole("alert")).toHaveTextContent(message);

  deliver(nextStart);
  expect(mock.start).toHaveBeenCalledTimes(2);
  expect(mock.stop).not.toHaveBeenCalled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("retries an ignored reply once against the same active snapshot revision", async () => {
  await openPanel();
  mock.complete.mockResolvedValue(ignored);
  await act(async () => mock.options!.onError("ASR disconnected."));
  expect(mock.complete).toHaveBeenCalledTimes(2);
  expect(mock.snapshot).toHaveBeenCalledTimes(3);
  expect(mock.stop).not.toHaveBeenCalled();
});

it("drops an ignored terminal payload when native has already dismissed its take", async () => {
  await openPanel();
  mock.complete.mockResolvedValueOnce(ignored);
  mock.snapshot.mockResolvedValueOnce({ revision: 3, takeId: 1, action: "idle" });
  await act(async () => mock.options!.onError("ASR disconnected."));
  expect(mock.complete).toHaveBeenCalledTimes(1);
  expect(preview()).toHaveTextContent("正在启动…");
  deliver(nextStart);
  expect(mock.start).toHaveBeenCalledTimes(2);
  expect(mock.stop).not.toHaveBeenCalled();
});

it("surfaces a failed Stop command while the microphone remains active", async () => {
  const panel = await openPanel();
  panel.capture("listening");
  mock.stopWindow.mockRejectedValueOnce(new Error("Stop delivery failed"));
  fireEvent.click(stopButton());

  expect(await screen.findByRole("alert")).toHaveTextContent("语音输入仍在进行");
  expect(preview()).toHaveTextContent("正在听…");
  expect(panel.signal).toHaveClass("is-listening");
  expect(mock.stop).not.toHaveBeenCalled();
  fireEvent.click(stopButton());
  deliver(stopRequest);
  expect(mock.stop).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("retains failed clipboard text and retries the same take without dragging either button", async () => {
  await openPanel();
  fireEvent.mouseDown(screen.getByRole("status"), { button: 0 });
  const stop = stopButton();
  fireEvent.mouseDown(stop.querySelector("svg")!, { button: 0 });
  fireEvent.click(stop);
  expect(mock.stopWindow).toHaveBeenCalledTimes(1);
  expect(mock.drag).toHaveBeenCalledTimes(1);
  deliver(stopRequest);
  mock.complete.mockResolvedValueOnce(reported(copyRefused, "copy_failed"));
  const text = copyRefused.outcome.text!;
  await act(async () => mock.options!.onComplete(text, "completed"));

  expect(screen.getByRole("alert")).toHaveTextContent("无法完成复制");
  expect(preview()).toHaveTextContent(text);
  const retry = screen.getByRole("button", { name: "重试复制" });
  fireEvent.mouseDown(retry, { button: 0 });
  expect(mock.drag).toHaveBeenCalledTimes(1);

  const terminal = deferred<CompleteResult>();
  mock.complete.mockReturnValueOnce(terminal.promise);
  fireEvent.click(retry);
  expect(mock.complete).toHaveBeenCalledTimes(2);
  expect(mock.complete).toHaveBeenLastCalledWith(1, { kind: "completed", text });
  expect(screen.getByRole("status")).toHaveTextContent("正在整理…");
  expect(preview()).toHaveTextContent(text);
  await act(async () => terminal.resolve({ status: "applied", copied: true }));
});

it("restores a refused copy and its same-take retry from a snapshot after remount", async () => {
  const first = await openPanel();
  deliver(stopRequest);
  mock.complete.mockResolvedValueOnce(reported(copyRefused, "copy_failed"));
  const text = copyRefused.outcome.text!;
  await act(async () => mock.options!.onComplete(text, "completed"));
  first.unmount();

  mock.snapshot.mockResolvedValueOnce(copyRefused);
  render(<DictationWindowApp />);
  const retry = await screen.findByRole("button", { name: "重试复制" });
  expect(preview()).toHaveTextContent(text);
  expect(screen.getByRole("alert")).toHaveTextContent("无法完成复制");
  await act(async () => { fireEvent.click(retry); });
  expect(mock.complete).toHaveBeenLastCalledWith(1, { kind: "completed", text });
  expect(mock.start).toHaveBeenCalledTimes(1);
});

it("preserves the copy retry when an attempted restart restores the same visible outcome", async () => {
  await openPanel();
  deliver(stopRequest);
  mock.complete.mockResolvedValueOnce(reported(copyRefused, "copy_failed"));
  const text = copyRefused.outcome.text!;
  await act(async () => mock.options!.onComplete(text, "completed"));

  const restored = { ...copyRefused, revision: 5 };
  await deliverOutcome(restored);
  mock.complete.mockResolvedValueOnce(ignored);
  mock.snapshot.mockResolvedValueOnce(restored);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "重试复制" })); });
  expect(preview()).toHaveTextContent(text);
  expect(screen.getByRole("alert")).toHaveTextContent("无法完成复制");
  expect(mock.complete).toHaveBeenCalledTimes(2);

  mock.complete.mockResolvedValueOnce({ status: "applied", copied: true });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "重试复制" })); });
  expect(mock.complete).toHaveBeenLastCalledWith(1, { kind: "completed", text });
  expect(mock.start).toHaveBeenCalledTimes(1);
});

it.each(["无法自动收起窗口", "无法开始下一次语音输入"])(
  "reports a successful copy with a presentation failure (%s) and offers dismissal",
  async (problem) => {
    await openPanel();
    deliver(stopRequest);
    const message = `已复制到剪贴板，但${problem}。`;
    mock.complete.mockResolvedValueOnce(reported(outcomeRequest({
      kind: "copied_presentation_failed", copied: true, message,
    }), "presentation_failed"));

    await act(async () => mock.options!.onComplete("已经复制的文字。", "completed"));
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByRole("button", { name: "重试复制" })).not.toBeInTheDocument();
    await act(async () => { fireEvent.click(dismissButton()); });
    expect(mock.stopWindow).toHaveBeenCalledTimes(1);
    expect(mock.complete).toHaveBeenCalledTimes(1);
  },
);

it.each(["dismiss", "restart"])("recovers from delayed hide failure with %s", async (action) => {
  const panel = await openPanel();
  deliver(stopRequest);
  panel.capture("idle");
  mock.complete.mockResolvedValueOnce({ status: "applied", copied: true });
  await act(async () => mock.options!.onComplete("已经复制的文字。", "completed"));
  expect(screen.getByRole("status")).toHaveTextContent("正在整理…");

  await deliverOutcome(outcomeRequest({
    kind: "copied_presentation_failed", copied: true, message: "已复制到剪贴板，但无法自动收起窗口。",
  }, 4));
  expect(screen.getByRole("alert")).toHaveTextContent("已复制到剪贴板");
  expect(screen.queryByRole("button", { name: "重试复制" })).not.toBeInTheDocument();
  if (action === "dismiss") {
    await act(async () => { fireEvent.click(dismissButton()); });
    expect(mock.stopWindow).toHaveBeenCalledTimes(1);
  } else {
    deliver(nextStart);
    panel.capture("starting");
    expect(mock.start).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  }
  expect(mock.complete).toHaveBeenCalledTimes(1);
});

it("keeps over-limit text visible with a permanent error and dismissal", async () => {
  await openPanel();
  deliver(stopRequest);
  const text = "é".repeat(100_001);
  mock.complete.mockResolvedValueOnce(reported(outcomeRequest({
    kind: "invalid_text", copied: false, text, message: "文字超过 200000 字节，无法复制。",
  }), "invalid_text"));

  await act(async () => mock.options!.onComplete(text, "completed"));
  expect(screen.getByRole("alert")).toHaveTextContent("文字超过 200000 字节");
  expect(preview().textContent).toBe(text);
  expect(screen.queryByRole("button", { name: "重试复制" })).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(dismissButton()); });
  expect(mock.stopWindow).toHaveBeenCalledTimes(1);
  expect(mock.complete).toHaveBeenCalledTimes(1);
});

it("deduplicates revisions and starts a native queued take even while the old reply is pending", async () => {
  const panel = await openPanel();
  deliver(startRequest);
  expect(mock.start).toHaveBeenCalledTimes(1);
  deliver(stopRequest);
  deliver(stopRequest);
  expect(mock.stop).toHaveBeenCalledTimes(1);
  const terminal = deferred<CompleteResult>();
  mock.complete.mockReturnValueOnce(terminal.promise);
  act(() => { mock.options!.onComplete("上一段。", "completed"); });
  panel.capture("idle", "上一段。");

  deliver(nextStart);
  panel.capture("starting");
  expect(mock.start).toHaveBeenCalledTimes(2);
  await act(async () => terminal.reject(new Error("old reply lost")));
  deliver({ takeId: copyRefused.takeId, revision: copyRefused.revision, action: "outcome" });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(preview()).toHaveTextContent("正在启动…");
});

class CaptureSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  static instances: CaptureSocket[] = [];
  readyState = CaptureSocket.CONNECTING;
  bufferedAmount = 0;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn(() => { this.readyState = CaptureSocket.CLOSED; });

  constructor() {
    CaptureSocket.instances.push(this);
  }

  receive(frame: Record<string, unknown>) {
    if (this.readyState === CaptureSocket.CONNECTING) this.readyState = CaptureSocket.OPEN;
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(frame) }));
  }
}

function microphone() {
  const track = { readyState: "live", muted: false, onended: null, stop: vi.fn() };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
  return { track, stream };
}

it("releases a superseded real capture and submits only the replacement take's transcript", async () => {
  mock.realCapture = true;
  CaptureSocket.instances = [];
  const oldMic = microphone();
  const freshMic = microphone();
  const permission = deferred<MediaStream>();
  const details = deferred<DictationRequest>();
  const retired = outcomeRequest({
    kind: "error", copied: false, text: "Retired preview", message: "The previous capture ended.",
  }, 2);
  const getUserMedia = vi.fn<MediaDevices["getUserMedia"]>()
    .mockReturnValueOnce(permission.promise).mockResolvedValueOnce(freshMic.stream);
  const originalMedia = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });

  const processor = {
    onaudioprocess: null as ((event: AudioProcessingEvent) => void) | null,
    connect: vi.fn(),
    disconnect: vi.fn(),
  };
  const context = {
    sampleRate: 16_000,
    destination: {},
    resume: vi.fn().mockResolvedValue(undefined),
    close: vi.fn().mockResolvedValue(undefined),
    createScriptProcessor: vi.fn(() => processor),
    createMediaStreamSource: vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() })),
  };
  vi.stubGlobal("WebSocket", CaptureSocket);
  vi.stubGlobal("AudioContext", vi.fn(function () { return context; }));

  try {
    render(<DictationWindowApp />);
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));
    const oldSocket = CaptureSocket.instances[0]!;
    const oldReceive = oldSocket.onmessage!;
    act(() => oldSocket.receive({ type: "service.ready" }));
    mock.snapshot.mockReturnValueOnce(details.promise);
    deliver({ revision: retired.revision, takeId: retired.takeId, action: "outcome" });
    expect(oldSocket.close).toHaveBeenCalledTimes(1);
    expect(oldSocket.readyState).toBe(CaptureSocket.CLOSED);

    await act(async () => details.resolve(retired));
    expect(screen.getByRole("alert")).toHaveTextContent("The previous capture ended.");
    deliver({ revision: 3, takeId: 1, action: "idle" });
    await act(async () => permission.resolve(oldMic.stream));
    expect(oldMic.track.stop).toHaveBeenCalledTimes(1);
    expect(oldSocket.send).not.toHaveBeenCalled();
    expect(context.createScriptProcessor).not.toHaveBeenCalled();

    deliver({ revision: 4, takeId: 4, action: "start" });
    await waitFor(() => expect(context.createScriptProcessor).toHaveBeenCalledTimes(1));
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(CaptureSocket.instances).toHaveLength(2);
    const freshSocket = CaptureSocket.instances[1]!;
    act(() => {
      freshSocket.receive({ type: "service.ready" });
      processor.onaudioprocess?.({
        inputBuffer: { getChannelData: () => new Float32Array(1_024).fill(0.25) },
      } as unknown as AudioProcessingEvent);
      freshSocket.receive({ type: "transcript.piece", id: "fresh", start_ms: 0, text: "Fresh take", final: true });
      oldReceive(new MessageEvent("message", {
        data: JSON.stringify({ type: "service.error", message: "Late failure from the retired capture." }),
      }));
    });
    expect(preview()).toHaveTextContent("Fresh take");
    deliver({ revision: 5, takeId: 4, action: "stop" });
    expect(freshMic.track.stop).toHaveBeenCalledTimes(1);
    expect(freshSocket.send.mock.calls.map(([frame]) => JSON.parse(frame).type)).toEqual([
      "input_audio_buffer.append", "input_audio_buffer.commit",
    ]);
    await act(async () => freshSocket.receive({ type: "transcript.flushed", until_ms: 64 }));
    expect(mock.complete).toHaveBeenCalledExactlyOnceWith(4, { kind: "completed", text: "Fresh take" });
    expect(oldSocket.send).not.toHaveBeenCalled();
  } finally {
    cleanup();
    if (originalMedia) Object.defineProperty(navigator, "mediaDevices", originalMedia);
    else Reflect.deleteProperty(navigator, "mediaDevices");
    vi.unstubAllGlobals();
  }
});

it("keeps a failed outcome notice dismissible at its real revision and allows snapshot retry", async () => {
  render(<DictationWindowApp />);
  await waitFor(() => expect(mock.start).toHaveBeenCalledTimes(1));

  deliver({ revision: 2, takeId: 1, action: "stop" });
  await act(async () => mock.options?.onComplete("已复制的文字。", "completed"));
  expect(mock.complete).toHaveBeenCalledExactlyOnceWith(1, { kind: "completed", text: "已复制的文字。" });

  // Delayed native hide failure publishes outcome notice at revision 4, and the first snapshot read fails.
  const delayedOutcome = outcomeRequest({
    kind: "copied_presentation_failed",
    copied: true,
    text: "已复制的文字。",
    message: "已复制到剪贴板，但无法自动收起窗口。",
  }, 4);
  mock.snapshot.mockRejectedValueOnce(new Error("ipc busy")).mockResolvedValueOnce(delayedOutcome);
  deliver({ revision: 4, takeId: 1, action: "outcome" });
  expect(await screen.findByRole("alert")).toHaveTextContent("无法读取语音输入结果。");

  // Dismissing while details failed to load targets revision 4, not obsolete revision 2.
  fireEvent.click(screen.getByRole("button", { name: "Dismiss voice input" }));
  expect(mock.stopWindow).toHaveBeenCalledExactlyOnceWith(1, 4);

  // Redelivering revision 4 (or retrying snapshot read) recovers the outcome with no second clipboard write.
  deliver({ revision: 4, takeId: 1, action: "outcome" });
  expect(await screen.findByRole("alert")).toHaveTextContent("已复制到剪贴板，但无法自动收起窗口。");
  expect(screen.queryByRole("button", { name: "重试复制" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重试读取" })).not.toBeInTheDocument();
  expect(mock.complete).toHaveBeenCalledTimes(1);

  // A higher revision supersedes an in-flight outcome read.
  const lateRead = deferred<DictationRequest>();
  mock.snapshot.mockReturnValueOnce(lateRead.promise);
  deliver({ revision: 5, takeId: 1, action: "outcome" });
  deliver({ revision: 6, takeId: 2, action: "start" });
  await act(async () => lateRead.resolve(outcomeRequest({ kind: "error", copied: false, message: "Service disconnected" }, 5)));
  expect(mock.start).toHaveBeenCalledTimes(2);
  expect(preview()).toHaveTextContent("正在启动…");
});
