import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { CitationDetails } from "../components/evidence/CitationButton";
import { MarkdownView } from "../components/markdown/MarkdownView";
import { AgentSteps, AnswerBody, citationNumbers, LiveAnswer } from "./AnswerBody";

describe("AnswerBody", () => {
  it("links only citations that exist", () => {
    render(<AnswerBody content="A [4] and B [1]." numbers={[4, 6]} onCitation={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Inspect citation 4" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Inspect citation 1" })).toBeNull();
    expect(screen.getByText(/B \[1\]/)).toBeVisible();
  });

  it("renders Markdown with citation buttons that open the evidence", async () => {
    const onCitation = vi.fn();
    render(<AnswerBody content={"**CD3D** marks T cells [1].\n\n- one\n- two"} numbers={[1]} onCitation={onCitation} />);
    expect(screen.getByText("CD3D").tagName).toBe("STRONG");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 1" }));
    expect(onCitation).toHaveBeenCalledOnce();
  });

  it("keeps punctuation adjacent to its citation outside the clickable control", () => {
    const { container } = render(<AnswerBody content="Field use [1] . 另一项 [p:2]。” The literal `[1] .` is kept." numbers={[1, 2]} onCitation={vi.fn()} />);
    const first = screen.getByRole("button", { name: "Inspect citation 1" });
    const second = screen.getByRole("button", { name: "Inspect citation 2" });
    expect(first.closest(".gx-citation-tail")?.textContent).toBe("[1].");
    expect(second.closest(".gx-citation-tail")?.textContent).toBe("[2]。”");
    expect(second).toHaveClass("is-partly");
    expect(container.querySelector("code")?.textContent).toBe("[1] .");
  });

  it.each(["reader", "paper"])("preserves separators after citation marks in the %s", (mode) => {
    const content = 'A [1] / B [2] "Quoted text" [1] . He said "field use [2]".';
    const { container } = render(<AnswerBody content={content} numbers={[1, 2]} onCitation={mode === "reader" ? vi.fn() : undefined} />);
    expect(container.querySelector("p")?.textContent).toBe(content.replace("[1] .", "[1]."));
    expect([...container.querySelectorAll(".gx-citation-tail")].map((tail) => tail.textContent)).toEqual(["[1].", '[2]".']);
  });

  it("keeps a focused citation clickable and uses the latest handler after its parent updates", async () => {
    const before = vi.fn();
    const after = vi.fn();
    function Reader() {
      const [focused, setFocused] = useState(false);
      return <div onFocus={() => setFocused(true)}><AnswerBody content="A finding [1]." numbers={[1]} onCitation={focused ? after : before} /></div>;
    }
    render(<Reader />);
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 1" }));
    expect(before).not.toHaveBeenCalled();
    expect(after).toHaveBeenCalledExactlyOnceWith(0);
  });

  it("previews a clipped citation on hover or focus and opens the evidence on activation", async () => {
    const user = userEvent.setup();
    const onCitation = vi.fn();
    const quote = "Rain continued through the afternoon. ".repeat(12);
    render(<CitationDetails.Provider value={[{
      id: "citation", sourceId: "notes", sourceTitle: "Field notes", assertionId: null,
      quote, locator: "Page 4", status: "verified", confidence: 1,
    }]}>
      <AnswerBody content="The rain continued [1]." numbers={[1]} onCitation={onCitation} />
    </CitationDetails.Provider>);
    const button = screen.getByRole("button", { name: "Inspect citation 1" });
    await user.hover(button);
    const tip = within(screen.getByRole("tooltip"));
    expect(tip.getByText("Field notes")).toBeVisible();
    expect(tip.getByText("Page 4")).toBeVisible();
    expect(tip.getByText(/…$/)).toHaveTextContent(quote.slice(0, 240).trimEnd());
    await user.unhover(button);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await user.tab();
    expect(button).toHaveFocus();
    expect(screen.getByRole("tooltip")).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await user.click(button);
    expect(onCitation).toHaveBeenCalledExactlyOnceWith(0);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("numbers sources in list order and marks claims no source backs", async () => {
    const onCitation = vi.fn();
    expect(citationNumbers([{}, {}, {}])).toEqual([1, 2, 3]);
    render(<AnswerBody content="Sourced [2]. Own knowledge [?]." numbers={[1, 2]} onCitation={onCitation} />);
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 2" }));
    expect(onCitation).toHaveBeenCalledWith(1);
    expect(screen.getByText("unverified")).toBeInTheDocument();
  });

  it("leaves citation syntax and spacing in fenced code, inline code and math", async () => {
    const code = "arr[1]  =  arr[7]\n[?]";
    const content = `A result [1] uses \`arr[1]\` and $$x[1]$$.\n\n~~~python\n${code}\n~~~\n\n$$\nx[1] + y[?]\n\n$$`;
    const { container } = render(<AnswerBody content={content} numbers={[1]} onCitation={vi.fn()} />);
    expect(screen.getAllByRole("button", { name: "Inspect citation 1" })).toHaveLength(1);
    expect(screen.getByText("arr[1]", { selector: "code" })).toBeVisible();
    expect(container.querySelector("pre code")?.textContent).toBe(code);
    await waitFor(() => expect([...container.querySelectorAll(".katex annotation")].map((node) => node.textContent)).toEqual(["x[1]", "x[1] + y[?]\n"]));
    expect(screen.queryByText("unverified")).toBeNull();
  });

  it("typesets inline math, a display formula and a math fence", async () => {
    const content = "Inline $$x^2$$.\n\n$$\nx^2\n$$\n\n```math\nx^2\n```";
    const { container } = render(<AnswerBody content={content} numbers={[]} />);
    await waitFor(() => expect(container.querySelectorAll(".katex")).toHaveLength(3));
    expect(container.querySelector("p .katex .katex-mathml")).toBeInTheDocument();
    expect(container.querySelectorAll(".gx-md-math-display .katex-display")).toHaveLength(2);
    expect([...container.querySelectorAll(".katex annotation")].map((node) => node.textContent)).toEqual(["x^2", "x^2", "x^2"]);
  });

  it("shows the original TeX and KaTeX's error for a malformed formula", async () => {
    const tex = String.raw`\frac{x}{`;
    render(<AnswerBody content={`An incomplete formula $$${tex}$$.`} numbers={[]} />);
    const error = await screen.findByTitle(/KaTeX parse error: Unexpected end of input/);
    expect(error).toHaveClass("katex-error");
    expect(error.textContent).toBe(tex);
  });

  it("looks at the original spelling of citations in continued Markdown text", () => {
    const content = [String.raw`> Literal \[1\], \[1], [1\], &#91;1&#93;, \[?\].`, String.raw`>    Actual [1] and \\[2].`].join("\r\n");
    const { container } = render(<AnswerBody content={content} numbers={[1, 2]} onCitation={vi.fn()} />);
    expect(container.querySelector("blockquote")?.textContent).toContain("Literal [1], [1], [1], [1], [?].");
    expect(screen.getAllByRole("button", { name: "Inspect citation 1" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Inspect citation 2" })).toHaveLength(1);
    expect(screen.queryByText("unverified")).toBeNull();
  });

  it("renders a citation after a decoded autolink and keeps a positioned escaped literal", () => {
    const content = String.raw`See https&#58;//example.org and [1]. Literal **\[2\]**.`;
    render(<AnswerBody content={content} numbers={[1, 2]} onCitation={vi.fn()} />);
    expect(screen.getByRole("link", { name: "https://example.org" })).toHaveAttribute("href", "https://example.org");
    expect(screen.getByRole("button", { name: "Inspect citation 1" })).toBeVisible();
    expect(screen.getByText("[2]", { selector: "strong" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Inspect citation 2" })).toBeNull();
  });

  it("keeps a long BOM-prefixed open fence unfolded", () => {
    const body = `${"line\n".repeat(26)}last line`;
    const { container } = render(<AnswerBody content={`\uFEFF~~~\n${body}`} numbers={[]} />);
    expect(container.querySelector("pre code")?.textContent).toBe(body);
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
  });

  it("hides numbers and marks while an answer is still being written", () => {
    const code = `${"arr[1]\n".repeat(26)}arr[7]`;
    const { container } = render(<LiveAnswer state={{ steps: [], text: `A [4] and B [1, 3]. C [?].\n\nD [i:1] [i:2] and E [p:3] F [d:2].\n\n\`\`\`python\n${code}` }} />);
    expect(screen.getByText("A and B. C.")).toBeVisible();
    expect(screen.getByText("D and E F.")).toBeVisible();
    expect(container.querySelector("pre code")?.textContent).toBe(code);
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
  });

  it("shows a run of inferences as one label naming its sources", async () => {
    const onCitation = vi.fn();
    render(<AnswerBody content="Likely so [i:1] [i:2][i:1]. [i:7] stays." numbers={[1, 2]} onCitation={onCitation} />);
    expect(screen.getAllByText(/inference/)).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 2" }));
    expect(onCitation).toHaveBeenCalledWith(1);
    expect(screen.getByRole("button", { name: "Inspect citation 1" })).toBeInTheDocument();
    expect(screen.getByText(/\[i:7\] stays/)).toBeVisible();
  });

  it("shows a run of disputes as one label naming the sources that disagree", async () => {
    const onCitation = vi.fn();
    render(<AnswerBody content="Boils at 100 C [d:1][d:2]. [d:7] stays." numbers={[1, 2]} onCitation={onCitation} />);
    const label = screen.getByText(/disputed ·/);
    expect(label).toHaveClass("disputed-mark");
    expect(label).toHaveAttribute("title", "Sources disagree with this statement from the model's own knowledge");
    await userEvent.click(screen.getByRole("button", { name: "Inspect citation 2" }));
    expect(onCitation).toHaveBeenCalledWith(1);
    expect(screen.getByText(/\[d:7\] stays/)).toBeVisible();
  });

  it("shows a partly supported claim as a hollow number with what the source leaves out", () => {
    render(<AnswerBody content={"A `[p:2]` $$[p:2]$$ **[p:2]**. B [p:1]."} numbers={[1, 2]} onCitation={vi.fn()} supportNotes={["no date"]} />);
    const [first, second] = screen.getAllByRole("button", { name: /Inspect citation/ });
    expect(first).toHaveClass("is-partly");
    expect(first).toHaveAttribute("title", "Partly supported: no date");
    expect(second).toHaveAttribute("title", "Partly supported");
  });

  it("shows a link chip for a remote image without loading it and blocks unsafe links", () => {
    render(<AnswerBody content={"![x](https://tracker.example/p.png) [site](https://example.org) [bad](javascript:alert(1))"} numbers={[]} />);
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: "x" })).toHaveClass("gx-md-image-link");
    expect(screen.getByRole("link", { name: "x" })).toHaveAttribute("href", "https://tracker.example/p.png");
    expect(screen.getByRole("link", { name: "site" })).toHaveAttribute("href", "https://example.org");
    expect(screen.queryByRole("link", { name: "bad" })).toBeNull();
  });

  it("uses revocable blob URLs for supported embedded images in answers and notes", () => {
    const createObjectURL = vi.fn().mockReturnValueOnce("blob:answer").mockReturnValueOnce("blob:note");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    });
    try {
      const { unmount } = render(<>
        <AnswerBody content="![Answer figure](data:image/png;base64,iVBORw0KGgo=)" numbers={[]} />
        <MarkdownView source="![Note figure](data:image/gif,GIF89a%00%01) ![Unsupported](data:image/svg+xml;base64,PHN2Zy8+)" />
      </>);
      expect(screen.getByRole("img", { name: "Answer figure" })).toHaveAttribute("src", "blob:answer");
      expect(screen.getByRole("img", { name: "Note figure" })).toHaveAttribute("src", "blob:note");
      expect(screen.queryByRole("img", { name: "Unsupported" })).toBeNull();
      expect(createObjectURL.mock.calls.map(([blob]) => [blob.type, blob.size])).toEqual([["image/png", 8], ["image/gif", 8]]);
      unmount();
      expect(revokeObjectURL.mock.calls).toEqual([["blob:answer"], ["blob:note"]]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("keeps Markdown link labels and ordinary fragment links intact", () => {
    render(<AnswerBody content="[Details [1]](https://example.org) [Anchor](#cite-1) and [1]." numbers={[1]} onCitation={vi.fn()} />);
    expect(screen.getByRole("link", { name: "Details [1]" })).toHaveAttribute("href", "https://example.org");
    expect(screen.getByRole("link", { name: "Anchor" })).toHaveAttribute("href", "#cite-1");
    expect(screen.getAllByRole("button", { name: "Inspect citation 1" })).toHaveLength(1);
  });

  it("uses the authored link target when it wraps a remote image", () => {
    render(<AnswerBody content="[![Chart](https://example.org/chart.png)](https://example.org/study)" numbers={[]} />);
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Chart" })).toHaveAttribute("href", "https://example.org/study");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("renders single dollar signs as currency", () => {
    render(<AnswerBody content="It costs $5 and $10." numbers={[]} />);
    expect(screen.getByText("It costs $5 and $10.")).toBeVisible();
    expect(document.querySelector("code")).toBeNull();
  });

  it("renders only managed local figures while retaining the source citation", () => {
    render(<AnswerBody content={"![Study figure](gunther-asset:ast_plot) [1].\n\n![bad](gunther-asset:../secret)\n\n![remote](https://example.org/tracker.png)"} numbers={[1]} onCitation={vi.fn()} />);
    expect(screen.getByRole("img", { name: "Study figure" })).toHaveAttribute("src", expect.stringContaining("/api/assets/ast_plot"));
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Inspect citation 1" })).toBeVisible();
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
