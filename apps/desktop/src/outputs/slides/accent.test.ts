import { describe, expect, it } from "vitest";
import { ACCENTS } from "../sections";
import { ACCENT_COLOURS, ACCENT_LABELS, accentColour, contrast, isAccent } from "./accent";

describe("the accent colours", () => {
  it("are the six names a title slide's marker may carry, each with a colour and a label", () => {
    expect(Object.keys(ACCENT_COLOURS).sort()).toEqual([...ACCENTS].sort());
    expect(Object.keys(ACCENT_LABELS).sort()).toEqual([...ACCENTS].sort());
  });

  it.each(ACCENTS)("%s reaches 4.5:1 against white paper, the AA mark for text", (name) => {
    expect(contrast(ACCENT_COLOURS[name], "#ffffff")).toBeGreaterThanOrEqual(4.5);
  });

  it("works out contrast as WCAG does", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#ffffff", "#ffffff")).toBe(1);
    // Order does not matter.
    expect(contrast("#ffffff", "#2668b6")).toBe(contrast("#2668b6", "#ffffff"));
    // The muted ink of the slides is itself readable on white.
    expect(contrast("#5f5e5a", "#ffffff")).toBeGreaterThan(6);
  });

  it("is ink for no accent, an unknown name, or a name a newer version knows", () => {
    expect(accentColour(null)).toBe(ACCENT_COLOURS.ink);
    expect(accentColour(undefined)).toBe(ACCENT_COLOURS.ink);
    expect(accentColour("pink")).toBe(ACCENT_COLOURS.ink);
    expect(accentColour("teal")).toBe(ACCENT_COLOURS.teal);
    expect(isAccent("amber")).toBe(true);
    expect(isAccent("pink")).toBe(false);
  });
});
