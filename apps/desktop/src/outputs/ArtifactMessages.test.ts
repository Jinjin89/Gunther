import { describe, expect, it } from "vitest";
import { describeChange } from "./ArtifactMessages";

describe("describeChange", () => {
  it("says what changed in a part, with the writer's instruction kept short", () => {
    const long = "Shorten slide 3 to its essentials: keep the 2.1 °C figure and its comparison, and cut the rest of the detail";
    const row = describeChange({ position: 3, was: 3, heading: "Heat fell", what: "rewritten", note: long }, "slides");
    expect(row.startsWith("Slide 3 · Heat fell — Shorten slide 3")).toBe(true);
    expect(row.endsWith("…")).toBe(true);
    expect(row.length).toBeLessThan(130);
  });
  it("names an added part by its place and heading only", () => {
    expect(describeChange({ position: 2, was: null, heading: "Costs", what: "added", note: "Write it for the lab" }, "report")).toBe("Added section 2 · Costs");
  });
});
