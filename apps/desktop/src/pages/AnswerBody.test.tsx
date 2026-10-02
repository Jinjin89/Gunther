import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentSteps, AnswerBody, citationNumbers, withCitationLinks, withoutMarks } from "./AnswerBody";

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

  it("numbers sources in list order and marks claims no source backs", async () => {
    const onCitation = vi.fn();
    expect(citationNumbers([{}, {}, {}])).toEqual([1, 2, 3]);
    render(<AnswerBody content="Sourced [2]. Own knowledge [?]." numbers={[1, 2]} onCitation={onCitation} />);
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 2" }));
    expect(onCitation).toHaveBeenCalledWith(1);
    expect(screen.getByText("unverified")).toBeInTheDocument();
  });

  it("hides numbers and marks while an answer is still being written", () => {
    expect(withoutMarks("A [4] and B [1, 3]. C [?].")).toBe("A and B. C.");
    expect(withoutMarks("A [i:1] [i:2] and B [p:3] C [d:2].")).toBe("A and B C.");
  });

  it("shows a run of inferences as one label naming its sources", async () => {
    const onCitation = vi.fn();
    expect(withCitationLinks("So [i:1] [i:2] it is. [i:7] stays.", [1, 2])).toBe("So [inference](#infer-1-2) it is. [i:7] stays.");
    render(<AnswerBody content="Likely so [i:1][i:2]." numbers={[1, 2]} onCitation={onCitation} />);
    expect(screen.getAllByText(/inference/)).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 2" }));
    expect(onCitation).toHaveBeenCalledWith(1);
    expect(screen.getByRole("button", { name: "Inspect citation 1" })).toBeInTheDocument();
  });

  it("shows a run of disputes as one label naming the sources that disagree", async () => {
    const onCitation = vi.fn();
    expect(withCitationLinks("So [d:1] [d:2] it is. [d:7] stays.", [1, 2])).toBe("So [disputed](#disputed-1-2) it is. [d:7] stays.");
    render(<AnswerBody content="Boils at 100 C [d:1][d:2]." numbers={[1, 2]} onCitation={onCitation} />);
    const label = screen.getByText(/disputed ·/);
    expect(label).toHaveClass("disputed-mark");
    expect(label).toHaveAttribute("title", "Sources disagree with this statement from the model's own knowledge");
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 2" }));
    expect(onCitation).toHaveBeenCalledWith(1);
  });

  it("shows a partly supported claim as a hollow number with what the source leaves out", () => {
    expect(withCitationLinks("A [p:2]. B [p:1].", [1, 2])).toBe("A [2](#partly-2-0). B [1](#partly-1-1).");
    render(<AnswerBody content="A [p:2]. B [p:1]." numbers={[1, 2]} onCitation={vi.fn()} supportNotes={["no date"]} />);
    const [first, second] = screen.getAllByRole("button");
    expect(first).toHaveClass("is-partly");
    expect(first).toHaveAttribute("title", "Partly supported: no date");
    expect(second).toHaveAttribute("title", "Partly supported");
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

  it("shows a source being read further with a reading icon", () => {
    const { container } = render(<AgentSteps steps={[
      { tool: "read_source", label: "Read more of “Trial report”", query: "1", found: 1, error: null },
    ]} />);
    expect(screen.getByText("Read more of “Trial report”")).toBeInTheDocument();
    expect(container.querySelector("svg.lucide-book-open")).not.toBeNull();
  });

  it("shows the method check and the revision without counting results", () => {
    const { container } = render(<AgentSteps steps={[
      { tool: "check", label: "Checked the method", query: "", found: 2, error: null },
      { tool: "revise", label: "Revised to follow the method", query: "", found: 1, error: null },
      { tool: "revise", label: "Revised to follow the method", query: "", found: 0, error: "nothing came back" },
    ]} />);
    expect(screen.getByText("Checked the method")).toBeInTheDocument();
    expect(screen.queryByText(/results?$/)).not.toBeInTheDocument();
    expect(screen.getByText("nothing came back")).toBeInTheDocument();
    expect(container.querySelector("svg.lucide-clipboard-check")).not.toBeNull();
    expect(container.querySelector("svg.lucide-pen-line")).not.toBeNull();
  });

  it("shows nothing when the agent searched nothing", () => {
    const { container } = render(<AgentSteps steps={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
