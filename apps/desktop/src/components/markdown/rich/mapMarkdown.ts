import type { Parent, RootContent } from "mdast";

type Transform = (node: RootContent, parent: Parent) => RootContent | RootContent[] | void;

export function mapMarkdown(parent: Parent, transform: Transform): void {
  parent.children = parent.children.flatMap((node) => {
    const replacement = transform(node, parent);
    if (replacement) return replacement;
    if ("children" in node) mapMarkdown(node, transform);
    return node;
  });
}
