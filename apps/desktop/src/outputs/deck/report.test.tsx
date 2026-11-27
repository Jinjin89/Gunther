import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { docxBytes } from "./exportDocx";
import { fixture } from "./fixtures";
import { isReportV2 } from "./detect";
import { parseReport, readBlocks } from "./parseReport";
import { ReportView } from "../ReportView";
import type { Artifact } from "@gunther/contracts";

const artifact = (content: string) => ({
  id: "r", kind: "report", title: "T", content, origin: "agent", sections: [], outline: [], citations: [],
}) as unknown as Artifact;

describe("a studio report", () => {
  it("is found by its marker line", () => {
    expect(isReportV2(fixture("report"))).toBe(true);
    expect(isReportV2("# Old report\n\n## A section")).toBe(false);
  });

  it("is read as a summary, sections, exhibits and callouts", () => {
    const doc = parseReport(fixture("report"));
    expect(doc.title).toBe("Cool roofs cut cooling demand, but by half as much as the headline says");
    expect(doc.sections[0]).toMatchObject({ summary: true });
    expect(doc.sections.slice(1).every((section) => !section.summary)).toBe(true);
    const blocks = doc.sections.flatMap((section) => section.blocks);
    expect(blocks.filter((block) => block.t === "exhibit").length).toBe(4);
    expect(blocks.filter((block) => block.t === "callout")).toEqual([{ t: "callout", title: "Why the adjusted figure matters", text: expect.stringContaining("overestimate") }]);
  });

  it("reads an exhibit's caption, table and source line", () => {
    const [block] = readBlocks(":::exhibit chart=column\nExhibit 2 — Savings\n| Summer | Raw (%) |\n| --- | --- |\n| 2021 | 21 [1] |\n| 2022 | 17 [1] |\nSource: Harlow 2024 [1]\n:::");
    expect(block).toMatchObject({ t: "exhibit", caption: "Exhibit 2 — Savings", source: "Harlow 2024 [1]" });
    expect(block?.t === "exhibit" && block.table?.layout).toBe("chart");
    expect(block?.t === "exhibit" && block.grid?.rows[0]?.cells).toEqual(["2021", "21 [1]"]);
  });

  it.each(["来源：", "参照資料：", "Archival records: "])("renders trailing exhibit attribution with the label %s", (label) => {
    const exhibit = [
      ":::exhibit", "Comparison by season", "| Season | Value |", "| --- | --- |",
      "| Spring | 7 |", "", `  ${label}Field archive: notebook 2`, "", ":::",
    ].join("\n");
    expect(readBlocks(exhibit)[0]).toMatchObject({
      t: "exhibit", caption: "Comparison by season", source: "Field archive: notebook 2", grid: { layout: "table" },
    });

    const content = `<!-- report: v2 -->\n# T\n\n## Observations\n\n${exhibit}`;
    const view = render(<ReportView artifact={artifact(content)} onCite={() => undefined} />);
    expect(view.getByRole("table")).toBeVisible();
    expect(view.getByText("Comparison by season")).toBeVisible();
    expect(view.container.querySelector(".rp-source")).toHaveTextContent("Field archive: notebook 2");
    expect(view.queryByText("Exhibit data could not be read.")).not.toBeInTheDocument();
  });

  it("keeps colons in the caption and table cells", () => {
    const exhibit = [
      ":::exhibit", "Results: afternoon", "| Sample | Time |", "| --- | --- |", "| A | 12:30 |", ":::",
    ].join("\n");
    expect(readBlocks(exhibit)[0]).toMatchObject({
      t: "exhibit", caption: "Results: afternoon", source: null, grid: { rows: [{ cells: ["A", "12:30"] }] },
    });
  });

  it("renders structured and unmarked reports through the same rich Markdown body", () => {
    const v2 = render(<ReportView artifact={artifact(fixture("report"))} onCite={() => undefined} />);
    expect(v2.container.querySelectorAll(".rp-summary").length).toBe(1);
    expect(v2.container.querySelectorAll(".rp-exhibit").length).toBe(4);
    expect(v2.container.querySelectorAll(".rp-callout").length).toBe(1);
    v2.unmount();
    const old = render(<ReportView artifact={artifact("# T\n\n## A section\n\nSome text.")} onCite={() => undefined} />);
    expect(old.container.querySelector(".rp-prose .answer-markdown")).toHaveTextContent("Some text.");
    expect(old.container.querySelector(".rp-heading h2")).toHaveTextContent("A section");
  });

  it("keeps Studio delimiters inside code and after a nested fence closes", () => {
    const code = ["~~~md", ":::exhibit chart=column", "  | Sample | Value |", ":::", "~~~"].join("\n");
    const inside = "Before\n\n" + code + "\n\nAfter";
    expect(readBlocks(`:::callout Example\n${inside}\n:::\n\nTail`)).toEqual([
      { t: "callout", title: "Example", text: inside },
      { t: "md", text: "\nTail" },
    ]);
    expect(readBlocks(code)).toEqual([{ t: "md", text: code }]);
  });

  it("is written to Word with its exhibits as tables, citations as superscripts and a Sources list", async () => {
    const zip = await JSZip.loadAsync(await docxBytes(fixture("report"), { sources: ["Harlow 2024 · p. 3", "Okafor 2025"] }));
    const xml = await zip.file("word/document.xml")!.async("string");
    expect((xml.match(/<w:tbl>/g) ?? []).length).toBeGreaterThanOrEqual(6);
    expect(xml).toContain("Exhibit 1");
    expect(xml).toContain('<w:vertAlign w:val="superscript"/>');
    expect(xml).toContain("Sources");
    expect(xml).toContain("Harlow 2024 · p. 3");
  });

  it("writes every paragraph of an unheaded preamble to Word once and in order", async () => {
    const saved = "<!-- report: v2 -->\n# Field report\n\nThe first paragraph\nwraps onto the next line.\n\nA second paragraph.\n\nThe closing paragraph.";
    const zip = await JSZip.loadAsync(await docxBytes(saved));
    const xml = await zip.file("word/document.xml")!.async("string");
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    expect(Array.from(doc.getElementsByTagName("w:p"), (paragraph) => paragraph.textContent)).toEqual([
      "Field report", "The first paragraph wraps onto the next line.", "A second paragraph.", "The closing paragraph.",
    ]);
  });

  it("writes an unreadable unheaded exhibit's caption, body and source once in Word", async () => {
    const saved = ["<!-- report: v2 -->", "# Field report", "", ":::exhibit", "Exhibit caption", "The exhibit body remains readable.", "Source: Archive notes", ":::"].join("\n");
    const zip = await JSZip.loadAsync(await docxBytes(saved));
    const xml = await zip.file("word/document.xml")!.async("string");
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    expect(Array.from(doc.getElementsByTagName("w:p"), (paragraph) => paragraph.textContent)).toEqual([
      "Field report", "Exhibit caption", "The exhibit body remains readable.", "Source: Archive notes",
    ]);
  });
});
