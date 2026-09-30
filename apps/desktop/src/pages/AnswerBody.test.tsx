import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentSteps, AnswerBody, withCitationLinks } from "./AnswerBody";

describe("AnswerBody", () => {
  it("links only citations that exist", () => {
    expect(withCitationLinks("A [1] and B [3].", [1, 2])).toBe("A [1](#cite-1) and B [3].");
    expect(withCitationLinks("A [4] and B [1].", [4, 6])).toBe("A [4](#cite-4) and B [1].");
  });

  it("renders Markdown with citation buttons that open the evidence", async () => {
    const onCitation = vi.fn();
    render(<AnswerBody content={"**CD3D** marks T cells [1].\n\n- one\n- two"} numbers={[1]} onCitation={onCitation} />);
    expect(screen.getByText("CD3D").tagName).toBe("STRONG");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 1" }));
    expect(onCitation).toHaveBeenCalledOnce();
  });

  it("opens the citation by its pool number and marks claims no source backs", async () => {
    const onCitation = vi.fn();
    render(<AnswerBody content="Sourced [6]. Own knowledge [?]." numbers={[4, 6]} onCitation={onCitation} />);
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 6" }));
    expect(onCitation).toHaveBeenCalledWith(1);
    expect(screen.getByText("unverified")).toBeInTheDocument();
  });

  it("never loads remote images and only opens web links", () => {
    render(<AnswerBody content={"![x](https://tracker.example/p.png) [site](https://example.org) [bad](javascript:alert(1))"} numbers={[]} />);
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: "site" })).toHaveAttribute("href", "https://example.org");
    expect(screen.queryByRole("link", { name: "bad" })).toBeNull();
  });
});

describe("AgentSteps", () => {
  it("shows each search and what it found, and failures", () => {
    render(<AgentSteps steps={[
      { tool: "search_library", label: "Searched your library for “CD3D”", query: "CD3D", found: 3, error: null },
      { tool: "search_web", label: "Searched the web for “CD3D news”", query: "CD3D news", found: 0, error: "Tavily did not accept the API key." },
    ]} />);
    expect(screen.getByText("3 results")).toBeInTheDocument();
    expect(screen.getByText("Tavily did not accept the API key.")).toBeInTheDocument();
  });

  it("shows nothing when the agent searched nothing", () => {
    const { container } = render(<AgentSteps steps={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
