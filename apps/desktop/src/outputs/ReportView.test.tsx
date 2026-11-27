import type { Artifact, ConversationCitation, OutputType, OutputTypeId } from "@gunther/contracts";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { sourceAssetUrl } from "../api";
import { PrintRoot } from "./PrintRoot";
import { ReportView } from "./ReportView";

const source: ConversationCitation = {
  id: "note-1", sourceId: "notes", sourceTitle: "Study notes", quote: "A saved passage.",
  locator: "p. 4", assertionId: null, status: "verified", confidence: 1,
};

function artifact(content: string, fields: Partial<Artifact> = {}): Artifact {
  return {
    id: "report-1", title: "Saved document", kind: "report", origin: "build", content,
    sections: [], citations: [source], versionNumber: 1, createdAt: "2025-01-01T00:00:00Z", ...fields,
  } as Artifact;
}

function type(id: OutputTypeId): OutputType {
  return { id, shape: "report", label: id } as OutputType;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it.each(["build", "legacy"] as const)("renders an unheaded %s preamble once in the reader and on paper", (origin) => {
  const saved = artifact("# Saved document\n\nPreamble [1].\n\nThe rest of the text.", { origin });
  const { container } = render(<><ReportView artifact={saved} /><PrintRoot artifact={saved} /></>);
  for (const scope of [container, document.getElementById("print-root")!]) {
    expect(within(scope).getAllByText(/Preamble/)).toHaveLength(1);
    expect(within(scope).getAllByText("The rest of the text.")).toHaveLength(1);
    expect(within(scope).queryByText("Contents")).toBeNull();
  }
  expect(document.querySelectorAll("#print-root h1")).toHaveLength(1);
  expect(document.querySelectorAll("#print-root .outputs-print-sources")).toHaveLength(1);
});

it("keeps mixed-language prose and version metadata independently of the recipe", () => {
  const saved = artifact("# Root\n\n## First\n\n同じ texte.", {
    style: "faq", provenance: { language: "fr-CA" } as Artifact["provenance"],
  });
  const { container, rerender } = render(<ReportView artifact={saved} />);
  expect(container.querySelector(".rp")).toHaveAttribute("lang", "fr-CA");
  expect(screen.getByText("同じ texte.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "First" })).toBeNull();
  rerender(<ReportView artifact={artifact("<!-- report: v2 -->\n# Empty")} />);
  expect(screen.getByText("This version has no report text.")).toBeVisible();
});

it("expands FAQ answers by original position and keeps heading citations independent", () => {
  const saved = artifact("## Same question\n\nFirst answer.\n\n## Same question [1]\n\nLater answer.");
  const onCite = vi.fn();
  const onPart = vi.fn();
  const onAsk = vi.fn();
  const faq = type("faq");
  const { rerender } = render(<ReportView artifact={saved} type={faq} onCite={onCite} onPart={onPart} onAsk={onAsk} />);
  const [first, second] = screen.getAllByRole("button", { name: /^Same question/ });
  expect(first).toHaveAttribute("aria-expanded", "true");
  expect(second).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText("Later answer.")).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Inspect citation 1" }));
  expect(onCite).toHaveBeenCalledWith(source, 0);
  expect(second).toHaveAttribute("aria-expanded", "false");

  rerender(<ReportView artifact={saved} type={faq} onCite={onCite} onPart={onPart} onAsk={onAsk} goTo={{ index: 1, nonce: 1 }} />);
  expect(second).toHaveFocus();
  expect(screen.getByText("Later answer.")).toBeVisible();
  expect(onPart).toHaveBeenLastCalledWith(1);
  fireEvent.click(within(second!.closest("section")!).getByRole("button", { name: "Refine section" }));
  expect(onAsk).toHaveBeenCalledWith(1);
  fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  expect(first).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
  expect(second).toHaveAttribute("aria-expanded", "true");
});

it("uses the same full FAQ content in preview and paper with static paper references", () => {
  const saved = artifact("## First question\n\nFirst answer.\n\n## Later question\n\nLater answer [1].");
  const faq = type("faq");
  const { container } = render(<><ReportView artifact={saved} type={faq} presentation="preview" />
    <PrintRoot artifact={saved} type={faq} /></>);
  expect(within(container).getByText(/Later answer/)).toBeVisible();
  const paper = document.getElementById("print-root")!;
  expect(paper).toHaveTextContent("Later answer [1].");
  expect(paper.querySelector("button, details")).toBeNull();
});

it("jumps from the study topic list and the next and previous controls", () => {
  const saved = artifact("## Concept\n\nThe introduction.\n\n## Exercise\n\nThe exercise [1].\n\n## Review\n\nThe conclusion.");
  const onPart = vi.fn();
  render(<ReportView artifact={saved} type={type("study_guide")} onPart={onPart} />);
  expect(screen.getByRole("button", { name: "Previous topic" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Next topic" }));
  const exercise = screen.getByRole("region", { name: "Exercise" });
  expect(exercise).toHaveFocus();
  expect(exercise).toHaveTextContent("Topic 2 of 3");
  fireEvent.click(screen.getByText("Topics", { selector: "summary" }));
  fireEvent.click(screen.getByRole("link", { name: "Review" }));
  expect(screen.getByRole("region", { name: "Review" })).toHaveFocus();
  expect(onPart).toHaveBeenLastCalledWith(2);
  expect(screen.getByRole("button", { name: "Next topic" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Previous topic" }));
  expect(exercise).toHaveFocus();
});

it("keeps timeline events ordered by saved position, with duplicate or undated headings", () => {
  const saved = artifact("## Final review\n\nFirst saved event.\n\n## 1990–1992\n\nSecond saved event.\n\n## Final review\n\nLast saved event.");
  render(<ReportView artifact={saved} type={type("timeline")} goTo={{ index: 2, nonce: 1 }} />);
  const rows = screen.getByText("First saved event.").closest("ol")!;
  expect([...rows.children].map((row) => row.querySelector("h2")?.textContent)).toEqual(["Final review", "1990–1992", "Final review"]);
  expect(screen.getAllByRole("region", { name: "Final review" })[1]).toHaveFocus();
  expect(screen.getByText("Event 3 of 3")).toBeVisible();
});

it("lays out top-level Markdown tables and exhibits on the report track and keeps semantic blocks intact", () => {
  const table = (heading: string) => `| ${heading} | Result |\n| --- | --- |\n| Item | A |`;
  const saved = artifact([
    "Preamble text.", table("Preamble measure"),
    "## Summary\n\n<!-- block: summary -->\n\nSummary text.", table("Summary measure"),
    "## Measurements\n\nDetails.", table("Direct measure"),
    table("Quoted measure").split("\n").map((line) => `> ${line}`).join("\n"),
    `- Procedure\n\n${table("Listed measure").split("\n").map((line) => `  ${line}`).join("\n")}`,
    `:::callout Method\n${table("Callout measure")}\n:::`,
    `:::exhibit\nObserved values\n${table("Exhibit measure")}\n:::`,
  ].join("\n\n"));
  const timeline = type("timeline");
  const { container } = render(<><ReportView artifact={saved} type={timeline} /><PrintRoot artifact={saved} type={timeline} /></>);
  const report = container.querySelector<HTMLDivElement>(".rp")!;
  expect(report).toHaveClass("reader-track");
  expect(report).toHaveAttribute("data-track", "report");
  const reader = within(report);
  const tableFor = (name: string) => reader.getByRole("columnheader", { name }).closest(".rp-table")!;

  for (const [heading, block] of [
    ["Preamble measure", "rp-preamble"], ["Summary measure", "rp-summary"], ["Direct measure", "rp-prose"],
  ] as const) {
    const markdown = tableFor(heading).parentElement;
    expect(markdown).toHaveClass("answer-markdown");
    expect(markdown?.parentElement).toHaveClass(block);
    expect(markdown?.querySelector(":scope > p")).toBeInTheDocument();
  }
  expect(tableFor("Quoted measure").parentElement?.tagName).toBe("BLOCKQUOTE");
  expect(tableFor("Listed measure").parentElement?.tagName).toBe("LI");
  expect(tableFor("Callout measure").closest("aside")).toHaveClass("rp-callout");
  expect(tableFor("Exhibit measure").closest(".rp-exhibit")?.parentElement).toHaveClass("rp-prose");
  expect(report.querySelector(".rp-events .rp-exhibit")).toBeInTheDocument();

  const paper = document.getElementById("print-root")!;
  expect(paper.parentElement).toBe(document.body);
  expect(paper.querySelector(".rp")).toHaveAttribute("data-presentation", "print");
  expect(paper.querySelector(".reader-track, [data-track]")).toBeNull();
  expect(paper.querySelectorAll("table")).toHaveLength(7);
});

it("updates the current part after a pane resize at the same scroll position as an end jump", () => {
  let resized: ResizeObserverCallback;
  const observe = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: ResizeObserverCallback) { resized = callback; }
    observe = observe;
    disconnect = vi.fn();
  });
  const saved = artifact("## First\n\nStart.\n\n## Second\n\nMiddle.\n\n## Last\n\nEnd.");
  const guide = type("study_guide");
  const onPart = vi.fn();
  const view = (goTo?: { index: number; nonce: number }) => <main className="library-middle-page">
    <ReportView artifact={saved} type={guide} onPart={onPart} goTo={goTo} />
  </main>;
  const { container, rerender } = render(view());
  const pane = container.querySelector<HTMLElement>(".library-middle-page")!;
  pane.scrollTop = 200;
  const width = vi.spyOn(pane, "clientWidth", "get").mockReturnValue(520);
  const [first, second, last] = [...pane.querySelectorAll("section")];
  vi.spyOn(first!, "getBoundingClientRect").mockReturnValue(new DOMRect(0, -190, 520, 130));
  const secondBox = vi.spyOn(second!, "getBoundingClientRect").mockReturnValue(new DOMRect(0, -60, 520, 30));
  vi.spyOn(last!, "getBoundingClientRect").mockReturnValue(new DOMRect(0, -30, 520, 250));
  rerender(view({ index: 2, nonce: 1 }));
  expect(last).toHaveFocus();
  expect(onPart).toHaveBeenLastCalledWith(2);
  expect(observe).toHaveBeenCalledWith(pane);
  act(() => resized([], {} as ResizeObserver));
  expect(onPart).toHaveBeenLastCalledWith(2);
  expect(screen.getByRole("button", { name: "Next topic" })).toBeDisabled();

  width.mockReturnValue(320);
  secondBox.mockReturnValue(new DOMRect(0, -60, 320, 170));
  act(() => resized([], {} as ResizeObserver));
  expect(pane.scrollTop).toBe(200);
  expect(onPart).toHaveBeenLastCalledWith(1);
  expect(screen.getByRole("button", { name: "Next topic" })).toBeEnabled();
});

it("renders retained figures, math and literal code with the normal Markdown rules", async () => {
  const code = ":::exhibit chart=bar\n  ## An example [1]\n:::";
  const saved = artifact(`## Methods\n\n![Retained figure](gunther-asset:figure_1)\n\n![Remote figure](https://example.com/figure.png)\n\n~~~md\n${code}\n~~~\n\n$$\nx^2\n$$`);
  const { container } = render(<ReportView artifact={saved} />);
  expect(screen.getByRole("img", { name: "Retained figure" })).toHaveAttribute("src", sourceAssetUrl("figure_1"));
  expect(screen.getByRole("link", { name: "Remote figure" })).toHaveAttribute("href", "https://example.com/figure.png");
  expect(container.querySelector("pre code")?.textContent).toBe(code);
  expect(container.querySelector(".rp-exhibit")).toBeNull();
  await waitFor(() => expect(container.querySelector(".katex")).toBeInTheDocument());
});

it("renders chart values from original cells and retains unreadable exhibit text only once", () => {
  const saved = artifact([
    "## Results", "", ":::exhibit chart=bar", "Observed values", "| Sample | Mean (%) |", "| --- | ---: |",
    "| Repeated | **3** [1] |", "| Repeated | 4 [1] |", "Source: Notes [1]", ":::", "",
    ":::exhibit", "Original caption", "Still readable prose.", "Source: Saved note [1]", ":::",
  ].join("\n"));
  const onCite = vi.fn();
  const { container } = render(<><ReportView artifact={saved} onCite={onCite} /><PrintRoot artifact={saved} /></>);
  const table = container.querySelector("table")!;
  const values = [...table.querySelectorAll("td")];
  expect(values.map((cell) => cell.textContent)).toEqual(["3 [1]", "4 [1]"]);
  fireEvent.click(within(values[1]!).getByRole("button", { name: "Inspect citation 1", hidden: true }));
  expect(onCite).toHaveBeenLastCalledWith(source, 0);
  const paper = document.getElementById("print-root")!;
  expect(paper.querySelector("td sup")).toHaveTextContent("[1]");
  expect(paper.querySelector("td button, .rp-data")).toBeNull();
  expect(within(container).getByText("Exhibit data could not be read.")).toBeVisible();
  expect(within(container).getAllByText(/Still readable prose/)).toHaveLength(1);
});

it("keeps a dense exhibit scrollable in the reader and pairs headers with values on paper", () => {
  const heads = Array.from({ length: 10 }, (_, i) => `Measure ${i + 1}`);
  const row = (cells: string[]) => `| ${cells.join(" | ")} |`;
  const saved = artifact([
    "## Experiment", "", ":::exhibit", "Measurements", row(heads), row(heads.map(() => "---")),
    row(heads.map((_, i) => `${i + 1} [1]`)), ":::",
  ].join("\n"));
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(function (this: HTMLElement) {
    return this.matches(".rp-table") ? 840 : 320;
  });
  const { container } = render(<><ReportView artifact={saved} /><PrintRoot artifact={saved} /></>);
  const readerGrid = container.querySelector(".rp-table")!;
  expect(readerGrid).toHaveAttribute("aria-label", "Measurements");
  expect(readerGrid).toHaveAttribute("tabindex", "0");
  expect(readerGrid.querySelectorAll("thead th")).toHaveLength(10);
  const paperGrid = document.querySelector("#print-root .rp-table")!;
  expect(paperGrid.querySelectorAll("dl > div")).toHaveLength(10);
  expect(paperGrid.querySelector("dl > div:last-child")).toHaveTextContent("Measure 1010 [1]");
});

it("shares the accessible overflow and paper rows for a 40-column ordinary Markdown table", () => {
  const heads = Array.from({ length: 40 }, (_, index) => `H${index + 1}`);
  const row = (cells: string[]) => `| ${cells.join(" | ")} |`;
  const saved = artifact([
    "## Readings", "", row(heads), row(heads.map(() => "---")), row(heads.map((_, index) => index === 39 ? "*x* [1]" : "x")),
  ].join("\n"));
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
    return this.closest("#print-root") ? 673 : 320;
  });
  vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(function (this: HTMLElement) {
    return this.matches(".rp-table") ? 3360 : this.clientWidth;
  });
  const { container } = render(<><ReportView artifact={saved} presentation="read" />
    <ReportView artifact={saved} presentation="preview" /><PrintRoot artifact={saved} /></>);
  const regions = within(container).getAllByRole("region", { name: "Table" });
  expect(regions).toHaveLength(2);
  for (const region of regions) {
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.querySelectorAll("thead th")).toHaveLength(40);
    expect(region.querySelector("td:last-child em")).toHaveTextContent("x");
  }
  const paper = document.getElementById("print-root")!;
  expect(paper.querySelector("table")).toBeNull();
  expect(paper.querySelectorAll("dl > div")).toHaveLength(40);
  expect(paper.querySelector("dl > div:last-child dt")).toHaveTextContent("H40");
  expect(paper.querySelector("dl > div:last-child dd")).toHaveTextContent("x [1]");
});

it("measures a wide inline fraction in a two-column table after the formula renders", async () => {
  const numerator = Array.from({ length: 14 }, (_, index) => `a_{${index + 1}}`).join("+");
  const saved = artifact(`## Calculations\n\n| Measure | Formula |\n| --- | --- |\n| Mean | $$\\frac{${numerator}}{n}$$ |`);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
    const paper = this.closest("#print-root");
    if (this.matches(".rp-table")) return paper ? 673 : 320;
    return paper ? 330 : 150;
  });
  vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(function (this: HTMLElement) {
    return this.matches("td") && this.querySelector('[data-equation-state="ready"]') ? 590 : this.clientWidth;
  });
  const { container } = render(<><ReportView artifact={saved} /><PrintRoot artifact={saved} /></>);
  const grid = container.querySelector(".rp-table")!;
  expect(grid).not.toHaveAttribute("tabindex");
  await waitFor(() => expect(grid).toHaveAttribute("tabindex", "0"));
  expect(grid).toHaveAttribute("role", "region");
  expect(grid).toHaveAttribute("aria-label", "Table");
  expect(grid.querySelectorAll("td")).toHaveLength(2);

  const paper = document.getElementById("print-root")!;
  await waitFor(() => expect(paper.querySelector(".rp-paper-rows dd .katex")).toBeInTheDocument());
  expect(paper.querySelector("table")).toBeNull();
  expect(paper.querySelector("dl > div:last-child dt")).toHaveTextContent("Formula");
});

it.each(["line", "column"] as const)("exposes chart data when %s labels omit saved values", (kind) => {
  const rows = Array.from({ length: 9 }, (_, index) => `| Point ${index + 1} | ${index + 12} |`);
  const saved = artifact([
    "## Readings", "", `:::exhibit chart=${kind}`, "Observed values", "| Sample | Mean |", "| --- | --- |", ...rows, ":::",
  ].join("\n"));
  vi.spyOn(SVGElement.prototype, "getBoundingClientRect").mockImplementation(function (this: SVGElement) {
    if (this.localName === "svg") return new DOMRect(0, 0, 1120, 380);
    return new DOMRect(Number(this.dataset.category) * 50, this.dataset.series === undefined ? 0 : 30, 24, 12);
  });
  const { container } = render(<ReportView artifact={saved} />);
  const plot = container.querySelector(".rp-chart");
  expect(plot?.querySelectorAll("text[data-series]")).toHaveLength(kind === "line" ? 2 : 9);
  if (kind === "line") {
    expect(plot).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector(".rp-data")).toBeNull();
    expect(screen.getAllByRole("row")).toHaveLength(10);
  } else {
    expect(plot).not.toHaveAttribute("aria-hidden");
    expect(container.querySelector(".rp-data")).not.toHaveAttribute("open");
  }
});
