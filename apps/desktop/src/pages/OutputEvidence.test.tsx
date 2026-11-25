import type { Artifact, ContentsSource, ConversationCitation } from "@gunther/contracts";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { makeSource } from "../test/fixtures";
import { OutputEvidence } from "./OutputEvidence";

const citations: ConversationCitation[] = Array.from({ length: 13 }, (_, index) => ({
  id: `cite-${index}`, sourceId: `source-${index}`, sourceTitle: `Source ${index + 1}`,
  assertionId: null, quote: `Passage ${index + 1}`, locator: "", status: "verified", confidence: 1,
}));
const sources: ContentsSource[] = [{ ...makeSource({ id: "source-0", kind: "note" }),
  durationSeconds: null, authors: null, year: null, pages: null, mediaType: null, host: null, processing: "ready" }];
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
  const { rerender } = render(<OutputEvidence artifact={deck} sources={sources} partInView={0} onOpen={open} />);
  await user.click(screen.getByRole("button", { name: "This slide" }));
  expect(screen.getByRole("button", { name: "Inspect citation 1" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Inspect citation 2" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Inspect citation 12" })).toBeNull();
  rerender(<OutputEvidence artifact={deck} sources={sources} partInView={1} onOpen={open} />);
  expect(screen.queryByRole("button", { name: "Inspect citation 1" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Inspect citation 12" }));
  expect(open).toHaveBeenCalledWith(citations, 11);
  expect(screen.getByRole("button", { name: "Inspect citation 13" })).toBeVisible();
});

it("includes grouped citation marks and excludes numbers in slide design HTML", async () => {
  const user = userEvent.setup();
  const content = "# First\n[1, 2] [i:3， 4] [5、6]\n\n:::design\n<div>Not a text citation [7]</div>\n:::\n\n---\n\n# Second\n[8]";
  render(<OutputEvidence artifact={{ ...deck, content }} sources={sources} partInView={0} onOpen={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "This slide" }));
  for (const number of [1, 2, 3, 4, 5, 6]) expect(screen.getByRole("button", { name: `Inspect citation ${number}` })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Inspect citation 7" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Inspect citation 8" })).toBeNull();
});

it("filters a report section by prose markers, excluding code, math and escaped literals", async () => {
  const user = userEvent.setup();
  const content = [
    "## First",
    "Prose [2] [p:3]. Inline `arr[4]` and $$x[5]$$. Escaped \\[6\\].",
    "See https&#58;//example.org and [10].",
    "~~~\narr[1]\n~~~",
    "    arr[7]",
    "$$\nx[8]\n$$",
    "## Second\nSee https&#58;//example.org and &#91;9&#93;.",
  ].join("\n\n");
  const artifact: Artifact = { ...deck, kind: "report", content };
  const { rerender } = render(<OutputEvidence artifact={artifact} sources={sources} partInView={0} onOpen={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "This section" }));
  expect(screen.getAllByRole("button", { name: /Inspect citation/ }).map((button) => button.textContent)).toEqual(["[2]", "[3]", "[10]"]);
  rerender(<OutputEvidence artifact={artifact} sources={sources} partInView={1} onOpen={vi.fn()} />);
  expect(screen.getAllByRole("button", { name: /Inspect citation/ }).map((button) => button.textContent)).toEqual(["[9]"]);
});

it("only offers part filtering beyond twelve citations with multiple parts", () => {
  const { rerender } = render(<OutputEvidence artifact={{ ...deck, citations: citations.slice(0, 12) }} sources={sources} partInView={0} onOpen={vi.fn()} />);
  expect(screen.queryByRole("group", { name: "Citation scope" })).toBeNull();
  rerender(<OutputEvidence artifact={{ ...deck, content: "# Only slide\n[1]" }} sources={sources} partInView={0} onOpen={vi.fn()} />);
  expect(screen.queryByRole("group", { name: "Citation scope" })).toBeNull();
});

it("groups older web citations by URL even when their kind is absent", () => {
  const first = { ...citations[0]!, sourceId: "", url: "https://www.example.org/record", sourceTitle: "Web record", quote: "First quote", locator: "Public web" };
  const second = { ...first, id: "second", quote: "Later quote" };
  const { container } = render(<OutputEvidence artifact={{ ...deck, citations: [first, second] }} sources={sources} partInView={0} onOpen={vi.fn()} />);
  const groups = container.querySelectorAll<HTMLElement>(".evidence-source-group");
  expect(groups).toHaveLength(1);
  expect(groups[0]).toHaveTextContent("First quote");
  expect(groups[0]).toHaveTextContent("Web · example.org");
  expect(within(groups[0]!).getByRole("button", { name: "Inspect citation 1" })).toBeVisible();
  expect(within(groups[0]!).getByRole("button", { name: "Inspect citation 2" })).toBeVisible();
});

it("shows saved section coverage and source metadata in separate bands", () => {
  const sections: Artifact["sections"] = [
    { index: 0, heading: "Checked", checked: true, issues: [] },
    { index: 1, heading: "With an issue", checked: true, issues: [{ claim: "An unsupported finding", verdict: "unsupported", note: "" }] },
    { index: 2, heading: "Pending", checked: false, issues: [{ claim: "Awaiting a check", verdict: "unchecked", note: "" }] },
  ];
  const saved = { ...deck, sections, citations: [{ ...citations[0]!, locator: "Source text" }, { ...citations[1]!, locator: "Page 3" }] };
  const { rerender } = render(<OutputEvidence artifact={saved} sources={sources} partInView={0} onOpen={vi.fn()} />);
  const details = screen.getByRole("region", { name: "Output details" });
  expect(within(details).getByText("Version 1")).toBeVisible();
  expect(within(details).getByText("2 of 3 sections checked · 1 issue")).toBeVisible();
  expect(details.querySelectorAll(".evidence-coverage-track > span")).toHaveLength(3);
  expect(screen.getByText("Note")).toBeVisible();
  expect(screen.getByText("Source · Page 3")).toBeVisible();
  expect(screen.queryByText("Source text")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Inspect citation 1" })).not.toHaveAttribute("title");
  rerender(<OutputEvidence artifact={{ ...saved, sections: sections.slice(0, 2).map((section) => ({ ...section, issues: [] })) }}
    sources={sources} partInView={0} onOpen={vi.fn()} />);
  expect(within(details).getByText("All 2 sections checked")).toBeVisible();
  expect(details.querySelectorAll(".evidence-coverage-track .is-verified")).toHaveLength(1);
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
      { kind: "library", title: "An uncited reading", sourceId: "reading", url: null, locator: "Source text", snippet: "A close reading.", query: "trial", kept: true },
      { kind: "web", title: "An uncited page", url: "https://example.org/article", sourceId: null, locator: "example.org", snippet: "Page excerpt.", query: "trial", kept: false },
    ],
  } };
  const { rerender } = render(<OutputEvidence artifact={saved} sources={sources} partInView={null} onOpen={open} />);
  const notes = screen.getByText("Research notes");
  expect(notes.closest("details")).not.toHaveAttribute("open");
  expect(screen.getByRole("region", { name: "Cited sources" }).compareDocumentPosition(notes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByText("Key results used").closest("details")).not.toHaveAttribute("open");
  expect(screen.getByText("Also searched").closest("details")).not.toHaveAttribute("open");
  await user.click(screen.getByText("Research notes"));
  expect(screen.getByText("Start with the measured changes.")).toBeVisible();
  expect(screen.getByText("Later readings were unavailable.")).toBeVisible();
  expect(screen.getByText("One observation site.")).toBeVisible();
  await user.click(screen.getByText("Key results used"));
  expect(screen.getByText("Two readings support the result.")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Open citation 2 for this key result" }));
  expect(open).toHaveBeenLastCalledWith(citations, 1);
  await user.click(screen.getByText("Also searched"));
  expect(screen.getByText("1 web · 1 library")).toBeVisible();
  const web = screen.getByText("An uncited page");
  const library = screen.getByRole("button", { name: /An uncited reading/ });
  expect(library).not.toHaveTextContent("Source text");
  expect(within(web.closest("article")!).getByText("Filtered")).toBeVisible();
  expect(within(library).queryByText("Filtered")).not.toBeInTheDocument();
  expect(screen.getAllByText("Query: trial")).toHaveLength(2);
  expect(web.compareDocumentPosition(library) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await user.click(library);
  expect(open).toHaveBeenLastCalledWith([expect.objectContaining({ sourceId: "reading", locator: "Source text", quote: "A close reading." })], 0);

  rerender(<OutputEvidence artifact={deck} sources={sources} partInView={null} onOpen={open} />);
  expect(screen.queryByText("Research notes")).toBeNull();
  expect(screen.queryByText(/Key results used/)).toBeNull();
  expect(screen.queryByText("Also searched")).toBeNull();
});
