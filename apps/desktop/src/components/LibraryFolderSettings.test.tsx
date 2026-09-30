import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryFolderSettings } from "./LibraryFolderSettings";

const api = vi.hoisted(() => ({ storage: vi.fn(), revealLibraryFolder: vi.fn() }));
vi.mock("../api", () => ({ knowledgeApi: api }));
const desktop = vi.hoisted(() => ({ libraryLocation: vi.fn(), pickLibraryDestination: vi.fn(), moveLibraries: vi.fn() }));
vi.mock("../libraryLocation", () => desktop);

const renderSettings = () => {
  const props = { onNotify: vi.fn(), onCopy: vi.fn() };
  return { ...render(<LibraryFolderSettings {...props} />), props };
};
const folders = (libraryRoot: string) => ({ libraryRoot, foldersEnabled: true, problem: null, lastSyncedAt: "2026-09-28T09:00:00Z", lastError: null });

describe("LibraryFolderSettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    Object.values(desktop).forEach((mock) => mock.mockReset());
    api.revealLibraryFolder.mockResolvedValue({ opened: true });
    desktop.libraryLocation.mockResolvedValue(null);
  });

  it("shows where libraries live and opens or copies that folder", async () => {
    api.storage.mockResolvedValue(folders("/Users/keke/Gunther"));
    const { props } = renderSettings();
    expect(await screen.findByText("/Users/keke/Gunther")).toBeInTheDocument();
    expect(screen.getByText(/Updated/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Show in Finder|Open folder/ }));
    expect(api.revealLibraryFolder).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Copy library folder path" }));
    expect(props.onCopy).toHaveBeenCalledWith("Library folder path", "/Users/keke/Gunther");
    // Outside the installed app there is nothing here to move it with.
    expect(screen.queryByRole("button", { name: /Change/ })).not.toBeInTheDocument();
  });

  it("moves the libraries to a folder picked in the installed app", async () => {
    api.storage.mockResolvedValueOnce(folders("/Users/keke/Gunther")).mockResolvedValue(folders("/Users/keke/Documents/Gunther"));
    desktop.libraryLocation.mockResolvedValue({ chosen: null, canChange: true });
    desktop.pickLibraryDestination.mockResolvedValue("/Users/keke/Documents/Gunther");
    desktop.moveLibraries.mockResolvedValue("/Users/keke/Documents/Gunther");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { props } = renderSettings();
    await userEvent.click(await screen.findByRole("button", { name: "Change…" }));
    expect(desktop.pickLibraryDestination).toHaveBeenCalledWith("/Users/keke/Gunther");
    expect(confirm.mock.calls[0]?.[0]).toContain("/Users/keke/Documents/Gunther");
    expect(desktop.moveLibraries).toHaveBeenCalledWith("/Users/keke/Gunther", "/Users/keke/Documents/Gunther");
    expect(await screen.findByText("/Users/keke/Documents/Gunther")).toBeInTheDocument();
    expect(props.onNotify).toHaveBeenCalledWith("Your libraries now live in /Users/keke/Documents/Gunther.");
    confirm.mockRestore();
  });

  it("says why a picked folder cannot be used, and moves nothing", async () => {
    api.storage.mockResolvedValue(folders("/Users/keke/Gunther"));
    desktop.libraryLocation.mockResolvedValue({ chosen: null, canChange: true });
    desktop.pickLibraryDestination.mockRejectedValue("The new place cannot be inside the current library folder.");
    const { props } = renderSettings();
    await userEvent.click(await screen.findByRole("button", { name: "Change…" }));
    await waitFor(() => expect(props.onNotify).toHaveBeenCalledWith("The new place cannot be inside the current library folder."));
    expect(desktop.moveLibraries).not.toHaveBeenCalled();
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
