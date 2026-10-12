import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AtlasMode } from "../atlas";
import { knowledgeApi } from "../api";
import { makeBase } from "../test/fixtures";
import { KnowledgeBaseWorkspace } from "./KnowledgeBaseWorkspace";

vi.mock("./SessionWorkspace", () => ({ SessionWorkspace: () => <div>Ask area</div> }));
vi.mock("../outputs/OutputsPage", () => ({ OutputsPage: () => <div>Outputs area</div> }));

const view = (mode: AtlasMode | "overview" | "sources", extra: Record<string, unknown> = {}) => {
  const onMode = vi.fn();
  render(<KnowledgeBaseWorkspace base={makeBase({ id: "lib", title: "ML 2026", description: "Lectures and slides for autumn." })} workspaceId={null} mode={mode as AtlasMode} selectedChapterId="" onMode={onMode} onChapter={vi.fn()} onBack={vi.fn()} onEvidence={vi.fn()} onAdd={vi.fn()} onRecord={vi.fn()} onExport={vi.fn()} onEdit={vi.fn()} onNotify={vi.fn()} onOpenSource={vi.fn()} {...extra} />);
  return { onMode };
};

beforeEach(() => {
  window.localStorage.clear();
  vi.spyOn(knowledgeApi, "topics").mockResolvedValue([]);
  vi.spyOn(knowledgeApi, "contents").mockResolvedValue({
    sections: [{ id: "a", parentId: null, title: "Part A", description: "", position: 0, version: 1, passageCount: 0 }],
    placements: [{ sourceId: "s1", sectionId: "a", position: 0 }],
    sources: [{ id: "s1", title: "Lecture 1", kind: "recording", createdAt: "2026-10-03T10:00:00", durationSeconds: 60, authors: null, year: null, pages: null, mediaType: null, host: null, processing: "ready" }],
  });
});
afterEach(() => { vi.restoreAllMocks(); });

describe("Library workspace", () => {
  it("has three tabs: Contents, Ask and Outputs", async () => {
    const { onMode } = view("contents");
    const tabs = within_nav();
    expect(tabs).toEqual(["Contents", "Ask", "Outputs"]);
    expect(screen.getByRole("button", { name: "Contents" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("heading", { name: "Part A" })).toBeVisible();
    expect(screen.getByText("Lectures and slides for autumn.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(onMode).toHaveBeenCalledWith("ask");
  });

  it.each(["sources", "overview"] as const)("opens Contents for the older %s name", async (name) => {
    view(name);
    expect(await screen.findByRole("heading", { name: "Part A" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Contents" })).toHaveAttribute("aria-current", "page");
  });

  it("offers the kinds of source to add, and an import", async () => {
    const onAdd = vi.fn();
    view("contents", { onAdd });
    await screen.findByRole("heading", { name: "Part A" });
    await userEvent.click(screen.getByRole("button", { name: /Add source/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Web page" }));
    expect(onAdd).toHaveBeenCalledWith("link");
    expect(screen.getByRole("button", { name: /Import folder/ })).toBeVisible();
  });

  it("shows the Ask tab", () => {
    view("ask");
    expect(screen.getByText("Ask area")).toBeVisible();
  });
});

function within_nav() {
  return [...screen.getByRole("navigation", { name: "Library views" }).querySelectorAll("button")].map((button) => button.textContent);
}
