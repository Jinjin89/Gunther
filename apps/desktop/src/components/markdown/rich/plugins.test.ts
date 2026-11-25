import { fromMarkdown } from "mdast-util-from-markdown";
import { describe, expect, it } from "vitest";
import { mapMarkdown } from "./mapMarkdown";
import { remarkFenceState } from "./plugins";

describe("remarkFenceState", () => {
  it.each([
    ["```python\ncode\n```\n", true],
    ["~~~lang extra\r\ncode\r\n~~~~   ", true],
    ["```\n```", true],
    ["```python", false],
    ["```python\ncode\n", false],
    ["````\ncode\n```", false],
    ["```\ncode\n~~~", false],
    ["~~~\ncode\n~~~ extra", false],
    ["~~~\ncode\n    ~~~", false],
    ["123. ~~~\n     code\n      ~~~", true],
    ["> - ```\n>   code\n>   ```", true],
    ["> - ```\n>   code\n>       ```", false],
    ["    indented code\n    ```", undefined],
  ])("marks the fence in %j as closed=%s", (value, closed) => {
    const tree = fromMarkdown(value);
    remarkFenceState()(tree, { value });
    const found: unknown[] = [];
    mapMarkdown(tree, (node) => {
      if (node.type === "code") found.push(node.data?.hProperties?.dataFenceClosed);
    });
    expect(found).toEqual([closed]);
  });
});
