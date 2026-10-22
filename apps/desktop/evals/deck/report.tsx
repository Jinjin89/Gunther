import { createRoot } from "react-dom/client";
import type { Artifact } from "@gunther/contracts";
import { ReportView } from "../../src/outputs/ReportView";
import "../../src/styles.css";
import "../../src/design/tokens.css";
import "../../src/design/primitives.css";
import "../../src/design/prose.css";
import "../../src/outputs/outputs.css";

/** Development gallery: a studio report as the app shows it. Never part of the app. */
declare global { interface Window { __GALLERY__?: { content: string; citations: Array<{ title: string; locator?: string }>; kind: string } } }
const fixtures = import.meta.glob("../../src/outputs/deck/fixtures/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const content = window.__GALLERY__?.content ?? fixtures["../../src/outputs/deck/fixtures/report.md"] ?? "";
const citations = window.__GALLERY__?.citations?.length ? window.__GALLERY__.citations : ["Harlow 2024, p. 3", "Okafor 2025", "Lindqvist 2023, table 4", "City procurement data 2025", "Roofing survey 2024", "Met Office review 2019", "Climate plan 2026", "Tenant interviews 2025", "Planning memo 2025"].map((title) => ({ title }));
const artifact = {
  id: "report", kind: "report", title: /^#\s+(.*)$/m.exec(content)?.[1] ?? "Report", content, origin: "agent", sections: [], outline: [], versionNumber: 1, createdAt: "2026-03-01",
  citations: citations.map((c, at) => ({ id: String(at), ref: at + 1, sourceId: String(at), sourceTitle: c.title, locator: c.locator ?? "", quote: "" })),
} as unknown as Artifact;
const params = new URLSearchParams(location.search);

createRoot(document.getElementById("root")!).render(<>
  <style>{`body{margin:0;background:#fff}main{padding:32px 48px 64px;max-width:1000px;margin:auto}`}</style>
  <main className={params.get("theme") ? `theme-${params.get("theme")}` : ""}>
    <article className="outputs-document is-saved is-studio"><h2 className="outputs-title">{artifact.title}</h2>
      <ReportView artifact={artifact} onCite={() => undefined} /></article>
  </main>
</>);
