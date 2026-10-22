"""Bounded, content-only scene data for composed slide pages."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_BLOCKS, MAX_NODES, MAX_DEPTH, MAX_SCENE_CHARS = 24, 40, 4, 24_000
FENCE = re.compile(r"^\s*(?:\x60{3}|~~~)")
HEADING = re.compile(r"^#{1,2}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
LAYOUT = re.compile(r"^[ \t]*<!--[ \t]*layout:[^\r\n]*-->[ \t]*$")
SCENE_LINE = re.compile(r"^[ \t]*<!--[ \t]*scene:[ \t]*([A-Za-z0-9_-]+)[ \t]*-->[ \t]*$")
SCENE_HEADER = re.compile(r"^[ \t]*<!--[ \t]*scene:[^\r\n]*-->[ \t]*$")
NOTE = re.compile(r"^Note:")
MANAGED_IMAGE = re.compile(r"^!\[[^\]\n]*\]\(gunther-asset:[A-Za-z0-9_-]+\)$")
TABLE_RULE = re.compile(r'^\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?$')
STAT = re.compile(r"^\*\*[^*\n]+\*\*$")
VALUE = re.compile(
    r"^[\s~≈<>≤≥±+\-−–—]*[$€£¥￥₹₩]?[\s]*\d[\d,]*(?:\.\d+)?"
    r"[\s]*(?:[%‰°][A-Za-z℃℉]*|[A-Za-z]{1,8})?$"
)
CHART_VALUE = re.compile(
    r"^\s*([~≈<>≤≥±+\-−–—]*[$€£¥￥₹₩]?)\s*"
    r"(\d[\d,]*(?:\.\d+)?)\s*([%‰°][A-Za-z℃℉]*|[A-Za-z]{1,8})?\s*$"
)
LIST_LINE = re.compile(r"^(?:[ \t]*(?:[-+*]|\d+[.)])[ \t]+)")
ALL_CITATIONS = re.compile(r"\[(?:[ipd]:)?\d+(?:\s*[,，、]\s*\d+)*\]|\[\?\]")


class SceneModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SceneLeaf(SceneModel):
    kind: Literal["leaf"]
    block_id: str = Field(alias="blockId", pattern=r"^b[1-9][0-9]*$")
    view: Literal[
        "text",
        "stat",
        "list",
        "table",
        "quote",
        "image",
        "flow",
        "diagram",
        "bar_chart",
        "line_chart",
    ]
    size: Literal["small", "medium", "large", "display"] = "medium"
    align: Literal["start", "center", "end", "stretch"] = "start"
    role: Literal["main", "support", "focus"] = "main"


class SceneGroup(SceneModel):
    kind: Literal["row", "column"]
    children: list[SceneNode] = Field(min_length=2, max_length=4)
    weights: list[int] = Field(min_length=2, max_length=4)
    gap: Literal["tight", "normal", "wide"] = "normal"
    padding: Literal["none", "small", "medium"] = "small"
    surface: Literal["none", "soft", "focus", "outline"] = "none"
    align: Literal["start", "center", "stretch"] = "stretch"


SceneNode = Annotated[SceneLeaf | SceneGroup, Field(discriminator="kind")]
SceneGroup.model_rebuild()


class SceneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    root: SceneNode


class SceneEnvelope(SceneModel):
    version: Literal[1, 2]
    source_hash: str = Field(alias="sourceHash", pattern=r"^[0-9a-f]{64}$")
    graphics_hash: str | None = Field(
        default=None, alias="graphicsHash", pattern=r"^[0-9a-f]{64}$"
    )
    graphics: dict[str, Literal["flow", "diagram", "bar_chart", "line_chart"]] = Field(
        default_factory=dict
    )
    root: SceneNode

    def model_post_init(self, __context: object) -> None:
        if self.version == 1 and (self.graphics_hash is not None or self.graphics):
            raise ValueError("A version 1 scene cannot contain graphic licenses.")
        if self.version == 2 and self.graphics_hash is None:
            raise ValueError("A version 2 scene needs a graphics hash.")


class SlideBlock(BaseModel):
    id: str
    type: Literal["text", "stat", "list", "table", "quote", "image"]
    markdown: str


def _lines(text: str) -> list[tuple[str, bool]]:
    fenced = False
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if FENCE.match(line):
            out.append((line, True))
            fenced = not fenced
        else:
            out.append((line, fenced))
    return out


def _strip_metadata(text: str) -> str:
    rows = _lines(text)
    first = next((i for i, (line, _) in enumerate(rows) if line.strip()), None)
    if first is None or not LAYOUT.fullmatch(rows[first][0]):
        return text
    marker = first + 1
    while marker < len(rows) and not rows[marker][0].strip():
        marker += 1
    has_scene = marker < len(rows) and SCENE_HEADER.fullmatch(rows[marker][0])
    skip = marker + 1 if has_scene else first + 1
    while skip < len(rows) and not rows[skip][0].strip():
        skip += 1
    return "\n".join(line for line, _ in rows[:first] + rows[skip:])


def visible_markdown(text: str) -> str:
    rows = _lines(_strip_metadata(text))
    visible = []
    for line, fenced in rows:
        if not fenced and NOTE.match(line):
            break
        visible.append(line if fenced else ALL_CITATIONS.sub("", line))
    return "\n".join(visible).strip()


def source_hash(text: str) -> str:
    return hashlib.sha256(visible_markdown(text).encode("utf-8")).hexdigest()


def graphics_markdown(text: str) -> str:
    """Visible body used for strict graphic licenses; citations remain byte-for-byte intact."""
    rows = _lines(_strip_metadata(text))
    visible = []
    for line, fenced in rows:
        if not fenced and NOTE.match(line):
            break
        visible.append(line)
    return "\n".join(visible).strip()


def graphics_hash(text: str) -> str:
    return hashlib.sha256(graphics_markdown(text).encode("utf-8")).hexdigest()


def rebind_graphics_hash(
    before: str, after: str, reference_map: dict[int, int] | None = None
) -> str:
    """Carry v2 graphic permission across code-only citation renumbering.

    Callers that renumber citations must provide the trusted old-to-new reference map. This
    deliberately refuses manual reference edits and dropped/added references.
    """
    marker, _line = scene_marker(before)
    if not marker:
        return after
    scene_line = f"<!-- scene: {marker} -->"
    scene = decode_scene(scene_line)
    if (
        scene is None
        or scene.version != 2
        or scene.graphics_hash != graphics_hash(before)
        or scene.source_hash != source_hash(before)
        or source_hash(before) != source_hash(after)
    ):
        return after

    if reference_map is None:
        if graphics_markdown(before) != graphics_markdown(after):
            return after
    else:
        citation = re.compile(r"\[((?:[ipd]:)?)(\d+(?:\s*[,，、]\s*\d+)*)\]")

        def translated(text: str) -> str | None:
            def replace(match: re.Match[str]) -> str:
                refs = [int(value) for value in re.findall(r"\d+", match.group(2))]
                if any(ref not in reference_map for ref in refs):
                    raise KeyError("The trusted reference map is incomplete.")
                mapped = [reference_map[ref] for ref in refs]
                if any(not isinstance(value, int) or value < 1 for value in mapped):
                    raise ValueError("The trusted reference map contains an invalid reference.")
                prefix = match.group(1)
                return "".join(f"[{prefix}{value}]" for value in mapped)

            output = []
            try:
                for line, fenced in _lines(graphics_markdown(text)):
                    output.append(line if fenced else citation.sub(replace, line))
            except (KeyError, ValueError):
                return None
            return "\n".join(output)

        expected = translated(before)
        if expected is None or expected != graphics_markdown(after):
            return after
    updated = scene.model_copy(update={"graphics_hash": graphics_hash(after)})
    return with_scene(after, updated)


def _block_type(rows: list[tuple[str, bool]]) -> str:
    lines = [line.strip() for line, _ in rows if line.strip()]
    if not lines or any(fenced for _, fenced in rows):
        return "text"
    if len(lines) == 1 and MANAGED_IMAGE.fullmatch(lines[0]):
        return "image"
    if any(line.startswith("|") for line in lines) and any(
        TABLE_RULE.fullmatch(line) for line in lines
    ):
        return "table"
    if all(LIST_LINE.match(line) for line in lines):
        return "list"
    quoted = [line for line in lines if line.startswith(">")]
    if quoted and len(quoted) + sum(line.startswith("—") for line in lines) == len(lines):
        return "quote"
    if len(lines) == 1:
        candidate = ALL_CITATIONS.sub("", lines[0]).strip()
        if STAT.fullmatch(candidate) and VALUE.fullmatch(candidate[2:-2].strip()):
            return "stat"
    return "text"


def _table_rows(markdown: str) -> list[list[str]] | None:
    lines = [line.strip() for line in markdown.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3 or not TABLE_RULE.fullmatch(lines[1]):
        return None
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
    if any(any("\\|" in cell for cell in row) for row in rows):
        # Escaped-pipe table parsing is ambiguous here; keep the source table as a table.
        return None
    return rows


def _table_only_block(markdown: str) -> bool:
    lines = [line for line in markdown.splitlines() if line.strip()]
    return bool(lines) and all(line.lstrip().startswith("|") for line in lines)


def _chart_candidate(markdown: str) -> bool:
    if not _table_only_block(markdown):
        return False
    rows = _table_rows(markdown)
    if rows is None or any(len(row) != 2 for row in rows):
        return False
    data = rows[2:]
    if not 2 <= len(data) <= 6:
        return False
    values = []
    for row in data:
        cell = ALL_CITATIONS.sub("", row[1]).strip()
        match = CHART_VALUE.fullmatch(cell)
        if not match:
            return False
        prefix, _number, suffix = match.groups()
        currency = "".join(re.findall(r"[$€£¥￥₹₩]", prefix or ""))
        unit = currency + (suffix or "")
        values.append(unit.casefold())
    # Units may be carried by the header when the cells contain bare values.
    header = rows[0][1]
    parenthetical = re.search(r"\(([^()]+)\)\s*$", header)
    unit_text = parenthetical.group(1) if parenthetical else header
    header_unit = "".join(re.findall(r"[%‰°℃℉$€£¥￥₹₩]|\b[A-Za-z]{1,8}\b", unit_text))
    header_unit = header_unit.casefold()
    normalized = [unit or header_unit for unit in values]
    return bool(normalized) and len(set(normalized)) == 1


def allowed_views(block: SlideBlock) -> list[str]:
    """Return safe display choices inferred from this block's structure alone."""
    views = [block.type]
    if block.type == "list":
        lines = [line for line in block.markdown.splitlines() if line.strip()]
        if 2 <= len(lines) <= 6 and all(
            re.match(r"^[ \t]*\d+[.)][ \t]+", line) for line in lines
        ):
            views.append("flow")
    if block.type == "table":
        rows = _table_rows(block.markdown)
        if _table_only_block(block.markdown) and rows and len(rows[0]) == 3:
            from gunther.skill_checks import diagram_problem

            if diagram_problem(f"## Graphic block\n\n{block.markdown}") is None:
                views.append("diagram")
        if _chart_candidate(block.markdown):
            views.extend(("bar_chart", "line_chart"))
    return views


def split_blocks(text: str) -> tuple[str, list[SlideBlock]]:
    rows = _lines(_strip_metadata(text))
    visible = []
    for line, fenced in rows:
        if not fenced and NOTE.match(line):
            break
        visible.append((line, fenced))

    heading = ""
    for at, (line, fenced) in enumerate(visible):
        if line.strip() and not fenced:
            match = HEADING.fullmatch(line.strip())
            if match:
                heading = match.group(1).strip()
                visible.pop(at)
            break

    groups: list[list[tuple[str, bool]]] = []
    current: list[tuple[str, bool]] = []
    for line, fenced in visible:
        if not line.strip() and not fenced:
            if current:
                groups.append(current)
                current = []
        else:
            current.append((line, fenced))
    if current:
        groups.append(current)
    if len(groups) > MAX_BLOCKS:
        return heading, []
    blocks = []
    for number, group in enumerate(groups, start=1):
        markdown = "\n".join(line for line, _ in group).strip()
        if markdown:
            blocks.append(SlideBlock(id=f"b{number}", type=_block_type(group), markdown=markdown))
    return heading, blocks


def validate_scene(
    root: SceneNode,
    blocks: list[SlideBlock],
    allowed: dict[str, list[str] | set[str]] | None = None,
    graphics: dict[str, str] | None = None,
) -> str | None:
    if not isinstance(root, SceneGroup):
        return "The scene root must be a row or column."
    if not 2 <= len(blocks) <= MAX_BLOCKS:
        return "The page does not have a supported number of content blocks."
    expected = {block.id: block.type for block in blocks}
    choices = allowed or {block.id: {block.type} for block in blocks}
    seen: list[str] = []
    count = 0

    def walk(node: SceneNode, depth: int) -> str | None:
        nonlocal count
        count += 1
        if count > MAX_NODES:
            return "The scene contains too many elements."
        if depth > MAX_DEPTH:
            return "The scene is nested too deeply."
        if isinstance(node, SceneLeaf):
            if node.block_id not in expected:
                return f"Unknown content block {node.block_id}."
            permitted = set(choices.get(node.block_id, (expected[node.block_id],)))
            if node.view not in permitted:
                return f"The {node.view} view does not match {node.block_id}'s content type."
            if node.view in {"flow", "diagram", "bar_chart", "line_chart"}:
                graphic_count[0] += 1
                if graphics is not None and graphics.get(node.block_id) != node.view:
                    return f"The {node.view} view for {node.block_id} has no valid license."
            seen.append(node.block_id)
            return None
        if len(node.children) != len(node.weights):
            return "Every scene group needs one weight per child."
        if any(weight < 1 or weight > 4 for weight in node.weights):
            return "Scene proportions must be between 1 and 4."
        for child in node.children:
            issue = walk(child, depth + 1)
            if issue:
                return issue
        return None

    graphic_count = [0]
    issue = walk(root, 1)
    if issue:
        return issue
    if graphic_count[0] > 3:
        return "A composed page can contain at most three graphic elements."
    duplicates = sorted({block_id for block_id in seen if seen.count(block_id) > 1})
    missing = sorted(set(expected) - set(seen))
    if duplicates:
        return f"Duplicate content blocks: {', '.join(duplicates)}."
    if missing:
        return f"Missing content blocks: {', '.join(missing)}."
    if graphics is not None:
        licensed = {
            block_id: view
            for block_id, view in graphics.items()
            if view in {"flow", "diagram", "bar_chart", "line_chart"}
        }
        rendered = {
            node.block_id: node.view
            for node in _leaves(root)
            if node.view in {"flow", "diagram", "bar_chart", "line_chart"}
        }
        if any(rendered.get(block_id) != view for block_id, view in licensed.items()):
            return "Every graphic license must match its selected block view."
    return None


def _leaves(root: SceneNode) -> list[SceneLeaf]:
    if isinstance(root, SceneLeaf):
        return [root]
    return [leaf for child in root.children for leaf in _leaves(child)]


def canonical_scene_json(scene: SceneEnvelope | dict) -> str:
    data = (
        scene.model_dump(by_alias=True, exclude_none=True)
        if isinstance(scene, BaseModel)
        else scene
    )
    if data.get("version") == 1:
        data = {key: data[key] for key in ("version", "sourceHash", "root")}
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def encode_scene(scene: SceneEnvelope) -> str:
    raw = canonical_scene_json(scene).encode("utf-8")
    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if len(payload) > MAX_SCENE_CHARS:
        raise ValueError("The scene metadata is too large.")
    return f"<!-- scene: {payload} -->"


def decode_scene(line: str) -> SceneEnvelope | None:
    match = SCENE_LINE.fullmatch(line.strip())
    if not match or len(match.group(1)) > MAX_SCENE_CHARS:
        return None
    try:
        raw = base64.urlsafe_b64decode(match.group(1) + "=" * (-len(match.group(1)) % 4))
        if len(raw) > MAX_SCENE_CHARS:
            return None
        return SceneEnvelope.model_validate_json(raw)
    except Exception:
        return None


def scene_marker(text: str) -> tuple[str | None, str]:
    rows = text.splitlines()
    first = next((i for i, line in enumerate(rows) if line.strip()), None)
    if first is None or not LAYOUT.fullmatch(rows[first]):
        return None, ""
    marker = first + 1
    while marker < len(rows) and not rows[marker].strip():
        marker += 1
    if marker >= len(rows):
        return None, ""
    line = rows[marker]
    if not SCENE_HEADER.fullmatch(line):
        return None, ""
    match = SCENE_LINE.fullmatch(line)
    return (match.group(1) if match else None, line)


def without_scene_line(text: str) -> str:
    rows = text.splitlines(keepends=True)
    values = [row.rstrip("\r\n") for row in rows]
    first = next((i for i, line in enumerate(values) if line.strip()), None)
    if first is None or not LAYOUT.fullmatch(values[first]):
        return text
    marker = first + 1
    while marker < len(rows) and not values[marker].strip():
        marker += 1
    if marker >= len(rows) or not SCENE_HEADER.fullmatch(values[marker]):
        return text
    return "".join(rows[:marker] + rows[marker + 1 :])


def with_scene(text: str, scene: SceneEnvelope) -> str:
    return with_scene_line(text, encode_scene(scene))


def with_scene_line(text: str, marker: str) -> str:
    """Insert a scene marker (possibly stale or invalid) directly after the layout marker."""
    clean = without_scene_line(text)
    rows = clean.splitlines(keepends=True)
    values = [row.rstrip("\r\n") for row in rows]
    first = next((i for i, line in enumerate(values) if line.strip()), None)
    if first is not None and LAYOUT.fullmatch(values[first]):
        ending = "\r\n" if rows[first].endswith("\r\n") else "\n"
        return "".join(rows[:first + 1]) + marker + ending + "".join(rows[first + 1 :])
    return f"{marker}\n{clean}"
