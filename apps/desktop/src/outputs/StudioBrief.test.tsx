import type { Artifact } from "@gunther/contracts";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApproachPanel } from "./ApproachPanel";
import { applyOutputEvent, EMPTY_LIVE, outputActivity } from "./liveOutput";
import { StudioLive } from "./StudioBrief";
import { qualityReport, spoken } from "./studioReport";

const memo = { answer: "Cool roofs save 9% once weather is adjusted", insights: [{ claim: "The saving halves", so_what: "Budget with 9%", refs: [2, 7] }], tensions: [{ issue: "Costs are not in the material", refs: [] }] };

const saved = {
  id: "a1", kind: "slides",
  sections: [
    { index: 0, heading: "Cover", checked: true, issues: [] },
    { index: 1, heading: "Size", checked: true, issues: [{ claim: "x", verdict: "unsupported", note: "" }, { claim: "y", verdict: "unsupported", note: "" }] },
    { index: 2, heading: "Saving", checked: false, issues: [] },
  ],
  citations: [{}, {}],
  provenance: {
    approach: { method: "studio", question: "q", answer: memo.answer, gaps: [], memo, reviews: [{ round: 1, verdict: "ready", understood: "", problems: [] }], refMap: { "2": 1 } },
    skill: { name: "studio", version: 1, skipped: [{ step: "verify", label: "Format", reason: "Slide 3 had to become a table" }], trail: [{ step: "memo", state: "done", detail: "", seconds: 70 }, { step: "write", state: "done", detail: "", seconds: 50 }] },
  },
} as unknown as Artifact;

describe("a studio artifact being made", () => {
  it("keeps the memo, the critic's rounds and the stage seconds as they arrive", () => {
    const state = [
      { type: "stage", id: "memo", label: "Finding what is worth saying", state: "done", seconds: 12 },
      { type: "memo", memo },
      { type: "review", round: 1, verdict: "revise", understood: "", problems: [{ problem: "Title announces" }] },
      { type: "outline", title: "T", sections: [{ heading: "One", goal: "" }] },
    ].reduce((all, event) => applyOutputEvent(all, event as never), EMPTY_LIVE);
    // An event the page does not use (the storyline) must not wipe what it has heard.
    expect(applyOutputEvent(state, { type: "storyline", storyline: { title: "T" } })).toBe(state);
    expect(state.memo?.insights).toHaveLength(1);
    expect(state.stages[0]?.seconds).toBe(12);
    render(<StudioLive state={state} />);
    expect(screen.getByText(memo.answer)).toBeInTheDocument();
    expect(screen.getByText("The critic asked for 1 change (round 1).")).toBeInTheDocument();
    expect(screen.getByText("12 s")).toBeInTheDocument();
    expect(screen.getByLabelText("Planned")).toHaveTextContent("One");
  });

  it("shows what a running step is doing now", () => {
    const state = [
      { type: "stage", id: "science", label: "Reviewing the scientific explanation", state: "running", detail: "checking the slides against the material" },
      { type: "stage", id: "science", label: "Reviewing the scientific explanation", state: "running", detail: "fixing 3 problems" },
    ].reduce((all, event) => applyOutputEvent(all, event as never), EMPTY_LIVE);
    render(<StudioLive state={state} />);
    expect(screen.getByText("fixing 3 problems")).toBeInTheDocument();
    expect(outputActivity(state)).toBe("Reviewing the scientific explanation · fixing 3 problems");
  });
});

describe("a saved studio artifact", () => {
  it("says in plain words what the checker still flags", () => {
    const report = qualityReport(saved);
    expect(report.flagged).toBe(2);
    expect(report.rows[1]?.notes).toEqual(["2 claims could not be confirmed in their sources"]);
    expect(report.seconds).toBe(120);
    expect(report.formatNotes).toEqual(["Format: Slide 3 had to become a table"]);
  });
  it("extends the approach panel with the answer, cited insights and the quality report", () => {
    const cite = vi.fn();
    render(<ApproachPanel artifact={saved} onCite={cite} />);
    fireEvent.click(screen.getByRole("button", { name: /How it was made/ }));
    fireEvent.click(screen.getByRole("button", { name: "Source 1" }));
    expect(cite).toHaveBeenCalledWith(0);
    expect(screen.queryByText("[7]")).toBeNull();
    expect(screen.getByText("Costs are not in the material")).toBeInTheDocument();
    expect(screen.getByText("The critic accepted the storyline.")).toBeInTheDocument();
    expect(screen.getByText("The checker still flags 2 of 3. 1 not checked yet.")).toBeInTheDocument();
  });
});

describe("a scientific deck's review", () => {
  it("says plainly when the review could not run or left open problems", async () => {
    const { criticLine, ScientificReview } = await import("./StudioBrief");
    expect(criticLine({ verdict: "unavailable", understood: "", problems: [] })).toBe("The critic could not review the storyline.");
    const { rerender } = render(<ScientificReview status="open_problems" limitations={["Review: slide 3: The control is unnamed"]} />);
    expect(screen.getByText(/Delivered with open problems/)).toBeInTheDocument();
    expect(screen.getByText("Review: slide 3: The control is unnamed")).toBeInTheDocument();
    rerender(<ScientificReview status="unavailable" limitations={[]} />);
    expect(screen.getByText(/Not reviewed/)).toBeInTheDocument();
    rerender(<ScientificReview status="ready" limitations={[]} />);
    expect(screen.getByText("The scientific review found the deck ready.")).toBeInTheDocument();
  });
});

describe("a stage time", () => {
  it("reads in whole seconds", () => {
    expect(spoken(263.338)).toBe("4 min 23 s");
    expect(spoken(12.6)).toBe("13 s");
    expect(spoken(119.7)).toBe("2 min");
  });
});
