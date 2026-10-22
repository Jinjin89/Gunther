import type { Artifact } from "@gunther/contracts";
import { citationNumbers } from "../pages/AnswerBody";
import { parseSlides, slideLayouts } from "./sections";
import { SlideFace } from "./SlideFace";

/**
 * The slides of a deck on paper: one 1280 × 720 face to a page, drawn as the deck is on the
 * page. They are in a file of their own because they carry every layout's drawing and the
 * chart library, which PrintRoot (always in the page) loads only for a deck.
 */
export function PrintSlides({ artifact }: { artifact: Artifact }) {
  const numbers = citationNumbers(artifact.citations);
  const layouts = slideLayouts(artifact);
  const slides = parseSlides(artifact.content);
  const accent = slides[0]?.accent ?? null;
  const theme = slides[0]?.theme ?? "classic";
  const designVersion = slides[0]?.designVersion ?? 0;
  return <>
    {slides.map((slide, index) => <section key={index} className="outputs-print-slide">
      <SlideFace body={slide.body} title={index === 0} layout={layouts[index]} accent={accent} theme={theme} designVersion={designVersion} composition={slide.composition} scene={slide.scene} numbers={numbers} />
    </section>)}
  </>;
}
