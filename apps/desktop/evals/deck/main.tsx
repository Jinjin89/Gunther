import { useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { DeckSlide } from "../../src/outputs/deck/DeckSlide";
import { parseDeck } from "../../src/outputs/deck/parseDeck";
import { withDeckMarker } from "../../src/outputs/deck/marker";
import { readTemplate } from "../../src/outputs/deck/brand";
import { setBrandTheme } from "../../src/outputs/deck/brandStore";
import { makeTestTemplate } from "../../src/outputs/deck/testTemplate";
import { DECK_THEMES, type DeckTheme } from "../../src/outputs/sections";
import "../../src/styles.css";
import "../../src/design/tokens.css";
import "../../src/design/primitives.css";
import "../../src/design/prose.css";
import "../../src/outputs/outputs.css";

/** Development gallery for the studio deck and report. Never part of the app. */
const fixtures = import.meta.glob("../../src/outputs/deck/fixtures/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const fixture = (name: string) => fixtures[`../../src/outputs/deck/fixtures/${name}.md`] ?? "";

declare global { interface Window { __GALLERY__?: { content: string; citations: Array<{ title: string; locator?: string }>; kind: string } } }

const params = new URLSearchParams(location.search);
const injected = window.__GALLERY__;
const theme = (DECK_THEMES.find((name) => name === params.get("theme")) ?? "paper") as DeckTheme;
const accent = params.get("accent") ?? undefined;
const sheet = params.has("sheet");

/** Sources of the fixtures, in citation-number order. */
const FIXTURE_SOURCES = ["Harlow 2024, p. 3", "Okafor 2025", "Lindqvist 2023, table 4", "City procurement data 2025", "Roofing survey 2024", "Met Office heatwave review 2019", "Climate plan 2026, section 5", "Tenant interviews 2025", "Planning dept. memo 2025"];

function Card({ index, total, content, sources }: { index: number; total: number; content: ReturnType<typeof parseDeck>; sources: string[] }) {
  const [fit, setFit] = useState({ fit: 1, overflow: false });
  const onFit = useCallback((next: number, overflow: boolean) => setFit((old) => old.fit === next && old.overflow === overflow ? old : { fit: next, overflow }), []);
  const slide = content.slides[index]!;
  const numbers = useMemo(() => sources.map((_, at) => at + 1), [sources]);
  const [why, setWhy] = useState("");
  return <article ref={(el) => { const reason = el?.querySelector<HTMLElement>(".dk-face")?.dataset.overflowWhy ?? ""; if (reason !== why) setWhy(reason); }} data-why={why} data-slide={index} data-layout={slide.data.layout} data-fit={fit.fit} data-overflow={String(fit.overflow)}>
    {!sheet && <div className="gallery-label">{index + 1}/{total} · {slide.data.layout} · fit {Math.round(fit.fit * 100)}%</div>}
    <div className="gallery-stage"><DeckSlide body={slide.body} layout={slide.layout} attrs={slide.attrs} theme={content.theme} accent={content.accent} first={index === 0} numbers={numbers} sources={sources} page={index + 1} onFitChange={onFit} /></div>
  </article>;
}

function Deck() {
  const source = injected?.content ?? fixture(params.get("fixture") ?? "deck.short");
  const content = useMemo(() => parseDeck(withDeckMarker(source, { theme, ...(accent ? { accent } : {}) })), [source]);
  const sources = injected ? injected.citations.map((c) => [c.title, c.locator].filter(Boolean).join(" · ")) : FIXTURE_SOURCES;
  return <main className={sheet ? "is-sheet" : ""}>{content.slides.map((_, at) => <Card key={at} index={at} total={content.slides.length} content={content} sources={sources} />)}</main>;
}

/** The brand variant: a template made in the page, imported the way a person would. */
const brandReady = theme === "brand" ? makeTestTemplate().then((bytes) => readTemplate(bytes, "aster.pptx")).then(({ brand }) => setBrandTheme(brand)) : Promise.resolve();

function App() {
  const [ready, setReady] = useState(false);
  useEffect(() => { void brandReady.then(() => setReady(true)); }, []);
  if (!ready) return null;
  return <><style>{`
    body{margin:0;background:#e4e6e9;font-family:Arial,sans-serif}main{padding:24px;display:grid;gap:28px;justify-content:center}
    .gallery-label{font-size:13px;color:#4c5664;margin-bottom:8px}.gallery-stage{width:1280px;height:720px;box-shadow:0 8px 30px #1e2b3b1a}
    main.is-sheet{padding:0;gap:0;grid-template-columns:repeat(2,640px);justify-content:start;background:#9aa0a8}
    main.is-sheet article{width:640px;height:360px;overflow:hidden;border:1px solid #9aa0a8;box-sizing:border-box;background:#fff}
    main.is-sheet .gallery-stage{box-shadow:none;transform:scale(.5);transform-origin:0 0}
  `}</style><Deck /></>;
}
createRoot(document.getElementById("root")!).render(<App />);
