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
