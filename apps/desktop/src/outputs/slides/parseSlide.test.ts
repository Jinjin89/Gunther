import { describe, expect, it } from "vitest";
import { type ChartShape, parseSlide, plainText, SLIDE_LAYOUTS, type TableShape, unitOf, units, valueOf } from "./parseSlide";

const TABLE = "| Item | Share (%) |\n| --- | --- |\n";

// The form each layout asks for: [layout, the slide, whether it takes the form]. The same list as
// SHAPES in tests/test_skillbook.py, which tests `layout_shape`, the check the service makes while a
// deck is written. A fourth item says how the app draws a slide the service accepts but that it will
// not draw as its layout.
const SHAPES: Array<[string, string, boolean, string?]> = [
  ["title", "# Deck\n\nOne line saying it", true],
  // A title without its message line is still the title.
  ["title", "# Deck", true],
  ["title", "## Deck\n\nOne line", false],
  ["big_number", "## Labour\n**62%**\nOf the three-year cost [3]", true],
  ["big_number", "## Labour\n**62%**", false],
  ["big_number", "## Labour\n**62%** of the cost [3]\nMore", false],
  ["big_number", "## Labour\n**" + "word ".repeat(20) + "**\nMore", false],
  ["compare", "## C\n| | A | B |\n| --- | --- | --- |\n| x | 1 [1] | 2 [2] |\nA wins.", true],
  ["two_column", "## C\n| Usual | Better |\n| :--- | ---: |\n| a [1] | b [1] |", true],
  ["compare", "## C\n- a [1]\n- b [1]", false],
  ["compare", "## C\n| a | b |\n| x | y |", false],
  ["compare", "## C\n| a | b |\n| --- | --- |", false],
  ["bar_chart", "## C\n" + TABLE + "| Staff | 62 [3] |\n| Gear | 18 [3] |\nStaff leads.", true],
  // The service only asks for a number; but 1,200 and 3.5 万 (35,000) are not on one scale, so the app draws the table.
  ["line_chart", "## C\n| Year | Users |\n| --- | --- |\n| 2019 | 1,200 [2] |\n| 2020 | 3.5 万 [2] |", true, "compare"],
  ["bar_chart", "## C\n" + TABLE + "| Staff | 62 [3] |", false],
  ["bar_chart", "## C\n" + TABLE + Array.from({ length: 7 }, (_, n) => `| r${n} | ${n} [1] |\n`).join(""), false],
  ["bar_chart", "## C\n" + TABLE + "| Staff | high [3] |\n| Gear | 18 [3] |", false],
  ["bar_chart", "## C\n" + TABLE + "| Staff | 12–15 [3] |\n| Gear | 18 [3] |", false],
  ["bar_chart", "## C\n- Staff 62 [3]\n- Gear 18 [3]", false],
  ["flow", "## F\n1. Clean [1]\n2. Label [2]", true],
  ["flow", "## F\n1. Clean [1]", false],
  ["flow", "## F\n- Clean [1]\n- Label [2]", false],
  ["timeline", "## T\n- **2016** First [1]\n- **2020** Second [2]", true],
  ["timeline", "## T\n- 2016 First [1]\n- **2020** Second [2]", false],
  ["timeline", "## T\n" + Array.from({ length: 8 }, (_, n) => `- **${2000 + n}** x [1]\n`).join(""), false],
  ["cards", "## K\n- **A**: one [1]\n- **B**: two [2]", true],
  ["cards", "## K\n- **A**: one [1]", false],
  ["quote", "## Q\n> The words.\n— Someone, 2023 [6]", true],
  ["quote", "## Q\n> The words.", false],
  ["quote", "## Q\n— Someone, 2023 [6]\n> The words.", false],
  ["figure", "## G\n![Figure 2](gunther-asset:ast_3f9a1c)\nLook at week four [4]", true],
  ["figure", "## G\n![Figure 2](gunther-asset:ast_3f9a1c)", false],
  ["figure", "## G\n![Figure 2](gunther-asset:ast_3f9a1c) [1].\nLook at it", false],
  ["figure", "## G\n![Figure 2](https://example.org/a.png)\nLook at week four [4]", false],
  ["figure", "## G\n![One](gunther-asset:ast_1)\nLook ![Two](gunther-asset:ast_2)", false],
  ["text", "## T\nAnything at all", true],
  ["takeaways", "## T", true],
  ["", "## T\nA slide with no layout", true],
];

describe("a slide that takes the form its layout asks for", () => {
  it.each(SHAPES)("%j: %j fits %s", (layout, text, fits, instead) => {
    const shape = parseSlide(layout, text);
    // A slide with no layout has nothing to be drawn as: it is plain text, as before layouts.
    if (layout === "") expect(shape).toBeNull();
    // One that fits is drawn as its layout. One that does not is drawn as plain text, or, when it
    // is a table that cannot be a chart, as the table it is.
    else if (instead) expect(shape?.layout).toBe(instead);
    else if (fits) expect(shape?.layout).toBe(layout);
    else expect(shape === null || shape.layout === "compare").toBe(true);
    if (!fits && shape) expect(layout.endsWith("_chart")).toBe(true);
  });

  it("is plain text for a layout that is none of the thirteen", () => {
    expect(SLIDE_LAYOUTS).toHaveLength(13);
    for (const layout of [null, undefined, "", "hero", "Text", "bar chart"]) expect(parseSlide(layout, "## T\n\nwords")).toBeNull();
  });
});

describe("what each layout holds, as the slide wrote it", () => {
  it("keeps the markup of every field, so a citation stays a citation", () => {
    const shape = parseSlide("compare", "## **A** is cheaper [1]\n| | *A* | `B` |\n| :-- | --: | :-: |\n| **Upfront** [2] | 1.2m [2] | a \\| b |\nA costs more at first [3].");
    expect(shape).toEqual({
      layout: "compare",
      title: "**A** is cheaper [1]",
      lead: null,
      head: ["", "*A*", "`B`"],
      rows: [["**Upfront** [2]", "1.2m [2]", "a \\| b"]],
      align: ["left", "right", "center"],
      after: "A costs more at first [3].",
    });
  });

  it("reads a title, with or without its message", () => {
    expect(parseSlide("title", "# Cool roofs [1]\n\nWhat three trials found\n\nA second line")).toEqual({
      layout: "title", title: "Cool roofs [1]", message: "What three trials found\n\nA second line",
    });
    expect(parseSlide("title", "# Deck")).toEqual({ layout: "title", title: "Deck", message: null });
  });

  it("reads a big number: the bold line, and what is said about it", () => {
    expect(parseSlide("big_number", "## Labour is the biggest cost\n**62%**\nShare of the three-year cost, far above the 18% for equipment [3]")).toEqual({
      layout: "big_number",
      title: "Labour is the biggest cost",
      figure: "62%",
      caption: "Share of the three-year cost, far above the 18% for equipment [3]",
    });
    // The caption may be a paragraph of its own.
    expect(parseSlide("big_number", "## H\n**3 months**\n\nTo break even [2]")).toMatchObject({ figure: "3 months", caption: "To break even [2]" });
    // The number comes first.
    expect(parseSlide("big_number", "## H\nBefore it\n**62%**\nAfter")).toBeNull();
  });

  it("keeps words around a table: those before it and the sentence under it", () => {
    const shape = parseSlide("two_column", "## H\nBoth sides, same questions\n\n| Usual | Better |\n| --- | --- |\n| a [1] | b [1] |\n\nThe second side wins [2].") as TableShape;
    expect(shape).toMatchObject({ layout: "two_column", lead: "Both sides, same questions", after: "The second side wins [2].", head: ["Usual", "Better"], rows: [["a [1]", "b [1]"]] });
  });

  it("does not take the sentence under a table for one more row", () => {
    // GFM does. The service does not (a row has a pipe), and neither does the slide.
    const shape = parseSlide("compare", "## H\n| a | b |\n| --- | --- |\n| 1 | 2 |\nThe words under it.") as TableShape;
    expect(shape.rows).toEqual([["1", "2"]]);
    expect(shape.after).toBe("The words under it.");
  });

  it("draws a two-column table of other than two columns as the grid it is, and pads short rows", () => {
    expect(parseSlide("two_column", "## H\n| | a | b |\n| --- | --- | --- |\n| x | 1 | 2 |")).toMatchObject({ layout: "compare" });
    expect(parseSlide("compare", "## H\n| a | b | c |\n| --- | --- | --- |\n| 1 |\n| 2 | 3 |")).toMatchObject({ rows: [["1", "", ""], ["2", "3", ""]] });
    // A cell beyond the header's columns would be lost, so it is not a table to draw.
    expect(parseSlide("compare", "## H\n| a | b |\n| --- | --- |\n| 1 | 2 | 3 |")).toBeNull();
  });

  it("reads a flow, a timeline and cards from their lists", () => {
    expect(parseSlide("flow", "## Clean first\n1. Deduplicate [2]\n2. Pre-label [3]\n3. Review the disputed ones [3]")).toMatchObject({
      layout: "flow", title: "Clean first", steps: ["Deduplicate [2]", "Pre-label [3]", "Review the disputed ones [3]"],
    });
    expect(parseSlide("timeline", "## Growth came from abroad\n- **2016** First product [1]\n- **2020**：Entered Europe [4]\n- **2023** - Six in ten dollars [6]")).toMatchObject({
      layout: "timeline",
      events: [{ when: "2016", what: "First product [1]" }, { when: "2020", what: "Entered Europe [4]" }, { when: "2023", what: "Six in ten dollars [6]" }],
    });
    expect(parseSlide("cards", "## Three sources of error\n- **Capture**: brightness differs [2]\n- **Labelling**：borderline cases [4]")).toMatchObject({
      layout: "cards", cards: [{ name: "Capture", text: "brightness differs [2]" }, { name: "Labelling", text: "borderline cases [4]" }],
    });
    // A bold start with nothing after it is a card with a name.
    expect(parseSlide("cards", "## H\n- **A**\n- **B**: two")).toMatchObject({ cards: [{ name: "A", text: "" }, { name: "B", text: "two" }] });
    // "__" is bold in Markdown, but the service looks for "**".
    expect(parseSlide("cards", "## H\n- __A__: one\n- **B**: two")).toBeNull();
  });

  it("keeps a sentence under a list", () => {
    expect(parseSlide("flow", "## H\n1. a\n2. b\n\nThen it repeats.")).toMatchObject({ steps: ["a", "b"], after: "Then it repeats." });
  });

  it("reads a quote, however its lines are marked, and who said it", () => {
    expect(parseSlide("quote", "## Regulators put explanation first\n> A model that cannot say why should not diagnose alone.\n— Guidance, 2023 [6]")).toEqual({
      layout: "quote",
      title: "Regulators put explanation first",
      lead: null,
      quote: "A model that cannot say why should not diagnose alone.",
      by: "— Guidance, 2023 [6]",
      after: null,
    });
    expect(parseSlide("quote", "## H\n> First line\n> second line\n\n— Someone [6]\n\nWhy it matters.")).toMatchObject({
      quote: "First line\nsecond line", by: "— Someone [6]", after: "Why it matters.",
    });
    // The marker needs its space, as in the service.
    expect(parseSlide("quote", "## H\n>No space\n— Someone")).toBeNull();
  });

  it("reads a figure: the asset it names, its caption, and the line about it", () => {
    expect(parseSlide("figure", "## The groups part after week four\n![Figure 2: glucose by group](gunther-asset:ast_3f9a1c)\nLook at the gap after week four; the paper says it opens there [4]")).toEqual({
      layout: "figure",
      title: "The groups part after week four",
      assetId: "ast_3f9a1c",
      alt: "Figure 2: glucose by group",
      line: "Look at the gap after week four; the paper says it opens there [4]",
    });
    // The image may be on a line of its own, with the sentence in the next paragraph.
    expect(parseSlide("figure", "## H\n![Fig](gunther-asset:a_1)\n\nLook here [1]")).toMatchObject({ assetId: "a_1", line: "Look here [1]" });
  });

  it("takes anything for text, and lists for takeaways", () => {
    expect(parseSlide("text", "## Accuracy is not enough\n\n- Different patients [3]\n- Rare cases [4]\n\nSo pilot first.")).toEqual({
      layout: "text", title: "Accuracy is not enough", body: "- Different patients [3]\n- Rare cases [4]\n\nSo pilot first.",
    });
    expect(parseSlide("text", "Just words, no heading")).toEqual({ layout: "text", title: null, body: "Just words, no heading" });
    expect(parseSlide("takeaways", "## Start with screening\n- Good enough for screening [2]\n- Next: a six-month pilot")).toEqual({
      layout: "takeaways", title: "Start with screening", lead: null, after: null, items: ["Good enough for screening [2]", "Next: a six-month pilot"],
    });
    expect(parseSlide("takeaways", "## Start with screening\n\nOnly a sentence.")).toEqual({
      layout: "takeaways", title: "Start with screening", lead: null, after: "Only a sentence.", items: [],
    });
  });
});

describe("a chart", () => {
  const bars = (rows: string, head = "| Item | Share (%) |") => parseSlide("bar_chart", `## Labour leads\n${head}\n| --- | --- |\n${rows}\nLabour alone outweighs the rest [3].`);

  it("takes its numbers from the second column, its unit from the header, and its citations from the cells", () => {
    expect(bars("| **Labour** | 62 [3] |\n| Equipment | 18 [3] |\n| Other | 20 [3] |")).toEqual({
      layout: "bar_chart",
      title: "Labour leads",
      lead: null,
      after: "Labour alone outweighs the rest [3].",
      axis: "Item",
      measure: "Share (%)",
      points: [
        { label: "**Labour**", x: null, value: 62, text: "62%", marks: "[3]", cites: [3], highlight: true },
        { label: "Equipment", x: null, value: 18, text: "18%", marks: "[3]", cites: [3], highlight: false },
        { label: "Other", x: null, value: 20, text: "20%", marks: "[3]", cites: [3], highlight: false },
      ],
    });
  });

  it("points at no row when none is bold, or when all are", () => {
    const highlights = (shape: ReturnType<typeof bars>) => (shape as ChartShape).points.map((point) => point.highlight);
    expect(highlights(bars("| A | 1 |\n| B | 2 |"))).toEqual([false, false]);
    expect(highlights(bars("| **A** | 1 |\n| **B** | 2 |"))).toEqual([false, false]);
    expect(highlights(bars("| A | 1 |\n| **B** | 2 |"))).toEqual([false, true]);
    // Bold in part of a label is emphasis inside it, not a row to point at.
    expect(highlights(bars("| A **x** | 1 |\n| B | 2 |"))).toEqual([false, false]);
  });

  it("puts the header's unit on a number that has none, and leaves a number that has its own", () => {
    const text = (shape: ReturnType<typeof bars>) => (shape as ChartShape).points.map((point) => point.text);
    expect(text(bars("| A | 1.5 [1] |\n| B | 2 [1] |", "| Item | Users (万) |"))).toEqual(["1.5万", "2万"]);
    expect(text(bars("| A | 1.5 万 [1] |\n| B | 2 万 [1] |", "| Item | Users (万) |"))).toEqual(["1.5 万", "2 万"]);
    expect(text(bars("| A | 1.5 [1] |\n| B | 2 [1] |", "| Item | Latency (ms) |"))).toEqual(["1.5 ms", "2 ms"]);
    // A unit that says what the number counts in is not put after it.
    expect(text(bars("| A | 1.5 [1] |\n| B | 2 [1] |", "| Item | Revenue ($M) |"))).toEqual(["1.5", "2"]);
    expect(text(bars("| A | 1.5 [1] |\n| B | 2 [1] |", "| Item | Users (10k) |"))).toEqual(["1.5", "2"]);
    expect(text(bars("| A | 1.5 [1] |\n| B | 2 [1] |", "| Item | Users |"))).toEqual(["1.5", "2"]);
  });

  it("reads thousands, signs, decimals, and every kind of citation mark", () => {
    const shape = bars("| A | 1,200 [2] |\n| B | -5.2 [i:3][4] |\n| C | ≈7 [p:3] |\n| D | 8 [?] |\n| E | 9 [1, 2] |") as ChartShape;
    expect(shape.points.map((point) => [point.value, point.text, point.marks, point.cites])).toEqual([
      [1200, "1,200%", "[2]", [2]],
      [-5.2, "-5.2%", "[i:3][4]", [3, 4]],
      [7, "≈7%", "[p:3]", [3]],
      [8, "8%", "[?]", []],
      [9, "9%", "[1, 2]", [1, 2]],
    ]);
  });

  it("is drawn as the table it is when a value is not a number, or the rows do not count in one unit", () => {
    expect(bars("| A | high [1] |\n| B | 2 [1] |")).toMatchObject({ layout: "compare", head: ["Item", "Share (%)"], rows: [["A", "high [1]"], ["B", "2 [1]"]], after: "Labour alone outweighs the rest [3]." });
    expect(bars("| A | 12–15 [1] |\n| B | 2 [1] |")).toMatchObject({ layout: "compare" });
    expect(bars("| A | 12 [1] |\n| B | 3.5 万 [1] |")).toMatchObject({ layout: "compare" });
    expect(bars("| A | 12 [1] |")).toMatchObject({ layout: "compare" });
    expect(bars(Array.from({ length: 7 }, (_, n) => `| r${n} | ${n} |`).join("\n"))).toMatchObject({ layout: "compare" });
    // A table with one column cannot be a chart.
    expect(parseSlide("bar_chart", "## H\n| a |\n| --- |\n| 1 |\n| 2 |")).toMatchObject({ layout: "compare" });
  });

  it("puts a line chart's times in order when every label is a number, and leaves other labels as they come", () => {
    const years = parseSlide("line_chart", "## Slowed\n| Year | Users (万) |\n| --- | --- |\n| 2021 | 41 [5] |\n| 2019 | 12 [2] |\n| 2020 | 30 [2] |") as ChartShape;
    expect(years.points.map((point) => [point.x, point.label, point.text])).toEqual([[2019, "2019", "12万"], [2020, "2020", "30万"], [2021, "2021", "41万"]]);
    const named = parseSlide("line_chart", "## Slowed\n| Quarter | Users |\n| --- | --- |\n| Q2 | 41 |\n| Q1 | 12 |") as ChartShape;
    expect(named.points.map((point) => [point.x, point.label])).toEqual([[null, "Q2"], [null, "Q1"]]);
    // The same year twice is not a time axis.
    const twice = parseSlide("line_chart", "## H\n| Year | Users |\n| --- | --- |\n| 2020 | 1 |\n| 2020 | 2 |") as ChartShape;
    expect(twice.points.map((point) => point.x)).toEqual([null, null]);
  });
});

describe("the pieces", () => {
  it("counts how long a text reads as the service does", () => {
    expect(units("人工成本最高 [3]")).toBe(6);
    expect(units("Costs are **highest** [2][?]")).toBeCloseTo(3 * 1.7);
    expect(units("")).toBe(0);
  });

  it("reads a number with a unit, and refuses what is not one", () => {
    expect(valueOf("62 [3]")).toMatchObject({ number: 62, tail: "", text: "62", marks: "[3]", cites: [3] });
    expect(valueOf("**62**%")).toMatchObject({ number: 62, tail: "%", text: "**62**%" });
    expect(valueOf("$4.2M [1]")).toMatchObject({ number: 4.2, tail: "M" });
    expect(valueOf("1 234 567")).toMatchObject({ number: 1234567 });
    expect(valueOf("12,5")).toBeNull();
    expect(valueOf("high")).toBeNull();
    expect(valueOf("12–15")).toBeNull();
    expect(valueOf("")).toBeNull();
    expect(valueOf("3 apples and 4 pears")).toBeNull();
  });

  it("finds the unit in a header's last brackets, and the words without marks", () => {
    expect(unitOf("Share (%)")).toBe("%");
    expect(unitOf("用户数（万）")).toBe("万");
    expect(unitOf("Rate (per 1,000) (%)")).toBe("%");
    expect(unitOf("Users")).toBeNull();
    expect(unitOf("Users ()")).toBeNull();
    expect(plainText("**Labour** costs \\[sic] [3] `code`")).toBe("Labour costs [sic] code");
  });
});
