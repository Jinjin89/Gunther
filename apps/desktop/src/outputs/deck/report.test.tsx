import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { docxBytes } from "./exportDocx";
import { fixture } from "./fixtures";
import { isReportV2 } from "./detect";
import { parseReport, readBlocks } from "./parseReport";
import { ReportView } from "../ReportView";
import type { Artifact } from "@gunther/contracts";

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
  });

  it("is drawn by ReportView with a summary box, exhibits and a callout; an old report is not", () => {
    const artifact = (content: string) => ({ id: "r", kind: "report", title: "T", content, origin: "agent", sections: [], outline: [], citations: [] }) as unknown as Artifact;
    const v2 = render(<ReportView artifact={artifact(fixture("report"))} onCite={() => undefined} />);
    expect(v2.container.querySelectorAll(".rp-summary").length).toBe(1);
    expect(v2.container.querySelectorAll(".rp-exhibit").length).toBe(4);
    expect(v2.container.querySelectorAll(".rp-callout").length).toBe(1);
    v2.unmount();
    const old = render(<ReportView artifact={artifact("# T\n\n## A section\n\nSome text.")} onCite={() => undefined} />);
    expect(old.container.querySelector(".rp")).toBeNull();
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
});
