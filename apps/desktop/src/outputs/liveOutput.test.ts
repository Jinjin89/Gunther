import { expect, it } from "vitest";
import type { ArtifactTurnEvent } from "../api";
import { libraryScope } from "./artifactConversations";
import { applyOutputEvent, EMPTY_LIVE, outputActivity, type PlanEvent } from "./liveOutput";

const feed = (...events: ArtifactTurnEvent[]) => events.reduce(applyOutputEvent, EMPTY_LIVE);

it("shows the current stage while an action is running", () => {
  const stage = { type: "stage", id: "write", label: "Writing slides", state: "running", detail: "3 of 8" } as const;
  const state = feed({ type: "action", id: "a1", label: "Building", note: "", state: "running" }, stage);

  expect(outputActivity(state)).toBe("Writing slides · 3 of 8");
  const finished = applyOutputEvent(state, { ...stage, state: "done" });
  expect(outputActivity(finished)).toBe("Building");
  expect(outputActivity(applyOutputEvent(finished, { type: "action", id: "a1", label: "Building", note: "", state: "done" }))).toBe("Starting…");
});

it("uses section progress when the stream has no current stage", () => {
  const state = feed(
    { type: "outline", title: "Trial findings", sections: [{ heading: "Results", goal: "" }] },
    { type: "section", index: 0, state: "checking" },
  );
  expect(state.title).toBe("Trial findings");
  expect(outputActivity(state)).toBe("Checking sources · Results");
  expect(outputActivity(applyOutputEvent(state, { type: "section", index: 0, state: "done" }))).toBe("Starting…");
});

it("keeps streamed reply text in order", () => {
  const state = feed({ type: "reply", text: "The evidence " }, { type: "reply", text: "is mixed." });
  expect(state.reply).toBe("The evidence is mixed.");
});

it("records only the latest committed plan and retains the independent live progress", () => {
  const committed: PlanEvent = {
    type: "plan", conversationId: "conversation", workKey: "draft_work", editVersion: 8,
    plan: {
      planId: "draft_work", planRevision: "p3-revision", planStatus: "needs_answer", basis: "assistant", blocker: null,
      options: {
        type: "flashcards", scope: libraryScope(), focus: "", title: null, audience: "student", length: null,
        language: null, use: null, theme: null, designVersion: null, voices: null, web: false, model: null, effort: null,
      },
      preferences: { emphasize: [], avoid: [] }, outline: null, proposal: null, attempt: null, approvedRevision: null,
      pending: { planId: "draft_work", planRevision: "p3-revision", messageId: "question", question: "Who will read this?", options: [] },
    },
  };
  const stage = { type: "stage", id: "read", label: "Reading", state: "running" } as const;
  const state = feed(
    { type: "outline", title: "Stream preview", sections: [{ heading: "Overview", goal: "" }] },
    { type: "reply", text: "A question follows." },
    stage,
    committed,
  );
  expect(state.plan).toBe(committed);
  expect(state).toMatchObject({ title: "Stream preview", headings: ["Overview"], reply: "A question follows." });
  expect(outputActivity(state)).toBe("Reading");
  expect(applyOutputEvent(state, { ...committed, editVersion: 7 })).toBe(state);
  expect(applyOutputEvent(state, { ...stage, state: "done" }).plan).toBe(committed);
});
