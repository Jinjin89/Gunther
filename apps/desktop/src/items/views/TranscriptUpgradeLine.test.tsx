import type { TranscriptUpgradeStatus } from "@gunther/contracts";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptUpgradeLine, useTranscriptUpgrade } from "./TranscriptUpgradeLine";

const api = vi.hoisted(() => ({ transcriptUpgrade: vi.fn(), retryTranscriptUpgrade: vi.fn() }));
vi.mock("../../api", () => ({ knowledgeApi: api }));

const status = (overrides: Partial<TranscriptUpgradeStatus> = {}): TranscriptUpgradeStatus => ({ state: "none", reason: null, ahead: 0, heardMs: 0, totalMs: 0, error: null, ...overrides });

function Probe({ onUpgraded }: { onUpgraded?: () => void }) {
  const upgrade = useTranscriptUpgrade("rec_1", onUpgraded);
  return <TranscriptUpgradeLine upgrade={upgrade} />;
}

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

describe("TranscriptUpgradeLine", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); api.transcriptUpgrade.mockReset(); api.retryTranscriptUpgrade.mockReset(); });
  afterEach(() => vi.useRealTimers());

  it.each([
    [status({ state: "running", heardMs: 720000, totalMs: 2820000 }), "Improving this transcript… 12 of 47 min"],
    [status({ state: "waiting", reason: "queue", ahead: 2 }), "Improving this transcript after 2 other recordings."],
    [status({ state: "waiting", reason: "queue", ahead: 1 }), "Improving this transcript after 1 other recording."],
    [status({ state: "waiting", reason: "model" }), "Will improve this transcript once a Recording model is set in Settings → Transcription."],
    [status({ state: "waiting", reason: "service" }), "Will improve this transcript when the transcription service answers again."],
    [status({ state: "waiting", reason: "recording" }), "Waiting until the current recording ends."],
  ])("says %# in plain words", async (value, text) => {
    api.transcriptUpgrade.mockResolvedValue(value);
    render(<Probe />);
    await flush();
    expect(screen.getByRole("status")).toHaveTextContent(text);
  });

  it("shows the error with Retry, and Retry calls the API then looks again", async () => {
    api.transcriptUpgrade.mockResolvedValueOnce(status({ state: "failed", error: "Qwen: 401 Invalid API key" })).mockResolvedValue(status({ state: "running", heardMs: 0, totalMs: 600000 }));
    api.retryTranscriptUpgrade.mockResolvedValue(status({ state: "waiting", reason: "queue" }));
    render(<Probe />);
    await flush();
    expect(screen.getByRole("status")).toHaveTextContent("Couldn’t improve this transcript: Qwen: 401 Invalid API key · Retry");
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Retry" }));
    await flush();
    expect(api.retryTranscriptUpgrade).toHaveBeenCalledWith("rec_1");
    expect(api.transcriptUpgrade).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("status")).toHaveTextContent("0 of 10 min");
  });

  it("renders nothing and stops polling for none", async () => {
    api.transcriptUpgrade.mockResolvedValue(status());
    const { container } = render(<Probe />);
    await flush();
    await act(async () => { vi.advanceTimersByTime(120000); });
    expect(container).toBeEmptyDOMElement();
    expect(api.transcriptUpgrade).toHaveBeenCalledTimes(1);
  });

  it("polls every 5 s while running, then calls onUpgraded once when it is done", async () => {
    const onUpgraded = vi.fn();
    api.transcriptUpgrade.mockResolvedValueOnce(status({ state: "running", heardMs: 60000, totalMs: 120000 })).mockResolvedValue(status({ state: "done" }));
    const { container } = render(<Probe onUpgraded={onUpgraded} />);
    await flush();
    expect(onUpgraded).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(5000); });
    await flush();
    expect(onUpgraded).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(120000); });
    expect(api.transcriptUpgrade).toHaveBeenCalledTimes(2);
    expect(onUpgraded).toHaveBeenCalledTimes(1);
    expect(container).toBeEmptyDOMElement();
  });

  it("reloads when a waiting recording is already done by the next look", async () => {
    const onUpgraded = vi.fn();
    api.transcriptUpgrade.mockResolvedValueOnce(status({ state: "waiting", reason: "queue", ahead: 0 })).mockResolvedValue(status({ state: "done" }));
    render(<Probe onUpgraded={onUpgraded} />);
    await flush();
    await act(async () => { vi.advanceTimersByTime(30000); });
    await flush();
    expect(onUpgraded).toHaveBeenCalledTimes(1);
  });

  it("keeps looking after a failure, since it is tried again by itself", async () => {
    api.transcriptUpgrade.mockResolvedValueOnce(status({ state: "failed", error: "Qwen: timeout" })).mockResolvedValue(status({ state: "running", heardMs: 0, totalMs: 600000 }));
    render(<Probe />);
    await flush();
    await act(async () => { vi.advanceTimersByTime(30000); });
    await flush();
    expect(api.transcriptUpgrade).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("status")).toHaveTextContent("Improving this transcript… 0 of 10 min");
  });

  it("polls every 30 s while waiting and stops on unmount", async () => {
    api.transcriptUpgrade.mockResolvedValue(status({ state: "waiting", reason: "queue", ahead: 1 }));
    const { unmount } = render(<Probe />);
    await flush();
    await act(async () => { vi.advanceTimersByTime(29000); });
    expect(api.transcriptUpgrade).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(1500); });
    expect(api.transcriptUpgrade).toHaveBeenCalledTimes(2);
    unmount();
    await act(async () => { vi.advanceTimersByTime(120000); });
    expect(api.transcriptUpgrade).toHaveBeenCalledTimes(2);
  });
});
