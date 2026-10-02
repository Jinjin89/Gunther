import type { ConversationContext, SessionMessage } from "@gunther/contracts";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LiveAnswer, ResearchPlan, researchUsage } from "../../pages/AnswerBody";
import { applyAnswerEvent, type LiveAnswerState } from "../../pages/liveAnswer";
import { Composer, ConversationMessage } from "../../pages/SessionWorkspace";
import { forgetAskSkills, type SkillBudget } from "./skills";

const api = vi.hoisted(() => ({ askSkills: vi.fn() }));
vi.mock("../../api", () => ({ knowledgeApi: api }));

const skills = [
  { command: "compare", title: "Compare sources", description: "Set sources side by side.", budgets: [] },
  { command: "research", title: "Deep research", description: "Research a question in depth.", budgets: ["standard", "deep"] },
];

beforeEach(() => {
  forgetAskSkills();
  api.askSkills.mockReset();
  api.askSkills.mockResolvedValue(skills);
});

function Session({ initial, onSend, stopLabel, sending = false }: { initial: string | null; onSend: (skill: string | null, budget: SkillBudget) => void; stopLabel?: string; sending?: boolean }) {
  const [skill, setSkill] = useState<string | null>(initial);
  const [budget, setBudget] = useState<SkillBudget>("standard");
  return <Composer value="Why?" sending={sending} sourceCount={0} chapterTitle={undefined} onChange={vi.fn()} onSend={() => onSend(skill, budget)} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready skill={skill} onSkillChange={setSkill} budget={budget} onBudgetChange={setBudget} {...(stopLabel ? { stopLabel } : {})} />;
}

describe("the research chip", () => {
  it("offers Standard and Deep, Standard first, and sends the one chosen", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Session initial="research" onSend={onSend} />);
    const depth = await screen.findByRole("group", { name: "Research depth" });
    expect(screen.getByRole("button", { name: "Standard" })).toHaveAttribute("aria-pressed", "true");
    expect(depth).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Deep" }));
    expect(screen.getByRole("button", { name: "Deep" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(onSend).toHaveBeenCalledWith("research", "deep");
  });

  it("has no depth choice on a skill without budgets", async () => {
    render(<Session initial="compare" onSend={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "Remove /compare" })).toBeVisible();
    expect(screen.queryByRole("group", { name: "Research depth" })).not.toBeInTheDocument();
  });

  it("says Stop and write while research is still gathering", async () => {
    render(<Session initial={null} onSend={vi.fn()} sending stopLabel="Stop and write" />);
    expect(await screen.findByRole("button", { name: "Stop and write" })).toHaveTextContent("Stop and write");
  });

  it("keeps the plain Stop for everything else", async () => {
    render(<Session initial={null} onSend={vi.fn()} sending />);
    expect(await screen.findByRole("button", { name: "Stop waiting for response" })).toBeVisible();
  });
});

describe("a research run, as it is heard", () => {
  const start: LiveAnswerState = { steps: [], text: "" };
  const plan = { type: "research_plan" as const, coreQuestion: "How well do cool roofs work?", subQuestions: [{ id: "q1", text: "What did the trial measure?" }, { id: "q2", text: "Do other sources disagree?" }], doneWhen: "x", budget: "standard", limits: { searches: 8, reads: 4 } };

  it("keeps the plan and the latest budget used", () => {
    let state = applyAnswerEvent(start, plan);
    expect(state.research?.subQuestions).toHaveLength(2);
    state = applyAnswerEvent(state, { type: "step", state: "running", tool: "search_library", label: "Searched your library for “x”", used: { searches: [1, 8], reads: [0, 4] } });
    expect(state.research?.used).toEqual({ searches: [1, 8], reads: [0, 4] });
    expect(state.steps).toHaveLength(1);
  });

  it("shows the plan and the usage line above the steps", () => {
    let state = applyAnswerEvent(start, plan);
    state = applyAnswerEvent(state, { type: "step", state: "done", tool: "search_library", label: "Searched your library for “x”", found: 2, used: { searches: [5, 8], reads: [2, 4] } });
    render(<LiveAnswer state={state} />);
    expect(screen.getByText("How well do cool roofs work?")).toBeVisible();
    expect(screen.getByText("What did the trial measure?")).toBeVisible();
    expect(screen.getByText("Searches 5/8 · Reads 2/4")).toBeVisible();
  });

  it("shows the budget at zero before the first step", () => {
    render(<LiveAnswer state={applyAnswerEvent(start, plan)} />);
    expect(screen.getByText("Searches 0/8 · Reads 0/4")).toBeVisible();
  });

  it("writes the usage line the same way for a saved answer", () => {
    expect(researchUsage({ searches: [3, 16], reads: [1, 8] })).toBe("Searches 3/16 · Reads 1/8");
  });
});

const context = (patch: Partial<ConversationContext>): ConversationContext => ({
  sourcesConsidered: 1, assertionsConsidered: 1, verifiedAssertions: 1, retrievalMode: "all", responderMode: "model", modelLabel: "DeepSeek", ...patch,
});
const research = (state: "asking" | "done" | "stopped_early") => ({ state, budget: "standard" as const, used: { searches: 6, reads: 2 }, limits: { searches: 8, reads: 4 }, coreQuestion: "How well do cool roofs work?", doneWhen: "x" });
const work = { subQuestions: [{ id: "q1", text: "What did the trial measure?", query: "" }, { id: "q2", text: "Do other sources disagree?", query: "" }], findings: [{ ref: 1, serves: "q1", says: "a" }, { title: "Other", serves: "q1", says: "b" }, { ref: 2, serves: "q2", says: "c" }] };

describe("a saved research answer", () => {
  it("folds its plan away under Research plan, with what each sub-question found", async () => {
    const user = userEvent.setup();
    const { container } = render(<ResearchPlan context={context({ research: research("done"), work })} />);
    const block = container.querySelector("details")!;
    expect(block).not.toHaveAttribute("open");
    await user.click(screen.getByText("Research plan"));
    expect(block).toHaveAttribute("open");
    expect(screen.getByText("What did the trial measure?")).toBeVisible();
    expect(screen.getByText("2 sources")).toBeVisible();
    expect(screen.getByText("1 source")).toBeVisible();
    expect(screen.getByText("Searches 6/8 · Reads 2/4")).toBeVisible();
  });

  it("has no plan when it was only asking the reader something", () => {
    const { container } = render(<ResearchPlan context={context({ research: research("asking") })} />);
    expect(container).toBeEmptyDOMElement();
  });

  const message = (patch: Partial<ConversationContext>): SessionMessage => ({
    id: "msg_1", sessionId: "ses_1", role: "assistant", content: "An answer.", citations: [], createdAt: "2026-10-02T08:00:00.000Z", context: context(patch),
  });
  const show = (patch: Partial<ConversationContext>, onMakeReport?: () => void) => render(<ConversationMessage message={message(patch)} retryDisabled={false} onRetry={vi.fn()} onEdit={vi.fn()} onCite={vi.fn()} selected={false} promoting={false} promoted={false} branching={false} onSelect={vi.fn()} onCopy={vi.fn()} onPromote={vi.fn()} onRemove={vi.fn()} onBranch={vi.fn()} onMakeReport={onMakeReport} />);

  it("offers a report from it, and only from a research answer", async () => {
    const onMakeReport = vi.fn();
    const user = userEvent.setup();
    const { unmount } = show({ research: research("done"), work }, onMakeReport);
    await user.click(screen.getByRole("button", { name: "Make a report from this" }));
    expect(onMakeReport).toHaveBeenCalledOnce();
    unmount();
    show({}, onMakeReport);
    expect(screen.queryByRole("button", { name: "Make a report from this" })).not.toBeInTheDocument();
  });

  it("offers no report from questions the reader still has to answer", () => {
    show({ research: research("asking") }, vi.fn());
    expect(screen.queryByRole("button", { name: "Make a report from this" })).not.toBeInTheDocument();
    expect(screen.queryByText("Not from your sources")).not.toBeInTheDocument();
  });
});
