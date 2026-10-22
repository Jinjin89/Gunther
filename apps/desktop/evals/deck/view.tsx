import { createRoot } from "react-dom/client";
import type { Artifact } from "@gunther/contracts";
import { SlidesView } from "../../src/outputs/SlidesView";
import { withDeckMarker } from "../../src/outputs/deck/marker";
import { DECK_THEMES, type DeckTheme } from "../../src/outputs/sections";
import "../../src/styles.css";
import "../../src/design/tokens.css";
import "../../src/design/primitives.css";
import "../../src/design/prose.css";
import "../../src/outputs/outputs.css";
import "../../src/outputs/workspace.css";

/** Development page: a studio deck in the app's own SlidesView (arrows, thumbnails, notes). Never part of the app. */
const fixtures = import.meta.glob("../../src/outputs/deck/fixtures/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const params = new URLSearchParams(location.search);
const theme = (DECK_THEMES.find((name) => name === params.get("theme")) ?? "paper") as DeckTheme;
const content = withDeckMarker(fixtures["../../src/outputs/deck/fixtures/deck.short.md"] ?? "", { theme });
const titles = ["Harlow 2024", "Okafor 2025", "Lindqvist 2023", "City data 2025", "Survey 2024", "Review 2019", "Plan 2026", "Interviews 2025", "Memo 2025"];
const artifact = {
  id: "deck", kind: "slides", title: "Cool roofs", content, origin: "agent", sections: [], outline: [], versionNumber: 1, createdAt: "2026-03-01",
  citations: titles.map((t, at) => ({ id: String(at), ref: at + 1, sourceId: String(at), sourceTitle: t, locator: "p. 3", quote: "" })),
} as unknown as Artifact;
createRoot(document.getElementById("root")!).render(<main style={{ maxWidth: 1000, margin: "16px auto" }}><SlidesView artifact={artifact} onCite={() => undefined} goTo={params.get("slide") ? { index: Number(params.get("slide")), nonce: 1 } : null} /></main>);
