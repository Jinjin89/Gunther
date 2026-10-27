import JSZip from "jszip";
import { beforeEach, describe, expect, it } from "vitest";
import { readTemplate } from "./brand";
import { getBrandTheme, setBrandTheme } from "./brandStore";
import { resolveTheme } from "./theme";
import { pptxBytes } from "./exportPptx";
import { readable } from "./colour";
import { fixture } from "./fixtures";
import { makeTestTemplate, TEST_TEMPLATE } from "./testTemplate";
import { withDeckMarker } from "./marker";
import { render } from "@testing-library/react";
import { DeckSlide } from "./DeckSlide";
import { budgetIssues, parseDeckSlide } from "./parseDeck";

describe("importing a template", () => {
  beforeEach(() => setBrandTheme(null));

  it("reads the colours, fonts, name and logo of a .pptx", async () => {
    const { brand, notes } = await readTemplate(await makeTestTemplate(), "aster.pptx");
    expect(brand).toMatchObject({ name: "Aster Brand", background: TEST_TEMPLATE.background, text: TEST_TEMPLATE.text, accent: TEST_TEMPLATE.accent, accent2: TEST_TEMPLATE.accent2, headingFont: "Georgia", bodyFont: "Arial" });
    expect(brand.logo).toMatch(/^data:image\/png;base64,/);
    expect(notes).toEqual([]);
  });

  it("says so when the file is not a template or not 16:9, and keeps the accent readable", async () => {
    await expect(readTemplate(new Uint8Array([1, 2, 3]), "x.pptx")).rejects.toThrow("not a PowerPoint template");
    const zip = await JSZip.loadAsync(await makeTestTemplate());
    zip.file("ppt/presentation.xml", (await zip.file("ppt/presentation.xml")!.async("string")).replace(/<p:sldSz[^>]*\/>/, '<p:sldSz cx="9144000" cy="6858000"/>'));
    const { notes } = await readTemplate(await zip.generateAsync({ type: "uint8array" }));
    expect(notes.join(" ")).toContain("not 16:9");
    expect(readable("#ffe08a", "#ffffff")).toMatchObject({ changed: true });
  });

  it("is kept once, and a deck that asks for it without one is paper", async () => {
    const { brand } = await readTemplate(await makeTestTemplate(), "aster.pptx");
    expect(resolveTheme("brand", null).theme).toBe("paper");
    setBrandTheme(brand);
    expect(getBrandTheme()?.name).toBe("Aster Brand");
    expect(resolveTheme("brand", getBrandTheme()).tokens.bg).toBe("#fff8ee");
    setBrandTheme(null);
    expect(getBrandTheme()).toBeNull();
  });

  it("draws a brand deck with the logo and its colours, and falls back to paper without one", async () => {
    const { brand } = await readTemplate(await makeTestTemplate(), "aster.pptx");
    const body = "## A claim that is long enough\n- **a**: b\n- **c**: d";
    const without = render(<DeckSlide body={body} layout="summary" theme="brand" numbers={[]} />);
    expect(without.container.querySelector(".dk-logo")).toBeNull();
    expect((without.container.querySelector(".dk-face") as HTMLElement).style.getPropertyValue("--dk-bg")).toBe("#ffffff");
    without.unmount();
    setBrandTheme(brand);
    const withBrand = render(<DeckSlide body={body} layout="summary" theme="brand" numbers={[]} />);
    expect(withBrand.container.querySelector(".dk-logo")).not.toBeNull();
    expect((withBrand.container.querySelector(".dk-face") as HTMLElement).style.getPropertyValue("--dk-accent")).toBe("#a8432d");
  });

  it("exports a brand deck with its fonts, colours and a logo on every slide", async () => {
    const { brand } = await readTemplate(await makeTestTemplate(), "aster.pptx");
    const content = withDeckMarker(fixture("deck.short"), { theme: "brand" });
    const zip = await JSZip.loadAsync(await pptxBytes(content, { sources: ["A"], brand }));
    const slide3 = await zip.file("ppt/slides/slide3.xml")!.async("string");
    expect(slide3).toContain('typeface="Arial"');
    expect(slide3).toContain("1B2A41");
    expect(slide3).toContain("<p:pic>");
    const plain = await JSZip.loadAsync(await pptxBytes(content, { sources: ["A"] }));
    expect(await plain.file("ppt/slides/slide3.xml")!.async("string")).not.toContain("<p:pic>");
  });
});

import { joinNumbers, numberPieces } from "./inlineTokens";

describe("figures in running text", () => {
  it("keeps a number with its unit, sign and date together", () => {
    const keep = (text: string) => numberPieces(text).filter((p) => p.keep).map((p) => p.text);
    expect(keep("新增债务11.86万亿元，增长5.0%，+38.7%，9月26日")).toEqual(["11.86万亿元", "5.0%", "+38.7%", "9月26日"]);
    expect(keep("a 2.1 °C drop")).toEqual(["2.1 °C"]);
    expect(joinNumbers("5.0%增长")).toBe("5⁠.⁠0⁠%增长");
  });
});


describe("scientific source figures", () => {
  it("renders image, controls, axes and limitations on the slide and exports them", async () => {
    const body = "## Controlled ablation\n![Panels A and B](gunther-asset:ast_synthetic)\nFeedback reduces drift [1]\n- A: fixed vs adaptive; y is drift in mV [1]\n- B: feedback on vs off [2]\n- Simulated conditions only [2]";
    const view = render(<DeckSlide body={body} layout="figure" theme="paper" numbers={[]} />);
    expect(view.container.querySelector("img")?.getAttribute("src")).toContain("ast_synthetic");
    expect(view.container.querySelector(".dk-figure-explanation")?.textContent).toContain("y is drift in mV");
    expect(view.container.textContent).toContain("Simulated conditions only");
    const content = "<!-- layout: title; design: v2; theme: paper -->\n# A synthetic study\nFeedback under controlled noise\n\n---\n<!-- layout: figure -->\n" + body;
    const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg==";
    const bytes = await pptxBytes(content, { resolveImage: async (id) => { expect(id).toBe("ast_synthetic"); return png; } });
    const zip = await JSZip.loadAsync(bytes);
    const slide = await zip.file("ppt/slides/slide2.xml")!.async("string");
    expect(slide).toContain("y is drift in mV");
    expect(slide).toContain("feedback on vs off");
    expect(slide).toContain("Simulated conditions only");
    expect(Object.keys(zip.files).some((name) => name.startsWith("ppt/media/"))).toBe(true);
  });
});

describe("text beside a source figure", () => {
  const deck = (lines: string[]) => "<!-- layout: title; design: v2; theme: paper -->\n# A synthetic study\nSubtitle\n\n---\n<!-- layout: figure -->\n## Controlled ablation\n![Panels](gunther-asset:ast_synthetic)\n" + lines.join("\n");
  const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg==";
  const sizeOf = async (content: string, word: string) => {
    const zip = await JSZip.loadAsync(await pptxBytes(content, { resolveImage: async () => png }));
    const xml = await zip.file("ppt/slides/slide2.xml")!.async("string");
    const run = xml.split("<a:r>").find((part) => part.includes(word))!;
    return Number(/sz="(\d+)"/.exec(run)![1]);
  };

  it("keeps a too-long column inside the slide with smaller type in the export", async () => {
    const short = await sizeOf(deck(["Feedback reduces drift [1]", "- Marker: fixed vs adaptive [1]"]), "Marker");
    const long = "- Marker " + "explains the comparison and the control in many words ".repeat(5) + "[1]";
    const crowded = await sizeOf(deck(["Feedback reduces drift and the ablation confirms the mechanism under all tested conditions [1]", long, long, long]), "Marker");
    expect(crowded).toBeLessThan(short);
  });

  it("budgets the column the same way the backend does", () => {
    const issues = budgetIssues(parseDeckSlide("figure", "## Controlled ablation\n![Panels](gunther-asset:ast_x)\n" + "a ".repeat(60) + "\n- " + "b".repeat(85) + "\n- " + "c".repeat(95)), true);
    expect(issues.some((issue) => issue.startsWith("explanation has 95 units"))).toBe(true);
    expect(issues.some((issue) => issue.startsWith("text beside the figure has 299 units"))).toBe(true);
  });
});
