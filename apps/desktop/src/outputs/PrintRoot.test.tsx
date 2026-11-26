import type { Artifact, OutlineItem } from "@gunther/contracts";
import { act, cleanup, render, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrintRoot } from "./PrintRoot";
import documentSections from "./fixtures/document-sections.json";
import { prepareForPrint } from "./printReady";
import { withDesignBlock } from "./sections";

const loadEquationRenderer = vi.hoisted(() => vi.fn());
vi.mock("katex", () => loadEquationRenderer());

const printRenderer = vi.hoisted(() => ({ pending: undefined as Promise<void> | undefined }));
vi.mock("./PrintSlides", async (load) => {
  const { PrintSlides } = await load<typeof import("./PrintSlides")>();
  return { PrintSlides: (props: ComponentProps<typeof PrintSlides>) => {
    if (printRenderer.pending) throw printRenderer.pending;
    return <PrintSlides {...props} />;
  } };
});

const deck = (content: string, outline: OutlineItem[] = [], citations: unknown[] = []) => ({
  id: "art_1", kind: "slides", origin: "build", title: "Deck", versionNumber: 1, createdAt: "2026-10-15T09:00:00Z",
  content, outline, citations, sections: [],
}) as unknown as Artifact;

const faces = () => Array.from(document.querySelectorAll("#print-root .outputs-slide-face")).map((face) => face.getAttribute("data-layout"));
const drawn = () => Array.from(document.querySelectorAll("#print-root .outputs-slide-face")).map((face) => face.getAttribute("data-drawn"));

afterEach(cleanup);

it("prints every line of long report code without a toolbar or fold control", () => {
  const code = Array.from({ length: 55 }, (_, index) => `line ${index + 1}`).join("\n");
  const artifact: Artifact = { ...deck(`# Report\n\n## Methods\n\n~~~\n${code}\n~~~`), kind: "report" };
  render(<PrintRoot artifact={artifact} />);
  const block = document.querySelector("#print-root .gx-md-code");
  expect(block?.querySelector("pre code")?.textContent).toBe(code);
  expect(block?.querySelector("figcaption, button")).toBeNull();
});

it("prints an unheaded report once on A4 with one numbered sources list", () => {
  const quote = "The field notes preserve each recorded observation. ".repeat(6) + "The final observation is also retained.";
  const artifact: Artifact = { ...deck("# Deck\n\nThe field record is bounded [1].", [], [
    { sourceTitle: "Field record", quote },
  ]), kind: "report" };
  render(<PrintRoot artifact={artifact} />);
  const paper = document.getElementById("print-root")!;
  expect(paper.querySelector("style")).toHaveTextContent("@page { size: A4; margin: 18mm 16mm; }");
  expect(paper.querySelectorAll("h1")).toHaveLength(1);
  expect(within(paper).getAllByText(/The field record is bounded/)).toHaveLength(1);
  expect(paper.querySelectorAll(".outputs-print-sources ol")).toHaveLength(1);
  expect(paper.querySelectorAll(".outputs-print-sources li")).toHaveLength(1);
  expect(paper.querySelector(".outputs-print-sources q")?.textContent).toBe(quote);
  expect(paper.querySelector("sup")).toHaveTextContent("[1]");
  expect(paper.querySelector("button")).toBeNull();
});

it("prepares ordinary inline code without loading the equation renderer", async () => {
  const artifact: Artifact = { ...deck("# Deck\n\nRead the `total` field."), kind: "report" };
  render(<PrintRoot artifact={artifact} />);
  (await prepareForPrint(artifact.id))();
  expect(document.querySelector("#print-root code")?.textContent).toBe("total");
  expect(loadEquationRenderer).not.toHaveBeenCalled();
});

it("reports a failed equation load through print preparation", async () => {
  loadEquationRenderer.mockRejectedValueOnce(new Error("The module could not load."));
  const artifact: Artifact = { ...deck("# Deck\n\nA formula $$x^2$$."), kind: "report" };
  render(<PrintRoot artifact={artifact} />);
  await expect(prepareForPrint(artifact.id)).rejects.toThrow("Could not render an equation for printing.");
  expect(document.querySelector('#print-root [data-equation-state="failed"]')?.textContent).toBe("x^2");
  expect(document.documentElement).not.toHaveAttribute("data-print-prep");
});

it("waits for a saved print figure and releases the layout when an image fails", async () => {
  const artifact: Artifact = { ...deck("# Deck\n\n## Measurements\n\n![Field diagram](gunther-asset:diagram)"), kind: "report" };
  render(<PrintRoot artifact={artifact} />);
  const image = document.querySelector<HTMLImageElement>("#print-root img")!;
  let loaded!: () => void;
  const decode = vi.fn(() => new Promise<void>((resolve) => { loaded = resolve; }));
  Object.defineProperty(image, "decode", { value: decode });
  const preparing = prepareForPrint(artifact.id);
  await waitFor(() => expect(decode).toHaveBeenCalledOnce());
  expect(image.loading).toBe("eager");
  expect(document.documentElement).toHaveAttribute("data-print-prep");
  loaded();
  (await preparing)();
  expect(document.documentElement).not.toHaveAttribute("data-print-prep");

  decode.mockRejectedValueOnce(new Error("Missing asset"));
  await expect(prepareForPrint(artifact.id)).rejects.toThrow("Could not load the print image “Field diagram”.");
  expect(document.documentElement).not.toHaveAttribute("data-print-prep");
});

it("prints the audio script and static citations with no playback controls", () => {
  const fixture = documentSections.find((item) => item.shape === "audio")!;
  const artifact: Artifact = { ...deck(fixture.content, [], [{ sourceTitle: "Trial log", quote: "Supporting passage." }]), kind: "audio" };
  render(<PrintRoot artifact={artifact} />);
  const paper = document.getElementById("print-root")!;
  const script = paper.querySelector(".audio-script")!;
  expect(script.querySelectorAll("li")).toHaveLength(3);
  expect(script.querySelector("sup")).toHaveTextContent("[1]");
  expect(paper.querySelector(".outputs-print-sources")).toHaveTextContent("Trial log");
  expect(paper.querySelector("audio, button")).toBeNull();
});

it("prints the full mind map as a nested outline with sources", () => {
  const fixture = documentSections.find((item) => item.shape === "map")!;
  const artifact: Artifact = { ...deck(fixture.content, [], [{ sourceTitle: "Trial log", quote: "Supporting passage." }]), kind: "map" };
  render(<PrintRoot artifact={artifact} />);
  const paper = document.getElementById("print-root")!;
  expect(paper).toHaveAttribute("data-kind", "map");
  const outline = paper.querySelector(".mind-map-outline")!;
  expect(outline.querySelector("ul > li > ul > li > ul > li")).toHaveTextContent("Steers progenitors toward the red-cell branch [1]");
  expect(paper.querySelector(".outputs-print-sources")).toHaveTextContent("Trial log");
  expect(within(outline as HTMLElement).queryByRole("button", { hidden: true })).not.toBeInTheDocument();
});

it("prints every card with static citations, the correct choice and the explanation", () => {
  const fixture = documentSections.find((item) => item.shape === "cards")!;
  const artifact: Artifact = { ...deck(fixture.content, [], [{ sourceTitle: "Lab notes", quote: "A source passage." }]), kind: "cards" };
  render(<PrintRoot artifact={artifact} />);
  const paper = document.getElementById("print-root")!;
  expect(paper).toHaveAttribute("data-kind", "cards");
  expect(paper.querySelectorAll(".cards-list-item")).toHaveLength(2);
  expect(paper.querySelector(".is-correct")).toHaveTextContent("White-cell");
  expect(paper).toHaveTextContent("Red-cell");
  expect(paper).toHaveTextContent("Why: removing T shifted cells toward the white-cell lineage");
  expect(paper.querySelectorAll("sup")).toHaveLength(2);
  expect(paper).toHaveTextContent("Lab notes");
  expect(within(paper).queryByRole("button", { hidden: true })).not.toBeInTheDocument();
});

// The slides are drawn by a part of the app that is fetched when a deck is open, so they appear a moment after the root does.
describe("printing a deck", () => {
  const design = '<section class="slide"><h2>Field results</h2><gunther-chart></gunther-chart></section>';
  const title = "<!-- layout: title; design: v2; theme: paper; style: light -->\n# Deck";

  it("waits for the planned slides of a cold deck even when its Sources page has mounted", async () => {
    const artifact = deck("# Deck\n\n---\n\n## Field results", [], [{ sourceTitle: "Field record", quote: "All observations were logged." }]);
    let loaded!: () => void;
    printRenderer.pending = new Promise<void>((resolve) => { loaded = resolve; });
    render(<PrintRoot artifact={artifact} />);
    const paper = document.getElementById("print-root")!;
    expect(paper.querySelectorAll(".outputs-print-slide")).toHaveLength(1);
    expect(paper.querySelector(".outputs-print-slide-sources")).toBeInTheDocument();

    let prepared = false;
    const preparing = prepareForPrint(artifact.id).then((done) => { prepared = true; done(); });
    try {
      // Let both paint phases pass while the slide renderer is suspended.
      for (let frame = 0; frame < 5; frame++) await new Promise(requestAnimationFrame);
      expect(prepared).toBe(false);
    } finally {
      await act(async () => { printRenderer.pending = undefined; loaded(); });
      await preparing;
    }
    expect(paper.querySelectorAll(".outputs-print-slide")).toHaveLength(3);
    expect(document.documentElement).not.toHaveAttribute("data-print-prep");
  });

  it("prepares a designed slide with an intentionally empty chart slot", async () => {
    const artifact = deck(withDesignBlock(title, design));
    render(<PrintRoot artifact={artifact} />);
    const paper = document.getElementById("print-root")!;
    await waitFor(() => expect(paper.querySelector("[data-designed]")?.shadowRoot?.querySelector("gunther-chart")).toBeInTheDocument());
    expect(paper.querySelector("[data-designed]")?.shadowRoot?.querySelector("gunther-chart svg")).toBeNull();
    (await prepareForPrint(artifact.id, 1_000))();
  });

  it("waits for a drawable designed chart to gain a layout box", async () => {
    const content = withDesignBlock("<!-- layout: chart; chart: bar -->\n## Counts\n| Group | Count |\n| --- | --- |\n| A | 1 |\n| B | 3 |", design);
    const artifact = deck(`${title}\n\n---\n\n${content}`);
    render(<PrintRoot artifact={artifact} />);
    const paper = document.getElementById("print-root")!;
    const plot = () => paper.querySelector("[data-designed]")?.shadowRoot?.querySelector("gunther-chart .dk-plot svg");
    await waitFor(() => expect(plot()).toBeInTheDocument());
    const bounds = vi.spyOn(plot()!, "getBoundingClientRect").mockReturnValue(new DOMRect());
    let prepared = false;
    const preparing = prepareForPrint(artifact.id).then((done) => { prepared = true; return done; });
    await waitFor(() => expect(bounds).toHaveBeenCalled());
    expect(prepared).toBe(false);
    bounds.mockReturnValue(new DOMRect(0, 0, 600, 300));
    (await preparing)();
  });

  it("prints each slide without its marker, drawn as the layout it has", async () => {
    const content = [
      "<!-- layout: title; accent: blue -->\n# Deck\n\nOne line",
      "<!-- layout: flow -->\n## One\n\n1. a\n2. b\n\nNote: Say a.",
    ].join("\n\n---\n\n");
    render(<PrintRoot artifact={deck(content)} />);
    const paper = document.getElementById("print-root")!;
    await waitFor(() => expect(faces()).toEqual(["title", "flow"]));
    expect(drawn()).toEqual(["title", "flow"]);
    expect(paper.querySelector("style")).toHaveTextContent("@page { size: 13.333in 7.5in; margin: 0; }");
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
