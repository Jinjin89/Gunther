import type { KnowledgeSearchResult, SearchScope, WebSearchResult } from "@gunther/contracts";
import { useCallback, useMemo, useRef, useState } from "react";
import { knowledgeApi } from "../../api";
import type { KnowledgeBase, KnowledgeChapter } from "../../atlas";

/** Library-scoped searches ask for a little more, since each library is a narrower pool. */
const SCOPED_RESULT_LIMIT = 40;

export interface SubmittedSearch {
  query: string;
  scope: SearchScope;
  baseIds: string[];
}

export interface CuratedResult {
  type: "base" | "chapter";
  base: KnowledgeBase;
  chapter: KnowledgeChapter | null;
}

export function useKnowledgeSearch(bases: KnowledgeBase[]) {
  const [submitted, setSubmitted] = useState<SubmittedSearch | null>(null);
  const [indexedResults, setIndexedResults] = useState<KnowledgeSearchResult[]>([]);
  const [webResult, setWebResult] = useState<WebSearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestRequest = useRef(0);

  const run = useCallback(async (query: string, scope: SearchScope, baseIds: string[]) => {
    const needle = query.trim();
    if (!needle) return;
    const requestId = latestRequest.current + 1;
    latestRequest.current = requestId;
    const isCurrent = () => latestRequest.current === requestId;
    setSubmitted({ query: needle, scope, baseIds });
    setSearching(true);
    setError(null);
    setIndexedResults([]);
    setWebResult(null);
    const requests: Promise<void>[] = [];
    if (scope !== "web") {
      const request = baseIds.length ? knowledgeApi.search(needle, SCOPED_RESULT_LIMIT, baseIds) : knowledgeApi.search(needle);
      requests.push(request.then((results) => {
        if (!isCurrent()) return;
        // The service scopes results itself; the filter keeps the UI honest regardless.
        setIndexedResults(baseIds.length ? results.filter((result) => result.knowledgeBaseId !== null && baseIds.includes(result.knowledgeBaseId)) : results);
      }).catch((reason: unknown) => {
        if (isCurrent()) setError(reason instanceof Error ? reason.message : "Your local index could not be reached.");
      }));
    }
    if (scope !== "knowledge") {
      requests.push(knowledgeApi.webSearch(needle).then((result) => {
        if (isCurrent()) setWebResult(result);
      }).catch((reason: unknown) => {
        if (isCurrent()) setWebResult({ query: needle, answer: "", sources: [], mode: "failed", message: reason instanceof Error ? reason.message : "Online search could not be reached." });
      }));
    }
    await Promise.all(requests);
    if (isCurrent()) setSearching(false);
  }, []);

  const reset = useCallback(() => {
    latestRequest.current += 1;
    setSubmitted(null);
    setIndexedResults([]);
    setWebResult(null);
    setSearching(false);
    setError(null);
  }, []);

  // Bundled field guides are searched locally so their chapters stay reachable.
  const curatedResults = useMemo<CuratedResult[]>(() => {
    if (!submitted || submitted.scope === "web") return [];
    const needle = submitted.query.toLowerCase();
    const scopedBases = submitted.baseIds.length ? bases.filter((base) => submitted.baseIds.includes(base.id)) : bases;
    return scopedBases.flatMap((base) => [
      ...(base.title.toLowerCase().includes(needle) || base.description.toLowerCase().includes(needle) ? [{ type: "base" as const, base, chapter: null }] : []),
      ...base.chapters
        .filter((chapter) => `${chapter.title} ${chapter.question} ${chapter.summary} ${chapter.topics.map((topic) => `${topic.title} ${topic.markers?.join(" ") ?? ""}`).join(" ")}`.toLowerCase().includes(needle))
        .map((chapter) => ({ type: "chapter" as const, base, chapter })),
    ]).slice(0, 10);
  }, [bases, submitted]);

  return { submitted, indexedResults, curatedResults, webResult, searching, error, run, reset };
}
