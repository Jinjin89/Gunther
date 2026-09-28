import type { Assertion, KnowledgeProposal, NotebookNote, SourceDetail, SourceStructure } from "@gunther/contracts";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeBase, makeTrashItem } from "../test/fixtures";
import { ItemPage, type ItemPageProps } from "./ItemPage";

const api = vi.hoisted(() => ({
  source: vi.fn(),
  sourceStructure: vi.fn(),
  notes: vi.fn(),
  updateNote: vi.fn(),
  fileNote: vi.fn(),
  fileSource: vi.fn(),
  updateSourceAssertionStatuses: vi.fn(),
  updateAssertionStatus: vi.fn(),
  proposals: vi.fn(),
  updateProposal: vi.fn(),
  knowledgeBases: vi.fn(),
  reprocessSource: vi.fn(),
  trashSource: vi.fn(),
  trashNote: vi.fn(),
  restoreFromTrash: vi.fn(),
}));

vi.mock("../api", () => ({
  knowledgeApi: api,
  sourceAssetUrl: (id: string) => `/api/assets/${id}`,
  recordingAssetUrl: (id: string) => `/api/recordings/${id}`,
}));

const bases = [makeBase({ id: "biology", title: "Biology" }), makeBase({ id: "computing", title: "Computing", color: "blue" })];

const source = (overrides: Partial<SourceDetail> = {}): SourceDetail => ({
  id: "src_1",
  title: "Captured item",
  kind: "note",
  createdAt: "2026-09-20T10:00:00.000Z",
  assertionCount: 0,
  entityCount: 0,
  content: "Plain **text**.",
  assertions: [],
  webSnapshot: null,
  knowledgeBases: [],
  ...overrides,
});

const note = (overrides: Partial<NotebookNote> = {}): NotebookNote => ({
  id: "note_1",
  title: "Reading plan",
  content: "## This week\n\n- [ ] Read chapter 2\n- [x] Buy the book",
  status: "inbox",
  pinned: false,
  knowledgeBaseId: null,
  promotedSourceId: null,
  createdAt: "2026-09-20T10:00:00.000Z",
  updatedAt: "2026-09-20T10:00:00.000Z",
  ...overrides,
});

const structure = (blocks: SourceStructure["blocks"], state: SourceStructure["processing"]["state"] = "ready"): SourceStructure => ({
  sourceId: "src_1",
  revisionId: "rev_1",
  processing: { state, jobId: null, revisionId: "rev_1", warning: null },
  parser: "gunther-asset-v1",
  nextOffset: null,
  blocks,
});

const renderPage = (overrides: Partial<ItemPageProps> = {}) => {
  const props: ItemPageProps = {
    item: { type: "source", id: "src_1" },
    bases,
    backLabel: "Inbox",
    position: { index: 0, total: 3 },
    onBack: vi.fn(),
    onPrevious: null,
    onNext: vi.fn(),
    onResolved: vi.fn(),
    onOpenBase: vi.fn(),
    onOpenSession: vi.fn(),
    onOpenNotebook: vi.fn(),
    onCreateBase: vi.fn(),
    onNotify: vi.fn(),
    onTitle: vi.fn(),
    ...overrides,
  };
  return { ...render(<ItemPage {...props} />), props };
};

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.sourceStructure.mockResolvedValue(structure([]));
  api.fileSource.mockResolvedValue({});
  api.updateSourceAssertionStatuses.mockResolvedValue([]);
  api.updateProposal.mockResolvedValue({});
});

describe("ItemPage navigation", () => {
  it("goes back with Esc and steps through the queue with J / K", async () => {
    api.source.mockResolvedValue(source());
    const onPrevious = vi.fn();
    const { props } = renderPage({ onPrevious });
    await screen.findByRole("heading", { name: "Captured item", level: 1 });
    expect(screen.getByText("1 of 3")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "j" });
    expect(props.onNext).toHaveBeenCalledOnce();
    fireEvent.keyDown(window, { key: "k" });
    expect(onPrevious).toHaveBeenCalledOnce();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(props.onBack).toHaveBeenCalledOnce();
    // The title is reported from an effect, which can land after the heading renders.
    await waitFor(() => expect(props.onTitle).toHaveBeenCalledWith("Captured item"));
  });

  it("explains a missing item and retries", async () => {
    api.source.mockRejectedValueOnce(new Error("Source src_1 was not found")).mockResolvedValueOnce(source());
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText("Source src_1 was not found")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Retry/ }));
    expect(await screen.findByRole("heading", { name: "Captured item", level: 1 })).toBeVisible();
  });
});

describe("source pages", () => {
  it("files an unfiled source into the chosen library and moves on", async () => {
    api.source.mockResolvedValue(source());
    const user = userEvent.setup();
    const { props } = renderPage();
    await screen.findByRole("heading", { name: "Choose a home" });
    await user.click(screen.getByRole("button", { name: /File to library: Biology/ }));
    await user.click(screen.getByRole("option", { name: /Computing/ }));
    await user.click(screen.getByRole("button", { name: /File to library$/ }));
    await waitFor(() => expect(api.fileSource).toHaveBeenCalledWith("src_1", "computing"));
    expect(props.onResolved).toHaveBeenCalledWith("Filed “Captured item” into Computing.");
  });

  it("files with ⌘↵ / Ctrl+↵", async () => {
    api.source.mockResolvedValue(source());
    const { props } = renderPage();
    await screen.findByRole("heading", { name: "Choose a home" });
    fireEvent.keyDown(window, { key: "Enter", ctrlKey: true, metaKey: true });
    await waitFor(() => expect(api.fileSource).toHaveBeenCalledWith("src_1", "biology"));
    expect(props.onResolved).toHaveBeenCalled();
  });

  it("reviews claims together or one at a time", async () => {
    const claim = (id: string, status: Assertion["status"]): Assertion => ({
      id,
      predicate: "marks",
      confidence: 0.8,
      status,
      qualifiers: {},
      subject: { id: "e1", label: "CD3E", type: "gene" },
      object: { id: "e2", label: "T cells", type: "cell" },
      source: { id: "src_1", title: "Captured item", kind: "paper" },
      evidence: [{ id: "ev", stance: "supports", quote: "CD3E marks T cells.", locator: "Page 1" }],
      createdAt: "2026-09-20T10:00:00.000Z",
    });
    api.source.mockResolvedValue(source({ knowledgeBases: [{ id: "biology", title: "Biology" }], assertions: [claim("a1", "provisional"), claim("a2", "verified")] }));
    api.updateAssertionStatus.mockResolvedValue({});
    const user = userEvent.setup();
    const { props } = renderPage();
    expect(await screen.findByRole("heading", { name: "Review what Gunther found" })).toBeVisible();
    expect(screen.getByText("1 claim to review")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /^Accept: CD3E marks T cells/ }));
    await waitFor(() => expect(api.updateAssertionStatus).toHaveBeenCalledWith("a1", { status: "verified", reason: "Reviewed on the source page" }));
    await user.click(screen.getByRole("button", { name: /Accept all/ }));
    await waitFor(() => expect(api.updateSourceAssertionStatuses).toHaveBeenCalledWith("src_1", { status: "verified", reason: "Accepted from the source page" }));
    expect(props.onResolved).toHaveBeenCalledWith("Accepted 1 claim as trusted knowledge.");
  });

  it("presents a recording with its player, moments, summary and timed transcript", async () => {
    api.source.mockResolvedValue(source({
      kind: "recording",
      title: "Cell biology lecture",
      content: "# Cell biology lecture\n\nDuration: 00:42:10 · Captured: today · Local recording: rec_0123456789abcdef01234567\n\n## Marked moments\n\n- 00:03:05 · Definition\n\n## Summary\n\nMarkers identify **cell types**.\n\n## Key points\n\n- Context matters\n\n## Full transcript\n\n[00:00:01] Welcome back.\n[00:00:09] Today: markers.",
    }));
    const user = userEvent.setup();
    const { container } = renderPage();
    expect(await screen.findByRole("button", { name: "Play" })).toBeVisible();
    expect(container.querySelector("audio")).toHaveAttribute("src", "/api/recordings/rec_0123456789abcdef01234567");
    expect(screen.getByRole("button", { name: /3:05\s*Definition/ })).toBeVisible();
    expect(screen.getByText("cell types").tagName).toBe("STRONG");
    expect(screen.getByText("Context matters")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Transcript" }));
    expect(screen.getByRole("button", { name: "Play from 0:09" })).toBeVisible();
    await user.type(screen.getByRole("textbox", { name: "Find in transcript" }), "markers");
    expect(screen.getByText("1 passage")).toBeVisible();
    expect(screen.queryByText("Welcome back.")).not.toBeInTheDocument();
  });

  it("presents a web snapshot as a site card, your context and the captured text", async () => {
    api.source.mockResolvedValue(source({
      kind: "link",
      title: "Marker genes explained",
      content: "# Web snapshot\n\nOriginal URL: https://example.com/markers\nFinal URL: https://www.example.com/markers\nCaptured at: 2026-09-20T10:00:00.000Z\nHTTP status: 200\nContent type: text/html\nSHA-256: abcdef0123456789abcdef0123456789\nSnapshot ID: snap_1\n\n## Your context\n\nFor the exam\n\n## Captured content\n\nFirst paragraph.\nSecond paragraph.",
    }));
    renderPage();
    expect(await screen.findByText("example.com", { selector: ".gx-site-body strong" })).toBeVisible();
    expect(screen.getByRole("link", { name: /Open page/ })).toHaveAttribute("href", "https://www.example.com/markers");
    expect(screen.getByText("For the exam")).toBeVisible();
    expect(screen.getByText("First paragraph.").tagName).toBe("P");
    expect(screen.getByText("Second paragraph.").tagName).toBe("P");
    expect(screen.getByRole("button", { name: "Copy fingerprint" })).toBeInTheDocument();
  });

  it("rebuilds a document from its indexed blocks with pages, headings and tables", async () => {
    api.source.mockResolvedValue(source({
      kind: "file",
      title: "Report",
      content: "# Original file\n\nFile: report.pdf\nMedia type: application/pdf\nSize: 2516582 bytes\nSHA-256: abc",
      asset: { id: "ast_1", contentHash: "abc", originalName: "report.pdf", mediaType: "application/pdf", sizeBytes: 2516582, downloadUrl: "", createdAt: "" },
    }));
    const block = (id: string, kind: string, content: string, page: number, charStart: number, headings: string[] = []) => ({ id, parentId: null, kind, content, locator: `Page ${page}`, headings, anchor: { page, charStart, charEnd: charStart + content.length } });
    api.sourceStructure.mockResolvedValue(structure([
      block("b1", "heading", "Methods", 1, 0, ["Methods"]),
      block("b2", "paragraph", "First sentence.", 1, 10, ["Methods"]),
      block("b3", "paragraph", "Second sentence.", 1, 26, ["Methods"]),
      block("b4", "table", "| gene | count |", 2, 100),
      block("b5", "table", "| CD3E | 12 |", 2, 117),
    ]));
    renderPage();
    expect(await screen.findByText("report.pdf", { selector: ".gx-file-body strong" })).toBeVisible();
    expect(await screen.findByText("PDF · 2.4 MB · 2 pages")).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Methods" })).toBeVisible();
    expect(screen.getByText("First sentence. Second sentence.")).toBeVisible();
    expect(screen.getByRole("separator", { name: "Page 2" })).toBeInTheDocument();
    const table = screen.getByRole("region", { name: "Table" });
    expect(within(table).getByRole("columnheader", { name: "gene" })).toBeVisible();
    expect(within(table).getByText("CD3E")).toBeVisible();
  });

  it("shows pasted rows as a data grid", async () => {
    api.source.mockResolvedValue(source({ kind: "table", title: "Counts", content: "gene\tcount\nCD3E\t1,204\nMS4A1\t88" }));
    renderPage();
    expect(await screen.findByRole("columnheader", { name: "count" })).toHaveClass("is-number");
    expect(screen.getByText("1,204")).toBeVisible();
    expect(screen.getByText(/Tab-separated/)).toBeVisible();
  });
});

describe("moving to Trash", () => {
  it("moves the open source to Trash from the toolbar, and with ⌘⌫ outside a field", async () => {
    api.source.mockResolvedValue(source());
    api.trashSource.mockResolvedValue(makeTrashItem({ id: "src_1", title: "Captured item" }));
    const onTrashed = vi.fn();
    renderPage({ onTrashed });
    await screen.findByRole("heading", { name: "Captured item", level: 1 });

    await userEvent.click(screen.getByRole("button", { name: "Move to Trash" }));
    expect(api.trashSource).toHaveBeenCalledWith("src_1");
    await waitFor(() => expect(onTrashed).toHaveBeenCalledWith(expect.objectContaining({ id: "src_1" })));

    const field = document.body.appendChild(document.createElement("input"));
    field.focus();
    fireEvent.keyDown(field, { key: "Backspace", ctrlKey: true, metaKey: true });
    expect(api.trashSource).toHaveBeenCalledTimes(1);
    field.remove();

    fireEvent.keyDown(window, { key: "Backspace", ctrlKey: true, metaKey: true });
    await waitFor(() => expect(api.trashSource).toHaveBeenCalledTimes(2));
  });

  it("moves a note to Trash as a note", async () => {
    api.notes.mockResolvedValue([note()]);
    api.trashNote.mockResolvedValue(makeTrashItem({ kind: "note", id: "note_1", title: "Reading plan" }));
    const onTrashed = vi.fn();
    renderPage({ item: { type: "note", id: "note_1" }, onTrashed });
    await userEvent.click(await screen.findByRole("button", { name: "Move to Trash" }));
    expect(api.trashNote).toHaveBeenCalledWith("note_1");
    await waitFor(() => expect(onTrashed).toHaveBeenCalledOnce());
  });

  it("says why a live recording cannot go, and stays", async () => {
    api.source.mockResolvedValue(source());
    api.trashSource.mockRejectedValue(new Error("Stop the recording before moving it to Trash."));
    const { props } = renderPage({ onTrashed: vi.fn() });
    await userEvent.click(await screen.findByRole("button", { name: "Move to Trash" }));
    await waitFor(() => expect(props.onNotify).toHaveBeenCalledWith("Stop the recording before moving it to Trash."));
    expect(props.onTrashed).not.toHaveBeenCalled();
  });

  it("opens a source that is in Trash with Restore instead of filing", async () => {
    api.source.mockResolvedValueOnce(source({ trashedAt: "2026-09-27T10:00:00.000Z" })).mockResolvedValue(source());
    api.restoreFromTrash.mockResolvedValue(makeTrashItem({ id: "src_1" }));
    const { props } = renderPage({ onTrashed: vi.fn() });
    expect(await screen.findByText("This source is in Trash")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Move to Trash" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(api.restoreFromTrash).toHaveBeenCalledWith("source", "src_1");
    await waitFor(() => expect(screen.queryByText("This source is in Trash")).not.toBeInTheDocument());
    expect(props.onNotify).toHaveBeenCalledWith("Restored from Trash.");
  });
});

describe("note pages", () => {
  it("reads as Markdown, ticks tasks in place and autosaves", async () => {
    api.notes.mockResolvedValue([note()]);
    api.updateNote.mockImplementation(async (_id: string, patch: Partial<NotebookNote>) => note({ ...patch }));
    renderPage({ item: { type: "note", id: "note_1" } });
    expect(await screen.findByRole("heading", { name: "This week" })).toBeVisible();
    const tasks = screen.getAllByRole("checkbox");
    expect(tasks.map((task) => task.getAttribute("aria-checked"))).toEqual(["false", "true"]);
    vi.useFakeTimers();
    try {
      fireEvent.click(tasks[0]!);
      await act(async () => { vi.advanceTimersByTime(700); });
    } finally {
      vi.useRealTimers();
    }
    await waitFor(() => expect(api.updateNote).toHaveBeenCalledWith("note_1", { title: "Reading plan", content: "## This week\n\n- [x] Read chapter 2\n- [x] Buy the book" }));
  });

  it("switches to writing with E and files the note after saving", async () => {
    api.notes.mockResolvedValue([note()]);
    api.fileNote.mockResolvedValue({ note: note({ status: "filed", knowledgeBaseId: "biology" }), importResult: {} });
    const user = userEvent.setup();
    const { props } = renderPage({ item: { type: "note", id: "note_1" } });
    await screen.findByRole("heading", { name: "This week" });
    fireEvent.keyDown(window, { key: "e" });
    expect(await screen.findByRole("textbox", { name: "Note title" })).toHaveValue("Reading plan");
    await user.click(screen.getByRole("button", { name: /Read/ }));
    await user.click(screen.getByRole("button", { name: /File to library$/ }));
    await waitFor(() => expect(api.fileNote).toHaveBeenCalledWith("note_1", "biology"));
    expect(props.onResolved).toHaveBeenCalledWith("Filed “Reading plan” into Biology.");
  });
});

describe("suggestion pages", () => {
  const proposal = (overrides: Partial<KnowledgeProposal> = {}): KnowledgeProposal => ({
    id: "prop_1",
    knowledgeBaseId: "biology",
    sessionId: "ses_1",
    messageId: "msg_1",
    targetChapterId: null,
    kind: "knowledge_unit",
    title: "Markers need context",
    content: "Marker genes are **context dependent**.",
    status: "pending",
    decisionReason: null,
    knowledgeUnitId: null,
    sourceSessionTitle: "Marker questions",
    createdAt: "2026-09-20T10:00:00.000Z",
    updatedAt: "2026-09-20T10:00:00.000Z",
    ...overrides,
  });

  it("renders the proposed unit, links to its conversation and accepts it", async () => {
    api.proposals.mockResolvedValue([proposal()]);
    const user = userEvent.setup();
    const { props } = renderPage({ item: { type: "suggestion", id: "prop_1", baseId: "biology" } });
    expect(await screen.findByText("context dependent")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Marker questions/ }));
    expect(props.onOpenSession).toHaveBeenCalledWith("biology", "ses_1", "msg_1");
    await user.click(screen.getByRole("button", { name: /^Accept/ }));
    await waitFor(() => expect(api.updateProposal).toHaveBeenCalledWith("prop_1", { status: "accepted", reason: "Accepted from the suggestion page" }));
    expect(props.onResolved).toHaveBeenCalledWith("“Markers need context” is now trusted knowledge.");
  });
});
