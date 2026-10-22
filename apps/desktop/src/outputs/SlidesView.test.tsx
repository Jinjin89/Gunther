import type { Artifact, ConversationCitation } from "@gunther/contracts";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SlidesView } from "./SlidesView";

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

afterEach(async () => {
  cleanup();
  // Reveal's window-resize text fitting debounces for 100 ms. Let its deferred
  // destroy and trailing animation frame finish before jsdom closes the window.
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 120)); });
  delete (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"];
  setFullscreen.mockClear();
});

describe("the slide viewer", () => {
  it("shows every slide in the deck and as a thumbnail, with the notes of the one in view", async () => {
    const user = userEvent.setup();
    await show(deck());

    expect(document.querySelectorAll(".slides > section")).toHaveLength(3);
    const strip = screen.getByRole("list", { name: "Slides" });
    expect(within(strip).getAllByRole("button")).toHaveLength(3);
    expect(screen.getByText("Slide 1 of 3")).toBeVisible();
    // The title slide has no notes; the second does.
    expect(screen.getByText("No notes for this slide.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));
    expect(screen.getByText("Slide 2 of 3")).toBeVisible();
    expect(screen.getByRole("region", { name: "Speaker notes" })).toHaveTextContent("Say that CD3D is on every T cell [1].");
    expect(screen.getByRole("button", { name: "Go to slide 2" })).toHaveAttribute("aria-current", "true");
    // What the Checker has not looked at is flagged under the deck, for the slide in view.
    expect(screen.getByText("Not re-checked")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Go to slide 3" }));
    expect(screen.queryByText("Not re-checked")).not.toBeInTheDocument();
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

  it("offers Ask about this slide for the slide in view, only when it can be asked", async () => {
    const user = userEvent.setup(); const onAsk = vi.fn();
    const first = await show(deck(), vi.fn(), { onAsk });
    await user.click(screen.getByRole("button", { name: "Go to slide 2" }));
    await user.click(screen.getByRole("button", { name: "Ask about this slide" }));
    expect(onAsk).toHaveBeenCalledWith(1);
    first.unmount();
    await show(deck());
    expect(screen.queryByRole("button", { name: "Ask about this slide" })).not.toBeInTheDocument();
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
