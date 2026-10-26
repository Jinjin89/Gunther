import type { DocumentElement, SourceDocument } from "@gunther/contracts";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CanonicalReader } from "./CanonicalReader";

function element(id: string, kind: string, content: string, extra: Partial<DocumentElement> = {}): DocumentElement {
  return { id, kind, content, parentId: null, locator: "Page 1", headings: [], anchor: { page: 1 }, mentions: [], ...extra };
}

const smith: SourceDocument["references"] = [{ id: "ref_1", elementId: "el_ref", label: "1", raw: "[1] Smith 2020.", metadata: {} }];

function document(elements: DocumentElement[], manifest: Record<string, unknown> = {}, references = smith): SourceDocument {
  return {
    sourceId: "src_paper", revisionId: "srev_1", parser: "fixture", nextOffset: null,
    processing: { state: "ready", jobId: null, revisionId: "srev_1", warning: null },
    manifest, elements, references,
  };
}

/** The manifest of a document the structure plan assembled (schema 2). */
function planned(structure: Record<string, unknown> = { state: "checked", by: "deepseek:deepseek-flash", error: null }, more: Record<string, unknown> = {}): Record<string, unknown> {
  return { schema: 2, documentType: "paper", structure, ...more };
}

/** What the reader prints for headings and paragraphs, in order. */
function reading(container: HTMLElement): (string | null)[] {
  return [...container.querySelectorAll("h1, h2, h3, h4, p")].map((node) => node.textContent);
}

describe("CanonicalReader", () => {
  it("uses Unicode code points and links a citation back from its bibliography", () => {
    render(<CanonicalReader document={document([
      element("el_p", "paragraph", "😀 中文 [1] 后文。", { mentions: [{ id: "mention_1", start: 5, end: 8, raw: "[1]", status: "resolved", targets: ["ref_1"] }] }),
      element("el_ref", "reference", "[1] Smith 2020."),
    ])} />);
    expect(screen.getByRole("link", { name: "[1]" })).toHaveAttribute("href", "#ref_1");
    expect(screen.getByText(/😀 中文/).textContent).toBe("😀 中文 [1] 后文。");
    expect(screen.getAllByText("[1] Smith 2020.")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Return to citation" })).toHaveAttribute("href", "#mention_1");
    expect(screen.queryByText("Show page furniture")).not.toBeInTheDocument();
  });

  it("keeps all six page-furniture kinds out of the reading text", async () => {
    const user = userEvent.setup();
    const kinds = ["page_header", "page_footer", "footnote", "page_number", "sidebar", "margin_note"];
    const data = document([
      element("body", "paragraph", "Readable body paragraph."),
      element("metadata", "metadata", "Original publication notice."),
      ...kinds.map((kind, index) => element(`furniture_${kind}`, kind, `Original ${kind} text.`, { locator: `Page ${index + 1}` })),
    ]);
    render(<CanonicalReader document={data} />);
    expect(screen.getByText("Readable body paragraph.")).toBeVisible();
    for (const kind of kinds) expect(screen.queryByText(`Original ${kind} text.`)).not.toBeInTheDocument();
    expect(screen.queryByText("Show page furniture")).not.toBeInTheDocument();
    await user.click(screen.getByText("Document details"));
    expect(screen.getByText("Original publication notice.")).toBeVisible();
  });

  it("leaves an older document in its stored order and prints no placeholder caption", () => {
    const { container } = render(<CanonicalReader document={document([
      element("logo", "embedded_image", "Figure", { payload: { imageAssetId: "ast_logo", role: "decoration" } }),
      element("title", "heading", "A study", { payload: { role: "title", level: 1 } }),
      element("intro", "paragraph", "Introduction text."),
      element("summary", "paragraph", "We present a method.", { payload: { role: "abstract", abstractSource: "first-page-position" } }),
      element("results", "heading", "Results", { payload: { level: 2 } }),
      element("uncaptioned", "figure", "Figure", { payload: { imageAssetId: "ast_plot" } }),
      element("note", "note", "Stored as a note."),
    ], { schema: 1, structure: { policy: "source-defined", headings: 1 } })} />);
    // Nothing is moved or labelled; the older "structure" entry is not a status.
    expect(reading(container).slice(0, 4)).toEqual(["A study", "Introduction text.", "We present a method.", "Results"]);
    expect(screen.queryByRole("heading", { name: "Abstract" })).not.toBeInTheDocument();
    expect(container.querySelector(".gx-structure-status")).toBeNull();
    expect(screen.queryByText("Figure")).not.toBeInTheDocument();
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "Notes" })).not.toBeInTheDocument();
  });

  it("preserves saved figures, unavailable positions and table cell spans", () => {
    render(<CanonicalReader document={document([
      element("el_image", "figure", "Figure", { payload: { imageAssetId: "ast_plot", caption: "Figure 1. Study result." } }),
      element("el_missing", "figure", "Figure"),
      element("el_table", "table", "Results", { payload: { table: { table_cells: [{ text: "Measure", start_row_offset_idx: 0, end_row_offset_idx: 2, start_col_offset_idx: 0, end_col_offset_idx: 2, column_header: true }] } } }),
    ])} />);
    expect(screen.getByRole("img")).toHaveAttribute("src", expect.stringContaining("/api/assets/ast_plot"));
    expect(screen.getByText("Image unavailable · Page 1")).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Measure" })).toHaveAttribute("rowspan", "2");
    expect(screen.getByRole("columnheader", { name: "Measure" })).toHaveAttribute("colspan", "2");
  });

  it("separates page furniture and visual annotations from body paragraphs", () => {
    const data = document([
      element("heading", "heading", "Retention policy", { payload: { level: 2 } }),
      element("child", "heading", "Escalation paths", { payload: { level: 3 } }),
      element("header", "page_header", "Organization quarterly review"),
      element("body", "paragraph", "The original policy paragraph."),
      element("visual", "figure", "Figure", { payload: { documentRef: "#/pictures/0", imageAssetId: "ast_plot", caption: "Figure 1. Revenue comparison." } }),
      element("label", "figure_text", "Revenue 0.1 0.2 0.3", { payload: { visualOwner: "#/pictures/0" } }),
      element("caption", "caption", "Figure 1. Revenue comparison.", { payload: { visualOwner: "#/pictures/0" } }),
    ]);
    data.manifest.metadata = { publisher: [{ value: "Example Press", origin: "xmp:publisher", status: "observed" }], fileCreated: [{ value: "D:20200102", origin: "pdf-info", status: "observed" }] };
    const { container } = render(<CanonicalReader document={data} />);
    expect(screen.getByRole("heading", { level: 2, name: "Retention policy" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 3, name: "Escalation paths" })).toBeVisible();
    expect(screen.getByText("Example Press").closest("details")).not.toBeNull();
    expect(screen.queryByText("Organization quarterly review")).not.toBeInTheDocument();
    expect(screen.queryByText("Revenue 0.1 0.2 0.3")).not.toBeInTheDocument();
    expect(container.querySelectorAll("figcaption")).toHaveLength(1);
    expect(screen.getByText("The original policy paragraph.")).toBeVisible();
  });

  it("renders native scripts, original equations and continued sentences without chart debris", () => {
    const { container } = render(<CanonicalReader document={document([
      element("start", "paragraph", "Read xi and R2. We", { payload: { documentRef: "first", inlineSpans: [{ start: 6, end: 7, script: "sub" }, { start: 13, end: 14, script: "super" }] } }),
      element("label", "figure_text", "Knowledge transfer ?"),
      element("finish", "paragraph", "attribute this to the observations.", { payload: { documentRef: "second", continuesDocumentRef: "first" } }),
      element("formula", "equation", "", { payload: { imageAssetId: "ast_equation", widthPt: 90 } }),
    ], { bodyFontSize: 9 })} />);
    expect(container.querySelector("#start")).toHaveTextContent("Read xi and R2. We attribute this to the observations.");
    expect(container.querySelector("#start sub")).toHaveTextContent("i");
    expect(container.querySelector("#start sup")).toHaveTextContent("2");
    expect(container.querySelector("#finish")).not.toBeNull();
    expect(screen.queryByText("Knowledge transfer ?")).not.toBeInTheDocument();
    const formula = screen.getByRole("img", { name: "Formula from Page 1" });
    expect(formula).toHaveAttribute("src", expect.stringContaining("ast_equation"));
    expect(formula.style.width).toBe("min(100%, 10.00em)");
    expect(screen.queryByText("Formula text")).not.toBeInTheDocument();
  });

  it("shows a unique author list and a readable file timestamp with the source offset", async () => {
    const data = document([]);
    data.manifest.metadata = {
      authors: [{ value: "Alex Example", origin: "pdf-info" }, { value: "Alex Example; Sam Example", origin: "xmp" }],
      fileCreated: [{ value: "D:20260128004804+05'30'", origin: "pdf-info" }],
    };
    render(<CanonicalReader document={data} />);
    await userEvent.click(screen.getByText("Document details"));
    expect(screen.getByText("Alex Example · Sam Example")).toBeVisible();
    expect(screen.getByText("2026-01-28 00:48:04 UTC+05:30")).toHaveAttribute("title", expect.stringContaining("D:20260128004804"));
  });
});

describe("CanonicalReader with a structured (schema 2) document", () => {
  const front = () => [
    element("title", "heading", "A study of reading", { payload: { role: "title", level: 1, part: "front" } }),
    element("subtitle", "heading", "A longer look", { payload: { role: "subtitle", level: 1, part: "front" } }),
    element("by_1", "metadata", "Ada Lovelace", { payload: { role: "authors", part: "front" } }),
    element("by_2", "metadata", "Grace Hopper; Alan Turing", { payload: { role: "authors", part: "front" } }),
    element("keywords", "metadata", "Keywords: reading; layout", { payload: { role: "keywords", part: "front" } }),
  ];

  it("prints the title, one authors line and the keywords, then the text in its stored order", () => {
    const { container } = render(<CanonicalReader document={document([
      ...front(),
      element("head", "page_header", "Running head text"),
      element("abstract_1", "paragraph", "We present a method.", { payload: { role: "abstract", part: "front" } }),
      element("abstract_2", "paragraph", "It works well.", { payload: { role: "abstract", part: "front" } }),
      element("intro", "heading", "Introduction", { payload: { level: 1, part: "body" } }),
      element("intro_text", "paragraph", "Reading comes first.", { payload: { part: "body" } }),
      element("method", "heading", "Method", { payload: { level: 2, part: "body" } }),
      element("point", "list_item", "Open the file", { payload: { marker: "1." } }),
      element("labels", "figure_text", "axis labels"),
      element("el_ref", "reference", "[1] Smith 2020.", { payload: { reference: true, part: "back" } }),
    ], planned())} />);
    expect(reading(container)).toEqual([
      "Structure checked by deepseek-flash",
      "A study of reading", "A longer look",
      "Ada Lovelace, Grace Hopper, Alan Turing",
      "Keywords: reading; layout",
      "Abstract", "We present a method.", "It works well.",
      "Introduction", "Reading comes first.", "Method", "1. Open the file",
      "References", "[1] Smith 2020.",
    ]);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "A study of reading" })).toBeVisible();
    // Sections stored from level 1 sit under the title, not beside it.
    expect(screen.getByRole("heading", { level: 2, name: "Introduction" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 3, name: "Method" })).toBeVisible();
    expect(container.querySelector(".gx-document-authors")).toHaveTextContent("Ada Lovelace, Grace Hopper, Alan Turing");
    expect(container.querySelector(".gx-document-keywords > span")).toHaveTextContent("Keywords:");
    expect(screen.queryByText("Running head text")).not.toBeInTheDocument();
    expect(screen.queryByText("axis labels")).not.toBeInTheDocument();
  });

  it("keeps the levels of sections that are already stored below the title", () => {
    const { container } = render(<CanonicalReader document={document([
      element("title", "heading", "A study", { payload: { role: "title", level: 1 } }),
      element("results", "heading", "Results", { payload: { level: 2 } }),
      element("detail", "heading", "Detail", { payload: { level: 3 } }),
    ], planned(), [])} />);
    expect(screen.getByRole("heading", { level: 2, name: "Results" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 3, name: "Detail" })).toBeVisible();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
  });

  it("shows chapters stored from level 1 as they are when the document prints no title", () => {
    render(<CanonicalReader document={document([
      element("chapter", "heading", "Chapter one", { payload: { level: 1 } }),
      element("section", "heading", "A section", { payload: { level: 2 } }),
    ], planned(), [])} />);
    expect(screen.getByRole("heading", { level: 1, name: "Chapter one" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "A section" })).toBeVisible();
  });

  it("adds the Keywords label to keywords that print none, and not twice to ones that do", () => {
    const { container } = render(<CanonicalReader document={document([
      element("plain", "metadata", "reading; layout", { payload: { role: "keywords" } }),
      element("chinese", "metadata", "关键词：深度学习；阅读", { payload: { role: "keywords" } }),
      element("empty", "metadata", "Key words:", { payload: { role: "keywords" } }),
      element("nameless", "metadata", " ; ", { payload: { role: "authors" } }),
    ], planned(), [])} />);
    expect(container.querySelector("#plain")).toHaveTextContent("Keywords: reading; layout");
    expect(container.querySelector("#chinese")).toHaveTextContent("Keywords: 深度学习；阅读");
    // A label with no keywords after it, and an authors line with no names, print nothing.
    expect(container.querySelector("#empty")).toBeNull();
    expect(container.querySelector("#nameless")).toBeNull();
  });

  it("labels an abstract once when the paper prints no heading for it, and never when it does", () => {
    const unheaded = render(<CanonicalReader document={document([
      ...front(),
      element("abstract_1", "paragraph", "We present a method.", { payload: { role: "abstract" } }),
      element("abstract_2", "paragraph", "It works well.", { payload: { role: "abstract" } }),
      element("intro", "heading", "Introduction", { payload: { level: 2 } }),
    ], planned())} />);
    expect(screen.getAllByRole("heading", { name: "Abstract" })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 2, name: "Abstract" })).toHaveAttribute("id", "abstract_1-label");
    expect(screen.getByRole("link", { name: "Abstract" })).toHaveAttribute("href", "#abstract_1-label");
    unheaded.unmount();

    const { container } = render(<CanonicalReader document={document([
      element("title", "heading", "A study", { payload: { role: "title", level: 1 } }),
      element("abstract_heading", "heading", "Abstract", { payload: { role: "abstract", level: 2 } }),
      element("abstract_1", "paragraph", "We present a method.", { payload: { role: "abstract" } }),
      element("abstract_2", "paragraph", "It works well.", { payload: { role: "abstract" } }),
    ], planned(), [])} />);
    expect(screen.getAllByRole("heading", { name: "Abstract" })).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Abstract" })).toHaveAttribute("id", "abstract_heading");
    expect(reading(container)).toEqual(["Structure checked by deepseek-flash", "A study", "Abstract", "We present a method.", "It works well."]);
  });

  it("prints a figure's caption only when it has one, and never the bare word Figure", async () => {
    const onOpenPage = vi.fn();
    const { container } = render(<CanonicalReader onOpenPage={onOpenPage} document={document([
      element("plain", "figure", "Figure", { locator: "Page 3", anchor: { page: 3 }, payload: { imageAssetId: "ast_plain", caption: "" } }),
      element("captioned", "figure", "Figure 2. A result.", { locator: "Page 4", anchor: { page: 4 }, payload: { imageAssetId: "ast_result", number: "2", caption: "Figure 2. A result." } }),
      element("lost", "figure", "Figure 3. Not saved.", { locator: "Page 5", anchor: { page: 5 }, payload: { number: "3", caption: "Figure 3. Not saved." } }),
    ], planned(), [])} />);
    expect(container.querySelectorAll("figcaption")).toHaveLength(2);
    expect(screen.queryByText("Figure")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Figure on Page 3" })).toHaveAttribute("src", expect.stringContaining("ast_plain"));
    expect(screen.getByRole("img", { name: "Figure 2. A result." })).toBeVisible();
    expect(screen.getByText("Figure 2. A result.")).toBeVisible();
    expect(screen.getByText("Image unavailable · Page 5")).toBeVisible();
    expect(screen.getByText("Figure 3. Not saved.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "View on page 3" }));
    expect(onOpenPage).toHaveBeenCalledWith(3);
    expect(screen.getByRole("button", { name: "View on page 4" })).toBeVisible();
  });

  it("gathers the notes under one Notes heading, each with its marker", () => {
    const { container } = render(<CanonicalReader document={document([
      element("last", "paragraph", "The last paragraph.", { payload: { part: "body" } }),
      element("note_1", "note", "First note text.", { payload: { marker: "1", part: "notes" } }),
      element("note_2", "note", "* Second note text.", { payload: { marker: "*", part: "notes" } }),
      element("appendix", "heading", "Appendix", { payload: { level: 2, part: "back" } }),
    ], planned(), [])} />);
    expect(reading(container)).toEqual(["Structure checked by deepseek-flash", "The last paragraph.", "Notes", "1 First note text.", "* Second note text.", "Appendix"]);
    expect(screen.getAllByRole("heading", { name: "Notes" })).toHaveLength(1);
    expect(container.querySelector("#note_1 .gx-document-note-marker")).toHaveTextContent("1");
    // A marker the text already starts with is not printed twice.
    expect(container.querySelector("#note_2 .gx-document-note-marker")).toBeNull();
  });

  it("adds no Notes heading under a heading the paper already prints for them", () => {
    render(<CanonicalReader document={document([
      element("notes_heading", "heading", "Endnotes", { payload: { role: "notes", level: 2 } }),
      element("note_a", "note", "Only note.", { payload: { marker: "a" } }),
    ], planned(), [])} />);
    expect(screen.queryByRole("heading", { name: "Notes" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Endnotes" })).toBeVisible();
    expect(screen.getByText("Only note.")).toBeVisible();
  });

  it("shows a table with its caption above it, a table printed as a picture, and code as preformatted text", () => {
    const { container } = render(<CanonicalReader document={document([
      element("grid", "table", "Measure 0.9", { payload: { number: "1", caption: "Table 1. Results.", table: { table_cells: [
        { text: "Measure", start_row_offset_idx: 0, end_row_offset_idx: 1, start_col_offset_idx: 0, end_col_offset_idx: 1, column_header: true },
        { text: "0.9", start_row_offset_idx: 1, end_row_offset_idx: 2, start_col_offset_idx: 0, end_col_offset_idx: 1 },
      ] } } }),
      element("plain_grid", "table", "A B", { payload: { table: { table_cells: [{ text: "A", start_row_offset_idx: 0, start_col_offset_idx: 0 }] } } }),
      element("scan", "table", "", { locator: "Page 2", anchor: { page: 2 }, payload: { caption: "Table 2. Scanned.", imageAssetId: "ast_scan" } }),
      element("snippet", "code", "const a = 1;\nconst b = 2;"),
    ], planned(), [])} />);
    const captioned = container.querySelector("#grid")!;
    expect(captioned.tagName).toBe("FIGURE");
    expect(captioned.firstElementChild).toHaveTextContent("Table 1. Results.");
    expect(within(captioned as HTMLElement).getByRole("columnheader", { name: "Measure" })).toBeVisible();
    expect(container.querySelector("#plain_grid")?.tagName).toBe("DIV");
    expect(screen.getByRole("img", { name: "Table 2. Scanned." })).toHaveAttribute("src", expect.stringContaining("ast_scan"));
    expect(screen.getByText("Table 2. Scanned.")).toBeVisible();
    expect(container.querySelector("#snippet")?.tagName).toBe("PRE");
    expect(container.querySelector("#snippet")?.textContent).toBe("const a = 1;\nconst b = 2;");
  });

  it("lists the bibliography once, under the heading the paper prints for it", () => {
    const { container } = render(<CanonicalReader document={document([
      element("intro", "heading", "Introduction", { payload: { level: 2 } }),
      element("para", "paragraph", "Cited work [1].", { mentions: [{ id: "mention_1", start: 11, end: 14, raw: "[1]", status: "resolved", targets: ["ref_1"] }] }),
      element("literature", "heading", "Literature cited", { payload: { role: "references", level: 2, part: "back" } }),
      element("el_ref", "reference", "[1] Smith 2020.", { payload: { reference: true } }),
    ], planned())} />);
    expect(screen.getAllByRole("heading", { name: "Literature cited" })).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "References" })).not.toBeInTheDocument();
    expect(container.querySelector("section.gx-document-references h2")).toHaveAttribute("id", "literature");
    expect(screen.getAllByText("[1] Smith 2020.")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Literature cited" })).toHaveAttribute("href", "#literature");
    expect(screen.getByRole("link", { name: "Return to citation" })).toHaveAttribute("href", "#mention_1");
  });

  it("lists the headings except the title in the contents, with the Abstract label where it was added", () => {
    render(<CanonicalReader document={document([
      ...front(),
      element("abstract_1", "paragraph", "We present a method.", { payload: { role: "abstract" } }),
      element("intro", "heading", "Introduction", { payload: { level: 1 } }),
      element("method", "heading", "Method", { payload: { level: 2 } }),
      element("detail", "heading", "Details", { payload: { level: 3 } }),
    ], planned(), [])} />);
    const links = within(screen.getByRole("navigation", { name: "Document contents" })).getAllByRole("link");
    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Abstract", "#abstract_1-label"], ["Introduction", "#intro"], ["Method", "#method"], ["Details", "#detail"],
    ]);
  });

  it.each([
    ["is checked by a model, whose provider is dropped", { state: "checked", by: "deepseek:deepseek-flash" }, "Structure checked by deepseek-flash"],
    ["is checked by a model whose own name has a colon", { state: "checked", by: "ollama:llama3:8b" }, "Structure checked by llama3:8b"],
    ["is a draft", { state: "draft", by: "rules" }, "Structure not checked by a model. Add one in Settings → Models, then Read again."],
    ["is a draft that waits for a key", { state: "needs-key", by: "rules" }, "Structure not checked by a model. Add one in Settings → Models, then Read again."],
    ["is too long to be checked", { state: "too-long", by: "rules" }, "Too long for a structure check; showing the layout version."],
  ])("says so when the structure %s", (_case, structure, line) => {
    const { container } = render(<CanonicalReader onReprocess={vi.fn()} document={document([element("p", "paragraph", "Text.")], planned(structure))} />);
    expect(screen.getByText(line)).toBeVisible();
    expect(container.querySelectorAll(".gx-structure-status")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("shows a failed check with its error and a Try again button that reads the document again", async () => {
    let finish!: () => void;
    const reprocess = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    render(<CanonicalReader onReprocess={reprocess} document={document([element("p", "paragraph", "Text.")], planned({ state: "failed", by: "rules", error: "The answer was not valid JSON." }))} />);
    expect(screen.getByText("Structure check failed: The answer was not valid JSON.")).toBeVisible();
    const retry = screen.getByRole("button", { name: "Try again" });
    await userEvent.click(retry);
    expect(reprocess).toHaveBeenCalledTimes(1);
    expect(retry).toBeDisabled();
    finish();
    await waitFor(() => expect(retry).toBeEnabled());
  });

  it("shows a failure that has no error text, and no retry where nothing can read the document again", () => {
    render(<CanonicalReader document={document([element("p", "paragraph", "Text.")], planned({ state: "failed", by: "rules", error: null }))} />);
    expect(screen.getByText("Structure check failed")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("shows no status for an older document, an unknown state, or while the document is read again", () => {
    const body = [element("p", "paragraph", "Text.")];
    const failed = { state: "failed", by: "rules", error: "Something went wrong." };
    const older = render(<CanonicalReader onReprocess={vi.fn()} document={document(body, { schema: 1, structure: failed })} />);
    expect(older.container.querySelector(".gx-structure-status")).toBeNull();
    older.unmount();
    const unknown = render(<CanonicalReader onReprocess={vi.fn()} document={document(body, planned({ state: "later-state" }))} />);
    expect(unknown.container.querySelector(".gx-structure-status")).toBeNull();
    unknown.unmount();
    const unrecorded = render(<CanonicalReader onReprocess={vi.fn()} document={document(body, { schema: 2 })} />);
    expect(unrecorded.container.querySelector(".gx-structure-status")).toBeNull();
    unrecorded.unmount();
    const inProgress = document(body, planned(failed));
    inProgress.processing = { ...inProgress.processing, state: "running" };
    const again = render(<CanonicalReader onReprocess={vi.fn()} document={inProgress} />);
    expect(again.container.querySelector(".gx-structure-status")).toBeNull();
    expect(screen.getByText("Text.")).toBeVisible();
  });

  it("does not list the printed authors again under Document details, and accepts metadata of another shape", async () => {
    const { container } = render(<CanonicalReader document={document([
      element("by", "metadata", "Ada Lovelace", { payload: { role: "authors" } }),
      element("received", "metadata", "Received 3 May 2024", { payload: { role: "received" } }),
    ], planned(undefined, { metadata: { title: "A plain string", authors: [{ value: "Ada Lovelace", origin: "pdf-info", status: "observed" }] } }), [])} />);
    await userEvent.click(screen.getByText("Document details"));
    expect(container.querySelectorAll("#by")).toHaveLength(1);
    expect(container.querySelector("#by")).toHaveClass("gx-document-authors");
    expect(screen.getByText("Received 3 May 2024")).toBeVisible();
    expect(screen.getByText("Ada Lovelace", { selector: "dd" })).toBeVisible();
  });
});
