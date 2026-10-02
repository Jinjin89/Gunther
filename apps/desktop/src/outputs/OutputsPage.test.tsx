import type { Artifact, ArtifactSummary, ConversationCitation, KnowledgeUnit, ModelMenu } from "@gunther/contracts";
import { invoke } from "@tauri-apps/api/core";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi, OutputStoppedError, type OutputEvent } from "../api";
import { OPEN_SETTINGS_EVENT } from "../models/askModel";
import { makeBase, makeSource } from "../test/fixtures";
import { OutputsPage } from "./OutputsPage";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

// CodeMirror is a contenteditable surface; a plain textarea stands in for it here.
vi.mock("../components/markdown/MarkdownEditor", () => ({
  MarkdownEditor: ({ value, onChange, ariaLabel }: { value: string; onChange: (value: string) => void; ariaLabel: string }) => (
    <textarea aria-label={ariaLabel} value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));

const menu: ModelMenu = {
  models: [{ ref: "deepseek/deepseek-flash", provider: "DeepSeek", label: "Flash", display: "DeepSeek · Flash", vision: false, levels: [] }],
  default: { model: "deepseek/deepseek-flash", effort: "high" },
  efforts: [],
};

const citation: ConversationCitation = {
  id: "cit_1", kind: "web", url: "https://www.example.org/markers", sourceId: "", sourceTitle: "Marker review", assertionId: null,
  quote: "CD3D is a marker of T cells.", locator: "example.org", status: "provisional", confidence: 0, ref: 1,
};

const unit = (id: string, title: string, status: KnowledgeUnit["status"] = "trusted"): KnowledgeUnit => ({
  id, knowledgeBaseId: "biology", title, kind: "knowledge_unit", status, content: "x", revisionCount: 1, sourceProposalId: "prp_1",
  sourceSessionId: "ses_1", sourceMessageId: "msg_1", targetChapterId: null, evidenceCount: 1, createdAt: "2026-10-01T00:00:00.000Z", updatedAt: "2026-10-01T00:00:00.000Z",
});

function version(number = 1, overrides: Partial<Artifact> = {}): Artifact {
  return {
    id: `art_${number}`, workspaceId: "wsp_primary", knowledgeBaseId: "biology", lineageId: "arl_1", versionNumber: number,
    supersedesArtifactId: number > 1 ? `art_${number - 1}` : null, format: "report", audience: "scientist", title: `Immune markers v${number}`,
    contentHash: "a".repeat(64), manifestHash: "ab".repeat(32), acceptedUnitIds: [], unitCount: 0, createdAt: `2026-10-01T00:0${number}:00.000Z`,
    kind: "report", style: "overview", origin: "build", content: "# Immune markers\n\n_For the lab_\n\n## T cells\n\nCD3D marks T cells [1].\n\n## B cells\n\nCD19 marks B cells [?].",
    unitSnapshots: [],
    provenance: { schemaVersion: 2, generator: "gunther.output-agents.v1", workspaceId: "wsp_primary", knowledgeBaseId: "biology", knowledgeBaseQuestion: "What marks cells?", acceptedOnly: false, acceptedUnitIds: [], revisionIds: [] },
    brief: "For the lab", outline: [{ heading: "T cells", goal: "How T cells are told apart" }, { heading: "B cells", goal: "" }],
    citations: [citation], scope: { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] }, inputs: { sources: 2, units: 1, sessions: 0 }, modelLabel: "DeepSeek · Flash",
    sections: [{ index: 0, heading: "T cells", checked: true, issues: [] }, { index: 1, heading: "B cells", checked: true, issues: [] }],
    ...overrides,
  };
}

const summary = (value: Artifact): ArtifactSummary => ({
  id: value.id, workspaceId: value.workspaceId, knowledgeBaseId: value.knowledgeBaseId, lineageId: value.lineageId, versionNumber: value.versionNumber,
  supersedesArtifactId: value.supersedesArtifactId, format: value.format, audience: value.audience, title: value.title, contentHash: value.contentHash,
  manifestHash: value.manifestHash, acceptedUnitIds: value.acceptedUnitIds, unitCount: value.unitCount, createdAt: value.createdAt, kind: value.kind, style: value.style, origin: value.origin,
});

function setup(options: { from?: string; history?: Artifact[]; menu?: ModelMenu; sources?: number; units?: KnowledgeUnit[]; follow?: typeof knowledgeApi.followOutputBuild } = {}) {
  const history = options.history ?? [];
  vi.spyOn(knowledgeApi, "modelMenu").mockResolvedValue(options.menu ?? menu);
  vi.spyOn(knowledgeApi, "sources").mockResolvedValue(Array.from({ length: options.sources ?? 2 }, (_, index) => makeSource({ id: `src_${index + 1}`, title: index === 0 ? "Markers" : `Source ${index + 1}`, kind: "note" })));
  vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue(options.units ?? [unit("unt_1", "T-cell identity"), unit("unt_2", "Removed answer", "deprecated")]);
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([]);
  vi.spyOn(knowledgeApi, "artifacts").mockResolvedValue(history.map(summary));
  vi.spyOn(knowledgeApi, "artifact").mockImplementation(async (_base, id) => history.find((item) => item.id === id) ?? version(1));
  vi.spyOn(knowledgeApi, "followOutputBuild").mockImplementation(options.follow ?? (async () => null));
  vi.spyOn(knowledgeApi, "health").mockRejectedValue(new Error("No web search in these tests."));
  const onNotify = vi.fn();
  const onAdd = vi.fn();
  const onFromDiscussion = vi.fn();
  render(<OutputsPage base={makeBase({ id: "biology", title: "Biology", question: "What marks cells?" })} workspaceId="wsp_primary" onNotify={onNotify} onAdd={onAdd} fromDiscussion={options.from ?? null} onFromDiscussion={onFromDiscussion} />);
  return { onNotify, onAdd, onFromDiscussion };
}

/** A promise settled from the test, to hold a build part-way. */
function held<T>() {
  let release: (value: T) => void = () => undefined;
  let fail: (reason: Error) => void = () => undefined;
  const promise = new Promise<T>((resolve, reject) => { release = resolve; fail = reject; });
  return { promise, release, fail };
}

const buildButton = () => screen.findByRole("button", { name: /^(Build|Rebuild)$/ });

beforeEach(() => window.localStorage.clear());

describe("building an output", () => {
  it("shows the plan, then each section as it is written, then opens the saved version", async () => {
    const user = userEvent.setup();
    const saved = version(1);
    const finish = held<Artifact>();
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockImplementation(async (_base, _request, _workspace, onEvent) => {
      const send = (event: OutputEvent) => act(() => onEvent(event));
      send({ type: "outline", title: "Immune markers", sections: [{ heading: "T cells", goal: "How T cells are told apart" }, { heading: "B cells", goal: "" }] });
      send({ type: "section", index: 0, state: "writing" });
      send({ type: "text", section: 0, text: "## T cells\n\nCD3D marks T cells [1]." });
      return finish.promise;
    });
    const { onNotify } = setup();

    await user.type(await screen.findByPlaceholderText("What should it cover, and for whom?"), "For the lab");
    await user.click(await buildButton());

    // The plan shows first, and the text as it comes, without the numbers that are not final.
    expect(await screen.findByText("Immune markers")).toBeVisible();
    expect(screen.getByText("How T cells are told apart")).toBeVisible();
    expect(await screen.findByText(/CD3D marks T cells\./)).toBeVisible();
    expect(screen.queryByText(/\[1\]/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Building…" })).toBeDisabled();

    await act(async () => finish.release(saved));
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    expect(onNotify).toHaveBeenCalledWith("Report version 1 saved.");
    expect(build).toHaveBeenCalledTimes(1);
    expect(build.mock.calls[0]?.[0]).toBe("biology");
    expect(build.mock.calls[0]?.[1]).toEqual({
      clientRequestId: expect.stringMatching(/^[A-Za-z0-9_-]{8,128}$/),
      kind: "report", style: "auto", audience: "scientist", brief: "For the lab",
      scope: { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] },
    });
    expect(build.mock.calls[0]?.[2]).toBe("wsp_primary");
  });

  it("builds slides without a style, and the next build on a version is a rebuild of it", async () => {
    const user = userEvent.setup();
    const first = version(1, { kind: "slides", format: "slides", style: null, content: "# Deck\n\n---\n\n## One\n\n- a [1]\n\nNote: Say a." });
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValueOnce(first).mockResolvedValueOnce(version(2));
    setup();

    await user.click(await screen.findByRole("button", { name: "Slides" }));
    expect(screen.queryByRole("group", { name: "Style" })).not.toBeInTheDocument();
    await user.click(await buildButton());
    expect(build.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ kind: "slides", audience: "scientist" }));
    expect(build.mock.calls[0]?.[1]).not.toHaveProperty("style");
    expect(build.mock.calls[0]?.[1]).not.toHaveProperty("supersedesArtifactId");

    // Opened, a deck is shown in the viewer, with the speaker's notes of the slide in view.
    expect(await screen.findByText("Slide 1 of 2")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));
    expect(screen.getByRole("region", { name: "Speaker notes" })).toHaveTextContent("Say a.");
    await user.click(screen.getByRole("button", { name: "Rebuild" }));
    expect(build.mock.calls[1]?.[1]).toEqual(expect.objectContaining({ supersedesArtifactId: "art_1" }));
  });

  it("shows the approach a skill took, and rebuilds with the question as the person rewrote it", async () => {
    const user = userEvent.setup();
    const skilled = version(1, {
      style: "auto",
      provenance: {
        ...version(1).provenance,
        generator: "gunther.output-skills.v1",
        skill: { name: "report", version: 1, skipped: [{ step: "reader_test", label: "Reading it as the audience", reason: "The reader is down" }] },
        approach: {
          question: "How are T and B cells told apart?", answer: "By CD3D and CD19.", purpose: "Scientists choosing a panel.",
          structure: "which", structureReason: "Two options are compared.", pages: null,
          supplements: [{ title: "Marker review", url: "https://www.example.org/markers", role: "update", why: "Newer than the library." }],
          gaps: ["How stable the markers are in culture"],
        },
      },
    });
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(2));
    setup({ history: [skilled] });

    await user.click(await screen.findByRole("button", { name: /Approach/ }));
    const panel = screen.getByRole("region", { name: "Approach" });
    expect(panel).toHaveTextContent("By CD3D and CD19.");
    expect(panel).toHaveTextContent("Options compared, then a recommendation");
    expect(panel).toHaveTextContent("Marker review · Latest");
    expect(panel).toHaveTextContent("How stable the markers are in culture");
    expect(panel).toHaveTextContent("Reading it as the audienceThe reader is down");

    await user.click(screen.getByRole("button", { name: "Edit approach" }));
    const question = screen.getByLabelText("Question");
    await user.clear(question);
    await user.type(question, "Which marker should a panel use?");
    await user.click(screen.getByRole("button", { name: "Rebuild with this approach" }));
    expect(build.mock.calls[0]?.[1]).toEqual(expect.objectContaining({
      style: "auto",
      supersedesArtifactId: "art_1",
      approach: { question: "Which marker should a panel use?", answer: "By CD3D and CD19.", purpose: "Scientists choosing a panel." },
    }));
  });

  it("asks for a deck to be read when Reading is chosen", async () => {
    const user = userEvent.setup();
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(1, { kind: "slides", format: "slides", style: null, content: "# Deck\n\nsub" }));
    setup();

    await user.click(await screen.findByRole("button", { name: "Slides" }));
    await user.click(screen.getByRole("button", { name: "Reading" }));
    await user.click(await buildButton());
    expect(build.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ kind: "slides", use: "read" }));
  });

  it("says why nothing was saved and sends the same request again on Retry", async () => {
    const user = userEvent.setup();
    const build = vi.spyOn(knowledgeApi, "buildOutputStream")
      .mockRejectedValueOnce(new Error("DeepSeek did not accept the API key."))
      .mockResolvedValueOnce(version(1));
    setup();

    await user.click(await buildButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("The output wasn’t saved");
    expect(screen.getByRole("alert")).toHaveTextContent("DeepSeek did not accept the API key.");
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    expect(build).toHaveBeenCalledTimes(2);
    expect(build.mock.calls[1]?.[1]).toBe(build.mock.calls[0]?.[1]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stops a build and keeps nothing", async () => {
    const user = userEvent.setup();
    const running = held<Artifact>();
    vi.spyOn(knowledgeApi, "buildOutputStream").mockImplementation(async (_base, _request, _workspace, onEvent) => {
      act(() => onEvent({ type: "outline", title: "Immune markers", sections: [{ heading: "T cells", goal: "" }] }));
      return running.promise;
    });
    const stop = vi.spyOn(knowledgeApi, "stopOutputBuild").mockImplementation(async () => { running.fail(new OutputStoppedError()); });
    const { onNotify } = setup();

    await user.click(await buildButton());
    await user.click(await screen.findByRole("button", { name: "Stop" }));

    await waitFor(() => expect(onNotify).toHaveBeenCalledWith("Stopped. Nothing was saved."));
    expect(stop).toHaveBeenCalledWith("biology", "wsp_primary");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await buildButton()).toBeEnabled();
  });

  it("follows a build that is still running when the page opens", async () => {
    const running = held<Artifact | null>();
    setup({
      follow: async (_base, _workspace, onEvent) => {
        act(() => {
          onEvent({ type: "resumed", question: "Cover B cells", startedAt: 1 });
          onEvent({ type: "outline", title: "Immune markers", sections: [{ heading: "B cells", goal: "" }] });
          onEvent({ type: "section", index: 0, state: "researching" });
        });
        return running.promise;
      },
    });

    expect(await screen.findByText("Cover B cells")).toBeVisible();
    expect(screen.getByText("Looking things up")).toBeVisible();
    expect(screen.getByRole("button", { name: "Building…" })).toBeDisabled();
    await act(async () => running.release(version(1)));
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
  });
});

describe("what an output is built from", () => {
  it("counts the whole library, then only what was chosen", async () => {
    const user = userEvent.setup();
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(1));
    setup();

    // Two sources and one saved answer (the removed one does not count).
    expect(await screen.findByText("2 sources · 1 saved")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Choose…" }));
    const dialog = await screen.findByRole("dialog", { name: "Choose what to use" });
    expect(await within(dialog).findByText("Markers")).toBeVisible();
    expect(within(dialog).queryByText("Removed answer")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Use these" })).toBeDisabled();

    await user.click(within(dialog).getByRole("checkbox", { name: /Markers/ }));
    await user.click(within(dialog).getByRole("checkbox", { name: /T-cell identity/ }));
    expect(within(dialog).getByText("1 source · 1 saved")).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Use these" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("1 source · 1 saved")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Build" }));
    expect(build.mock.calls[0]?.[1].scope).toEqual({ mode: "selection", sourceIds: ["src_1"], unitIds: ["unt_1"], sessionIds: [] });

    await user.click(screen.getByRole("button", { name: "Whole library" }));
    expect(screen.getByText("2 sources · 1 saved")).toBeVisible();
  });

  it("starts a new output from a conversation, even when an earlier version is the one that opens", async () => {
    const user = userEvent.setup();
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(2));
    const { onFromDiscussion } = setup({ history: [version(1)], from: "ses_1" });

    expect(await screen.findByText("1 discussion")).toBeVisible();
    expect(onFromDiscussion).toHaveBeenCalledOnce();
    await user.click(await screen.findByRole("button", { name: "Build" }));
    expect(build.mock.calls[0]?.[1].scope).toEqual({ mode: "selection", sourceIds: [], unitIds: [], sessionIds: ["ses_1"] });
  });

  it("filters the choices and selects all of a group", async () => {
    const user = userEvent.setup();
    setup({ sources: 3 });
    await user.click(await screen.findByRole("button", { name: "Choose…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Markers");
    await user.type(within(dialog).getByRole("textbox", { name: "Filter by title" }), "source");
    expect(within(dialog).queryByText("Markers")).not.toBeInTheDocument();
    await user.click(within(dialog).getAllByRole("button", { name: "Select all" })[0] as HTMLElement);
    expect(within(dialog).getByText("2 sources")).toBeVisible();
  });
});

describe("when there is nothing to do yet", () => {
  it("asks for a model, and opens Settings", async () => {
    const user = userEvent.setup();
    const opened = vi.fn();
    window.addEventListener(OPEN_SETTINGS_EVENT, opened);
    setup({ menu: { models: [], default: null, efforts: [] } });

    expect(await screen.findByText("Outputs need a model. Set one up under Settings → Models.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Build" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Open Settings/ }));
    expect(opened).toHaveBeenCalledTimes(1);
    expect((opened.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({ pane: "models" });
    window.removeEventListener(OPEN_SETTINGS_EVENT, opened);
  });

  it("offers to add sources to an empty library", async () => {
    const user = userEvent.setup();
    const { onAdd } = setup({ sources: 0, units: [] });
    expect(await screen.findByText("Nothing to build from yet")).toBeVisible();
    expect(screen.getByRole("button", { name: "Build" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Add sources/ }));
    expect(onAdd).toHaveBeenCalledTimes(1);
  });
});

describe("history and reading", () => {
  it("opens the latest version, and any earlier one from the history", async () => {
    const user = userEvent.setup();
    const first = version(1, { title: "First pass", content: "# First pass\n\n## Only\n\nOne [1].", sections: [{ index: 0, heading: "Only", checked: true, issues: [] }] });
    const second = version(2, { origin: "edit" });
    setup({ history: [second, first] });

    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v2" })).toBeVisible();
    expect(screen.getByText(/Report · Overview · Scientist · v2/)).toBeVisible();
    expect(screen.getByText(/Report · Overview · Scientist · Edited/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: /First pass/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "First pass" })).toBeVisible();
    expect(knowledgeApi.artifact).toHaveBeenCalledWith("biology", "art_1", "wsp_primary");
    // An older version is not built on: the button opens the latest.
    expect(screen.getByRole("button", { name: "Open latest version 2" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: /New/ }));
    expect(screen.getByRole("button", { name: "Build" })).toBeVisible();
    expect(screen.getByText("Ready when you are")).toBeVisible();
  });

  it("shows each section with what was found in it, and opens the evidence of a number", async () => {
    const user = userEvent.setup();
    setup({
      history: [version(1, {
        sections: [
          { index: 0, heading: "T cells", checked: true, issues: [] },
          { index: 1, heading: "B cells", checked: false, issues: [{ claim: "CD19 marks B cells", verdict: "unverified", note: "From the model's own knowledge; no source was found." }] },
        ],
      })],
    });

    // The page's own document: the print layout holds the same text, hidden.
    const page = within(await screen.findByRole("article"));
    expect(await page.findByText(/CD3D marks T cells/)).toBeVisible();
    expect(page.getAllByText("Not re-checked")).toHaveLength(1);
    expect(page.getByText("Not backed by a source")).toBeVisible();
    expect(page.getByText("CD19 marks B cells")).toBeVisible();
    // The brief is shown under the title, once.
    expect(page.getAllByText("For the lab")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Inspect citation 1" }));
    // The evidence panel opens (the print layout holds the same passage, hidden).
    expect(await screen.findByRole("link", { name: /Open example\.org/ })).toBeVisible();
  });

  it("reads a version from the old builder as it is, with no citations to inspect", async () => {
    setup({ history: [version(1, { origin: "legacy", style: "field_guide", citations: [], sections: [], scope: null, modelLabel: null, content: "# Legacy\n\n## 1. T-cell identity\n\nPinned text [1]." })] });
    expect(await within(await screen.findByRole("article")).findByText(/Pinned text \[1\]/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /Inspect citation/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Not re-checked")).not.toBeInTheDocument();
  });

  it("rebuilds from an outline that was edited here", async () => {
    const user = userEvent.setup();
    const build = vi.spyOn(knowledgeApi, "buildOutputStream").mockResolvedValue(version(2));
    setup({ history: [version(1)] });

    await user.click(await screen.findByRole("button", { name: /Outline/ }));
    expect(screen.getByText("How T cells are told apart")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit outline" }));
    const heading = screen.getByRole("textbox", { name: "Heading 2" });
    await user.clear(heading);
    await user.type(heading, "Plasma cells");
    await user.click(screen.getByRole("button", { name: "Move 2 up" }));
    await user.click(screen.getByRole("button", { name: "Rebuild with this outline" }));

    expect(build).toHaveBeenCalledTimes(1);
    expect(build.mock.calls[0]?.[1]).toEqual(expect.objectContaining({
      supersedesArtifactId: "art_1",
      outline: [{ heading: "Plasma cells", goal: "" }, { heading: "T cells", goal: "How T cells are told apart" }],
    }));
  });
});

describe("changing a version", () => {
  it("types over it, marks what was typed, and saves it as the next version", async () => {
    const user = userEvent.setup();
    const first = version(1);
    const typed = version(2, {
      origin: "edit", modelLabel: null, content: `${first.content} Extra.`,
      sections: [{ index: 0, heading: "T cells", checked: true, issues: [] }, { index: 1, heading: "B cells", checked: false, issues: [] }],
    });
    const edit = vi.spyOn(knowledgeApi, "editOutput").mockResolvedValue(typed);
    const { onNotify } = setup({ history: [first] });
    vi.mocked(knowledgeApi.artifacts).mockResolvedValue([typed, first].map(summary));

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const box = await screen.findByRole("textbox", { name: "Report, as Markdown" });
    expect(box).toHaveValue(first.content);
    expect(screen.getByText("No changes yet")).toBeVisible();
    expect(screen.getByRole("button", { name: /Save as new version/ })).toBeDisabled();

    // The last section is typed in; the preview says so, and the rest is as the Checker left it.
    await user.type(box, " Extra.");
    expect(screen.getByText("Unsaved changes · 1 part to re-check")).toBeVisible();
    expect(within(screen.getByLabelText("Preview")).getAllByText("Not re-checked")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: /Save as new version/ }));

    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v2" })).toBeVisible();
    expect(edit).toHaveBeenCalledWith("biology", "art_1", { clientRequestId: expect.stringMatching(/^[A-Za-z0-9_-]{8,128}$/), content: `${first.content} Extra.` }, "wsp_primary");
    expect(onNotify).toHaveBeenCalledWith("Saved as version 2. What you typed has not been re-checked yet.");
    expect(screen.getByText("Not re-checked")).toBeVisible();
    expect(screen.getByRole("button", { name: "Check again" })).toBeVisible();
    expect(screen.getByText(/typed by hand/)).toBeVisible();
  });

  it("keeps what was typed when the edit cannot be saved, and discards it on Cancel", async () => {
    const user = userEvent.setup();
    const edit = vi.spyOn(knowledgeApi, "editOutput").mockRejectedValue(new Error("That is not the latest version of this output. Open the latest one."));
    setup({ history: [version(1)] });

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const box = await screen.findByRole("textbox", { name: "Report, as Markdown" });
    await user.type(box, " More.");
    await user.click(screen.getByRole("button", { name: /Save as new version/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The edit wasn’t saved");
    expect(screen.getByRole("alert")).toHaveTextContent("That is not the latest version");
    expect(screen.getByRole("textbox", { name: "Report, as Markdown" })).toHaveValue(`${version(1).content} More.`);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("textbox", { name: "Report, as Markdown" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeVisible();
    expect(edit).toHaveBeenCalledTimes(1);
  });

  it("changes one section as asked, shows the others as kept, and opens the new version", async () => {
    const user = userEvent.setup();
    const first = version(1);
    const second = version(2, { origin: "revise" });
    const finish = held<Artifact>();
    const revise = vi.spyOn(knowledgeApi, "reviseOutputStream").mockImplementation(async (_base, _id, _request, _workspace, onEvent) => {
      act(() => {
        onEvent({ type: "section", index: 1, state: "writing" });
        onEvent({ type: "text", section: 1, text: "## B cells\n\nShorter [1]." });
      });
      return finish.promise;
    });
    const { onNotify } = setup({ history: [first] });
    vi.mocked(knowledgeApi.artifacts).mockResolvedValue([second, first].map(summary));

    await user.click(await screen.findByRole("button", { name: /Ask Gunther to change/ }));
    await user.selectOptions(screen.getByRole("combobox", { name: /Which part/ }), "1");
    await user.type(screen.getByRole("textbox", { name: /What should change/ }), "Make it shorter");
    await user.click(screen.getByRole("button", { name: "Change it" }));

    expect(await screen.findByText(/Shorter\./)).toBeVisible();
    expect(screen.getByText("Kept as it is")).toBeVisible();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    await act(async () => finish.release(second));

    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v2" })).toBeVisible();
    expect(onNotify).toHaveBeenCalledWith("Changed as asked: version 2 saved.");
    expect(revise).toHaveBeenCalledWith("biology", "art_1", { clientRequestId: expect.stringMatching(/^[A-Za-z0-9_-]{8,128}$/), instruction: "Make it shorter", sectionIndex: 1 }, "wsp_primary", expect.any(Function), expect.any(AbortSignal));
  });

  it("shows a section the revision leaves alone as kept, not as the word the writer answered with", async () => {
    const user = userEvent.setup();
    const finish = held<Artifact>();
    vi.spyOn(knowledgeApi, "reviseOutputStream").mockImplementation(async (_base, _id, _request, _workspace, onEvent) => {
      act(() => {
        onEvent({ type: "section", index: 0, state: "writing" });
        onEvent({ type: "text", section: 0, text: "UNCHANGED" });
        onEvent({ type: "section", index: 0, state: "done" });
        onEvent({ type: "section", index: 1, state: "writing" });
        onEvent({ type: "text", section: 1, text: "## B cells\n\nRewritten [1]." });
      });
      return finish.promise;
    });
    setup({ history: [version(1)] });

    await user.click(await screen.findByRole("button", { name: /Ask Gunther to change/ }));
    await user.type(screen.getByRole("textbox", { name: /What should change/ }), "Keep to B cells");
    await user.click(screen.getByRole("button", { name: "Change it" }));

    expect(await screen.findByText(/Rewritten\./)).toBeVisible();
    expect(screen.getByText("Kept as it is")).toBeVisible();
    expect(screen.queryByText("UNCHANGED")).not.toBeInTheDocument();
    await act(async () => finish.release(version(2, { origin: "revise" })));
  });

  it("changes the whole output when no part is chosen, and retries a failed change", async () => {
    const user = userEvent.setup();
    const revise = vi.spyOn(knowledgeApi, "reviseOutputStream")
      .mockRejectedValueOnce(new Error("DeepSeek did not accept the API key."))
      .mockResolvedValueOnce(version(2, { origin: "revise" }));
    setup({ history: [version(1)] });
    vi.mocked(knowledgeApi.artifacts).mockResolvedValue([version(2), version(1)].map(summary));

    await user.click(await screen.findByRole("button", { name: /Ask Gunther to change/ }));
    expect(screen.getByRole("button", { name: "Change it" })).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: /What should change/ }), "Use plainer words");
    await user.click(screen.getByRole("button", { name: "Change it" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The change wasn’t saved");
    expect(revise.mock.calls[0]?.[2]).toEqual({ clientRequestId: expect.any(String), instruction: "Use plainer words" });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v2" })).toBeVisible();
    expect(revise.mock.calls[1]?.[2]).toBe(revise.mock.calls[0]?.[2]);
  });

  it("checks again what was not checked, and the flags go", async () => {
    const user = userEvent.setup();
    const flagged = version(1, { sections: [{ index: 0, heading: "T cells", checked: true, issues: [] }, { index: 1, heading: "B cells", checked: false, issues: [] }] });
    const checked = version(1);
    const check = vi.spyOn(knowledgeApi, "checkOutput").mockResolvedValue(checked);
    const { onNotify } = setup({ history: [flagged] });

    expect(await screen.findByText("Not re-checked")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Check again" }));

    await waitFor(() => expect(screen.queryByText("Not re-checked")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
    expect(check).toHaveBeenCalledWith("biology", "art_1", "wsp_primary");
    expect(onNotify).toHaveBeenCalledWith("Checked against the sources.");
  });

  it("only changes the latest version: an older one opens the latest instead", async () => {
    const user = userEvent.setup();
    setup({ history: [version(2), version(1)] });

    await user.click(await screen.findByRole("button", { name: /Immune markers v1/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v1" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ask Gunther to change/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open latest" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Immune markers v2" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Edit" })).toBeVisible();
  });

  it("leaves a version from the old builder as it is", async () => {
    setup({ history: [version(1, { origin: "legacy", citations: [], sections: [], scope: null, modelLabel: null, content: "# Legacy\n\n## 1. Unit\n\nPinned text." })] });
    expect(await screen.findByText("This version came from the old builder. Rebuild it to edit it.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ask Gunther to change/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
  });
});

describe("exporting to PDF", () => {
  const readCss = () => readFileSync(resolve(process.cwd(), "src/outputs/outputs.css"), "utf8");

  it("lays a report out for A4 with its sources, hidden on screen", async () => {
    setup({ history: [version(1)] });
    await screen.findByRole("heading", { level: 2, name: "Immune markers v1" });

    const paper = document.getElementById("print-root") as HTMLElement;
    expect(paper.parentElement).toBe(document.body);
    expect(paper).toHaveAttribute("aria-hidden", "true");
    expect(paper.querySelector("style")?.textContent).toContain("size: A4");
    expect(within(paper).getByRole("heading", { level: 1, hidden: true, name: "Immune markers v1" })).toBeInTheDocument();
    expect(paper.textContent).toContain("CD3D marks T cells");
    // A number is a superscript, and what it stands for is listed at the end.
    expect(paper.querySelector("sup")?.textContent).toBe("[1]");
    const sources = paper.querySelector(".outputs-print-sources") as HTMLElement;
    expect(sources).toHaveTextContent("Marker review");
    expect(sources).toHaveTextContent("CD3D is a marker of T cells.");
    expect(paper.querySelector("button")).toBeNull();
  });

  it("lays a deck out one slide to a page, with no notes and a page of sources", async () => {
    setup({ history: [version(1, { kind: "slides", format: "slides", style: null, content: "# Deck\n\nsub\n\n---\n\n## One\n\n- a [1]\n\nNote: Say a." })] });
    await screen.findByText("Slide 1 of 2");

    const paper = document.getElementById("print-root") as HTMLElement;
    expect(paper.querySelector("style")?.textContent).toContain("size: 13.333in 7.5in");
    expect(paper.querySelectorAll(".outputs-print-slide")).toHaveLength(3);
    expect(paper.textContent).not.toContain("Say a.");
    expect(paper.querySelector(".outputs-print-slide-sources")).toHaveTextContent("Marker review");
  });

  it("prints from the page in a browser", async () => {
    const user = userEvent.setup();
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    const { onNotify } = setup({ history: [version(1)] });

    await user.click(await screen.findByRole("button", { name: "Export PDF" }));
    await waitFor(() => expect(print).toHaveBeenCalledTimes(1));
    expect(onNotify).toHaveBeenCalledWith("In the print window, choose PDF to save it.");
  });

  it("asks the app's shell to print on a Mac, where the web view cannot", async () => {
    const user = userEvent.setup();
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    vi.mocked(invoke).mockResolvedValue(undefined);
    (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15");
    try {
      setup({ history: [version(1)] });
      await user.click(await screen.findByRole("button", { name: "Export PDF" }));
      await waitFor(() => expect(invoke).toHaveBeenCalledWith("print_output"));
      expect(print).not.toHaveBeenCalled();

      vi.mocked(invoke).mockRejectedValueOnce("Printing is not available.");
      await user.click(screen.getByRole("button", { name: "Export PDF" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("It couldn’t be printed");
    } finally {
      delete (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"];
    }
  });

  it("keeps the rule that prints only the paper layout", () => {
    const css = readCss();
    expect(css).toMatch(/@media screen\s*\{\s*#print-root\s*\{\s*display:\s*none;/);
    expect(css).toMatch(/@media print[\s\S]*body > :not\(#print-root\)\s*\{\s*display:\s*none/);
    expect(css).toMatch(/\.outputs-print-slide\s*\{[^}]*width:\s*1280px;[^}]*height:\s*720px;[^}]*break-after:\s*page/);
  });
});
