import { describe, expect, it } from "vitest";
import { layoutOf } from "../sections";
import { budgetIssues, parseDeck, parseDeckSlide } from "./parseDeck";
import { fixture } from "./fixtures";
import { countUnits } from "./units";

const parse = (layout: string, body: string, attrs: Record<string, string> = {}) => parseDeckSlide(layout, body, attrs);

describe("units", () => {
  it("counts a Latin character as one unit and a CJK character as two", () => {
    expect(countUnits("abc de")).toBe(6);
    expect(countUnits("冷屋顶")).toBe(6);
    expect(countUnits("18% 节电")).toBe(8);
  });

  it("does not count citation marks or emphasis marks", () => {
    expect(countUnits("**Savings** halve [1][2]")).toBe(13);
    expect(countUnits("it holds [?]")).toBe(8);
  });
});

describe("a deck's marker", () => {
  it("says design v2 with its theme and accent", () => {
    const found = layoutOf("<!-- layout: title; design: v2; theme: editorial; accent: teal -->\n# Deck");
    expect(found).toMatchObject({ layout: "title", designVersion: 2, deckTheme: "editorial", accent: "teal" });
  });

  it("defaults to the paper theme, and an old deck is not v2", () => {
    expect(layoutOf("<!-- layout: title; design: v2 -->\n# Deck").deckTheme).toBe("paper");
    expect(layoutOf("<!-- layout: title; design: v1 -->\n# Deck").designVersion).toBe(1);
    expect(layoutOf("<!-- layout: title -->\n# Deck").designVersion).toBeUndefined();
  });

  it("keeps options such as a unit that is not a word", () => {
    const deck = parseDeck("<!-- layout: title; design: v2 -->\n# Deck\n\n---\n\n<!-- layout: chart; chart: column; unit: % -->\n## A claim here that is long enough\n| a | b |\n| --- | --- |\n| x | 1 |\n| y | 2 |");
    expect(deck.version).toBe(2);
    expect(deck.slides[1]!.attrs).toEqual({ chart: "column", unit: "%" });
    expect(deck.slides[1]!.data.layout).toBe("chart");
  });
});

describe("parsing each layout", () => {
  it("title: title, subtitle and meta", () => {
    expect(parse("title", "# Big title\nThe subtitle [1]\nMeta line")).toEqual({ layout: "title", title: "Big title", subtitle: "The subtitle [1]", meta: "Meta line" });
  });

  it("section: title and subtitle", () => {
    expect(parse("section", "# Part one\nWhat it covers")).toEqual({ layout: "section", title: "Part one", subtitle: "What it covers" });
  });

  it("summary: rows with a bold lead and a detail, citations kept", () => {
    const data = parse("summary", "## A claim\n- **Lead one**: detail one [1][2]\n- **Lead two**: detail two");
    expect(data).toMatchObject({ layout: "summary", title: "A claim", items: [{ lead: "Lead one", detail: "detail one [1][2]" }, { lead: "Lead two", detail: "detail two" }] });
  });

  it("stat: value, label and context", () => {
    expect(parse("stat", "## A claim\n**40 h → 16 h**\nMedian time [1]\nTwo teams [2]")).toMatchObject({ layout: "stat", value: "40 h → 16 h", label: "Median time [1]", context: "Two teams [2]" });
  });

  it("stats: values and labels, and the closing line directly under the list", () => {
    const data = parse("stats", "## A claim\n- **412** buildings [1]\n- **3** summers\nEach was matched [1]");
    expect(data).toMatchObject({ layout: "stats", items: [{ value: "412", label: "buildings [1]" }, { value: "3", label: "summers" }], after: "Each was matched [1]" });
  });

  it("chart: categories, series with units, the highlighted row and the closing line", () => {
    const data = parse("chart", "## A claim\n| Summer | Raw (%) | Adjusted (%) |\n| --- | --- | --- |\n| 2021 | 21 [1] | 10 |\n| **2022** | 17 | 9 |\nPlan with the second [2]", { chart: "column" });
    expect(data).toMatchObject({ layout: "chart", kind: "column", after: "Plan with the second [2]" });
    if (data.layout !== "chart") throw new Error("not a chart");
    expect(data.categories).toEqual([{ label: "2021", highlight: false }, { label: "2022", highlight: true }]);
    expect(data.series.map((s) => [s.name, s.unit, s.values.map((v) => v.label)])).toEqual([["Raw", "%", ["21%", "17%"]], ["Adjusted", "%", ["10%", "9%"]]]);
  });

  it("table: header, rows, the bold row as highlighted", () => {
    const data = parse("table", "## A claim\n| | A | B |\n| --- | :-: | ---: |\n| Up | 1 [1] | 2 |\n| **Total** | **3** | **4** |\nSo A [1]");
    expect(data).toMatchObject({ layout: "table", head: ["", "A", "B"], align: [null, "center", "right"], after: "So A [1]" });
    if (data.layout !== "table") throw new Error("not a table");
    expect(data.rows.map((r) => r.highlight)).toEqual([false, true]);
  });

  it("compare: two sides and a verdict", () => {
    const data = parse("compare", "## A claim\n### Left\n- one [1]\n- two\n### Right\n- three\nPick right [2]");
    expect(data).toEqual({ layout: "compare", title: "A claim", sides: [{ name: "Left", items: ["one [1]", "two"] }, { name: "Right", items: ["three"] }], after: "Pick right [2]" });
  });

  it("process and actions: ordered items with a bold step", () => {
    expect(parse("process", "## A claim\n1. **Survey**: roofs [5]\n2. **Coat**: twice")).toMatchObject({ layout: "process", items: [{ lead: "Survey", detail: "roofs [5]" }, { lead: "Coat", detail: "twice" }] });
    expect(parse("actions", "## A claim\n1. **Approve** the plan [7]\n2. **Budget** it")).toMatchObject({ layout: "actions", items: [{ lead: "Approve", detail: "the plan [7]" }, { lead: "Budget", detail: "it" }] });
  });

  it("timeline: when and event", () => {
    expect(parse("timeline", "## A claim\n- **2019** Heatwave [6]\n- **2021** Trial starts")).toMatchObject({ layout: "timeline", items: [{ when: "2019", event: "Heatwave [6]" }, { when: "2021", event: "Trial starts" }] });
  });

  it("quote: the quotation and who said it", () => {
    expect(parse("quote", "## A claim\n> We stopped getting calls.\n— Building manager [8]")).toMatchObject({ layout: "quote", quote: "We stopped getting calls.", attribution: "Building manager [8]" });
    expect(parse("quote", "## A claim\n> Said this\n> — A person")).toMatchObject({ quote: "Said this", attribution: "A person" });
  });

  it("figure: the kept figure by its asset id, and the takeaway", () => {
    expect(parse("figure", "## A claim\n![Figure 3: Peak](gunther-asset:ast_3f9a1c)\nThe gap opens [1]")).toEqual({ layout: "figure", title: "A claim", alt: "Figure 3: Peak", assetId: "ast_3f9a1c", takeaway: "The gap opens [1]" });
  });

  it("statement: the sentence and up to three items", () => {
    expect(parse("statement", "## A claim\nCoatings age [3]\n- one\n- two")).toMatchObject({ layout: "statement", statement: "Coatings age [3]", items: ["one", "two"] });
  });

  it("points: items with an optional lead", () => {
    expect(parse("points", "## A claim\n- **No data**: only power [1]\n- plain point")).toMatchObject({ layout: "points", items: [{ lead: "No data", detail: "only power [1]" }, { lead: null, detail: "plain point" }] });
  });
});

describe("a body that does not fit its layout", () => {
  it("keeps scientific explanation beside the original figure", () => {
    const data = parse("figure", "## Controlled ablation\n![Panels A and B](gunther-asset:ast_synthetic)\nFeedback reduces drift [1]\n- A: fixed vs adaptive; y is drift in mV [1]\n- B: feedback on vs off [2]\n- Simulated conditions only [2]");
    expect(data).toMatchObject({ layout: "figure", assetId: "ast_synthetic", takeaway: "Feedback reduces drift [1]", explanation: ["A: fixed vs adaptive; y is drift in mV [1]", "B: feedback on vs off [2]", "Simulated conditions only [2]"] });
    expect(budgetIssues(data)).toEqual([]);
  });

  it("a chart with a value that is not a number becomes a table", () => {
    const data = parse("chart", "## A claim\n| Name | Score |\n| --- | --- |\n| a | high |\n| b | low |");
    expect(data.layout).toBe("table");
  });

  it("a chart with a single row becomes a table", () => {
    expect(parse("chart", "## A claim\n| a | b |\n| --- | --- |\n| x | 1 |").layout).toBe("table");
  });

  it("anything else becomes text with every word kept", () => {
    const data = parse("compare", "## A claim\nJust a paragraph [1] with **bold**.\n\n- and a list");
    expect(data).toEqual({ layout: "text", title: "A claim", markdown: "Just a paragraph [1] with **bold**.\n\n- and a list", from: "compare" });
    expect(parse("stat", "## A claim\nno bold number").layout).toBe("text");
    expect(parse("quote", "## A claim\nno quote").layout).toBe("text");
    expect(parse("figure", "## A claim\nno image").layout).toBe("text");
    expect(parse("summary", "## A claim\nno list").layout).toBe("text");
  });

  it("an unknown or missing layout is text", () => {
    expect(parse("big_number", "## A claim\n**62%**").layout).toBe("text");
    expect(parseDeckSlide(null, "## A claim\nwords", {}).layout).toBe("text");
  });
});

describe("the fixtures follow the format", () => {
  it.each(["deck.short", "deck.max", "deck.zh"] as const)("%s has no slide over budget and none that degraded", (name) => {
    const deck = parseDeck(fixture(name));
    expect(deck.version).toBe(2);
    expect(deck.slides.length).toBe(17);
    for (const [at, slide] of deck.slides.entries()) {
      expect(slide.data.layout, `slide ${at + 1}`).not.toBe("text");
      expect(budgetIssues(slide.data), `slide ${at + 1} (${slide.data.layout})`).toEqual([]);
      if (at > 1) expect(slide.notes.length, `notes of slide ${at + 1}`).toBeGreaterThan(0);
    }
  });

  it("budgets count the Chinese deck by units", () => {
    const deck = parseDeck(fixture("deck.zh"));
    const title = (deck.slides[2]!.data as { title: string }).title;
    expect(countUnits(title)).toBeGreaterThan(title.length);
  });

  it("flags text over its budget", () => {
    const long = "x".repeat(101);
    expect(budgetIssues(parse("summary", `## ${long}\n- **a**: b\n- **c**: d`))).toEqual([expect.stringContaining("title has 101 units")]);
    expect(budgetIssues(parse("points", "## A claim that is long enough\n- **a**: b"))).toEqual([expect.stringContaining("items: 1")]);
  });
});

describe("a chart table's bold marks", () => {
  it("point at the highlighted row and are not drawn", () => {
    const data = parseDeckSlide("chart", "## T\n| Item | Share (%) |\n| --- | --- |\n| A | 2.2 |\n| B | **1.5** [3] |\n| C | 1.3 |", { chart: "column" });
    if (data.layout !== "chart") throw new Error("not a chart");
    expect(data.categories.map((c) => c.highlight)).toEqual([false, true, false]);
    expect(data.series[0]!.values.map((v) => v.label)).toEqual(["2.2%", "1.5%", "1.3%"]);
    expect(JSON.stringify(data)).not.toContain("**");
  });
});

it("preserves normalized crop provenance and the uncropped figure shape", () => {
  const cropped = parse("figure_detail", "## Controlled panel comparison\n![Panel B](gunther-asset:ast_panels#x=0.25,y=0.1,w=0.5,h=0.8)\nObservation\n- Setup\n- Control\n- Interpretation");
  expect(cropped).toMatchObject({ layout: "figure_detail", assetId: "ast_panels", crop: { x: 0.25, y: 0.1, w: 0.5, h: 0.8 } });
  expect(parse("figure", "## Original figure\n![Panels](gunther-asset:ast_panels)")).not.toHaveProperty("crop");
});

it("checks detailed figure counts and text budgets equally for talk and read", () => {
  const body = "## Controlled panel comparison\n![Panel B](gunther-asset:ast_panels)\n" + "x".repeat(80) + "\n" + Array.from({ length: 6 }, () => "- " + "y".repeat(90)).join("\n");
  const parsed = parse("figure_detail", body);
  expect(budgetIssues(parsed)).toEqual([]);
  expect(budgetIssues(parsed, true)).toEqual([]);
  expect(budgetIssues(parse("figure_detail", body + "z"))).toContain("text beside the figure has 621 units (allowed 0-620)");
  expect(budgetIssues(parse("figure_detail", body + "\n- Extra"))).toContain("explanations: 7 (allowed 3-6)");
  expect(budgetIssues(parse("figure_detail", body.replace("y".repeat(90), "y".repeat(126))))).toContain("explanation has 126 units (allowed 0-125)");
});
