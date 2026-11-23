import { expect, it } from "vitest";
import type { ArtifactTurnEvent } from "../api";
import { applyOutputEvent, EMPTY_LIVE, outputActivity } from "./liveOutput";

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
