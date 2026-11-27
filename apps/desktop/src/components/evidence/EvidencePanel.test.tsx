import type { ConversationCitation, SourceDetail, SourceStructure } from "@gunther/contracts";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../../api";
import { EvidenceDetail } from "./EvidenceDetail";
import { EvidencePanel } from "./EvidencePanel";
import { highlightRenderer } from "./highlight";

vi.mock("./PdfViewer", () => ({ default: ({ page, quote }: { page?: number; quote?: string }) => <div data-testid="pdf" data-page={page} data-quote={quote} /> }));
vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return { ...original, knowledgeApi: { ...original.knowledgeApi, source: vi.fn(), sourceStructure: vi.fn() } };
});

const web: ConversationCitation = { id: "c_web", kind: "web", url: "https://www.example.org/post", sourceId: "", sourceTitle: "A post", assertionId: null, quote: "CD3D marks T cells.", locator: "example.org", status: "provisional", confidence: 0 };
const library: ConversationCitation = { id: "c_lib", sourceId: "src_1", sourceTitle: "Cell paper", assertionId: null, quote: "Second block.", locator: "Page 3", status: "verified", confidence: 1, blockId: "b2", sourceRevisionId: "rev_1", anchor: { page: 3 } };
const block = (id: string, content: string) => ({ id, parentId: null, kind: "paragraph", content, locator: "", headings: [], anchor: {} });
const source = (asset: { mediaType: string; originalName: string } | null) => ({ id: "src_1", title: "Cell paper", kind: "file", asset: asset ? { id: "asset_1", sizeBytes: 10, ...asset } : null }) as unknown as SourceDetail;
const structure = (citedText = library.quote): SourceStructure => ({
  sourceId: library.sourceId, revisionId: "rev_1", parser: null, nextOffset: null,
  processing: { state: "ready", jobId: null, revisionId: "rev_1", warning: null },
  blocks: [block("b1", "First block."), block("b2", citedText), block("b3", "Third block.")],
});

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(knowledgeApi.sourceStructure).mockResolvedValue(structure());
});

describe("EvidencePanel", () => {
  it("shows the matched web passage, then a link out to the page", () => {
    render(<EvidencePanel citation={web} index={0} onClose={vi.fn()} />);
    expect(screen.getByText("CD3D marks T cells.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show (context|full passage)/ })).not.toBeInTheDocument();
    expect(screen.getByText("Outside your library")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open example\.org/ })).toHaveAttribute("href", "https://www.example.org/post");
    expect(screen.queryByRole("button", { name: "+ Add to library" })).toBeNull();
  });

  it.each([["web", web], ["library", library]] as const)("leads with a unique verbatim %s quote and marks it in context", async (_kind, citation) => {
    vi.mocked(knowledgeApi.source).mockResolvedValue(source(null));
    const supportText = `The passage begins here.\n\n${citation.quote} The passage continues here.`;
    render(<EvidencePanel citation={{ ...citation, supportText }} onClose={vi.fn()} />);

    const quote = screen.getByText(citation.quote, { selector: "blockquote.gx-evidence-quote" });
    const toggle = screen.getByRole("button", { name: "Show context" });
    const context = document.getElementById(toggle.getAttribute("aria-controls")!);
    expect(quote).toBeVisible();
    expect(context).not.toBeVisible();
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(toggle);
    expect(toggle).toHaveAccessibleName("Hide context");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(context).toBeVisible();
    expect(context?.textContent).toBe(supportText);
    expect(context?.querySelector("mark")?.textContent).toBe(citation.quote);
    expect(screen.queryByText("First block.")).not.toBeInTheDocument();

    await userEvent.click(toggle);
    expect(quote).toBeVisible();
    expect(context).not.toBeVisible();
  });

  const sentence = "The team compared the two records.";
  const preview = `${sentence} `.repeat(9).trimEnd();
  const passage = `${preview} Counterbalancing completed the review.`;

  it.each([
    ["a quote alone", { quote: passage }],
    ["identical support and quote", { supportText: passage, quote: passage }],
    ["a repeated quote", { supportText: passage, quote: sentence }],
    ["a quote with different casing", { supportText: passage, quote: "Counterbalancing Completed" }],
  ] as const)("previews a long passage with %s and reveals the full wording", async (_case, fields) => {
    render(<EvidencePanel citation={{ ...web, ...fields }} onClose={vi.fn()} />);
    const panel = screen.getByRole("complementary", { name: "Evidence" });
    const quote = screen.getByText(`${preview}…`, { selector: "blockquote" });
    expect(quote.textContent).toBe(`${preview}…`);
    expect(screen.queryByRole("button", { name: "Show context" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show full passage" }));
    expect(quote.textContent).toBe(passage);
    expect(panel.querySelector("mark")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show less" }));
    expect(quote.textContent).toBe(`${preview}…`);
  });

  it("previews a long passage in desktop webviews without word segmentation", () => {
    const segmenter = vi.spyOn(Intl, "Segmenter", "get").mockReturnValue(undefined as never);
    try {
      render(<EvidencePanel citation={{ ...web, quote: passage }} onClose={vi.fn()} />);
      expect(screen.getByText(`${preview}…`, { selector: "blockquote" })).toBeVisible();
    } finally {
      segmenter.mockRestore();
    }
  });

  it("shows a short saved passage directly when the citation has only support text and an original PDF", async () => {
    vi.mocked(knowledgeApi.source).mockResolvedValue(source({ mediaType: "application/pdf", originalName: "paper.pdf" }));
    render(<EvidenceDetail citation={{ ...library, quote: "", supportText: "The record ends here." }} />);

    const original = await screen.findByRole("button", { name: "Original · p. 3" });
    const passageTab = screen.getByRole("button", { name: "Passage" });
    expect(passageTab).toHaveClass("is-active");
    expect(screen.getByText("The record ends here.", { selector: "blockquote" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /Show (context|full passage)/ })).not.toBeInTheDocument();

    await userEvent.click(original);
    expect(await screen.findByTestId("pdf")).toBeVisible();
    await userEvent.click(passageTab);
    expect(screen.getByText("The record ends here.", { selector: "blockquote" })).toBeVisible();
  });

  it("opens a saved web passage without an explicit kind as a web citation", () => {
    const { kind: _kind, ...older } = web;
    render(<EvidenceDetail citation={{ ...older, locator: "public web" }} />);
    expect(screen.getByText("Web · example.org")).toBeVisible();
    expect(screen.getByText("Outside your library")).toBeVisible();
    expect(screen.getByRole("link", { name: /Open example\.org/ })).toHaveAttribute("href", "https://www.example.org/post");
  });

  it("closes when the page outside it is pressed, but not for its own content or for something that opens a source", async () => {
    const onClose = vi.fn();
    render(<>
      <main>Conversation</main>
      <button type="button" aria-label="Inspect citation 2">[2]</button>
      <EvidencePanel citation={web} index={0} onClose={onClose} />
    </>);
    await userEvent.click(screen.getByText("CD3D marks T cells."));
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 2" }));
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText("Conversation"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("switches evidence from a marked trigger while keeping the panel open", async () => {
    const second = { ...web, id: "c_second", quote: "A different passage." };
    const closed = vi.fn();
    function Reader() {
      const [citation, setCitation] = useState<ConversationCitation | null>(web);
      return <>
        <button type="button" data-opens-evidence onClick={() => setCitation(second)}>Other answer sources</button>
        {citation && <EvidencePanel citation={citation} index={0} onClose={() => { closed(); setCitation(null); }} />}
      </>;
    }
    render(<Reader />);
    const panel = screen.getByRole("complementary", { name: "Evidence" });
    await userEvent.click(screen.getByRole("button", { name: "Other answer sources" }));
    expect(closed).not.toHaveBeenCalled();
    expect(screen.getByRole("complementary", { name: "Evidence" })).toBe(panel);
    expect(screen.getByText(second.quote)).toBeVisible();
  });

  it("shows a library passage among its neighbours and opens the source", async () => {
    vi.mocked(knowledgeApi.source).mockResolvedValue(source(null));
    const onOpenSource = vi.fn();
    render(<EvidencePanel citation={library} index={1} onClose={vi.fn()} onOpenSource={onOpenSource} />);
    expect(await screen.findByText("First block.")).toBeInTheDocument();
    expect(screen.getByText("Second block.")).toHaveClass("is-cited");
    expect(screen.queryByRole("button", { name: /Original/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Open source/ }));
    expect(onOpenSource).toHaveBeenCalledWith("src_1");
  });

  it.each([
    { occurrence: "repeated", text: "Sample A showed 18% and sample B also showed 18%.", marks: [] },
    { occurrence: "unique", text: "Sample A showed 12% and sample B showed 18%.", marks: ["18%"] },
  ])("renders the full structured block when the exact quote is $occurrence", async ({ text, marks }) => {
    vi.mocked(knowledgeApi.source).mockResolvedValue(source(null));
    vi.mocked(knowledgeApi.sourceStructure).mockResolvedValue(structure(text));
    render(<EvidencePanel citation={{ ...library, quote: "18%" }} onClose={vi.fn()} />);
    const panel = screen.getByRole("complementary", { name: "Evidence" });

    expect(await screen.findByText("First block.")).toBeVisible();
    expect(panel.querySelector(".gx-evidence-context .is-cited")?.textContent).toBe(text);
    expect(Array.from(panel.querySelectorAll("mark"), (mark) => mark.textContent)).toEqual(marks);
  });

  it("views the cited PDF page at the floating panel's chosen width, and the panel closes", async () => {
    vi.mocked(knowledgeApi.source).mockResolvedValue(source({ mediaType: "application/pdf", originalName: "paper.pdf" }));
    const onClose = vi.fn();
    render(<EvidencePanel citation={library} onClose={onClose} />);
    const resize = screen.getByRole("separator", { name: "Resize evidence" });
    expect(resize).toHaveAttribute("aria-valuenow", "460");
    fireEvent.keyDown(resize, { key: "ArrowLeft" });
    expect(resize).toHaveAttribute("aria-valuenow", "484");
    await userEvent.click(await screen.findByRole("button", { name: /Original · p\. 3/ }));
    expect(await screen.findByTestId("pdf")).toHaveAttribute("data-page", "3");
    expect(resize).toHaveAttribute("aria-valuenow", "484");
    expect(screen.getByRole("complementary", { name: "Evidence" })).toHaveClass("gx-evidence");
    await userEvent.click(screen.getByRole("button", { name: "Close evidence" }));
    expect(onClose).toHaveBeenCalledOnce();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(2));
  });

  it("still shows the quote when the source cannot be loaded", async () => {
    vi.mocked(knowledgeApi.source).mockRejectedValue(new Error("gone"));
    vi.mocked(knowledgeApi.sourceStructure).mockRejectedValue(new Error("gone"));
    render(<EvidencePanel citation={library} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("only the quote is shown");
    expect(screen.getByText("Second block.")).toBeInTheDocument();
  });
});

it("shares the citation viewer with inline navigation", async () => {
  const onBack = vi.fn();
  const onNext = vi.fn();
  render(<EvidenceDetail citation={web} index={0} onBack={onBack} position={0} total={2} onNext={onNext} />);
  expect(screen.getByText("1 / 2")).toBeVisible();
  expect(screen.getByRole("button", { name: "Previous source" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Next source" }));
  await userEvent.click(screen.getByRole("button", { name: "All 2 sources" }));
  expect(onNext).toHaveBeenCalledOnce();
  expect(onBack).toHaveBeenCalledOnce();
});

it("shows the source kind and replaces a placeholder passage heading", async () => {
  vi.mocked(knowledgeApi.source).mockResolvedValue({ ...source(null), kind: "note" });
  render(<EvidenceDetail citation={{ ...library, locator: "Source text" }} index={1} />);
  const kind = await screen.findByText("Note", { selector: ".gx-evidence-title small" });
  expect(kind.closest("header")?.querySelector(".gx-kind-icon.kind-note")).toBeVisible();
  expect(screen.getByText("[2]")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Passage" })).toBeVisible();
  expect(screen.queryByText("Source text")).toBeNull();
  expect(screen.queryByRole("button", { name: "Show in answer" })).toBeNull();
});

it("uses chips through five passages and keeps the selection in the six-passage picker", async () => {
  const passages = Array.from({ length: 6 }, (_, index) => ({ ...web, id: `passage-${index}`, quote: `Quote ${index + 1}`, anchor: { page: index + 2 } }));
  const first = passages[0]!;
  const onClose = vi.fn();
  const { rerender } = render(<EvidencePanel citation={first} passages={passages.slice(0, 5)} onClose={onClose} />);
  const chip = screen.getByRole("button", { name: "Passage 5 · p. 6" });
  await userEvent.click(chip);
  expect(chip).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("Quote 5")).toBeVisible();
  expect(screen.queryByRole("combobox")).toBeNull();
  rerender(<EvidencePanel citation={first} passages={passages} onClose={onClose} />);
  const picker = screen.getByRole("combobox", { name: "Passage" });
  expect(picker).toHaveValue("passage-4");
  await userEvent.selectOptions(picker, "passage-5");
  expect(screen.getByText("Quote 6")).toBeVisible();
});

it("offers the library action beside the original page when a library handler is supplied", async () => {
  const openSource = vi.fn();
  const importOne = vi.fn();
  const actions = { openSource, importOne, importAll: vi.fn(), batching: false, busy: false };
  const { rerender } = render(<EvidenceDetail citation={web} webImports={{ ...actions, status: () => ({ status: "idle" }) }} />);
  await userEvent.click(screen.getByRole("button", { name: "+ Add to library" }));
  expect(importOne).toHaveBeenCalledWith(expect.objectContaining({ url: web.url, title: web.sourceTitle }));
  rerender(<EvidenceDetail citation={web} webImports={{ ...actions, status: () => ({ status: "imported", sourceId: "captured" }) }} />);
  await userEvent.click(screen.getByRole("button", { name: "In library · Open source" }));
  expect(openSource).toHaveBeenCalledWith("captured");
  expect(screen.getByRole("link", { name: "Open example.org" })).toHaveAttribute("href", web.url);
});

it("returns from an inline original PDF to its cited passage", async () => {
  vi.mocked(knowledgeApi.source).mockResolvedValue(source({ mediaType: "application/pdf", originalName: "paper.pdf" }));
  render(<EvidenceDetail citation={library} />);
  await userEvent.click(await screen.findByRole("button", { name: /Original · p\. 3/ }));
  expect(await screen.findByTestId("pdf")).toHaveAttribute("data-page", "3");
  await userEvent.click(screen.getByRole("button", { name: "Passage" }));
  expect(screen.queryByTestId("pdf")).not.toBeInTheDocument();
  expect(screen.getByText("Second block.")).toHaveClass("is-cited");
});

describe("helpers", () => {
  it("marks the text runs that belong to the cited passage and escapes the rest", () => {
    const mark = highlightRenderer("The CD3D gene marks T cells.");
    expect(mark({ str: "CD3D gene" })).toBe("<mark>CD3D gene</mark>");
    expect(mark({ str: "<b>" })).toBe("&lt;b&gt;");
    expect(mark({ str: "of" })).toBe("of");
  });
});
