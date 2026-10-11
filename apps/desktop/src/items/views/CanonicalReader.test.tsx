import type { DocumentElement, SourceDocument } from "@gunther/contracts";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CanonicalReader } from "./CanonicalReader";

function element(id: string, kind: string, content: string, extra: Partial<DocumentElement> = {}): DocumentElement {
  return { id, kind, content, parentId: null, locator: "Page 1", headings: [], anchor: { page: 1 }, mentions: [], ...extra };
}

function document(elements: DocumentElement[]): SourceDocument {
  return {
    sourceId: "src_paper", revisionId: "srev_1", parser: "fixture", nextOffset: null,
    processing: { state: "ready", jobId: null, revisionId: "srev_1", warning: null },
    manifest: {}, elements,
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
    expect(screen.getByText("Revenue 0.1 0.2 0.3").closest("figure details")).not.toBeNull();
    expect(container.querySelectorAll("figcaption")).toHaveLength(1);
    expect(screen.getByText("The original policy paragraph.")).toBeVisible();
  });
});
