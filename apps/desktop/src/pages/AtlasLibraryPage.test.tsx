import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeBase } from "../test/fixtures";
import { AtlasLibraryPage } from "./AtlasLibraryPage";

describe("AtlasLibraryPage", () => {
  it("states the durable library model and reports indexed and reference counts honestly", () => {
    const bundledBase = makeBase({ id: "bundled", title: "Bundled base", sourceCount: 1 });
    delete bundledBase.indexedSourceCount;
    render(
      <AtlasLibraryPage
        bases={[
          makeBase({ id: "indexed", title: "Indexed base", indexedSourceCount: 8, sourceCount: 2 }),
          bundledBase,
        ]}
        onOpen={vi.fn()}
        onAdd={vi.fn()}
        onCreateBase={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Knowledge with a lasting home." })).toBeVisible();
    expect(screen.getByText("2 libraries")).toBeVisible();
    expect(screen.getByText("8 indexed · 2 references")).toBeVisible();
    expect(screen.getByText("1 reference")).toBeVisible();
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
    await user.click(screen.getByRole("button", { name: /^New knowledge base$/i }));
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
    await user.click(screen.getByRole("button", { name: /Start a knowledge base/i }));
    expect(onCreateBase).toHaveBeenCalledOnce();
  });
});
