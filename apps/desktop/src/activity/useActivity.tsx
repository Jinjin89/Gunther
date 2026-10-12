import type { ActivityItem, ActivityList } from "@gunther/contracts";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { knowledgeApi } from "../api";
import { ACTIVITY_CHANGED_EVENT } from "./events";

/** How long to wait before asking again after a failure: 2 s, then 5 s, then 10 s. */
export const RETRY_DELAYS_MS = [2_000, 5_000, 10_000];

interface ActivityValue {
  items: ActivityItem[];
  /** False while the service cannot be reached. */
  reachable: boolean;
  /** Ask again at once (after the app itself starts or stops something). */
  refresh: () => void;
}

const NOTHING: ActivityValue = { items: [], reachable: true, refresh: () => undefined };
const ActivityContext = createContext<ActivityValue>(NOTHING);

/**
 * The one long-poll loop for the whole app: ask for the list, and ask again with its
 * version as soon as each answer arrives (the service holds that request until
 * something changes). Mounted once, in App.
 */
export function ActivityProvider({ children }: { children: ReactNode }) {
  const [list, setList] = useState<ActivityList | null>(null);
  const [reachable, setReachable] = useState(true);
  const [refresh, setRefresh] = useState<() => void>(() => () => undefined);

  useEffect(() => {
    let alive = true;
    let version: number | undefined;
    let wake: (() => void) | null = null;
    let controller: AbortController | null = null;
    let failures = 0;

    // Forget the version so the next answer is immediate, and cut any wait short.
    const askAgain = () => {
      version = undefined;
      wake?.();
    };

    // Wait, unless something asks for an answer sooner.
    const rest = async (ms: number) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const rested = new Promise<void>((resolve) => { timer = setTimeout(resolve, ms); });
      const nudged = new Promise<void>((resolve) => { wake = resolve; });
      await Promise.race([rested, nudged]);
      clearTimeout(timer);
    };

    const run = async () => {
      while (alive) {
        const woken = new Promise<"woken">((resolve) => { wake = () => resolve("woken"); });
        controller = new AbortController();
        const asked = knowledgeApi.activity(version, controller.signal).then(
          (answer) => ({ answer }),
          (error: unknown) => ({ error }),
        );
        const outcome = await Promise.race([asked, woken]);
        if (!alive) return;
        if (outcome === "woken") {
          // The wait was cut short on purpose: drop it and ask again.
          controller.abort();
          continue;
        }
        if ("answer" in outcome) {
          failures = 0;
          const unchanged = version !== undefined && outcome.answer.version === version;
          version = outcome.answer.version;
          setReachable(true);
          setList((previous) => (previous?.version === outcome.answer.version ? previous : outcome.answer));
          // Nothing changed after the wait: pause a moment so a service that answers
          // at once (it should not) can never make this a busy loop.
          if (unchanged) await rest(1_000);
          continue;
        }
        if (outcome.error instanceof Error && outcome.error.name === "ServiceUnavailableError") setReachable(false);
        const delay = RETRY_DELAYS_MS[Math.min(failures, RETRY_DELAYS_MS.length - 1)]!;
        failures += 1;
        await rest(delay);
        version = undefined;
        if (!alive) return;
      }
    };

    const onVisible = () => { if (document.visibilityState === "visible") askAgain(); };
    window.addEventListener(ACTIVITY_CHANGED_EVENT, askAgain);
    window.addEventListener("online", askAgain);
    document.addEventListener("visibilitychange", onVisible);
    setRefresh(() => askAgain);
    void run();
    return () => {
      alive = false;
      window.removeEventListener(ACTIVITY_CHANGED_EVENT, askAgain);
      window.removeEventListener("online", askAgain);
      document.removeEventListener("visibilitychange", onVisible);
      controller?.abort();
      wake?.();
    };
  }, []);

  const value = useMemo<ActivityValue>(() => ({ items: list?.items ?? [], reachable, refresh }), [list, reachable, refresh]);
  return <ActivityContext.Provider value={value}>{children}</ActivityContext.Provider>;
}

export function useActivity(): ActivityValue {
  return useContext(ActivityContext);
}
