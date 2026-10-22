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
