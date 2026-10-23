import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { clampToStage, pickFont, readColour, type TextBlock } from "./designedText";
import { buildDocx } from "./exportDocx";
import { pptxBytes } from "./exportPptx";
import type { DesignedCapture } from "./designedPptx";
import { parseDeck } from "./parseDeck";
import { fixture } from "./fixtures";
import { Packer } from "docx";

const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const SOURCES = ["Harlow 2024 · p. 3", "Okafor 2025", "Lindqvist 2023 · table 4"];
const run = (text: string, extra: Partial<TextBlock["runs"][number]> = {}) => ({ text, font: "Inter", size: 20, bold: false, italic: false, underline: false, color: "111317", transparency: 0, superscript: false, spacing: 0, breakLine: false, ...extra });
const block = (text: string, y: number, extra: Partial<TextBlock> = {}): TextBlock => ({ x: 80, y, w: 600, h: 40, align: "left", lineHeight: 28, lines: 1, wrap: false, runs: [run(text)], ...extra });

/** A stand-in for the browser: one capture for each designed slide, with the slide's title and a number as boxes. */
const fake = async (jobs: Array<{ html: string }>): Promise<Array<DesignedCapture | null>> => jobs.map((job, at) => {
  const title = /<h[12][^>]*>([\s\S]*?)<\/h[12]>/.exec(job.html)?.[1]?.replace(/<[^>]+>/g, "").trim() ?? `Slide ${at}`;
  return { image: PNG, blocks: [block(title, 60 + at, { w: 1100, wrap: true, lines: 2, h: 56 }), block("9%", 300, { runs: [run("9", { size: 120, bold: true, color: "E4572E" }), run("%", { size: 60 }), run("3", { superscript: true })] })] };
});

describe("designed slides in a PowerPoint file", () => {
  it("writes a background picture, real text boxes and the notes for each designed slide", async () => {
    const content = fixture("deck.designed");
    const deck = parseDeck(content);
    const designed = deck.slides.filter((slide) => slide.design);
    expect(designed.length).toBeGreaterThan(5);
    const zip = await JSZip.loadAsync(await pptxBytes(content, { sources: SOURCES, designed: fake }));
    const files = Object.keys(zip.files);
    const slides = files.filter((file) => /^ppt\/slides\/slide\d+\.xml$/.test(file));
    expect(slides.length).toBe(deck.slides.length);
    expect(files.filter((file) => /^ppt\/media\/.*\.png$/.test(file)).length).toBe(designed.length);
    for (const [at, slide] of deck.slides.entries()) {
      const xml = await zip.file(`ppt/slides/slide${at + 1}.xml`)!.async("string");
      if (!slide.design) continue;
      expect(xml.match(/<p:pic>/g)?.length).toBe(1);
      expect(xml).toContain('<a:off x="0" y="0"/>');
      expect(xml).toContain('<a:ext cx="12192000" cy="6858000"/>');
      expect(xml.match(/<p:sp>/g)?.length).toBe(2);
      expect(xml).toContain("<a:t>9</a:t>");
      expect(xml).toContain('baseline="30000"');
      expect(xml).toContain('wrap="none"');
      // Every box lies inside the slide.
      for (const [, x, y, cx, cy] of xml.matchAll(/<a:off x="(\d+)" y="(\d+)"\/><a:ext cx="(\d+)" cy="(\d+)"\/>/g)) {
        expect(+x! + +cx!).toBeLessThanOrEqual(12192000 + 1);
        expect(+y! + +cy!).toBeLessThanOrEqual(6858000 + 1);
      }
    }
    const notes = await Promise.all(files.filter((file) => /^ppt\/notesSlides\/notesSlide\d+\.xml$/.test(file)).map((file) => zip.file(file)!.async("string")));
    const spoken = designed.find((slide) => slide.notes)!.notes.split(/[.:：，。]/)[0]!.slice(0, 8);
    expect(notes.some((xml) => xml.includes(spoken))).toBe(true);
    for (const file of files.filter((name) => /\.xml$/.test(name))) expect(await zip.file(file)!.async("string")).not.toMatch(/:::design|&lt;section/);
  });

  it("leaves a slide without a design to the template, and a failed capture too", async () => {
    const content = fixture("deck.designed");
    const deck = parseDeck(content);
    const plain = content.replace(/:::design[\s\S]*?\n:::/, ""); // the first slide loses its design
    expect(parseDeck(plain).slides[0]!.design).toBeNull();
    const zip = await JSZip.loadAsync(await pptxBytes(plain, { sources: SOURCES, designed: async (jobs) => jobs.map(() => null) }));
    expect(Object.keys(zip.files).filter((file) => /^ppt\/media\/.*\.png$/.test(file)).length).toBe(0);
    const first = await zip.file("ppt/slides/slide1.xml")!.async("string");
    expect(deck.slides[0]).toBeTruthy();
    expect(first).toContain("<p:sp>");
    expect(first).not.toContain("<p:pic>");
  });

  it("is the template's own file when no browser is given", async () => {
    const zip = await JSZip.loadAsync(await pptxBytes(fixture("deck.designed"), { sources: SOURCES }));
    expect(Object.keys(zip.files).filter((file) => /^ppt\/media\/.*\.png$/.test(file)).length).toBe(0);
    const first = await zip.file("ppt/slides/slide1.xml")!.async("string");
    expect(first).not.toContain("<p:pic>");
  });
});

describe("reading the browser's text", () => {
  it("reads colours with and without alpha", () => {
    expect(readColour("rgb(228, 87, 46)")).toEqual({ hex: "E4572E", alpha: 1 });
    expect(readColour("rgba(0, 0, 0, 0.5)")).toEqual({ hex: "000000", alpha: 0.5 });
    expect(readColour("color(srgb 1 0 0 / 0.25)")).toEqual({ hex: "FF0000", alpha: 0.25 });
    expect(readColour("transparent").alpha).toBe(0);
  });
  it("names the first family, and the CJK family for Chinese text", () => {
    const stack = '"Inter", "PingFang SC", "Noto Sans CJK SC", sans-serif';
    expect(pickFont(stack, "Revenue")).toBe("Inter");
    expect(pickFont(stack, "营收增长")).toBe("PingFang SC");
    expect(pickFont('"Source Serif 4", "Noto Serif CJK SC", serif', "营收")).toBe("Noto Serif CJK SC");
    expect(pickFont("Inter, sans-serif", "营收")).toBe("PingFang SC");
    expect(pickFont("sans-serif", "x")).toBe("Arial");
  });
  it("keeps a box inside the slide", () => {
    const box = clampToStage(block("x", 700, { x: 1200, w: 400, h: 100 }));
    expect(box.x + box.w).toBeLessThanOrEqual(1280);
    expect(box.y + box.h).toBeLessThanOrEqual(720);
  });
});

describe("a designed deck as a Word file", () => {
  it.each(["deck.designed", "deck.designed.zh"] as const)("never carries the design block of %s", async (name) => {
    const zip = await JSZip.loadAsync(await Packer.toBuffer(buildDocx(fixture(name), { sources: SOURCES, title: "Deck" })));
    const xml = await zip.file("word/document.xml")!.async("string");
    expect(xml).not.toMatch(/<section|&lt;section|:::design|var\(--|class="slide"|display:flex/);
    expect(xml).toContain("<w:t");
  });
});
