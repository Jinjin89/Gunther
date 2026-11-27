import type { Artifact, ArtifactSummary, KeyResult } from "@gunther/contracts";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { activityItem } from "./activity/fixtures";
import { knowledgeApi } from "./api";
import { appendStoredCapture, loadStoredCaptures } from "./localCaptureQueue";
import * as storage from "./persistentStorage";
import { makeBase, makeTrashItem } from "./test/fixtures";

HTMLElement.prototype.scrollTo ??= () => undefined;

const savedLibrary = makeBase({
  id: "single-cell-annotation",
  title: "Field recordings",
  description: "Recordings from the research trip.",
  sourceCount: 0,
});
const savedSession = {
  id: "field-session", knowledgeBaseId: savedLibrary.id, title: "Field trip discussion", summary: "",
  focusChapterId: null, selectedSourceIds: [], pinned: false, archived: false,
  parentSessionId: null, branchedFromMessageId: null, messageCount: 0, messages: [],
  createdAt: savedLibrary.createdAt, updatedAt: savedLibrary.updatedAt,
};

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  vi.stubGlobal("fetch", vi.fn(async (path: string) => path.endsWith("/workspace/bootstrap")
    ? Response.json({ workspaceId: "wsp" }) : Response.json([])));
  vi.spyOn(knowledgeApi, "knowledgeBases").mockResolvedValue([savedLibrary]);
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue({ models: [], default: null, efforts: [] });
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([savedSession]);
  vi.spyOn(knowledgeApi, "session").mockResolvedValue(savedSession);
  vi.spyOn(knowledgeApi, "followAnswer").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "topics").mockResolvedValue([]);
  vi.spyOn(knowledgeApi, "contents").mockResolvedValue({
    sections: [{ id: "trip", parentId: null, title: "Autumn field trip", description: "", position: 0, version: 1, passageCount: 0 }],
    placements: [], sources: [],
  });
});

afterEach(() => vi.unstubAllGlobals());

it("shows an unavailable library for a stale Activity link", async () => {
  vi.spyOn(knowledgeApi, "activity").mockResolvedValue({ version: 1, items: [activityItem({
    id: "output:missing", title: "Building slides", kind: "output", about: "Removed library",
    open: { view: "library", libraryId: "missing", tab: "outputs" },
  })] });
  const user = userEvent.setup();
  render(<App />);

  await user.click(await screen.findByRole("button", { name: /Building slides/ }));
  await user.click(within(screen.getByRole("dialog", { name: "Activity" })).getByText("Building slides: Removed library"));

  expect(await screen.findByRole("heading", { name: "Library unavailable" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  expect(screen.queryByRole("navigation", { name: "Library views" })).not.toBeInTheDocument();
  expect(knowledgeApi.contents).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Back to Libraries" }));
  expect(within(screen.getByRole("main")).getByRole("button", { name: /Field recordings/ })).toBeVisible();
});

it("uses stored metadata and Contents for a library with a former sample ID", async () => {
  vi.stubGlobal("innerWidth", 1280);
  const measure = HTMLElement.prototype.getBoundingClientRect;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    // jsdom does not measure the workspace beside the collapsed 62px sidebar.
    return this.classList.contains("library-workspace") ? new DOMRect(0, 0, 1280 - 62, 0) : measure.call(this);
  });
  vi.spyOn(knowledgeApi, "activity").mockResolvedValue({ version: 1, items: [] });
  const user = userEvent.setup();
  render(<App />);

  const libraries = screen.getByRole("region", { name: "Your libraries" });
  await user.click(await within(libraries).findByRole("button", { name: savedLibrary.title }));

  expect(await screen.findByRole("heading", { name: "Autumn field trip" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Library" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Sources" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "Studio" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Expand Gunther sidebar" })).toBeVisible();
  expect(screen.getByText(savedLibrary.description)).toBeVisible();
  expect(knowledgeApi.contents).toHaveBeenCalledWith(savedLibrary.id);
  expect(screen.queryByText("Single-cell Best Practices · Annotation")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Add source" }));
  await user.click(screen.getByRole("menuitem", { name: "Note or pasted text" }));
  const capture = within(await screen.findByRole("dialog", { name: "Capture something" }));
  expect(capture.getByRole("tab", { name: "Note", selected: true })).toBeVisible();
  expect(capture.getByText(savedLibrary.title)).toBeVisible();
});

it("exports the library's kept usable key results in the workbook JSON", async () => {
  const finding: KeyResult = {
    id: "finding", text: "The recorder was calibrated before each run.",
    origin: { kind: "answer", id: "answer", title: savedSession.title, sessionId: savedSession.id, createdAt: savedLibrary.createdAt },
    evidence: [{
      id: "passage", sourceId: "log", revisionId: "revision", blockId: "block", regionIds: ["region"],
      sourceTitle: "Calibration log", locator: "Page 2", quote: "We calibrated the recorder before each run.",
    }],
  };
  vi.spyOn(knowledgeApi, "activity").mockResolvedValue({ version: 1, items: [] });
  const keyResults = vi.spyOn(knowledgeApi, "keyResults").mockResolvedValue([finding]);
  const createObjectURL = vi.fn((_: Blob) => "blob:library-workbook");
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = createObjectURL;
    static revokeObjectURL = vi.fn();
  });
  const download = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    download(this.download);
  });
  const user = userEvent.setup();
  render(<App />);

  const libraries = screen.getByRole("region", { name: "Your libraries" });
  await user.click(await within(libraries).findByRole("button", { name: savedLibrary.title }));
  await user.click(await screen.findByRole("button", { name: "Library actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Export library" }));
  await waitFor(() => expect(download).toHaveBeenCalledExactlyOnceWith(`${savedLibrary.id}.gunther.json`));
  expect(keyResults).toHaveBeenCalledWith(savedLibrary.id);
  const json = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(createObjectURL.mock.calls[0]![0]);
  });
  expect(JSON.parse(json).keyResults).toEqual([finding]);
});

it("moves a Studio card to Trash and restores its batch with Undo", async () => {
  vi.stubGlobal("innerWidth", 1280);
  vi.spyOn(knowledgeApi, "activity").mockResolvedValue({ version: 1, items: [] });
  const artifact: ArtifactSummary = {
    id: "field-version", lineageId: "field-lineage", workspaceId: "wsp", knowledgeBaseId: savedLibrary.id,
    title: "Field report", kind: "report", format: "report", style: "report", outputType: "Report",
    origin: "build", versionNumber: 1, supersedesArtifactId: null, audience: "student",
    contentHash: "hash", manifestHash: "seal", acceptedUnitIds: [], unitCount: 0, createdAt: savedLibrary.createdAt,
  };
  const entry = makeTrashItem({ id: "trash-batch", title: artifact.title, kind: "output", versionCount: 1 });
  let live = true;
  vi.spyOn(knowledgeApi, "artifacts").mockImplementation(async () => live ? [artifact] : []);
  const move = vi.spyOn(knowledgeApi, "trashOutput").mockImplementation(async () => { live = false; return entry; });
  const undo = vi.spyOn(knowledgeApi, "restoreFromTrash").mockImplementation(async () => { live = true; return entry; });
  const user = userEvent.setup();
  render(<App />);

  const libraries = screen.getByRole("region", { name: "Your libraries" });
  await user.click(await within(libraries).findByRole("button", { name: savedLibrary.title }));
  await user.click(await screen.findByRole("button", { name: "Actions for “Field report”" }));
  await user.click(screen.getByRole("menuitem", { name: "Move to Trash" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: /Field report Report Version 1/ })).toBeNull());
  expect(move).toHaveBeenCalledExactlyOnceWith(savedLibrary.id, artifact.id, artifact.workspaceId);

  await user.click(await screen.findByRole("button", { name: "Undo" }));
  expect(undo).toHaveBeenCalledExactlyOnceWith("output", entry.id);
  expect(await screen.findByRole("button", { name: /Field report Report Version 1/ })).toBeVisible();
});

it("reopens the exact saved version after restoring from Trash following a remount", async () => {
  vi.stubGlobal("innerWidth", 1280);
  vi.spyOn(knowledgeApi, "activity").mockResolvedValue({ version: 1, items: [] });
  vi.spyOn(knowledgeApi, "followArtifactTurn").mockResolvedValue(null);
  vi.spyOn(knowledgeApi, "artifactConversations").mockResolvedValue([]);
  const turn = vi.spyOn(knowledgeApi, "artifactTurnStream");
  const original: Artifact = {
    id: "field-v1", lineageId: "field-lineage", conversationId: "field-work", workspaceId: "wsp", knowledgeBaseId: savedLibrary.id,
    title: "Field report", kind: "report", format: "report", style: "report", outputType: "Report", origin: "build",
    versionNumber: 1, supersedesArtifactId: null, audience: "student", contentHash: "hash", manifestHash: "seal",
    acceptedUnitIds: [], unitCount: 0, createdAt: savedLibrary.createdAt, content: "## First observations",
    unitSnapshots: [], sections: [], outline: [], citations: [], scope: null, brief: "", modelLabel: null,
    inputs: { sources: 0, units: 0, sessions: 0 },
    provenance: { acceptedOnly: false, acceptedUnitIds: [], schemaVersion: 2, generator: "gunther.output-agents.v1",
      workspaceId: "wsp", knowledgeBaseId: savedLibrary.id, knowledgeBaseQuestion: "", revisionIds: [] },
  };
  const head = { ...original, id: "field-v2", versionNumber: 2, supersedesArtifactId: original.id, content: "## Later observations" };
  const entry = makeTrashItem({ id: "trash-batch", title: original.title, kind: "output", versionCount: 2,
    open: { view: "library", libraryId: savedLibrary.id, tab: "outputs", conversationId: original.conversationId ?? null,
      lineageId: original.lineageId, artifactId: original.id, typeId: "report", planId: "draft_field" } });
  let trashed = false;
  vi.spyOn(knowledgeApi, "artifacts").mockImplementation(async () => trashed ? [] : [head, original]);
  const read = vi.spyOn(knowledgeApi, "artifact").mockImplementation(async (_base, id) => id === original.id ? original : head);
  vi.spyOn(knowledgeApi, "trashOutput").mockImplementation(async () => { trashed = true; return entry; });
  vi.spyOn(knowledgeApi, "trash").mockImplementation(async () => trashed ? [entry] : []);
  const restore = vi.spyOn(knowledgeApi, "restoreFromTrash").mockImplementation(async () => { trashed = false; return entry; });
  const user = userEvent.setup();
  const first = render(<App />);
  await user.click(await within(screen.getByRole("region", { name: "Your libraries" })).findByRole("button", { name: savedLibrary.title }));
  await user.click(await screen.findByRole("button", { name: /Field report Report Version 2/ }));
  await user.click(await screen.findByRole("button", { name: "Output actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Versions" }));
  await user.click(screen.getByRole("button", { name: "Version 1" }));
  await user.click(await screen.findByRole("button", { name: "Output actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Move to Trash" }));
  await screen.findByText("Moved “Field report” to Trash.");
  first.unmount();
  read.mockClear();

  render(<App />);
  await user.click(screen.getByRole("button", { name: "Trash" }));
  await user.click(await screen.findByRole("button", { name: "Restore “Field report”" }));
  expect(restore).toHaveBeenCalledExactlyOnceWith("output", entry.id);
  const reader = within(await screen.findByRole("article", { name: "Open output" }));
  expect(await reader.findByRole("heading", { name: "First observations" })).toBeVisible();
  expect(reader.getByText("Version 1")).toBeVisible();
  expect(read).toHaveBeenCalledExactlyOnceWith(savedLibrary.id, original.id, "wsp");
  expect(turn).not.toHaveBeenCalled();
});

it("recovers queued links according to their stored URL and workspace", async () => {
  vi.spyOn(knowledgeApi, "activity").mockResolvedValue({ version: 1, items: [] });
  const legacyLink = {
    id: "cap-legacy", title: "", content: "Existing reading", kind: "link",
    baseId: null, workspaceId: "wsp",
  };
  const snapshot = {
    ...legacyLink, id: "cap-snapshot", title: " Page ", content: " Notes ",
    url: "https://example.org/page", baseId: savedLibrary.id,
  };
  appendStoredCapture(legacyLink);
  appendStoredCapture(snapshot);
  appendStoredCapture({ ...snapshot, id: "cap-foreign", workspaceId: "different" });
  appendStoredCapture({ ...snapshot, id: "cap-unbound", workspaceId: null });
  const importSource = vi.spyOn(knowledgeApi, "importSource");
  const captureWeb = vi.spyOn(knowledgeApi, "captureWeb");
  render(<App />);

  expect(await screen.findByText("2 locally preserved captures recovered.")).toBeVisible();
  expect(importSource).toHaveBeenCalledExactlyOnceWith({
    title: "Untitled source", content: "Existing reading", kind: "link",
  }, "wsp", { deferAnalysis: true });
  expect(captureWeb).toHaveBeenCalledExactlyOnceWith({
    url: snapshot.url, title: "Page", notes: "Notes",
    knowledgeBaseId: savedLibrary.id, clientCaptureId: snapshot.id,
  }, "wsp");
  expect(loadStoredCaptures().map(({ id }) => id)).toEqual(["cap-foreign", "cap-unbound"]);
});

it("recovers a saved link when a storage flush would fail", async () => {
  vi.spyOn(knowledgeApi, "activity").mockResolvedValue({ version: 1, items: [] });
  const flush = vi.spyOn(storage, "flushPersistentStorage")
    .mockRejectedValue(new Error("Native storage is unavailable"));
  const captureWeb = vi.spyOn(knowledgeApi, "captureWeb");
  const saved = {
    id: "cap-saved-link", url: "https://example.org/page", title: "Saved page",
    content: "", kind: "link", baseId: null, workspaceId: "wsp",
  };
  appendStoredCapture(saved);
  render(<App />);

  await waitFor(() => expect(captureWeb).toHaveBeenCalledExactlyOnceWith({
    url: saved.url, title: saved.title, clientCaptureId: saved.id,
  }, saved.workspaceId));
  expect(await screen.findByText("1 locally preserved capture recovered.")).toBeVisible();
  expect(flush).not.toHaveBeenCalled();
  expect(loadStoredCaptures()).toEqual([]);
});
