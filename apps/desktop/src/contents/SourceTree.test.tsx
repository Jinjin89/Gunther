import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ContentsSource, KnowledgeTopic, LibraryContents as Contents } from "@gunther/contracts";
import { useState } from "react";
import { knowledgeApi } from "../api";
import { makeTrashItem } from "../test/fixtures";
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

const props = () => ({ baseId: "lib", onTrashed: vi.fn(), onSource: vi.fn() });
function Tree({ baseId, onSource, onTrashed }: ReturnType<typeof props>) {
  const [contents, setContents] = useState<Contents | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [selection, onScope] = useSourceSelection(baseId);
  const [open, onOpenChange] = useState(true);
  const ids = selectedSources(selection, contents?.sources.map((item) => item.id) ?? []);
  return <SourceTree baseId={baseId} onSource={onSource} onTrashed={onTrashed} contents={contents} onContents={setContents}
    loadFailed={loadFailed} onLoadFailedChange={setLoadFailed} selection={selection} selectedIds={ids} onScope={onScope}
    activeSourceId={null} open={open} onOpenChange={onOpenChange} />;
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
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Sources tree", () => {
  it("isolates sources and sections with Only and Alt-click without collapsing them", async () => {
    const user = userEvent.setup();
    await show();
    const tick = (name: string) => screen.getByRole("checkbox", { name: `Select ${name}` });
    const onlyNote = screen.getByRole("button", { name: "Only My notes on week 3" });
    onlyNote.focus();
    expect(onlyNote).toHaveFocus();
    await user.click(onlyNote);
    expect(tick("My notes on week 3")).toBeChecked();
    expect(tick("Lecture 1 recording")).not.toBeChecked();
    await user.keyboard("{Alt>}");
    await user.click(tick("Lecture 2 recording"));
    await user.keyboard("{/Alt}");
    expect(tick("Lecture 2 recording")).toBeChecked();
    expect(tick("My notes on week 3")).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Only sources in Part A" }));
    expect(tick("Course outline.pdf")).toBeChecked();
    expect(tick("Lecture 1 recording")).toBeChecked();
    expect(tick("Lecture 2 recording")).toBeChecked();
    expect(tick("My notes on week 3")).not.toBeChecked();
    await user.keyboard("{Alt>}");
    await user.click(screen.getByRole("checkbox", { name: "Select sources in Lecture 1" }));
    await user.keyboard("{/Alt}");
    expect(tick("Lecture 1 recording")).toBeChecked();
    expect(tick("week1-slides.pdf")).toBeChecked();
    expect(tick("Course outline.pdf")).not.toBeChecked();
    expect(tick("Lecture 2 recording")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Collapse Lecture 1" })).toHaveAttribute("aria-expanded", "true");
  });

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
    expect(rowOf("Part A")).toHaveClass("is-level-1");
    expect(within(rowOf("Part A")).getByTitle("2 PDFs · 2 recordings")).toHaveTextContent("4");
    expect(rowOf("Lecture 1").querySelector<HTMLElement>(".contents-guides")?.style.width).toBe("14px");
  });

  it("collapses and reopens the Sources section without losing its arrangement", async () => {
    const user = userEvent.setup();
    await show();
    const header = screen.getByRole("button", { name: "Sources" });
    expect(header).toHaveAttribute("aria-expanded", "true");
    await user.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("heading", { name: "Part A" })).not.toBeInTheDocument();
    await user.click(header);
    expect(screen.getByRole("heading", { name: "Part A" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Add section" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Collapse all" })).toBeVisible();
  });

  it("moves a row with the keyboard drag handle", async () => {
    const user = userEvent.setup();
    const moved = { ...base, placements: base.placements.map((item) => item.sourceId === "rec2" ? { ...item, sectionId: "b" } : item) };
    const move = vi.spyOn(knowledgeApi, "moveContents").mockResolvedValue(moved);
    await show();
    const row = rowOf("Lecture 2 recording");
    const destination = rowOf("Part B");
    document.querySelectorAll<HTMLElement>(".contents-list [data-row]").forEach((element, index) => {
      vi.spyOn(element, "getBoundingClientRect").mockReturnValue(new DOMRect(0, index * 40, 260, 40));
    });
    const handle = within(row).getByRole("button", { name: "Drag Lecture 2 recording" });
    handle.focus();
    expect(handle).toHaveFocus();
    await user.keyboard("[Space]");
    await waitFor(() => expect(row).toHaveClass("is-dragging"));
    await user.keyboard("{ArrowDown}{ArrowDown}");
    await waitFor(() => expect(destination).toHaveClass("is-drop-on"));
    await user.keyboard("[Space]");
    await waitFor(() => expect(move).toHaveBeenCalledExactlyOnceWith("lib", {
      items: [{ kind: "source", id: "rec2" }], parentId: "b", index: null,
    }));
  });

  it("hides what is under a collapsed section, remembers it, and shows the count", async () => {
    const user = userEvent.setup();
    await show();
    await user.click(screen.getByRole("button", { name: "Collapse Part A" }));
    expect(list().queryByText("Lecture 1 recording")).toBeNull();
    expect(within(rowOf("Part A")).getByTitle("2 PDFs · 2 recordings")).toHaveTextContent("4");
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
    const scope = screen.getByRole("checkbox", { name: "Select Lecture 2 recording" });
    await user.click(scope);
    const row = within(rowOf("Lecture 2 recording"));
    expect(row.getAllByRole("button").map((button) => button.getAttribute("aria-label") ?? button.textContent)).toEqual([
      "Drag Lecture 2 recording", expect.stringContaining("Lecture 2 recording"), "Only Lecture 2 recording", "Actions for Lecture 2 recording",
    ]);
    await user.click(screen.getByRole("button", { name: "Actions for Lecture 2 recording" }));
    await user.click(screen.getByRole("menuitem", { name: "Move to…" }));
    const picker = screen.getByRole("dialog", { name: "Move to…" });
    await user.type(within(picker).getByLabelText("Filter"), "part b");
    await user.click(within(picker).getByRole("option", { name: "Part B" }));
    expect(move).toHaveBeenCalledWith("lib", { items: [{ kind: "source", id: "rec2" }], parentId: "b", index: null });
    expect(await list().findByText("Lecture 2 recording")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Select Lecture 2 recording" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select Lecture 1 recording" })).toBeChecked();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("takes a single source out of its section and moves it to Trash without changing scope", async () => {
    const user = userEvent.setup();
    const entry = makeTrashItem({ id: "rec2", title: "Lecture 2 recording" });
    const unplaced = { ...base, placements: base.placements.filter((item) => item.sourceId !== entry.id) };
    const move = vi.spyOn(knowledgeApi, "moveContents").mockResolvedValue(unplaced);
    const trash = vi.spyOn(knowledgeApi, "trashSource").mockResolvedValue(entry);
    const { onTrashed } = await show();
    const check = (title: string) => screen.getByRole("checkbox", { name: `Select ${title}` });
    await user.click(check("Course outline.pdf"));
    const scope = window.localStorage.getItem("gunther:library-sources:lib");
    const actions = () => screen.getByRole("button", { name: `Actions for ${entry.title}` });
    await user.click(actions());
    await user.click(screen.getByRole("menuitem", { name: "Take out of section" }));
    expect(move).toHaveBeenCalledExactlyOnceWith("lib", { items: [{ kind: "source", id: entry.id }], parentId: null, index: null });
    await user.click(actions());
    expect(screen.queryByRole("menuitem", { name: "Take out of section" })).not.toBeInTheDocument();
    vi.mocked(knowledgeApi.contents).mockResolvedValue({ ...unplaced, sources: base.sources.filter((item) => item.id !== entry.id) });
    await user.click(screen.getByRole("menuitem", { name: "Move to Trash" }));
    expect(trash).toHaveBeenCalledExactlyOnceWith(entry.id);
    expect(onTrashed).toHaveBeenCalledExactlyOnceWith(entry);
    await waitFor(() => expect(screen.queryByText(entry.title)).not.toBeInTheDocument());
    expect(check("Course outline.pdf")).not.toBeChecked();
    expect(check("Lecture 1 recording")).toBeChecked();
    expect(window.localStorage.getItem("gunther:library-sources:lib")).toBe(scope);
  });

  it("starts a drag only at the handle and moves one row while leaving scope ticks intact", async () => {
    const user = userEvent.setup();
    class TestPointerEvent extends MouseEvent {
      readonly isPrimary = true;
      readonly pointerId = 1;
    }
    vi.stubGlobal("PointerEvent", TestPointerEvent);
    const moved = { ...base, placements: base.placements.map((item) => item.sourceId === "rec2" ? { ...item, sectionId: "b" } : item) };
    const move = vi.spyOn(knowledgeApi, "moveContents").mockResolvedValue(moved);
    await show();
    const checkbox = (title: string) => screen.getByRole("checkbox", { name: `Select ${title}` });
    const title = "Lecture 2 recording";
    await user.click(checkbox(title));
    const scope = window.localStorage.getItem("gunther:library-sources:lib");
    const row = rowOf(title);
    const destination = rowOf("Part B");
    document.querySelectorAll<HTMLElement>(".contents-list [data-row]").forEach((element, index) => {
      vi.spyOn(element, "getBoundingClientRect").mockReturnValue(new DOMRect(0, index * 40, 260, 40));
    });
    const start = { clientX: 20, clientY: row.getBoundingClientRect().top + 20 };
    const end = { clientX: 20, clientY: destination.getBoundingClientRect().top + 20 };
    fireEvent.pointerDown(within(row).getByRole("button", { name: /^Lecture 2 recording/ }), start);
    fireEvent.pointerMove(document, end);
    fireEvent.pointerUp(document, end);
    expect(move).not.toHaveBeenCalled();

    fireEvent.pointerDown(within(row).getByRole("button", { name: `Drag ${title}` }), start);
    fireEvent.pointerMove(row, { ...start, clientX: 30 });
    try {
      await waitFor(() => expect(row).toHaveClass("is-dragging"));
      fireEvent.pointerMove(row, end);
      await waitFor(() => expect(destination).toHaveClass("is-drop-on"));
    } finally {
      fireEvent.pointerUp(row, end);
    }
    expect(move).toHaveBeenCalledExactlyOnceWith("lib", {
      items: [{ kind: "source", id: "rec2" }], parentId: "b", index: null,
    });
    expect(checkbox(title)).not.toBeChecked();
    expect(checkbox("Lecture 1 recording")).toBeChecked();
    expect(window.localStorage.getItem("gunther:library-sources:lib")).toBe(scope);
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
