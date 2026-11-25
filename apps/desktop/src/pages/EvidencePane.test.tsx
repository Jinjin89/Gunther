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
  sourceTitle: "Web page", assertionId: null, locator: "Public web", quote: "A web passage.", status: "provisional", confidence: 0 };
const earlier: ConversationCitation = { ...paper, id: "earlier", ref: 1, sourceTitle: "Earlier paper", locator: "Source text" };
const answer = (id: string, citations: ConversationCitation[]): SessionMessage => ({ id, sessionId: "chat", role: "assistant",
  content: "Answer", citations, createdAt: at,
  context: { sourcesConsidered: 2, assertionsConsidered: 1, verifiedAssertions: 1, retrievalMode: "all", responderMode: "model" } });
const previous = answer("previous", [earlier]);
const selected: SessionMessage = { ...answer("selected", [paper, web]), context: { ...answer("selected", []).context,
  steps: [{ tool: "search_web", label: "Searched the web", query: "question", found: 2, error: null }],
  coverage: { status: "complete", considered: 3, checked: 3, unchecked: 0, claims: [
    { id: "partial", n: 1, text: "The readings matched [p:1].", refs: [2], verdict: "partly", reason: "Only the morning was measured." },
    { id: "supported", n: 2, text: "A second finding [2].", refs: [3], verdict: "supported", reason: "" },
    { id: "transition", n: 3, text: "Here is what comes next.", refs: [], verdict: "nonfactual", reason: "" },
  ] },
  research: { state: "done", budget: "standard", used: { searches: 1, reads: 1 }, limits: { searches: 4, reads: 4 }, coreQuestion: "What changed?", doneWhen: "" },
} };
const session: KnowledgeSession = { id: "chat", knowledgeBaseId: "library", title: "Chat", summary: "", focusChapterId: null,
  selectedSourceIds: [], pinned: false, archived: false, parentSessionId: null, branchedFromMessageId: null,
  messageCount: 2, createdAt: at, updatedAt: at, messages: [previous, selected] };
const handlers = () => ({ onOpen: vi.fn(), onStep: vi.fn(), onBack: vi.fn(), onOpenSource: vi.fn(), onOriginalPdfChange: vi.fn(), onSourceCount: vi.fn() });
const props = () => ({ session, selected, chatVisible: true, sources: [source], selectedSourceIds: [source.id], allSelected: true,
  webEnabled: false, onFocusSources: vi.fn(), detail: null as EvidenceTarget | null, ...handlers() });

describe("EvidencePane", () => {
  it("shows current scope and Web status for an empty chat", () => {
    const values = props();
    const { rerender } = render(<EvidencePane {...values} selected={null} />);
    expect(screen.getByText("All 1 source")).toBeVisible();
    expect(screen.getByText("1 paper")).toBeVisible();
    expect(screen.getByText("Web off")).toBeVisible();
    rerender(<EvidencePane {...values} selected={null} allSelected={false} selectedSourceIds={[]} webEnabled />);
    expect(screen.getByText("0 of 1 selected")).toBeVisible();
    expect(screen.queryByText("1 paper")).not.toBeInTheDocument();
    expect(screen.getByText("Web on")).toBeVisible();
  });

  it("only offers Brief when there is content, an update, an error, or messages", () => {
    const values = props();
    const empty = { ...session, messages: [], messageCount: 0,
      brief: { goal: "", constraints: [], settled: [], open: [], edited: [], error: null } };
    const brief = { updating: false, readOnly: false, save: vi.fn(), retry: vi.fn() };
    const { rerender } = render(<EvidencePane {...values} session={empty} selected={null} brief={brief} />);
    expect(screen.queryByRole("heading", { name: "Brief" })).toBeNull();
    rerender(<EvidencePane {...values} session={empty} selected={null} brief={{ ...brief, updating: true }} />);
    expect(screen.getByText("Updating…")).toBeVisible();
    rerender(<EvidencePane {...values} session={{ ...empty, brief: { ...empty.brief, goal: "A saved goal" } }} selected={null} brief={brief} />);
    expect(screen.getByText("A saved goal")).toBeVisible();
  });

  it("opens the owning citation list from Brief and prefers the selected answer for repeated refs", async () => {
    const user = userEvent.setup();
    const values = props();
    const old = answer("old", [earlier, { ...paper, sourceTitle: "Earlier version" }]);
    const conversation = { ...session, messages: [old, selected], brief: {
      goal: "Compare sources", constraints: [], open: [], edited: [], error: null,
      settled: [{ text: "Both readings support the comparison", by: "sources" as const, refs: [1, 2], because: "" }],
    } };
    const brief = { updating: false, readOnly: false, save: vi.fn(), retry: vi.fn() };
    render(<EvidencePane {...values} session={conversation} brief={brief} />);
    const card = screen.getByRole("region", { name: "Brief" });
    expect(card).toBe(card.parentElement?.firstElementChild);
    await user.click(within(card).getByRole("button", { name: "[1]" }));
    expect(values.onOpen).toHaveBeenLastCalledWith(old.citations, 0);
    await user.click(within(card).getByRole("button", { name: "[2]" }));
    expect(values.onOpen).toHaveBeenLastCalledWith(selected.citations, 0);
  });

  it("labels an older answer's conclusions and retains them alongside the newest three", async () => {
    const user = userEvent.setup();
    const values = props();
    const names = ["First dated result", "Second dated result", "Undated result", "New result A", "New result B", "New result C"];
    const conversation: KnowledgeSession = { ...session, brief: {
      goal: "", constraints: [], open: [], edited: [], error: null, throughMessageId: selected.id,
      settled: names.map((text, index) => ({ text, refs: [], by: "you", because: "", messageId: index < 2 ? previous.id : null })),
      lastChange: { messageId: selected.id, goalChanged: false,
        added: { constraints: [], settled: ["New result C"], open: [] }, removed: { constraints: [], open: ["What changed?"] } },
    } };
    const brief = { updating: false, readOnly: false, save: vi.fn(), retry: vi.fn() };
    const { rerender } = render(<EvidencePane {...values} session={conversation} brief={brief} />);
    const card = within(screen.getByRole("region", { name: "Brief" }));
    expect(card.getByText("After the last answer: +1 settled · 1 question closed")).toBeVisible();
    expect(card.queryByText("First dated result")).toBeNull();

    rerender(<EvidencePane {...values} session={conversation} brief={brief} selected={previous} />);
    expect(card.queryByText(/After the last answer/)).toBeNull();
    expect(card.queryByText("New")).toBeNull();
    expect(card.queryByText("Closed", { selector: "summary" })).toBeNull();
    expect(card.getAllByText("from this answer")).toHaveLength(2);
    expect(card.getAllByRole("listitem")).toEqual(names.filter((_, index) => index !== 2).map((name) => card.getByText(name).closest("li")));
    await user.click(card.getByRole("button", { name: "Show all 6" }));
    expect(card.getByText("Undated result")).toBeVisible();
    await user.click(card.getByRole("button", { name: "Show fewer" }));
    expect(card.queryByText("Undated result")).toBeNull();
  });

  it("shows the saved answer check, its process, and citations with the answer's numbering", async () => {
    const user = userEvent.setup();
    const values = props();
    const trace = vi.fn();
    render(<EvidencePane {...values} traceEnabled onOpenTrace={trace} />);
    const check = within(screen.getByRole("region", { name: "Answer check" }));
    const coverage = check.getByText("1 of 2 claims verified · 1 partial");
    expect(coverage.closest("details")).not.toHaveAttribute("open");
    await user.click(coverage);
    expect(check.getByText("The readings matched.")).toBeVisible();
    expect(check.getByText("Only the morning was measured.")).toBeVisible();
    const process = check.getByText("Process");
    expect(process.closest("details")).not.toHaveAttribute("open");
    expect(check.getByText("1 search · 1 step")).toBeVisible();
    await user.click(process);
    expect(check.getByText("Searched the web")).toBeVisible();
    expect(check.getByText("Research plan").closest(".evidence-disclosure")).toBe(process.closest("details"));
    await user.click(check.getByRole("button", { name: "View trace" }));
    expect(trace).toHaveBeenCalledOnce();
    const cited = within(screen.getByRole("region", { name: "Cited sources" }));
    const paperCard = cited.getByRole("button", { name: /A paper passage/ });
    const webCard = cited.getByRole("button", { name: /A web passage/ });
    expect(paperCard.querySelector(".gx-kind-icon.kind-paper")).toBeInTheDocument();
    expect(paperCard).toHaveTextContent("Paper · Page 2");
    expect(within(paperCard).getByText("[1]")).toBeVisible();
    expect(paperCard).toHaveTextContent("Partly supported");
    expect(webCard).toHaveTextContent("Web · example.org");
    expect(within(webCard).getByText("[2]")).toBeVisible();
    expect(webCard).not.toHaveTextContent("Partly supported");
    await user.click(webCard);
    expect(values.onOpen).toHaveBeenCalledWith(selected.citations, 1);
    const earlierSources = screen.getByText("Earlier in this conversation");
    expect(earlierSources.closest("details")).not.toHaveAttribute("open");
    await user.click(earlierSources);
    const earlierCard = screen.getByRole("button", { name: /Earlier paper/ });
    expect(earlierCard).not.toHaveTextContent("Source text");
    await user.click(earlierCard);
    expect(values.onOpen).toHaveBeenLastCalledWith([earlier], 0);
  });

  it("shows verified coverage without a disclosure and keeps older aggregate checks readable", () => {
    const values = props();
    const base = selected.context.coverage!;
    const supported = { ...base, claims: base.claims.map((claim) => claim.verdict === "partly"
      ? { ...claim, verdict: "supported" } : claim) };
    const { rerender } = render(<EvidencePane {...values} selected={{ ...selected, context: { ...selected.context, coverage: supported } }} />);
    expect(screen.getByText("All 2 claims verified").closest("details")).toBeNull();

    const aggregate = { ...base, status: "partial" as const, checked: 1, unchecked: 1, considered: 2, claims: [] };
    rerender(<EvidencePane {...values} selected={{ ...selected, context: { ...selected.context, coverage: aggregate } }} />);
    const legacy = screen.getByText("Partially checked · 1 unchecked");
    expect(legacy.closest(".evidence-coverage")?.querySelector(".evidence-coverage-track")).toBeNull();
  });

  it("prefers the dispute mark when multiple claims flag a legacy citation", () => {
    const values = props();
    const citation = { ...paper, ref: null };
    const verdicts = ["disputed", "partly"];
    const coverage = { status: "complete" as const, considered: 2, checked: 2, unchecked: 0,
      claims: verdicts.map((verdict, index) => ({ id: verdict, n: index + 1, text: "Result", refs: [1], verdict, reason: "" })) };
    render(<EvidencePane {...values} selected={{ ...selected, citations: [citation], context: { ...selected.context, coverage } }} />);
    const cited = within(screen.getByRole("region", { name: "Cited sources" }));
    expect(cited.getByText("Disputed").className).toContain("is-danger");
    expect(cited.queryByText("Partly supported")).toBeNull();
  });

  it("handles old answers and allows stepping back from a detail", async () => {
    const user = userEvent.setup();
    const values = props();
    const old = answer("old", []);
    const { rerender } = render(<EvidencePane {...values} selected={old} />);
    expect(screen.getByText("No sources needed")).toBeVisible();
    expect(screen.queryByRole("region", { name: "Answer check" })).toBeNull();
    rerender(<EvidencePane {...values} detail={{ contextKey: "chat", citations: [paper, web], index: 1 }} />);
    expect(screen.getByText("2 / 2")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Previous source" }));
    await user.click(screen.getByRole("button", { name: "All 2 sources" }));
    expect(values.onStep).toHaveBeenCalledWith(0);
    expect(values.onBack).toHaveBeenCalledOnce();
  });
});
