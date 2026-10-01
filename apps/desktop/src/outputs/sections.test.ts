import type { Artifact } from "@gunther/contracts";
import { describe, expect, it } from "vitest";
import { headingOf, joinDocument, parseSlides, sectionHash, splitDocument, splitNotes, titleOf, withDraft } from "./sections";

// The same cases as tests/test_outputs.py: the service and the app split a document alike.

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
  it("gives each slide its heading, what it shows and what is said", () => {
    expect(parseSlides("# Deck\n\nsub\n\n---\n\n## One\n\n- a\n\nNote: Say a.")).toEqual([
      { heading: "Deck", body: "# Deck\n\nsub", notes: "" },
      { heading: "One", body: "## One\n\n- a", notes: "Say a." },
    ]);
    expect(parseSlides("")).toEqual([]);
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
