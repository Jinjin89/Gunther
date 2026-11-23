import type { Artifact } from "@gunther/contracts";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { MindMapView } from "./MindMapView";

const header = "<!-- map: mind_map; design: v1 -->";
const map = (content: string) => ({
  id: "map-1", content: `${header}\n${content}`, kind: "map", sections: [],
  citations: [{ id: "source-1", sourceTitle: "Trial log", quote: "The log records the result." }],
}) as unknown as Artifact;

it("opens and closes branches independently of Ask and citation activation", async () => {
  const user = userEvent.setup();
  const onAskChat = vi.fn();
  const onCite = vi.fn();
  const artifact = map("- Trial\n  - Setup [1]\n    - Results\n      - A measurement [1]\n  - Notes");
  render(<MindMapView artifact={artifact} onAskChat={onAskChat} onCite={onCite} />);
  const node = (name: string) => within(screen.getByRole("article", { name }));
  expect(screen.getByRole("article", { name: "Notes" })).toBeVisible();
  expect(screen.queryByRole("article", { name: "A measurement" })).not.toBeInTheDocument();

  await user.tab();
  const rootToggle = node("Trial").getByRole("button", { name: "Collapse Trial" });
  expect(rootToggle).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(screen.queryByRole("article", { name: "Setup" })).not.toBeInTheDocument();
  await user.keyboard("{Enter}");
  await user.click(node("Results").getByText("Results"));
  expect(screen.getByRole("article", { name: "A measurement" })).toBeVisible();

  const citation = node("Setup").getByRole("button", { name: "Inspect citation 1" });
  expect(citation).toHaveAttribute("data-opens-evidence");
  await user.click(citation);
  expect(onCite).toHaveBeenCalledExactlyOnceWith(artifact.citations[0], 0);
  expect(onAskChat).not.toHaveBeenCalled();
  expect(node("Setup").getByRole("button", { name: "Collapse Setup" })).toHaveAttribute("aria-expanded", "true");
  await user.click(node("Setup").getByRole("button", { name: "Ask about Setup" }));
  expect(onAskChat).toHaveBeenCalledExactlyOnceWith("Tell me about Trial › Setup");
  expect(onCite).toHaveBeenCalledOnce();
  expect(screen.getByRole("article", { name: "A measurement" })).toBeVisible();

  await user.click(node("Results").getByRole("button", { name: "Collapse Results" }));
  expect(screen.queryByRole("article", { name: "A measurement" })).not.toBeInTheDocument();
});

it("lays out a sixty-node map inside the scroll region", () => {
  const branches = Array.from({ length: 59 }, (_, index) => `  - Branch ${index + 1}`);
  render(<MindMapView artifact={map(["- Root", ...branches].join("\n"))} />);
  const mapRegion = screen.getByRole("region", { name: "Mind map" });
  expect(within(mapRegion).getAllByRole("article")).toHaveLength(60);
  expect(mapRegion.querySelectorAll("svg.mind-map-links path")).toHaveLength(59);
});
