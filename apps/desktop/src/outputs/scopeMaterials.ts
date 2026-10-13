import type { KnowledgeSessionSummary, KnowledgeUnit, LibraryContents, SourceSummary } from "@gunther/contracts";
import { knowledgeApi } from "../api";

export interface ScopeMaterials {
  sources: SourceSummary[];
  units: KnowledgeUnit[];
  sessions: KnowledgeSessionSummary[];
  contents: LibraryContents | null;
}

/** Preserve the library's arrangement, including files imported while it is being read. */
export async function loadScopeMaterials(baseId: string): Promise<ScopeMaterials> {
  const [sources, units, sessions, arranged] = await Promise.all([
    knowledgeApi.sources(baseId), knowledgeApi.knowledgeUnits(baseId), knowledgeApi.sessions(baseId),
    knowledgeApi.contents(baseId).catch(() => null),
  ]);
  const details = new Map(arranged?.sources.map((source) => [source.id, source]) ?? []);
  return {
    sources,
    units: units.filter((unit) => unit.status === "trusted"),
    sessions,
    contents: arranged ? { ...arranged, sources: sources.map((source) => details.get(source.id) ?? {
      id: source.id, title: source.title, kind: source.kind, createdAt: source.createdAt,
      durationSeconds: null, authors: null, year: null, pages: null, mediaType: source.asset?.mediaType ?? null, host: null,
      processing: source.processing?.state === "failed" ? "failed" : source.processing && ["pending", "queued", "running"].includes(source.processing.state) ? "reading" : "ready",
    }) } : null,
  };
}
