import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeBase, makeInboxItem } from "../test/fixtures";
import { InboxPageV3 } from "./InboxPage";

const api = vi.hoisted(() => ({
  inbox: vi.fn(),
  fileSource: vi.fn(),
  fileNote: vi.fn(),
  updateSourceAssertionStatuses: vi.fn(),
  updateProposal: vi.fn(),
}));

vi.mock("../api", () => ({
  knowledgeApi: api,
}));

const bases = [
  makeBase({ id: "biology", title: "Biology" }),
  makeBase({ id: "computing", title: "Computing" }),
];

const unfiled = makeInboxItem();
const sourceReview = makeInboxItem({
  id: "review-source",
  state: "needs_review",
  title: "Candidate findings",
  sourceId: "source-review",
  assertionCount: 2,
  knowledgeBases: [{ id: "biology", title: "Biology" }],
});
const heldProposal = makeInboxItem({
  id: "proposal-held",
  itemType: "knowledge_suggestion",
  state: "held",
  title: "Reusable explanation",
  sourceKind: null,
  sourceId: null,
  proposalId: "proposal-1",
  proposalStatus: "held",
  knowledgeBases: [{ id: "computing", title: "Computing" }],
});

const renderInbox = (overrides: Partial<React.ComponentProps<typeof InboxPageV3>> = {}) => {
  const props: React.ComponentProps<typeof InboxPageV3> = {
    bases,
    onOpenBase: vi.fn(),
    onOpenNote: vi.fn(),
    onCapture: vi.fn(),
    onCountChange: vi.fn(),
    onNotify: vi.fn(),
    ...overrides,
  };
  return { ...render(<InboxPageV3 {...props} />), props };
};

describe("InboxPageV3", () => {
  beforeEach(() => {
    api.inbox.mockResolvedValue([unfiled, sourceReview, heldProposal]);
    api.fileSource.mockResolvedValue({});
    api.fileNote.mockResolvedValue({});
    api.updateSourceAssertionStatuses.mockResolvedValue([]);
    api.updateProposal.mockResolvedValue({});
  });

  it("separates organize, review, and held states while excluding held items from attention count", async () => {
    const user = userEvent.setup();
    const { props } = renderInbox();

    expect(await screen.findByText("Unsorted lecture")).toBeVisible();
    expect(screen.getByText("Candidate findings")).toBeVisible();
    expect(screen.queryByText("Reusable explanation")).not.toBeInTheDocument();
    expect(props.onCountChange).toHaveBeenLastCalledWith(2);

    expect(screen.getByRole("button", { name: /Needs attention\s*2/i })).toBeVisible();
    expect(screen.getByRole("button", { name: /To organize\s*1/i })).toBeVisible();
    expect(screen.getByRole("button", { name: /To review\s*1/i })).toBeVisible();
    expect(screen.getByRole("button", { name: /Held\s*1/i })).toBeVisible();

    await user.click(screen.getByRole("button", { name: /Held\s*1/i }));
    expect(screen.getByText("Reusable explanation")).toBeVisible();
    expect(screen.queryByText("Unsorted lecture")).not.toBeInTheDocument();
  });

  it("files a source to the explicitly selected knowledge base and refreshes the inbox", async () => {
    const user = userEvent.setup();
    const onNotify = vi.fn();
    const updated = vi.fn();
    window.addEventListener("gunther:sources-updated", updated);
    const { props } = renderInbox({ onNotify });

    await screen.findByText("Unsorted lecture");
    await user.click(screen.getByRole("button", { name: /File to library: Biology/i }));
    await user.click(screen.getByRole("option", { name: /Computing/ }));
    await user.click(screen.getByRole("button", { name: /File source/i }));

    await waitFor(() => expect(api.fileSource).toHaveBeenCalledWith("source-1", "computing"));
    expect(api.inbox).toHaveBeenCalledTimes(2);
    expect(updated).toHaveBeenCalledOnce();
    expect(onNotify).toHaveBeenCalledWith("Filed “Unsorted lecture”. Any AI suggestions remain here for review.");
    expect(props.onOpenBase).not.toHaveBeenCalled();
    window.removeEventListener("gunther:sources-updated", updated);
  });

  it("records a grounded claim decision through the source review contract", async () => {
    const user = userEvent.setup();
    const onNotify = vi.fn();
    renderInbox({ onNotify });

    await screen.findByText("Candidate findings");
    await user.click(screen.getByRole("button", { name: /Accept 2/i }));

    await waitFor(() => expect(api.updateSourceAssertionStatuses).toHaveBeenCalledWith("source-review", {
      status: "verified",
      reason: "Accepted from unified Inbox",
    }));
    expect(api.inbox).toHaveBeenCalledTimes(2);
    expect(onNotify).toHaveBeenCalledWith("Claims accepted as trusted knowledge.");
  });

  it("can return a held knowledge suggestion to review without silently accepting it", async () => {
    const user = userEvent.setup();
    const onNotify = vi.fn();
    renderInbox({ onNotify });

    await screen.findByText("Unsorted lecture");
    await user.click(screen.getByRole("button", { name: /Held\s*1/i }));
    await user.click(screen.getByRole("button", { name: /Return to review/i }));

    await waitFor(() => expect(api.updateProposal).toHaveBeenCalledWith("proposal-1", {
      status: "pending",
      reason: "Returned to unified Inbox",
    }));
    expect(onNotify).toHaveBeenCalledWith("Suggestion returned for review.");
  });

  it("opens any row as its own page, with the visible list as the J / K queue", async () => {
    const user = userEvent.setup();
    const onOpenItem = vi.fn();
    renderInbox({ onOpenItem });

    await user.click(await screen.findByRole("button", { name: "Candidate findings" }));
    expect(onOpenItem).toHaveBeenCalledWith(
      { type: "source", id: "source-review" },
      [{ type: "source", id: "source-1" }, { type: "source", id: "source-review" }],
    );
  });

  it("moves between rows with J and K and opens the focused row with Enter", async () => {
    const user = userEvent.setup();
    const onOpenItem = vi.fn();
    renderInbox({ onOpenItem });

    await screen.findByText("Unsorted lecture");
    await user.keyboard("j");
    expect(screen.getByRole("button", { name: "Unsorted lecture" })).toHaveFocus();
    await user.keyboard("j");
    expect(screen.getByRole("button", { name: "Candidate findings" })).toHaveFocus();
    await user.keyboard("k");
    expect(screen.getByRole("button", { name: "Unsorted lecture" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onOpenItem).toHaveBeenCalledWith({ type: "source", id: "source-1" }, expect.any(Array));
  });

  it("returns focus to the row an item was opened from", async () => {
    renderInbox({ onOpenItem: vi.fn(), focusItemKey: "source:source-review" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Candidate findings" })).toHaveFocus());
  });

  it("surfaces service failure and recovers through an explicit retry", async () => {
    const user = userEvent.setup();
    api.inbox.mockRejectedValueOnce(new Error("Local service is offline")).mockResolvedValueOnce([]);
    const { props } = renderInbox();

    expect(await screen.findByText("Inbox is temporarily unavailable")).toBeVisible();
    expect(screen.getByText("Local service is offline")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("You’re all caught up.")).toBeVisible();
    expect(api.inbox).toHaveBeenCalledTimes(2);
    expect(props.onCountChange).toHaveBeenLastCalledWith(0);
  });
});
