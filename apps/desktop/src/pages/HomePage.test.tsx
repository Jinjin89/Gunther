import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeBase, makeSource } from "../test/fixtures";
import { HomePage } from "./HomePage";

const api = vi.hoisted(() => ({
  sources: vi.fn(),
  search: vi.fn(),
  webSearch: vi.fn(),
  importSource: vi.fn(),
}));

vi.mock("../api", () => ({
  knowledgeApi: api,
}));

const searchResult = (overrides: Partial<Awaited<ReturnType<typeof api.search>>[number]> = {}) => ({
  id: "src_1",
  knowledgeBaseId: "base_1",
  kind: "source" as const,
  title: "Preserved paper",
  snippet: "A matching source",
  meta: "paper",
  updatedAt: new Date().toISOString(),
  sourceSessionId: null,
  sourceMessageId: null,
  ...overrides,
});

const renderHome = (overrides: Partial<React.ComponentProps<typeof HomePage>> = {}) => {
  const props: React.ComponentProps<typeof HomePage> = {
    bases: [],
    inboxCount: 0,
    onCapture: vi.fn(),
    onOpenBase: vi.fn(),
    onOpenChapter: vi.fn(),
    onAskBase: vi.fn(),
    onOpenNote: vi.fn(),
    onOpenLibraries: vi.fn(),
    onCreateBase: vi.fn(),
    onOpenInbox: vi.fn(),
    onNotify: vi.fn(),
    resolveWorkspaceId: vi.fn().mockResolvedValue("wsp_primary"),
    ...overrides,
  };
  return { ...render(<HomePage {...props} />), props };
};

const searchBox = () => screen.getByRole("textbox", { name: "Search your knowledge or the web" });

describe("HomePage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    api.sources.mockResolvedValue([]);
    api.search.mockResolvedValue([]);
    api.webSearch.mockResolvedValue({ query: "", answer: "", sources: [], mode: "not_configured", message: "Online search is not configured." });
  });

  it("opens on a focused search composer with a greeting", () => {
    renderHome({ profileName: "Keke Sun" });

    expect(searchBox()).toHaveFocus();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/, Keke$/);
  });

  it("keeps every capture source one click away and preserves its exact kind", async () => {
    const user = userEvent.setup();
    const { props } = renderHome();

    const actions = [
      ["Quick note", "note"],
      ["Document", "file"],
      ["Photo or scan", "image"],
      ["Web page", "link"],
      ["Recording", "recording"],
      ["Table or data", "table"],
    ] as const;

    for (const [label, kind] of actions) {
      await user.click(screen.getByRole("button", { name: label }));
      expect(props.onCapture).toHaveBeenLastCalledWith(kind);
    }

    await user.click(screen.getByRole("button", { name: "All capture options" }));
    expect(props.onCapture).toHaveBeenLastCalledWith();
  });

  it("makes an empty workspace useful without forcing a library first", async () => {
    const user = userEvent.setup();
    const { props } = renderHome({ inboxCount: 2 });

    expect(screen.getByText("No libraries yet.")).toBeVisible();
    expect(screen.getByText("2 items waiting in Inbox")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /New library/i }));
    await user.click(screen.getByRole("button", { name: /2 items waiting in Inbox/i }));

    expect(props.onCreateBase).toHaveBeenCalledOnce();
    expect(props.onOpenInbox).toHaveBeenCalledOnce();
  });

  it("shows only the three most recent libraries and opens the selected one", async () => {
    const user = userEvent.setup();
    const bases = [
      makeBase({ id: "base-1", title: "Biology" }),
      makeBase({ id: "base-2", title: "Machine Learning" }),
      makeBase({ id: "base-3", title: "Product Strategy" }),
      makeBase({ id: "base-4", title: "Hidden fourth base" }),
    ];
    const { props } = renderHome({ bases });
    const recent = screen.getByRole("region", { name: "Jump back in" });

    expect(within(recent).getByRole("button", { name: /Biology/i })).toBeVisible();
    expect(within(recent).getByRole("button", { name: /Machine Learning/i })).toBeVisible();
    expect(within(recent).getByRole("button", { name: /Product Strategy/i })).toBeVisible();
    expect(within(recent).queryByText("Hidden fourth base")).not.toBeInTheDocument();

    await user.click(within(recent).getByRole("button", { name: /Machine Learning/i }));
    expect(props.onOpenBase).toHaveBeenCalledWith("base-2");
  });

  it("loads at most four recently captured sources and keeps source kinds explicit", async () => {
    api.sources.mockResolvedValue([
      makeSource({ id: "s1", title: "Lecture recording", kind: "recording" }),
      makeSource({ id: "s2", title: "Research paper", kind: "paper" }),
      makeSource({ id: "s3", title: "Lab notes", kind: "note" }),
      makeSource({ id: "s4", title: "Reference site", kind: "link" }),
      makeSource({ id: "s5", title: "Fifth source", kind: "file" }),
    ]);

    renderHome();

    await waitFor(() => expect(api.sources).toHaveBeenCalledOnce());
    expect(await screen.findByText("Lecture recording")).toBeVisible();
    expect(screen.getByText("Research paper")).toBeVisible();
    expect(screen.getByText("Lab notes")).toBeVisible();
    expect(screen.getByText("Reference site")).toBeVisible();
    expect(screen.queryByText("Fifth source")).not.toBeInTheDocument();
    expect(screen.getByText(/Recording ·/)).toBeVisible();
    expect(screen.getByText(/Paper ·/)).toBeVisible();
  });
});

describe("HomePage search", () => {
  beforeEach(() => {
    window.localStorage.clear();
    api.sources.mockResolvedValue([]);
    api.search.mockResolvedValue([]);
    api.webSearch.mockResolvedValue({ query: "", answer: "", sources: [], mode: "not_configured", message: "Online search is not configured." });
  });

  it("searches only your knowledge by default and opens a source in Sources", async () => {
    api.search.mockResolvedValueOnce([searchResult()]);
    const user = userEvent.setup();
    const { props } = renderHome();

    await user.type(searchBox(), "paper");
    await user.click(screen.getByRole("button", { name: "Run search" }));
    await user.click(await screen.findByRole("button", { name: /Preserved paper/i }));

    expect(api.search).toHaveBeenCalledWith("paper");
    expect(api.webSearch).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("gunther:open-source:base_1")).toBe("src_1");
    expect(props.onOpenBase).toHaveBeenCalledWith("base_1", "sources");
  });

  it("sends unfiled captures to Inbox", async () => {
    api.search.mockResolvedValueOnce([searchResult({ id: "src_unfiled", knowledgeBaseId: null, title: "Unsorted scan" })]);
    const user = userEvent.setup();
    const { props } = renderHome();

    await user.type(searchBox(), "scan{Enter}");
    const result = await screen.findByRole("button", { name: /Unsorted scan/ });
    expect(result).toHaveTextContent("Inbox");
    await user.click(result);

    expect(props.onOpenInbox).toHaveBeenCalledOnce();
    expect(props.onOpenBase).not.toHaveBeenCalled();
  });

  it("includes the web only when asked, and remembers the choice", async () => {
    const user = userEvent.setup();
    renderHome();

    await user.click(screen.getByRole("button", { name: "Web" }));
    await user.type(searchBox(), "annotation{Enter}");

    await waitFor(() => expect(api.webSearch).toHaveBeenCalledWith("annotation"));
    expect(await screen.findByText("Web search isn’t set up yet.")).toBeVisible();
    expect(window.localStorage.getItem("gunther:search-web")).toBe("on");
  });

  it("does not let an older slow query overwrite newer results", async () => {
    let resolveOlder: ((value: Awaited<ReturnType<typeof api.search>>) => void) | undefined;
    api.search
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOlder = resolve; }))
      .mockResolvedValueOnce([searchResult({ id: "src_new", title: "Newer result", snippet: "The latest query" })]);
    const user = userEvent.setup();
    renderHome();

    await user.type(searchBox(), "older");
    await user.click(screen.getByRole("button", { name: "Run search" }));
    await user.clear(searchBox());
    await user.type(searchBox(), "newer");
    await user.click(screen.getByRole("button", { name: "Run search" }));
    expect(await screen.findByRole("button", { name: /Newer result/i })).toBeInTheDocument();

    resolveOlder?.([searchResult({ id: "src_old", title: "Older stale result", snippet: "Must not replace the latest query" })]);
    await waitFor(() => expect(screen.queryByText("Older stale result")).not.toBeInTheDocument());
    expect(screen.getByText("Newer result")).toBeInTheDocument();
  });

  it("uses @ to pick a library, scopes results to it, and hands the question to Ask", async () => {
    const bases = [
      makeBase({ id: "biology", title: "Biology", color: "green" }),
      makeBase({ id: "computing", title: "Computing", eyebrow: "Software", color: "blue" }),
    ];
    api.search.mockResolvedValueOnce([
      searchResult({ id: "bio-src", knowledgeBaseId: "biology", title: "Cell atlas notes", snippet: "marker genes for T cells" }),
      searchResult({ id: "cs-src", knowledgeBaseId: "computing", title: "Compiler notes", snippet: "marker passes" }),
      searchResult({ id: "note-1", knowledgeBaseId: null, kind: "note", title: "Loose note", snippet: "marker" }),
    ]);
    const user = userEvent.setup();
    const { props } = renderHome({ bases });

    await user.type(searchBox(), "@bio");
    const menu = screen.getByRole("listbox", { name: "Libraries" });
    expect(within(menu).getByRole("option", { name: /Biology/ })).toHaveAttribute("aria-selected", "true");
    expect(within(menu).queryByRole("option", { name: /Computing/ })).not.toBeInTheDocument();

    await user.keyboard("{Enter}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Biology" })).toBeVisible();
    expect(searchBox()).toHaveValue("");

    await user.type(searchBox(), "marker genes{Enter}");
    await waitFor(() => expect(api.search).toHaveBeenCalledWith("marker genes", 40, ["biology"]));
    expect(await screen.findByRole("button", { name: /Cell atlas notes/ })).toBeVisible();
    expect(screen.queryByText("Compiler notes")).not.toBeInTheDocument();
    expect(screen.queryByText("Loose note")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Ask Biology/ }));
    expect(props.onAskBase).toHaveBeenCalledWith("biology", "marker genes");
  });

  it("opens a mentioned library directly when there is nothing else to search", async () => {
    const user = userEvent.setup();
    const { props } = renderHome({ bases: [makeBase({ id: "biology", title: "Biology" })] });

    await user.type(searchBox(), "@Bio{Enter}");
    await user.click(screen.getByRole("button", { name: "Open library" }));

    expect(props.onOpenBase).toHaveBeenCalledWith("biology");
    expect(api.search).not.toHaveBeenCalled();
  });

  it("closes the library menu on Escape and removes the last library with Backspace", async () => {
    const user = userEvent.setup();
    renderHome({ bases: [makeBase({ id: "biology", title: "Biology" })] });

    await user.type(searchBox(), "@");
    expect(screen.getByRole("listbox", { name: "Libraries" })).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await user.clear(searchBox());
    await user.click(screen.getByRole("button", { name: "Choose a library" }));
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Remove Biology" })).toBeVisible();

    await user.keyboard("{Backspace}");
    expect(screen.queryByRole("button", { name: "Remove Biology" })).not.toBeInTheDocument();
  });

  it("opens the library menu after CJK text but not inside an email address", async () => {
    const user = userEvent.setup();
    renderHome({ bases: [makeBase({ id: "biology", title: "Biology" })] });

    await user.type(searchBox(), "me@lab");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await user.clear(searchBox());
    await user.type(searchBox(), "关于@bio");
    expect(screen.getByRole("listbox", { name: "Libraries" })).toBeVisible();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Remove Biology" })).toBeVisible();
    expect(searchBox()).toHaveValue("关于");
  });

  it("moves between results with the arrow keys and returns to the search box", async () => {
    api.search.mockResolvedValueOnce([
      searchResult({ id: "a", title: "First paper" }),
      searchResult({ id: "b", title: "Second paper" }),
    ]);
    const user = userEvent.setup();
    renderHome();

    await user.type(searchBox(), "paper{Enter}");
    const first = await screen.findByRole("button", { name: /First paper/ });
    const second = screen.getByRole("button", { name: /Second paper/ });

    await user.keyboard("{ArrowDown}");
    expect(first).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(second).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(second).toHaveFocus();
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(searchBox()).toHaveFocus();
  });

  it("returns to the calm Home when Escape clears a search", async () => {
    api.search.mockResolvedValueOnce([searchResult()]);
    const user = userEvent.setup();
    renderHome();

    await user.type(searchBox(), "paper{Enter}");
    expect(await screen.findByRole("region", { name: "Search results" })).toBeVisible();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Search results" })).not.toBeInTheDocument();
    expect(searchBox()).toHaveValue("");
    expect(screen.getByRole("region", { name: "Jump back in" })).toBeVisible();
  });
});
