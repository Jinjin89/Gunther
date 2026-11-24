import type { ConversationCitation, SourceDetail } from "@gunther/contracts";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../../api";
import { EvidenceDetail, hostOf } from "./EvidenceDetail";
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

beforeEach(() => {
  vi.mocked(knowledgeApi.sourceStructure).mockResolvedValue({ sourceId: "src_1", revisionId: "rev_1", processing: { state: "ready" }, parser: null, nextOffset: null, blocks: [block("b1", "First block."), block("b2", "Second block."), block("b3", "Third block.")] } as never);
});

describe("EvidencePanel", () => {
  it("shows the matched web passage, then a link out to the page", () => {
    render(<EvidencePanel citation={web} index={0} onClose={vi.fn()} />);
    expect(screen.getByText("CD3D marks T cells.")).toBeInTheDocument();
    expect(screen.getByText("Outside your library")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open example\.org/ })).toHaveAttribute("href", "https://www.example.org/post");
  });

  it("opens a saved web passage without an explicit kind as a web citation", () => {
    const { kind: _kind, ...older } = web;
    render(<EvidenceDetail citation={older} />);
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

  it("views a PDF in the panel at the cited page, and the panel closes", async () => {
    vi.mocked(knowledgeApi.source).mockResolvedValue(source({ mediaType: "application/pdf", originalName: "paper.pdf" }));
    const onClose = vi.fn();
    render(<EvidencePanel citation={library} onClose={onClose} />);
    await userEvent.click(await screen.findByRole("button", { name: /Original · p\. 3/ }));
    expect(await screen.findByTestId("pdf")).toHaveAttribute("data-page", "3");
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
  expect(screen.getByText("[1 / 2]")).toBeVisible();
  expect(screen.getByRole("button", { name: "Previous source" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Next source" }));
  await userEvent.click(screen.getByRole("button", { name: "All 2 sources" }));
  expect(onNext).toHaveBeenCalledOnce();
  expect(onBack).toHaveBeenCalledOnce();
});

it("reports when an inline original PDF is visible", async () => {
  vi.mocked(knowledgeApi.source).mockResolvedValue(source({ mediaType: "application/pdf", originalName: "paper.pdf" }));
  const onOriginalPdfChange = vi.fn();
  render(<EvidenceDetail citation={library} onOriginalPdfChange={onOriginalPdfChange} />);
  await userEvent.click(await screen.findByRole("button", { name: /Original · p\. 3/ }));
  expect(onOriginalPdfChange).toHaveBeenLastCalledWith(true);
  await userEvent.click(screen.getByRole("button", { name: "Passage" }));
  expect(onOriginalPdfChange).toHaveBeenLastCalledWith(false);
});

describe("helpers", () => {
  it("names a page by its host", () => {
    expect(hostOf("https://www.example.org/a?b=1")).toBe("example.org");
    expect(hostOf("nonsense")).toBe("");
  });

  it("marks the text runs that belong to the cited passage and escapes the rest", () => {
    const mark = highlightRenderer("The CD3D gene marks T cells.");
    expect(mark({ str: "CD3D gene" })).toBe("<mark>CD3D gene</mark>");
    expect(mark({ str: "<b>" })).toBe("&lt;b&gt;");
    expect(mark({ str: "of" })).toBe("of");
  });
});
