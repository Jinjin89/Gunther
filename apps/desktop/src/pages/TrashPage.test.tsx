import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeTrashItem } from "../test/fixtures";
import { TrashPage } from "./TrashPage";

const api = vi.hoisted(() => ({
  trash: vi.fn(),
  restoreFromTrash: vi.fn(),
  deleteForever: vi.fn(),
  emptyTrash: vi.fn(),
}));

vi.mock("../api", () => ({ knowledgeApi: api }));

const library = makeTrashItem({ kind: "library", id: "cells", title: "Cells", color: "blue", sourceKind: null, itemCount: 2 });
const recording = makeTrashItem({ id: "src_rec", title: "Lecture 3", libraryTitles: ["Cells"] }, 28);

const renderTrash = () => {
  const props = { onNotify: vi.fn(), onRestored: vi.fn() };
  return { ...render(<TrashPage {...props} />), props };
};

describe("TrashPage", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.trash.mockResolvedValue([library, recording]);
    api.restoreFromTrash.mockImplementation(async (kind: string, id: string) => (kind === "library" ? library : { ...recording, id }));
    api.deleteForever.mockResolvedValue(recording);
    api.emptyTrash.mockResolvedValue({ deleted: 2 });
  });

  it("lists what is in Trash, what went alongside it, and the time left", async () => {
    renderTrash();
    const rows = await screen.findAllByRole("listitem");
    expect(within(rows[0]!).getByText("Library · with 2 sources")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("29 days left")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Recording · Cells")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("2 days left")).toHaveClass("is-soon");
    expect(screen.getByText(/Deleted for good after 30 days/)).toBeInTheDocument();
  });

  it("restores an item and tells the host", async () => {
    const { props } = renderTrash();
    await userEvent.click(await screen.findByRole("button", { name: "Restore “Cells”" }));
    expect(api.restoreFromTrash).toHaveBeenCalledWith("library", "cells");
    expect(props.onRestored).toHaveBeenCalledWith(library);
    expect(props.onNotify).toHaveBeenCalledWith("Restored “Cells”.");
  });

  it("deletes an item forever only after it is confirmed", async () => {
    const { props } = renderTrash();
    await userEvent.click(await screen.findByRole("button", { name: "Delete “Lecture 3” forever" }));
    const confirm = screen.getByRole("group", { name: "Confirm deleting “Lecture 3”" });
    expect(api.deleteForever).not.toHaveBeenCalled();
    await userEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Confirm deleting “Lecture 3”" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete “Lecture 3” forever" }));
    await userEvent.click(within(screen.getByRole("group", { name: "Confirm deleting “Lecture 3”" })).getByRole("button", { name: "Delete forever" }));
    expect(api.deleteForever).toHaveBeenCalledWith("source", "src_rec");
    await waitFor(() => expect(props.onNotify).toHaveBeenCalledWith("Deleted “Lecture 3” for good."));
  });

  it("empties Trash after it is confirmed", async () => {
    const { props } = renderTrash();
    await userEvent.click(await screen.findByRole("button", { name: "Empty Trash" }));
    const confirm = screen.getByRole("group", { name: "Confirm emptying Trash" });
    expect(within(confirm).getByText("Delete all 2 items for good? This can’t be undone.")).toBeInTheDocument();
    await userEvent.click(within(confirm).getByRole("button", { name: "Empty Trash" }));
    expect(api.emptyTrash).toHaveBeenCalledOnce();
    await waitFor(() => expect(props.onNotify).toHaveBeenCalledWith("Deleted 2 items for good."));
  });

  it("explains an empty Trash", async () => {
    api.trash.mockResolvedValue([]);
    renderTrash();
    expect(await screen.findByRole("heading", { name: "Trash is empty." })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Empty Trash" })).not.toBeInTheDocument();
  });
});
