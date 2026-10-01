import type { Artifact, ConversationCitation } from "@gunther/contracts";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
async function show(artifact: Artifact, onCite = vi.fn()) {
  const view = render(<SlidesView artifact={artifact} onCite={onCite} />);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
  return { ...view, onCite };
}

afterEach(() => {
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

  it("presents full screen in a browser, and Esc leaves it", async () => {
    const user = userEvent.setup();
    const request = vi.fn(async () => undefined);
    Object.defineProperty(document.documentElement, "requestFullscreen", { configurable: true, value: request });
    await show(deck());

    await user.click(screen.getByRole("button", { name: "Present" }));
    expect(document.querySelector(".outputs-stage")).toHaveClass("is-presenting");
    expect(request).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Leave full screen" })).toBeVisible();

    await user.keyboard("{Escape}");
    expect(document.querySelector(".outputs-stage")).not.toHaveClass("is-presenting");
    expect(screen.queryByRole("button", { name: "Leave full screen" })).not.toBeInTheDocument();
  });

  it("presents through the app's window, and gives the screen back", async () => {
    const user = userEvent.setup();
    (window as unknown as Record<string, unknown>)["__TAURI_INTERNALS__"] = {};
    await show(deck());

    await user.click(screen.getByRole("button", { name: "Present" }));
    await act(async () => undefined);
    expect(setFullscreen).toHaveBeenLastCalledWith(true);
    await user.click(screen.getByRole("button", { name: "Leave full screen" }));
    await act(async () => undefined);
    expect(setFullscreen).toHaveBeenLastCalledWith(false);
    expect(document.querySelector(".outputs-stage")).not.toHaveClass("is-presenting");
  });

  it("says so when a deck has no slides", async () => {
    await show(deck({ content: "" }));
    expect(screen.getByText("This deck has no slides yet.")).toBeVisible();
  });
});
