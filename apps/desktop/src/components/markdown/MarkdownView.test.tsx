import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { setTaskCheckedAt } from "./markdownEditing";
import { MarkdownView } from "./MarkdownView";

describe("MarkdownView", () => {
  it("renders GFM safely: literal HTML, hidden comments, safe links and remote images", () => {
    const { container } = render(<MarkdownView source={"# Title\n\nIntro <b>bold</b> <!-- gunther:page=3 --> [bad](javascript:alert(1)) [good](https://example.com)\n\n![chart](https://tracker.example/pixel.png)\n\n| a | b |\n|:--|--:|\n| 1 | 2 |"} />);
    expect(screen.getByRole("heading", { level: 2, name: "Title" })).toHaveClass("gx-md-h1");
    expect(container.textContent).toContain("<b>bold</b>");
    expect(container.textContent).not.toContain("gunther:page");
    expect(screen.getByText("bad").closest("a")).toBeNull();
    expect(screen.getByRole("link", { name: "good" })).toHaveAttribute("href", "https://example.com");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: /chart/ })).toHaveAttribute("href", "https://tracker.example/pixel.png");
    expect(screen.getByRole("region", { name: "Table" }).querySelectorAll("td")).toHaveLength(2);
  });

  it("labels fenced code with its language and offers copy", () => {
    render(<MarkdownView source={"```python\nprint('hi')\n```"} />);
    expect(screen.getByText("python")).toBeInTheDocument();
    expect(screen.getByText("print('hi')")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
  });

  it("shows complete standalone legacy SVG as a picture with its original source available", () => {
    const svg = '<svg viewBox="0 0 10 10"><rect width="10" height="10" /></svg>';
    const { container } = render(<MarkdownView source={svg} />);
    expect(screen.getByRole("img", { name: "SVG diagram" })).toHaveAttribute("src", expect.stringMatching(/^blob:/));
    fireEvent.click(screen.getByRole("button", { name: "Code" }));
    expect(container.querySelector("pre code")?.textContent).toBe(svg);
    expect(container.querySelector('svg[viewBox="0 0 10 10"]')).toBeNull();
  });

  it.each([
    "Inline <svg /> text",
    "<svg><g></svg>",
    "<svg>\n  <rect />\n</svg>\n<svg />",
    '<svg xmlns="https://example.org" />',
  ])("does not convert %s into an SVG block", (source) => {
    const { container } = render(<MarkdownView source={source} />);
    expect(container.querySelector("pre, svg")).toBeNull();
    expect(container.textContent).toContain(source);
  });

  it("hides Markdown comments and keeps them inside code", () => {
    const { container } = render(<MarkdownView source={"<!-- hidden -->\n\n```\n<!-- literal -->\n```"} />);
    expect(container.textContent).not.toContain("hidden");
    expect(container.querySelector("code")?.textContent).toBe("<!-- literal -->");
  });

  it("parses a fence exposed by a leading comment and keeps code inside it", () => {
    const { container } = render(<MarkdownView source={"<!-- hidden -->```\n<!-- literal -->\n```"} />);
    expect(container.querySelector("pre code")?.textContent).toBe("<!-- literal -->");
    expect(container.textContent).not.toContain("hidden");
  });

  it("keeps indented literal comment code visible even when it is the entire note", () => {
    const { container } = render(<MarkdownView source="    <!-- literal -->" empty={<p>Nothing yet</p>} />);
    expect(container.querySelector("pre code")?.textContent).toBe("<!-- literal -->");
    expect(screen.queryByText("Nothing yet")).toBeNull();
  });

  it("renders retained local figures without permitting arbitrary image URLs", () => {
    render(<MarkdownView source={"![Source figure](gunther-asset:ast_figure_123)\n\n![Unsafe](gunther-asset:../../secret)"} />);
    expect(screen.getByRole("img", { name: "Source figure" })).toHaveAttribute("src", expect.stringContaining("/api/assets/ast_figure_123"));
    expect(screen.queryByRole("img", { name: "Unsafe" })).toBeNull();
  });

  it("toggles tasks at their exact source position, even after hidden comments", () => {
    const source = "<!-- gunther:page=1 -->\n- [ ] first\n- [x] second";
    const onToggle = vi.fn((offset: number, checked: boolean) => setTaskCheckedAt(source, offset, checked));
    render(<MarkdownView source={source} onToggleTask={onToggle} />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.map((box) => box.getAttribute("aria-checked"))).toEqual(["false", "true"]);
    fireEvent.click(boxes[0]!);
    expect(onToggle.mock.results[0]!.value).toBe("<!-- gunther:page=1 -->\n- [x] first\n- [x] second");
    fireEvent.click(boxes[1]!);
    expect(onToggle.mock.results[1]!.value).toBe("<!-- gunther:page=1 -->\n- [ ] first\n- [ ] second");
  });

  it("parses tasks after same-line comments and reports offsets in the original source", () => {
    const source = "\uFEFF<!-- leading --><!-- annotation -->- [ ] First\r\n- [ ] Second";
    const onToggle = vi.fn((offset: number, checked: boolean) => setTaskCheckedAt(source, offset, checked));
    render(<MarkdownView source={source} onToggleTask={onToggle} />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(2);
    fireEvent.click(boxes[0]!);
    fireEvent.click(boxes[1]!);
    expect(onToggle.mock.results.map((result) => result.value)).toEqual([
      source.replace("[ ] First", "[x] First"),
      source.replace("[ ] Second", "[x] Second"),
    ]);
  });

  it("shows read-only tasks without toggles and an empty placeholder for blank text", () => {
    const { rerender } = render(<MarkdownView source={"- [x] done"} />);
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByRole("img", { name: "Done" })).toBeInTheDocument();
    rerender(<MarkdownView source={"   "} empty={<p>Nothing yet</p>} />);
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
    rerender(<MarkdownView source={"<!-- hidden -->"} empty={<p>Nothing yet</p>} />);
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
  });
});
