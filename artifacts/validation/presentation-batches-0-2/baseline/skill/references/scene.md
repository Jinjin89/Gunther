# Composed scene contract

The page text is already written and fact-checked. The numbered blocks below are the only page
content available to this step. Return a JSON object with a root row/column tree. A leaf names a
block by its id and chooses one of that block's supplied `allowedViews`; never repeat, omit,
reorder the text within, or copy any block into the response.

Containers may be nested up to the program's limit. Each row or column has 2–4 children and one
weight from 1 to 4 per child. The weights set relative space. Pick gap ("tight", "normal", "wide"),
padding ("none", "small", "medium"), surface ("none", "soft", "focus", "outline") and alignment
("start", "center", "stretch") only when they help the existing content.

Budget the fixed 1280x720 canvas: after its heading, the body area is about 1000x460. Leaf text
sizes are approximately 25, 32, 42 and 68 pixels for small, medium, large and display. A display
stat digit is about 1.55 times the selected size. Nested weights multiply: a one-third child inside
a one-third parent gets about one ninth of the width. Put long prose in wider branches and use
small or medium type when a block receives a narrow share. Stay within 24 content blocks, 40 scene
nodes and nesting depth 4.

Leaves use kind "leaf", blockId, and view. The supplied block's `allowedViews` lists the
choices this page's exact content permits; choose only one of them. Text, stat, list, table,
quote and image views preserve the matching source form. A numbered list may offer `flow` only
when it already contains 2–6 ordered steps. A table may offer `diagram` only when its three
columns form a valid cited directed acyclic graph; it may offer `bar_chart` or `line_chart` only
when it has exactly two columns, 2–6 data rows, numeric values and one consistent unit. Graphic
leaves need enough width and height for every label, value and citation. Keep their local text at
the renderer's readable size; do not squeeze a graph into a narrow or shallow branch. Never
ignore an extra column, reorder a flow, or infer a graph/chart from a block type alone. A page
may use at most three graphic leaves. The code independently checks each selected graph against
the cited evidence; an unlicensed view is shown as the complete original list or table.

Size is "small", "medium",
"large" or "display"; alignment is "start", "center", "end" or "stretch"; role is "main", "support"
or "focus". These controls affect visual presentation only. Do not add labels, icons, facts, claims,
citations, or text.

Example response shape:

~~~json
{
  "root": {
    "kind": "row",
    "weights": [2, 1],
    "gap": "wide",
    "padding": "small",
    "surface": "none",
    "align": "center",
    "children": [
      {
        "kind": "leaf",
        "blockId": "b1",
        "view": "stat",
        "size": "display",
        "align": "center",
        "role": "focus"
      },
      {
        "kind": "column",
        "weights": [1, 1],
        "gap": "normal",
        "padding": "small",
        "surface": "soft",
        "align": "stretch",
        "children": [
          { "kind": "leaf", "blockId": "b2", "view": "text", "size": "medium", "align": "start", "role": "support" },
          { "kind": "leaf", "blockId": "b3", "view": "image", "size": "large", "align": "center", "role": "main" }
        ]
      }
    ]
  }
}
~~~

Return the tree only. Keep equal items visually equal, preserve order and support text, and use the
actual content's relationship to choose row/column nesting. A scene that is invalid or too crowded
is rejected; do not solve it by dropping a block.
