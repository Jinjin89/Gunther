import type { Artifact } from "@gunther/contracts";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { zoomTransform } from "d3-zoom";
import type { CSSProperties } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { TEXT_SIZES } from "../design/typography";
import { MindMapOutline, MindMapView } from "./MindMapView";

const header = "<!-- map: mind_map; design: v1 -->";
const map = (content: string) => ({
  id: "map-1", content: `${header}\n${content}`, kind: "map", sections: [],
  citations: [{ id: "source-1", sourceTitle: "Trial log", quote: "The log records the result." }],
}) as unknown as Artifact;

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("opens on immediate branches and keeps keyboard traversal, citations and node actions independent", async () => {
  const user = userEvent.setup();
  const onAskChat = vi.fn();
  const onAsk = vi.fn();
  const onCite = vi.fn();
  const onPart = vi.fn();
  const artifact = map("- Trial\n  - Setup [1]\n    - Results\n      - A measurement [1]\n  - Notes");
  render(<MindMapView artifact={artifact} onAskChat={onAskChat} onAsk={onAsk} onCite={onCite} onPart={onPart} />);
  const root = await screen.findByRole("treeitem", { name: "Trial" });
  const setup = screen.getByRole("treeitem", { name: "Setup" });
  const citation = within(setup).getByRole("button", { name: "Inspect citation 1" });
  const actions = within(screen.getByRole("region", { name: "Selected node" }));
  expect(screen.getAllByRole("treeitem")).toHaveLength(3);
  expect(root).toHaveAttribute("tabindex", "0");
  expect(citation).toHaveAttribute("tabindex", "-1");
  expect(within(setup).getByRole("button", { name: "Expand Setup" })).toHaveTextContent("+1");

  await user.click(citation);
  expect(root).toHaveAttribute("aria-selected", "true");
  expect(onCite).toHaveBeenCalledExactlyOnceWith(artifact.citations[0], 0);
  expect(onPart).not.toHaveBeenCalled();
  expect(setup).toHaveAttribute("aria-expanded", "false");

  const setupLabel = within(setup).getByText("Setup");
  await user.click(setupLabel);
  expect(setup).toHaveFocus();
  expect(setup).toHaveAttribute("aria-expanded", "true");
  await user.click(citation);
  expect(onCite).toHaveBeenCalledTimes(2);
  expect(setup).toHaveAttribute("aria-expanded", "true");
  await user.click(setupLabel);
  expect(setup).toHaveAttribute("aria-expanded", "false");

  const notes = screen.getByRole("treeitem", { name: "Notes" });
  fireEvent.click(within(notes).getByText("Notes"));
  expect(notes).toHaveFocus();
  expect(onPart).toHaveBeenLastCalledWith(4);

  act(() => root.focus());
  await user.keyboard("{ArrowRight}");
  expect(setup).toHaveFocus();
  await user.tab();
  expect(citation).toHaveFocus();
  await user.tab();
  expect(actions.getByRole("button", { name: "Ask about Setup" })).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(onAskChat).toHaveBeenCalledExactlyOnceWith("Tell me about Trial › Setup");

  act(() => setup.focus());
  await user.keyboard("{ArrowRight}");
  expect(setup).toHaveFocus();
  expect(setup).toHaveAttribute("aria-expanded", "true");
  await user.keyboard("{ArrowRight}");
  const results = screen.getByRole("treeitem", { name: "Results" });
  expect(results).toHaveFocus();
  await user.keyboard("{Enter}{ArrowDown}");
  expect(screen.getByRole("treeitem", { name: "A measurement" })).toHaveFocus();
  await user.click(actions.getByRole("button", { name: "Refine" }));
  expect(onAsk).toHaveBeenCalledExactlyOnceWith(3);
  expect(onPart).toHaveBeenLastCalledWith(3);
  await user.click(within(setup).getByRole("button", { name: "Collapse Setup" }));
  expect(setup).toHaveFocus();
  expect(screen.queryByRole("treeitem", { name: "A measurement" })).not.toBeInTheDocument();
  await user.keyboard("{End}");
  expect(screen.getByRole("treeitem", { name: "Notes" })).toHaveFocus();
  await user.keyboard("{ArrowUp}");
  expect(setup).toHaveFocus();
  await user.keyboard("{ArrowLeft}{Enter}");
  expect(root).toHaveFocus();
  expect(screen.getAllByRole("treeitem")).toEqual([root]);
});

it("shows the decoded text of a formatted selection in its action bar", async () => {
  const onAskChat = vi.fn();
  render(<MindMapView artifact={map("- Root\n  - **Inspection** [roof log](https://example.invalid/log) &amp; `notes`")} onAskChat={onAskChat} />);
  const item = await screen.findByRole("treeitem", { name: /Inspection/ });
  fireEvent.click(within(item).getByText("Inspection"));
  const selection = within(screen.getByRole("region", { name: "Selected node" }));
  expect(selection.getByText("Inspection roof log & notes", { selector: "strong" })).toBeVisible();
  expect(selection.getByRole("button", { name: "Ask about Inspection roof log & notes" })).toBeVisible();
  expect(selection.queryByRole("link")).not.toBeInTheDocument();
});

it("opens history ancestors by saved position and keeps selection and expansion across modes", async () => {
  const user = userEvent.setup();
  const onPart = vi.fn();
  const artifact = map("- Root\n  - Branch\n      - Displaced row\n    - Repeat\n  - Repeat");
  const props = { artifact, onPart, changed: [3] };
  const { rerender } = render(<MindMapView {...props} />);
  const branch = await screen.findByRole("treeitem", { name: "Branch" });
  rerender(<MindMapView {...props} goTo={{ index: 3, nonce: 1 }} />);
  const [child, sibling] = screen.getAllByRole("treeitem", { name: "Repeat" });
  expect(child).toHaveAttribute("data-node-position", "3");
  expect(sibling).toHaveAttribute("data-node-position", "4");
  expect(child).toHaveFocus();
  expect(onPart).toHaveBeenLastCalledWith(3);
  expect(branch).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("Position 3 · - Displaced row")).toBeInTheDocument();

  await user.click(within(child!).getByRole("button", { name: "Changed · Repeat" }));
  expect(screen.getByRole("region", { name: "Selected node" })).toHaveFocus();
  expect(screen.getByText("Changed", { exact: true })).toBeVisible();

  await user.click(screen.getByRole("button", { name: "Outline" }));
  expect(within(screen.getByRole("tree", { name: "Mind map outline" })).getAllByRole("treeitem", { name: "Repeat" })[0])
    .toHaveAttribute("aria-selected", "true");
  await user.click(screen.getByRole("button", { name: "Map" }));
  expect(await screen.findByRole("treeitem", { name: "Branch" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getAllByRole("treeitem", { name: "Repeat" })[0]).toHaveAttribute("aria-selected", "true");
});

it("moves keyboard focus to the revealed Outline row at Extra large and measures the footer again on Arrow Up", async () => {
  const user = userEvent.setup();
  const leaves = Array.from({ length: 20 }, (_, index) => `    - Leaf ${index + 1}`);
  const artifact = map(["- Root", "  - Branch", ...leaves].join("\n"));
  const extraLarge = TEXT_SIZES.find((size) => size.id === "xlarge")!;
  const width = 320;
  render(<div className="library-middle-page" style={{ width, "--gx-fs": extraLarge.scale } as CSSProperties}>
    <div className="library-middle-bar" />
    <MindMapView artifact={artifact} goTo={{ index: 21, nonce: 1 }} />
  </div>);
  expect(await screen.findByRole("treeitem", { name: "Leaf 20" })).toHaveFocus();
  const outlineButton = screen.getByRole("button", { name: "Outline" });
  const footer = screen.getByRole("region", { name: "Selected node" });
  const page = footer.closest<HTMLElement>(".library-middle-page")!;
  let footerHeight = 109.5;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    if (this === page) return DOMRect.fromRect({ width, height: 400 });
    if (this.matches(".library-middle-bar")) return DOMRect.fromRect({ height: 44 });
    if (this === outlineButton) return DOMRect.fromRect({ y: 75 - page.scrollTop, height: 32 });
    if (this === footer) return DOMRect.fromRect({ y: 400 - footerHeight, height: footerHeight });
    if (this.matches(".mind-map-outline-item > .mind-map-node-row")) {
      const position = Number((this.parentElement as HTMLElement).dataset.nodePosition);
      return DOMRect.fromRect({ y: (position - 1) * 20 - page.scrollTop, height: 40 });
    }
    return DOMRect.fromRect();
  });

  await user.tab({ shift: true });
  expect(outlineButton).toHaveFocus();
  await user.keyboard("{Enter}");
  const outline = screen.getByRole("tree", { name: "Mind map outline" });
  const selected = within(outline).getByRole("treeitem", { name: "Leaf 20" });
  expect(outlineButton.getBoundingClientRect().bottom).toBeLessThanOrEqual(page.getBoundingClientRect().top);
  expect(selected).toHaveFocus();
  expect(page.scrollTop).toBe(153.5);
  expect(selected.firstElementChild?.getBoundingClientRect().bottom).toBeLessThan(footer.getBoundingClientRect().top);

  footerHeight = 152;
  await user.keyboard("{ArrowUp}");
  const previous = within(outline).getByRole("treeitem", { name: "Leaf 19" });
  expect(previous).toHaveFocus();
  expect(page.scrollTop).toBe(176);
  expect(previous.firstElementChild?.getBoundingClientRect().bottom).toBeLessThan(footer.getBoundingClientRect().top);
});

function measuredCanvas(width: number, overrides = new Map<number, { width: number; height: number }>()) {
  const viewport = { width, height: 420 };
  const isViewport = (element: HTMLElement) => element.classList.contains("mind-map-scroll");
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
    return isViewport(this) ? viewport.width : 0;
  });
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
    return isViewport(this) ? viewport.height : 0;
  });
  const size = (element: HTMLElement) => overrides.get(Number(element.dataset.nodePosition)) ?? { width: 130, height: 24 };
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) { return size(this).width; });
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) { return size(this).height; });
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  let notify: (() => void) | undefined;
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { notify = callback; }
    observe() {}
    disconnect() {}
  });
  return {
    viewport,
    async notifyResize() {
      await act(async () => {
        notify?.();
        await new Promise(requestAnimationFrame);
      });
    },
  };
}

function screenBounds(node: HTMLElement) {
  const transform = zoomTransform(node.closest(".mind-map-scroll")!);
  const left = transform.applyX(Number.parseFloat(node.style.left));
  const top = transform.applyY(Number.parseFloat(node.style.top));
  return { left, top, right: left + node.offsetWidth * transform.k, bottom: top + node.offsetHeight * transform.k };
}

it("fits 59 measured siblings without overlap, keeps the initial labels readable and restores actual size", async () => {
  measuredCanvas(690, new Map([[1, { width: 210, height: 100 }]]));
  const user = userEvent.setup();
  const branches = Array.from({ length: 59 }, (_, index) => `  - Branch ${index + 1}`);
  render(<MindMapView artifact={map(["- Root", ...branches].join("\n"))} />);
  const tree = await screen.findByRole("tree", { name: "Mind map tree" });
  const [root, ...ordered] = within(tree).getAllByRole("treeitem");
  expect(ordered).toHaveLength(59);
  expect(tree.querySelectorAll("svg.mind-map-links path")).toHaveLength(59);
  expect(screenBounds(root!).top).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "Actual size" })).toHaveTextContent("86%");
  for (const [index, node] of ordered.entries()) {
    const next = ordered[index + 1];
    if (next) expect(screenBounds(next).top).toBeGreaterThanOrEqual(screenBounds(node).bottom);
  }
  await user.click(screen.getByRole("button", { name: "Fit" }));
  expect(screenBounds(ordered[0]!).top).toBeGreaterThanOrEqual(24 - 0.01);
  expect(screenBounds(ordered.at(-1)!).bottom).toBeLessThan(420);
  await user.click(screen.getByRole("button", { name: "Actual size" }));
  expect(screen.getByRole("button", { name: "Actual size" })).toHaveTextContent("100%");
});

it("keeps the selected branch in place when expanding and measuring a resized viewport", async () => {
  const h = measuredCanvas(650, new Map([[3, { width: 190, height: 104 }]]));
  const user = userEvent.setup();
  render(<MindMapView artifact={map("- Root\n  - Branch\n    - Short\n    - Long\n  - Other")} />);
  const branch = await screen.findByRole("treeitem", { name: "Branch" });
  const top = screenBounds(branch).top;
  await user.click(within(branch).getByRole("button", { name: "Expand Branch" }));
  expect(branch).toHaveFocus();
  expect(screenBounds(branch).top).toBeCloseTo(top);
  h.viewport.width = 500;
  await h.notifyResize();
  expect(screenBounds(branch).top).toBeCloseTo(top);
});

it("reveals an edge leaf after Actual size and a narrower viewport", async () => {
  const { viewport, notifyResize } = measuredCanvas(520);
  const user = userEvent.setup();
  const artifact = map("- Root\n  - Branch\n    - Detail\n      - Edge leaf");
  render(<MindMapView artifact={artifact} goTo={{ index: 3, nonce: 1 }} />);
  const leaf = await screen.findByRole("treeitem", { name: "Edge leaf" });
  await user.click(screen.getByRole("button", { name: "Fit" }));
  expect(screenBounds(leaf).left + leaf.offsetWidth).toBeGreaterThan(viewport.width);
  const actualSize = screen.getByRole("button", { name: "Actual size" });
  await user.click(actualSize);
  expect(actualSize).toHaveTextContent("100%");
  expect(screenBounds(leaf).left).toBeGreaterThanOrEqual(0);
  expect(screenBounds(leaf).right).toBeLessThanOrEqual(viewport.width);

  viewport.width = 400;
  expect(screenBounds(leaf).right).toBeGreaterThan(viewport.width);
  await notifyResize();
  expect(screenBounds(leaf).left).toBeGreaterThanOrEqual(0);
  expect(screenBounds(leaf).right).toBeLessThanOrEqual(viewport.width);
});

it("defaults to an outline in a narrow pane and respects an explicit canvas choice", async () => {
  const h = measuredCanvas(240);
  const user = userEvent.setup();
  render(<MindMapView artifact={map("- Root\n  - Branch\n    - Detail")} />);
  const outline = await screen.findByRole("tree", { name: "Mind map outline" });
  expect(within(outline).getByRole("treeitem", { name: "Detail" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Map" }));
  expect(screen.getByRole("tree", { name: "Mind map tree" })).toBeVisible();
  h.viewport.width = 220;
  await h.notifyResize();
  expect(screen.getByRole("button", { name: "Map" })).toHaveAttribute("aria-pressed", "true");
});

it("keeps all 60 levels in the static outline and reveals the last level of a saved jump", async () => {
  const artifact = map(Array.from({ length: 60 }, (_, index) =>
    `${"  ".repeat(index)}- Level ${index + 1}${index === 59 ? " [1]" : ""}`).join("\n"));
  const onCite = vi.fn();
  const page = render(<MindMapOutline artifact={artifact} presentation="print" onCite={onCite} />);
  expect(screen.getAllByRole("listitem")).toHaveLength(60);
  expect(page.container.querySelector("[data-depth='59']")).toHaveStyle("--map-indent-level: 5");
  expect(page.container.querySelector("sup")).toHaveTextContent("[1]");
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  page.unmount();

  measuredCanvas(600);
  render(<MindMapView artifact={artifact} goTo={{ index: 59, nonce: 1 }} />);
  const leaf = await screen.findByRole("treeitem", { name: "Level 60" });
  expect(leaf).toHaveAttribute("data-node-position", "59");
  expect(leaf).toHaveFocus();
});

it("renders a single root and reports an empty map once", async () => {
  measuredCanvas(500);
  const page = render(<MindMapView artifact={map("- A single root")} />);
  const root = await screen.findByRole("treeitem", { name: "A single root" });
  await waitFor(() => expect(screenBounds(root).top).toBeGreaterThan(0));
  expect(screen.getAllByRole("treeitem")).toEqual([root]);
  page.unmount();

  render(<MindMapView artifact={map("")} />);
  expect(screen.getAllByText("No readable nodes.")).toHaveLength(1);
  expect(screen.queryByRole("tree")).not.toBeInTheDocument();
});
