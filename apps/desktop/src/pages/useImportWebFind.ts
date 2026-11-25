import type { ConversationCitation } from "@gunther/contracts";
import { useEffect, useMemo, useRef, useState } from "react";
import { knowledgeApi } from "../api";
import type { SourceUpdate } from "../trash/trash";

export interface WebFind {
  title: string;
  url?: string | null | undefined;
  librarySourceId?: string | null | undefined;
}

type ImportState = { status: "idle" | "importing" }
  | { status: "imported"; sourceId: string }
  | { status: "error"; message: string };

export interface WebFindImports {
  status: (find: WebFind) => ImportState;
  importOne: (find: WebFind) => Promise<void>;
  importAll: (finds: WebFind[]) => Promise<void>;
  openSource: (id: string) => void;
  batching: boolean;
  busy: boolean;
}

export function webFindOf(citation: ConversationCitation): WebFind {
  return { title: citation.sourceTitle, url: citation.url, librarySourceId: citation.librarySourceId };
}

export function webFindKey(url: WebFind["url"]): string | null {
  if (!url) return null;
  try {
    const link = new URL(url);
    if (link.protocol !== "https:" && link.protocol !== "http:") return null;
    link.hash = "";
    return link.href;
  } catch {
    return null;
  }
}

export function useImportWebFind({ baseId, workspaceId, finds, onNotify, onOpenSource }: {
  baseId: string | undefined;
  workspaceId: string | null | undefined;
  finds: WebFind[];
  onNotify: ((message: string) => void) | undefined;
  onOpenSource: ((id: string) => void) | undefined;
}): WebFindImports | undefined {
  const [entries, setEntries] = useState(new Map<string, ImportState>());
  const current = useRef(entries);
  const [batching, setBatching] = useState(false);
  const batchInFlight = useRef(false);
  const sourceChanges = useRef(0);
  const trashedAt = useRef(new Map<string, number>());
  const liveMatches = useMemo(() => {
    const matches = new Map<string, string>();
    for (const find of finds) {
      const key = webFindKey(find.url);
      if (key && find.librarySourceId) matches.set(key, find.librarySourceId);
    }
    return matches;
  }, [finds]);

  useEffect(() => {
    if (!baseId) return;
    const refresh = (event: Event) => {
      const sourceId = (event as CustomEvent<SourceUpdate>).detail?.trashedSourceId;
      if (!sourceId) return;
      trashedAt.current.set(sourceId, ++sourceChanges.current);
      const remaining = new Map([...current.current].filter(([, entry]) =>
        entry.status !== "imported" || entry.sourceId !== sourceId));
      if (remaining.size === current.current.size) return;
      current.current = remaining;
      setEntries(remaining);
    };
    window.addEventListener("gunther:sources-updated", refresh);
    return () => window.removeEventListener("gunther:sources-updated", refresh);
  }, [baseId]);

  if (!baseId || !onOpenSource) return undefined;

  const statusOf = (find: WebFind, saved: ReadonlyMap<string, ImportState>): ImportState => {
    const key = webFindKey(find.url);
    const entry = key ? saved.get(key) : undefined;
    const sourceId = find.librarySourceId ?? (key ? liveMatches.get(key) : undefined);
    return sourceId ? { status: "imported", sourceId } : entry ?? { status: "idle" };
  };
  const update = (key: string, status: ImportState) => {
    current.current = new Map(current.current).set(key, status);
    setEntries(current.current);
  };
  const capture = async (find: WebFind) => {
    const key = webFindKey(find.url);
    const prior = statusOf(find, current.current);
    if (!key || !find.url || prior.status === "importing" || prior.status === "imported") return;
    const startedAt = sourceChanges.current;
    const title = find.title.trim();
    const clipped = title.slice(0, (title.codePointAt(159) ?? 0) > 0xffff ? 159 : 160);
    update(key, { status: "importing" });
    try {
      const result = await knowledgeApi.captureWeb({
        url: find.url, ...(clipped ? { title: clipped } : {}), knowledgeBaseId: baseId,
        clientCaptureId: crypto.randomUUID(),
      }, workspaceId ?? undefined);
      const sourceId = result.importResult.source.id;
      update(key, (trashedAt.current.get(sourceId) ?? 0) > startedAt
        ? { status: "idle" } : { status: "imported", sourceId });
      window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
      if (result.note) onNotify?.(result.note);
    } catch (reason) {
      update(key, { status: "error", message: reason instanceof Error ? reason.message : "The page could not be added." });
    }
  };
  const loading = (saved: ReadonlyMap<string, ImportState>) => [...saved.values()].some((entry) => entry.status === "importing");
  return {
    status: (find) => statusOf(find, entries),
    openSource: onOpenSource,
    batching,
    busy: batching || loading(entries),
    importOne: async (find) => { if (!batchInFlight.current) await capture(find); },
    importAll: async (pending) => {
      if (batchInFlight.current || loading(current.current)) return;
      batchInFlight.current = true;
      setBatching(true);
      try {
        for (const find of pending) await capture(find);
      } finally {
        batchInFlight.current = false;
        setBatching(false);
      }
    },
  };
}
