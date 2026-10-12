import type { TranscribeAgainStatus } from "@gunther/contracts";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TranscribeAgainButton, TranscribeAgainLine, useTranscribeAgain } from "./TranscribeAgain";

const api = vi.hoisted(() => ({ transcribeAgainStatus: vi.fn(), transcribeAgain: vi.fn() }));
vi.mock("../../api", () => ({ knowledgeApi: api }));

const status = (overrides: Partial<TranscribeAgainStatus> = {}): TranscribeAgainStatus => ({ state: "idle", heardMs: 0, totalMs: 0, error: null, ...overrides });

function Probe({ onDone }: { onDone?: () => void }) {
  const again = useTranscribeAgain("rec_1", onDone);
  return <><TranscribeAgainButton again={again} /><TranscribeAgainLine again={again} /></>;
}

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

describe("Transcribe again", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); api.transcribeAgainStatus.mockReset(); api.transcribeAgain.mockReset(); });
  afterEach(() => vi.useRealTimers());

  it("is a button and nothing else until pressed", async () => {
    api.transcribeAgainStatus.mockResolvedValue(status());
    render(<Probe />);
    await flush();
    expect(screen.getByRole("button", { name: "Transcribe again" })).toBeEnabled();
    expect(screen.queryByRole("status")).toBeNull();
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(api.transcribeAgainStatus).toHaveBeenCalledTimes(1);
  });

  it("starts, shows how far it is, and brings the new transcript in when done", async () => {
    const onDone = vi.fn();
    api.transcribeAgainStatus
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce(status({ state: "running", heardMs: 720_000, totalMs: 2_820_000 }))
      .mockResolvedValueOnce(status({ state: "running", heardMs: 2_820_000, totalMs: 2_820_000 }))
      .mockResolvedValue(status({ state: "done", heardMs: 2_820_000, totalMs: 2_820_000 }));
    api.transcribeAgain.mockResolvedValue(status({ state: "waiting" }));
    render(<Probe onDone={onDone} />);
    await flush();
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Transcribe again" }));
    await flush();
    expect(api.transcribeAgain).toHaveBeenCalledWith("rec_1");
    expect(screen.getByRole("status")).toHaveTextContent("Transcribing again… 12 of 47 min heard");
    expect(screen.getByRole("button", { name: "Transcribing…" })).toBeDisabled();
    await act(async () => { vi.advanceTimersByTime(2_000); });
    await flush();
    expect(screen.getByRole("status")).toHaveTextContent("Making paragraphs…");
    await act(async () => { vi.advanceTimersByTime(2_000); });
    await flush();
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "Transcribe again" })).toBeEnabled();
  });

  it("says why it can't start", async () => {
    api.transcribeAgainStatus.mockResolvedValue(status());
    api.transcribeAgain.mockRejectedValue(new Error("Set a Recording model in Settings → Transcription first."));
    render(<Probe />);
    await flush();
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Transcribe again" }));
    await flush();
    expect(screen.getByRole("status")).toHaveTextContent("Set a Recording model in Settings → Transcription first.");
  });

  it("shows a failure, and the button can be pressed again", async () => {
    api.transcribeAgainStatus.mockResolvedValue(status({ state: "failed", error: "Qwen answered 401: Invalid API key" }));
    render(<Probe />);
    await flush();
    expect(screen.getByRole("status")).toHaveTextContent("Couldn’t transcribe again: Qwen answered 401: Invalid API key");
    expect(screen.getByRole("button", { name: "Transcribe again" })).toBeEnabled();
  });

  it("follows a job that was already running when the page opened, but not an old done one", async () => {
    const onDone = vi.fn();
    api.transcribeAgainStatus.mockResolvedValueOnce(status({ state: "waiting" })).mockResolvedValue(status({ state: "done" }));
    const { unmount } = render(<Probe onDone={onDone} />);
    await flush();
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for another recording to finish transcribing…");
    await act(async () => { vi.advanceTimersByTime(2_000); });
    await flush();
    expect(onDone).toHaveBeenCalledTimes(1);
    unmount();

    const later = vi.fn();
    render(<Probe onDone={later} />);
    await flush();
    expect(later).not.toHaveBeenCalled();
  });
});
