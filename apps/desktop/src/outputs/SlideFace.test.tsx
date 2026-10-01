import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SlideFace } from "./SlideFace";
import { fitFor } from "./slideFit";

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

/**
 * jsdom lays nothing out, so the sizes are stood in for: a face is 720 px tall with 56 px above and
 * 64 px below its text, and its text is 10 px tall per character, scaled by the face's fit.
 */
beforeEach(() => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const unseen = this.closest("[data-unseen]") !== null;
    let height = 0;
    if (!unseen && this.classList.contains("outputs-slide-face")) height = 720;
    else if (!unseen && this.classList.contains("answer-markdown")) {
      const fit = Number((this.parentElement as HTMLElement).style.getPropertyValue("--outputs-fit") || 1);
      height = (this.textContent ?? "").length * 10 * fit;
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

  it("does not guess for a slide that is not in view", () => {
    const { container } = render(<div data-unseen><SlideFace body={"Not shown. ".repeat(12)} title={false} numbers={[]} /></div>);
    expect(fitOf(container)).toBe("1");
  });
});
