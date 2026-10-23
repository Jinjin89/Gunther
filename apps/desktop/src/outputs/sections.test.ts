import type { Artifact, OutlineItem } from "@gunther/contracts";
import { describe, expect, it } from "vitest";
import { headingOf, joinDocument, layoutOf, parseSlides, restoreUnchangedScenes, sectionHash, slideLayouts, splitDocument, splitNotes, titleOf, withAccent, withDraft, withTheme, withoutSceneMarkers } from "./sections";

// The same cases as tests/test_outputs.py: the service and the app split a document alike.

// A slide's marker: [text, its layout, its accent, the slide without the marker]. The same list as
// MARKER_CASES in tests/test_outputs.py.
const MARKER_CASES: Array<[string, string | null, string | null, string]> = [
  ["<!-- layout: compare -->\n## A claim\n\n| a | b |", "compare", null, "## A claim\n\n| a | b |"],
  ["<!-- layout: title; accent: blue -->\n# Deck\n\nOne line", "title", "blue", "# Deck\n\nOne line"],
  ["## No marker\n\n- a", null, null, "## No marker\n\n- a"],
  ["<!--layout:big_number-->\n## Tight\n\n**62%**", "big_number", null, "## Tight\n\n**62%**"],
  ["\n\n  <!-- layout: text -->\n\n\n## Blank lines", "text", null, "## Blank lines"],
  ["<!-- layout: text -->\r\n## Windows", "text", null, "## Windows"],
  ["<!-- layout: text -->", "text", null, ""],
  // Only the title slide has an accent, and only one of the six.
  ["<!-- layout: title; accent: pink -->\n# Deck", "title", null, "# Deck"],
  ["<!-- layout: compare; accent: blue -->\n## A claim", "compare", null, "## A claim"],
  // Not a marker: not the first line, not the whole line, not a layout, not one line.
  ["## A\n\n<!-- layout: text -->\n- a", null, null, "## A\n\n<!-- layout: text -->\n- a"],
  ["<!-- layout: text --> ## A", null, null, "<!-- layout: text --> ## A"],
  ["<!-- note: layout -->\n## A", null, null, "<!-- note: layout -->\n## A"],
  ["<!-- layout: Text -->\n## A", null, null, "<!-- layout: Text -->\n## A"],
  ["<!--\nlayout: text -->\n## A", null, null, "<!--\nlayout: text -->\n## A"],
  ["```\n<!-- layout: text -->\n```", null, null, "```\n<!-- layout: text -->\n```"],
];

describe("a slide's marker", () => {
  it.each(MARKER_CASES)("is read from %j", (text, layout, accent, body) => {
    expect(layoutOf(text)).toEqual({ layout, accent, theme: null, composition: "standard", body });
  });

  it("stays with its slide when a deck is split and put back, and the hash covers it", async () => {
    const content = "<!-- layout: title; accent: blue -->\n# Deck\n\nsub\n\n---\n\n<!-- layout: flow -->\n## One\n\n1. a\n2. b";
    const { sections } = splitDocument(content, "slides");
    expect(sections).toEqual(["<!-- layout: title; accent: blue -->\n# Deck\n\nsub", "<!-- layout: flow -->\n## One\n\n1. a\n2. b"]);
    expect(joinDocument("", sections, "slides")).toBe(content);
    expect(await sectionHash(sections[1] ?? "")).not.toBe(await sectionHash("## One\n\n1. a\n2. b"));
    expect(await sectionHash(sections[1] ?? "")).toBe(await sectionHash("  <!-- layout: flow -->\r\n## One\r\n\r\n1. a\r\n2. b\n"));
    expect(titleOf(content)).toBe("Deck");
  });

  it("reads per-slide composition from unordered keyed metadata", () => {
    const deck = [
      "<!-- layout: title; theme: editorial -->\n# Title",
      "<!-- layout: big_number; composition: spotlight -->\n## Claim\n\n**62%**\n\nCompared with [1]",
      "<!-- layout: quote; composition: split -->\n## Quote\n\n> Words [1]\n\n— Source [1]",
    ].join("\n\n---\n\n");
    expect(parseSlides(deck).map((slide) => [slide.theme, slide.composition])).toEqual([
      ["editorial", "standard"], [null, "spotlight"], [null, "split"],
    ]);
    expect(layoutOf("<!-- layout: compare; composition: split -->\n## Table").composition).toBe("standard");
  });

  it("consumes malformed scene metadata at the header while leaving Markdown examples alone", () => {
    const parsed = layoutOf("<!-- layout: composed -->\n<!-- scene: malformed! -->\n## Heading\n\nText\n\n```md\n<!-- scene: example -->\n```");
    expect(parsed.scene?.issue).toContain("invalid encoding");
    expect(parsed.body).toContain("## Heading");
    expect(parsed.body).toContain("<!-- scene: example -->");
    expect(parsed.body).not.toContain("malformed!");
  });

  it("keeps the same-position tree when identical source pages have different compositions", async () => {
    const page = "<!-- layout: composed -->\n## Same\n\nOne\n\nTwo";
    const envelope = (firstBlock: "b1" | "b2") => btoa(JSON.stringify({
      version: 1,
      sourceHash: "a".repeat(64),
      root: {
        kind: "row",
        children: [firstBlock, firstBlock === "b1" ? "b2" : "b1"].map((blockId) => ({ kind: "leaf", blockId, view: "text", size: "medium", align: "start", role: "main" })),
        weights: [1, 1], gap: "normal", padding: "small", surface: "none", align: "stretch",
      },
    })).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
    const firstPayload = envelope("b1");
    const secondPayload = envelope("b2");
    const marked = (payload: string) => page.replace("<!-- layout: composed -->", `<!-- layout: composed -->\n<!-- scene: ${payload} -->`);
    const original = `${marked(firstPayload)}\n\n---\n\n${marked(secondPayload)}`;
    const editable = withoutSceneMarkers(original);
    const changed = editable.replace("One", "Changed one");
    expect(editable).not.toContain(firstPayload);
    const restored = await restoreUnchangedScenes(original, changed);
    const parsed = parseSlides(restored);
    expect(parsed[0]?.scene).toBeUndefined();
    expect(parsed[1]?.scene?.scene?.root.kind).toBe("row");
    expect(parsed[1]?.scene?.scene?.root.kind === "row" && parsed[1]?.scene?.scene?.root.children[0]?.kind === "leaf" ? parsed[1].scene.scene.root.children[0].blockId : null).toBe("b2");
  });
});

describe("a report's sections", () => {
  it("start at level-two headings outside code", () => {
    const content = "# Title\n\n_Brief_\n\n## One\n\ntext\n\n```md\n## not a section\n```\n\n### Sub\n\n## Two\n\nmore\n";
    expect(splitDocument(content, "report")).toEqual({
      preamble: "# Title\n\n_Brief_",
      sections: ["## One\n\ntext\n\n```md\n## not a section\n```\n\n### Sub", "## Two\n\nmore"],
    });
    expect(splitDocument("Just words, no headings.", "report")).toEqual({ preamble: "Just words, no headings.", sections: [] });
  });

  it("go back together as the service writes them", () => {
    const { preamble, sections } = splitDocument("# Title\n\n## One\n\ntext\n\n## Two\n\nmore", "report");
    expect(joinDocument(preamble, sections, "report")).toBe("# Title\n\n## One\n\ntext\n\n## Two\n\nmore");
  });
});

describe("a deck's slides", () => {
  it("are the parts between lines that are only three dashes", () => {
    const content = "# Deck\n\nsub\n\n---\n\n## One\n\n- a\n\nNote: n\n\n```\n---\n```\n\n   ---  \n\n## Two\n\n---\n\n---\n";
    expect(splitDocument(content, "slides")).toEqual({
      preamble: "",
      sections: ["# Deck\n\nsub", "## One\n\n- a\n\nNote: n\n\n```\n---\n```", "## Two"],
    });
    expect(joinDocument("", ["# Deck", "## One"], "slides")).toBe("# Deck\n\n---\n\n## One");
  });

  it("keep the presenter's notes apart from what is shown", () => {
    expect(splitNotes("## One\n\n- a\n- b\n\nNote: Say this first.\nThen this.")).toEqual({
      body: "## One\n\n- a\n- b",
      notes: "Say this first.\nThen this.",
    });
    expect(splitNotes("## Plain\n\n- a")).toEqual({ body: "## Plain\n\n- a", notes: "" });
    expect(splitNotes("## Code\n\n```\nNote: not notes\n```")).toEqual({ body: "## Code\n\n```\nNote: not notes\n```", notes: "" });
  });
});

describe("parsing a deck", () => {
  it("gives each slide its heading, how it is drawn, what it shows and what is said", () => {
    expect(parseSlides("# Deck\n\nsub\n\n---\n\n## One\n\n- a\n\nNote: Say a.")).toEqual([
      { heading: "Deck", layout: null, accent: null, theme: null, composition: "standard", design: null, body: "# Deck\n\nsub", notes: "" },
      { heading: "One", layout: null, accent: null, theme: null, composition: "standard", design: null, body: "## One\n\n- a", notes: "Say a." },
    ]);
    expect(parseSlides("")).toEqual([]);
  });

  it("takes the marker off a slide's body and keeps it out of the heading and the notes", () => {
    const content = "<!-- layout: title; accent: blue -->\n# Deck\n\nsub\n\n---\n\n<!-- layout: flow -->\n## One\n\n1. a\n2. b\n\nNote: Say a.";
    expect(parseSlides(content)).toEqual([
      { heading: "Deck", layout: "title", accent: "blue", theme: null, composition: "standard", design: null, body: "# Deck\n\nsub", notes: "" },
      { heading: "One", layout: "flow", accent: null, theme: null, composition: "standard", design: null, body: "## One\n\n1. a\n2. b", notes: "Say a." },
    ]);
  });
});

describe("a section's hash", () => {
  it("ignores line ends and outer space, and nothing else", async () => {
    expect(await sectionHash("a\r\nb\n")).toBe(await sectionHash("  a\nb"));
    expect(await sectionHash("a\nb")).not.toBe(await sectionHash("a\n\nb"));
    // The service computes the same digests.
    expect(await sectionHash("a\nb")).toBe("7e18f737311b2dc3b2f269dd78396b0351f14fb66efa879f768cb23181883c78");
    expect(await sectionHash("")).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  });
});

describe("title and headings", () => {
  it("take the first top-level heading outside code", () => {
    expect(titleOf("```\n# not this\n```\n\n# This one\n\n# Not this either")).toBe("This one");
    expect(titleOf("## only a section")).toBeNull();
    expect(titleOf(`# ${"x".repeat(200)}`)).toBe("x".repeat(160));
    expect(headingOf("\n## The heading\n\ntext")).toBe("The heading");
    expect(headingOf("# Title slide")).toBe("Title slide");
    // A slide's heading is the first line under its marker.
    expect(headingOf("<!-- layout: compare -->\n## The claim\n\ntext")).toBe("The claim");
    expect(headingOf("<!-- layout: title; accent: blue -->\n# Title slide")).toBe("Title slide");
  });
});

describe("a deck's accent", () => {
  const rest = "\n\n---\n\n<!-- layout: flow -->\n## One\n\n1. a\n2. b\n\nNote: Say a.";

  it("is set in the title slide's marker, and nothing else in the deck moves", () => {
    const plain = `<!-- layout: title -->\n# Deck\n\nOne line${rest}`;
    const blue = withAccent(plain, "blue");
    expect(blue).toBe(`<!-- layout: title; accent: blue -->\n# Deck\n\nOne line${rest}`);
    expect(parseSlides(blue).map((slide) => slide.accent)).toEqual(["blue", null]);
    // Another colour replaces it, and ink takes it away: ink is the default.
    expect(withAccent(blue, "teal")).toBe(`<!-- layout: title; accent: teal -->\n# Deck\n\nOne line${rest}`);
    expect(withAccent(blue, "ink")).toBe(plain);
    // Choosing what it already is changes nothing.
    expect(withAccent(blue, "blue")).toBe(blue);
    expect(withAccent(plain, "ink")).toBe(plain);
  });

  it("makes a marker for a first slide that has none, and leaves a deck with no slides alone", () => {
    expect(withAccent("# Deck\n\nOne line", "red")).toBe("<!-- layout: title; accent: red -->\n# Deck\n\nOne line");
    expect(withAccent("\n\n# Deck", "red")).toBe("\n\n<!-- layout: title; accent: red -->\n# Deck");
    expect(withAccent("# Deck", "ink")).toBe("# Deck");
    expect(parseSlides(withAccent("# Deck\n\n---\n\n## One", "amber")).map((slide) => [slide.layout, slide.accent])).toEqual([["title", "amber"], [null, null]]);
  });

  it("keeps the line ends of the file, and the blank lines before the marker", () => {
    expect(withAccent("<!-- layout: title -->\r\n# Deck\r\n", "violet")).toBe("<!-- layout: title; accent: violet -->\r\n# Deck\r\n");
    expect(withAccent("\n  <!-- layout: title; accent: blue -->\n# Deck", "teal")).toBe("\n  <!-- layout: title; accent: teal -->\n# Deck");
    expect(withAccent("<!--layout:title-->\n# Deck", "blue")).toBe("<!-- layout: title; accent: blue -->\n# Deck");
  });

  it("comes back as it was when the first slide is not a title slide, or the colour is not one of the six", () => {
    const text = "<!-- layout: text -->\n## Not a title\n\n- a";
    expect(withAccent(text, "blue")).toBe(text);
    const title = "<!-- layout: title -->\n# Deck";
    expect(withAccent(title, "pink")).toBe(title);
    expect(withAccent(title, "")).toBe(title);
    expect(withAccent("", "blue")).toBe("");
    expect(withAccent("\n \n", "blue")).toBe("\n \n");
  });

  it("round trips themes while preserving the accent and slide text", () => {
    const content = "<!-- layout: title; accent: teal -->\n# Deck\n\nMessage\n\nNote: Keep this";
    const editorial = withTheme(content, "editorial");
    expect(layoutOf(editorial)).toMatchObject({ designVersion: 1, body: layoutOf(content).body });
    expect(layoutOf(editorial)).toMatchObject({ accent: "teal", theme: "editorial" });
    expect(layoutOf(withAccent(editorial, "ink"))).toMatchObject({ accent: "ink", designVersion: 1, body: layoutOf(content).body });
    expect(withAccent(editorial, "red")).toContain("accent: red; theme: editorial");
    expect(layoutOf(withTheme(withAccent(editorial, "red"), "classic"))).toMatchObject({ accent: "red", theme: null, designVersion: 1, body: layoutOf(content).body });
    expect(withTheme("<!-- layout: title; theme: unknown -->\n# Deck", "briefing")).toContain("theme: briefing");
    expect(withTheme("# Old deck\n\nPlain slide", "classic")).toBe("# Old deck\n\nPlain slide");
  });
});

describe("the layout each slide is drawn in", () => {
  const item = (layout: string): OutlineItem => ({ heading: "x", goal: "", layout });
  const outline = [item("title"), item("compare"), item("takeaways")];
  const marked = "<!-- layout: title -->\n# Deck\n\n---\n\n<!-- layout: cards -->\n## One\n\n---\n\n<!-- layout: takeaways -->\n## Two";
  const unmarked = "# Deck\n\n---\n\n## One\n\n---\n\n## Two";

  it("is the marker's, which a hand edit may have changed from the plan", () => {
    expect(slideLayouts({ content: marked, outline })).toEqual(["title", "cards", "takeaways"]);
  });

  it("is the outline's for a deck made before markers, when the outline has a part for every slide", () => {
    expect(slideLayouts({ content: unmarked, outline })).toEqual(["title", "compare", "takeaways"]);
    expect(slideLayouts({ content: unmarked, outline: [item("title"), { heading: "x", goal: "" }, item("takeaways")] })).toEqual(["title", null, "takeaways"]);
  });

  it("is none when the outline and the deck do not line up, or there is no outline", () => {
    expect(slideLayouts({ content: unmarked, outline: outline.slice(0, 2) })).toEqual([null, null, null]);
    expect(slideLayouts({ content: unmarked, outline: [] })).toEqual([null, null, null]);
    expect(slideLayouts({ content: unmarked } as Pick<Artifact, "content" | "outline">)).toEqual([null, null, null]);
    // A slide with a marker still has its layout.
    expect(slideLayouts({ content: marked, outline: outline.slice(0, 1) })).toEqual(["title", "cards", "takeaways"]);
    expect(slideLayouts({ content: "", outline })).toEqual([]);
  });
});

describe("text typed over a version", () => {
  const version = {
    id: "art_1", kind: "report", content: "# T\n\n## One\n\na\n\n## Two\n\nb",
    sections: [
      { index: 0, heading: "One", checked: true, issues: [] },
      { index: 1, heading: "Two", checked: true, issues: [{ claim: "b", verdict: "unverified", note: "" }] },
    ],
  } as unknown as Artifact;

  it("keeps what was found in a section that still reads as it did, wherever it moved", () => {
    const moved = withDraft(version, "# T\n\n## New\n\nx\n\n## Two\n\nb\n\n## One\n\na");
    expect(moved.sections.map((s) => [s.index, s.heading, s.checked, s.issues.length])).toEqual([
      [0, "New", false, 0],
      [1, "Two", true, 1],
      [2, "One", true, 0],
    ]);
    // One id for every keystroke, so a deck is not rebuilt each time.
    expect(moved.id).toBe("art_1:draft");
    expect(moved.content).toContain("## New");
  });

  it("marks a section that was typed in as not re-checked", () => {
    expect(withDraft(version, "# T\n\n## One\n\na, and more\n\n## Two\n\nb").sections.map((s) => s.checked)).toEqual([false, true]);
    expect(withDraft(version, version.content).sections).toEqual(version.sections);
  });
});
