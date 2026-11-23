import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AtlasTitlebar } from "./AtlasChrome";
import { LibraryActions } from "./LibraryActions";

vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: vi.fn() }));

function setup() {
  const onEdit = vi.fn();
  const onExport = vi.fn();
  render(<AtlasTitlebar path={[{ label: "Libraries", onClick: vi.fn() }, { label: "Test library" }]} theme="light" onHome={vi.fn()} onSearch={vi.fn()} onCapture={vi.fn()} onTheme={vi.fn()} navigation={<LibraryActions onEdit={onEdit} onExport={onExport} />} />);
  return { onEdit, onExport };
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  vi.restoreAllMocks();
});

describe("library titlebar", () => {
  it("keeps library operations accessible", async () => {
    const user = userEvent.setup();
    const { onEdit, onExport } = setup();
    await user.click(screen.getByRole("button", { name: "Library actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit library details" }));
    expect(onEdit).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "Library actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Export library" }));
    expect(onExport).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Search ⌘K" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Capture" })).toBeVisible();
  });

  it("allows native window dragging while excluding the library controls", () => {
    const drag = vi.fn().mockResolvedValue(undefined);
    vi.mocked(getCurrentWindow).mockReturnValue({ startDragging: drag } as unknown as ReturnType<typeof getCurrentWindow>);
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    setup();
    fireEvent.mouseDown(screen.getByRole("button", { name: "Library actions" }), { button: 0 });
    expect(drag).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByText("Test library"), { button: 0 });
    expect(drag).toHaveBeenCalledOnce();
  });
});

it("makes the sidebar toggle available without allowing native window dragging", async () => {
  const toggle = vi.fn(); const drag = vi.fn().mockResolvedValue(undefined);
  vi.mocked(getCurrentWindow).mockReturnValue({ startDragging: drag } as unknown as ReturnType<typeof getCurrentWindow>);
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  render(<AtlasTitlebar path={[{ label: "Library" }]} theme="light" sidebarCollapsed onSidebar={toggle} onHome={vi.fn()} onSearch={vi.fn()} onCapture={vi.fn()} onTheme={vi.fn()} />);
  const button = screen.getByRole("button", { name: "Expand Gunther sidebar" });
  fireEvent.mouseDown(button, { button: 0 }); await userEvent.click(button); expect(toggle).toHaveBeenCalledOnce(); expect(drag).not.toHaveBeenCalled();
});
