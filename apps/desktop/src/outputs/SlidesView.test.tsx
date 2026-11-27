import type { Artifact, ArtifactConversationDetail, ConversationCitation, KeyResultJob, OutputType } from "@gunther/contracts";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { libraryScope } from "./artifactConversations";
import { EMPTY_LIVE } from "./liveOutput";
import { OpenOutput, type OpenOutputProps } from "./OpenOutput";
import { SlideReviewPanel } from "./SlideReviewPanel";
import { SlidesView } from "./SlidesView";
import { fixture } from "./deck/fixtures";
import outputStyles from "./outputs.css?raw";

const setFullscreen = vi.fn(async () => undefined);
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => ({ setFullscreen }) }));

const citation: ConversationCitation = {
  id: "cit_1", kind: "web", url: "https://www.example.org/markers", sourceId: "", sourceTitle: "Marker review", assertionId: null,
  quote: "CD3D is a marker of T cells.", locator: "example.org", status: "provisional", confidence: 0, ref: 1,
};

const CONTENT = [
  "# Immune markers\n\nHow cells are told apart",
  "## T cells\n\n- CD3D marks T cells [1]\n- It sits on the cell surface [?]\n\nNote: Say that CD3D is on every T cell [1].",
  "## Takeaways\n\n- Markers tell cells apart\n\nNote: Close on CD3D.",
].join("\n\n---\n\n");

const deck = (overrides: Partial<Artifact> = {}) => ({
  id: "art_1", kind: "slides", content: CONTENT, citations: [citation],
  sections: [
    { index: 0, heading: "Immune markers", checked: true, issues: [] },
    { index: 1, heading: "T cells", checked: false, issues: [] },
    { index: 2, heading: "Takeaways", checked: true, issues: [] },
  ],
  ...overrides,
}) as unknown as Artifact;

/** Reveal starts up asynchronously; let it finish before looking. */
async function show(artifact: Artifact, onCite = vi.fn(), more: Partial<ComponentProps<typeof SlidesView>> = {}) {
  const view = render(<SlidesView artifact={artifact} onCite={onCite} {...more} />);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
  return { ...view, onCite };
}

const savedAt = "2026-11-27T08:00:00Z";

const keyResultJob = (state: KeyResultJob["state"], changes: Partial<KeyResultJob> = {}): KeyResultJob => ({
  id: "results", originKind: "output", originId: "art_1", state, error: null, keptCount: 0, keptIds: [], ...changes,
});

function openSavedDeck(overrides: Partial<Artifact> = {}) {
  const scope = libraryScope();
  const preferences = { emphasize: [], avoid: [] };
  const slides: OutputType = {
    id: "slides", shape: "slides", label: "Slides", icon: "Presentation", description: "Present the findings.", options: [],
    capabilities: { unitLabel: "slide", outline: true, design: true, exports: [] },
  };
  const artifact = deck({
    title: "Saved deck", outline: [], lineageId: "work", conversationId: "conversation", origin: "build", style: "slides", outputType: "Slides", scope,
    provenance: {
      schemaVersion: 2, generator: "studio", workspaceId: "workspace", knowledgeBaseId: "library",
      knowledgeBaseQuestion: "", acceptedOnly: false, acceptedUnitIds: [], revisionIds: [],
      creationPlan: {
        planId: "plan", revision: "revision", approvedAt: savedAt, approvalMessageId: "approval", materialKey: "material", preferences,
        options: { type: "slides", scope, focus: "", title: null, audience: "student", length: null, language: null, use: null,
          theme: null, designVersion: null, voices: null, web: false, model: null, effort: null },
        proposal: { title: "Saved deck", summary: "The saved approach.", angle: "", coverage: [], count: { mode: "exact", value: 3 }, targetMinutes: null },
        outline: null,
      },
    },
    ...overrides,
  });
  const detail: ArtifactConversationDetail = {
    id: artifact.conversationId!, title: artifact.title, scope, shape: "slides", outputType: "Slides", lineageId: artifact.lineageId,
    artifactCount: 1, waiting: false, lastTurn: null, messages: [], active: artifact.lineageId, pending: null, archived: false,
    createdAt: savedAt, updatedAt: savedAt, brief: { goal: null, audience: null, constraints: [], open: [], ...preferences },
    works: [{ key: artifact.lineageId, kind: "slides", title: artifact.title, versionId: artifact.id, parts: [],
      spec: { kind: "slides", outputType: null, use: null, length: null }, approach: { state: "ok", why: "" }, outline: { state: "ok", why: "" } }],
  };
  const studio: OpenOutputProps["studio"] = {
    types: [slides], artifacts: [artifact], artifactsLoaded: true, conversations: [detail], details: { [detail.id]: detail },
    works: [], activities: [], jobs: new Map(), failures: {}, error: "",
    planning: { locals: {}, targetFor: vi.fn(), setLocal: vi.fn(), discardLocal: vi.fn(), patch: vi.fn(), turn: vi.fn() },
    read: vi.fn().mockResolvedValue(detail), follow: vi.fn(), reload: vi.fn(), ensure: vi.fn(), send: vi.fn(), direct: vi.fn(),
    remember: vi.fn(), rememberVersion: vi.fn(), forgetTrashed: vi.fn(), savedForPlan: vi.fn(), owner: vi.fn(), stop: vi.fn(), turnStop: vi.fn(),
  };
  const props: OpenOutputProps = {
    baseId: "library", workspaceId: "workspace", target: { conversationId: detail.id, lineageId: artifact.lineageId },
    studio, scope, sourceState: () => "ready", modelMenu: null, webAvailable: false, dirty: { current: false }, editFlush: { current: null },
    onNotify: vi.fn(), onCite: vi.fn(), onOpen: vi.fn(), onWideReader: vi.fn(), onAskChat: vi.fn(), onTrash: vi.fn(),
    onArtifactChange: vi.fn(), onPartChange: vi.fn(),
  };
  const reader = (changes: Partial<typeof studio> = {}) => <div className="library-middle-page">
    <OpenOutput {...props} studio={{ ...studio, ...changes }} />
  </div>;
  const readArtifact = vi.spyOn(knowledgeApi, "artifact").mockResolvedValue(artifact);
  const view = render(reader());
  return { artifact, readArtifact, conversationId: detail.id, updateStudio: (changes: Partial<typeof studio>) => view.rerender(reader(changes)) };
}

afterEach(async () => {
  cleanup();
  // Reveal's window-resize text fitting debounces for 100 ms. Let its deferred
  // destroy and trailing animation frame finish before jsdom closes the window.
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 120)); });
  vi.unstubAllGlobals();
  delete (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"];
  setFullscreen.mockClear();
});

describe("the slide viewer", () => {
  it("shows every slide in the deck and as a thumbnail, with the notes of the one in view", async () => {
    const user = userEvent.setup();
    await show(deck());

    expect(document.querySelectorAll(".slides > section")).toHaveLength(3);
    const strip = screen.getByRole("list", { name: "Slides" });
    const track = strip.closest(".reader-track");
    expect(track).toHaveAttribute("data-track", "deck");
    expect(track?.querySelector(".outputs-stage")).toBeInTheDocument();
    expect(track).toContainElement(screen.getByRole("region", { name: "Speaker notes" }));
    expect(within(strip).getAllByRole("button")).toHaveLength(3);
    expect(screen.getByText("Slide 1 of 3")).toBeVisible();
    // The title slide has no notes; the second does.
    expect(screen.getByText("No notes for this slide.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));
    expect(screen.getByText("Slide 2 of 3")).toBeVisible();
    expect(screen.getByRole("region", { name: "Speaker notes" })).toHaveTextContent("Say that CD3D is on every T cell [1].");
    expect(screen.getByRole("button", { name: "Go to slide 2" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: "Not re-checked" })).toBeVisible();
    expect(within(screen.getByRole("region", { name: "Slide source check" })).getByRole("status")).toHaveTextContent("Not re-checked");
    await user.click(screen.getByRole("button", { name: "Go to slide 3" }));
    expect(screen.queryByText("Not re-checked")).not.toBeInTheDocument();
  });

  it("lets both rows of slide controls wrap within the deck track", async () => {
    const { container } = await show(deck(), vi.fn(), { onAsk: vi.fn(), onAccent: vi.fn(), onTheme: vi.fn() });
    const tools = screen.getByRole("button", { name: "Present" }).closest(".outputs-slides-tools");
    const bar = tools?.closest(".outputs-slides-bar");
    expect(bar).toContainElement(screen.getByRole("button", { name: "Refine slide" }));
    expect(bar?.closest(".reader-track")).toBe(container.querySelector('[data-track="deck"]'));
    // jsdom has no layout engine. Verify the wrap declaration used by both rows.
    expect(outputStyles).toMatch(/\.outputs-slides-bar,\s*\.outputs-slides-tools,\s*\.outputs-slides-position\s*\{[^}]*flex-wrap:\s*wrap/);
  });

  it("links to the current slide's check and folds only the optional notes", async () => {
    const user = userEvent.setup();
    const complete = deck();
    const note = "The cited passage gives a range without an exact value.";
    const finding = "The rate was exactly 18 percent.";
    const contradicted = "The rate doubled.";
    await show(deck({ sections: [complete.sections[0]!, {
      index: 1, heading: "T cells", checked: false,
      coverage: { status: "partial", considered: 3, checked: 2, unchecked: 1 },
      issues: [{ claim: finding, verdict: "unsupported", note }, { claim: contradicted, verdict: "contradicted", note: "" }],
    }, {
      index: 2, heading: "Takeaways", checked: false,
      issues: [{ claim: "All readings match.", verdict: "unchecked", note: "" }, { claim: "Every sample was included.", verdict: "unsourced", note: "" }],
      coverage: { status: "stale", considered: 2, checked: 2, unchecked: 0 },
    }] }));
    const toolbar = within(screen.getByText("Slide 1 of 3").closest<HTMLElement>(".outputs-slides-bar")!);
    expect(toolbar.getAllByRole("button")).toEqual([toolbar.getByRole("button", { name: "Present" })]);
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));

    const pill = toolbar.getByRole("button", { name: "1 contradicted · 1 unsupported · Partially checked · 1 unchecked" });
    const check = screen.getByRole("region", { name: "Slide source check" });
    expect(pill).toHaveClass("is-severe");
    expect(pill).toHaveAttribute("aria-controls", check.id);
    expect(within(check).getByRole("status")).toHaveTextContent("Partially checked · 1 unchecked");
    const [unsupported, disagreement] = within(check).getAllByRole("listitem");
    expect(within(unsupported!).getByText("Unsupported")).toBeVisible();
    expect(within(unsupported!).getByText(finding, { selector: "q" })).toBeVisible();
    expect(within(disagreement!).getByText("Contradicted")).toBeVisible();
    expect(within(disagreement!).getByText(contradicted, { selector: "q" })).toBeVisible();
    const details = within(check).getByText("Check details", { selector: "summary" });
    expect(within(check).getByText(note)).not.toBeVisible();
    await user.click(pill);
    expect(check).toHaveFocus();
    await user.click(details);
    expect(within(check).getByText(note)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Go to slide 3" }));
    const stale = toolbar.getByRole("button", { name: "1 unsourced · 1 unchecked · Source check is out of date" });
    expect(stale).toBeVisible();
    expect(stale).not.toHaveClass("is-severe");
    expect(within(check).getByRole("status")).toHaveTextContent("Source check is out of date");
    expect(within(check).queryByText(finding)).toBeNull();
  });

  it("places idle deck disclosures after the notes and keeps running or failing reviews above the deck", async () => {
    const { artifact, conversationId, readArtifact, updateStudio } = openSavedDeck({
      keyResults: keyResultJob("done", { keptCount: 1, keptIds: ["result"] }),
    });
    const notes = await screen.findByRole("region", { name: "Speaker notes" });
    const review = screen.getByRole("button", { name: "Review rendered slides" });
    for (const item of [screen.getByText("Approved plan"), screen.getByText("Preferences for future changes"), screen.getByText(/1 key result kept/), review]) {
      expect(notes.compareDocumentPosition(item) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }

    const stage = "Reviewing rendered slides";
    const jobs: OpenOutputProps["studio"]["jobs"] = new Map([[conversationId, {
      kind: "version", artifactId: artifact.id, startedAt: savedAt, stopping: false, stop: vi.fn(),
      live: { ...EMPTY_LIVE, running: [{ kind: "stage", id: "visual_review", label: stage }] },
    }]]);
    updateStudio({ jobs });
    expect(review).toBeDisabled();
    expect(notes.compareDocumentPosition(review) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(notes.compareDocumentPosition(screen.getByText(stage)) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();

    readArtifact.mockResolvedValue({ ...artifact, provenance: { ...artifact.provenance, skill: {
      name: "slides", version: 1, skipped: [], visualReview: {
        version: 1, candidateHash: "reviewed", status: "needs_review", imageReview: "reviewed", calls: 1, repairCycles: 0,
        repairedSlides: [], history: [], findings: [{ slideId: "slide-1", blockId: null, severity: "error", route: "layout",
          problem: "The legend overlaps a label.", correction: "Move the legend below the chart." }],
      },
    } } });
    updateStudio({ failures: { [conversationId]: { message: "A new review could not finish." } } });
    const warning = await screen.findByRole("button", { name: "Slides need visual review" });
    expect(notes.compareDocumentPosition(warning) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(notes.compareDocumentPosition(screen.getByRole("alert")) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    fireEvent.click(warning);
    expect(screen.getByText(/The legend overlaps a label/)).toBeVisible();
  });

  it.each([
    { state: "queued", label: "Checking key results…", action: null },
    { state: "running", label: "Checking key results…", action: null },
    { state: "failed", label: "Key results could not be checked.", action: "Retry" },
    { state: "needs_model", label: "Key results need an Analysis model", action: "Open Models settings" },
  ] as const)("keeps $state key results above the deck", async ({ state, label, action }) => {
    const job = keyResultJob(state);
    vi.spyOn(knowledgeApi, "keyResultOrigin").mockResolvedValue(job);
    openSavedDeck({ keyResults: job });
    const slides = (await screen.findByRole("region", { name: "Speaker notes" })).closest(".outputs-slides")!;
    const status = screen.getByText(label).closest<HTMLElement>(".key-results-status")!;
    expect(status.closest(".reader-track")).toHaveAttribute("data-track", "deck");
    expect(status.compareDocumentPosition(slides) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    if (action) expect(within(status).getByRole("button", { name: action })).toBeEnabled();
  });

  it("moves the completed key-result count below the deck after one poll", async () => {
    const running = keyResultJob("running");
    const retry = vi.spyOn(knowledgeApi, "retryKeyResults").mockResolvedValue(running);
    const poll = vi.spyOn(knowledgeApi, "keyResultOrigin")
      .mockResolvedValue(keyResultJob("done", { keptCount: 2, keptIds: ["one", "two"] }));
    openSavedDeck({ keyResults: keyResultJob("failed") });
    const notes = await screen.findByRole("region", { name: "Speaker notes" });
    vi.useFakeTimers();
    try {
      await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Retry" })); });
      expect(retry).toHaveBeenCalledExactlyOnceWith("library", running.id);
      expect(screen.getByText("Checking key results…").compareDocumentPosition(notes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(poll).not.toHaveBeenCalled();

      await act(async () => vi.advanceTimersByTimeAsync(2_500));
      const count = screen.getByText("✓ 2 key results kept");
      expect(notes.compareDocumentPosition(count) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      await act(async () => vi.advanceTimersByTimeAsync(10_000));
      expect(count).toBeVisible();
      expect(poll).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("updates the stage allowance for a wrapped title and clears it in edit mode", async () => {
    const watched = new Map<Element, () => void>();
    vi.stubGlobal("ResizeObserver", class {
      constructor(private notify: () => void) {}
      observe(element: Element) { watched.set(element, this.notify); }
      unobserve(element: Element) { watched.delete(element); }
      disconnect() {
        for (const [element, notify] of watched) if (notify === this.notify) watched.delete(element);
      }
    });
    vi.stubGlobal("innerHeight", 1000);
    openSavedDeck();
    const thumbs = await screen.findByRole("list", { name: "Slides" });
    const reader = screen.getByRole("article", { name: "Open output" });
    const page = reader.closest<HTMLElement>(".library-middle-page")!;
    const heading = reader.querySelector(".output-reader-heading")!;
    const stage = reader.querySelector<HTMLElement>(".outputs-stage")!;
    const toolbar = reader.querySelector(".outputs-slides-bar")!;
    await waitFor(() => expect(watched.has(heading)).toBe(true));
    for (const box of [page, toolbar, thumbs]) expect(watched.has(box)).toBe(true);
    expect(reader.style.getPropertyValue("--stage-overhead")).toBe("");

    let titleHeight = 36;
    const stageTop = () => 44 + 116 + titleHeight - page.scrollTop;
    vi.spyOn(page, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 44, 1800, 956));
    vi.spyOn(page, "clientHeight", "get").mockReturnValue(956);
    vi.spyOn(stage, "getBoundingClientRect").mockImplementation(() => new DOMRect(0, stageTop(), 1100, 619));
    vi.spyOn(thumbs, "getBoundingClientRect").mockImplementation(() => new DOMRect(0, stageTop() + 619 + 14, 1100, 140));
    vi.spyOn(thumbs, "offsetHeight", "get").mockReturnValue(140);
    const measure = () => act(async () => {
      watched.get(heading)!();
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    });
    await measure();
    expect(reader.style.getPropertyValue("--stage-overhead")).toBe("362px");

    titleHeight += 70;
    page.scrollTop = 56;
    await measure();
    expect(reader.style.getPropertyValue("--stage-overhead")).toBe("432px");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Output actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));
    expect(reader).toHaveAttribute("data-mode", "edit");
    expect(reader.style.getPropertyValue("--stage-overhead")).toBe("");
    expect(watched.has(heading)).toBe(false);
  });

  it("fills the local preview width when compact", async () => {
    const { container } = await show(deck(), vi.fn(), { compact: true });
    expect(container.querySelector(".outputs-stage")?.closest(".reader-track")).toHaveAttribute("data-track", "full");
  });

  it("places full-size slide renders in a focusable review scroller", () => {
    const artifact = deck({
      provenance: {
        schemaVersion: 2, generator: "studio", workspaceId: "workspace", knowledgeBaseId: "library",
        knowledgeBaseQuestion: "", acceptedOnly: false, acceptedUnitIds: [], revisionIds: [],
        skill: {
          name: "slides", version: 1, skipped: [],
          visualReview: {
            version: 1, candidateHash: "reviewed", status: "clean", imageReview: "reviewed",
            calls: 1, repairCycles: 0, repairedSlides: [], findings: [], history: [],
          },
          renderCheck: {
            version: 1, candidateHash: "reviewed", status: "captured", id: "review",
            slides: [{ id: "slide-1", index: 0, image: "slide-01.png", fit: 1, overflow: false, resolvedFonts: [] }],
          },
        },
      },
    });
    render(<SlideReviewPanel artifact={artifact} baseId="library" workspaceId="workspace" disabled={false} onFollowUp={vi.fn()} />);
    expect(screen.getByRole("region", { name: "Rendered-slide review" })).toHaveAttribute("data-track", "review");
    fireEvent.click(screen.getByRole("button", { name: "Rendered slides reviewed" }));
    fireEvent.click(screen.getByRole("button", { name: "Slide 1" }));
    const scroll = screen.getByRole("region", { name: "Full-size inspected render" });
    expect(scroll).toHaveAttribute("tabindex", "0");
    expect(within(scroll).getByRole("img")).toHaveStyle({ width: "1280px" });
  });

  it("keeps citation numbering in thumbnails without adding citation buttons", async () => {
    await show(deck());
    const thumbnail = screen.getByRole("button", { name: "Go to slide 2" }).querySelector(".outputs-thumb-frame");
    expect(thumbnail?.querySelector("sup")?.textContent).toBe("[1]");
    expect(thumbnail?.querySelector('button[aria-label="Inspect citation 1"]')).toBeNull();
  });

  it("opens the evidence of a number on a slide", async () => {
    const user = userEvent.setup();
    const { onCite } = await show(deck());
    // Reveal hides the slides that are not in view, so go to the one with the number.
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));
    await user.click(await screen.findByRole("button", { name: "Inspect citation 1" }));
    expect(onCite).toHaveBeenCalledWith(citation, 0);
  });

  it("starts again at the first slide for another version", async () => {
    const user = userEvent.setup();
    const { rerender } = await show(deck());
    await user.click(screen.getByRole("button", { name: "Go to slide 3" }));
    expect(screen.getByText("Slide 3 of 3")).toBeVisible();
    rerender(<SlidesView artifact={deck({ id: "art_2" })} onCite={vi.fn()} />);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
    expect(screen.getByText("Slide 1 of 3")).toBeVisible();
  });

  it("presents in a layer on the document body, from the slide in view, and Esc leaves it", async () => {
    const user = userEvent.setup();
    // Present fills the window the page is in; it never takes the whole screen.
    const request = vi.fn(async () => undefined);
    Object.defineProperty(document.documentElement, "requestFullscreen", { configurable: true, value: request });
    await show(deck());
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));

    await user.click(screen.getByRole("button", { name: "Present" }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
    const layer = screen.getByRole("dialog", { name: "Presenting" });
    // Not inside the page: the page's entrance animation leaves a transform on it, and a fixed layer
    // inside a transformed element fills that element, not the window.
    expect(layer.parentElement).toBe(document.body);
    expect(layer).toHaveClass("is-presenting");
    // It starts where the page was, with its own deck.
    expect(within(layer).getByRole("heading", { name: "T cells" })).toBeVisible();
    expect(within(layer).queryByRole("heading", { name: "Takeaways" })).not.toBeInTheDocument();
    // A number is not a button while presenting.
    expect(within(layer).queryByRole("button", { name: "Inspect citation 1" })).not.toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Presenting" })).not.toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
  });

  it("leaves on Esc although Reveal cancels that key for its own overview", async () => {
    const user = userEvent.setup();
    await show(deck());
    // What Reveal does with Esc: it takes it for its overview and cancels the event.
    const reveal = vi.fn((event: KeyboardEvent) => { if (event.key === "Escape") event.preventDefault(); });
    document.addEventListener("keydown", reveal);
    try {
      await user.click(screen.getByRole("button", { name: "Present" }));
      await act(async () => undefined);
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog", { name: "Presenting" })).not.toBeInTheDocument();
      expect(reveal).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener("keydown", reveal);
    }
  });

  it("keeps the page at the slide the presentation ended on", async () => {
    const user = userEvent.setup();
    await show(deck());

    await user.click(screen.getByRole("button", { name: "Present" }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
    fireEvent.keyDown(document.body, { key: "ArrowRight", keyCode: 39 });
    await user.click(screen.getByRole("button", { name: "Leave Present" }));
    await act(async () => undefined);
    expect(screen.getByText("Slide 2 of 3")).toBeVisible();
  });

  it("presents inside the app's window without making the window full screen", async () => {
    const user = userEvent.setup();
    (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
    await show(deck());

    await user.click(screen.getByRole("button", { name: "Present" }));
    await act(async () => undefined);
    expect(screen.getByRole("dialog", { name: "Presenting" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Leave Present" }));
    await act(async () => undefined);
    expect(screen.queryByRole("dialog", { name: "Presenting" })).not.toBeInTheDocument();
    expect(setFullscreen).not.toHaveBeenCalled();
  });

  it("keeps each slide's marker out of sight and hands its layout to the face", async () => {
    const content = [
      "<!-- layout: title; accent: blue -->\n# Immune markers\n\nHow cells are told apart",
      "<!-- layout: compare -->\n## T cells\n\n| | T |\n| --- | --- |\n| Marker | CD3D [1] |\n\nNote: Say that CD3D is on every T cell [1].",
      "<!-- layout: takeaways -->\n## Takeaways\n\n- Markers tell cells apart\n\nNote: Close on CD3D.",
    ].join("\n\n---\n\n");
    await show(deck({ content }));

    const faces = (root: ParentNode) => Array.from(root.querySelectorAll(".outputs-slide-face")).map((face) => face.getAttribute("data-layout"));
    expect(faces(document.querySelector(".slides")!)).toEqual(["title", "compare", "takeaways"]);
    expect(faces(screen.getByRole("list", { name: "Slides" }))).toEqual(["title", "compare", "takeaways"]);
    // The marker is not shown anywhere, not even as text, and the slide still has its heading.
    expect(document.body.textContent).not.toContain("layout:");
    expect(document.body.innerHTML).not.toContain("<!--");
    expect(screen.getAllByRole("heading", { name: "Immune markers" }).length).toBeGreaterThan(0);
    // The notes are what follows "Note:", not the marker.
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));
    expect(screen.getByRole("region", { name: "Speaker notes" })).toHaveTextContent("Say that CD3D is on every T cell [1].");
  });

  it("gives a deck made before markers the layouts of its outline, but only when they line up", async () => {
    const outline = [
      { heading: "Immune markers", goal: "", layout: "title" },
      { heading: "T cells", goal: "", layout: "compare" },
      { heading: "Takeaways", goal: "", layout: "takeaways" },
    ];
    const faces = () => Array.from(document.querySelectorAll(".slides .outputs-slide-face")).map((face) => face.getAttribute("data-layout"));
    const { unmount } = await show(deck({ outline } as Partial<Artifact>));
    expect(faces()).toEqual(["title", "compare", "takeaways"]);
    unmount();

    // An outline with a part too few says nothing about which slide is which.
    await show(deck({ outline: outline.slice(0, 2) } as Partial<Artifact>));
    expect(faces()).toEqual([null, null, null]);
  });

  it("draws a slide that takes its layout's form as that layout, in the deck and in the strip", async () => {
    const content = [
      "<!-- layout: title -->\n# Immune markers\n\nHow cells are told apart",
      "<!-- layout: bar_chart -->\n## T cells have the marker\n| Cell | Share (%) |\n| --- | --- |\n| **T** | 62 [1] |\n| B | 18 [1] |",
      "<!-- layout: flow -->\n## One step\n\n- not a numbered list",
    ].join("\n\n---\n\n");
    await show(deck({ content }));
    const drawn = (root: ParentNode) => Array.from(root.querySelectorAll(".outputs-slide-face")).map((face) => face.getAttribute("data-drawn"));
    // The third slide does not have the form of its layout, so it is its text, as before layouts.
    expect(drawn(document.querySelector(".slides")!)).toEqual(["title", "bar_chart", null]);
    expect(drawn(screen.getByRole("list", { name: "Slides" }))).toEqual(["title", "bar_chart", null]);
  });

  it("says so when a deck has no slides", async () => {
    await show(deck({ content: "" }));
    expect(screen.getByText("This deck has no slides yet.")).toBeVisible();
  });
});

describe("the accent of a deck", () => {
  const colourOf = (root: ParentNode) => Array.from(root.querySelectorAll<HTMLElement>(".outputs-slide-face")).map((face) => face.style.getPropertyValue("--outputs-accent"));
  const marked = (accent: string) => [
    `<!-- layout: title${accent ? `; accent: ${accent}` : ""} -->\n# Immune markers\n\nHow cells are told apart`,
    "<!-- layout: takeaways -->\n## Takeaways\n\n- Markers tell cells apart [1]",
  ].join("\n\n---\n\n");

  it("is the title slide's, on every slide of the deck, the strip and Present", async () => {
    const user = userEvent.setup();
    await show(deck({ content: marked("teal") }));
    expect(colourOf(document.querySelector(".slides")!)).toEqual(["#028066", "#028066"]);
    expect(colourOf(screen.getByRole("list", { name: "Slides" }))).toEqual(["#028066", "#028066"]);
    await user.click(screen.getByRole("button", { name: "Present" }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
    expect(colourOf(screen.getByRole("dialog", { name: "Presenting" }))).toEqual(["#028066", "#028066"]);
  });

  it("is ink when the title slide names none", async () => {
    await show(deck({ content: marked("") }));
    expect(colourOf(document.querySelector(".slides")!)).toEqual(["#1a1a18", "#1a1a18"]);
  });

  it("is chosen in a menu of the toolbar, which says which one the deck has", async () => {
    const user = userEvent.setup();
    const onAccent = vi.fn();
    await show(deck({ content: marked("teal") }), vi.fn(), { onAccent });
    await user.click(screen.getByRole("button", { name: "Accent colour" }));
    const menu = screen.getByRole("menu", { name: "Accent colour" });
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((item) => item.textContent)).toEqual(["Ink (no colour)", "Blue", "Teal", "Red", "Amber", "Violet"]);
    expect(items.map((item) => item.querySelector("svg")?.classList.contains("lucide-check"))).toEqual([false, false, true, false, false, false]);
    await user.click(within(menu).getByRole("menuitem", { name: "Red" }));
    expect(onAccent).toHaveBeenCalledExactlyOnceWith("red");
    expect(screen.queryByRole("menu", { name: "Accent colour" })).not.toBeInTheDocument();
  });

  it("is ink in the menu for a deck that names none", async () => {
    const user = userEvent.setup();
    await show(deck({ content: marked("") }), vi.fn(), { onAccent: vi.fn() });
    await user.click(screen.getByRole("button", { name: "Accent colour" }));
    expect(screen.getByRole("menuitem", { name: "Ink (no colour)" }).querySelector("svg")).toHaveClass("lucide-check");
  });

  it("is not offered for a deck that opens with another layout, which has nowhere to keep it", async () => {
    await show(deck({ content: "<!-- layout: text -->\n## Not a title\n\n- a\n\n---\n\n## Two\n\n- b" }), vi.fn(), { onAccent: vi.fn() });
    expect(screen.queryByRole("button", { name: /^Accent/ })).not.toBeInTheDocument();
  });

  it("is offered for a deck made before markers, whose first slide gets one when the colour is chosen", async () => {
    await show(deck(), vi.fn(), { onAccent: vi.fn() });
    expect(screen.getByRole("button", { name: "Accent colour" })).toBeEnabled();
  });

  it("cannot be chosen while the deck cannot be changed, and is not offered where there is no way to save it", async () => {
    const first = await show(deck({ content: marked("blue") }), vi.fn(), { onAccent: vi.fn(), accentDisabled: true });
    expect(screen.getByRole("button", { name: "Accent" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Accent colour" })).not.toBeInTheDocument();
    first.unmount();
    await show(deck({ content: marked("blue") }));
    expect(screen.queryByRole("button", { name: /^Accent/ })).not.toBeInTheDocument();
  });
});

describe("pointing at a slide", () => {
  it("tells which slide is in view, on every change, and again for another version", async () => {
    const user = userEvent.setup(); const onSlide = vi.fn();
    const { rerender } = await show(deck(), vi.fn(), { onSlide });
    expect(onSlide).toHaveBeenLastCalledWith(0);
    await user.click(screen.getByRole("button", { name: "Go to slide 3" }));
    expect(onSlide).toHaveBeenLastCalledWith(2);
    onSlide.mockClear();
    rerender(<SlidesView artifact={deck({ id: "art_2" })} onCite={vi.fn()} onSlide={onSlide} />);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
    expect(onSlide).toHaveBeenLastCalledWith(0);
  });

  it("offers Refine slide for the slide in view when refinement is available", async () => {
    const user = userEvent.setup(); const onAsk = vi.fn();
    const first = await show(deck(), vi.fn(), { onAsk });
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));
    await user.click(screen.getByRole("button", { name: "Refine slide" }));
    expect(onAsk).toHaveBeenCalledWith(1);
    first.unmount();
    await show(deck());
    expect(screen.queryByRole("button", { name: "Refine slide" })).not.toBeInTheDocument();
  });

  it("moves to a slide when asked to, and again to the same one with a new request", async () => {
    const user = userEvent.setup();
    const { rerender } = await show(deck(), vi.fn(), { goTo: { index: 2, nonce: 1 } });
    expect(screen.getByText("Slide 3 of 3")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Go to slide 1" }));
    expect(screen.getByText("Slide 1 of 3")).toBeVisible();
    rerender(<SlidesView artifact={deck()} onCite={vi.fn()} goTo={{ index: 2, nonce: 2 }} />);
    expect(await screen.findByText("Slide 3 of 3")).toBeVisible();
  });

  it("marks the slides a version changed, in the strip and for the slide in view", async () => {
    const user = userEvent.setup();
    await show(deck(), vi.fn(), { changed: [1] });
    const strip = within(screen.getByRole("list", { name: "Slides" }));
    expect(strip.getAllByText("Changed")).toHaveLength(1);
    expect(screen.queryByText(/· Changed/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));
    expect(screen.getByText(/· Changed/)).toBeVisible();
  });
});

describe("a designed deck in the viewer", () => {
  const designed = deck({ content: fixture("deck.designed"), citations: Array.from({ length: 12 }, (_, at) => ({ ...citation, id: `cit_${at}`, ref: at + 1 })) });

  it("draws the slides from their designs and has one Style menu instead of Theme and Accent", async () => {
    const onDeckStyle = vi.fn();
    const { container } = await show(designed, vi.fn(), { onDeckStyle, onDeckTheme: vi.fn(), onAccent: vi.fn() });
    expect(container.querySelectorAll(".dk-designed").length).toBeGreaterThan(5);
    expect(screen.queryByRole("button", { name: /Accent colour/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Deck theme/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Deck style/ }));
    expect(screen.getByRole("menuitem", { name: /Light/ })).toBeTruthy();
    await userEvent.click(screen.getByRole("menuitem", { name: /Bold/ }));
    expect(onDeckStyle).toHaveBeenCalledWith("bold");
  });

  it("keeps the Theme and Accent menus for an old studio deck", async () => {
    const old = deck({ content: fixture("deck.short") });
    await show(old, vi.fn(), { onDeckStyle: vi.fn(), onDeckTheme: vi.fn(), onAccent: vi.fn() });
    expect(screen.getByRole("button", { name: /Deck theme/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Deck style/ })).toBeNull();
  });
});
