import type { Artifact, ConversationCitation } from "@gunther/contracts";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { OutputEvidence } from "./OutputEvidence";

const citations: ConversationCitation[] = Array.from({ length: 13 }, (_, index) => ({
  id: `cite-${index}`, sourceId: `source-${index}`, sourceTitle: `Source ${index + 1}`,
  assertionId: null, quote: `Passage ${index + 1}`, locator: "", status: "verified", confidence: 1,
}));
const deck: Artifact = { id: "deck", lineageId: "lineage", workspaceId: "workspace", knowledgeBaseId: "library",
  kind: "slides", format: "slides", style: "slides", outputType: "Slides", audience: "student", origin: "build",
  versionNumber: 1, supersedesArtifactId: null, title: "Deck", createdAt: "2026-01-01T00:00:00Z", contentHash: "hash", manifestHash: "seal",
  content: "# First\n[1] [i:2]\n\n---\n\n# Second\n[p:12] [d:13]", scope: null, sections: [], citations,
  unitSnapshots: [], acceptedUnitIds: [], unitCount: 0, inputs: { sources: 0, units: 0, sessions: 0 }, modelLabel: null,
  brief: "", outline: [], provenance: { acceptedOnly: false, acceptedUnitIds: [], schemaVersion: 2,
    generator: "gunther.output-agents.v1", workspaceId: "workspace", knowledgeBaseId: "library", knowledgeBaseQuestion: "", revisionIds: [] } };

it("filters a large deck by the current slide without renumbering annotated citations", async () => {
  const user = userEvent.setup();
  const open = vi.fn();
  const { rerender } = render(<OutputEvidence artifact={deck} partInView={0} onOpen={open} />);
  await user.click(screen.getByRole("button", { name: "This section" }));
  expect(screen.getByRole("button", { name: "Inspect citation 1" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Inspect citation 2" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Inspect citation 12" })).toBeNull();
  rerender(<OutputEvidence artifact={deck} partInView={1} onOpen={open} />);
  expect(screen.queryByRole("button", { name: "Inspect citation 1" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Inspect citation 12" }));
  expect(open).toHaveBeenCalledWith(citations, 11);
  expect(screen.getByRole("button", { name: "Inspect citation 13" })).toBeVisible();
});

it("includes grouped citation marks and excludes numbers in slide design HTML", async () => {
  const user = userEvent.setup();
  const content = "# First\n[1, 2] [i:3， 4] [5、6]\n\n:::design\n<div>Not a text citation [7]</div>\n:::\n\n---\n\n# Second\n[8]";
  render(<OutputEvidence artifact={{ ...deck, content }} partInView={0} onOpen={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "This section" }));
  for (const number of [1, 2, 3, 4, 5, 6]) expect(screen.getByRole("button", { name: `Inspect citation ${number}` })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Inspect citation 7" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Inspect citation 8" })).toBeNull();
});

it("only offers part filtering beyond twelve citations with multiple parts", () => {
  const { rerender } = render(<OutputEvidence artifact={{ ...deck, citations: citations.slice(0, 12) }} partInView={0} onOpen={vi.fn()} />);
  expect(screen.queryByRole("group", { name: "Citation scope" })).toBeNull();
  rerender(<OutputEvidence artifact={{ ...deck, content: "# Only slide\n[1]" }} partInView={0} onOpen={vi.fn()} />);
  expect(screen.queryByRole("group", { name: "Citation scope" })).toBeNull();
});

it("groups older web citations by URL even when their kind is absent", () => {
  const first = { ...citations[0]!, sourceId: "", url: "https://example.org/record", sourceTitle: "Web record", quote: "First quote" };
  const second = { ...first, id: "second", quote: "Later quote" };
  const { container } = render(<OutputEvidence artifact={{ ...deck, citations: [first, second] }} partInView={0} onOpen={vi.fn()} />);
  const groups = container.querySelectorAll<HTMLElement>(".evidence-source-group");
  expect(groups).toHaveLength(1);
  expect(groups[0]).toHaveTextContent("First quote");
  expect(within(groups[0]!).getByRole("button", { name: "Inspect citation 1" })).toBeVisible();
  expect(within(groups[0]!).getByRole("button", { name: "Inspect citation 2" })).toBeVisible();
});

it("folds research notes, checked key results and searched passages when present", async () => {
  const user = userEvent.setup();
  const open = vi.fn();
  const saved: Artifact = { ...deck, provenance: { ...deck.provenance,
    approach: { question: "What changed?", answer: "", purpose: "Compare", structure: "", structureReason: "",
      pages: null, supplements: [], gaps: ["Later readings were unavailable."], memo: { answer: "Start with the measured changes.", insights: [] },
      studyMap: { question: "What changed?", contribution: "Measured changes", claims: [], limitations: ["One observation site."] } },
    keyResultsUsed: [{ id: "checked", text: "Two readings support the result.", refs: [2] }],
    searched: [
      { kind: "library", title: "An uncited reading", sourceId: "reading", url: null, locator: "Page 4", snippet: "A close reading.", query: "trial", kept: true },
      { kind: "web", title: "An uncited page", url: "https://example.org/article", sourceId: null, locator: "example.org", snippet: "Page excerpt.", query: "trial", kept: false },
    ],
  } };
  const { rerender } = render(<OutputEvidence artifact={saved} partInView={null} onOpen={open} />);
  expect(screen.getByText("Research notes").closest("details")).not.toHaveAttribute("open");
  expect(screen.getByText("Key results used (1)").closest("details")).not.toHaveAttribute("open");
  expect(screen.getByText("Also searched, not cited (2)", { exact: false }).closest("details")).not.toHaveAttribute("open");
  await user.click(screen.getByText("Research notes"));
  expect(screen.getByText("Start with the measured changes.")).toBeVisible();
  expect(screen.getByText("Later readings were unavailable.")).toBeVisible();
  expect(screen.getByText("One observation site.")).toBeVisible();
  await user.click(screen.getByText("Key results used (1)"));
  expect(screen.getByText("Two readings support the result.")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Open citation 2 for this key result" }));
  expect(open).toHaveBeenLastCalledWith(citations, 1);
  await user.click(screen.getByText("Also searched, not cited (2)", { exact: false }));
  expect(screen.getByText("2 more (1 online)")).toBeVisible();
  const web = screen.getByText("An uncited page");
  const library = screen.getByRole("button", { name: /An uncited reading/ });
  expect(web.compareDocumentPosition(library) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await user.click(library);
  expect(open).toHaveBeenLastCalledWith([expect.objectContaining({ sourceId: "reading", locator: "Page 4", quote: "A close reading." })], 0);

  rerender(<OutputEvidence artifact={deck} partInView={null} onOpen={open} />);
  expect(screen.queryByText("Research notes")).toBeNull();
  expect(screen.queryByText(/Key results used/)).toBeNull();
  expect(screen.queryByText(/Also searched, not cited/)).toBeNull();
});
