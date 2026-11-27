import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { useDragWidth } from "../hooks/useDragWidth";
import { InspectorRail, LibraryInspector, type InspectorTab } from "./LibraryInspector";

function Harness({ width = 400, max = 960, slideOver = false, briefUpdating = false }: {
  width?: number; max?: number; slideOver?: boolean; briefUpdating?: boolean;
}) {
  const columnRef = useRef<HTMLElement>(null);
  const panelId = "inspector-panel";
  const [collapsed, setCollapsed] = useState(slideOver);
  const [tab, setTab] = useState<InspectorTab>("studio");
  const size = useDragWidth({ storageKey: "test:inspector-width", min: 280, max, fallback: width });
  const select = (next: InspectorTab) => { setTab(next); setCollapsed(false); };
  return <LibraryInspector columnRef={columnRef} panelId={panelId}
    layout={{ collapsed, slideOver, track: collapsed || slideOver ? 44 : size.width, size }}
    rail={<InspectorRail panelId={panelId} expanded={null} running onToggle={select} />}
    onCollapse={() => setCollapsed(true)} tab={tab} onTab={select} evidenceCount={2} briefUpdating={briefUpdating} running
    evidence={<p>Evidence content</p>} studio={<p>Studio content</p>} />;
}

beforeEach(() => { window.localStorage.clear(); });

describe("LibraryInspector", () => {
  it.each([280, 560, 760, 960])("renders the allocated %dpx dock with the same bounds across tabs", async (width) => {
    const user = userEvent.setup();
    render(<Harness width={width} briefUpdating />);
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    const evidence = screen.getByRole("tab", { name: "Evidence (2)" });
    expect(screen.getByRole("tab", { name: "Studio" })).toHaveAttribute("aria-selected", "true");
    expect(evidence).toHaveAccessibleDescription("Brief updating");
    expect(screen.getByTitle("Brief updating")).toBeVisible();
    const resize = screen.getByRole("separator", { name: "Resize Inspector" });
    expect(resize).toHaveAttribute("aria-valuemin", "280");
    expect(resize).toHaveAttribute("aria-valuemax", "960");
    expect(resize).toHaveAttribute("aria-valuenow", String(width));
    await user.click(evidence);
    expect(screen.getByRole("tabpanel", { name: "Evidence" })).toBeVisible();
    expect(inspector.style.width).toBe(`${width}px`);
    expect(resize).toHaveAttribute("aria-valuenow", String(width));
    fireEvent.keyDown(resize, { key: "ArrowLeft" });
    expect(resize).toHaveAttribute("aria-valuenow", String(Math.min(width + 24, 960)));
  });

  it("uses the same controlled panel from either overlay rail trigger", async () => {
    const user = userEvent.setup();
    render(<Harness slideOver max={278} />);
    const evidence = screen.getByRole("button", { name: "Expand Evidence" });
    const studio = screen.getByRole("button", { name: "Expand Studio" });
    expect(evidence).toHaveAttribute("aria-expanded", "false");
    expect(studio).toHaveAttribute("aria-expanded", "false");
    expect(evidence).toHaveAttribute("aria-controls", studio.getAttribute("aria-controls"));
    const panel = document.getElementById(evidence.getAttribute("aria-controls")!)!;
    expect(panel).not.toBeVisible();
    await user.click(evidence);
    expect(panel).toBeVisible();
    expect(screen.getByRole("tab", { name: "Evidence (2)" })).toHaveAttribute("aria-selected", "true");
    const close = screen.getByRole("button", { name: "Collapse Inspector" });
    expect(close).toHaveAttribute("aria-expanded", "true");
    expect(close).toHaveAttribute("aria-controls", panel.id);
    expect(screen.queryByRole("separator", { name: "Resize Inspector" })).not.toBeInTheDocument();
    await user.click(close);
    await user.click(screen.getByRole("button", { name: "Expand Studio" }));
    expect(screen.getByRole("tabpanel", { name: "Studio" })).toBeVisible();
    expect(panel).toBeVisible();
  });
});
