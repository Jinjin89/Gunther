import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeBase } from "../test/fixtures";
import { AtlasLibraryPage } from "./AtlasLibraryPage";

describe("AtlasLibraryPage", () => {
  it("displays each library's stored source count and last update", () => {
    const updatedAt = "2026-10-19T15:00:00Z";
    render(
      <AtlasLibraryPage
        bases={[
          makeBase({ id: "indexed", title: "Indexed base", sourceCount: 8, updatedAt }),
          makeBase({ id: "small", title: "Small base", sourceCount: 1 }),
        ]}
        onOpen={vi.fn()}
        onAdd={vi.fn()}
        onCreateBase={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Libraries" })).toBeVisible();
    expect(screen.getByText("2 libraries")).toBeVisible();
    expect(screen.getByText("8 sources")).toBeVisible();
    expect(screen.getByText("1 source")).toBeVisible();
    expect(screen.getAllByTitle("Last updated")[0]).toHaveAttribute("datetime", updatedAt);
  });

  it("routes capture, creation, and opening through explicit callbacks", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const onAdd = vi.fn();
    const onCreateBase = vi.fn();
    render(
      <AtlasLibraryPage
        bases={[makeBase({ id: "bio", title: "Bioinformatics" })]}
        onOpen={onOpen}
        onAdd={onAdd}
        onCreateBase={onCreateBase}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^Capture$/i }));
    await user.click(screen.getByRole("button", { name: /^New library$/i }));
    await user.click(screen.getByRole("button", { name: /Bioinformatics/i }));

    expect(onAdd).toHaveBeenCalledOnce();
    expect(onCreateBase).toHaveBeenCalledOnce();
    expect(onOpen).toHaveBeenCalledWith("bio");
  });

  it("keeps a clear creation path when no libraries exist", async () => {
    const user = userEvent.setup();
    const onCreateBase = vi.fn();
    render(<AtlasLibraryPage bases={[]} onOpen={vi.fn()} onAdd={vi.fn()} onCreateBase={onCreateBase} />);

    expect(screen.getByText("0 libraries")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Start a new library/i }));
    expect(onCreateBase).toHaveBeenCalledOnce();
  });

  it("filters a longer list of libraries by name or description", async () => {
    const user = userEvent.setup();
    render(
      <AtlasLibraryPage
        bases={[
          makeBase({ id: "bio", title: "Bioinformatics" }),
          makeBase({ id: "ml", title: "Machine Learning", description: "Models and evaluation." }),
          makeBase({ id: "pm", title: "Project Management", description: "Planning and delivery." }),
          makeBase({ id: "ux", title: "Design", description: "Interfaces and research." }),
        ]}
        onOpen={vi.fn()}
        onAdd={vi.fn()}
        onCreateBase={vi.fn()}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: "Filter libraries" }), "planning");
    expect(screen.getByRole("button", { name: /Project Management/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /Bioinformatics/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start a new library/ })).not.toBeInTheDocument();

    await user.clear(screen.getByRole("textbox", { name: "Filter libraries" }));
    await user.type(screen.getByRole("textbox", { name: "Filter libraries" }), "zzz");
    expect(screen.getByText("No library matches “zzz”.")).toBeVisible();
  });
});
