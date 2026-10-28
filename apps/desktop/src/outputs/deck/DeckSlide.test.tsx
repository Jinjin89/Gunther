import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DeckSlide } from "./DeckSlide";
import { parseDeck } from "./parseDeck";
import { fixture } from "./fixtures";
import { SlideFace } from "../SlideFace";

const SOURCES = ["Harlow 2024 · p. 3", "Okafor 2025", "Lindqvist 2023 · table 4", "City data 2025", "Survey 2024", "Review 2019", "Plan 2026", "Interviews 2025", "Memo 2025"];
const NUMBERS = SOURCES.map((_, at) => at + 1);

afterEach(() => vi.restoreAllMocks());

describe("DeckSlide", () => {
  it.each(["deck.short", "deck.max", "deck.zh"] as const)("draws every slide of %s", (name) => {
    const deck = parseDeck(fixture(name));
    for (const [at, slide] of deck.slides.entries()) {
      const { container, unmount } = render(<DeckSlide body={slide.body} layout={slide.layout} attrs={slide.attrs} theme={deck.theme} accent={deck.accent} first={at === 0} numbers={NUMBERS} sources={SOURCES} page={at + 1} />);
      const face = container.querySelector(".dk-face")!;
      expect(face.getAttribute("data-layout"), `slide ${at + 1}`).toBe(slide.data.layout);
      expect(face.textContent?.length, `slide ${at + 1}`).toBeGreaterThan(10);
      unmount();
    }
  });

  it("writes the sources a slide cites at its foot, and draws citations as superscripts that open the evidence", async () => {
    const onCite = vi.fn();
    const deck = parseDeck(fixture("deck.short"));
    const slide = deck.slides[2]!;
    const { container } = render(<DeckSlide body={slide.body} layout={slide.layout} theme="paper" numbers={NUMBERS} sources={SOURCES} page={3} onCite={onCite} />);
    expect(container.querySelector(".dk-source")?.textContent).toBe("Sources: Harlow 2024; Okafor 2025; Lindqvist 2023");
    expect(container.querySelector(".dk-page")?.textContent).toBe("3");
    await userEvent.click(screen.getAllByRole("button", { name: "Inspect citation 3" })[0]!);
    expect(onCite).toHaveBeenCalledWith(2);
  });

  it("is what SlideFace draws for a v2 deck, and old decks keep their renderer", () => {
    const v2 = render(<SlideFace body={"## A claim that is long enough\n- **a**: b\n- **c**: d"} title={false} layout="summary" designVersion={2} deckTheme="ink" numbers={[]} />);
    expect(v2.container.querySelector(".dk-face")?.getAttribute("data-theme")).toBe("ink");
    v2.unmount();
    const old = render(<SlideFace body={"## A claim\n- a\n- b"} title={false} layout="text" numbers={[]} />);
    expect(old.container.querySelector(".dk-face")).toBeNull();
    expect(old.container.querySelector(".outputs-slide-face")).not.toBeNull();
  });

  it("draws text that did not fit its layout as plain blocks", () => {
    const { container } = render(<DeckSlide body={"## A claim that is long enough\nA paragraph [1].\n\n- one\n- two"} layout="compare" theme="paper" numbers={[1]} sources={["S"]} />);
    expect(container.querySelector(".dk-prose p")?.textContent).toBe("A paragraph1.");
    expect(container.querySelectorAll(".dk-prose li").length).toBe(2);
  });

  it("does not treat the clipped portion of a panel crop as slide overflow", async () => {
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("dk-picture") ? 620 : 1120;
    });
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("dk-picture") ? 380 : 500;
    });
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      if (this.matches(".dk-face")) return DOMRect.fromRect({ x: 0, y: 0, width: 1280, height: 720 });
      if (this.matches(".dk-body")) return DOMRect.fromRect({ x: 80, y: 150, width: 1120, height: 500 });
      if (this.matches(".dk-crop img")) return DOMRect.fromRect({ x: -400, y: 0, width: 1400, height: 900 });
      return DOMRect.fromRect({ x: 100, y: 200, width: 200, height: 100 });
    });
    const body = "## Compare the controlled panels\n![Panel B](gunther-asset:source#x=0.5,y=0,w=0.5,h=1)\nThe comparison isolates feedback.\n- Setup and readout\n- Matched control\n- Interpretation";
    const first = vi.fn();
    const { container, rerender } = render(<DeckSlide body={body} layout="figure_detail" theme="paper" numbers={[]} onFitChange={first} />);
    await waitFor(() => expect(container.querySelector(".dk-crop img")).not.toBeNull());
    const settled = vi.fn();
    rerender(<DeckSlide body={body} layout="figure_detail" theme="paper" numbers={[]} onFitChange={settled} />);
    expect(settled).toHaveBeenCalledWith(1, false);
    expect(container.querySelector(".dk-face")?.getAttribute("data-overflow-why")).toBeNull();
  });
});

import { shortSource, sourceLine } from "./inlineTokens";

describe("the source line", () => {
  it("names each source once, without places, and shortens long titles", () => {
    expect(shortSource("Cool roofs in Velmora: a reanalysis (Okafor 2025) › Findings")).toBe("Okafor 2025");
    expect(shortSource("Interviews with Velmora building managers (2024)")).toBe("Interviews with Velmora building…");
    const sources = ["Paper A (Okafor 2025) · Findings", "Paper A (Okafor 2025) · Method", "Plan 2026"];
    expect(sourceLine("x [1] y [2][3]", [1, 2, 3], sources)).toBe("Sources: Okafor 2025; Plan 2026");
  });

  it("shows the first sources and how many more when the line is too long", () => {
    const sources = Array.from({ length: 9 }, (_, at) => `A rather long source name number ${at + 1}`);
    const line = sourceLine(sources.map((_, at) => `[${at + 1}]`).join(" "), sources.map((_, at) => at + 1), sources);
    expect(line).toMatch(/^Sources: .*\+\d+ more$/);
  });
});
