import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeBase, makeSource } from "../test/fixtures";
import { HomePage } from "./HomePage";

const api = vi.hoisted(() => ({
  sources: vi.fn(),
}));

vi.mock("../api", () => ({
  knowledgeApi: api,
}));

const renderHome = (overrides: Partial<React.ComponentProps<typeof HomePage>> = {}) => {
  const props: React.ComponentProps<typeof HomePage> = {
    bases: [],
    inboxCount: 0,
    onCapture: vi.fn(),
    onSearch: vi.fn(),
    onOpenBase: vi.fn(),
    onOpenLibraries: vi.fn(),
    onCreateBase: vi.fn(),
    onOpenInbox: vi.fn(),
    ...overrides,
  };
  return { ...render(<HomePage {...props} />), props };
};

describe("HomePage", () => {
  beforeEach(() => {
    api.sources.mockResolvedValue([]);
  });

  it("keeps every capture source one click away and preserves its exact kind", async () => {
    const user = userEvent.setup();
    const { props } = renderHome();

    const actions = [
      ["Quick note", "note"],
      ["Document", "file"],
      ["Photo or scan", "image"],
      ["Web page", "link"],
      ["Recording", "recording"],
      ["Table or data", "table"],
    ] as const;

    for (const [label, kind] of actions) {
      await user.click(screen.getByRole("button", { name: new RegExp(label, "i") }));
      expect(props.onCapture).toHaveBeenLastCalledWith(kind);
    }

    await user.click(screen.getByRole("button", { name: /All capture options/i }));
    expect(props.onCapture).toHaveBeenLastCalledWith();

    await user.click(screen.getByRole("button", { name: /Web search/i }));
    expect(props.onSearch).toHaveBeenCalledOnce();
  });

  it("makes an empty workspace useful without forcing a knowledge base first", async () => {
    const user = userEvent.setup();
    const { props } = renderHome({ inboxCount: 2 });

    expect(screen.getByText("Create a home when the subject becomes clear.")).toBeVisible();
    expect(screen.getByText("Items waiting to be organized or reviewed")).toBeVisible();
    expect(screen.getByText("2")).toHaveClass("has-items");

    await user.click(screen.getByRole("button", { name: /New knowledge base/i }));
    await user.click(screen.getByRole("button", { name: /Items waiting to be organized/i }));

    expect(props.onCreateBase).toHaveBeenCalledOnce();
    expect(props.onOpenInbox).toHaveBeenCalledOnce();
  });

  it("shows only the three most recent knowledge bases and opens the selected one", async () => {
    const user = userEvent.setup();
    const bases = [
      makeBase({ id: "base-1", title: "Biology" }),
      makeBase({ id: "base-2", title: "Machine Learning" }),
      makeBase({ id: "base-3", title: "Product Strategy" }),
      makeBase({ id: "base-4", title: "Hidden fourth base" }),
    ];
    const { props } = renderHome({ bases });

    expect(screen.getByRole("button", { name: /Biology/i })).toBeVisible();
    expect(screen.getByRole("button", { name: /Machine Learning/i })).toBeVisible();
    expect(screen.getByRole("button", { name: /Product Strategy/i })).toBeVisible();
    expect(screen.queryByText("Hidden fourth base")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Machine Learning/i }));
    expect(props.onOpenBase).toHaveBeenCalledWith("base-2");
  });

  it("loads at most four recently captured sources and keeps source kinds explicit", async () => {
    api.sources.mockResolvedValue([
      makeSource({ id: "s1", title: "Lecture recording", kind: "recording" }),
      makeSource({ id: "s2", title: "Research paper", kind: "paper" }),
      makeSource({ id: "s3", title: "Lab notes", kind: "note" }),
      makeSource({ id: "s4", title: "Reference site", kind: "link" }),
      makeSource({ id: "s5", title: "Fifth source", kind: "file" }),
    ]);

    renderHome();

    await waitFor(() => expect(api.sources).toHaveBeenCalledOnce());
    expect(await screen.findByText("Lecture recording")).toBeVisible();
    expect(screen.getByText("Research paper")).toBeVisible();
    expect(screen.getByText("Lab notes")).toBeVisible();
    expect(screen.getByText("Reference site")).toBeVisible();
    expect(screen.queryByText("Fifth source")).not.toBeInTheDocument();
    expect(screen.getByText(/Recording ·/)).toBeVisible();
    expect(screen.getByText(/Paper ·/)).toBeVisible();
  });
});
