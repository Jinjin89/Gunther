import { render } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import { restoreUnchangedScenes, parseSlides, splitDesign, withDesignBlock, withoutSceneMarkers } from "../sections";
import { SlideFace } from "../SlideFace";
import { DeckSlide } from "./DeckSlide";
import { DesignedSlide } from "./DesignedSlide";
import { designCss } from "./designStyles";
import { fixture } from "./fixtures";
import { withDeckMarker } from "./marker";
import { parseDeck } from "./parseDeck";
import { safeStyle, sanitizeDesign } from "./sanitizeDesign";
import type { BrandTheme } from "./brand";

const SOURCES = ["Harlow 2024 · p. 3", "Okafor 2025", "Lindqvist 2023", "City data 2025", "Survey 2024", "Review 2019", "Plan 2026", "Interviews 2025", "Memo 2025", "A", "B", "C"];
const NUMBERS = SOURCES.map((_, at) => at + 1);
const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const shadow = (container: HTMLElement) => container.querySelector<HTMLElement>(".dk-designed")!.shadowRoot!;

describe("a designed deck is read", () => {
  const deck = parseDeck(fixture("deck.designed"));

  it("with its style and one design per slide, never in the text or the notes", () => {
    expect(deck.style).toBe("light");
    expect(deck.version).toBe(2);
    expect(deck.slides).toHaveLength(10);
    for (const slide of deck.slides) {
      expect(slide.design, slide.body).toMatch(/^<section class="slide/);
      for (const text of [slide.body, slide.notes]) {
        expect(text).not.toContain(":::");
        expect(text).not.toContain("<section");
      }
    }
    expect(deck.slides[1]!.notes).toMatch(/^Four findings carry this briefing/);
    expect(deck.slides[1]!.data.layout).toBe("summary");
  });

  it("with the same words as the Markdown without the designs", () => {
    const plain = parseDeck(fixture("deck.designed").replace(/\n:::design\n[\s\S]*?\n:::(?=\n\n---|\n*$)/g, ""));
    expect(plain.slides.map((slide) => slide.design)).toEqual(plain.slides.map(() => null));
    expect(plain.slides.map((slide) => [slide.body, slide.notes])).toEqual(deck.slides.map((slide) => [slide.body, slide.notes]));
  });

  it("keeps a chart slide's table for the chart", () => {
    expect(deck.slides[2]!.data.layout).toBe("chart");
    expect(deck.slides[2]!.design).toContain("<gunther-chart></gunther-chart>");
  });

  it("reads the Chinese deck too, and old decks have no design or style", () => {
    const zh = parseDeck(fixture("deck.designed.zh"));
    expect(zh.style).toBe("light");
    expect(zh.slides.every((slide) => slide.design)).toBe(true);
    for (const name of ["deck.short", "deck.max", "deck.zh"] as const) {
      const old = parseDeck(fixture(name));
      expect(old.style).toBeNull();
      expect(old.slides.every((slide) => slide.design === null)).toBe(true);
    }
  });

  it("splits and joins a block by itself", () => {
    const slide = "<!-- layout: summary -->\n## A\n- b\nNote: n";
    const html = '<section class="slide">x [1]</section>';
    const joined = withDesignBlock(slide, html);
    expect(joined).toBe(`${slide}\n:::design\n${html}\n:::`);
    expect(splitDesign(joined)).toEqual({ text: slide, design: html });
    expect(withDesignBlock(joined, '<section class="slide">y</section>')).toContain(">y<");
    expect(withDesignBlock(joined, null)).toBe(slide);
    expect(parseSlides(joined)[0]).toMatchObject({ notes: "n", design: html });
  });
});

describe("the sanitiser", () => {
  it("removes scripts, event handlers, pictures, frames, styles and external addresses", () => {
    const dirty = `<section class="slide" onclick="x()" style="background:url(https://evil.example/a.png);color:red"><script>alert(1)</script>
      <img src="https://evil.example/a.png"><iframe src="https://evil.example"></iframe><link rel="stylesheet" href="https://evil.example/a.css"><style>@import "https://evil.example";</style>
      <a href="https://evil.example" onmouseover="x()">out</a><a href="javascript:alert(1)">js</a><a href="#note">in</a>
      <svg viewBox="0 0 10 10" onload="x()"><foreignObject><div>x</div></foreignObject><rect fill="url(https://evil.example/f)" width="5" height="5"/><circle fill="url(#grad)" r="2"/><text>kept</text></svg><p>words</p></section>`;
    const clean = sanitizeDesign(dirty);
    for (const bad of ["script", "onclick", "onload", "onmouseover", "<img", "iframe", "<link", "<style", "evil.example", "javascript", "foreignObject", "foreignobject"]) expect(clean.toLowerCase(), bad).not.toContain(bad.toLowerCase());
    expect(clean).toContain('href="#note"');
    expect(clean).toContain("color:red");
    expect(clean).toContain("url(#grad)");
    expect(clean).toContain("<text>kept</text>");
    expect(clean).toContain("<p>words</p>");
  });

  it("keeps the chart placeholder and drops its attributes", () => {
    expect(sanitizeDesign('<section class="slide"><gunther-chart onclick="x()"></gunther-chart></section>')).toBe('<section class="slide"><gunther-chart></gunther-chart></section>');
  });

  it("keeps only the safe declarations of a style attribute", () => {
    expect(safeStyle("color:red; background: url('https://x.example/a.png'); width:3px")).toBe("color:red; width:3px");
    expect(safeStyle("fill:url(#g)")).toBe("fill:url(#g)");
  });

  it("leaves nothing of a design that is only unsafe", () => {
    expect(sanitizeDesign("<script>alert(1)</script>")).toBe("");
  });
});

describe("a designed slide", () => {
  const html = '<section class="slide" style="padding:20px"><h2>Savings halve [1][6]</h2><p>A claim [3, 4] and one from memory [?].</p><svg viewBox="0 0 10 10"><text>[2]</text></svg><gunther-chart></gunther-chart></section>';
  const deck = parseDeck(fixture("deck.designed"));
  const chart = deck.slides[2]!.data.layout === "chart" ? deck.slides[2]!.data : null;

  it("is drawn in a shadow root with the base CSS and the deck's style", async () => {
    const { container } = render(<DesignedSlide html={sanitizeDesign(html)} style="bold" chart={null} numbers={NUMBERS} />);
    await flush();
    const root = shadow(container);
    expect(root.querySelector("h2")?.textContent).toContain("Savings halve");
    const css = root.querySelector("style")!.textContent!;
    expect(css).toContain("width: 1280px");
    expect(css).toContain("--dominant:#FF4F2E");
    expect(css).toContain("--head-weight:800");
    expect(css).not.toContain("--dominant:#1F2A44");
    expect(container.querySelector("h2")).toBeNull();
  });

  it("turns citation marks in the text into the template slides' marks, and opens the evidence on a click", async () => {
    const onCite = vi.fn();
    const { container } = render(<DesignedSlide html={sanitizeDesign(html)} style="light" chart={null} numbers={NUMBERS} sources={SOURCES} onCite={onCite} />);
    await flush();
    const root = shadow(container);
    const marks = [...root.querySelectorAll("sup.dk-cite")];
    expect(marks.map((mark) => mark.textContent)).toEqual(["1,6", "3,4", "?"]);
    expect(root.querySelector("h2")?.textContent).toBe("Savings halve1,6");
    expect(root.querySelector("svg text")?.textContent).toBe("[2]");
    expect(marks[2]!.classList.contains("is-unverified")).toBe(true);
    expect(root.querySelector<HTMLElement>('button[data-cite="5"]')?.title).toBe(SOURCES[5]);
    act(() => { root.querySelector<HTMLElement>('button[data-cite="2"]')!.click(); });
    expect(onCite).toHaveBeenCalledWith(2);
  });

  it("draws plain marks, not buttons, where nothing opens (print)", async () => {
    const { container } = render(<DesignedSlide html={sanitizeDesign(html)} style="light" chart={null} numbers={NUMBERS} />);
    await flush();
    expect(shadow(container).querySelectorAll("button")).toHaveLength(0);
    expect(shadow(container).querySelectorAll("sup.dk-cite")).toHaveLength(3);
  });

  it("draws the slide's chart table where the placeholder is, with the style's colours", async () => {
    expect(chart).not.toBeNull();
    const { container } = render(<DesignedSlide html={sanitizeDesign(html)} style="light" chart={chart} numbers={NUMBERS} />);
    await flush();
    const place = shadow(container).querySelector("gunther-chart")!;
    expect(place.querySelector("svg")).not.toBeNull();
    expect(place.textContent).toContain("18");
    expect(place.getAttribute("data-highlight")).toBe("true");
    expect(designCss("light", null)).toContain("gunther-chart");
  });

  it("lays the chart out inside the placeholder's own box, never larger", async () => {
    const size = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(640);
    const high = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(380);
    const { container } = render(<DesignedSlide html={sanitizeDesign('<section class="slide"><div style="width:640px;height:380px"><gunther-chart></gunther-chart></div></section>')} style="light" chart={chart} numbers={NUMBERS} />);
    await flush();
    const svg = shadow(container).querySelector("gunther-chart svg")!;
    expect(Number(svg.getAttribute("width"))).toBeLessThanOrEqual(640);
    expect(Number(svg.getAttribute("height"))).toBeLessThanOrEqual(380);
    const css = designCss("light", null);
    expect(css).toMatch(/gunther-chart \{[^}]*overflow: hidden/);
    expect(css).toMatch(/\.dk-plot svg \{[^}]*overflow: hidden/);
    size.mockRestore(); high.mockRestore();
  });

  it("draws every grouping of citations as one raised mark without brackets", async () => {
    for (const text of ["2028 [1][8][10]", "2028 [1, 8, 10]", "2028 [1,8,10]"]) {
      const { container, unmount } = render(<DesignedSlide html={sanitizeDesign(`<section class="slide"><p>${text}</p></section>`)} style="light" chart={null} numbers={NUMBERS} />);
      await flush();
      const p = shadow(container).querySelector("p")!;
      expect([...p.querySelectorAll("sup.dk-cite")].map((m) => m.textContent), text).toEqual(["1,8,10"]);
      expect(p.textContent, text).toBe("20281,8,10");
      unmount();
    }
  });

  it("draws nothing in the placeholder without a chart table", async () => {
    const { container } = render(<DesignedSlide html={sanitizeDesign(html)} style="light" chart={null} numbers={NUMBERS} />);
    await flush();
    expect(shadow(container).querySelector("gunther-chart")?.childElementCount).toBe(0);
  });

  it("keeps only the first placeholder", async () => {
    const { container } = render(<DesignedSlide html={sanitizeDesign(html.replace("</section>", "<gunther-chart></gunther-chart></section>"))} style="light" chart={null} numbers={NUMBERS} />);
    await flush();
    expect(shadow(container).querySelectorAll("gunther-chart")).toHaveLength(1);
  });

  it("takes the template's colours and fonts over the style's", async () => {
    const brand: BrandTheme = { name: "Acme", background: "#ffffff", text: "#202020", accent: "#0a64c8", accent2: "#0a64c8", headingFont: "Georgia", bodyFont: "Verdana", logo: null };
    const css = designCss("dark", brand);
    expect(css.lastIndexOf("--dominant:#202020")).toBeGreaterThan(css.indexOf("--dominant:#F2F4F7"));
    expect(css).toContain('--font-head:"Georgia", "Inter"');
    expect(css).toContain('--font-body:"Verdana", "Inter"');
    expect(css).toContain("--accent:#0a64c8");
  });
});

describe("DeckSlide and SlideFace", () => {
  const deck = parseDeck(fixture("deck.designed"));
  const props = (at: number) => ({ body: deck.slides[at]!.body, layout: deck.slides[at]!.layout, attrs: deck.slides[at]!.attrs, theme: deck.theme, first: at === 0, numbers: NUMBERS, sources: SOURCES });

  it("draws a slide from its design when the deck has a style, else by the template", async () => {
    const designed = render(<DeckSlide {...props(1)} design={deck.slides[1]!.design} style="light" />);
    await flush();
    expect(designed.container.querySelector(".dk-designed")).not.toBeNull();
    expect(designed.container.querySelector(".dk-face")).toBeNull();
    designed.unmount();
    const noStyle = render(<DeckSlide {...props(1)} design={deck.slides[1]!.design} />);
    await flush();
    expect(noStyle.container.querySelector(".dk-face")).not.toBeNull();
    noStyle.unmount();
    const noDesign = render(<DeckSlide {...props(1)} design={null} style="light" />);
    await flush();
    expect(noDesign.container.querySelector(".dk-face")).not.toBeNull();
    noDesign.unmount();
    const unsafe = render(<DeckSlide {...props(1)} design="<script>alert(1)</script>" style="light" />);
    await flush();
    expect(unsafe.container.querySelector(".dk-face")).not.toBeNull();
  });

  it("is the path SlideFace takes for a v2 deck's designed slide", async () => {
    const { container } = render(<SlideFace {...props(2)} title={false} designVersion={2} design={deck.slides[2]!.design} deckStyle="dark" />);
    await flush();
    const host = container.querySelector<HTMLElement>(".dk-designed")!;
    expect(host.dataset.designStyle).toBe("dark");
    expect(host.shadowRoot!.querySelector("gunther-chart svg")).not.toBeNull();
    expect(host.shadowRoot!.querySelector("style")!.textContent).toContain("--bg:#0E1116");
  });

  it("recolours when only the style changes", async () => {
    const view = render(<DeckSlide {...props(1)} design={deck.slides[1]!.design} style="light" />);
    await flush();
    expect(shadow(view.container).querySelector("style")!.textContent).toContain("--bg:#FFFFFF; --text:#1B1F24");
    view.rerender(<DeckSlide {...props(1)} design={deck.slides[1]!.design} style="bold" />);
    await flush();
    expect(shadow(view.container).querySelector("style")!.textContent).toContain("--head-weight:800");
    view.rerender(<DeckSlide {...props(1)} design={deck.slides[1]!.design} style="dark" />);
    await flush();
    expect(shadow(view.container).querySelector("style")!.textContent).toContain("--bg:#0E1116");
  });
});

describe("the deck's style in its marker", () => {
  it("is rewritten without touching the rest", () => {
    const content = fixture("deck.designed");
    const next = withDeckMarker(content, { style: "bold", theme: "paper" });
    expect(next.split("\n")[0]).toBe("<!-- layout: title; design: v2; theme: paper; style: bold -->");
    expect(next.slice(next.indexOf("\n"))).toBe(content.slice(content.indexOf("\n")));
    expect(parseDeck(next).style).toBe("bold");
    expect(withDeckMarker(content, { theme: "brand" }).split("\n")[0]).toContain("theme: brand; style: light");
    expect(parseDeck(withDeckMarker(content, { theme: "brand" })).theme).toBe("brand");
  });
});

describe("a hand edit", () => {
  const original = fixture("deck.designed");

  it("is typed over Markdown without the designs", () => {
    const editable = withoutSceneMarkers(original);
    expect(editable).not.toContain(":::design");
    expect(editable).not.toContain("<section");
    expect(editable).toContain("Note: Four findings");
  });

  it("keeps the designs of the slides whose words are unchanged and drops the design of an edited one", async () => {
    const editable = withoutSceneMarkers(original);
    expect(parseSlides(await restoreUnchangedScenes(original, editable)).map((slide) => slide.design)).toEqual(parseSlides(original).map((slide) => slide.design));
    const edited = editable.replace("The trial defends 2.1", "The trial defends two");
    expect(edited).not.toBe(editable);
    const slides = parseSlides(await restoreUnchangedScenes(original, edited));
    expect(slides[1]!.design).toBeNull();
    expect(slides[0]!.design).not.toBeNull();
    expect(slides.slice(2).every((slide) => slide.design)).toBe(true);
    expect(slides[1]!.body).toContain("two");
  });

  it("keeps a design when only the speaker notes change, and moves it with a slide that moved", async () => {
    const editable = withoutSceneMarkers(original);
    const notes = editable.replace("Four findings carry this briefing", "Three findings carry this briefing");
    expect(parseSlides(await restoreUnchangedScenes(original, notes))[1]!.design).not.toBeNull();
    const parts = editable.split("\n\n---\n\n");
    [parts[2], parts[3]] = [parts[3]!, parts[2]!];
    const moved = parseSlides(await restoreUnchangedScenes(original, parts.join("\n\n---\n\n")));
    const before = parseSlides(original);
    expect(moved[2]!.design).toBe(before[3]!.design);
    expect(moved[3]!.design).toBe(before[2]!.design);
  });
});
