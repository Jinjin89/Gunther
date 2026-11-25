import { mapMarkdown } from "./mapMarkdown";
import { parseRichMarkdown, sourceForPositions } from "./plugins";

const HTML_COMMENT = /<!--[\s\S]*?-->/g;
type Range = { start: number; end: number };

export function noteSource(source: string) {
  let visible = source;
  const passes: Range[][] = [];
  for (;;) {
    const tree = parseRichMarkdown(visible);
    const bom = visible.length - sourceForPositions(visible).length;
    const removed: Range[] = [];
    let exposesMarkdown = false;
    mapMarkdown(tree, (node) => {
      if (exposesMarkdown) return node;
      if (node.type !== "html") return;
      const start = node.position?.start.offset;
      const end = node.position?.end.offset;
      if (start === undefined || end === undefined) return;
      const html = visible.slice(start + bom, end + bom);
      for (const comment of html.matchAll(HTML_COMMENT)) {
        const at = comment.index;
        const after = at + comment[0].length;
        removed.push({ start: start + bom + at, end: start + bom + after });
        if (!html.slice(0, at).trim() && html.slice(after).trim()) {
          // Reparse before stripping later comments. This comment can hide a fence or list opener.
          exposesMarkdown = true;
          break;
        }
      }
    });
    if (!removed.length) return {
      visible,
      empty: !tree.children.some((node) => node.type !== "definition" && node.type !== "footnoteDefinition"),
      toOriginal(offset: number) {
        let original = offset + bom;
        for (let index = passes.length - 1; index >= 0; index--) {
          for (const { start, end } of passes[index]!) {
            if (start > original) break;
            original += end - start;
          }
        }
        return original;
      },
    };

    let cursor = 0;
    const parts = removed.map(({ start, end }) => {
      const part = visible.slice(cursor, start);
      cursor = end;
      return part;
    });
    visible = parts.join("") + visible.slice(cursor);
    passes.push(removed);
  }
}
