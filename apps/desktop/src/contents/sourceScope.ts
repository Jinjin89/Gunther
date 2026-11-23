import type { OutputScope } from "@gunther/contracts";
import { useEffect, useState } from "react";
import { persistentStorage } from "../persistentStorage";

export type SourceSelection = { mode: "all" } | { mode: "selected"; sourceIds: string[] };
export const allSources: SourceSelection = { mode: "all" };

export function useSourceSelection(baseId: string) {
  const key = `gunther:library-sources:${baseId}`;
  const [selection, setSelection] = useState<SourceSelection>(() => {
    try {
      const stored = JSON.parse(persistentStorage.getItem(key) ?? "null");
      if (stored?.mode === "selected" && Array.isArray(stored.sourceIds)) {
        return { mode: "selected", sourceIds: stored.sourceIds.filter((id: unknown) => typeof id === "string") };
      }
    } catch { /* An unreadable preference opens the whole library. */ }
    return allSources;
  });
  useEffect(() => {
    try { persistentStorage.setItem(key, JSON.stringify(selection)); }
    catch { /* The selection is still available in this window. */ }
  }, [key, selection]);
  return [selection, setSelection] as const;
}

export function selectedSources(selection: SourceSelection, liveIds: readonly string[]): string[] {
  if (selection.mode === "all") return [...liveIds];
  const chosen = new Set(selection.sourceIds);
  return liveIds.filter((id) => chosen.has(id));
}

export function outputScope(selection: SourceSelection, selectedIds: string[]): OutputScope {
  return {
    mode: selection.mode === "all" ? "library" : "selection",
    sourceIds: selection.mode === "all" ? [] : selectedIds,
    unitIds: [],
    sessionIds: [],
  };
}
