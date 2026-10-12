import { describe, expect, it } from "vitest";
import type { ContentsSection, ContentsSource, LibraryContents } from "@gunther/contracts";
import { applyMove, buildTree, describeSources, dropTarget, isDescendant, naturalOrder, nodeRef, readableLength, rowDetail, sectionCount, sectionPath, shortAuthors, subtreeStats, visibleRows } from "./contentsTree";

const section = (id: string, title: string, parentId: string | null, position: number): ContentsSection => ({ id, parentId, title, description: "", position, version: 1, passageCount: 0 });
const source = (id: string, title: string, kind: ContentsSource["kind"] = "file", extra: Partial<ContentsSource> = {}): ContentsSource => ({
  id, title, kind, createdAt: "2026-10-03T10:00:00", durationSeconds: null, authors: null, year: null, pages: null, mediaType: null, host: null, processing: "ready", ...extra,
});

// Part A { outline, Lecture 1 { rec1, slides1 }, Lecture 2 { rec2 } }, Part B {}, and two loose sources.
const contents: LibraryContents = {
  sections: [section("a", "Part A", null, 0), section("b", "Part B", null, 1), section("l1", "Lecture 1", "a", 1), section("l2", "Lecture 2", "a", 2)],
  placements: [
    { sourceId: "outline", sectionId: "a", position: 0 },
    { sourceId: "rec1", sectionId: "l1", position: 0 },
    { sourceId: "slides1", sectionId: "l1", position: 1 },
    { sourceId: "rec2", sectionId: "l2", position: 0 },
  ],
  sources: [
    source("outline", "Course outline.pdf", "file"), source("rec1", "Lecture 1", "recording"), source("slides1", "week1-slides.pdf"), source("rec2", "Lecture 2", "recording"),
    source("old", "Older link", "link", { createdAt: "2026-10-01T10:00:00" }), source("new", "Newer note", "note", { createdAt: "2026-10-12T10:00:00" }),
  ],
};
const tree = buildTree(contents);
const keys = (rows: ReturnType<typeof visibleRows>) => rows.map((row) => row.key);

describe("contents tree", () => {
  it("builds the tree from positions, sections and sources sharing one order", () => {
    expect(tree.roots.map((node) => node.id)).toEqual(["a", "b"]);
    const a = tree.sections.get("a")!;
    expect(a.children.map((node) => node.id)).toEqual(["outline", "l1", "l2"]);
    expect(tree.sections.get("l1")!.children.map((node) => node.id)).toEqual(["rec1", "slides1"]);
    expect(tree.parents.get("rec1")).toBe("l1");
    expect(tree.parents.get("l1")).toBe("a");
  });

  it("puts sources that are placed nowhere in Not placed yet, newest first", () => {
    expect(tree.unplaced.map((item) => item.id)).toEqual(["new", "old"]);
    const rows = visibleRows(tree, new Set());
    expect(keys(rows).slice(-3)).toEqual(["unplaced", "source:new", "source:old"]);
    expect(rows.find((row) => row.key === "unplaced")?.count).toBe("2");
  });

  it("hides Not placed yet when it is empty", () => {
    const all = buildTree({ ...contents, sources: contents.sources.filter((item) => !["old", "new"].includes(item.id)) });
    expect(keys(visibleRows(all, new Set()))).not.toContain("unplaced");
  });

  it("hides the children of a collapsed section and counts them", () => {
    const rows = visibleRows(tree, new Set(["a"]));
    expect(keys(rows)).toEqual(["section:a", "section:b", "empty:b", "unplaced", "source:new", "source:old"]);
    expect(rows[0]).toMatchObject({ collapsed: true, count: "2 documents · 2 recordings" });
    expect(rows.find((row) => row.key === "section:b")?.count).toBe("Empty");
  });

  it("says what a section holds by kind, up to two kinds", () => {
    const papers = buildTree({ sections: [section("r", "Readings", null, 0)], placements: [{ sourceId: "p1", sectionId: "r", position: 0 }, { sourceId: "p2", sectionId: "r", position: 1 }], sources: [source("p1", "P1", "paper"), source("p2", "P2", "paper")] });
    expect(sectionCount(papers.roots[0]!)).toBe("2 papers");
    expect(sectionCount(tree.sections.get("l2")!)).toBe("1 recording");
    expect(describeSources([source("x", "X", "file", { mediaType: "application/pdf" }), source("y", "Y", "recording"), source("z", "Z", "note")])).toBe("3 sources");
    expect(describeSources([source("x", "X", "file", { mediaType: "application/pdf" }), source("w", "W", "file", { mediaType: "application/pdf" }), source("y", "Y", "recording")])).toBe("2 PDFs · 1 recording");
  });

  it("gives every row its depth, with a stand-in row in an open empty section", () => {
    const rows = visibleRows(tree, new Set());
    expect(rows.filter((row) => row.type !== "unplaced").map((row) => [row.key, row.depth])).toEqual([
      ["section:a", 0], ["source:outline", 1], ["section:l1", 1], ["source:rec1", 2], ["source:slides1", 2], ["section:l2", 1], ["source:rec2", 2], ["section:b", 0], ["empty:b", 1], ["source:new", 1], ["source:old", 1],
    ]);
  });

  it("knows the sections above a row", () => {
    expect(sectionPath(tree, "rec1").map((step) => step.title)).toEqual(["Part A", "Lecture 1"]);
    expect(sectionPath(tree, "l1").map((step) => step.title)).toEqual(["Part A"]);
    expect(sectionPath(tree, "new")).toEqual([]);
  });

  it("filters rows but keeps their headings, ignoring collapse", () => {
    const rows = visibleRows(tree, new Set(["a", "l1"]), "slides");
    expect(keys(rows)).toEqual(["section:a", "section:l1", "source:slides1"]);
  });

  it("shows everything in a section whose own title matches", () => {
    expect(keys(visibleRows(tree, new Set(), "lecture 2"))).toEqual(["section:a", "section:l2", "source:rec2"]);
  });

  it("filters Not placed yet too", () => {
    expect(keys(visibleRows(tree, new Set(), "newer"))).toEqual(["unplaced", "source:new"]);
  });

  it("orders by name with numbers by value, ignoring case", () => {
    const titles = ["Lecture 10", "lecture 2", "Lecture 1", "Appendix"].map((title, index) => ({ type: "source" as const, id: String(index), source: source(String(index), title) }));
    expect(naturalOrder(titles).map((node) => node.type === "source" ? node.source.title : "")).toEqual(["Appendix", "Lecture 1", "lecture 2", "Lecture 10"]);
  });

  it("keeps a stable order for Chinese titles", () => {
    const titles = ["第二讲", "第一讲", "第三讲"].map((title, index) => ({ type: "source" as const, id: String(index), source: source(String(index), title) }));
    const once = naturalOrder(titles).map((node) => node.id);
    expect(naturalOrder(naturalOrder(titles)).map((node) => node.id)).toEqual(once);
    expect(new Set(once).size).toBe(3);
    const same = ["讲", "讲"].map((title, index) => ({ type: "source" as const, id: `s${index}`, source: source(`s${index}`, title) }));
    expect(naturalOrder(same).map((node) => node.id)).toEqual(["s0", "s1"]);
  });

  it("knows what is below a section", () => {
    expect(isDescendant(tree, "rec1", "a")).toBe(true);
    expect(isDescendant(tree, "l1", "a")).toBe(true);
    expect(isDescendant(tree, "a", "a")).toBe(false);
    expect(isDescendant(tree, "b", "a")).toBe(false);
    expect(subtreeStats(tree.sections.get("a")!)).toEqual({ sections: 2, sources: 4 });
  });
});

describe("row details", () => {
  const year = new Date().getFullYear();
  it("shows each kind's own detail: what tells sources apart, not the day they came in", () => {
    expect(rowDetail(source("r", "R", "recording", { durationSeconds: 4324 }))).toBe("1:12:04");
    expect(rowDetail(source("r", "R", "recording", { durationSeconds: 3520 }))).toBe("58:40");
    expect(rowDetail(source("p", "P", "paper", { authors: "Chiyuan Zhang, Samy Bengio, Moritz Hardt", year: 2017 }))).toBe("Zhang et al. · 2017");
    expect(rowDetail(source("f", "F", "file", { mediaType: "application/pdf", pages: 24 }))).toBe("PDF · 24 pages");
    expect(rowDetail(source("f", "F", "file", { mediaType: "application/pdf", pages: 1 }))).toBe("PDF · 1 page");
    expect(rowDetail(source("f", "F", "file", { mediaType: "application/pdf" }))).toBe("PDF");
    expect(rowDetail(source("l", "L", "link", { host: "arxiv.org", createdAt: `${year}-10-11T10:00:00` }))).toBe("arxiv.org");
    expect(rowDetail(source("n", "N", "note", { createdAt: `${year}-10-12T10:00:00` }))).toMatch(/^Oct 12$/);
    // With nothing else to say, the day it was added.
    expect(rowDetail(source("r", "R", "recording", { createdAt: `${year}-10-03T10:00:00` }))).toMatch(/^Oct 3$/);
  });

  it("shortens authors to family names", () => {
    expect(shortAuthors("Diederik P. Kingma")).toBe("Kingma");
    expect(shortAuthors("Diederik P. Kingma, Jimmy Ba")).toBe("Kingma & Ba");
    expect(shortAuthors("Kingma; Ba; Reddi")).toBe("Kingma et al.");
    expect(shortAuthors("Ann Lee and Bo Chen")).toBe("Lee & Chen");
    expect(shortAuthors(null)).toBe("");
  });

  it("reads a total length as hours and minutes", () => {
    expect(readableLength(40)).toBe("40 s");
    expect(readableLength(3520)).toBe("59 min");
    expect(readableLength(4324 + 3520)).toBe("2 h 11 min");
    expect(readableLength(7200)).toBe("2 h");
  });

  it("says when a source is still being read or could not be", () => {
    expect(rowDetail(source("a", "A", "file", { processing: "reading" }))).toBe("Reading…");
    expect(rowDetail(source("a", "A", "file", { processing: "failed" }))).toBe("Couldn’t read");
  });
});

describe("drop targets", () => {
  const rows = visibleRows(tree, new Set());
  const rec2 = [{ kind: "source" as const, id: "rec2" }];
  const l1 = [{ kind: "section" as const, id: "l1" }];

  it("drops onto a heading at the end of that section", () => {
    expect(dropTarget(tree, rows, "section:b", "on", rec2)).toEqual({ parentId: "b", index: null });
  });

  it("drops onto an empty section's stand-in row into that section, but not a section into itself", () => {
    expect(dropTarget(tree, rows, "empty:b", "on", rec2)).toEqual({ parentId: "b", index: null });
    expect(dropTarget(tree, rows, "empty:b", "on", [{ kind: "section", id: "a" }])).toEqual({ parentId: "b", index: null });
    expect(dropTarget(tree, rows, "empty:b", "on", [{ kind: "section", id: "b" }])).toBeNull();
  });

  it("drops between rows into that row's section", () => {
    // Before "week1-slides" inside Lecture 1: remaining children are rec1, slides1 -> index 1.
    expect(dropTarget(tree, rows, "source:slides1", "before", rec2)).toEqual({ parentId: "l1", index: 1 });
    expect(dropTarget(tree, rows, "source:slides1", "after", rec2)).toEqual({ parentId: "l1", index: 2 });
  });

  it("counts places among the children that stay when moving inside one section", () => {
    // Move rec1 after slides1 in Lecture 1: slides1 is the only one left, so index 1.
    expect(dropTarget(tree, rows, "source:slides1", "after", [{ kind: "source", id: "rec1" }])).toEqual({ parentId: "l1", index: 1 });
    // Move Lecture 2 before Lecture 1 in Part A: remaining are outline, l1 -> index 1.
    expect(dropTarget(tree, rows, "section:l1", "before", [{ kind: "section", id: "l2" }])).toEqual({ parentId: "a", index: 1 });
  });

  it("drops a section beside a top-level section", () => {
    expect(dropTarget(tree, rows, "section:b", "before", l1)).toEqual({ parentId: null, index: 1 });
  });

  it("takes sources out of their section on Not placed yet", () => {
    expect(dropTarget(tree, rows, "unplaced", "on", rec2)).toEqual({ parentId: null, index: null });
    expect(dropTarget(tree, rows, "source:old", "before", rec2)).toEqual({ parentId: null, index: null });
  });

  it("does not allow a section into Not placed yet, itself or below itself", () => {
    expect(dropTarget(tree, rows, "unplaced", "on", l1)).toBeNull();
    expect(dropTarget(tree, rows, "section:l1", "on", l1)).toBeNull();
    const a = [{ kind: "section" as const, id: "a" }];
    expect(dropTarget(tree, rows, "section:l2", "on", a)).toBeNull();
    expect(dropTarget(tree, rows, "source:rec2", "before", a)).toBeNull();
    expect(dropTarget(tree, rows, "source:rec1", "after", a)).toBeNull();
  });

  it("does not put a source at the top level", () => {
    expect(dropTarget(tree, rows, "section:b", "before", rec2)).toBeNull();
  });
});

describe("applying a move locally", () => {
  it("moves a source to the end of another section", () => {
    const next = buildTree(applyMove(contents, { items: [{ kind: "source", id: "rec2" }], parentId: "l1", index: null }));
    expect(next.sections.get("l1")!.children.map((node) => node.id)).toEqual(["rec1", "slides1", "rec2"]);
    expect(next.sections.get("l2")!.children).toEqual([]);
  });

  it("moves several at an index, keeping their order, and takes sources out", () => {
    const next = buildTree(applyMove(contents, { items: [{ kind: "source", id: "rec2" }, { kind: "section", id: "b" }], parentId: "a", index: 1 }));
    expect(next.sections.get("a")!.children.map((node) => node.id)).toEqual(["outline", "rec2", "b", "l1", "l2"]);
    const out = buildTree(applyMove(contents, { items: [nodeRef(tree.sections.get("a")!.children[0]!)], parentId: null, index: null }));
    expect(out.unplaced.map((item) => item.id)).toContain("outline");
  });

  it("reorders the top level", () => {
    const next = buildTree(applyMove(contents, { items: [{ kind: "section", id: "b" }, { kind: "section", id: "a" }], parentId: null, index: 0 }));
    expect(next.roots.map((node) => node.id)).toEqual(["b", "a"]);
  });
});
