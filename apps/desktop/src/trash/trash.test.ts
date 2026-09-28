import { describe, expect, it } from "vitest";
import { makeTrashItem } from "../test/fixtures";
import { daysLeft, trashedMessage } from "./trash";

describe("Trash wording", () => {
  it("says what went to Trash, including a library's sources", () => {
    expect(trashedMessage(makeTrashItem({ title: "Lecture 3" }))).toBe("Moved “Lecture 3” to Trash.");
    expect(trashedMessage(makeTrashItem({ kind: "library", title: "Cells", itemCount: 1 }))).toBe("Moved “Cells” and 1 source to Trash.");
    expect(trashedMessage(makeTrashItem({ kind: "library", title: "Cells", itemCount: 12 }))).toBe("Moved “Cells” and 12 sources to Trash.");
    expect(trashedMessage(makeTrashItem({ kind: "library", title: "Empty", itemCount: 0 }))).toBe("Moved “Empty” to Trash.");
  });

  it("counts whole days left and never below zero", () => {
    const now = Date.parse("2026-09-28T12:00:00Z");
    expect(daysLeft({ expiresAt: "2026-10-28T12:00:00Z" }, now)).toBe(30);
    expect(daysLeft({ expiresAt: "2026-09-28T13:00:00Z" }, now)).toBe(1);
    expect(daysLeft({ expiresAt: "2026-09-20T12:00:00Z" }, now)).toBe(0);
  });
});
