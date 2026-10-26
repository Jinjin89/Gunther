import type { SourceDetail, SourceDocument } from "@gunther/contracts";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseFileContent } from "../sourceContent";
import { DocumentBody } from "./DocumentView";

const api = vi.hoisted(() => ({ sourceDocument: vi.fn(), sourceStructure: vi.fn(), reprocessSource: vi.fn(), sourcePaper: vi.fn() }));
vi.mock("../../api", () => ({ knowledgeApi: api, sourceAssetUrl: (id: string) => `/api/assets/${id}` }));

const source: SourceDetail = {
  id: "src_layout", title: "Report", kind: "paper", createdAt: "", assertionCount: 0, entityCount: 0,
  assertions: [], webSnapshot: null, knowledgeBases: [],
  content: "# Original file\n\nFile: report.pdf\nMedia type: application/pdf",
  asset: { id: "ast_pdf", contentHash: "abc", originalName: "report.pdf", mediaType: "application/pdf", sizeBytes: 100, downloadUrl: "", createdAt: "" },
};
const file = parseFileContent(source.content);
function document(state: SourceDocument["processing"]["state"], revisionId = "native"): SourceDocument {
  return {
    sourceId: source.id, revisionId, parser: "fixture", nextOffset: null, manifest: {}, references: [],
    processing: { state, jobId: "job_layout", revisionId, warning: state === "failed" ? "Layout parsing failed: the time budget was exceeded. The previous document revision was retained." : null },
    elements: [{ id: "paragraph", kind: "paragraph", content: revisionId === "native" ? "Native readable paragraph." : "Layout readable paragraph.", parentId: null, locator: "Page 1", headings: [], anchor: { page: 1 }, mentions: [] }],
  };
}

/** A document the structure plan assembled (schema 2), with the state of its structure check. */
function structured(structure: Record<string, unknown>, state: SourceDocument["processing"]["state"] = "ready"): SourceDocument {
  return { ...document(state, "layout"), manifest: { schema: 2, documentType: "paper", structure } };
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.sourcePaper.mockRejectedValue(new Error("Not yet summarized"));
  api.reprocessSource.mockResolvedValue({});
});
afterEach(() => { vi.useRealTimers(); });

describe("document layout status", () => {
  it("keeps native content visible while reading layout and polls for the replacement", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.sourceDocument.mockResolvedValueOnce(document("running")).mockResolvedValue(document("ready", "layout"));
    render(<DocumentBody source={source} file={file} />);
    expect(await screen.findByText("Reading layout…")).toBeVisible();
    expect(screen.getByText("Native readable paragraph.")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(await screen.findByText("Layout readable paragraph.")).toBeVisible();
    expect(screen.getByText("Read and indexed")).toBeVisible();
    expect(screen.queryByText("Reading layout…")).not.toBeInTheDocument();
  });

  it("explains failure and retries without clearing the readable revision", async () => {
    api.sourceDocument.mockResolvedValue(document("failed"));
    render(<DocumentBody source={source} file={file} />);
    expect(await screen.findByText("Layout parsing failed")).toBeVisible();
    expect(screen.getByText(/the time budget was exceeded/)).toBeVisible();
    expect(screen.getByText("Native readable paragraph.")).toBeVisible();
    let complete!: (value: SourceDocument) => void;
    api.sourceDocument.mockReturnValue(new Promise<SourceDocument>((resolve) => { complete = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.reprocessSource).toHaveBeenCalledWith(source.id));
    await waitFor(() => expect(api.sourceDocument).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Native readable paragraph.")).toBeVisible();
    await act(async () => { complete(document("running")); });
    expect(await screen.findByText("Reading layout…")).toBeVisible();
  });

  it("shows cancellation with retry and preserves the existing text", async () => {
    api.sourceDocument.mockResolvedValue(document("cancelled"));
    render(<DocumentBody source={source} file={file} />);
    expect(await screen.findByText("Layout reading was cancelled")).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
    expect(screen.getByText("Native readable paragraph.")).toBeVisible();
  });

  it("continues the same revision before the bibliography and exposes a retryable page error", async () => {
    const first = document("ready", "layout");
    first.nextOffset = 500;
    first.references = [{ id: "ref", elementId: "reference", label: "1", raw: "A source reference.", metadata: {} }];
    api.sourceDocument.mockResolvedValueOnce(first).mockRejectedValueOnce(new Error("Could not load the next page."));
    render(<DocumentBody source={source} file={file} />);
    const button = await screen.findByRole("button", { name: "Continue reading" });
    expect(button.compareDocumentPosition(screen.getByRole("heading", { name: "References" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the next page.");
    const next = document("ready", "layout");
    next.references = first.references;
    next.elements = [{ ...next.elements[0]!, id: "next", content: "The next complete paragraph." }];
    api.sourceDocument.mockResolvedValueOnce(next);
    fireEvent.click(button);
    expect(await screen.findByText("The next complete paragraph.")).toBeVisible();
    expect(api.sourceDocument).toHaveBeenLastCalledWith(source.id, 500, "layout");
    expect(screen.getAllByText("Layout readable paragraph.")).toHaveLength(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue reading" })).not.toBeInTheDocument();
  });

  it("can refresh an already completed extraction without removing the current text", async () => {
    api.sourceDocument.mockResolvedValue(document("ready"));
    render(<DocumentBody source={source} file={file} />);
    fireEvent.click(await screen.findByRole("button", { name: "Read again" }));
    await waitFor(() => expect(api.reprocessSource).toHaveBeenCalledWith(source.id));
    expect(screen.getByText("Native readable paragraph.")).toBeVisible();
  });

  it("explains a failed structure check and tries again with the same Read again action", async () => {
    api.sourceDocument.mockResolvedValue(structured({ state: "failed", by: "rules", error: "The model did not answer in the expected form." }));
    render(<DocumentBody source={source} file={file} />);
    expect(await screen.findByText("Structure check failed: The model did not answer in the expected form.")).toBeVisible();
    expect(screen.getByText("Read and indexed")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.reprocessSource).toHaveBeenCalledWith(source.id));
    await waitFor(() => expect(api.sourceDocument).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Layout readable paragraph.")).toBeVisible();
  });

  it("names the model that checked the structure and waits while the document is read again", async () => {
    const checked = { state: "checked", by: "deepseek:deepseek-flash", error: null };
    api.sourceDocument.mockResolvedValueOnce(structured(checked)).mockResolvedValue(structured(checked, "running"));
    render(<DocumentBody source={source} file={file} />);
    expect(await screen.findByText("Structure checked by deepseek-flash")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect(await screen.findByText("Reading layout…")).toBeVisible();
    expect(screen.queryByText("Structure checked by deepseek-flash")).not.toBeInTheDocument();
  });

  it("asks for a model when the structure was only drafted, and shows nothing for an older document", async () => {
    api.sourceDocument.mockResolvedValueOnce(structured({ state: "needs-key", by: "rules", error: null }));
    const drafted = render(<DocumentBody source={source} file={file} />);
    expect(await screen.findByText("Structure not checked by a model. Add one in Settings → Models, then Read again.")).toBeVisible();
    drafted.unmount();
    api.sourceDocument.mockResolvedValueOnce(document("ready", "layout"));
    const older = render(<DocumentBody source={source} file={file} />);
    expect(await screen.findByText("Layout readable paragraph.")).toBeVisible();
    expect(older.container.querySelector(".gx-structure-status")).toBeNull();
  });
});
