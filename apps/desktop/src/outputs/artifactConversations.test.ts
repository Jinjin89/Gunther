import type { Artifact, ArtifactSummary } from "@gunther/contracts";
import { beforeEach, describe, expect, it } from "vitest";
import { ArtifactConversationStore, libraryScope, type ArtifactTurn } from "./artifactConversations";

const artifact = (id: string, lineageId: string, conversationId?: string): Artifact => ({
  id, lineageId, workspaceId: "workspace", knowledgeBaseId: "library", versionNumber: 1, supersedesArtifactId: null, format: "report", kind: "report", audience: "student", style: "overview", origin: "build", title: "A document", contentHash: "hash", manifestHash: "seal", acceptedUnitIds: [], unitCount: 0, createdAt: "2026-01-01T00:00:00Z", content: "# A document\n\nPreserved backend content", unitSnapshots: [], provenance: { schemaVersion: 2, generator: "agents", workspaceId: "workspace", knowledgeBaseId: "library", knowledgeBaseQuestion: "", acceptedOnly: false, acceptedUnitIds: [], revisionIds: [] }, brief: "", outline: [], citations: [], scope: libraryScope(), inputs: { sources: 1, units: 0, sessions: 0 }, modelLabel: null, sections: [], ...(conversationId ? { conversationId, conversationTitle: "A conversation" } : {}),
});
const turn = (id: string): ArtifactTurn => ({ id, instruction: "Make a report", status: "running", createdAt: "2026-01-01T00:00:00Z", result: null, error: null, request: { operation: "build", payload: { clientRequestId: id, kind: "report", audience: "student", scope: libraryScope(), model: "configured/model", effort: "high" } } });
beforeEach(() => window.localStorage.clear());

describe("durable artifact conversations", () => {
  it("restores the draft, source selection and model within the same workspace and library", () => {
    const store = new ArtifactConversationStore("workspace", "library");
    store.update(store.active().id, { draft: "Continue this request", scope: { mode: "selection", sourceIds: ["source-a"], unitIds: [], sessionIds: [] }, choice: { model: "configured/model", effort: "max" } });
    const restored = new ArtifactConversationStore("workspace", "library");
    expect(restored.active()).toMatchObject({ draft: "Continue this request", scope: { sourceIds: ["source-a"] }, choice: { model: "configured/model", effort: "max" } });
    expect(new ArtifactConversationStore("other-workspace", "library").active().draft).toBe("");
    expect(new ArtifactConversationStore("workspace", "other-library").active().draft).toBe("");
  });
  it("records retries once and preserves the captured request across reopening", () => {
    const store = new ArtifactConversationStore("workspace", "library"); const id = store.active().id;
    store.begin(id, turn("request_first")); store.settle(id, "request_first", "failed", null, "API failure"); store.begin(id, turn("request_first"));
    const restored = new ArtifactConversationStore("workspace", "library"); expect(restored.active().turns).toHaveLength(1);
    expect(restored.active().turns[0]).toMatchObject({ status: "interrupted", request: { payload: { clientRequestId: "request_first", model: "configured/model" } } });
  });
  it("stores result metadata without duplicating backend document bodies", () => {
    const store = new ArtifactConversationStore("workspace", "library"); const id = store.active().id;
    store.begin(id, turn("request_first")); const saved = { ...artifact("first", "lineage", id), clientRequestId: "request_first" }; store.finish(id, "request_first", saved); store.reconcile([saved]);
    const json = JSON.parse(window.localStorage.getItem(store.key)!);
    expect(json.conversations[0].turns[0].result).not.toHaveProperty("content"); expect(store.active().lineageIds).toEqual(["lineage"]);
  });
  it("recovers multiple artifacts and requests from backend conversation provenance", () => {
    const store = new ArtifactConversationStore("workspace", "library");
    const first = { ...artifact("first", "report", "conversation"), request: "Make notes", clientRequestId: "request_first" };
    const second = { ...artifact("second", "deck", "conversation"), kind: "slides" as const, format: "slides" as const, request: "Make slides", clientRequestId: "request_second" };
    store.reconcile([second, first]); expect(store.active().id).toBe("conversation"); expect(store.active().lineageIds).toEqual(["deck", "report"]); expect(store.active().turns).toHaveLength(2);
  });
  it("does not invent a conversation transcript for older artifacts", () => {
    const store = new ArtifactConversationStore("workspace", "library"); store.reconcile([artifact("old", "old_lineage")]);
    expect(store.active().title).toBe("Earlier work"); expect(store.active().turns).toEqual([]);
  });
  it("keeps the newly selected conversation active when a different request finishes", () => {
    const store = new ArtifactConversationStore("workspace", "library"); const first = store.active(); store.begin(first.id, turn("request_first")); const other = store.create();
    store.finish(first.id, "request_first", artifact("result", "lineage", first.id)); store.reconcile([artifact("result", "lineage", first.id)]);
    expect(store.active().id).toBe(other.id); expect(store.get(first.id)?.lineageIds).toEqual(["lineage"]);
  });
  it("reconciles a saved outcome for a previously interrupted request", () => {
    const store = new ArtifactConversationStore("workspace", "library"); const id = store.active().id; store.begin(id, turn("request_first"));
    const restored = new ArtifactConversationStore("workspace", "library"); restored.reconcile([{ ...artifact("result", "lineage", id), clientRequestId: "request_first", request: "Make a report" }]);
    expect(restored.active().turns).toHaveLength(1); expect(restored.active().turns[0]?.status).toBe("saved");
  });
  it("keeps previous snapshots immutable when history is reconciled", () => {
    const store = new ArtifactConversationStore("workspace", "library"); const id = store.active().id; store.begin(id, turn("request_first")); const before = store.snapshot();
    store.reconcile([{ ...artifact("result", "lineage", id), clientRequestId: "request_first" } as ArtifactSummary]);
    expect(before.conversations[0]?.turns[0]?.status).toBe("running"); expect(store.active().turns[0]?.status).toBe("saved");
  });
});
