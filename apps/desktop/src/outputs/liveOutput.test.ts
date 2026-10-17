import { describe, expect, it } from "vitest";
import type { ArtifactTurnEvent, OutputEvent } from "../api";
import { applyOutputEvent, EMPTY_LIVE } from "./liveOutput";

const empty = EMPTY_LIVE;
const feed = (...events: ArtifactTurnEvent[]) => events.reduce(applyOutputEvent, empty);

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

  it("follows the skill's steps, each updated in place, and keeps the approach it settled on", () => {
    const approach = {
      question: "How are T and B cells told apart?", answer: "By CD3D and CD19.", purpose: "A panel.",
      structure: "what", structureReason: "", pages: null, supplements: [], gaps: [],
    };
    const state = feed(
      { type: "stage", id: "understand", label: "Understanding the material", state: "running" },
      { type: "approach", approach },
      { type: "stage", id: "understand", label: "Understanding the material", state: "done" },
      { type: "stage", id: "reader_test", label: "Reading it as the audience", state: "failed", detail: "The reader is down" },
    );
    expect(state.stages.map((stage) => [stage.id, stage.state, stage.detail])).toEqual([
      ["understand", "done", null],
      ["reader_test", "failed", "The reader is down"],
    ]);
    expect(state.approach?.question).toBe("How are T and B cells told apart?");
  });

  it("lists the actions a turn of the Artifacts agent decides on, and updates each by its id", () => {
    const state = feed(
      { type: "turn", conversationId: "conv", messageId: "m1" },
      { type: "decision", actions: [{ id: "a1", do: "search_library", label: "Searching your library" }, { id: "a2", do: "write", label: "Writing slide 4" }] },
      { type: "action", id: "a1", state: "running", label: "Searching your library", note: "" },
      { type: "action", id: "a1", state: "done", label: "Searched your library for “cost”", note: "6 found" },
      { type: "action", id: "a2", state: "running", label: "Writing slide 4", note: "" },
    );
    expect(state.turn).toEqual({ conversationId: "conv", messageId: "m1" });
    expect(state.actions.map((action) => [action.id, action.action, action.state, action.label, action.note])).toEqual([
      ["a1", "search_library", "done", "Searched your library for “cost”", "6 found"],
      ["a2", "write", "running", "Writing slide 4", ""],
    ]);
    // A later decision adds its actions after these.
    expect(applyOutputEvent(state, { type: "decision", actions: [{ id: "a3", do: "save", label: "Saving" }] }).actions.map((action) => [action.id, action.state])).toEqual([["a1", "done"], ["a2", "running"], ["a3", "waiting"]]);
  });

  it("keeps the question that ends a turn, the pieces of a reply, and the brief", () => {
    const brief = { goal: { text: "Trial results", mark: "you" as const }, audience: null, constraints: [], open: [] };
    const state = feed(
      { type: "brief", brief, spec: null },
      { type: "reply", text: "The Marker " }, { type: "reply", text: "review says so [1]." },
      { type: "question", text: "Which focus?", options: ["Trial", "Whole debate"] },
    );
    expect(state.reply).toBe("The Marker review says so [1].");
    expect(state.asking).toEqual({ text: "Which focus?", options: ["Trial", "Whole debate"] });
    expect(state.brief?.brief.goal?.text).toBe("Trial results");
  });
});
