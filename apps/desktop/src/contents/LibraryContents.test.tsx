import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ContentsSource, KnowledgeTopic, LibraryContents as Contents } from "@gunther/contracts";
import { knowledgeApi } from "../api";
import { LibraryContents } from "./LibraryContents";

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

const props = () => ({ baseId: "lib", onOpenSource: vi.fn(), onAsk: vi.fn(), onAddSource: vi.fn(), onChanged: vi.fn() });
const show = async (contents: Contents = base, extra: Partial<ReturnType<typeof props>> = {}) => {
  vi.spyOn(knowledgeApi, "contents").mockResolvedValue(contents);
  const all = { ...props(), ...extra };
  render(<LibraryContents {...all} />);
  await screen.findByRole("heading", { name: "Part A" });
  return all;
};
const list = () => within(document.querySelector(".contents-list") as HTMLElement);
const panel = () => within(screen.getByRole("complementary", { name: "Details" }));
const rowOf = (title: string) => list().getByText(title).closest(".contents-row") as HTMLElement;

beforeEach(() => {
  window.localStorage.clear();
  vi.spyOn(knowledgeApi, "sourceDigest").mockResolvedValue({ sourceId: "x", state: "off", stale: false, method: null, offReason: "no_key", error: null, digest: null });
});
afterEach(() => { vi.restoreAllMocks(); });

describe("Library contents", () => {
  it("refreshes duration estimates until audio measurement finishes", async () => {
    vi.useFakeTimers();
    try {
      const pending = { ...base, sources: base.sources.map((item) => item.id === "rec1" ? { ...item, durationSeconds: 60, durationPending: true } : item) };
      const measured = { ...base, sources: base.sources.map((item) => item.id === "rec1" ? { ...item, durationSeconds: 120, durationPending: false } : item) };
      const load = vi.spyOn(knowledgeApi, "contents").mockResolvedValueOnce(pending).mockResolvedValue(measured);
      render(<LibraryContents {...props()} />);
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

  it("shows a clicked source in the panel, and opens it on a double click, Enter or ↗ with the visible rows as the J / K queue", async () => {
    const user = userEvent.setup();
    const { onOpenSource } = await show();
    await user.click(list().getByText("Lecture 2 recording"));
    expect(onOpenSource).not.toHaveBeenCalled();
    expect(panel().getByLabelText("Name")).toHaveValue("Lecture 2 recording");
    expect(panel().getByText("58:40")).toBeVisible();
    expect(panel().getByRole("button", { name: "Lecture 2" })).toBeVisible();
    await user.dblClick(list().getByText("Lecture 2 recording"));
    expect(onOpenSource).toHaveBeenCalledWith("rec2", ["outline", "rec1", "slides1", "rec2", "note", "link"]);
    await user.click(panel().getByRole("button", { name: "Open" }));
    expect(onOpenSource).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Open week1-slides.pdf" }));
    expect(onOpenSource).toHaveBeenLastCalledWith("slides1", expect.any(Array));
    list().getByText("Course outline.pdf").closest("button")!.focus();
    await user.keyboard("{Enter}");
    expect(onOpenSource).toHaveBeenLastCalledWith("outline", expect.any(Array));
  });

  it("walks the rows with the arrow keys, and Left goes up to the section", async () => {
    const user = userEvent.setup();
    await show();
    await user.click(list().getByText("Lecture 1 recording"));
    list().getByText("Lecture 1 recording").closest("button")!.focus();
    await user.keyboard("{ArrowDown}");
    expect(list().getByText("week1-slides.pdf").closest("button")).toHaveFocus();
    expect(panel().getByLabelText("Name")).toHaveValue("week1-slides.pdf");
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("button", { name: "Lecture 1" })).toHaveFocus();
    expect(panel().getByLabelText("Name")).toHaveValue("Lecture 1");
    await user.keyboard("{ArrowLeft}");
    expect(list().queryByText("week1-slides.pdf")).toBeNull();
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
    const { onChanged } = await show();
    vi.mocked(knowledgeApi.contents).mockResolvedValue({ ...base, sections: [...base.sections, section("c", "New section", null, 2)] });
    await user.click(screen.getByRole("button", { name: "Add section" }));
    const field = await screen.findByLabelText("Section name");
    expect(field).toHaveFocus();
    expect(field).toHaveValue("New section");
    expect(save).toHaveBeenCalledWith("lib", { title: "New section", description: "", parentId: null });
    expect(onChanged).toHaveBeenCalled();
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
    const { onChanged } = await show();
    await user.click(screen.getByRole("button", { name: "Actions for Lecture 2 recording" }));
    await user.click(screen.getByRole("menuitem", { name: "Move to…" }));
    const picker = screen.getByRole("dialog", { name: "Move to…" });
    await user.type(within(picker).getByLabelText("Filter"), "part b");
    await user.click(within(picker).getByRole("option", { name: "Part B" }));
    expect(move).toHaveBeenCalledWith("lib", { items: [{ kind: "source", id: "rec2" }], parentId: "b", index: null });
    expect(await list().findByText("Lecture 2 recording")).toBeVisible();
    expect(onChanged).toHaveBeenCalled();
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

  it("shows several selected rows in the panel and takes them out of their sections", async () => {
    const user = userEvent.setup();
    const move = vi.spyOn(knowledgeApi, "moveContents").mockResolvedValue(base);
    await show();
    await user.click(screen.getByRole("checkbox", { name: "Select Course outline.pdf" }));
    await user.keyboard("{Shift>}");
    await user.click(screen.getByRole("checkbox", { name: "Select week1-slides.pdf" }));
    await user.keyboard("{/Shift}");
    expect(screen.getByText("3 selected")).toBeVisible();
    expect(screen.getByRole("heading", { name: "2 PDFs · 1 recording" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Take out" }));
    expect(move).toHaveBeenCalledWith("lib", { items: [{ kind: "source", id: "outline" }, { kind: "source", id: "rec1" }, { kind: "source", id: "slides1" }], parentId: null, index: null });
    await waitFor(() => expect(screen.queryByText("3 selected")).toBeNull());
  });

  it("adds a row with ⌘-click to the one shown, and lets go of a selection", async () => {
    const user = userEvent.setup();
    await show();
    await user.click(list().getByText("My notes on week 3"));
    await user.keyboard("{Meta>}");
    await user.click(list().getByText("A blog post about Adam"));
    await user.keyboard("{/Meta}");
    expect(screen.getByText("2 selected")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Clear the selection" }));
    expect(screen.queryByText("2 selected")).toBeNull();
    expect(panel().getByRole("heading", { name: "This library" })).toBeVisible();
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
    render(<LibraryContents {...props()} />);
    await screen.findByRole("heading", { name: "Recordings" });
    await user.click(screen.getByRole("button", { name: "Actions for Recordings" }));
    await user.click(screen.getByRole("menuitem", { name: "Sort by name" }));
    expect(order).toHaveBeenCalledWith("lib", { parentId: "r", children: [{ kind: "source", id: "Lecture 1" }, { kind: "source", id: "Lecture 2" }, { kind: "source", id: "Lecture 10" }] });
    await waitFor(() => expect([...document.querySelectorAll(".contents-source strong")].map((node) => node.textContent)).toEqual(["Lecture 1", "Lecture 2", "Lecture 10"]));
  });

  it("asks before deleting a section and says what happens to its sources", async () => {
    const user = userEvent.setup();
    const remove = vi.spyOn(knowledgeApi, "deleteSection").mockResolvedValue({ ...base, sections: base.sections.filter((item) => !["a", "l1", "l2"].includes(item.id)), placements: [] });
    await show();
    await user.click(screen.getByRole("button", { name: "Actions for Part A" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete section" }));
    expect(panel().getByText("Delete “Part A” and its 2 sub-sections? Their 4 sources go back to Not placed yet; no source is deleted.")).toBeVisible();
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete section" }));
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

  it("asks a section, but only one that has something in it", async () => {
    const user = userEvent.setup();
    const { onAsk } = await show();
    await user.click(screen.getByRole("button", { name: "Actions for Part B" }));
    expect(screen.getByRole("menuitem", { name: "Ask this section" })).toBeDisabled();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Actions for Part A" }));
    await user.click(screen.getByRole("menuitem", { name: "Ask this section" }));
    expect(onAsk).toHaveBeenCalledWith("a");
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

  it("writes a section overview and opens a cited paper", async () => {
    const user = userEvent.setup();
    vi.spyOn(knowledgeApi, "topicOverview").mockRejectedValue(new Error("none"));
    const write = vi.spyOn(knowledgeApi, "writeTopicOverview").mockResolvedValue({
      topicId: "r", markdown: "# Readings\n\n## Papers\n\nGenome quality needs controls. [1]\n",
      citations: [{ number: 1, sourceId: "p1", sourceTitle: "Quality paper", blockId: "b1", revisionId: "r1", quote: "Genome quality needs controls." }],
      method: "local", sourceCount: 1, createdAt: "2026-09-28T10:00:00",
    });
    const { onOpenSource } = await showReadings();
    await user.click(screen.getByRole("button", { name: "Readings" }));
    await user.click(await panel().findByRole("button", { name: "Write overview" }));
    expect(write).toHaveBeenCalledWith("lib", "r");
    expect(await screen.findByText("Genome quality needs controls. [1]")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Quality paper" }));
    expect(onOpenSource).toHaveBeenCalledWith("p1", ["p1"]);
  });

  it("renames a section and writes its note in the panel", async () => {
    const user = userEvent.setup();
    const save = vi.spyOn(knowledgeApi, "saveTopic").mockResolvedValue({ id: "l1", knowledgeBaseId: "lib", parentId: "a", title: "Lecture 1 · Intro", description: "", position: 1, version: 2, blockIds: [], sourceIds: [] });
    await show();
    await user.click(screen.getByRole("button", { name: "Lecture 1" }));
    expect(panel().getByRole("button", { name: "Part A" })).toBeVisible();
    expect(panel().getByText("2 sources")).toBeVisible();
    const name = panel().getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Lecture 1 · Intro{Enter}");
    expect(save).toHaveBeenCalledWith("lib", { title: "Lecture 1 · Intro", description: "", parentId: "a", version: 1 }, "l1");
    await user.type(panel().getByLabelText("Note"), "Week one{Enter}");
    await waitFor(() => expect(save).toHaveBeenLastCalledWith("lib", expect.objectContaining({ description: "Week one" }), "l1"));
  });

  it("adds a sub-section from the panel", async () => {
    const user = userEvent.setup();
    const save = vi.spyOn(knowledgeApi, "saveTopic").mockResolvedValue({ id: "c", knowledgeBaseId: "lib", parentId: "b", title: "New section", description: "", position: 0, version: 1, blockIds: [], sourceIds: [] });
    await show();
    await user.click(screen.getByRole("button", { name: "Part B" }));
    await user.click(panel().getByRole("button", { name: "Sub-section" }));
    expect(save).toHaveBeenCalledWith("lib", { title: "New section", description: "", parentId: "b" });
  });

  it("renames a source in the panel and in its row", async () => {
    const user = userEvent.setup();
    const rename = vi.spyOn(knowledgeApi, "renameSource").mockResolvedValue({} as never);
    await show();
    vi.mocked(knowledgeApi.contents).mockResolvedValue({ ...base, sources: base.sources.map((item) => item.id === "note" ? { ...item, title: "Week 3 questions" } : item) });
    await user.click(list().getByText("My notes on week 3"));
    const name = panel().getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Week 3 questions{Enter}");
    expect(rename).toHaveBeenCalledWith("note", "Week 3 questions");
    expect(await list().findByText("Week 3 questions")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Actions for A blog post about Adam" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const field = screen.getByLabelText("Source name");
    expect(field).toHaveFocus();
    await user.clear(field);
    await user.type(field, "Adam, explained{Enter}");
    expect(rename).toHaveBeenLastCalledWith("link", "Adam, explained");
  });

  it("shows the library in the panel when nothing is chosen", async () => {
    const user = userEvent.setup();
    const onAskLibrary = vi.fn();
    vi.spyOn(knowledgeApi, "contents").mockResolvedValue(base);
    render(<LibraryContents {...props()} library={{ title: "ML 2026", eyebrow: "Course", description: "Lectures and readings." }} onAskLibrary={onAskLibrary} />);
    await screen.findByRole("heading", { name: "ML 2026" });
    expect(panel().getByText("Lectures and readings.")).toBeVisible();
    expect(panel().getByText("2 recordings")).toBeVisible();
    expect(panel().getByText("2 PDFs")).toBeVisible();
    expect(panel().getByRole("button", { name: "2 not placed yet" })).toBeVisible();
    await user.click(panel().getByRole("button", { name: /My notes on week 3/ }));
    expect(panel().getByLabelText("Name")).toHaveValue("My notes on week 3");
    await user.click(panel().getByRole("button", { name: "Back to the library" }));
    await user.click(panel().getByRole("button", { name: "Ask this library" }));
    expect(onAskLibrary).toHaveBeenCalled();
  });

  it("offers a way to fill an empty section", async () => {
    const user = userEvent.setup();
    await show();
    const empty = document.querySelector('[data-row="empty:b"]') as HTMLElement;
    expect(empty).toHaveTextContent("Empty. Drag sources here, or");
    await user.click(within(empty).getByRole("button", { name: "add sources" }));
    expect(screen.getByRole("dialog", { name: "Add sources to “Part B”" })).toBeVisible();
  });

  it("says a library with nothing in it is ready for its first source", async () => {
    vi.spyOn(knowledgeApi, "contents").mockResolvedValue({ sections: [], placements: [], sources: [] });
    const { onAddSource } = props();
    render(<LibraryContents {...props()} onAddSource={onAddSource} />);
    await userEvent.click(await screen.findByRole("button", { name: "Choose a source" }));
    expect(onAddSource).toHaveBeenCalled();
  });

  it("never says topic", async () => {
    await show();
    expect(document.body.textContent).not.toMatch(/topic/i);
  });
});

async function showReadings() {
  const contents: Contents = { sections: [section("r", "Readings", null, 0)], placements: [{ sourceId: "p1", sectionId: "r", position: 0 }], sources: [source("p1", "Quality paper", "paper")] };
  vi.spyOn(knowledgeApi, "contents").mockResolvedValue(contents);
  const all = props();
  render(<LibraryContents {...all} />);
  await screen.findByRole("heading", { name: "Readings" });
  return all;
}
