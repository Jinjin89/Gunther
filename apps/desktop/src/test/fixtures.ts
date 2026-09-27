import type { InboxItem, SourceSummary } from "@gunther/contracts";
import type { KnowledgeBase } from "../atlas";

export const makeBase = (overrides: Partial<KnowledgeBase> = {}): KnowledgeBase => ({
  id: "bioinformatics",
  eyebrow: "Computational biology",
  title: "Bioinformatics",
  subtitle: "A working field guide",
  question: "How do the methods connect?",
  description: "Papers, classes, recordings, and decisions in one durable home.",
  color: "green",
  status: "Growing",
  progress: 42,
  updated: "Today",
  chapterCount: 3,
  sourceCount: 2,
  indexedSourceCount: 5,
  chapters: [],
  sources: [],
  ...overrides,
});

export const makeSource = (overrides: Partial<SourceSummary> = {}): SourceSummary => ({
  id: "source-1",
  title: "Genome annotation lecture",
  kind: "recording",
  createdAt: "2026-08-29T08:00:00.000Z",
  assertionCount: 3,
  entityCount: 7,
  ...overrides,
});

export const makeInboxItem = (overrides: Partial<InboxItem> = {}): InboxItem => ({
  id: "inbox-1",
  itemType: "source",
  state: "unfiled",
  title: "Unsorted lecture",
  preview: "A preserved source waiting for a home.",
  sourceKind: "recording",
  knowledgeBases: [],
  sourceId: "source-1",
  noteId: null,
  proposalId: null,
  proposalStatus: null,
  assertionCount: 0,
  createdAt: "2026-08-29T08:00:00.000Z",
  updatedAt: "2026-08-29T09:00:00.000Z",
  ...overrides,
});
