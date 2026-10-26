"""Check a visual's crop against the page pixels, and complete it when it cuts ink.

A layout parser's picture box can end inside the figure: a lower panel, the right
end of an axis, a legend or labels set outside the detected image. The page image
shows what is printed. Ink that is connected to the content inside the crop and
runs across its edge belongs to the figure, unless it is part of other text of the
page (the caption, a paragraph, a heading), which is never pulled into a picture.

The result says what was found: ``complete`` (no ink crosses the final edges),
``extended`` (connected ink was taken in) or ``clipped`` (ink still crosses an edge
that could not be moved, for instance because body text touches the figure).
"""

from __future__ import annotations

from collections import deque

import numpy as np

# The grid cell, as a fraction of the page width: ink closer than two cells is joined.
CELL = 1 / 300
JOIN = 2
# Growth beyond this share of the page in one direction is not one figure's ink (a
# page frame, a column rule) and is reported rather than taken in.
REACH = 0.45


def complete_region(
    gray: np.ndarray, box: tuple[int, int, int, int], obstacles: list[tuple[int, int, int, int]]
) -> tuple[tuple[int, int, int, int], dict]:
    """``box`` (left, top, right, bottom, in pixels) grown over connected ink.

    ``obstacles`` are boxes of other text on the page; their ink is never joined.
    Returns the new box and a record of the check.
    """
    height, width = gray.shape
    cell = max(2, round(width * CELL))
    rows, columns = -(-height // cell), -(-width // cell)
    background = float(np.median(gray))
    ink = np.abs(gray.astype(np.int16) - background) > 40
    for left, top, right, bottom in obstacles:
        ink[max(0, top):max(0, bottom), max(0, left):max(0, right)] = False
    padded = np.zeros((rows * cell, columns * cell), bool)
    padded[:height, :width] = ink
    grid = padded.reshape(rows, cell, columns, cell).any(axis=(1, 3))
    joined = _dilate(grid, JOIN)
    left, top, right, bottom = (
        box[0] // cell, box[1] // cell, -(-box[2] // cell), -(-box[3] // cell)
    )
    left, top = max(0, left), max(0, top)
    right, bottom = min(columns, max(right, left + 1)), min(rows, max(bottom, top + 1))
    original = (left, top, right, bottom)
    seen = np.zeros_like(joined)
    refused: set[str] = set()
    for _ in range(6):
        grown = False
        for component in _crossing(grid, joined, seen, (left, top, right, bottom)):
            c_left, c_top, c_right, c_bottom = component
            if (
                (left - c_left) > REACH * columns or (c_right - right) > REACH * columns
                or (top - c_top) > REACH * rows or (c_bottom - bottom) > REACH * rows
            ):
                refused |= _sides(component, (left, top, right, bottom))
                continue
            new = (min(left, c_left), min(top, c_top), max(right, c_right), max(bottom, c_bottom))
            if new != (left, top, right, bottom):
                left, top, right, bottom = new
                grown = True
        if not grown:
            break
    # Ink the obstacles hide still touching an edge means the crop may cut the figure.
    touching = refused | _blocked(ink, (left, top, right, bottom), cell, obstacles)
    # Only a side that moved takes the grid's position; the others stay exact.
    result = (
        left * cell if left < original[0] else box[0],
        top * cell if top < original[1] else box[1],
        min(width, right * cell) if right > original[2] else box[2],
        min(height, bottom * cell) if bottom > original[3] else box[3],
    )
    extended = result != tuple(box)
    check: dict = {"state": "clipped" if touching else "extended" if extended else "complete"}
    if extended:
        check["grownPx"] = [box[0] - result[0], box[1] - result[1],
                            result[2] - box[2], result[3] - box[3]]
    if touching:
        check["sides"] = sorted(touching)
    return result, check


def _dilate(grid: np.ndarray, steps: int) -> np.ndarray:
    out = grid.copy()
    for _ in range(steps):
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _crossing(grid: np.ndarray, joined: np.ndarray, seen: np.ndarray, box):
    """Boxes of ink inside the box joined to ink outside it (the ink, not the halo)."""
    left, top, right, bottom = box
    rows, columns = joined.shape
    border = (
        [(top, x) for x in range(left, right)] + [(bottom - 1, x) for x in range(left, right)]
        + [(y, left) for y in range(top, bottom)] + [(y, right - 1) for y in range(top, bottom)]
    )
    found = []
    for start in border:
        if not joined[start] or seen[start]:
            continue
        queue = deque([start])
        seen[start] = True
        y0 = x0 = 1 << 30
        y1 = x1 = -1
        while queue:
            y, x = queue.popleft()
            if grid[y, x]:
                y0, y1, x0, x1 = min(y0, y), max(y1, y), min(x0, x), max(x1, x)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < rows and 0 <= nx < columns and joined[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        if y1 < 0:
            continue
        component = (x0, y0, x1 + 1, y1 + 1)
        inside = x0 < right and x1 + 1 > left and y0 < bottom and y1 + 1 > top
        if inside and (x0 < left or y0 < top or x1 + 1 > right or y1 + 1 > bottom):
            found.append(component)
    return found


def _sides(component, box) -> set[str]:
    sides = set()
    if component[0] < box[0]:
        sides.add("left")
    if component[1] < box[1]:
        sides.add("top")
    if component[2] > box[2]:
        sides.add("right")
    if component[3] > box[3]:
        sides.add("bottom")
    return sides


def _blocked(ink: np.ndarray, box, cell: int, obstacles) -> set[str]:
    """Sides where ink of the content runs into another text's box at the edge."""
    left, top, right, bottom = (value * cell for value in box)
    sides = set()
    height, width = ink.shape
    for name, inner, outer in (
        ("top", (slice(top, top + cell), slice(left, right)), (top - cell, left, top, right)),
        ("bottom", (slice(bottom - cell, bottom), slice(left, right)),
         (bottom, left, bottom + cell, right)),
        ("left", (slice(top, bottom), slice(left, left + cell)), (top, left - cell, bottom, left)),
        ("right", (slice(top, bottom), slice(right - cell, right)),
         (top, right, bottom, right + cell)),
    ):
        o_top, o_left, o_bottom, o_right = outer
        if o_top < 0 or o_left < 0 or o_bottom > height or o_right > width:
            continue
        edge_ink = ink[inner].any()
        hidden = any(
            b_left < o_right and b_right > o_left and b_top < o_bottom and b_bottom > o_top
            for b_left, b_top, b_right, b_bottom in obstacles
        )
        if edge_ink and hidden:
            sides.add(name)
    return sides
