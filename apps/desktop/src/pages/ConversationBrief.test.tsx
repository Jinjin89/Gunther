import type { ConversationBrief, ConversationCitation } from "@gunther/contracts";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { persistentStorage } from "../persistentStorage";
import { BriefPanel } from "./ConversationBrief";

const brief: ConversationBrief = {
  goal: "Brief the mayor",
  constraints: ["Under one page"],
  settled: [
    { text: "Cool roofs cut heat", refs: [1, 7], by: "sources", because: "" },
    { text: "Skip B cells", refs: [], by: "you", because: "yes skip them" },
  ],
  open: ["What does it cost?"],
  edited: ["constraints:Under one page"],
  error: null,
};

const citation: ConversationCitation = { id: "cit_1", sourceId: "src_1", sourceTitle: "Harlow trial", assertionId: null,
  quote: "Roofs were cooler.", locator: "p. 2", status: "verified", confidence: 0.9, ref: 1 };
const owningList = [citation];
const citations = new Map([[1, { citations: owningList, index: 0 }]]);
const emptyNote = "Nothing noted yet — Gunther keeps the goal, what's settled and what's open here as you talk.";
const panel = (props: Partial<Parameters<typeof BriefPanel>[0]> = {}) => {
  const handlers = { onSave: vi.fn(), onRetry: vi.fn(), onOpenCitation: vi.fn() };
  return { ...render(<BriefPanel brief={brief} updating={false} readOnly={false} hasMessages citations={citations} {...handlers} {...props} />), ...handlers };
};

beforeEach(() => persistentStorage.removeItem("gunther:brief-collapsed"));

describe("The conversation brief", () => {
  it("shows selectable text, provenance, and clickable citations in read mode", async () => {
    const user = userEvent.setup();
    const { onOpenCitation } = panel();
    for (const text of ["Brief the mayor", "Under one page", "Cool roofs cut heat", "Skip B cells", "What does it cost?"]) {
      expect(screen.getByText(text, { selector: "p" })).toBeVisible();
    }
    for (const label of ["Goal", "Constraints", "Settled", "Open"]) expect(screen.getByRole("heading", { name: new RegExp(`^${label}`) })).toBeVisible();
    await user.click(screen.getByText("Under one page"));
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.getByText("Under one page").closest("li")).toHaveAttribute("title", "Edited by you — Gunther keeps your wording");
    expect(screen.getByText("You agreed: “yes skip them”")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "[1]" }));
    expect(onOpenCitation).toHaveBeenCalledWith(owningList, 0);
    expect(screen.getByText("[7]").tagName).toBe("SPAN");
  });

  it("shows empty stages and their add buttons only in edit mode", async () => {
    const user = userEvent.setup();
    panel({ brief: { ...brief, goal: "", constraints: [], settled: [] } });
    expect(screen.queryByRole("heading", { name: "Goal" })).toBeNull();
    expect(screen.queryByRole("heading", { name: /^Constraints/ })).toBeNull();
    expect(screen.getByRole("heading", { name: /^Open/ })).toBeVisible();
    expect(screen.queryByText(emptyNote)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("heading", { name: "Goal" })).toBeVisible();
    for (const label of ["Set a goal…", "+ Add constraint", "+ Add conclusion", "+ Add open question"]) {
      expect(screen.getByRole("button", { name: label })).toBeVisible();
    }
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("explains an empty brief in read mode and shows add rows when editing", async () => {
    const user = userEvent.setup();
    panel({ brief: undefined });
    expect(screen.getByText(emptyNote)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Collapse Brief" }));
    expect(screen.getByText(emptyNote)).toBeVisible();
    expect(screen.queryByText("0 constraints · 0 settled · 0 open")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.queryByText(emptyNote)).toBeNull();
    for (const label of ["Set a goal…", "+ Add constraint", "+ Add conclusion", "+ Add open question"]) {
      expect(screen.getByRole("button", { name: label })).toBeVisible();
    }
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.getByText(emptyNote)).toBeVisible();
  });

  it("shows the answer's saved changes, including in the collapsed preview", async () => {
    const user = userEvent.setup();
    panel({ selectedMessageId: "latest", brief: {
      ...brief, throughMessageId: "latest", constraints: ["Under one page", "Use the pilot data"],
      lastChange: {
        messageId: "latest", goalChanged: true,
        added: { constraints: ["Use the pilot data"], settled: ["Cool roofs cut heat"], open: ["What does it cost?"] },
        removed: { constraints: [], open: ["Who ran the pilot?", "When did it end?"] },
      },
    } });
    const summary = "After the last answer: goal updated · +1 constraint · +1 settled · 2 questions closed · +1 open";
    expect(screen.getByText(summary)).toBeVisible();
    for (const text of ["Brief the mayor", "Use the pilot data", "Cool roofs cut heat", "What does it cost?"]) {
      const line = screen.getByText(text).closest("li")!;
      expect(line).toHaveClass("is-accented");
      expect(within(line).getByText("New")).toBeVisible();
    }
    expect(within(screen.getByText("Under one page").closest("li")!).queryByText("New")).toBeNull();
    const closed = screen.getByText("Closed", { selector: "summary" });
    expect(closed).toHaveTextContent("Closed (2)");
    expect(screen.getByText("Who ran the pilot?")).toBeVisible();
    await user.click(closed);
    expect(screen.getByText("Who ran the pilot?")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "Collapse Brief" }));
    expect(screen.getByText(summary)).toBeVisible();
    expect(screen.queryByText("New")).toBeNull();
  });

  it("shows Closed under Open when the answer closed the last question", () => {
    panel({ hasMessages: false, brief: {
      goal: "", constraints: [], settled: [], open: [], edited: [], error: null,
      throughMessageId: "latest", lastChange: {
        messageId: "latest", goalChanged: false, added: { constraints: [], settled: [], open: [] },
        removed: { constraints: [], open: ["When did it end?"] },
      },
    } });
    expect(screen.getByText("After the last answer: 1 question closed")).toBeVisible();
    expect(screen.getByRole("heading", { name: /^Open/ })).toHaveTextContent(/Open\s*0/);
    expect(screen.getByText("When did it end?")).toBeVisible();
  });

  it("keeps citations in view and echoes answer IDs when saving a settled edit", async () => {
    const user = userEvent.setup();
    const settled = brief.settled.map((item) => ({ ...item, messageId: "same-answer" }));
    const { onSave } = panel({ brief: { ...brief, settled } });
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "Cool roofs cut heat" }));
    const field = screen.getByRole("textbox", { name: "Edit conclusion" });
    expect(field).toHaveFocus();
    expect(screen.getByRole("button", { name: "[1]" })).toBeVisible();
    await user.keyboard("{End}{Shift>}{Enter}{/Shift}in summer");
    expect(field).toHaveValue("Cool roofs cut heat\nin summer");
    expect(onSave).not.toHaveBeenCalled();
    await user.tab();
    expect(onSave).toHaveBeenCalledOnce();
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ settled: [
      { ...settled[0], text: "Cool roofs cut heat\nin summer" }, settled[1],
    ] }));
  });

  it("discards a focused edit, then exits edit mode on the next Escape", async () => {
    const user = userEvent.setup();
    const { onSave } = panel();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "Under one page" }));
    await user.type(screen.getByRole("textbox", { name: "Edit constraint" }), " changed{Escape}");
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Under one page" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Done" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Edit" })).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Under one page" })).toBeNull();
  });

  it("removes the active row without saving its draft and adds a line with Enter", async () => {
    const user = userEvent.setup();
    const { onSave } = panel();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "What does it cost?" }));
    await user.type(screen.getByRole("textbox", { name: "Edit open question" }), " draft");
    await user.click(screen.getByRole("button", { name: "Remove open question: What does it cost?" }));
    expect(onSave).toHaveBeenCalledOnce();
    expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ open: [] }));
    await user.click(screen.getByRole("button", { name: "+ Add constraint" }));
    await user.type(screen.getByRole("textbox", { name: "Add constraint" }), " Show the costs {Enter}");
    expect(onSave).toHaveBeenCalledTimes(2);
    expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ constraints: ["Under one page", "Show the costs"] }));
  });

  it("remembers collapse across chats and shows the newest three conclusions when expanded", async () => {
    const user = userEvent.setup();
    const current = { ...brief, settled: Array.from({ length: 5 }, (_, index) => ({ text: `Conclusion ${index}`, by: "you" as const, refs: [], because: "" })) };
    const { unmount } = panel();
    await user.click(screen.getByRole("button", { name: "Collapse Brief" }));
    expect(screen.getByText("1 constraint · 2 settled · 1 open")).toBeVisible();
    unmount();
    panel({ brief: current });
    expect(screen.getByRole("button", { name: "Expand Brief" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Goal" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Expand Brief" }));
    expect(screen.queryByText("Conclusion 1")).toBeNull();
    expect(screen.getByText("Conclusion 2")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Show all 5" }));
    expect(screen.getByText("Conclusion 0")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Show fewer" }));
    await user.click(screen.getByRole("button", { name: "Collapse Brief" }));
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("button", { name: "Conclusion 0" })).toBeVisible();
  });

  it("shows update status and Retry even while the brief is collapsed", async () => {
    const user = userEvent.setup();
    const empty = { ...brief, goal: "", constraints: [], settled: [], open: [] };
    const { rerender, onRetry } = panel({ brief: empty, updating: true });
    await user.click(screen.getByRole("button", { name: "Collapse Brief" }));
    expect(screen.getByRole("status")).toHaveTextContent("Updating…");
    expect(screen.queryByText(emptyNote)).toBeNull();
    rerender(<BriefPanel brief={{ ...empty, error: "Model is down" }} updating={false} readOnly={false} hasMessages citations={citations}
      onSave={vi.fn()} onRetry={onRetry} onOpenCitation={vi.fn()} />);
    const alert = screen.getByRole("alert");
    expect(alert).toBeVisible();
    expect(screen.queryByText(emptyNote)).toBeNull();
    expect(alert.previousElementSibling).toBe(screen.getByRole("heading", { name: "Brief" }).closest("header"));
    expect(alert).toHaveTextContent("Couldn’t update the brief · Retry");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
