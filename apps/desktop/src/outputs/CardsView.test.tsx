import type { Artifact } from "@gunther/contracts";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { CardsView } from "./CardsView";
import documentSections from "./fixtures/document-sections.json";

const flash = "## What did the seal protect?\nThe sample [1].";
const quiz = "## (quiz) When did the test end? [1]\n- [x] Noon [1]\n- [ ] Midnight\nWhy: The log records an end time of 12:00 [1].";
const secondQuiz = "## (quiz) Where was it logged?\n- [ ] A notebook\n- [x] A register\nWhy: The entry is in the register [1].";

function show(content: string) {
  const artifact = {
    id: "cards-1", outline: [], sections: [], content,
    citations: [{ id: "source-1", sourceTitle: "Test log", quote: "The log records an end time of 12:00." }],
  } as unknown as Artifact;
  const onCite = vi.fn();
  render(<CardsView artifact={artifact} onCite={onCite} />);
  const region = screen.getByRole("region", { name: "Card reader" });
  return { reader: within(region), region, onCite, artifact };
}

it("flips a flashcard with click and Space and moves with buttons and arrow keys", async () => {
  const user = userEvent.setup();
  const { reader, region, onCite, artifact } = show(`${flash}\n\n## When was it read?\nAt noon.`);
  expect(reader.getByRole("heading", { name: "What did the seal protect?" })).toBeVisible();
  expect(reader.getByText("1 / 2")).toBeVisible();
  expect(reader.getByRole("button", { name: "Previous" })).toBeDisabled();
  expect(reader.queryByText("The sample")).not.toBeInTheDocument();
  await user.click(reader.getByRole("button", { name: "Show answer" }));
  expect(reader.getByRole("button", { name: "Inspect citation 1" })).toBeVisible();
  await user.keyboard("[Space]");
  expect(reader.queryByRole("button", { name: "Inspect citation 1" })).not.toBeInTheDocument();
  await user.keyboard("[Space]");
  const citation = reader.getByRole("button", { name: "Inspect citation 1" });
  expect(citation).toHaveAttribute("data-opens-evidence");
  await user.click(citation);
  expect(onCite).toHaveBeenCalledExactlyOnceWith(artifact.citations[0], 0);
  await user.click(reader.getByRole("button", { name: "Next" }));
  expect(reader.getByRole("heading", { name: "When was it read?" })).toBeVisible();
  expect(reader.getByText("2 / 2")).toBeVisible();
  await user.click(reader.getByRole("button", { name: "Previous" }));
  expect(reader.getByRole("heading", { name: "What did the seal protect?" })).toBeVisible();
  region.focus();
  await user.keyboard("{ArrowRight}{ArrowLeft}");
  expect(reader.getByText("1 / 2")).toBeVisible();
  await user.keyboard("{ArrowRight}");
  expect(reader.getByText("2 / 2")).toBeVisible();
});

it("locks a quiz choice, reveals its citations, and tracks and resets the score", async () => {
  const user = userEvent.setup();
  const { reader } = show(`${quiz}\n\n${secondQuiz}`);
  expect(reader.queryByText("[1]")).not.toBeInTheDocument();
  expect(reader.queryByText("Why:")).not.toBeInTheDocument();
  await user.click(reader.getByRole("button", { name: "Noon" }));
  expect(reader.getByText("Correct", { exact: true })).toBeVisible();
  expect(reader.getByText("1 of 1 correct")).toBeVisible();
  expect(reader.getAllByRole("button", { name: "Inspect citation 1" })).toHaveLength(3);
  expect(reader.getByText("Why:")).toBeVisible();
  expect(reader.queryByRole("button", { name: "Midnight" })).not.toBeInTheDocument();
  await user.click(reader.getByRole("button", { name: "Next" }));
  expect(reader.queryByRole("button", { name: "Inspect citation 1" })).not.toBeInTheDocument();
  await user.click(reader.getByRole("button", { name: "A notebook" }));
  expect(reader.getByText("Incorrect")).toBeVisible();
  expect(reader.getByText("Correct answer").closest("li")).toHaveTextContent("A register");
  expect(reader.getByText("1 of 2 correct")).toBeVisible();
  await user.click(reader.getByRole("button", { name: "Previous" }));
  expect(reader.getByText("Correct", { exact: true })).toBeVisible();
  await user.click(reader.getByRole("button", { name: "Start again" }));
  expect(reader.getByRole("button", { name: "Noon" })).toBeEnabled();
  expect(reader.queryByRole("button", { name: "Inspect citation 1" })).not.toBeInTheDocument();
  expect(reader.queryByText("1 of 2 correct")).not.toBeInTheDocument();
  expect(reader.getByRole("button", { name: "Start again" })).toBeDisabled();
});

it("preserves inline code in a quiz choice while hiding prose citations", () => {
  const { reader } = show("## (quiz) Which expression?\n- [x] `arr[1]` [2]\n- [ ] Nothing\nWhy: The index is required [1].");
  expect(reader.getByRole("button", { name: "`arr[1]`" })).toBeVisible();
  expect(reader.queryByText(/\[2\]/)).not.toBeInTheDocument();
});

it("shows every question and answer in the list and returns to practice", async () => {
  const user = userEvent.setup();
  const fixture = documentSections.find((item) => item.shape === "cards")!;
  const { reader } = show(fixture.content);
  const toggle = reader.getByRole("button", { name: "List view" });
  await user.click(toggle);
  const list = within(reader.getByRole("list", { name: "All cards" }));
  expect(list.getAllByRole("heading")).toHaveLength(2);
  expect(list.getByText("Correct answer").closest("li")).toHaveTextContent("White-cell");
  expect(list.getAllByRole("button", { name: "Inspect citation 1" })).toHaveLength(2);
  expect(reader.queryByRole("navigation", { name: "Card navigation" })).not.toBeInTheDocument();
  await user.click(toggle);
  expect(reader.getByRole("button", { name: "Show answer" })).toBeVisible();
});
