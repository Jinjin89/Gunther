import type { DocumentElement, SourceDocument } from "@gunther/contracts";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CanonicalReader } from "./CanonicalReader";

function element(id: string, kind: string, content: string, extra: Partial<DocumentElement> = {}): DocumentElement {
  return { id, kind, content, parentId: null, locator: "Page 1", headings: [], anchor: { page: 1 }, mentions: [], ...extra };
}

function document(elements: DocumentElement[], manifest: Record<string, unknown> = {}): SourceDocument {
  return {
    sourceId: "src_paper", revisionId: "srev_1", parser: "fixture", nextOffset: null,
    processing: { state: "ready", jobId: null, revisionId: "srev_1", warning: null },
    manifest, elements,
    references: [{ id: "ref_1", elementId: "el_ref", label: "1", raw: "[1] Smith 2020.", metadata: {} }],
  };
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

  it("hides all six page-furniture kinds behind one toggle while retaining their text and locations", async () => {
    const user = userEvent.setup();
    const kinds = ["page_header", "page_footer", "footnote", "page_number", "sidebar", "margin_note"];
    const data = document([
      element("body", "paragraph", "Readable body paragraph."),
      element("metadata", "metadata", "Original publication notice."),
      ...kinds.map((kind, index) => element(`furniture_${kind}`, kind, `Original ${kind} text.`, { locator: `Page ${index + 1}` })),
      element("repeated_header", "page_header", "Original page_header text.", { locator: "Page 7" }),
    ]);
    const { container } = render(<CanonicalReader document={data} />);
    const toggle = screen.getByText("Show page furniture");
    expect(toggle.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Readable body paragraph.")).toBeVisible();
    for (const kind of kinds) {
      for (const text of screen.getAllByText(`Original ${kind} text.`)) expect(text).not.toBeVisible();
    }
    await user.click(screen.getByText("Document details"));
    expect(screen.getByText("Original publication notice.")).toBeVisible();
    expect(screen.getByText("Original footnote text.")).not.toBeVisible();
    await user.click(toggle);
    for (const [index, kind] of kinds.entries()) {
      const retained = container.querySelector(`#furniture_${kind}`);
      expect(retained).toBeVisible();
      expect(retained).toHaveTextContent(`Page ${index + 1}`);
      expect(retained).toHaveTextContent(`Original ${kind} text.`);
      expect(retained?.closest("details")).toBe(toggle.closest("details"));
    }
    expect(screen.getAllByText("Original page_header text.")).toHaveLength(2);
    expect(container.querySelector("#repeated_header")).toHaveTextContent("Page 7");
    await user.click(toggle);
    for (const kind of kinds) expect(container.querySelector(`#furniture_${kind}`)).not.toBeVisible();
    expect(screen.getByText("Readable body paragraph.")).toBeVisible();
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
    expect(screen.getByText("Organization quarterly review").closest("details")).not.toBeNull();
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
