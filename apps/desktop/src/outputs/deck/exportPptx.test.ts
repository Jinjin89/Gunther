import JSZip from "jszip";
import { afterEach, describe, expect, it, vi } from "vitest";
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

  it("draws a designed deck by the template when no browser draws it, without its design block", async () => {
    const content = fixture("deck.designed");
    const zip = await JSZip.loadAsync(await pptxBytes(content, { sources: SOURCES }));
    const slides = Object.keys(zip.files).filter((file) => /^ppt\/slides\/slide\d+\.xml$/.test(file));
    expect(slides.length).toBe(parseDeck(content).slides.length);
    for (const file of Object.keys(zip.files).filter((name) => /\.xml$/.test(name))) expect(await zip.file(file)!.async("string")).not.toMatch(/:::design|&lt;section/);
  });
});

// An invented 120 x 60 figure; geometry is checked in the actual OOXML export.
const PANEL_IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHgAAAA8CAIAAAAiz+n/AAAAnklEQVR4nO3QQREAIRDAsOP8e15UUB4kCjpdM/Nx3n874BVGR4yOGB0xOmJ0xOiI0RGjI0ZHjI4YHTE6YnTE6IjREaMjRkeMjhgdMTpidMToiNERoyNGR4yOGB0xOmJ0xOiI0RGjI0ZHjI4YHTE6YnTE6IjREaMjRkeMjhgdMTpidMToiNERoyNGR4yOGB0xOmJ0xOiI0RGjI0ZHjI5sCV0Ddeok22MAAAAASUVORK5CYII=";

const PANEL_JPEG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAA8AHgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AL+AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD/2Q==";

const PANEL_GIF = "data:image/gif;base64,R0lGODdheAA8AIEAAP///wAAAAAAAAAAACwAAAAAeAA8AEAIigABCBxIsKDBgwgTKlzIsKHDhxAjSpxIsaLFixgzatzIsaPHjyBDihxJsqTJkyhTqlzJsqXLlzBjypxJs6bNmzhz6tzJs6fPn0CDCh1KtKjRo0iTKl3KtKnTp1CjSp1KtarVq1izat3KtavXr2DDih1LtqzZs2jTql3Ltq3bt3Djyp1Lt67duzwDAgA7";

afterEach(() => vi.unstubAllGlobals());

it.each([
  ["png", PANEL_IMAGE, false], ["png", PANEL_IMAGE, true],
  ["jpeg", PANEL_JPEG, false], ["jpeg", PANEL_JPEG, true],
  ["gif", PANEL_GIF, false], ["gif", PANEL_GIF, true],
] as const)("exports intrinsic picture geometry (%s, crop=%s)", async (format, data, cropped) => {
  const decoded: string[] = [];
  if (format === "gif") vi.stubGlobal("Image", class {
    naturalWidth = 120;
    naturalHeight = 60;
    onload: (() => void) | null = null;
    set src(value: string) { decoded.push(value); queueMicrotask(() => this.onload?.()); }
  });
  const fragment = cropped ? "#x=0.25,y=0.1,w=0.5,h=0.8" : "";
  const content = "<!-- layout: title; design: v2 -->\n# Figure geometry\n\n---\n\n<!-- layout: figure -->\n## Controlled panel comparison\n![Panel](gunther-asset:panels" + fragment + ")\nA controlled observation";
  const zip = await JSZip.loadAsync(await pptxBytes(content, { resolveImage: async () => data }));
  if (format === "gif") expect(decoded).toContain(data);
  const xml = await zip.file("ppt/slides/slide2.xml")!.async("string");
  const pictures = [...xml.matchAll(/<p:pic>[\s\S]*?<\/p:pic>/g)];
  expect(pictures).toHaveLength(1);
  for (const [picture] of pictures) {
    const extent = /<a:ext cx="(\d+)" cy="(\d+)"/.exec(picture)!;
    const ratio = Number(extent[1]) / Number(extent[2]);
    const expected = cropped ? (120 * 0.5) / (60 * 0.8) : 120 / 60;
    expect(Math.abs(ratio / expected - 1)).toBeLessThan(0.01);
    // Extent must fit the slide, not merely have the right ratio.
    expect(Number(extent[1])).toBeLessThan(13.334 * 914400);
    expect(Number(extent[2])).toBeLessThan(7.5 * 914400);
    if (cropped) {
      const rect = /<a:srcRect ([^>]+)\/>/.exec(picture)![1]!;
      const fractions = Object.fromEntries([...rect.matchAll(/([lrtb])="(-?\d+)"/g)].map(m => [m[1], Number(m[2])]));
      expect(fractions).toEqual({ l: 25000, r: 25000, t: 10000, b: 10000 });
    } else expect(picture).not.toContain("<a:srcRect");
  }
});
