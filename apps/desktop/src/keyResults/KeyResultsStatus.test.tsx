import type { KeyResultJob } from "@gunther/contracts";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { announceModelsChanged } from "../models/askModel";
import { KEY_RESULTS_CHANGED_EVENT, keyResultsChanged } from "./events";
import { KeyResultsStatus } from "./KeyResultsStatus";

const job = (state: KeyResultJob["state"], changes: Partial<KeyResultJob> = {}): KeyResultJob => ({
  id: "key-results-job", originKind: "output", originId: "output", state, error: null, keptCount: 0, keptIds: [], ...changes,
});

it("refreshes a model-blocked job and polls it through completion", async () => {
  vi.useFakeTimers();
  try {
    const read = vi.spyOn(knowledgeApi, "keyResultOrigin")
      .mockResolvedValueOnce(job("running"))
      .mockResolvedValueOnce(job("done", { keptCount: 1, keptIds: ["one"] }));
    render(<KeyResultsStatus baseId="library" initial={job("needs_model")} />);
    expect(read).not.toHaveBeenCalled();
    await act(async () => announceModelsChanged());
    expect(screen.getByText("Checking key results…")).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(2_500));
    expect(screen.getByText("✓ 1 key result kept")).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(read).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});

it("resets the Undo confirmation by library and action errors by job", async () => {
  vi.spyOn(knowledgeApi, "undoKeyResults").mockResolvedValue(job("done"));
  vi.spyOn(knowledgeApi, "retryKeyResults").mockRejectedValue(new Error("Retry could not start."));
  const { rerender } = render(<KeyResultsStatus baseId="library" initial={job("done", { keptCount: 1, keptIds: ["one"] })} />);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(await screen.findByText("Key results undone")).toBeVisible();

  rerender(<KeyResultsStatus baseId="other-library" initial={job("done")} />);
  expect(screen.queryByRole("status")).toBeNull();

  rerender(<KeyResultsStatus baseId="other-library" initial={job("failed")} />);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Retry could not start.");

  rerender(<KeyResultsStatus baseId="other-library" initial={job("failed", { id: "new-job", originId: "new-output" })} />);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
});

it("ignores a pending retry response after reopening the same job", async () => {
  let finish!: (result: KeyResultJob) => void;
  vi.spyOn(knowledgeApi, "retryKeyResults").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const failed = job("failed");
  const { rerender } = render(<KeyResultsStatus baseId="library" initial={failed} />);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
  rerender(<KeyResultsStatus baseId="other-library" initial={job("done")} />);
  rerender(<KeyResultsStatus baseId="library" initial={failed} />);

  await act(async () => finish(job("running")));
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
});

it("keeps a retry running and settles polling when a fresh failed snapshot arrives mid-retry", async () => {
  vi.useFakeTimers();
  try {
    let finish!: (result: KeyResultJob) => void;
    vi.spyOn(knowledgeApi, "retryKeyResults").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const read = vi.spyOn(knowledgeApi, "keyResultOrigin")
      .mockResolvedValueOnce(job("running"))
      .mockResolvedValueOnce(job("done", { keptCount: 2, keptIds: ["one", "two"] }));
    const { rerender } = render(<KeyResultsStatus baseId="library" initial={job("failed")} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();

    // Artifact refetches the same failed job while retry is still in flight.
    rerender(<KeyResultsStatus baseId="library" initial={job("failed")} />);
    await act(async () => finish(job("running")));

    expect(screen.getByText("Checking key results…")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(read).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(2_500));
    expect(screen.getByText("✓ 2 key results kept")).toBeVisible();
    expect(read).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});

it("retries failed snapshot reconciliation and resumes after hiding the view", async () => {
  vi.useFakeTimers();
  try {
    const read = vi.spyOn(knowledgeApi, "keyResultOrigin")
      .mockResolvedValueOnce(job("failed"))
      .mockRejectedValueOnce(new Error("Temporary network error"))
      .mockResolvedValueOnce(job("running"))
      .mockResolvedValueOnce(job("done", { keptCount: 2, keptIds: ["one", "two"] }))
      .mockImplementationOnce((_baseId, _origin, signal) => new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }))
      .mockResolvedValueOnce(job("done", { keptCount: 1, keptIds: ["one"] }));

    const { rerender } = render(<KeyResultsStatus baseId="library" initial={job("running")} visible />);
    await act(async () => vi.advanceTimersByTimeAsync(2_500));
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();

    // Fresh running snapshot arrives, first reconciliation read fails transiently, delayed retry recovers.
    rerender(<KeyResultsStatus baseId="library" initial={job("running")} visible />);
    await act(async () => Promise.resolve());
    expect(read).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();

    await act(async () => vi.advanceTimersByTimeAsync(2_500));
    expect(screen.getByText("Checking key results…")).toBeVisible();
    expect(read).toHaveBeenCalledTimes(3);

    await act(async () => vi.advanceTimersByTimeAsync(2_500));
    expect(screen.getByText("✓ 2 key results kept")).toBeVisible();
    expect(read).toHaveBeenCalledTimes(4);

    // Fresh snapshot reports keptCount 1; hiding the view aborts the in-flight read; reopening resumes it.
    const reduced = job("done", { keptCount: 1, keptIds: ["one"] });
    rerender(<KeyResultsStatus baseId="library" initial={reduced} visible />);
    expect(read).toHaveBeenCalledTimes(5);
    rerender(<KeyResultsStatus baseId="library" initial={reduced} visible={false} />);
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(read).toHaveBeenCalledTimes(5);

    rerender(<KeyResultsStatus baseId="library" initial={reduced} visible />);
    await act(async () => Promise.resolve());
    expect(screen.getByText("✓ 1 key result kept")).toBeVisible();
    expect(read).toHaveBeenCalledTimes(6);
  } finally {
    vi.useRealTimers();
  }
});

it("supersedes an in-flight reconciliation read when a newer snapshot arrives", async () => {
  vi.useFakeTimers();
  try {
    let finishFirst!: (result: KeyResultJob) => void;
    let finishSecond!: (result: KeyResultJob) => void;
    const signals: AbortSignal[] = [];
    const read = vi.spyOn(knowledgeApi, "keyResultOrigin")
      .mockResolvedValueOnce(job("done", { keptCount: 3, keptIds: ["one", "two", "three"] }))
      .mockImplementationOnce((_baseId, _origin, signal) => {
        signals.push(signal);
        return new Promise((resolve) => { finishFirst = resolve; });
      })
      .mockImplementationOnce((_baseId, _origin, signal) => {
        signals.push(signal);
        return new Promise((resolve) => { finishSecond = resolve; });
      });

    const { rerender } = render(<KeyResultsStatus baseId="library" initial={job("running")} visible />);
    await act(async () => vi.advanceTimersByTimeAsync(2_500));
    expect(screen.getByText("✓ 3 key results kept")).toBeVisible();

    // Snapshot 2 starts reconciliation read #1.
    rerender(<KeyResultsStatus baseId="library" initial={job("done", { keptCount: 2, keptIds: ["one", "two"] })} visible />);
    expect(read).toHaveBeenCalledTimes(2);
    expect(signals[0]?.aborted).toBe(false);

    // Snapshot 1 arrives before read #1 resolves; read #1 is aborted and read #2 starts.
    rerender(<KeyResultsStatus baseId="library" initial={job("done", { keptCount: 1, keptIds: ["one"] })} visible />);
    expect(read).toHaveBeenCalledTimes(3);
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);

    // Even if the superseded response resolves, it is ignored and read #2 wins.
    await act(async () => finishFirst(job("done", { keptCount: 2, keptIds: ["one", "two"] })));
    expect(screen.getByText("✓ 3 key results kept")).toBeVisible();

    await act(async () => finishSecond(job("done", { keptCount: 1, keptIds: ["one"] })));
    expect(screen.getByText("✓ 1 key result kept")).toBeVisible();
  } finally {
    vi.useRealTimers();
  }
});

it("keeps polling across consecutive running responses and emits completion once", async () => {
  vi.useFakeTimers();
  const events = vi.fn();
  window.addEventListener(KEY_RESULTS_CHANGED_EVENT, events);
  try {
    const read = vi.spyOn(knowledgeApi, "keyResultOrigin")
      .mockResolvedValueOnce(job("running"))
      .mockResolvedValueOnce(job("running"))
      .mockResolvedValueOnce(job("done", { keptCount: 2, keptIds: ["one", "two"] }));

    render(<KeyResultsStatus baseId="library" initial={job("running")} visible />);
    await act(async () => vi.advanceTimersByTimeAsync(2_500));
    await act(async () => vi.advanceTimersByTimeAsync(2_500));
    expect(screen.getByText("Checking key results…")).toBeVisible();
    expect(read).toHaveBeenCalledTimes(2);
    expect(events).not.toHaveBeenCalled();

    await act(async () => vi.advanceTimersByTimeAsync(2_500));
    expect(screen.getByText("✓ 2 key results kept")).toBeVisible();
    expect(read).toHaveBeenCalledTimes(3);
    expect(events).toHaveBeenCalledTimes(1);
  } finally {
    window.removeEventListener(KEY_RESULTS_CHANGED_EVENT, events);
    vi.useRealTimers();
  }
});

it("invalidates an event-triggered refresh when a materially newer snapshot arrives before the first local read", async () => {
  vi.useFakeTimers();
  try {
    let finishRefresh!: (result: KeyResultJob) => void;
    let signalRef: AbortSignal | undefined;
    const read = vi.spyOn(knowledgeApi, "keyResultOrigin").mockImplementationOnce((_baseId, _origin, signal) => {
      signalRef = signal;
      return new Promise((resolve) => { finishRefresh = resolve; });
    });

    const { rerender } = render(<KeyResultsStatus baseId="library" initial={job("done", { keptCount: 3, keptIds: ["one", "two", "three"] })} visible />);
    await act(async () => keyResultsChanged("library", true));
    expect(read).toHaveBeenCalledTimes(1);
    expect(signalRef?.aborted).toBe(false);

    rerender(<KeyResultsStatus baseId="library" initial={job("done", { keptCount: 1, keptIds: ["one"] })} visible />);
    expect(signalRef?.aborted).toBe(true);
    expect(screen.getByText("✓ 1 key result kept")).toBeVisible();

    await act(async () => finishRefresh(job("done", { keptCount: 2, keptIds: ["one", "two"] })));
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(screen.getByText("✓ 1 key result kept")).toBeVisible();
    expect(read).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});
