import { useCallback, useState } from "react";
import { createRoot } from "react-dom/client";
import type { Artifact } from "@gunther/contracts";
import { SlideFace } from "../src/outputs/SlideFace";
import { PrintRoot } from "../src/outputs/PrintRoot";
import { attrsOf, parseSlides, slideLayouts, splitDocument } from "../src/outputs/sections";
import { splitBlocks } from "../src/outputs/slides/scene";
import { measureSlide, waitForSlides } from "../src/outputs/slideMeasurements";
import "../src/styles.css";
import "../src/design/tokens.css";
import "../src/design/primitives.css";
import "../src/design/prose.css";
import "../src/outputs/outputs.css";
import "../src/outputs/slides/slides.css";

interface CaptureInput { artifact: Artifact; candidateHash: string; slideIds: string[]; use: "talk" | "read" }
async function start() {
const input = await fetch("./candidate.json").then(response => {
  if (!response.ok) throw new Error("The capture candidate is unavailable.");
  return response.json() as Promise<CaptureInput>;
});
const slides = parseSlides(input.artifact.content);
const layouts = slideLayouts(input.artifact);
const attrs = splitDocument(input.artifact.content, "slides").sections.map(attrsOf);
const numbers = input.artifact.citations.map((_, index) => index + 1);
const sources = input.artifact.citations.map(citation => [citation.sourceTitle, citation.locator].filter(Boolean).join(" \u00b7 "));
const theme = slides[0]?.theme ?? "classic";
const accent = slides[0]?.accent;
const designVersion = slides[0]?.designVersion ?? 0;
function Face({ index }: { index: number }) {
  const slide = slides[index]!;
  const [fit, setFit] = useState({ scale: 1, overflow: false });
  const [issue, setIssue] = useState<string | null>(null);
  const change = useCallback((scale: number, overflow: boolean) => setFit(old => old.scale === scale && old.overflow === overflow ? old : { scale, overflow }), []);
  return <article data-slide-id={input.slideIds[index]} data-fit={fit.scale} data-overflow={fit.overflow} data-scene-issue={issue ?? ""}>
    <SlideFace body={slide.body} title={index === 0} layout={layouts[index]} theme={theme} accent={accent} designVersion={designVersion} attrs={attrs[index]} deckTheme={slides[0]?.deckTheme} first={index === 0} page={index + 1} composition={slide.composition} scene={slide.scene} numbers={designVersion === 2 ? numbers : input.artifact.citations.map(citation => citation.ref ?? 0)} sources={sources} onFitChange={change} onSceneIssue={setIssue}/>
  </article>;
}
function App() {
  return <><style>{`body{margin:0}#capture-slides{width:1280px}#capture-slides>article{width:1280px;height:720px;position:relative;overflow:hidden}*{animation:none!important;transition:none!important}html{scroll-behavior:auto!important}`}</style>
    <main id="capture-slides">{slides.map((_, index) => <Face key={index} index={index}/>)}</main><PrintRoot artifact={input.artifact}/></>;
}
createRoot(document.getElementById("root")!).render(<App/>);

// Only this narrow, versioned interface is called by the local worker through CDP.
Object.assign(window, { guntherCapture: {
  version: 1,
  async ready(print = false) {
    const selector = print ? "#print-root :is(.outputs-slide-face,.dk-face)" : "#capture-slides :is(.outputs-slide-face,.dk-face)";
    await waitForSlides(selector, slides.length, 15000);
    return {
      version: 1, candidateHash: input.candidateHash,
      slides: [...document.querySelectorAll<HTMLElement>(selector)].map((face, index) => ({
        id: input.slideIds[index], index, blocks: splitBlocks(slides[index]!.body).blocks.map(block => ({ id: block.id, type: block.type })),
        ...measureSlide(face, input.use),
      })),
    };
  },
} });
}
void start();
