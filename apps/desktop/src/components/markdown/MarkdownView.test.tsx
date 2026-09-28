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

  it("shows read-only tasks without toggles and an empty placeholder for blank text", () => {
    const { rerender } = render(<MarkdownView source={"- [x] done"} />);
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByRole("img", { name: "Done" })).toBeInTheDocument();
    rerender(<MarkdownView source={"   "} empty={<p>Nothing yet</p>} />);
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
  });
});
