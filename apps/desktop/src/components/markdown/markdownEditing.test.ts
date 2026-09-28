import { describe, expect, it } from "vitest";
import {
  applyEdit,
  continueList,
  countWords,
  insertLink,
  markdownToPlainText,
  setTaskCheckedAt,
  shiftListItems,
  toggleLinePrefix,
  toggleWrap,
} from "./markdownEditing";

const run = (value: string, edit: ReturnType<typeof toggleWrap> | null) => {
  if (!edit) return null;
  const next = applyEdit(value, edit);
  return { next, selected: next.slice(edit.selectionStart, edit.selectionEnd) };
};

describe("markdown editing", () => {
  it("wraps and unwraps a selection", () => {
    expect(run("a word here", toggleWrap("a word here", 2, 6, "**"))).toEqual({ next: "a **word** here", selected: "word" });
    expect(run("a **word** here", toggleWrap("a **word** here", 4, 8, "**"))).toEqual({ next: "a word here", selected: "word" });
    expect(run("x", toggleWrap("x", 1, 1, "_", "emphasis"))).toEqual({ next: "x_emphasis_", selected: "emphasis" });
  });

  it("creates links around text or a pasted URL", () => {
    expect(run("see docs", insertLink("see docs", 4, 8))).toEqual({ next: "see [docs](https://)", selected: "https://" });
    expect(run("https://a.b/c", insertLink("https://a.b/c", 0, 13))).toEqual({ next: "[link](https://a.b/c)", selected: "link" });
  });

  it("continues bullets, numbers and tasks, and ends an empty item", () => {
    expect(run("- one", continueList("- one", 5, 5))?.next).toBe("- one\n- ");
    expect(run("  9. nine", continueList("  9. nine", 9, 9))?.next).toBe("  9. nine\n  10. ");
    expect(run("- [x] done", continueList("- [x] done", 10, 10))?.next).toBe("- [x] done\n- [ ] ");
    expect(run("- one\n- ", continueList("- one\n- ", 8, 8))?.next).toBe("- one\n");
    expect(continueList("plain text", 10, 10)).toBeNull();
    expect(continueList("- one", 1, 1)).toBeNull();
  });

  it("toggles line prefixes without stacking markers", () => {
    expect(run("a\nb", toggleLinePrefix("a\nb", 0, 3, "- "))?.next).toBe("- a\n- b");
    expect(run("- a\n- b", toggleLinePrefix("- a\n- b", 0, 7, "- "))?.next).toBe("a\nb");
    expect(run("- a\n- b", toggleLinePrefix("- a\n- b", 0, 7, "1. "))?.next).toBe("1. a\n2. b");
    expect(run("- a", toggleLinePrefix("- a", 3, 3, "- [ ] "))?.next).toBe("- [ ] a");
    expect(run("- a", toggleLinePrefix("- a", 3, 3, "> "))?.next).toBe("> - a");
    expect(run("Title", toggleLinePrefix("Title", 2, 2, "## "))?.next).toBe("## Title");
    expect(run("## Title", toggleLinePrefix("## Title", 4, 4, "## "))?.next).toBe("Title");
  });

  it("indents and outdents list items only", () => {
    expect(run("- a\n- b", shiftListItems("- a\n- b", 6, 6, false))?.next).toBe("- a\n  - b");
    expect(run("- a\n  - b", shiftListItems("- a\n  - b", 8, 8, true))?.next).toBe("- a\n- b");
    expect(shiftListItems("text", 1, 1, false)).toBeNull();
  });

  it("checks the task that starts at a source offset", () => {
    const source = "Intro\n- [ ] one\n  - [x] nested\n> - [ ] quoted";
    expect(setTaskCheckedAt(source, 6, true)).toBe("Intro\n- [x] one\n  - [x] nested\n> - [ ] quoted");
    expect(setTaskCheckedAt(source, source.indexOf("- [x] nested"), false)).toContain("  - [ ] nested");
    expect(setTaskCheckedAt(source, source.indexOf("- [ ] quoted"), true)).toContain("> - [x] quoted");
    expect(setTaskCheckedAt(source, 0, true)).toBe(source);
  });

  it("summarises Markdown as plain text and counts words", () => {
    expect(markdownToPlainText("# Title\n\n- [ ] **Buy** [milk](https://x.y)\n> quoted `code`")).toBe("Title Buy milk quoted code");
    expect(countWords("## Hello *there*\n\n- one two")).toBe(4);
    expect(countWords("学习笔记 notes")).toBe(5);
  });
});
