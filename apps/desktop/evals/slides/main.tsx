import { useCallback, useState } from "react";
import { createRoot } from "react-dom/client";
import { SlideFace } from "../../src/outputs/SlideFace";
import { PrintRoot } from "../../src/outputs/PrintRoot";
import { SlideStylePicker } from "../../src/outputs/SlideStylePicker";
import type { SlideTheme } from "../../src/outputs/sections";
import type { Artifact } from "@gunther/contracts";
import packets from "../../../backend/evals/slides/packets.json";
import "../../src/styles.css";
import "../../src/design/tokens.css";
import "../../src/design/primitives.css";
import "../../src/design/prose.css";
import "../../src/outputs/outputs.css";
import "../../src/outputs/slides/slides.css";

const params = new URLSearchParams(location.search);
const theme = params.get("theme") ?? "briefing";
const designVersion = params.get("design") === "0" ? 0 : 1;
const chosen = packets.find(p => p.id === params.get("packet")) ?? packets[0]!;
const all = params.has("all");

function Face({ packet, slide, index, onCite }: { packet: typeof chosen; slide: typeof chosen.slides[number]; index: number; onCite: (index: number) => void }) {
  const [fit, setFit] = useState({ scale: 1, overflow: false });
  const measure = useCallback((scale: number, overflow: boolean) => setFit(old => old.scale === scale && old.overflow === overflow ? old : { scale, overflow }), []);
  const props = { body: slide.body, title: slide.layout === "title", layout: slide.layout, theme, accent: null, designVersion, numbers: packet.sources.map((_, i) => i + 1), sources: packet.sources.map(s => `${s.title} · ${s.locator}`), onFitChange: measure, onCite };
  return <article data-packet={packet.id} data-slide={index} data-fit={fit.scale} data-overflow={String(fit.overflow)}>
    <div className="sample-label">{slide.layout === "title" ? "Cover" : packet.title}<span>{Math.round(fit.scale * 100)}%</span></div>
    <div className="sample-stage"><SlideFace {...props}/></div>
  </article>;
}
function App() {
  const [cited, setCited] = useState<{ packet: string; index: number } | null>(null);
  const sourcePacket = packets.find(packet => packet.id === cited?.packet) ?? chosen;
  const artifact = { kind: "slides", title: chosen.title, createdAt: "2026-10-18", versionNumber: 1,
    content: chosen.slides.map((slide,index) => `<!-- layout: ${slide.layout}${index === 0 ? `; theme: ${theme}${designVersion ? "; design: v1" : ""}` : ""} -->\n${slide.body}`).join("\n\n---\n\n"),
    outline: chosen.slides.map(slide => ({ heading: "", goal: "", layout: slide.layout })),
    citations: chosen.sources.map((source,index) => ({ id: source.id, ref: index+1, sourceId: source.id, sourceTitle: source.title, locator: source.locator, quote: source.text })),
  } as unknown as Artifact;
  return <><style>{`
    body{margin:0;background:#e7e9ec;color:#18202b}main{padding:32px;max-width:1360px;margin:auto;font-family:Arial,sans-serif}header{display:flex;justify-content:space-between;align-items:end;margin-bottom:28px}h1{font-size:26px;margin:0 0 8px}header p{margin:0;color:#515c6b;font-size:14px}nav{display:flex;gap:8px}nav a{padding:9px 14px;background:white;border-radius:6px;color:#273c54;text-decoration:none;font-size:14px}.samples{display:grid;gap:30px}.sample-label{display:flex;justify-content:space-between;font-size:13px;color:#4c5664;margin-bottom:10px}.sample-stage{width:100%;aspect-ratio:16/9;position:relative;container-type:inline-size;overflow:hidden;box-shadow:0 9px 36px #1e2b3b12}.sample-stage>.outputs-slide-face{position:absolute;transform-origin:0 0;scale:calc(100cqw / 1280px)}.sources{background:white;padding:22px 28px;margin-top:30px}.sources h2{font-size:18px}.sources li{font-size:14px;line-height:1.6;margin-bottom:12px}.sources q{display:block;color:#586273}.capture main{padding:0;max-width:none}.capture header,.capture .sample-label,.capture .sources{display:none}.capture .samples{gap:0}.capture .sample-stage{box-shadow:none;width:1280px;height:720px}.capture article{width:1280px}.capture article:not(:first-child){display:none}@media print{body{background:white}header,.sample-label,.sources{display:none}main{padding:0}.samples{display:block}.sample-stage{width:1280px;height:720px;break-after:page;box-shadow:none}}
  `}</style><main><header><div><h1>{theme[0]!.toUpperCase() + theme.slice(1)} · {designVersion ? "new design" : "baseline"}</h1><p>Same source-backed content, rendered by Gunther. Evaluation sources are synthetic.</p></div><nav><SlideStylePicker value={theme as SlideTheme} accent={null} designVersion={designVersion} disabled={false} onSelect={value => { location.search = `?theme=${value}&design=${value === "classic" ? designVersion : 1}&packet=${chosen.id}`; }} numbers={chosen.sources.map((_, index) => index + 1)} samples={chosen.slides.slice(0,2).map(slide => ({ body: slide.body, title: slide.layout === "title", layout: slide.layout }))}/>{["briefing", "editorial", "keynote"].map(t=><a key={t} href={`?theme=${t}&design=${designVersion}&packet=${chosen.id}${all?"&all":""}`}>{t}</a>)}</nav></header>
    <section className="samples">{(all ? packets : [chosen]).flatMap(packet => packet.slides.map((slide,index)=><Face key={`${packet.id}-${index}`} packet={packet} slide={slide} index={index} onCite={index => setCited({ packet: packet.id, index })}/>))}</section>
    <section className="sources"><h2>Sources and scope · {sourcePacket.title}</h2><ol>{sourcePacket.sources.map((s,i)=><li key={s.id} aria-current={cited?.index === i ? "true" : undefined}><strong>[{i+1}] {s.title}</strong> · {s.locator}<q>{s.text}</q></li>)}</ol>{cited !== null && <p role="status">Selected source: {sourcePacket.sources[cited.index]?.title}</p>}</section>
  </main>{params.has("print") && <PrintRoot artifact={artifact}/>}</>;
}
createRoot(document.getElementById("root")!).render(<App/>);
