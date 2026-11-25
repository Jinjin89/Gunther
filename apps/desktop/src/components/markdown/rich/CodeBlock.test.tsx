import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ThemedToken } from "shiki/types";
import { CodeBlock } from "./CodeBlock";
import { highlightCode } from "./highlightCode";

vi.mock("./highlightCode", () => ({ highlightCode: vi.fn() }));

const lines = Array.from({ length: 26 }, (_, index) => `line ${index + 1}`);
const longCode = lines.join("\r\n");

beforeEach(() => {
  vi.mocked(highlightCode).mockResolvedValue(null);
});

describe("CodeBlock", () => {
  it("labels an unknown language and copies the full code while folded", async () => {
    const user = userEvent.setup();
    const { container } = render(<CodeBlock code={longCode} language="Unknown Lang" closed />);
    expect(screen.getByText("Unknown Lang")).toBeVisible();
    expect(container.querySelector("code")?.textContent).toBe(lines.slice(0, 20).join("\r\n"));
    await user.click(screen.getByRole("button", { name: "Copy code" }));
    expect(await navigator.clipboard.readText()).toBe(longCode);
    expect(screen.getByRole("button", { name: "Copied" })).toBeVisible();
  });

  it("expands and folds a long block with one control", async () => {
    const user = userEvent.setup();
    const { container } = render(<CodeBlock code={longCode} language={null} closed />);
    const button = screen.getByRole("button", { name: "Show all 26 lines" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await user.click(button);
    expect(screen.getByRole("button", { name: "Show fewer lines" })).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("code")?.textContent).toBe(longCode);
    await user.click(button);
    expect(container.querySelector("code")?.textContent).toBe(lines.slice(0, 20).join("\r\n"));
  });

  it("keeps an open block plain and expanded and folds it on closing", () => {
    const { container, rerender } = render(<CodeBlock code={longCode} language="python" closed={false} />);
    expect(container.querySelector("code")?.textContent).toBe(longCode);
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
    expect(highlightCode).not.toHaveBeenCalled();
    rerender(<CodeBlock code={longCode} language="python" closed />);
    expect(screen.getByRole("button", { name: "Show all 26 lines" })).toBeVisible();
  });

  it("renders both theme colors as token styles while preserving the text", async () => {
    const code = "arr[1]\r\n\r\nreturn arr";
    const colors = { "--shiki-light": "#111111", "--shiki-dark": "#eeeeee" };
    const tokens: ThemedToken[][] = [
      [{ content: "arr", offset: 0, htmlStyle: colors }, { content: "[1]", offset: 3, htmlStyle: colors }],
      [],
      [{ content: "return arr", offset: 10, htmlStyle: colors }],
    ];
    let resolve!: (value: ThemedToken[][]) => void;
    vi.mocked(highlightCode).mockReturnValue(new Promise((done) => { resolve = done; }));
    const { container } = render(<CodeBlock code={code} language="py" closed />);
    const element = container.querySelector("code");
    expect(element?.textContent).toBe(code);
    expect(element?.querySelector("span")).toBeNull();
    await act(async () => { resolve(tokens); });
    expect(highlightCode).toHaveBeenCalledWith(code, "py");
    expect(element?.textContent).toBe(code);
    const token = element?.querySelector("span");
    expect(token?.style.getPropertyValue("--shiki-light")).toBe("#111111");
    expect(token?.style.getPropertyValue("--shiki-dark")).toBe("#eeeeee");
  });
});
