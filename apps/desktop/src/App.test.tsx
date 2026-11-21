import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { activityItem } from "./activity/fixtures";
import { knowledgeApi } from "./api";
import { makeBase } from "./test/fixtures";

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
  vi.spyOn(knowledgeApi, "activity").mockResolvedValue({ version: 1, items: [] });
  const user = userEvent.setup();
  render(<App />);

  const libraries = screen.getByRole("region", { name: "Your libraries" });
  await user.click(await within(libraries).findByRole("button", { name: savedLibrary.title }));
  await user.click(screen.getByRole("button", { name: "Contents" }));

  expect(await screen.findByRole("heading", { name: "Autumn field trip" })).toBeVisible();
  expect(screen.getByText(savedLibrary.description)).toBeVisible();
  expect(knowledgeApi.contents).toHaveBeenCalledWith(savedLibrary.id);
  expect(screen.queryByText("Single-cell Best Practices · Annotation")).not.toBeInTheDocument();
});
