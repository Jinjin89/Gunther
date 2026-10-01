import { describe, expect, it } from "vitest";
import type { OutputEvent } from "../api";
import { applyOutputEvent, type LiveOutputState } from "./liveOutput";

const empty: LiveOutputState = { question: null, title: null, sections: [], steps: [] };
const feed = (...events: OutputEvent[]) => events.reduce(applyOutputEvent, empty);

const outline: OutputEvent = {
  type: "outline",
  title: "Immune markers",
  sections: [{ heading: "T cells", goal: "How T cells are told apart" }, { heading: "B cells", goal: "" }],
};

describe("an output being built", () => {
  it("shows the plan first, with every section waiting", () => {
    const state = feed(outline);
    expect(state.title).toBe("Immune markers");
    expect(state.sections.map((s) => [s.heading, s.goal, s.state])).toEqual([
      ["T cells", "How T cells are told apart", "waiting"],
      ["B cells", "", "waiting"],
    ]);
  });

  it("moves each section through research, writing and checking, and gathers its text", () => {
    const state = feed(
      outline,
      { type: "section", index: 0, state: "researching" },
      { type: "section", index: 0, state: "writing" },
      { type: "text", section: 0, text: "## T cells\n\nCD3D " },
      { type: "text", section: 0, text: "marks T cells [1]." },
      { type: "section", index: 0, state: "checking" },
      { type: "section", index: 0, state: "done" },
      { type: "section", index: 1, state: "writing" },
      { type: "text", section: 1, text: "## B cells" },
    );
    expect(state.sections.map((s) => s.state)).toEqual(["done", "writing"]);
    expect(state.sections[0]?.text).toBe("## T cells\n\nCD3D marks T cells [1].");
    expect(state.sections[1]?.text).toBe("## B cells");
  });

  it("puts a search under the section that made it, and replaces a running one with its result", () => {
    const running: OutputEvent = { type: "step", state: "running", tool: "search_library", label: "Searched your library for “CD3D”", section: 0 };
    const state = feed(
      outline,
      running,
      { ...running, state: "done", query: "CD3D", found: 2, error: null },
      { type: "step", state: "running", tool: "search_library", label: "Searched your library for “instruction”", section: null },
    );
    expect(state.sections[0]?.steps).toEqual([
      { tool: "search_library", label: "Searched your library for “CD3D”", query: "CD3D", found: 2, error: null, running: false },
    ]);
    expect(state.sections[1]?.steps).toEqual([]);
    // One that belongs to no section (the research of a revision of the whole output) is kept apart.
    expect(state.steps.map((step) => [step.label, step.running])).toEqual([["Searched your library for “instruction”", true]]);
  });

  it("makes room for a section it was not told about, as a revision of an existing version does", () => {
    const state = feed({ type: "section", index: 1, state: "writing" }, { type: "text", section: 1, text: "Hello" });
    expect(state.sections).toHaveLength(2);
    expect(state.sections[1]).toMatchObject({ state: "writing", text: "Hello" });
  });

  it("remembers what a build that is followed again was asked", () => {
    expect(feed({ type: "resumed", question: "Cover B cells", startedAt: 1 }).question).toBe("Cover B cells");
  });
});
