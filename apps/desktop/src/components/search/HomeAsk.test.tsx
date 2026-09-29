import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeBase } from "../../test/fixtures";
import { HomeAsk, looksLikeQuestion } from "./HomeAsk";

const noop = () => undefined;
const baseProps = {
  bases: [makeBase({ id: "base_1", title: "Immunology" })],
  messages: [],
  pending: null,
  live: null,
  error: null,
  active: false,
  onAsk: noop,
  onCancel: noop,
  onClose: noop,
  onNew: noop,
  onFile: noop,
  onOpenSource: noop,
};

describe("looksLikeQuestion", () => {
  it.each([
    ["What marks T cells?", true],
    ["why is the sky blue", true],
    ["为什么 CD3D 重要", true],
    ["compare the two papers on attention", true],
    ["CD3D", false],
    ["t cell markers", false],
    ["", false],
  ])("%s → %s", (text, expected) => expect(looksLikeQuestion(text)).toBe(expected));
});

describe("HomeAsk", () => {
  it("offers to ask a search, loudly for a question and quietly for keywords", async () => {
    const onAsk = vi.fn();
    const { rerender } = render(<HomeAsk {...baseProps} query="What marks T cells?" onAsk={onAsk} />);
    await userEvent.click(screen.getByRole("button", { name: /Ask Gunther/ }));
    expect(onAsk).toHaveBeenCalledWith("What marks T cells?");
    rerender(<HomeAsk {...baseProps} query="CD3D" onAsk={onAsk} />);
    expect(screen.getByText("Or ask Gunther about this")).toBeInTheDocument();
    rerender(<HomeAsk {...baseProps} query="" onAsk={onAsk} />);
    expect(screen.queryByRole("button", { name: /Ask Gunther/ })).toBeNull();
  });

  it("shows the conversation, takes a follow-up and files it in a library", async () => {
    const onAsk = vi.fn();
    const onFile = vi.fn();
    const at = "2026-09-29T08:00:00Z";
    const context = { sourcesConsidered: 1, assertionsConsidered: 0, verifiedAssertions: 0, retrievalMode: "all" as const, responderMode: "model" as const, steps: [{ tool: "search_library" as const, label: "Searched your library for “CD3D”", query: "CD3D", found: 1, error: null }] };
    render(<HomeAsk {...baseProps} active query="" onAsk={onAsk} onFile={onFile} messages={[
      { id: "m1", sessionId: "s", role: "user", content: "Which marker?", citations: [], context, createdAt: at },
      { id: "m2", sessionId: "s", role: "assistant", content: "CD3D [1].", context, createdAt: at, citations: [
        { id: "c1", sourceId: "src_1", sourceTitle: "Cell note", assertionId: null, quote: "q", locator: "line 1", status: "provisional", confidence: 0 },
        { id: "c2", kind: "web", url: "https://example.org/x", sourceId: "", sourceTitle: "A page", assertionId: null, quote: "q", locator: "example.org", status: "provisional", confidence: 0 },
      ] },
    ]} />);
    expect(screen.getByText("Which marker?")).toBeInTheDocument();
    expect(screen.getByText("Searched your library for “CD3D”")).toBeInTheDocument();
    // A source opens its evidence at the right, with the way out to the page below it.
    await userEvent.click(screen.getByRole("button", { name: /Show evidence 2: A page/ }));
    expect(screen.getByRole("complementary", { name: "Evidence" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open example\.org/ })).toHaveAttribute("href", "https://example.org/x");
    await userEvent.click(screen.getByRole("button", { name: "Close evidence" }));
    expect(screen.queryByRole("complementary", { name: "Evidence" })).toBeNull();
    await userEvent.type(screen.getByRole("textbox", { name: "Ask a follow-up" }), "And its gene?{Enter}");
    expect(onAsk).toHaveBeenCalledWith("And its gene?");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "File in a library" }), "base_1");
    expect(onFile).toHaveBeenCalledWith("base_1");
  });

  it("can be stopped while it is answering", async () => {
    const onCancel = vi.fn();
    render(<HomeAsk {...baseProps} active query="" pending="Why?" live={{ intent: null, steps: [], text: "" }} onCancel={onCancel} />);
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
