import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import DictationWindowApp from "./DictationWindowApp";

const mock = vi.hoisted(() => ({
  options: null as null | { onComplete: (text: string) => void; onError: (message: string) => void },
  request: null as null | ((request: { serial: number; listening: boolean }) => void),
  start: vi.fn(), stop: vi.fn(), copy: vi.fn(), finish: vi.fn(), stopWindow: vi.fn(), drag: vi.fn(),
}));
vi.mock("../services/useDictation", () => ({
  useDictation: (_update: unknown, options: NonNullable<typeof mock.options>) => {
    mock.options = options;
    return { state: "idle", error: null, transcript: "", level: 0, start: mock.start, stop: mock.stop };
  },
}));
vi.mock("./bridge", () => ({
  listenForDictation: async (handler: NonNullable<typeof mock.request>) => { mock.request = handler; return vi.fn(); },
  pendingDictationRequest: async () => ({ serial: 1, listening: true }),
  copyDictation: (text: string) => mock.copy(text),
  finishDictation: (serial: number) => mock.finish(serial),
  stopDictation: () => mock.stopWindow(),
  dragDictation: () => mock.drag(),
}));
beforeEach(() => {
  mock.options = null; mock.request = null;
  mock.start.mockReset().mockResolvedValue(undefined); mock.stop.mockReset();
  mock.copy.mockReset().mockResolvedValue(undefined); mock.finish.mockReset().mockResolvedValue(undefined);
  mock.stopWindow.mockReset().mockResolvedValue(undefined);
  mock.drag.mockReset().mockResolvedValue(undefined);
});

it("drags from the header while leaving the stop button clickable", async () => {
  render(<DictationWindowApp />);
  await waitFor(() => expect(mock.start).toHaveBeenCalledTimes(1));
  fireEvent.mouseDown(screen.getByRole("status"), { button: 0 });
  expect(mock.drag).toHaveBeenCalledTimes(1);
  fireEvent.mouseDown(screen.getByRole("button", { name: "Stop voice input" }), { button: 0 });
  expect(mock.drag).toHaveBeenCalledTimes(1);
});

it("starts once and treats the next shortcut as stop, not another open request", async () => {
  render(<DictationWindowApp />);
  await waitFor(() => expect(mock.start).toHaveBeenCalledTimes(1));
  act(() => mock.request?.({ serial: 1, listening: true }));
  expect(mock.start).toHaveBeenCalledTimes(1);
  act(() => mock.request?.({ serial: 2, listening: false }));
  expect(mock.stop).toHaveBeenCalledTimes(1);
  expect(mock.copy).not.toHaveBeenCalled();
});

it("the stop button asks the native shell to hide immediately, before ASR completion", async () => {
  render(<DictationWindowApp />);
  await waitFor(() => expect(mock.start).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Stop voice input" }));
  expect(mock.stopWindow).toHaveBeenCalledTimes(1);
  expect(mock.copy).not.toHaveBeenCalled();
  expect(screen.queryByText("Copied to clipboard")).not.toBeInTheDocument();
});

it("copies the final take in the background with no result-confirmation pane", async () => {
  render(<DictationWindowApp />);
  await waitFor(() => expect(mock.start).toHaveBeenCalledTimes(1));
  act(() => mock.request?.({ serial: 2, listening: false }));
  await act(async () => mock.options?.onComplete("完整的语音文字。"));
  expect(mock.copy).toHaveBeenCalledExactlyOnceWith("完整的语音文字。");
  expect(mock.finish).toHaveBeenCalledWith(1);
  expect(screen.queryByText("Copied to clipboard")).not.toBeInTheDocument();
});

it("can begin a new take after an earlier stop is still flushing", async () => {
  render(<DictationWindowApp />);
  await waitFor(() => expect(mock.start).toHaveBeenCalledTimes(1));
  act(() => {
    mock.request?.({ serial: 2, listening: false });
    mock.request?.({ serial: 3, listening: true });
  });
  expect(mock.start).toHaveBeenCalledTimes(1);
  await act(async () => mock.options?.onComplete("上一段。"));
  expect(mock.finish).toHaveBeenCalledWith(1);
  expect(mock.start).toHaveBeenCalledTimes(2);
});

it("does not overwrite the clipboard on empty speech or claim a failed write succeeded", async () => {
  render(<DictationWindowApp />);
  await waitFor(() => expect(mock.start).toHaveBeenCalledTimes(1));
  act(() => mock.options?.onComplete("  "));
  expect(mock.copy).not.toHaveBeenCalled();
  mock.copy.mockRejectedValue(new Error("clipboard unavailable"));
  await act(async () => mock.options?.onComplete("保留下来的文字。"));
  expect(screen.getByRole("alert")).toHaveTextContent("Could not copy");
  expect(screen.queryByText("Copied to clipboard")).not.toBeInTheDocument();
});
