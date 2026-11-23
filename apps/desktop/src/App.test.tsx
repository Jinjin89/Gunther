import type { ArtifactSummary } from "@gunther/contracts";
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
  vi.spyOn(knowledgeApi, "activity").mockResolvedValue({ version: 1, items: [] });
  const user = userEvent.setup();
  render(<App />);

  const libraries = screen.getByRole("region", { name: "Your libraries" });
  await user.click(await within(libraries).findByRole("button", { name: savedLibrary.title }));

  expect(await screen.findByRole("heading", { name: "Autumn field trip" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Sources" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Studio" })).toBeVisible();
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
