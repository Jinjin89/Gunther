import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Composer, ConversationMessage } from "../../pages/SessionWorkspace";
import { SearchComposer } from "./SearchComposer";
import { forgetAskSkills, matchSkills } from "./skills";

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

function Home({ onAsk, onSubmit }: { onAsk: () => void; onSubmit: () => void }) {
  const [value, setValue] = useState("");
  const [skill, setSkill] = useState<string | null>(null);
  return <>
    <SearchComposer bases={[]} value={value} mentionIds={[]} web={false} searching={false} onValueChange={setValue} onMentionsChange={() => undefined} onWebChange={() => undefined} onSubmit={onSubmit} onAsk={onAsk} onClear={() => { setValue(""); setSkill(null); }} skill={skill} onSkillChange={setSkill} />
    <output aria-label="skill">{skill}</output>
  </>;
}

const box = () => screen.getByRole("textbox", { name: "Search your knowledge or the web" });

describe("the / menu in the Home composer", () => {
  it("opens only when / is the first character", async () => {
    const user = userEvent.setup();
    render(<Home onAsk={vi.fn()} onSubmit={vi.fn()} />);
    await screen.findByRole("textbox");
    await user.type(box(), "what is a/");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await user.clear(box());
    await user.type(box(), "/");
    expect(await screen.findByRole("listbox", { name: "Skills" })).toBeVisible();
    expect(screen.getByRole("option", { name: "Compare sources, /compare" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: "Deep research, /research" })).toBeVisible();
  });

  it("lists the skills that match what is typed, by command or title", async () => {
    const user = userEvent.setup();
    render(<Home onAsk={vi.fn()} onSubmit={vi.fn()} />);
    await user.type(box(), "/deep");
    const menu = await screen.findByRole("listbox", { name: "Skills" });
    expect(menu).toHaveTextContent("Deep research");
    expect(menu).not.toHaveTextContent("Compare sources");
  });

  it("picks with the arrow keys and Enter, takes the typed /word out, and shows a chip", async () => {
    const user = userEvent.setup();
    render(<Home onAsk={vi.fn()} onSubmit={vi.fn()} />);
    await user.type(box(), "/");
    await screen.findByRole("listbox", { name: "Skills" });
    await user.keyboard("{ArrowDown}{Enter}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove /research" })).toBeVisible();
    expect(screen.getByLabelText("skill")).toHaveTextContent("research");
    expect(box()).toHaveValue("");
  });

  it("picks with Tab and keeps the text that follows", async () => {
    const user = userEvent.setup();
    render(<Home onAsk={vi.fn()} onSubmit={vi.fn()} />);
    await user.type(box(), "/comp");
    await screen.findByRole("option", { name: /Compare sources/ });
    await user.keyboard("{Tab}");
    expect(screen.getByRole("button", { name: "Remove /compare" })).toBeVisible();
    await user.type(box(), "Harlow and Okafor");
    expect(box()).toHaveValue("Harlow and Okafor");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes on Escape without picking", async () => {
    const user = userEvent.setup();
    render(<Home onAsk={vi.fn()} onSubmit={vi.fn()} />);
    await user.type(box(), "/");
    await screen.findByRole("listbox", { name: "Skills" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByLabelText("skill")).toBeEmptyDOMElement();
    expect(box()).toHaveValue("/");
  });

  it("takes the skill off with Backspace at the start, or with the ×", async () => {
    const user = userEvent.setup();
    render(<Home onAsk={vi.fn()} onSubmit={vi.fn()} />);
    await user.type(box(), "/comp{Enter}");
    expect(screen.getByRole("button", { name: "Remove /compare" })).toBeVisible();
    await user.keyboard("{Backspace}");
    expect(screen.queryByRole("button", { name: "Remove /compare" })).not.toBeInTheDocument();

    await user.type(box(), "/comp{Enter}");
    await user.click(screen.getByRole("button", { name: "Remove /compare" }));
    expect(screen.getByLabelText("skill")).toBeEmptyDOMElement();
  });

  it("asks, instead of searching, while a skill is chosen", async () => {
    const onAsk = vi.fn();
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<Home onAsk={onAsk} onSubmit={onSubmit} />);
    await user.type(box(), "/comp{Enter}");
    await user.type(box(), "Compare Harlow and Okafor{Enter}");
    expect(onAsk).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("says so when no skill matches, and works with no skills at all", async () => {
    const user = userEvent.setup();
    api.askSkills.mockRejectedValue(new Error("offline"));
    render(<Home onAsk={vi.fn()} onSubmit={vi.fn()} />);
    await user.type(box(), "/");
    expect(await screen.findByText("No skills yet.")).toBeVisible();
  });
});

describe("matching skills", () => {
  it("puts a command prefix before a title word and drops what does not match", () => {
    expect(matchSkills(skills, "res").map((s) => s.command)).toEqual(["research"]);
    expect(matchSkills(skills, "sources").map((s) => s.command)).toEqual(["compare"]);
    expect(matchSkills(skills, "").map((s) => s.command)).toEqual(["compare", "research"]);
    expect(matchSkills(skills, "zzz")).toEqual([]);
  });
});

function Session({ onSend }: { onSend: (value: string, skill: string | null) => void }) {
  const [value, setValue] = useState("");
  const [skill, setSkill] = useState<string | null>(null);
  return <Composer value={value} sending={false} sourceCount={0} chapterTitle={undefined} onChange={setValue} onSend={() => onSend(value, skill)} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready skill={skill} onSkillChange={setSkill} />;
}

describe("the / menu in the session composer", () => {
  const field = () => screen.getByRole("textbox", { name: "Message Gunther" });

  it("opens at the start, picks a skill, and sends with it", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Session onSend={onSend} />);
    await user.type(field(), "x /");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await user.clear(field());
    await user.type(field(), "/");
    expect(await screen.findByRole("listbox", { name: "Skills" })).toBeVisible();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Remove /compare" })).toBeVisible();
    await user.type(field(), "Compare them{Enter}");
    expect(onSend).toHaveBeenCalledWith("Compare them", "compare");
  });

  it("takes the skill off with Backspace at the start", async () => {
    const user = userEvent.setup();
    render(<Session onSend={vi.fn()} />);
    await user.type(field(), "/comp{Enter}");
    await user.keyboard("{Backspace}");
    expect(screen.queryByRole("button", { name: "Remove /compare" })).not.toBeInTheDocument();
  });

  it("keeps Enter for sending when no menu is open", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Session onSend={onSend} />);
    await user.type(field(), "Hello{Enter}");
    expect(onSend).toHaveBeenCalledWith("Hello", null);
  });
});

describe("answers that followed a skill", () => {
  const message = (skill: { name: string; version: number; title: string; auto: boolean } | null) => ({
    id: "msg_1", sessionId: "ses_1", role: "assistant" as const, content: "An answer.", citations: [], createdAt: "2026-10-01T08:00:00.000Z",
    context: { sourcesConsidered: 1, assertionsConsidered: 1, verifiedAssertions: 1, retrievalMode: "all" as const, responderMode: "model" as const, modelLabel: "DeepSeek", skill },
  });
  const show = (skill: Parameters<typeof message>[0]) => render(<ConversationMessage message={message(skill)} retryDisabled={false} onRetry={vi.fn()} onEdit={vi.fn()} onCite={vi.fn()} selected={false} promoting={false} promoted={false} branching={false} onSelect={vi.fn()} onCopy={vi.fn()} onPromote={vi.fn()} onRemove={vi.fn()} onBranch={vi.fn()} />);

  it("says which skill was used beside the model", () => {
    show({ name: "compare", version: 1, title: "Compare sources", auto: false });
    expect(screen.getByText("· Used: Compare sources")).toBeVisible();
  });

  it("says when Ask picked it", () => {
    show({ name: "compare", version: 1, title: "Compare sources", auto: true });
    expect(screen.getByText("· Used: Compare sources (picked by Ask)")).toBeVisible();
  });

  it("says nothing without one", () => {
    show(null);
    expect(screen.queryByText(/Used:/)).not.toBeInTheDocument();
  });
});
