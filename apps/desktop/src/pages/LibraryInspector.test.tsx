import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ArtifactMenu } from "../outputs/ArtifactMenu";
import { LibraryInspector, type InspectorTab } from "./LibraryInspector";

function Harness({ width, collapsedAtStart = false, onMenuChoice = () => {}, briefUpdating = false }: {
  width: number; collapsedAtStart?: boolean; onMenuChoice?: () => void; briefUpdating?: boolean;
}) {
  const workspace = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(collapsedAtStart);
  const [tab, setTab] = useState<InspectorTab>("studio");
  const [detail, setDetail] = useState(false);
  const [originalPdf, setOriginalPdf] = useState(false);
  return <div ref={workspace}>
    <button type="button">Outside</button>
    <LibraryInspector workspace={workspace} screenWidth={width} collapsed={collapsed} onCollapse={() => setCollapsed(true)}
      tab={tab} onTab={(next) => { setTab(next); setCollapsed(false); }} evidenceCount={2} briefUpdating={briefUpdating} running detailOpen={detail}
      onBack={() => setDetail(false)} originalPdf={originalPdf}
      evidence={<><button type="button" onClick={() => setDetail(true)}>Open detail</button><button type="button" onClick={() => setOriginalPdf(true)}>Original PDF</button>{detail && <p>Passage detail</p>}</>}
      studio={<><p>Studio content</p><ArtifactMenu label="Artifact actions" items={[{ label: "Choose action", onSelect: onMenuChoice }]} /></>} />
  </div>;
}

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("LibraryInspector", () => {
  it("uses the wider default at a wide desktop size", () => {
    vi.stubGlobal("innerWidth", 1728);
    render(<Harness width={1728} />);
    expect(screen.getByRole("separator", { name: "Resize Inspector" })).toHaveAttribute("aria-valuenow", "380");
  });

  it("starts on Studio, resizes within its limits, and widens for an original PDF", async () => {
    vi.stubGlobal("innerWidth", 1280);
    const user = userEvent.setup();
    render(<Harness width={1280} briefUpdating />);
    expect(screen.getByRole("tab", { name: "Studio" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Evidence (2)" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Evidence (2)" })).toHaveAccessibleDescription("Brief updating");
    expect(screen.getByTitle("Brief updating")).toBeVisible();
    const resize = screen.getByRole("separator", { name: "Resize Inspector" });
    expect(resize).toHaveAttribute("aria-valuenow", "336");
    expect(resize).toHaveAttribute("aria-valuemin", "280");
    expect(resize).toHaveAttribute("aria-valuemax", "440");
    await user.click(screen.getByRole("tab", { name: "Evidence (2)" }));
    await user.click(screen.getByRole("button", { name: "Open detail" }));
    await user.click(screen.getByRole("button", { name: "Original PDF" }));
    expect(resize).toHaveAttribute("aria-valuenow", "460");
    expect(resize).toHaveAttribute("aria-valuemax", "640");
    fireEvent.keyDown(resize, { key: "ArrowRight" });
    expect(resize).toHaveAttribute("aria-valuenow", "436");
  });

  it("opens either narrow rail tab and steps back before closing on Escape", async () => {
    vi.stubGlobal("innerWidth", 900);
    const user = userEvent.setup();
    render(<Harness width={900} collapsedAtStart />);
    await user.click(screen.getByRole("button", { name: "Expand Evidence" }));
    expect(screen.getByRole("tab", { name: "Evidence (2)" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("separator", { name: "Resize Inspector" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open detail" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByText("Passage detail")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Evidence (2)" })).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Expand Studio" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Expand Studio" }));
    expect(screen.getByText("Studio content")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Outside" }));
    expect(screen.getByRole("button", { name: "Expand Evidence" })).toBeVisible();
  });

  it("keeps the Inspector open for its portaled menu and closes for a workspace press", async () => {
    vi.stubGlobal("innerWidth", 900);
    const user = userEvent.setup();
    const choose = vi.fn();
    render(<Harness width={900} collapsedAtStart onMenuChoice={choose} />);
    await user.click(screen.getByRole("button", { name: "Expand Studio" }));
    await user.click(screen.getByRole("button", { name: "Artifact actions" }));
    const item = screen.getByRole("menuitem", { name: "Choose action" });
    expect(document.body.contains(item)).toBe(true);
    expect(screen.getByRole("complementary", { name: "Inspector" })).not.toContainElement(item);
    await user.click(item);
    expect(choose).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Collapse Inspector" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Outside" }));
    expect(screen.getByRole("button", { name: "Expand Studio" })).toBeVisible();
  });
});
