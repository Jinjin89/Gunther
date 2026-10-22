import { fireEvent, render, screen } from "@testing-library/react";
import type { Artifact } from "@gunther/contracts";
import { describe, expect, it, vi } from "vitest";
import { SlideReviewPanel, visualFollowUp } from "./SlideReviewPanel";

const finding = { slideId: "evidence", blockId: "b1", severity: "error" as const, problem: "Labels are too small", correction: "Give the evidence more room.", route: "layout" as const };
function artifact(status = "needs_review") {
  return { id: "v1", kind: "slides", outline: [{ id: "cover" }, { id: "evidence" }], provenance: { skill: {
    visualReview: { status, imageReview: "reviewed", repairCycles: 1, repairedSlides: [], findings: [finding], calls: 3, history: [] },
    renderCheck: { status: "captured", id: "abc", contact: "contact.png", pdf: "deck.pdf", slides: [{ id: "cover", index: 0, image: "slide-01.png" }] },
  } } } as unknown as Artifact;
}
describe("rendered slide review", () => {
  it("keeps actionable findings outside the slide and links the inspected renders", () => {
    const follow = vi.fn();
    render(<SlideReviewPanel artifact={artifact()} baseId="library" workspaceId="local" disabled={false} onFollowUp={follow}/>);
    fireEvent.click(screen.getByRole("button", { name: "Slides need visual review" }));
    expect(screen.getByText("Slide 2: Labels are too small")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Request layout repair" }));
    expect(follow.mock.calls[0]![0]).toContain("Preserve all source bindings, values, units");
    expect(screen.getByRole("link", { name: "Inspected PDF" })).toHaveAttribute("href", expect.stringContaining("/renders/abc/deck.pdf?workspace_id=local"));
    expect(screen.getByRole("link", { name: "Inspected PDF" })).toHaveAttribute("download", "gunther-reviewed-deck.pdf");
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByText(/Cached renders are unavailable/)).toBeInTheDocument();
  });
  it("does not show stale screenshots as the inspected current deck", () => {
    const deck = artifact("stale");
    deck.provenance.skill!.renderCheck!.status = "stale";
    render(<SlideReviewPanel artifact={deck} baseId="b" workspaceId="w" disabled={true} onFollowUp={vi.fn()}/>);
    fireEvent.click(screen.getByRole("button", { name: "Slide review is out of date" }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request layout repair" })).toBeDisabled();
  });
  it("routes wording and structure through the checked editing path", () => {
    expect(visualFollowUp({ ...finding, route: "write" }, 2)).toMatch(/^Rewrite and fact-check slide 2/);
    expect(visualFollowUp({ ...finding, route: "plan" }, 2)).toMatch(/^Replan the outline and split or reorder slide 2/);
  });
  it("offers image review for an older deck without rewriting its content", () => {
    const deck = artifact();
    delete deck.provenance.skill!.visualReview;
    delete deck.provenance.skill!.renderCheck;
    const review = vi.fn();
    render(<SlideReviewPanel artifact={deck} baseId="b" workspaceId="w" disabled={false} onReview={review} onFollowUp={vi.fn()}/>);
    fireEvent.click(screen.getByRole("button", { name: "Review rendered slides" }));
    expect(review).toHaveBeenCalledOnce();
  });
  it("inspects full-size images inside the desktop instead of opening private service links", () => {
    render(<SlideReviewPanel artifact={artifact()} baseId="b" workspaceId="w" disabled={false} onFollowUp={vi.fn()}/>);
    fireEvent.click(screen.getByRole("button", { name: "Slides need visual review" }));
    fireEvent.click(screen.getByRole("button", { name: "Slide 1" }));
    expect(screen.getByRole("region", { name: "Full-size inspected render" })).toBeInTheDocument();
    expect(screen.getByAltText("Full-size inspected render")).toHaveStyle({ width: "1280px" });
    fireEvent.click(screen.getByRole("button", { name: "Close render" }));
    expect(screen.queryByRole("region", { name: "Full-size inspected render" })).not.toBeInTheDocument();
  });
});
