import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import {
  LectureRecorder,
  MICROPHONE_PERMISSION_TIMEOUT_MS,
  MicrophonePermissionTimeoutError,
  requestMicrophoneStream,
} from "./LectureRecorder";

const originalMediaDevices = Object.getOwnPropertyDescriptor(window.navigator, "mediaDevices");

vi.mock("../services/recordingSpool", async () => {
  const actual = await vi.importActual<typeof import("../services/recordingSpool")>(
    "../services/recordingSpool",
  );
  return {
    ...actual,
    assertRecordingSpoolAvailable: vi.fn().mockResolvedValue(undefined),
  };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function streamWithStop(stop = vi.fn()) {
  return {
    stream: { getTracks: () => [{ stop }] } as unknown as MediaStream,
    stop,
  };
}

function setMediaDevices(getUserMedia: ReturnType<typeof vi.fn>) {
  Object.defineProperty(window.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
}

function renderRecorder() {
  return render(
    <LectureRecorder
      title="Bioinformatics lecture"
      knowledgeBaseId="biology"
      workspaceId="wsp_primary"
      onKnowledgeContent={vi.fn()}
    />,
  );
}

describe("bounded microphone permission requests", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    if (originalMediaDevices) {
      Object.defineProperty(window.navigator, "mediaDevices", originalMediaDevices);
    } else {
      Reflect.deleteProperty(window.navigator, "mediaDevices");
    }
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("times out a silent permission request and stops every track from a late grant", async () => {
    const pending = deferred<MediaStream>();
    const late = streamWithStop();
    const getUserMedia = vi.fn().mockReturnValue(pending.promise);

    const request = requestMicrophoneStream(
      { getUserMedia } as Pick<MediaDevices, "getUserMedia">,
      { audio: true },
      { timeoutMs: 1_000 },
    );
    const rejection = expect(request).rejects.toBeInstanceOf(MicrophonePermissionTimeoutError);

    await act(async () => {
      vi.advanceTimersByTime(1_000);
    });
    await rejection;

    pending.resolve(late.stream);
    await act(async () => {
      await Promise.resolve();
    });
    expect(late.stop).toHaveBeenCalledTimes(1);
  });

  it("shows macOS recovery guidance, exposes retry, and cleans up both old and unmounted requests", async () => {
    const firstRequest = deferred<MediaStream>();
    const retryRequest = deferred<MediaStream>();
    const firstLateStream = streamWithStop();
    const retryLateStream = streamWithStop();
    const getUserMedia = vi.fn()
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(retryRequest.promise);
    setMediaDevices(getUserMedia);
    vi.spyOn(knowledgeApi, "health").mockResolvedValue({
      status: "ok",
      extractionMode: "local",
      webSearchMode: "not_configured",
      transcriptionMode: "sensevoice_local",
      transcriptionProvider: "sensevoice",
      transcriptionModel: "sensevoice-small",
      transcriptionDelay: "medium",
      transcriptionLanguages: ["en", "zh-cn"],
      summaryMode: "off",
    });

    const view = renderRecorder();
    fireEvent.click(screen.getByRole("button", { name: "Start recording" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Requesting access" })).toBeDisabled();

    await act(async () => {
      vi.advanceTimersByTime(MICROPHONE_PERMISSION_TIMEOUT_MS);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "System Settings → Privacy & Security → Microphone",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("permission dialog behind this window");
    const retry = screen.getByRole("button", { name: "Retry microphone" });

    fireEvent.click(retry);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getUserMedia).toHaveBeenCalledTimes(2);

    firstRequest.resolve(firstLateStream.stream);
    await act(async () => {
      await Promise.resolve();
    });
    expect(firstLateStream.stop).toHaveBeenCalledTimes(1);

    view.unmount();
    retryRequest.resolve(retryLateStream.stream);
    await act(async () => {
      await Promise.resolve();
    });
    expect(retryLateStream.stop).toHaveBeenCalledTimes(1);
  });
});
