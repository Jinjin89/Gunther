import type { Code, Root } from "mdast";
import type { Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkParse from "remark-parse";
import { unified } from "unified";
import { mapMarkdown } from "./mapMarkdown";

const LINE_ENDING = /\r\n?|\n/;
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

/** Micromark consumes the leading BOM before counting source offsets. */
export const sourceForPositions = (value: unknown) => String(value).replace(/^\uFEFF/, "");

function isSvg(value: string): boolean {
  if (!/^<svg(?=[\s/>])/.test(value)) return false;
  const document = new DOMParser().parseFromString(value, "image/svg+xml");
  const root = document.documentElement;
  return document.childNodes.length === 1 && root.localName === "svg"
    && (root.namespaceURI === SVG_NAMESPACE || root.namespaceURI === null);
}

function remarkLegacySvg() {
  return (tree: Root, file: { value: unknown }) => {
    const source = sourceForPositions(file.value);
    mapMarkdown(tree, (node, parent) => {
      let value: string;
      if (node.type === "html" && (parent.type === "root" || parent.type === "blockquote" || parent.type === "listItem" || parent.type === "footnoteDefinition")) {
        value = node.value.trim();
      } else if (node.type === "paragraph" && node.children[0]?.type === "html") {
        // CommonMark parses a one-line SVG with children as a paragraph of inline HTML.
        value = source.slice(node.position?.start.offset, node.position?.end.offset).trim();
      } else {
        return;
      }
      if (isSvg(value)) return { type: "code", lang: "svg", value };
    });
  };
}

function fenceClosed(node: Code, source: string): boolean | undefined {
  const { start, end } = node.position ?? {};
  if (start?.offset === undefined || end?.offset === undefined) return;
  const opening = /^(`{3,}|~{3,})/.exec(source.slice(start.offset, end.offset))?.[0];
  if (!opening) return;
  if (start.line === end.line) return false;

  const beforeEnd = end.offset - 1;
  const lineStart = Math.max(source.lastIndexOf("\n", beforeEnd), source.lastIndexOf("\r", beforeEnd)) + 1;
  const closing = /^[\t >]*(`+|~+)[\t ]*$/.exec(source.slice(lineStart, end.offset))?.[1];
  if (!closing || closing[0] !== opening[0] || closing.length < opening.length) return false;
  // The parsed body omits a real closing fence. This also rules out fence-like code
  // that is too deeply indented to close the fence within its list item or quote.
  const bodyLines = node.value ? node.value.split(LINE_ENDING).length : 0;
  return end.line - start.line > bodyLines;
}

export function remarkFenceState() {
  return (tree: Root, file: { value: unknown }) => {
    const source = sourceForPositions(file.value);
    mapMarkdown(tree, (node) => {
      if (node.type !== "code") return;
      const closed = fenceClosed(node, source);
      if (closed !== undefined) {
        node.data = { ...node.data, hProperties: { ...node.data?.hProperties, dataFenceClosed: closed } };
      }
    });
  };
}

export const richRemarkPlugins: NonNullable<Options["remarkPlugins"]> = [
  remarkGfm,
  [remarkMath, { singleDollarTextMath: false }],
  remarkLegacySvg,
  remarkFenceState,
];

const parser = unified().use(remarkParse).use(richRemarkPlugins);

export function parseRichMarkdown(source: string): Root {
  const tree = parser.parse(source);
  parser.runSync(tree, source);
  return tree;
}
