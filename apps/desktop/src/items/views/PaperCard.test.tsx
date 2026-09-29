import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../../api";
import { PaperCard } from "./PaperCard";

afterEach(() => { vi.restoreAllMocks(); });

describe("PaperCard", () => {
  it("shows what the paper says about itself", async () => {
    vi.spyOn(knowledgeApi, "sourcePaper").mockResolvedValue({
      sourceId: "s1", revisionId: "r1", title: "Attention Is All You Need", authors: "Ashish Vaswani, Noam Shazeer",
      year: 2017, doi: null, arxivId: "1706.03762", abstract: "The dominant sequence transduction models. ".repeat(12).trim(),
      abstractBlockIds: ["b1"], outline: ["1 Introduction", "2 Background"], summaryMarkdown: "# Attention",
      copies: [{ id: "s2", title: "attention (1)" }],
    });
    render(<PaperCard sourceId="s1" revisionId="r1" />);
    expect(await screen.findByText("Ashish Vaswani, Noam Shazeer · 2017")).toBeVisible();
    expect(screen.getByRole("link", { name: "arXiv 1706.03762" })).toHaveAttribute("href", "https://arxiv.org/abs/1706.03762");
    await userEvent.click(screen.getByRole("button", { name: "Show the whole abstract" }));
    expect(screen.getByRole("button", { name: "Show less" })).toBeVisible();
    expect(screen.getByText("2 sections")).toBeVisible();
    expect(screen.getByText(/Also saved as “attention \(1\)”/)).toBeVisible();
  });

  it("stays out of the way until the paper has been read", async () => {
    const read = vi.spyOn(knowledgeApi, "sourcePaper").mockRejectedValue(new Error("This source has not been read yet"));
    const { container } = render(<PaperCard sourceId="s1" revisionId="r1" />);
    await vi.waitFor(() => expect(read).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
