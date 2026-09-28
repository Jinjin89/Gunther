import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryFolderSettings } from "./LibraryFolderSettings";

const api = vi.hoisted(() => ({ storage: vi.fn(), revealLibraryFolder: vi.fn() }));
vi.mock("../api", () => ({ knowledgeApi: api }));

const renderSettings = () => {
  const props = { onNotify: vi.fn(), onCopy: vi.fn() };
  return { ...render(<LibraryFolderSettings {...props} />), props };
};

describe("LibraryFolderSettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.revealLibraryFolder.mockResolvedValue({ opened: true });
  });

  it("shows where libraries live and opens or copies that folder", async () => {
    api.storage.mockResolvedValue({ libraryRoot: "/Users/keke/Gunther", foldersEnabled: true, problem: null, lastSyncedAt: "2026-09-28T09:00:00Z", lastError: null });
    const { props } = renderSettings();
    expect(await screen.findByText("/Users/keke/Gunther")).toBeInTheDocument();
    expect(screen.getByText(/Updated/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Show in Finder|Open folder/ }));
    expect(api.revealLibraryFolder).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Copy library folder path" }));
    expect(props.onCopy).toHaveBeenCalledWith("Library folder path", "/Users/keke/Gunther");
  });

  it("explains how to turn folders on when no root is set", async () => {
    api.storage.mockResolvedValue({ libraryRoot: null, foldersEnabled: false, problem: null, lastSyncedAt: null, lastError: null });
    renderSettings();
    expect(await screen.findByText("Not set")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show in Finder|Open folder/ })).not.toBeInTheDocument();
  });

  it("says why a configured root is not in use", async () => {
    api.storage.mockResolvedValue({ libraryRoot: "/Users/keke/Gunther", foldersEnabled: false, problem: "/Users/keke/Gunther belongs to another Gunther workspace. Choose another LIBRARY_ROOT.", lastSyncedAt: null, lastError: null });
    renderSettings();
    expect(await screen.findByText("Not in use")).toBeInTheDocument();
    expect(screen.getByText(/belongs to another Gunther workspace/)).toBeInTheDocument();
  });

  it("reports a folder that could not be opened", async () => {
    api.storage.mockResolvedValue({ libraryRoot: "/x", foldersEnabled: true, problem: null, lastSyncedAt: null, lastError: null });
    api.revealLibraryFolder.mockRejectedValue(new Error("No file manager could open the library folder"));
    const { props } = renderSettings();
    await userEvent.click(await screen.findByRole("button", { name: /Show in Finder|Open folder/ }));
    expect(props.onNotify).toHaveBeenCalledWith("No file manager could open the library folder");
  });
});
