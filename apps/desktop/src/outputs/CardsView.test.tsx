import type { Artifact } from "@gunther/contracts";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { CardsView } from "./CardsView";
import { outputViews } from "./OutputViews";
import { PrintRoot } from "./PrintRoot";
import documentSections from "./fixtures/document-sections.json";
import type { OutputContentProps } from "./OutputParts";

const flash = "## What did the seal protect?\nThe sample [1].";
const quiz = "## (quiz) When did the test end? [1]\n- [x] Noon [1]\n- [ ] Midnight\nWhy: The log records an end time of 12:00 [1].";
const secondQuiz = "## (quiz) Where was it logged?\n- [ ] A notebook\n- [x] A register\nWhy: The entry is in the register [1].";

function savedCards(content: string) {
  return {
    id: "cards-1", kind: "cards", title: "Trial cards", versionNumber: 1, createdAt: "2026-10-15T09:00:00Z",
    outline: [], sections: [], content,
    citations: [{ id: "source-1", sourceTitle: "Test log", quote: "The log records an end time of 12:00." }],
  } as unknown as Artifact;
}

function show(content: string, props: Omit<OutputContentProps, "artifact"> = {}) {
  const artifact = savedCards(content);
  const onCite = vi.fn();
  const { rerender } = render(<CardsView artifact={artifact} onCite={onCite} {...props} />);
  const region = screen.getByRole("region", { name: "Card reader" });
  return {
    reader: within(region), region, onCite, artifact,
    update: (next: Omit<OutputContentProps, "artifact">) => rerender(<CardsView artifact={artifact} onCite={onCite} {...props} {...next} />),
  };
}

it("reveals a flashcard with its button and Space and navigates only within practice", async () => {
  const user = userEvent.setup();
  const { reader, region, onCite, artifact } = show(`${flash}\n\n## When was it read?\nAt noon.`);
  render(<button type="button">Outside practice</button>);
  await user.click(screen.getByRole("button", { name: "Outside practice" }));
  await user.keyboard("{ArrowRight}");
  expect(reader.getByRole("heading", { name: "What did the seal protect?" })).toBeVisible();
  expect(reader.getByText("1 of 2")).toBeVisible();
  expect(reader.getByRole("button", { name: "Previous" })).toBeDisabled();
  expect(reader.queryByText("The sample")).not.toBeInTheDocument();

  await user.click(reader.getByRole("button", { name: "Show answer" }));
  expect(reader.getByRole("button", { name: "Inspect citation 1" })).toBeVisible();
  await user.keyboard("[Space]");
  expect(reader.queryByRole("button", { name: "Inspect citation 1" })).not.toBeInTheDocument();
  region.focus();
  await user.keyboard("[Space]");
  const citation = reader.getByRole("button", { name: "Inspect citation 1" });
  expect(citation).toHaveAttribute("data-opens-evidence");
  await user.click(citation);
  expect(onCite).toHaveBeenCalledExactlyOnceWith(artifact.citations[0], 0);

  await user.click(reader.getByRole("button", { name: "Next" }));
  expect(region).toHaveFocus();
  expect(reader.getByRole("heading", { name: "When was it read?" })).toBeVisible();
  expect(reader.getByText("2 of 2")).toBeVisible();
  expect(reader.getByRole("button", { name: "Next" })).toBeDisabled();
  await user.keyboard("{ArrowRight}{ArrowLeft}");
  expect(reader.getByRole("heading", { name: "What did the seal protect?" })).toBeVisible();
  expect(reader.getByRole("button", { name: "Show answer" })).toBeVisible();
  await user.keyboard("{ArrowRight}");
  expect(reader.getByText("2 of 2")).toBeVisible();
});

it("reveals on a question tap while leaving selectable text, links and citations usable", async () => {
  const user = userEvent.setup();
  const { reader, region, onCite, artifact } = show("## Did you read the [notes](#notes)? [1]\nThe sample [1].");
  const heading = reader.getByRole("heading");
  const prompt = heading.closest(".cards-prompt");
  const mark = heading.querySelector("button[data-opens-evidence]");
  expect(mark).toBeInTheDocument();
  expect(prompt).toHaveAttribute("data-reveal", "false");
  const selection = document.getSelection()!;
  const range = document.createRange();
  range.selectNodeContents(heading);
  selection.removeAllRanges();
  selection.addRange(range);
  try {
    expect(selection.toString()).toContain("Did you read the notes?");
    fireEvent.click(heading);
    expect(reader.getByRole("button", { name: "Show answer" })).toBeVisible();
  } finally {
    selection.removeAllRanges();
  }
  await user.click(reader.getByRole("link", { name: "notes" }));
  expect(reader.getByRole("button", { name: "Show answer" })).toBeVisible();
  await user.click(heading);
  const toggle = reader.getByRole("button", { name: "Hide answer" });
  expect(region.querySelector(`[id="${toggle.getAttribute("aria-controls")}"]`)).toHaveTextContent("The sample");
  const citation = within(heading).getByRole("button", { name: "Inspect citation 1" });
  expect(citation).toBe(mark);
  await user.click(citation);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(onCite).toHaveBeenCalledExactlyOnceWith(artifact.citations[0], 0);
  await user.click(toggle);
  expect(prompt).toHaveAttribute("data-reveal", "false");
  expect(mark).toBeInTheDocument();
});

it("brings the mounted prompt below the workspace bar after Next and a history jump", () => {
  const answer = "A detailed answer with a supporting paragraph.\n\n".repeat(40);
  const artifact = savedCards(`## First tall card\n${answer}## Second tall card\n${answer}`);
  const workspace = (goTo?: OutputContentProps["goTo"]) => <div className="library-middle-page">
    <header className="library-middle-bar">Workspace bar</header>
    <CardsView artifact={artifact} goTo={goTo} />
  </div>;
  const { container, rerender } = render(workspace());
  const pane = container.querySelector<HTMLElement>(".library-middle-page")!;
  const bar = container.querySelector(".library-middle-bar");
  const measured: string[] = [];
  const bounds = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this === pane) return new DOMRect(0, 100, 640, 500);
    if (this === bar) return new DOMRect(0, 100, 640, 44);
    if (!this.classList.contains("cards-prompt")) return new DOMRect();
    measured.push(this.textContent ?? "");
    return new DOMRect(32, 220 - pane.scrollTop, 560, 60);
  });
  try {
    fireEvent.click(screen.getByRole("button", { name: "Show answer" }));
    pane.scrollTop = 1600;
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("heading", { name: "Second tall card" })).toBeVisible();
    expect(pane.scrollTop).toBe(76);

    fireEvent.click(screen.getByRole("button", { name: "Show answer" }));
    pane.scrollTop = 1730;
    rerender(workspace({ index: 0, nonce: 1 }));
    expect(screen.getByRole("heading", { name: "First tall card" })).toBeVisible();
    expect(pane.scrollTop).toBe(76);
    expect(measured).toEqual(["Second tall card", "First tall card"]);
  } finally {
    bounds.mockRestore();
  }
});

it("leaves Left and Right to a focused equation with horizontal overflow", () => {
  const { reader, region } = show("## First card\n$$\nx^2 + y^2 = z^2\n$$\n\n## Second card\nAnother answer.");
  fireEvent.click(reader.getByRole("button", { name: "Show answer" }));
  const equation = reader.getByRole("region", { name: "Equation" });
  equation.style.overflowX = "auto";
  Object.defineProperties(equation, { clientWidth: { value: 120 }, scrollWidth: { value: 480 } });
  equation.focus();
  expect(fireEvent.keyDown(equation, { key: "ArrowLeft" })).toBe(true);
  expect(fireEvent.keyDown(equation, { key: "ArrowRight" })).toBe(true);
  expect(equation).toHaveFocus();
  expect(reader.getByRole("heading", { name: "First card" })).toBeVisible();
  region.focus();
  fireEvent.keyDown(region, { key: "ArrowRight" });
  expect(reader.getByRole("heading", { name: "Second card" })).toBeVisible();
});

it("locks the same choice rows, grades once per quiz and resets the score", async () => {
  const user = userEvent.setup();
  const { reader } = show(`${quiz}\n\n${flash}\n\n${secondQuiz}`);
  const choices = within(reader.getByRole("list", { name: "Choices" }));
  const rows = choices.getAllByRole("listitem");
  const announcement = reader.getByRole("status");
  expect(reader.queryByText("[1]")).not.toBeInTheDocument();
  expect(reader.queryByText("Why:")).not.toBeInTheDocument();
  expect(reader.queryByRole("button", { name: "Start again" })).not.toBeInTheDocument();
  await user.dblClick(choices.getByRole("button", { name: "Noon" }));
  expect(choices.getAllByRole("listitem")).toEqual(rows);
  expect(choices.getByText("Correct · your answer").closest("li")).toHaveTextContent("Noon");
  expect(announcement).toHaveTextContent("Card 1. Correct.");
  expect(reader.getByText("1 of 1 correct")).toBeVisible();
  expect(reader.getAllByRole("button", { name: "Inspect citation 1" })).toHaveLength(3);
  expect(reader.getByText("Why:")).toBeVisible();
  expect(choices.queryByRole("button", { name: "Midnight" })).not.toBeInTheDocument();

  await user.click(reader.getByRole("button", { name: "Next" }));
  await user.click(reader.getByRole("button", { name: "Show answer" }));
  expect(reader.getByText("1 of 1 correct")).toBeVisible();
  await user.click(reader.getByRole("button", { name: "Next" }));
  expect(reader.queryByRole("button", { name: "Inspect citation 1" })).not.toBeInTheDocument();
  const wrong = reader.getByRole("button", { name: "A notebook" });
  wrong.focus();
  await user.keyboard("{Enter}");
  expect(reader.getByText("Incorrect")).toBeVisible();
  expect(announcement).toHaveTextContent("Card 3. Incorrect.");
  expect(reader.getByText("Your answer").closest("li")).toHaveTextContent("A notebook");
  expect(reader.getByText("Correct answer").closest("li")).toHaveTextContent("A register");
  expect(reader.getByText("1 of 2 correct")).toBeVisible();
  expect(reader.getByRole("button", { name: "Next" })).toBeDisabled();
  await user.keyboard("{ArrowLeft}{ArrowLeft}");
  expect(reader.getByText("Correct", { exact: true })).toBeVisible();
  expect(announcement).toHaveTextContent("Card 3. Incorrect.");
  expect(reader.getByText("1 of 2 correct")).toBeVisible();

  await user.keyboard("{ArrowRight}{ArrowRight}");
  await user.click(reader.getByRole("button", { name: "All cards" }));
  expect(reader.getByRole("list", { name: "All cards" })).toBeVisible();
  expect(reader.getByText("1 of 2 correct")).toBeVisible();
  await user.click(reader.getByRole("button", { name: "Start again" }));
  expect(reader.getByRole("button", { name: "Practice" })).toHaveAttribute("aria-pressed", "true");
  expect(reader.getByText("1 of 3")).toBeVisible();
  expect(reader.getByRole("button", { name: "Noon" })).toBeEnabled();
  expect(reader.queryByRole("button", { name: "Inspect citation 1" })).not.toBeInTheDocument();
  expect(reader.queryByText("1 of 2 correct")).not.toBeInTheDocument();
  expect(reader.queryByRole("button", { name: "Start again" })).not.toBeInTheDocument();
});

it("renders rich choice text with hidden citations and keeps links separate from selecting an answer", async () => {
  const user = userEvent.setup();
  const { reader } = show("## (quiz) Which expression?\n- [x] `arr[1]` [1] and *value* $$x^2$$\n- [ ] [Reference](#reference)\n- [ ] [1]\nWhy: The index is required [1].");
  const choice = reader.getByRole("button", { name: /arr\[1\] and value/ });
  const row = choice.closest("li")!;
  expect(row.querySelector("code")).toHaveTextContent("arr[1]");
  expect(row.querySelector("em")).toHaveTextContent("value");
  expect(row.querySelector("[data-equation-state]")).toBeInTheDocument();
  expect(reader.queryByText("[1]", { exact: true })).not.toBeInTheDocument();
  expect(reader.getByRole("button", { name: "Option 3" })).toBeEnabled();

  const reference = reader.getByRole("link", { name: "Reference" });
  expect(reference.closest("button")).toBeNull();
  await user.click(reference);
  expect(choice).toBeEnabled();
  await user.click(within(row).getByText("value"));
  expect(choice).not.toBeInTheDocument();
  const source = within(row).getByRole("button", { name: "Inspect citation 1" });
  expect(source).toHaveAttribute("data-opens-evidence");
});

it.each([
  { block: "blockquote", why: "Why: > A quoted threshold explains the result [1].", text: "A quoted threshold" },
  { block: "pre code", why: "Why:\n~~~text\nShow the calculation for arr[1].\n~~~", text: "Show the calculation for arr[1]." },
])("keeps an inline choice and an opening $block in practice, All cards, preview and paper", async ({ block, why, text }) => {
  const user = userEvent.setup();
  const { artifact, onCite, reader, region } = show(`## (quiz) Which threshold applies?\n- [x] > 30 °C\n- [ ] 1. Another threshold\n${why}`);
  expect(reader.getByRole("button", { name: "1. Another threshold" })).toBeVisible();
  await user.click(reader.getByRole("button", { name: "> 30 °C" }));
  const expectAnswer = (view: HTMLElement) => {
    expect(view.querySelector(".cards-choice.is-correct .cards-option-text")).toHaveTextContent("> 30 °C");
    expect(view.querySelector(".cards-option-text blockquote, .cards-option-text ol")).toBeNull();
    const label = view.querySelector(".cards-why");
    expect(label).toHaveTextContent("Why:");
    expect(label?.nextElementSibling?.querySelector(block)).toHaveTextContent(text);
  };
  expectAnswer(region);
  await user.click(reader.getByRole("button", { name: "All cards" }));
  expectAnswer(reader.getByRole("list", { name: "All cards" }));
  const { Reader } = outputViews.cards;
  const preview = render(<Reader artifact={artifact} onCite={onCite} presentation="preview" />);
  expectAnswer(preview.container);
  render(<PrintRoot artifact={artifact} />);
  expectAnswer(document.getElementById("print-root")!);
});

it("shows every saved question and answer in All cards and edit preview", async () => {
  const user = userEvent.setup();
  const fixture = documentSections.find((item) => item.shape === "cards")!;
  const { reader, artifact, onCite } = show(fixture.content);
  const allCards = reader.getByRole("button", { name: "All cards" });
  await user.click(allCards);
  expect(allCards).toHaveAttribute("aria-pressed", "true");
  const list = within(reader.getByRole("list", { name: "All cards" }));
  expect(list.getAllByRole("heading")).toHaveLength(2);
  expect(list.getByText("Correct answer").closest("li")).toHaveTextContent("White-cell");
  expect(list.getAllByRole("button", { name: "Inspect citation 1" })).toHaveLength(2);
  expect(reader.queryByRole("navigation", { name: "Card navigation" })).not.toBeInTheDocument();
  await user.click(reader.getByRole("button", { name: "Practice" }));
  expect(reader.getByRole("button", { name: "Show answer" })).toBeVisible();
  const { Reader } = outputViews.cards;
  const preview = render(<Reader artifact={artifact} onCite={onCite} presentation="preview" />);
  expect(within(preview.container).getByRole("list", { name: "All cards" })).toBeInTheDocument();
  expect(within(preview.container).getAllByRole("heading")).toHaveLength(2);
});

it("reports unreadable cards and uses saved positions for practice, part jumps and Refine", async () => {
  const user = userEvent.setup();
  const onAsk = vi.fn();
  const onPart = vi.fn();
  const { reader, update } = show(`${flash}\n\n## An empty card\n\n## (quiz) A broken quiz\n- [x] One\n- [x] Two\nWhy: Both marked.\n\n${quiz}`, { onAsk, onPart });
  expect(onPart).toHaveBeenLastCalledWith(0);
  expect(reader.getByRole("list", { name: "Skipped cards" })).toHaveTextContent("Card 2 was skipped. The answer is missing.");
  expect(reader.getByRole("list", { name: "Skipped cards" })).toHaveTextContent("Card 3 was skipped. The quiz needs one marked correct answer.");
  await user.click(reader.getByRole("button", { name: "All cards" }));
  expect(onPart).toHaveBeenLastCalledWith(null);
  const items = Array.from(reader.getByRole("list", { name: "All cards" }).children);
  expect(items.map((item) => item.getAttribute("value"))).toEqual(["1", "4"]);
  await user.click(within(items[1] as HTMLElement).getByRole("button", { name: "Refine this card" }));
  expect(onAsk).toHaveBeenLastCalledWith(3);
  update({ goTo: { index: 2, nonce: 1 } });
  expect(reader.getByRole("list", { name: "All cards" })).toBeVisible();
  update({ goTo: { index: 3, nonce: 2 } });
  expect(reader.getByText("4 of 4")).toBeVisible();
  expect(reader.getByRole("heading", { name: "When did the test end?" })).toBeVisible();
  expect(onPart).toHaveBeenLastCalledWith(3);
  await user.click(reader.getByRole("button", { name: "Refine this card" }));
  expect(onAsk).toHaveBeenLastCalledWith(3);
});

it("explains an empty set without exposing practice controls", () => {
  const { reader } = show("<!-- cards: flashcards; design: v1 -->");
  expect(reader.getByText("This version has no readable cards.")).toBeVisible();
  expect(reader.queryByRole("button")).not.toBeInTheDocument();
});

it.each([1, 10, 40])("navigates and lists a set of %i readable cards", async (count) => {
  const user = userEvent.setup();
  const content = Array.from({ length: count }, (_, index) => `## Question ${index + 1}\nAnswer ${index + 1}.`).join("\n\n");
  const { reader, update } = show(content);
  expect(reader.getByText(`1 of ${count}`)).toBeVisible();
  expect(reader.getByRole("button", { name: "Previous" })).toBeDisabled();
  update({ goTo: { index: count - 1, nonce: 1 } });
  expect(reader.getByText(`${count} of ${count}`)).toBeVisible();
  expect(reader.getByRole("button", { name: "Next" })).toBeDisabled();
  await user.click(reader.getByRole("button", { name: "All cards" }));
  const list = within(reader.getByRole("list", { name: "All cards" }));
  expect(list.getAllByRole("heading")).toHaveLength(count);
  expect(list.getByText(`Answer ${count}.`)).toBeVisible();
});

it("shows and grades every stored choice including those after Z", async () => {
  const user = userEvent.setup();
  const choices = Array.from({ length: 27 }, (_, index) => `- [${index === 26 ? "x" : " "}] Option ${index + 1}`);
  const { reader } = show(["## (quiz) Which option?", ...choices, "Why: The final option applies."].join("\n"));
  const list = reader.getByRole("list", { name: "Choices" });
  expect(list).toHaveAttribute("type", "A");
  expect(within(list).getAllByRole("listitem")).toHaveLength(27);
  await user.click(within(list).getByRole("button", { name: "Option 27" }));
  expect(reader.getByText("Correct · your answer").closest("li")).toHaveTextContent("Option 27");
});

it("starts a fresh practice session when the opened version changes", async () => {
  const user = userEvent.setup();
  const artifact = savedCards(quiz);
  const onCite = vi.fn();
  const { Reader } = outputViews.cards;
  const { rerender } = render(<Reader artifact={artifact} onCite={onCite} />);
  await user.click(screen.getByRole("button", { name: "Noon" }));
  expect(screen.getByText("1 of 1 correct")).toBeVisible();
  rerender(<Reader artifact={{ ...artifact, id: "cards-2", versionNumber: 2 }} onCite={onCite} />);
  expect(screen.getByRole("button", { name: "Noon" })).toBeEnabled();
  expect(screen.queryByText("1 of 1 correct")).not.toBeInTheDocument();
});

it("prints a mixed answer sheet with static citations and no practice session state", async () => {
  const user = userEvent.setup();
  const { artifact, reader } = show(`${quiz}\n\n${flash}\n\nAn additional paragraph with \`arr[1]\`.\n\n${secondQuiz}\n\nThe second paragraph of Why.`);
  await user.click(reader.getByRole("button", { name: "Midnight" }));
  render(<PrintRoot artifact={artifact} />);
  const paper = document.getElementById("print-root")!;
  expect(paper.querySelectorAll(".cards-list-item")).toHaveLength(3);
  expect(paper.querySelector("sup")).toHaveTextContent("[1]");
  expect(paper.querySelector("code")).toHaveTextContent("arr[1]");
  expect(paper).toHaveTextContent("An additional paragraph");
  expect(paper).toHaveTextContent("The second paragraph of Why.");
  expect(paper.querySelector(".is-correct")).toHaveTextContent("Noon");
  expect(paper.querySelectorAll(".outputs-print-sources")).toHaveLength(1);
  expect(paper.querySelector(".outputs-print-sources")).toHaveTextContent("Test log");
  expect(paper).not.toHaveTextContent("Your answer");
  expect(paper).not.toHaveTextContent("0 of 1 correct");
  expect(paper.querySelector("button, progress")).toBeNull();
});
