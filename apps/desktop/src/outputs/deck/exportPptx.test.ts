import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { parseDeck } from "./parseDeck";
import { pptxBytes } from "./exportPptx";
import { fixture } from "./fixtures";

const SOURCES = ["Harlow 2024 · p. 3", "Okafor 2025", "Lindqvist 2023 · table 4"];

describe("exportPptx", () => {
  it.each(["deck.short", "deck.zh"] as const)("writes one slide for each slide of %s, with notes and native charts", async (name) => {
    const content = fixture(name);
    const deck = parseDeck(content);
    const zip = await JSZip.loadAsync(await pptxBytes(content, { sources: SOURCES }));
    const slides = Object.keys(zip.files).filter((file) => /^ppt\/slides\/slide\d+\.xml$/.test(file));
    expect(slides.length).toBe(deck.slides.length);
    const notes = await Promise.all(Object.keys(zip.files).filter((file) => /^ppt\/notesSlides\/notesSlide\d+\.xml$/.test(file)).map((file) => zip.file(file)!.async("string")));
    const spoken = deck.slides.find((s) => s.notes)!.notes.split(/[.:：，。]/)[0]!.slice(0, 6);
    expect(notes.some((xml) => xml.includes(spoken))).toBe(true);
    expect(Object.keys(zip.files).filter((file) => /^ppt\/charts\/chart\d+\.xml$/.test(file)).length).toBe(deck.slides.filter((s) => s.data.layout === "chart").length);
    const table = await zip.file("ppt/slides/slide9.xml")!.async("string");
    expect(table).toContain("<a:tbl>");
  });

  it("writes citations as superscript numbers and the source line as a text box", async () => {
    const zip = await JSZip.loadAsync(await pptxBytes(fixture("deck.short"), { sources: SOURCES }));
    const summary = await zip.file("ppt/slides/slide3.xml")!.async("string");
    expect(summary).toContain('baseline="30000"');
    expect(summary).toContain("Sources: Harlow 2024; Okafor 2025; Lindqvist 2023");
  });
});
