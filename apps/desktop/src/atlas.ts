import type { KnowledgeBaseMetadata } from "@gunther/contracts";

export type AtlasMode = "contents" | "ask" | "outputs";

/** Older names for the Contents tab (it replaced Overview and Sources) still open it. */
export const normalizeAtlasMode = (mode: AtlasMode | "overview" | "sources"): AtlasMode => mode === "overview" || mode === "sources" ? "contents" : mode;

export type KnowledgeBase = KnowledgeBaseMetadata;
