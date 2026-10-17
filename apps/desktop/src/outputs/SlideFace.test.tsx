import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SlideFace } from "./SlideFace";
import { fitFor, settleFit } from "./slideFit";
import { SLIDE_LAYOUTS } from "./slides/parseSlide";

describe("how far a slide's type is scaled down", () => {
  it("leaves text that fits alone, and never grows it", () => {
    expect(fitFor(0.7, 0.83)).toBe(1);
    expect(fitFor(0.7, 0.83, 0.8)).toBe(0.8);
  });

  it("scales text to the room it has, but no smaller than is worth reading", () => {
    expect(fitFor(1.25, 0.8333)).toBe(0.66);
    // Worked out from a size it was already scaled to.
    expect(fitFor(1.0, 0.8333, 0.8)).toBe(0.66);
    expect(fitFor(3, 0.8333)).toBe(0.55);
  });

  it("does nothing for a slide that has no size yet", () => {
    expect(fitFor(0, 0.8333)).toBe(1);
    expect(fitFor(Number.NaN, 0.8333)).toBe(1);
    expect(fitFor(1.25, Number.NaN)).toBe(1);
  });
});

describe("the largest size that fits", () => {
  // Content whose height grows with the square of the type, as wrapped text does: it takes `size² × 900` px of a 720 px slide.
  const used = (size: number) => (size * size * 900) / 720;
  const room = 1 - 120 / 720;

  it("is found between the size the proportional guess gives and the size that did not fit", () => {
    // fitFor says 0.66 for it, and 0.81 is the largest size to a hundredth that fits (0.82 does not).
    const guess = fitFor(used(1), room);
    expect(guess).toBe(0.66);
    expect(settleFit(used, room, guess, 1)).toBe(0.81);
  });

  it("keeps the size it was given when nothing larger fits, or when there is nothing between", () => {
    expect(settleFit(() => 99, room, 0.6, 1)).toBe(0.6);
    expect(settleFit(used, room, 0.8, 0.81)).toBe(0.8);
    expect(settleFit(used, room, 0.81, 0.81)).toBe(0.81);
  });

  it("gives the largest size when everything fits, to a hundredth, in a few looks", () => {
    const looks = vi.fn(() => 0);
    const found = settleFit(looks, room, 0.55, 1);
    expect(found).toBeGreaterThanOrEqual(0.99);
    expect(found).toBeLessThan(1);
    expect(looks.mock.calls.length).toBeLessThanOrEqual(8);
  });
});

/**
 * jsdom lays nothing out, so the sizes are stood in for: a face is 720 px tall with 56 px above and
 * 64 px below its text, and its text is 10 px tall per character, scaled by the face's fit.
 */
beforeEach(() => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const unseen = this.closest("[data-unseen]") !== null;
    let height = 0;
    if (!unseen && this.classList.contains("outputs-slide-face")) height = 720;
    else if (!unseen && this.classList.contains("answer-markdown") && this.parentElement?.classList.contains("outputs-slide-face")) {
      const fit = Number((this.parentElement as HTMLElement).style.getPropertyValue("--outputs-fit") || 1);
      height = (this.textContent ?? "").length * 10 * fit;
    } else if (!unseen && this.classList.contains("outputs-s")) {
      // A designed slide wraps its words, so it is as tall as the square of its type.
      const fit = Number((this.closest(".outputs-slide-face") as HTMLElement).style.getPropertyValue("--outputs-fit") || 1);
      height = (this.textContent ?? "").length * 10 * fit * fit;
    }
    return { height, width: 1280, top: 0, left: 0, right: 1280, bottom: height, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  });
  const real = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element: Element, pseudo?: string | null) =>
    element.classList.contains("outputs-slide-face")
      ? ({ paddingTop: "56px", paddingBottom: "64px", height: "720px" } as CSSStyleDeclaration)
      : real(element, pseudo));
});

afterEach(() => vi.restoreAllMocks());

const fitOf = (container: HTMLElement) => container.querySelector<HTMLElement>(".outputs-slide-face")?.style.getPropertyValue("--outputs-fit");

describe("a slide face", () => {
  it("draws long text smaller, and the same everywhere the slide is drawn", () => {
    const long = "A long bullet. ".repeat(6).trim();
    const deck = render(<SlideFace body={long} title={false} numbers={[]} />);
    // The printed copy is never laid out on screen, so it cannot measure; it takes what the deck found.
    const printed = render(<div data-unseen><SlideFace body={long} title={false} numbers={[]} /></div>);

    // Ten pixels a character on a slide with 600 px of room: the text is scaled to 600 / (10 × characters).
    const characters = deck.container.querySelector(".answer-markdown")?.textContent?.length ?? 0;
    expect(characters).toBeGreaterThan(60);
    const expected = String(Math.floor(6000 / characters) / 100);
    expect(Number(expected)).toBeLessThan(1);
    expect(fitOf(deck.container)).toBe(expected);
    expect(fitOf(printed.container)).toBe(expected);
  });

  it("leaves a short slide as it was designed", () => {
    const { container } = render(<SlideFace body="A short line" title={false} numbers={[]} />);
    expect(fitOf(container)).toBe("1");
  });

  it("keeps the layout it was given on the face, and draws the text the same", () => {
    const plain = render(<SlideFace body="A short line" title={false} numbers={[]} />);
    const marked = render(<SlideFace body="A short line" title={false} layout="compare" numbers={[]} />);
    expect(plain.container.querySelector(".outputs-slide-face")).not.toHaveAttribute("data-layout");
    expect(marked.container.querySelector(".outputs-slide-face")).toHaveAttribute("data-layout", "compare");
    expect(marked.container.querySelector(".answer-markdown")?.innerHTML).toBe(plain.container.querySelector(".answer-markdown")?.innerHTML);
  });

  it("draws a slide that takes its layout's form as that layout, in the deck's accent", () => {
    const body = "## Labour leads\n| Item | Share (%) |\n| --- | --- |\n| **Labour** | 62 [3] |\n| Gear | 18 [3] |";
    const { container } = render(<SlideFace body={body} title={false} layout="bar_chart" accent="teal" numbers={[3]} />);
    const face = container.querySelector<HTMLElement>(".outputs-slide-face")!;
    expect(face).toHaveClass("is-designed");
    expect(face).toHaveAttribute("data-layout", "bar_chart");
    expect(face).toHaveAttribute("data-drawn", "bar_chart");
    expect(face.style.getPropertyValue("--outputs-accent")).toBe("#028066");
    expect(face.firstElementChild).toHaveClass("outputs-s");
    // A deck with no accent is black and white.
    const plain = render(<SlideFace body={body} title={false} layout="bar_chart" numbers={[3]} />);
    expect(plain.container.querySelector<HTMLElement>(".outputs-slide-face")!.style.getPropertyValue("--outputs-accent")).toBe("#1a1a18");
    // So is one whose accent is a name the app does not know.
    const odd = render(<SlideFace body={body} title={false} layout="bar_chart" accent="pink" numbers={[3]} />);
    expect(odd.container.querySelector<HTMLElement>(".outputs-slide-face")!.style.getPropertyValue("--outputs-accent")).toBe("#1a1a18");
  });

  it("draws a slide that does not take its layout's form exactly as a slide with no layout, whatever the accent", () => {
    // Each of these layouts asks for something this text does not have.
    const asking = SLIDE_LAYOUTS.filter((layout) => !["text", "takeaways"].includes(layout));
    expect(asking).toHaveLength(11);
    const body = "## A heading\n\nA short line, and [1] a number";
    const plain = render(<SlideFace body={body} title={false} numbers={[1]} />);
    const reference = plain.container.querySelector(".outputs-slide-face")!;
    for (const layout of asking) {
      const marked = render(<SlideFace body={body} title={false} layout={layout} accent="red" numbers={[1]} />);
      const face = marked.container.querySelector(".outputs-slide-face")!;
      expect(face).not.toHaveAttribute("data-drawn");
      expect(face).not.toHaveClass("is-designed");
      // The same face: its layout is the only thing it is told, and the accent is not on it.
      expect(face.getAttribute("class")).toBe(reference.getAttribute("class"));
      expect(face.getAttribute("style")).toBe(reference.getAttribute("style"));
      expect(face.innerHTML).toBe(reference.innerHTML);
      expect(face).toHaveAttribute("data-layout", layout);
      marked.unmount();
    }
  });

  it("keeps the title slide centred, in either way of drawing", () => {
    const designed = render(<SlideFace body="# Deck" title layout="title" numbers={[]} />);
    expect(designed.container.querySelector(".outputs-slide-face")).toHaveClass("is-title", "is-designed");
    const plain = render(<SlideFace body="# Deck" title numbers={[]} />);
    expect(plain.container.querySelector(".outputs-slide-face")).toHaveClass("is-title");
    expect(plain.container.querySelector(".outputs-slide-face")).not.toHaveClass("is-designed");
  });

  it("draws a designed slide as large as it can be, not as small as its proportion says", () => {
    const body = `## Reasoning\n\n${"A line of reasoning that goes on. ".repeat(4)}`;
    const { container } = render(<SlideFace body={body} title={false} layout="text" numbers={[]} />);
    const block = container.querySelector(".outputs-s")!;
    // The mock makes the block 10 px a character times the square of the type; 600 px is the room.
    const characters = block.textContent?.length ?? 0;
    const largest = Math.floor(Math.sqrt(600 / (characters * 10)) * 100) / 100;
    const proportional = Math.floor(6000 / characters) / 100;
    expect(characters).toBeGreaterThan(100);
    const fit = Number(fitOf(container));
    expect(fit).toBeLessThan(1);
    expect(Math.abs(fit - largest)).toBeLessThanOrEqual(0.011);
    expect(fit).toBeGreaterThan(proportional);
    // The same everywhere the slide is drawn, the printed page included.
    const printed = render(<div data-unseen><SlideFace body={body} title={false} layout="text" numbers={[]} /></div>);
    expect(fitOf(printed.container)).toBe(String(fit));
  });

  it("does not guess for a slide that is not in view", () => {
    const { container } = render(<div data-unseen><SlideFace body={"Not shown. ".repeat(12)} title={false} numbers={[]} /></div>);
    expect(fitOf(container)).toBe("1");
  });
});
