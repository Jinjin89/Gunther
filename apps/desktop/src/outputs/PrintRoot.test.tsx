import type { Artifact, OutlineItem } from "@gunther/contracts";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PrintRoot } from "./PrintRoot";

const deck = (content: string, outline: OutlineItem[] = [], citations: unknown[] = []) => ({
  id: "art_1", kind: "slides", origin: "build", title: "Deck", versionNumber: 1, createdAt: "2026-10-15T09:00:00Z",
  content, outline, citations, sections: [],
}) as unknown as Artifact;

const faces = () => Array.from(document.querySelectorAll("#print-root .outputs-slide-face")).map((face) => face.getAttribute("data-layout"));
const drawn = () => Array.from(document.querySelectorAll("#print-root .outputs-slide-face")).map((face) => face.getAttribute("data-drawn"));

afterEach(cleanup);

// The slides are drawn by a part of the app that is fetched when a deck is open, so they appear a moment after the root does.
describe("printing a deck", () => {
  it("prints each slide without its marker, drawn as the layout it has", async () => {
    const content = [
      "<!-- layout: title; accent: blue -->\n# Deck\n\nOne line",
      "<!-- layout: flow -->\n## One\n\n1. a\n2. b\n\nNote: Say a.",
    ].join("\n\n---\n\n");
    render(<PrintRoot artifact={deck(content)} />);
    const paper = document.getElementById("print-root")!;
    await waitFor(() => expect(faces()).toEqual(["title", "flow"]));
    expect(drawn()).toEqual(["title", "flow"]);
    expect(paper.textContent).not.toContain("layout:");
    expect(paper.textContent).not.toContain("Say a.");
    expect(paper.querySelectorAll(".outputs-print-slide")).toHaveLength(2);
  });

  it("puts one slide on each page, in the colour the title slide chose, followed by a page of sources", async () => {
    const content = ["<!-- layout: title; accent: teal -->\n# Deck", "<!-- layout: bar_chart -->\n## Bars\n| a | b (%) |\n| --- | --- |\n| x | 60 [1] |\n| y | 40 [1] |"].join("\n\n---\n\n");
    const citations = [{ id: "cit_1", sourceTitle: "Marker review", locator: "p. 2", quote: "A quote." }];
    render(<PrintRoot artifact={deck(content, [], citations)} />);
    const paper = document.getElementById("print-root")!;
    await waitFor(() => expect(drawn()).toEqual(["title", "bar_chart"]));
    // A page is a box that is 1280 × 720 and is not broken inside (outputs.css); here, that there is one for each slide and for the sources.
    const pages = Array.from(paper.querySelectorAll(".outputs-print-slide"));
    expect(pages).toHaveLength(3);
    expect(pages[2]).toHaveClass("outputs-print-slide-sources");
    // Every face has the deck's accent, so the whole deck is printed in it.
    for (const face of paper.querySelectorAll<HTMLElement>(".outputs-slide-face")) expect(face.style.getPropertyValue("--outputs-accent")).toBe("#028066");
  });

  it("prints a themed deck with the selected paper, text and accent tokens", async () => {
    render(<PrintRoot artifact={deck("<!-- layout: title; accent: red; theme: keynote -->\n# Deck\n\n---\n\n<!-- layout: flow -->\n## Steps\n\n1. Start\n2. Finish")} />);
    await waitFor(() => expect(faces()).toEqual(["title", "flow"]));
    const paper = document.getElementById("print-root")!;
    for (const face of paper.querySelectorAll<HTMLElement>(".outputs-slide-face")) {
      expect(face.dataset.theme).toBe("keynote");
      expect(face.style.getPropertyValue("--outputs-slide-paper")).toBe("#171a20");
      expect(face.style.getPropertyValue("--outputs-accent")).toBe("#ff9e96");
    }
  });

  it("takes the outline's layouts for a deck made before markers, when they line up", async () => {
    const outline = [{ heading: "Deck", goal: "", layout: "title" }, { heading: "One", goal: "", layout: "flow" }];
    render(<PrintRoot artifact={deck("# Deck\n\n---\n\n## One\n\n1. a\n2. b", outline)} />);
    await waitFor(() => expect(faces()).toEqual(["title", "flow"]));
    expect(drawn()).toEqual(["title", "flow"]);
    cleanup();
    render(<PrintRoot artifact={deck("# Deck\n\n---\n\n## One\n\n1. a", outline.slice(0, 1))} />);
    await waitFor(() => expect(faces()).toEqual([null, null]));
    // No layout, no drawing: the text as it was before layouts.
    expect(drawn()).toEqual([null, null]);
  });
});

it("prints a late capture batch with the whole deck's theme, page numbers and citations", async () => {
  const content = ["<!-- layout: title; design: v2; theme: ink -->\n# Batch fixture\nA longer presentation",
    ...Array.from({ length: 10 }, (_, n) => `<!-- layout: points -->\n## Controlled experiment ${n + 1}\n- Observation [2]\n- Interpretation [1]\nNote: Private note`)].join("\n\n---\n\n");
  const artifact = deck(content, [], [
    { id: "one", sourceTitle: "First study", ref: 12, quote: "First source" },
    { id: "two", sourceTitle: "Second study", ref: 29, quote: "Second source" },
  ]);
  render(<PrintRoot artifact={artifact} />);
  await waitFor(() => expect(document.querySelectorAll("#print-root .dk-face")).toHaveLength(11));
  const whole = [...document.querySelectorAll("#print-root .dk-face")].map(el => el.outerHTML);
  cleanup();
  render(<PrintRoot artifact={artifact} from={8} to={11} />);
  await waitFor(() => expect(document.querySelectorAll("#print-root .dk-face")).toHaveLength(3));
  const batch = [...document.querySelectorAll("#print-root .dk-face")].map(el => el.outerHTML);
  expect(batch).toEqual(whole.slice(8, 11));
  expect(document.querySelectorAll(".outputs-print-slide-sources")).toHaveLength(1);
  expect(document.getElementById("print-root")!.textContent).not.toContain("Private note");
  cleanup();
  render(<PrintRoot artifact={artifact} from={0} to={8} />);
  await waitFor(() => expect(document.querySelectorAll("#print-root .dk-face")).toHaveLength(8));
  expect(document.querySelectorAll(".outputs-print-slide-sources")).toHaveLength(0);
});
