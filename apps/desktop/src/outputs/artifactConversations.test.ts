import type { ArtifactConversationDetail, ArtifactConversationSummary, ArtifactSummary } from "@gunther/contracts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { ArtifactConversationStore, EARLIER_WORK, libraryScope } from "./artifactConversations";

const summary = (id: string, title = "A conversation"): ArtifactConversationSummary => ({ id, title, updatedAt: "2026-10-01T00:00:00Z", artifactCount: 0, waiting: false });
const detail = (id: string, title = "A conversation"): ArtifactConversationDetail => ({ ...summary(id, title), scope: libraryScope(), brief: { goal: null, audience: null, constraints: [], open: [] }, messages: [], works: [], active: null, pending: null, archived: false, createdAt: "2026-10-01T00:00:00Z" });
const item = (id: string, overrides: Partial<ArtifactSummary> = {}): ArtifactSummary => ({
  id, workspaceId: "workspace", knowledgeBaseId: "library", lineageId: `lineage_${id}`, versionNumber: 1, supersedesArtifactId: null, format: "report", kind: "report", audience: "student", style: "overview", origin: "build", title: `Document ${id}`, contentHash: "hash", manifestHash: "seal", acceptedUnitIds: [], unitCount: 0, createdAt: "2026-10-01T00:00:00Z", ...overrides,
});
/** What version 1 of the device store kept: whole conversations with their turns. */
const oldDevice = (conversations: unknown[], activeId: string) => JSON.stringify({ version: 1, activeId, conversations, problem: null });
const oldConversation = (id: string, turns: unknown[] = [], extra: Record<string, unknown> = {}) => ({ id, title: "Make notes", untitled: false, scope: libraryScope(), choice: null, draft: "", lineageIds: [], selectedArtifactId: null, createdAt: "2026-09-01T00:00:00Z", turns, ...extra });
const oldTurn = (id: string, status: string, extra: Record<string, unknown> = {}) => ({ id, instruction: `Instruction ${id}`, request: null, status, createdAt: "2026-09-01T00:00:00Z", result: null, error: null, ...extra });
const KEY = "gunther:artifact-conversations:workspace:library";
const selected = (...sourceIds: string[]) => ({ mode: "selection" as const, sourceIds, unitIds: [], sessionIds: [] });
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

let list: ArtifactConversationSummary[];
let imports: Array<{ conversations: Array<{ id: string; turns: Array<{ id?: string; status: string; artifactId?: string | null }> }> }>;
beforeEach(() => {
  window.localStorage.clear(); list = []; imports = [];
  vi.spyOn(knowledgeApi, "artifactConversations").mockImplementation(async () => list);
  vi.spyOn(knowledgeApi, "artifactConversation").mockImplementation(async (_base, id) => detail(id, list.find((entry) => entry.id === id)?.title));
  vi.spyOn(knowledgeApi, "importArtifactConversations").mockImplementation(async (_base, payload) => { imports.push(payload as never); list = [...payload.conversations.map((entry) => summary(entry.id, entry.title)), ...list]; return []; });
  vi.spyOn(knowledgeApi, "createArtifactConversation").mockImplementation(async (_base, payload) => { list = [summary(payload.id!), ...list]; return detail(payload.id!); });
});
afterEach(() => vi.restoreAllMocks());

describe("artifact conversations kept by the service", () => {
  it("restores the draft, source selection and model within the same workspace and library", () => {
    const store = new ArtifactConversationStore("workspace", "library");
    store.update(store.active().id, { draft: "Continue this request", scope: { mode: "selection", sourceIds: ["source-a"], unitIds: [], sessionIds: [] }, choice: { model: "configured/model", effort: "max" } });
    const restored = new ArtifactConversationStore("workspace", "library");
    expect(restored.active()).toMatchObject({ draft: "Continue this request", scope: { sourceIds: ["source-a"] }, choice: { model: "configured/model", effort: "max" } });
    expect(new ArtifactConversationStore("other-workspace", "library").active().draft).toBe("");
    expect(new ArtifactConversationStore("workspace", "other-library").active().draft).toBe("");
  });

  it("lists the service's conversations on a device that kept none", async () => {
    list = [summary("one", "First"), summary("two", "Second")];
    const store = new ArtifactConversationStore("workspace", "library");
    const active = await store.load([]);
    expect(store.snapshot().conversations.map((entry) => entry.title)).toEqual(["First", "Second"]);
    expect(active.id).toBe("one");
    expect(imports).toEqual([]);
  });

  it("sends the device's old conversations once, with their turns, and then keeps only drafts and preferences", async () => {
    window.localStorage.setItem(KEY, oldDevice([
      oldConversation("device_one", [oldTurn("t1", "saved", { result: { id: "art_1" } }), oldTurn("t2", "failed", { error: "API failure" })], { draft: "Half a thought", choice: { model: "configured/model", effort: "high" } }),
      oldConversation("device_blank", [], { draft: "Only a draft", untitled: true, title: "New conversation" }),
    ], "device_one"));
    const store = new ArtifactConversationStore("workspace", "library");
    await store.load([item("art_1", { conversationId: "device_one" })]);
    expect(imports).toHaveLength(1);
    expect(imports[0]!.conversations.map((entry) => entry.id)).toEqual(["device_one"]);
    expect(imports[0]!.conversations[0]!.turns).toMatchObject([{ id: "t1", status: "saved", artifactId: "art_1" }, { id: "t2", status: "failed" }]);
    const stored = JSON.parse(window.localStorage.getItem(KEY)!);
    expect(stored).toMatchObject({ version: 2, imported: true, legacy: [] });
    expect(stored.local.device_one).toMatchObject({ draft: "Half a thought", choice: { model: "configured/model" } });
    expect(JSON.stringify(stored)).not.toContain("Instruction t1");
    // The draft that had no conversation yet stays a new conversation.
    expect(store.snapshot().conversations.find((entry) => entry.id === "device_blank")).toMatchObject({ draft: "Only a draft", saved: false });
    // A second load, even from a new object, sends nothing more.
    await new ArtifactConversationStore("workspace", "library").load([item("art_1", { conversationId: "device_one" })]);
    expect(imports).toHaveLength(1);
  });

  it("shows the service's list when sending fails, and sends again next time", async () => {
    list = [summary("service_one", "From the service")];
    window.localStorage.setItem(KEY, oldDevice([oldConversation("device_one", [oldTurn("t1", "failed")])], "device_one"));
    const failing = vi.mocked(knowledgeApi.importArtifactConversations).mockRejectedValueOnce(new Error("Service busy"));
    const store = new ArtifactConversationStore("workspace", "library");
    await store.load([]);
    expect(failing).toHaveBeenCalledOnce();
    expect(store.snapshot().conversations.map((entry) => entry.title)).toContain("From the service");
    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toMatchObject({ imported: false });
    await new ArtifactConversationStore("workspace", "library").load([]);
    expect(imports).toHaveLength(1);
    expect(imports[0]!.conversations[0]!.id).toBe("device_one");
  });

  it("counts a request that was interrupted but saved its version as saved", async () => {
    window.localStorage.setItem(KEY, oldDevice([oldConversation("device_one", [oldTurn("request_first", "running")])], "device_one"));
    await new ArtifactConversationStore("workspace", "library").load([item("art_1", { conversationId: "device_one", clientRequestId: "request_first" })]);
    expect(imports[0]!.conversations[0]!.turns[0]).toMatchObject({ status: "saved", artifactId: "art_1" });
  });

  it("gathers versions that name no conversation into Earlier work, once", async () => {
    const store = new ArtifactConversationStore("workspace", "library");
    await store.load([item("old_a", { request: "Notes on markers" }), item("old_b", { lineageId: "other", versionNumber: 2 }), item("new_a", { conversationId: "owned" })]);
    expect(imports).toHaveLength(1);
    expect(imports[0]!.conversations[0]!.id).toBe(EARLIER_WORK);
    expect(imports[0]!.conversations[0]!.turns.map((turn) => turn.artifactId)).toEqual(["old_a", "old_b"]);
    await new ArtifactConversationStore("workspace", "library").load([item("old_a"), item("old_b", { lineageId: "other" })]);
    expect(imports).toHaveLength(1);
  });

  it("sends a new conversation to the service with its first message, once", async () => {
    const store = new ArtifactConversationStore("workspace", "library");
    const id = store.active().id;
    expect(store.active().saved).toBe(false);
    await store.ensure(id); await store.ensure(id);
    expect(knowledgeApi.createArtifactConversation).toHaveBeenCalledOnce();
    expect(vi.mocked(knowledgeApi.createArtifactConversation).mock.calls[0]![1]).toMatchObject({ id });
    expect(store.active()).toMatchObject({ id, saved: true });
  });

  it("sends a changed source selection to a conversation the service has, and not to a new one", async () => {
    const update = vi.spyOn(knowledgeApi, "updateArtifactConversation").mockResolvedValue(detail("one"));
    list = [summary("one")];
    const store = new ArtifactConversationStore("workspace", "library");
    const fresh = store.active().id;
    store.update(fresh, { scope: { mode: "selection", sourceIds: ["a"], unitIds: [], sessionIds: [] } });
    expect(update).not.toHaveBeenCalled();
    await store.load([]);
    store.update("one", { scope: { mode: "selection", sourceIds: ["b"], unitIds: [], sessionIds: [] } });
    expect(update).toHaveBeenCalledWith("library", "one", { scope: { mode: "selection", sourceIds: ["b"], unitIds: [], sessionIds: [] } }, "workspace");
  });

  it("saves rapid selection changes in order before allowing a turn to send", async () => {
    list = [summary("one")];
    const first = deferred<ArtifactConversationDetail>(), second = deferred<ArtifactConversationDetail>();
    const update = vi.spyOn(knowledgeApi, "updateArtifactConversation").mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    const store = new ArtifactConversationStore("workspace", "library");
    await store.load([]);
    store.update("one", { scope: selected("a") });
    store.update("one", { scope: selected("b") });
    expect(update).toHaveBeenCalledTimes(1);
    let ready = false;
    const sending = store.ensure("one").then(() => { ready = true; });
    expect(ready).toBe(false);
    first.resolve(detail("one"));
    await vi.waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update.mock.calls.map((call) => call[2])).toEqual([{ scope: selected("a") }, { scope: selected("b") }]);
    expect(ready).toBe(false);
    second.resolve(detail("one"));
    await sending;
    expect(ready).toBe(true);
  });

  it("blocks sending after a failed scope save and retries the current selection", async () => {
    list = [summary("one")];
    const update = vi.spyOn(knowledgeApi, "updateArtifactConversation").mockRejectedValueOnce(new Error("Selection save failed")).mockResolvedValue(detail("one"));
    const store = new ArtifactConversationStore("workspace", "library");
    await store.load([]);
    store.update("one", { scope: selected("chosen") });
    await expect(store.ensure("one")).rejects.toThrow("Selection save failed");
    await store.ensure("one");
    expect(update).toHaveBeenCalledTimes(2);
    expect(update).toHaveBeenLastCalledWith("library", "one", { scope: selected("chosen") }, "workspace");
  });

  it("persists a selection changed while the new conversation is being created", async () => {
    const creating = deferred<ArtifactConversationDetail>();
    vi.mocked(knowledgeApi.createArtifactConversation).mockReturnValueOnce(creating.promise);
    const update = vi.spyOn(knowledgeApi, "updateArtifactConversation").mockImplementation(async (_base, id) => detail(id));
    const store = new ArtifactConversationStore("workspace", "library");
    const id = store.active().id;
    store.update(id, { scope: selected("before") });
    const sending = store.ensure(id);
    store.update(id, { scope: selected("after") });
    creating.resolve({ ...detail(id), scope: selected("before") });
    await sending;
    expect(update).toHaveBeenCalledWith("library", id, { scope: selected("after") }, "workspace");
  });

  it("keeps previous snapshots immutable when something changes", async () => {
    list = [summary("one")];
    const store = new ArtifactConversationStore("workspace", "library");
    const before = store.snapshot();
    await store.load([]);
    expect(before.conversations.map((entry) => entry.id)).not.toContain("one");
    expect(store.snapshot().conversations.map((entry) => entry.id)).toContain("one");
  });
});
