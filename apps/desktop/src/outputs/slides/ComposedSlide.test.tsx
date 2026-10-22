import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SlideFace } from "../SlideFace";
import { graphicsHash, sourceHash, type SceneEnvelope } from "./scene";

const body = "## Scope\n\n**72%** [1]\n\nThe measured change kept its original scope [2].";
const sceneFor = async (kind: "row" | "column"): Promise<SceneEnvelope> => ({
  version: 1,
  sourceHash: await sourceHash(body),
  root: {
    kind,
    children: [
      { kind: "leaf", blockId: "b1", view: "stat", size: "display", align: "center", role: "focus" },
      { kind: "leaf", blockId: "b2", view: "text", size: "medium", align: "start", role: "support" },
    ],
    weights: [1, 2], gap: "wide", padding: "medium", surface: "soft", align: "stretch",
  },
});

describe("composed slides", () => {
  it("draws the heading and exactly the original Markdown blocks in the requested nested layout", async () => {
    const scene = await sceneFor("row");
    const issue = vi.fn();
    const { container } = render(<SlideFace body={body} title={false} layout="composed" scene={{ scene, issue: null }} numbers={[1, 2]} onSceneIssue={issue} />);
    await waitFor(() => expect(container.querySelector(".outputs-composed-group.is-row")).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Scope" })).toBeInTheDocument();
    expect(container.querySelector('[data-block-id="b2"]')?.textContent).toContain("The measured change kept its original scope");
    expect(container.querySelectorAll("[data-block-id]")).toHaveLength(2);
    expect(issue).toHaveBeenLastCalledWith(null);
  });

  it("falls back to the complete Markdown and reports a stale fingerprint", async () => {
    const scene = await sceneFor("column");
    const changed = body.replace("original scope", "changed scope");
    const issue = vi.fn();
    const { container } = render(<SlideFace body={changed} title={false} layout="composed" scene={{ scene, issue: null }} numbers={[1, 2]} onSceneIssue={issue} />);
    await waitFor(() => expect(issue).toHaveBeenLastCalledWith("This scene is out of date for the current slide text."));
    expect(container.querySelector(".outputs-composed-group")).not.toBeInTheDocument();
    expect(container.querySelector(".answer-markdown")?.textContent).toContain("The measured change kept its changed scope");
  });

  it("keeps a chart's full table in its own leaf when the v2 grant is missing", async () => {
    const body = "## Trend\n\n| Year | Change |\n| --- | --- |\n| 2020 | −6 [1] |\n| 2021 | 3 [2] |\n\nThe measurement scope stayed the same.";
    const scene: SceneEnvelope = {
      version: 2,
      sourceHash: await sourceHash(body),
      graphicsHash: await graphicsHash(body),
      graphics: {},
      root: { kind: "row", children: [
        { kind: "leaf", blockId: "b1", view: "line_chart", size: "large", align: "stretch", role: "main" },
        { kind: "leaf", blockId: "b2", view: "text", size: "small", align: "start", role: "support" },
      ], weights: [2, 1], gap: "normal", padding: "small", surface: "none", align: "stretch" },
    };
    const issue = vi.fn();
    const { container } = render(<SlideFace body={body} title={false} layout="composed" scene={{ scene, issue: null }} numbers={[1, 2]} onSceneIssue={issue} />);
    await waitFor(() => expect(container.querySelector('[data-graphic-fallback="true"]')).toBeInTheDocument());
    const fallback = container.querySelector('[data-block-id="b1"]');
    expect(fallback?.textContent).toContain("Change");
    expect(fallback?.textContent).toContain("−6");
    expect(fallback?.textContent).toContain("2021");
    expect(container.querySelector('[data-block-id="b2"]')?.textContent).toContain("The measurement scope stayed the same");
    await waitFor(() => expect(issue.mock.calls.at(-1)?.[0]).toContain("Graphic elements fell back"));
  });

  it("switches a leaf between table and bar chart views without changing its block identity", async () => {
    const source = "## Change\n\n| Quarter | Difference |\n| --- | --- |\n| Q1 | −6 [1] |\n| Q2 | 3 [2] |\n\nThe same block remains in the composition.";
    const hash = await sourceHash(source);
    const citesHash = await graphicsHash(source);
    const makeScene = (view: "table" | "bar_chart"): SceneEnvelope => ({
      version: 2,
      sourceHash: hash,
      graphicsHash: citesHash,
      graphics: view === "bar_chart" ? { b1: "bar_chart" } : {},
      root: { kind: "row", children: [
        { kind: "leaf", blockId: "b1", view, size: "medium", align: "stretch", role: "main" },
        { kind: "leaf", blockId: "b2", view: "text", size: "small", align: "start", role: "support" },
      ], weights: [2, 1], gap: "normal", padding: "small", surface: "none", align: "stretch" },
    });
    const { container, rerender } = render(<SlideFace body={source} title={false} layout="composed" scene={{ scene: makeScene("table"), issue: null }} numbers={[1, 2]} />);
    await waitFor(() => expect(container.querySelector('[data-block-id="b1"] table')).toBeInTheDocument());
    rerender(<SlideFace body={source} title={false} layout="composed" scene={{ scene: makeScene("bar_chart"), issue: null }} numbers={[1, 2]} />);
    await waitFor(() => expect(container.querySelector('[data-graphic-view="bar_chart"]')).toBeInTheDocument());
    expect(container.querySelector('[data-block-id="b2"]')?.textContent).toContain("The same block remains in the composition");
  });
});
