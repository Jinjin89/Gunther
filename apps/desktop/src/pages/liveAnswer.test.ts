import { describe, expect, it } from "vitest";
import { applyAnswerEvent, type LiveAnswerState } from "./liveAnswer";

const start: LiveAnswerState = { steps: [], text: "" };

describe("applyAnswerEvent", () => {
  it("follows a search from running to done, then the text", () => {
    let state = applyAnswerEvent(start, { type: "step", state: "running", tool: "search_library", label: "Searched your library for “x”" });
    expect(state.steps).toEqual([expect.objectContaining({ running: true })]);
    state = applyAnswerEvent(state, { type: "step", state: "done", tool: "search_library", label: "Searched your library for “x”", query: "x", found: 3 });
    expect(state.steps).toHaveLength(1);
    expect(state.steps[0]).toMatchObject({ running: false, found: 3 });
    state = applyAnswerEvent(applyAnswerEvent(state, { type: "text", text: "Hel" }), { type: "text", text: "lo" });
    expect(state.text).toBe("Hello");
  });
});
