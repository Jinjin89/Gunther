import type { LibraryContents, OutputScope } from "@gunther/contracts";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { makeSource } from "../test/fixtures";
import { MaterialSelectionPanel } from "./MaterialSelectionPanel";

const arranged: LibraryContents = {
  sections: [
    { id: "course", parentId: null, title: "Methods course", description: "", position: 0, version: 1, passageCount: 0 },
    { id: "week", parentId: "course", title: "Week 1", description: "", position: 1, version: 1, passageCount: 0 },
  ],
  placements: [{ sourceId: "src_1", sectionId: "course", position: 0 }, { sourceId: "src_2", sectionId: "week", position: 0 }],
  sources: [],
};

function setup(options: { contents?: LibraryContents | Error; disabled?: boolean } = {}) {
  vi.spyOn(knowledgeApi, "sources").mockResolvedValue([makeSource({ id: "src_1", title: "Markers" }), makeSource({ id: "src_2", title: "Source 2" }), makeSource({ id: "src_3", title: "Source 3" })]);
  vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([]);
  vi.spyOn(knowledgeApi, "sessions").mockResolvedValue([]);
  const contents = vi.spyOn(knowledgeApi, "contents");
  if (options.contents instanceof Error) contents.mockRejectedValue(options.contents);
  else contents.mockResolvedValue(options.contents ?? arranged);
  const changed = vi.fn();
  const loaded = vi.fn();
  function Harness() {
    const [scope, setScope] = useState<OutputScope>({ mode: "library", sourceIds: [], unitIds: [], sessionIds: [] });
    return <MaterialSelectionPanel baseId="test" scope={scope} onLoaded={loaded} onAdd={vi.fn()} disabled={options.disabled ?? false} onChange={(next) => { changed(next); setScope(next); }} />;
  }
  render(<Harness />);
  return { changed, loaded };
}
afterEach(() => vi.restoreAllMocks());

describe("persistent material selection", () => {
  it("starts with the library selected and applies a checkbox change without a dialog", async () => {
    const user = userEvent.setup();
    const { changed } = setup();
    const panel = within(screen.getByRole("complementary", { name: "Source selection" }));
    expect(await panel.findByRole("checkbox", { name: /Markers/ })).toBeChecked();
    expect(panel.getByRole("checkbox", { name: /Source 2/ })).toBeChecked();
    expect(panel.getByRole("checkbox", { name: /Source 3/ })).toBeChecked();
    await user.click(panel.getByRole("checkbox", { name: /Source 2/ }));
    expect(changed).toHaveBeenLastCalledWith({ mode: "selection", sourceIds: ["src_1", "src_3"], unitIds: [], sessionIds: [] });
    expect(panel.getByRole("checkbox", { name: "Select Methods course" })).toBePartiallyChecked();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("selects a course and its nested sources without dropping concurrent imports", async () => {
    const user = userEvent.setup();
    const { changed } = setup();
    const panel = within(screen.getByRole("complementary", { name: "Source selection" }));
    const course = await panel.findByRole("checkbox", { name: "Select Methods course" });
    expect(panel.getByText("Not placed yet")).toBeVisible();
    await user.click(course);
    expect(panel.getByRole("checkbox", { name: /Markers/ })).not.toBeChecked();
    expect(panel.getByRole("checkbox", { name: /Source 2/ })).not.toBeChecked();
    expect(panel.getByRole("checkbox", { name: /Source 3/ })).toBeChecked();
    await user.click(course);
    expect(course).toBeChecked();
    expect(changed.mock.calls.at(-1)?.[0].sourceIds).toEqual(expect.arrayContaining(["src_1", "src_2", "src_3"]));
  });

  it("filters with the section path and changes only matching descendants", async () => {
    const user = userEvent.setup();
    const { changed } = setup();
    const panel = within(screen.getByRole("complementary", { name: "Source selection" }));
    const course = await panel.findByRole("checkbox", { name: "Select Methods course" });
    const filter = panel.getByRole("textbox", { name: "Filter materials" });
    await user.type(filter, "Source 2");
    expect(panel.getByText("Week 1")).toBeVisible();
    expect(panel.queryByText("Markers")).not.toBeInTheDocument();
    await user.click(course);
    expect(changed).toHaveBeenLastCalledWith({ mode: "selection", sourceIds: ["src_1", "src_3"], unitIds: [], sessionIds: [] });
    await user.clear(filter);
    expect(course).toBePartiallyChecked();
    expect(panel.getByRole("checkbox", { name: /Markers/ })).toBeChecked();
    expect(panel.getByRole("checkbox", { name: /Source 2/ })).not.toBeChecked();
    await user.type(filter, "Methods course");
    expect(panel.getByText("Markers")).toBeVisible();
    expect(panel.getByText("Source 2")).toBeVisible();
    expect(panel.queryByText("Source 3")).not.toBeInTheDocument();
  });

  it("includes collapsed descendants when clearing and selecting all", async () => {
    const user = userEvent.setup();
    setup();
    const panel = within(screen.getByRole("complementary", { name: "Source selection" }));
    await user.click(await panel.findByRole("button", { name: "Collapse Methods course" }));
    expect(panel.queryByText("Markers")).not.toBeInTheDocument();
    await user.click(panel.getByRole("button", { name: "Clear" }));
    await user.click(panel.getByRole("button", { name: "Select all" }));
    await user.click(panel.getByRole("button", { name: "Expand Methods course" }));
    expect(panel.getByRole("checkbox", { name: /Markers/ })).toBeChecked();
    expect(panel.getByRole("checkbox", { name: /Source 2/ })).toBeChecked();
    expect(panel.getByRole("checkbox", { name: /Source 3/ })).toBeChecked();
  });

  it("keeps source selection available when the arrangement cannot be read", async () => {
    const user = userEvent.setup();
    const { changed } = setup({ contents: new Error("Unavailable") });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Contents couldn’t be loaded"));
    await user.click(screen.getByRole("checkbox", { name: /Markers/ }));
    expect(changed.mock.calls.at(-1)?.[0].sourceIds).toEqual(["src_2", "src_3"]);
  });

  it("prevents selection changes while an artifact is being built", async () => {
    const { changed } = setup({ disabled: true });
    expect(await screen.findByRole("checkbox", { name: /Markers/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clear" })).toBeDisabled();
    expect(changed).not.toHaveBeenCalled();
  });
});
