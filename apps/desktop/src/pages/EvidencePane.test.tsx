import type { ContentsSource, ConversationCitation, KnowledgeSession, SessionMessage } from "@gunther/contracts";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeSource } from "../test/fixtures";
import { EvidencePane, type EvidenceTarget } from "./EvidencePane";

const at = "2026-10-01T00:05:00Z";
const source: ContentsSource = { ...makeSource({ id: "paper", title: "Paper", kind: "paper" }), durationSeconds: null,
  authors: null, year: null, pages: null, mediaType: null, host: null, processing: "ready" };
const paper: ConversationCitation = { id: "paper-cite", ref: 2, sourceId: "paper", sourceTitle: "Paper", assertionId: null,
  locator: "Page 2", quote: "A paper passage.", status: "verified", confidence: 1 };
const web: ConversationCitation = { id: "web-cite", ref: 3, kind: "web", url: "https://example.org/post", sourceId: "",
  sourceTitle: "Web page", assertionId: null, locator: "", quote: "A web passage.", status: "provisional", confidence: 0 };
const earlier: ConversationCitation = { ...paper, id: "earlier", ref: 1, sourceTitle: "Earlier paper" };
const answer = (id: string, citations: ConversationCitation[]): SessionMessage => ({ id, sessionId: "chat", role: "assistant",
  content: "Answer", citations, createdAt: at,
  context: { sourcesConsidered: 2, assertionsConsidered: 1, verifiedAssertions: 1, retrievalMode: "all", responderMode: "model" } });
const previous = answer("previous", [earlier]);
const selected: SessionMessage = { ...answer("selected", [paper, web]), context: { ...answer("selected", []).context,
  steps: [{ tool: "search_web", label: "Searched the web", query: "question", found: 2, error: null }],
  coverage: { status: "partial", considered: 2, checked: 1, unchecked: 1, claims: [] },
  research: { state: "done", budget: "standard", used: { searches: 1, reads: 1 }, limits: { searches: 4, reads: 4 }, coreQuestion: "What changed?", doneWhen: "" },
} };
const session: KnowledgeSession = { id: "chat", knowledgeBaseId: "library", title: "Chat", summary: "", focusChapterId: null,
  selectedSourceIds: [], pinned: false, archived: false, parentSessionId: null, branchedFromMessageId: null,
  messageCount: 2, createdAt: at, updatedAt: at, messages: [previous, selected] };
const handlers = () => ({ onOpen: vi.fn(), onStep: vi.fn(), onBack: vi.fn(), onOpenSource: vi.fn(), onOriginalPdfChange: vi.fn(), onSourceCount: vi.fn() });
const props = () => ({ session, selected, chatVisible: true, sources: [source], selectedCount: 1, allSelected: true,
  webEnabled: false, detail: null as EvidenceTarget | null, ...handlers() });

describe("EvidencePane", () => {
  it("shows current scope and Web status for an empty chat", () => {
    const values = props();
    const { rerender } = render(<EvidencePane {...values} selected={null} />);
    expect(screen.getByText("All 1 source")).toBeVisible();
    expect(screen.getByText("Web off")).toBeVisible();
    rerender(<EvidencePane {...values} selected={null} allSelected={false} selectedCount={0} webEnabled />);
    expect(screen.getByText("0 of 1 selected")).toBeVisible();
    expect(screen.getByText("Web on")).toBeVisible();
  });

  it("only offers Brief when there is content, an update, an error, or messages", async () => {
    const user = userEvent.setup();
    const values = props();
    const empty = { ...session, messages: [], messageCount: 0,
      brief: { goal: "", constraints: [], settled: [], open: [], edited: [], error: null } };
    const brief = { updating: false, readOnly: false, save: vi.fn(), retry: vi.fn() };
    const { rerender } = render(<EvidencePane {...values} session={empty} selected={null} brief={brief} />);
    expect(screen.queryByText("Brief", { selector: "summary" })).toBeNull();
    rerender(<EvidencePane {...values} session={empty} selected={null} brief={{ ...brief, updating: true }} />);
    await user.click(screen.getByText("Brief", { selector: "summary" }));
    expect(screen.getByText("Updating…")).toBeVisible();
    rerender(<EvidencePane {...values} session={{ ...empty, brief: { ...empty.brief, goal: "A saved goal" } }} selected={null} brief={brief} />);
    expect(screen.getByText("A saved goal")).toBeVisible();
  });

  it("shows saved method and citations in order, with earlier sources folded", async () => {
    const user = userEvent.setup();
    const values = props();
    render(<EvidencePane {...values} />);
    expect(screen.getByText("Source check: partial · 1 unchecked")).toBeVisible();
    expect(screen.getByText("Research plan")).toBeVisible();
    await user.click(screen.getByText("1 search · 1 step"));
    expect(screen.getByText("Searched the web")).toBeVisible();
    const cited = within(screen.getByRole("region", { name: "Cited sources" }));
    const cards = cited.getAllByRole("button");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("[1]PaperPaperPage 2A paper passage.");
    expect(cards[1]).toHaveTextContent("[2]WebWeb pageexample.orgA web passage.");
    await user.click(cards[1]!);
    expect(values.onOpen).toHaveBeenCalledWith(selected.citations, 1);
    await user.click(screen.getByText("Earlier in this conversation (1)"));
    expect(screen.getByRole("button", { name: /Earlier paper/ })).toBeVisible();
  });

  it("handles old answers and allows stepping back from a detail", async () => {
    const user = userEvent.setup();
    const values = props();
    const old = answer("old", []);
    const { rerender } = render(<EvidencePane {...values} selected={old} />);
    expect(screen.getByText("No sources needed")).toBeVisible();
    rerender(<EvidencePane {...values} detail={{ contextKey: "chat", citations: [paper, web], index: 1 }} />);
    expect(screen.getByText("[2 / 2]")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Previous source" }));
    await user.click(screen.getByRole("button", { name: "All 2 sources" }));
    expect(values.onStep).toHaveBeenCalledWith(0);
    expect(values.onBack).toHaveBeenCalledOnce();
  });
});
