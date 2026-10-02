import type { ConversationBrief, ConversationCitation } from "@gunther/contracts";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BriefPanel } from "./ConversationBrief";

const brief: ConversationBrief = {
  goal: "Brief the mayor",
  constraints: ["Under one page"],
  settled: [
    { text: "Cool roofs cut heat", refs: [1, 7], by: "sources", because: "" },
    { text: "Skip B cells", refs: [], by: "you", because: "yes skip them" },
  ],
  open: ["What does it cost?"],
  edited: [],
  error: null,
};

const citation = { id: "cit_1", sourceId: "src_1", sourceTitle: "Harlow trial", assertionId: null, quote: "Roofs were cooler.", locator: "p. 2", status: "verified", confidence: 0.9, ref: 1 } as ConversationCitation;

const panel = (props: Partial<Parameters<typeof BriefPanel>[0]> = {}) => {
  const handlers = { onSave: vi.fn(), onRetry: vi.fn(), onOpenCitation: vi.fn() };
  render(<BriefPanel brief={brief} updating={false} readOnly={false} hasMessages citations={[citation]} {...handlers} {...props} />);
  return handlers;
};

describe("The conversation brief", () => {
  it("shows its four parts, with source numbers as buttons only when the selected answer has them", async () => {
    const user = userEvent.setup();
    const { onOpenCitation } = panel();
    for (const text of ["Brief the mayor", "Under one page", "Cool roofs cut heat", "Skip B cells", "What does it cost?"]) expect(screen.getByText(text)).toBeVisible();
    for (const label of ["Goal", "Constraints", "Settled", "Open"]) expect(screen.getByText(label, { selector: "small" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "[1]" }));
    expect(onOpenCitation).toHaveBeenCalledWith(citation);
    expect(screen.getByText("[7]").tagName).toBe("SPAN");
  });

  it("hides empty parts", () => {
    panel({ brief: { ...brief, goal: "", constraints: [], settled: [] } });
    expect(screen.queryByText("Goal", { selector: "small" })).not.toBeInTheDocument();
    expect(screen.queryByText("Constraints", { selector: "small" })).not.toBeInTheDocument();
    expect(screen.getByText("Open", { selector: "small" })).toBeVisible();
  });

  it("edits a line in place and saves it with Enter", async () => {
    const user = userEvent.setup();
    const { onSave } = panel();
    await user.click(screen.getByRole("button", { name: "Under one page" }));
    await user.clear(screen.getByRole("textbox", { name: "Edit constraint" }));
    await user.type(screen.getByRole("textbox", { name: "Edit constraint" }), "Two pages{Enter}");
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ constraints: ["Two pages"], goal: "Brief the mayor" }));
  });

  it("removes a line and adds one", async () => {
    const user = userEvent.setup();
    const { onSave } = panel();
    await user.click(screen.getByRole("button", { name: "Remove open question: What does it cost?" }));
    expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ open: [] }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Brief part" }), "Open");
    await user.type(screen.getByRole("textbox", { name: "Add to the brief" }), "Which year?{Enter}");
    expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ open: ["What does it cost?", "Which year?"] }));
  });

  it("says it is updating, and offers Retry when the update failed", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<BriefPanel brief={brief} updating readOnly={false} hasMessages citations={[]} onSave={vi.fn()} onRetry={vi.fn()} onOpenCitation={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Updating…");
    const onRetry = vi.fn();
    rerender(<BriefPanel brief={{ ...brief, error: "Model is down" }} updating={false} readOnly={false} hasMessages citations={[]} onSave={vi.fn()} onRetry={onRetry} onOpenCitation={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn’t update the brief · Retry");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.getByText("Brief the mayor")).toBeVisible();
  });
});
