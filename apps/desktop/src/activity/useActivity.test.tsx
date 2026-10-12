import type { ActivityList } from "@gunther/contracts";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { activityItem } from "./fixtures";
import { ActivityProvider, RETRY_DELAYS_MS, useActivity } from "./useActivity";

const list = (version: number, ...ids: string[]): ActivityList => ({ version, items: ids.map((id) => activityItem({ id })) });
const flush = () => act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); });

/** One pending request the test answers by hand. */
function hold() {
  const calls: Array<{ after: number | undefined; resolve: (value: ActivityList) => void; reject: (reason: unknown) => void }> = [];
  vi.spyOn(knowledgeApi, "activity").mockImplementation((after) => new Promise<ActivityList>((resolve, reject) => { calls.push({ after, resolve, reject }); }));
  return calls;
}

let seen: ReturnType<typeof useActivity>[] = [];
function Reader() {
  seen.push(useActivity());
  return null;
}
const latest = () => seen[seen.length - 1]!;

describe("useActivity", () => {
  beforeEach(() => { vi.useFakeTimers(); seen = []; });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("asks again with the version as soon as each answer arrives", async () => {
    const calls = hold();
    render(<ActivityProvider><Reader /></ActivityProvider>);
    await flush();
    expect(calls.map((call) => call.after)).toEqual([undefined]);
    await act(async () => { calls[0]!.resolve(list(3, "answer:s")); });
    await flush();
    expect(latest().items.map((item) => item.id)).toEqual(["answer:s"]);
    expect(calls.map((call) => call.after)).toEqual([undefined, 3]);
  });

  it("is one loop for any number of readers", async () => {
    const calls = hold();
    render(<ActivityProvider><Reader /><Reader /><Reader /></ActivityProvider>);
    await flush();
    expect(calls).toHaveLength(1);
  });

  it("backs off 2 s, 5 s, then 10 s after errors, and starts over after an answer", async () => {
    const calls = hold();
    render(<ActivityProvider><Reader /></ActivityProvider>);
    await flush();
    for (const [index, delay] of [...RETRY_DELAYS_MS, RETRY_DELAYS_MS[2]!].entries()) {
      await act(async () => { calls[index]!.reject(new Error("down")); });
      await flush();
      expect(calls).toHaveLength(index + 1);
      await act(async () => { vi.advanceTimersByTime(delay - 1); });
      expect(calls).toHaveLength(index + 1);
      await act(async () => { vi.advanceTimersByTime(1); });
      await flush();
      expect(calls).toHaveLength(index + 2);
    }
    await act(async () => { calls[4]!.resolve(list(1)); });
    await flush();
    await act(async () => { calls[5]!.reject(new Error("down")); });
    await flush();
    await act(async () => { vi.advanceTimersByTime(RETRY_DELAYS_MS[0]!); });
    await flush();
    expect(calls).toHaveLength(7);
  });

  it("marks the service unreachable only when it cannot be reached at all", async () => {
    const calls = hold();
    render(<ActivityProvider><Reader /></ActivityProvider>);
    await flush();
    await act(async () => { calls[0]!.reject(Object.assign(new Error("x"), { name: "ServiceUnavailableError" })); });
    await flush();
    expect(latest().reachable).toBe(false);
    await act(async () => { vi.advanceTimersByTime(2_000); });
    await flush();
    await act(async () => { calls[1]!.resolve(list(1)); });
    await flush();
    expect(latest().reachable).toBe(true);
  });

  it("refresh asks at once, without a version, and drops the wait", async () => {
    const calls = hold();
    render(<ActivityProvider><Reader /></ActivityProvider>);
    await flush();
    await act(async () => { calls[0]!.resolve(list(7)); });
    await flush();
    expect(calls[1]!.after).toBe(7);
    act(() => latest().refresh());
    await flush();
    expect(calls).toHaveLength(3);
    expect(calls[2]!.after).toBeUndefined();
    // The dropped wait cannot overwrite what the new ask brings.
    await act(async () => { calls[2]!.resolve(list(8, "output:lib")); });
    await act(async () => { calls[1]!.resolve(list(7)); });
    await flush();
    expect(latest().items.map((item) => item.id)).toEqual(["output:lib"]);
  });

  it("refreshes when the app says something started", async () => {
    const calls = hold();
    render(<ActivityProvider><Reader /></ActivityProvider>);
    await flush();
    await act(async () => { calls[0]!.resolve(list(2)); });
    await flush();
    act(() => { window.dispatchEvent(new Event("gunther:activity-changed")); });
    await flush();
    expect(calls[2]?.after).toBeUndefined();
  });

  it("stops asking when the app closes", async () => {
    const calls = hold();
    const view = render(<ActivityProvider><Reader /></ActivityProvider>);
    await flush();
    view.unmount();
    await act(async () => { calls[0]!.resolve(list(1)); });
    await flush();
    expect(calls).toHaveLength(1);
  });
});
