import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SlideFace } from "../SlideFace";
import { parseSlides } from "../sections";
import gallery from "./gallery.fixture.md?raw";
import { SLIDE_LAYOUTS } from "./parseSlide";

// What only a layout's own component draws, so that a slide drawn as plain text, or as another layout, is told apart.
const OWN: Record<string, string> = {
  title: ".outputs-s-rule",
  big_number: ".outputs-s-bignum",
  compare: ".outputs-s-table",
  two_column: ".outputs-s-sides",
  flow: ".outputs-s-steps",
  timeline: ".outputs-s-events",
  bar_chart: ".outputs-s-bars",
  line_chart: ".outputs-s-line-chart svg",
  cards: ".outputs-s-cards",
  quote: ".outputs-s-quote blockquote",
  figure: ".outputs-s-picture",
  diagram: ".outputs-s-diagram svg",
  text: ".outputs-s-body",
  takeaways: ".outputs-s-takeaways",
};

const NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 9];
const slides = parseSlides(gallery);

afterEach(cleanup);

describe("the gallery deck", () => {
  it("has a slide for every layout, and two of them are charts", () => {
    expect(slides).toHaveLength(SLIDE_LAYOUTS.length);
    expect(slides.map((slide) => slide.layout)).toEqual([...SLIDE_LAYOUTS]);
    expect(slides.filter((slide) => slide.layout?.endsWith("_chart"))).toHaveLength(2);
    // The title slide chooses the colour of the deck.
    expect(slides[0]?.accent).toBe("blue");
  });

  it.each(SLIDE_LAYOUTS.map((layout, index) => [layout, index] as const))("draws its %s slide with the component of that layout, not as plain text", (layout, index) => {
    const slide = slides[index]!;
    const { container } = render(<SlideFace body={slide.body} title={index === 0} layout={slide.layout} accent={slides[0]?.accent} numbers={NUMBERS} />);
    const face = container.querySelector<HTMLElement>(".outputs-slide-face")!;
    expect(face).toHaveAttribute("data-layout", layout);
    // The face says which layout it drew, and has that layout's own parts: it did not fall back.
    expect(face).toHaveAttribute("data-drawn", layout);
    expect(face).toHaveClass("is-designed");
    expect(face.querySelector(OWN[layout]!)).not.toBeNull();
    expect(face.style.getPropertyValue("--outputs-accent")).toBe("#2668b6");
    // The slide's words are there: nothing was dropped on the way to the drawing.
    expect(face.querySelector("h1, h2")).not.toBeNull();
  });

  it("cites only the nine sources it has, and every citation on a slide is a button that opens one", () => {
    const cited = new Set(gallery.match(/\[(\d+)\]/g)?.map((mark) => Number(mark.slice(1, -1))));
    expect([...cited].sort((a, b) => a - b)).toEqual(NUMBERS);
    for (const [index, slide] of slides.entries()) {
      const { container, unmount } = render(<SlideFace body={slide.body} title={index === 0} layout={slide.layout} numbers={NUMBERS} onCite={() => undefined} />);
      const marks = slide.body.match(/\[\d+\]/g)?.length ?? 0;
      expect(container.querySelectorAll("button[aria-label^='Inspect citation']"), `slide ${index + 1} (${slide.layout})`).toHaveLength(marks);
      unmount();
    }
  });
});
