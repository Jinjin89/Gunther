import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import mermaid from "mermaid";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnswerBody } from "../../../pages/AnswerBody";
import { saveBlob } from "../../../saveBlob";
import { hasSingleSvgRoot, sanitizeSvg } from "./svg";

const maxTextSize = vi.hoisted(() => 100);
vi.mock("mermaid", () => ({ default: {
  initialize: vi.fn(),
  render: vi.fn(),
  mermaidAPI: { defaultConfig: { maxTextSize, secure: ["securityLevel", "maxTextSize"] } },
} }));
vi.mock("../../../saveBlob", () => ({ saveBlob: vi.fn() }));
vi.mock("./highlightCode", () => ({ highlightCode: vi.fn().mockResolvedValue(null) }));

const svg = `<svg xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 80 40" onload="run()">
  <script>run()</script>
  <foreignObject><p>Embedded HTML</p></foreignObject>
  <rect id="shape" width="80" height="40" onclick="run()"/>
  <a href="#shape" xlink:href="#shape"><text>Shape</text></a>
  <a href="https://example.org" xlink:href="data:image/png;base64,AAAA"><text>External</text></a>
</svg>`;
const flow = "flowchart LR\n  A --> B";
const result = { svg, diagramType: "flowchart" };
const fence = (language: string, source: string, closed = true) => `\`\`\`${language}\n${source}${closed ? "\n```" : ""}`;
const createUrl = vi.fn<(blob: Blob) => string>();
const revokeUrl = vi.fn<(url: string) => void>();

beforeEach(() => {
  vi.mocked(mermaid.initialize).mockClear();
  vi.mocked(mermaid.render).mockReset().mockResolvedValue(result);
  vi.mocked(saveBlob).mockClear();
  revokeUrl.mockClear();
  createUrl.mockReset().mockImplementation(() => `blob:diagram-${createUrl.mock.calls.length}`);
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = createUrl;
    static revokeObjectURL = revokeUrl;
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("SVG picture cards", () => {
  it("preserves diagram geometry and fragment links while removing executable elements and external links", () => {
    const root = new DOMParser().parseFromString(sanitizeSvg(svg), "image/svg+xml").documentElement;
    expect(root.localName).toBe("svg");
    expect(root.namespaceURI).toBe("http://www.w3.org/2000/svg");
    expect(root.getAttribute("viewBox")).toBe("0 0 80 40");
    expect(root.querySelector("script, foreignObject, [onload], [onclick]")).toBeNull();
    const [local, external] = root.querySelectorAll("a");
    expect(local?.getAttribute("href")).toBe("#shape");
    expect(local?.getAttribute("xlink:href")).toBe("#shape");
    expect(external?.getAttribute("href")).toBeNull();
    expect(external?.getAttribute("xlink:href")).toBeNull();
  });

  it("removes stylesheet, style and presentation resources while preserving local paint and markers", () => {
    const clean = sanitizeSvg(String.raw`<svg viewBox="0 0 80 40">
      <defs><linearGradient id="paint"/><marker id="tip"/></defs>
      <style>
        @\69mport "https://assets.example.org/chart.css";
        @font-face { font-family: Chart; src: url(font.woff2); }
        .line { fill: url('#paint'); filter: u\72l(https://assets.example.org/filters.svg#blur); }
        .icon { background: image-set("//assets.example.org/icon.png" 1x); }
      </style>
      <path d="M0 0 H80" fill="url(#paint)" marker-end="url(#tip)"
        style="stroke: url(&quot;#paint&quot;); mask: url('../mask.svg#mask')" filter="url(./filters.svg#blur)"/>
    </svg>`);
    expect(clean).not.toMatch(/assets\.example\.org|font\.woff2|mask\.svg|filters\.svg/);
    const root = new DOMParser().parseFromString(clean, "image/svg+xml").documentElement;
    expect(root.querySelector("style")?.textContent).toContain("fill: url('#paint')");
    const path = root.querySelector("path");
    expect(path?.getAttribute("fill")).toBe("url(#paint)");
    expect(path?.getAttribute("marker-end")).toBe("url(#tip)");
    expect(path?.getAttribute("style")).toContain('stroke: url("#paint")');
    expect(path?.getAttribute("filter")).toBeNull();
  });

  it("shows the source and validation error in the picture card for an unsupported SVG root", () => {
    const source = `<v:svg xmlns:v="http://www.w3.org/2000/svg"><v:rect width="80" height="40"/></v:svg>`;
    expect(hasSingleSvgRoot(source)).toBe(false);
    const { container } = render(<AnswerBody content={fence("svg", source)} numbers={[]} />);
    expect(container.querySelector(".gx-md-picture pre code")?.textContent).toBe(source);
    expect(screen.getByRole("status")).toHaveTextContent(/^Couldn't draw this picture: Expected a single SVG element\.$/);
    expect(screen.getByRole("button", { name: "Code" })).toHaveAttribute("aria-pressed", "true");
    expect(createUrl).not.toHaveBeenCalled();
  });

  it("opens a legacy SVG in Picture, copies source, saves the preview blob and reuses the lightbox", async () => {
    const user = userEvent.setup();
    const onAnswerClick = vi.fn();
    const { container } = render(<article onClick={onAnswerClick}><AnswerBody content={`A diagram\n\n${svg}`} numbers={[]} /></article>);
    const picture = await screen.findByRole("img", { name: "SVG diagram" });
    expect(picture).toHaveAttribute("src", "blob:diagram-1");
    expect(container.querySelector("svg[viewBox='0 0 80 40']")).toBeNull();
    expect(screen.getByRole("button", { name: "Picture" })).toHaveAttribute("aria-pressed", "true");
    const blob = createUrl.mock.calls[0]![0];
    expect(blob.type).toBe("image/svg+xml");
    const text = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsText(blob);
    });
    expect(text).toBe(sanitizeSvg(svg));

    await user.click(screen.getByRole("button", { name: "Code" }));
    expect(screen.queryByRole("img", { name: "SVG diagram" })).toBeNull();
    expect(container.querySelector("pre code")?.textContent).toBe(svg);
    const copy = screen.getByRole("button", { name: "Copy source" });
    expect(copy).toHaveAttribute("title", "Copy source");
    await user.click(copy);
    expect(await navigator.clipboard.readText()).toBe(svg);
    await user.click(screen.getByRole("button", { name: "Picture" }));
    expect(picture.getAttribute("src")).toBe(screen.getByRole("img", { name: "SVG diagram" }).getAttribute("src"));
    const save = screen.getByRole("button", { name: "Save SVG" });
    expect(save).toHaveAttribute("title", "Save SVG");
    await user.click(save);
    expect(saveBlob).toHaveBeenCalledExactlyOnceWith(blob, "diagram.svg");

    const expand = screen.getByRole("button", { name: "Expand" });
    expect(expand).toHaveAttribute("title", "Expand");
    await user.click(expand);
    const lightbox = screen.getByRole("dialog", { name: "SVG diagram" });
    expect(lightbox.parentElement).toBe(document.body);
    expect(within(lightbox).getByRole("img")).toHaveAttribute("src", "blob:diagram-1");
    expect(within(lightbox).getByRole("link", { name: "Download original" })).toHaveAttribute("download", "diagram.svg");
    expect(within(lightbox).getByRole("button", { name: "Close" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(expand).toHaveFocus();
    expect(onAnswerClick).not.toHaveBeenCalled();
  });

  it("releases the SVG blob when its source changes and on unmount", async () => {
    const { rerender, unmount } = render(<AnswerBody content={fence("svg", svg)} numbers={[]} />);
    await screen.findByRole("img", { name: "SVG diagram" });
    rerender(<AnswerBody content={fence("svg", svg.replace("External", "Revised"))} numbers={[]} />);
    expect(await screen.findByRole("img", { name: "SVG diagram" })).toHaveAttribute("src", "blob:diagram-2");
    expect(revokeUrl).toHaveBeenCalledExactlyOnceWith("blob:diagram-1");
    unmount();
    expect(revokeUrl.mock.calls).toEqual([["blob:diagram-1"], ["blob:diagram-2"]]);
  });
});

describe("Mermaid picture cards", () => {
  it("shows streaming source with a drawing status and renders when the fence closes", async () => {
    const { container, rerender } = render(<AnswerBody content={fence("mermaid", flow, false)} numbers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("Drawing…");
    expect(container.querySelector("pre code")?.textContent).toBe(flow);
    expect(mermaid.render).not.toHaveBeenCalled();
    rerender(<AnswerBody content={fence("mermaid", flow)} numbers={[]} />);
    expect(await screen.findByRole("img", { name: "Mermaid diagram" })).toHaveAttribute("src", "blob:diagram-1");
    expect(screen.queryByRole("status")).toBeNull();
    expect(mermaid.initialize).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      startOnLoad: false, securityLevel: "strict", htmlLabels: false,
      flowchart: { htmlLabels: false, useMaxWidth: false }, theme: "neutral", themeVariables: { dropShadow: "none" },
      secure: expect.arrayContaining(["securityLevel", "maxTextSize", "htmlLabels"]),
    }));
  });

  it("serializes independent diagrams and keeps drawing after a failure", async () => {
    let fail!: (error: Error) => void;
    vi.mocked(mermaid.render).mockReturnValueOnce(new Promise((_, reject) => { fail = reject; }));
    const badFlow = "flowchart LR\n  A --> [";
    const { container } = render(<AnswerBody content={`${fence("mermaid", badFlow)}\n\n${fence("mermaid", flow)}`} numbers={[]} />);
    await waitFor(() => expect(mermaid.render).toHaveBeenCalledTimes(1));
    expect(mermaid.initialize).toHaveBeenCalledTimes(1);
    await act(async () => { fail(new Error("Parse error\nAdditional detail")); });
    expect(await screen.findByRole("img", { name: "Mermaid diagram" })).toHaveAttribute("src", "blob:diagram-1");
    expect(screen.getByRole("status")).toHaveTextContent(/^Couldn't draw this diagram: Parse error$/);
    expect(container.querySelector("pre code")?.textContent).toBe(badFlow);
    const [first, second] = vi.mocked(mermaid.render).mock.calls;
    expect(first?.[0]).not.toBe(second?.[0]);
    expect(first?.[2]?.isConnected).toBe(false);
    expect(second?.[2]?.isConnected).toBe(false);
  });

  it("shows source and the library limit for oversized Mermaid", async () => {
    const source = `${flow}${"B".repeat(maxTextSize)}`;
    const { container } = render(<AnswerBody content={fence("mermaid", source)} numbers={[]} />);
    expect(await screen.findByText(`Couldn't draw this diagram: Mermaid diagrams are limited to ${maxTextSize} characters.`)).toBeVisible();
    expect(container.querySelector("pre code")?.textContent).toBe(source);
    expect(screen.getByRole("button", { name: "Save SVG" })).toBeDisabled();
    expect(mermaid.render).not.toHaveBeenCalled();
  });
});
