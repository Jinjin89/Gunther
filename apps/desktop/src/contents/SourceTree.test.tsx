import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ContentsSource, KnowledgeTopic, LibraryContents as Contents } from "@gunther/contracts";
import { useState } from "react";
import { knowledgeApi } from "../api";
import { SourceTree } from "./SourceTree";
import { selectedSources, useSourceSelection } from "./sourceScope";

const source = (id: string, title: string, kind: ContentsSource["kind"], extra: Partial<ContentsSource> = {}): ContentsSource => ({
  id, title, kind, createdAt: "2026-10-03T10:00:00", durationSeconds: null, authors: null, year: null, pages: null, mediaType: null, host: null, processing: "ready", ...extra,
});
const section = (id: string, title: string, parentId: string | null, position: number) => ({ id, parentId, title, description: "", position, version: 1, passageCount: 0 });

// Part A { Course outline, Lecture 1 { recording, slides }, Lecture 2 { recording } }, Part B, and a loose note and link.
const base: Contents = {
  sections: [section("a", "Part A", null, 0), section("b", "Part B", null, 1), section("l1", "Lecture 1", "a", 1), section("l2", "Lecture 2", "a", 2)],
  placements: [
    { sourceId: "outline", sectionId: "a", position: 0 }, { sourceId: "rec1", sectionId: "l1", position: 0 },
    { sourceId: "slides1", sectionId: "l1", position: 1 }, { sourceId: "rec2", sectionId: "l2", position: 0 },
  ],
  sources: [
    source("outline", "Course outline.pdf", "file", { mediaType: "application/pdf", pages: 4 }),
    source("rec1", "Lecture 1 recording", "recording", { durationSeconds: 4324 }),
    source("slides1", "week1-slides.pdf", "file", { mediaType: "application/pdf", pages: 24 }),
    source("rec2", "Lecture 2 recording", "recording", { durationSeconds: 3520 }),
    source("link", "A blog post about Adam", "link", { host: "example.org", createdAt: "2026-10-11T10:00:00" }),
    source("note", "My notes on week 3", "note", { createdAt: "2026-10-12T10:00:00" }),
  ],
};

const props = () => ({ baseId: "lib", onOpenSource: vi.fn(), onSource: vi.fn() });
function Tree({ baseId, onSource, onOpenSource }: ReturnType<typeof props>) {
  const [contents, setContents] = useState<Contents | null>(null);
  const [selection, onScope] = useSourceSelection(baseId);
  const ids = selectedSources(selection, contents?.sources.map((item) => item.id) ?? []);
  return <SourceTree baseId={baseId} onSource={onSource} onOpenSource={onOpenSource} contents={contents} onContents={setContents} selection={selection} selectedIds={ids} onScope={onScope}
    activeSourceId={null} addSource={<button>Add source</button>} />;
}
const show = async (contents: Contents = base, extra: Partial<ReturnType<typeof props>> = {}) => {
  vi.spyOn(knowledgeApi, "contents").mockResolvedValue(contents);
  const all = { ...props(), ...extra };
  render(<Tree {...all} />);
  await screen.findByRole("heading", { name: "Part A" });
  return all;
};
const list = () => within(document.querySelector(".contents-list") as HTMLElement);
const rowOf = (title: string) => list().getByText(title).closest(".contents-row") as HTMLElement;

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => { vi.restoreAllMocks(); });

describe("Sources tree", () => {
  it("refreshes duration estimates until audio measurement finishes", async () => {
    vi.useFakeTimers();
    try {
      const pending = { ...base, sources: base.sources.map((item) => item.id === "rec1" ? { ...item, durationSeconds: 60, durationPending: true } : item) };
      const measured = { ...base, sources: base.sources.map((item) => item.id === "rec1" ? { ...item, durationSeconds: 120, durationPending: false } : item) };
      const load = vi.spyOn(knowledgeApi, "contents").mockResolvedValueOnce(pending).mockResolvedValue(measured);
      render(<Tree {...props()} />);
      await act(async () => { await Promise.resolve(); });
      expect(within(rowOf("Lecture 1 recording")).getByText("1:00")).toBeVisible();
      await act(async () => { vi.advanceTimersByTime(2500); });
      expect(within(rowOf("Lecture 1 recording")).getByText("2:00")).toBeVisible();
      await act(async () => { vi.advanceTimersByTime(5000); });
      expect(load).toHaveBeenCalledTimes(2);
    } finally { vi.useRealTimers(); }
  });
  it("shows headings, rows with their details, and Not placed yet last, newest first", async () => {
    await show();
    expect(screen.getByRole("heading", { name: "Lecture 1" })).toBeVisible();
    expect(within(rowOf("Course outline.pdf")).getByText("PDF · 4 pages")).toBeVisible();
    expect(within(rowOf("Lecture 1 recording")).getByText("1:12:04")).toBeVisible();
    expect(within(rowOf("week1-slides.pdf")).getByText("PDF · 24 pages")).toBeVisible();
    const order = [...document.querySelectorAll("[data-row]")].map((row) => row.getAttribute("data-row"));
    expect(order).toEqual(["section:a", "source:outline", "section:l1", "source:rec1", "source:slides1", "section:l2", "source:rec2", "section:b", "empty:b", "unplaced", "source:note", "source:link"]);
    expect(within(rowOf("A blog post about Adam")).getByText("example.org")).toBeVisible();
  });

  it("hides what is under a collapsed section, remembers it, and shows the count", async () => {
    const user = userEvent.setup();
    await show();
    await user.click(screen.getByRole("button", { name: "Collapse Part A" }));
    expect(list().queryByText("Lecture 1 recording")).toBeNull();
    expect(within(rowOf("Part A")).getByText("2 PDFs · 2 recordings")).toBeVisible();
    expect(JSON.parse(window.localStorage.getItem("gunther:contents-collapsed:lib")!)).toEqual(["a"]);
  });

  it("starts collapsed where it was left", async () => {
    window.localStorage.setItem("gunther:contents-collapsed:lib", JSON.stringify(["a"]));
    await show();
    expect(list().queryByText("Lecture 1 recording")).toBeNull();
    expect(screen.getByRole("button", { name: "Expand Part A" })).toHaveAttribute("aria-expanded", "false");
  });

  it("closes and opens every section at once", async () => {
    const user = userEvent.setup();
    await show();
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(list().queryByText("Course outline.pdf")).toBeNull();
    expect(list().getByText("My notes on week 3")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(list().getByText("Lecture 2 recording")).toBeVisible();
  });

  it("filters rows, keeping their headings", async () => {
    const user = userEvent.setup();
    await show();
    await user.type(screen.getByLabelText("Filter contents"), "slides");
    expect(list().getByText("week1-slides.pdf")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Lecture 1" })).toBeVisible();
    expect(list().queryByText("Lecture 2 recording")).toBeNull();
    await user.keyboard("{Escape}");
    expect(list().getByText("Lecture 2 recording")).toBeVisible();
  });

  it("adds a section with its name already being edited", async () => {
    const user = userEvent.setup();
    const save = vi.spyOn(knowledgeApi, "saveTopic").mockResolvedValue({ id: "c", knowledgeBaseId: "lib", parentId: null, title: "New section", description: "", position: 2, version: 1, blockIds: [], sourceIds: [] } satisfies KnowledgeTopic);
    await show();
    vi.mocked(knowledgeApi.contents).mockResolvedValue({ ...base, sections: [...base.sections, section("c", "New section", null, 2)] });
    await user.click(screen.getByRole("button", { name: "Add section" }));
    const field = await screen.findByLabelText("Section name");
    expect(field).toHaveFocus();
    expect(field).toHaveValue("New section");
    expect(save).toHaveBeenCalledWith("lib", { title: "New section", description: "", parentId: null });
    // Naming it saves under the new name.
    save.mockResolvedValue({ id: "c", knowledgeBaseId: "lib", parentId: null, title: "Readings", description: "", position: 2, version: 2, blockIds: [], sourceIds: [] });
    await user.clear(field);
    await user.type(field, "Readings{Enter}");
    await waitFor(() => expect(save).toHaveBeenLastCalledWith("lib", { title: "Readings", description: "", parentId: null, version: 1 }, "c"));
  });

  it("takes a new section back out when its name is not chosen", async () => {
    const user = userEvent.setup();
    vi.spyOn(knowledgeApi, "saveTopic").mockResolvedValue({ id: "c", knowledgeBaseId: "lib", parentId: null, title: "New section", description: "", position: 2, version: 1, blockIds: [], sourceIds: [] });
    const remove = vi.spyOn(knowledgeApi, "deleteSection").mockResolvedValue(base);
    await show();
    vi.mocked(knowledgeApi.contents).mockResolvedValue({ ...base, sections: [...base.sections, section("c", "New section", null, 2)] });
    await user.click(screen.getByRole("button", { name: "Add section" }));
    await screen.findByLabelText("Section name");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(remove).toHaveBeenCalledWith("lib", "c"));
  });

  it("moves one source with Move to… and sends the chosen section", async () => {
    const user = userEvent.setup();
    const moved: Contents = { ...base, placements: base.placements.map((item) => item.sourceId === "rec2" ? { ...item, sectionId: "b" } : item) };
    const move = vi.spyOn(knowledgeApi, "moveContents").mockResolvedValue(moved);
    await show();
    await user.click(screen.getByRole("button", { name: "Actions for Lecture 2 recording" }));
    await user.click(screen.getByRole("menuitem", { name: "Move to…" }));
    const picker = screen.getByRole("dialog", { name: "Move to…" });
    await user.type(within(picker).getByLabelText("Filter"), "part b");
    await user.click(within(picker).getByRole("option", { name: "Part B" }));
    expect(move).toHaveBeenCalledWith("lib", { items: [{ kind: "source", id: "rec2" }], parentId: "b", index: null });
    expect(await list().findByText("Lecture 2 recording")).toBeVisible();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not offer a section's own sub-tree when moving a section", async () => {
    const user = userEvent.setup();
    await show();
    await user.click(screen.getByRole("button", { name: "Actions for Part A" }));
    await user.click(screen.getByRole("menuitem", { name: "Move to…" }));
    const picker = screen.getByRole("dialog", { name: "Move to…" });
    expect(within(picker).getByRole("option", { name: "Top level" })).toBeEnabled();
    expect(within(picker).getByRole("option", { name: "Part A" })).toBeDisabled();
    expect(within(picker).getByRole("option", { name: "Lecture 1" })).toBeDisabled();
    expect(within(picker).getByRole("option", { name: "Part B" })).toBeEnabled();
  });

  it("adds sources into a section from its menu", async () => {
    const user = userEvent.setup();
    const move = vi.spyOn(knowledgeApi, "moveContents").mockResolvedValue(base);
    await show();
    await user.click(screen.getByRole("button", { name: "Actions for Part B" }));
    await user.click(screen.getByRole("menuitem", { name: "Add sources here…" }));
    const picker = screen.getByRole("dialog", { name: "Add sources to “Part B”" });
    expect(within(picker).getAllByRole("checkbox")[0]).toBeVisible();
    await user.click(within(picker).getByRole("checkbox", { name: /My notes on week 3/ }));
    await user.click(within(picker).getByRole("button", { name: "Add 1" }));
    expect(move).toHaveBeenCalledWith("lib", { items: [{ kind: "source", id: "note" }], parentId: "b", index: null });
  });

  it("sorts a section's children by name, numbers by value", async () => {
    const user = userEvent.setup();
    const contents: Contents = {
      sections: [section("r", "Recordings", null, 0)],
      placements: ["Lecture 10", "Lecture 2", "Lecture 1"].map((title, index) => ({ sourceId: title, sectionId: "r", position: index })),
      sources: ["Lecture 10", "Lecture 2", "Lecture 1"].map((title) => source(title, title, "recording")),
    };
    const order = vi.spyOn(knowledgeApi, "orderContents").mockResolvedValue({ ...contents, placements: ["Lecture 1", "Lecture 2", "Lecture 10"].map((title, index) => ({ sourceId: title, sectionId: "r", position: index })) });
    vi.spyOn(knowledgeApi, "contents").mockResolvedValue(contents);
    render(<Tree {...props()} />);
    await screen.findByRole("heading", { name: "Recordings" });
    await user.click(screen.getByRole("button", { name: "Actions for Recordings" }));
    await user.click(screen.getByRole("menuitem", { name: "Sort by name" }));
    expect(order).toHaveBeenCalledWith("lib", { parentId: "r", children: [{ kind: "source", id: "Lecture 1" }, { kind: "source", id: "Lecture 2" }, { kind: "source", id: "Lecture 10" }] });
    await waitFor(() => expect([...document.querySelectorAll(".contents-source strong")].map((node) => node.textContent)).toEqual(["Lecture 1", "Lecture 2", "Lecture 10"]));
  });

  it("asks before deleting a section and returns its sources to Not placed yet", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const remove = vi.spyOn(knowledgeApi, "deleteSection").mockResolvedValue({ ...base, sections: [base.sections[1]!], placements: [] });
    await show();
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await user.click(screen.getByRole("button", { name: "Actions for Part A" }));
      await user.click(screen.getByRole("menuitem", { name: "Delete section" }));
      if (!attempt) expect(remove).not.toHaveBeenCalled();
    }
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Move 4 sources to Not placed yet."));
    expect(remove).toHaveBeenCalledWith("lib", "a");
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Part A" })).toBeNull());
    expect(list().getByText("Lecture 2 recording")).toBeVisible();
  });

  it("puts the rows back and says so when a move fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(knowledgeApi, "moveContents").mockRejectedValue(new Error("The contents changed. Reload and try again."));
    await show();
    await user.click(screen.getByRole("button", { name: "Actions for Lecture 2 recording" }));
    await user.click(screen.getByRole("menuitem", { name: "Take out of section" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The contents changed. Reload and try again.");
    const names = [...document.querySelectorAll(".contents-row")].map((row) => row.querySelector("strong, .contents-heading")?.textContent);
    expect(names.indexOf("Lecture 2 recording")).toBeLessThan(names.indexOf("Part B"));
  });

  it("suggests sections once six papers are not placed, and creates one", async () => {
    const user = userEvent.setup();
    const papers = Array.from({ length: 6 }, (_, index) => source(`p${index}`, `Paper ${index}`, "paper"));
    vi.spyOn(knowledgeApi, "topicSuggestions").mockResolvedValue({
      suggestions: [{ key: "k1", title: "Rocket · Fuel", keywords: ["rocket", "fuel"], sourceIds: ["p0", "p1"], examples: ["Reusable boosters"], years: [2019, 2024] }],
      unfiled: 6, method: "semantic", reason: null,
    });
    const save = vi.spyOn(knowledgeApi, "saveTopic").mockResolvedValue({ id: "n", knowledgeBaseId: "lib", parentId: null, title: "Launch vehicles", description: "", position: 0, version: 1, blockIds: [], sourceIds: [] });
    await show({ sections: [section("a", "Part A", null, 0)], placements: [], sources: papers });
    await user.click(screen.getByRole("button", { name: "Suggest sections" }));
    expect(await screen.findByText("6 papers not placed yet, grouped by meaning.")).toBeVisible();
    const name = screen.getByDisplayValue("Rocket · Fuel");
    await user.clear(name);
    await user.type(name, "Launch vehicles");
    await user.click(screen.getByRole("button", { name: "Create section" }));
    expect(save).toHaveBeenCalledWith("lib", { title: "Launch vehicles", description: "", parentId: null, sourceIds: ["p0", "p1"] });
    expect(await screen.findByText("All suggestions are now sections.")).toBeVisible();
  });

  it("does not suggest with fewer than six papers", async () => {
    await show({ sections: [section("a", "Part A", null, 0)], placements: [], sources: [source("p0", "Paper", "paper")] });
    expect(screen.queryByRole("button", { name: "Suggest sections" })).toBeNull();
  });

  it("keeps rename and section notes accessible from the rows", async () => {
    const user = userEvent.setup();
    const save = vi.spyOn(knowledgeApi, "saveTopic").mockResolvedValue({ id: "l1", knowledgeBaseId: "lib", parentId: "a", title: "Introduction", description: "", position: 1, version: 2, blockIds: [], sourceIds: [] });
    const rename = vi.spyOn(knowledgeApi, "renameSource").mockResolvedValue({} as never);
    await show();
    await user.click(screen.getByRole("button", { name: "Actions for Lecture 1" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const name = screen.getByLabelText("Section name");
    await user.clear(name);
    vi.mocked(knowledgeApi.contents).mockResolvedValue({ ...base, sections: base.sections.map((item) => item.id === "l1" ? { ...item, title: "Introduction", version: 2 } : item) });
    await user.type(name, "Introduction{Enter}");
    expect(save).toHaveBeenCalledWith("lib", { title: "Introduction", description: "", parentId: "a", version: 1 }, "l1");
    await user.click(screen.getByRole("button", { name: "Actions for Introduction" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit section note" }));
    await user.type(screen.getByRole("textbox", { name: "Section note" }), "Week one");
    await user.click(screen.getByRole("button", { name: "Save note" }));
    expect(save).toHaveBeenLastCalledWith("lib", { title: "Introduction", description: "Week one", parentId: "a", version: 2 }, "l1");
    await user.click(screen.getByRole("button", { name: "Actions for A blog post about Adam" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const field = screen.getByLabelText("Source name");
    await user.clear(field);
    await user.type(field, "Adam, explained{Enter}");
    expect(rename).toHaveBeenCalledWith("link", "Adam, explained");
  });

  it("offers a way to fill an empty section", async () => {
    const user = userEvent.setup();
    await show();
    const empty = document.querySelector('[data-row="empty:b"]') as HTMLElement;
    expect(empty).toHaveTextContent("Empty. Drag sources here, or");
    await user.click(within(empty).getByRole("button", { name: "add sources" }));
    expect(screen.getByRole("dialog", { name: "Add sources to “Part B”" })).toBeVisible();
  });

  it("never says topic", async () => {
    await show();
    expect(document.body.textContent).not.toMatch(/topic/i);
  });
});
