"""One document shape for every source, built in four layers.

1. Evidence: numbered, read-only blocks with exact text and page positions
   (``Block``, ``Evidence``), from the layout parser and the PDF's own glyphs.
2. Plan: the only place structure is decided (``Plan``). Rules draft it and a
   model may correct it; both move block ids, neither writes text.
3. Check: the plan against the shape; every problem is a named ``Violation``.
4. Assemble: evidence + a plan without errors -> the stored document.

See docs/DOCUMENT_STRUCTURE_PLAN.md for the element contract and the types.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from gunther.content import ParsedBlock
from gunther.documents import ParsedDocument, split_reference_entries

DocumentType = Literal["paper", "book", "report", "slides", "article", "manual", "unknown"]
DOCUMENT_TYPES: tuple[str, ...] = (
    "paper", "book", "report", "slides", "article", "manual", "unknown",
)

# Layout labels a block can carry (Docling's, normalised to these names).
LABELS = (
    "text", "title", "section_header", "list_item", "caption", "footnote",
    "page_header", "page_footer", "page_number", "picture", "table", "formula",
    "code", "reference", "other",
)
TEXT_LABELS = frozenset(LABELS) - {"picture", "table", "formula"}
IMAGE_LABELS = frozenset({"picture", "table", "formula"})

FRONT_PARTS = ("abstract", "summary", "keywords", "preface", "other")
BACK_PARTS = (
    "references", "appendix", "availability", "acknowledgements", "notes", "glossary", "other",
)
FLOAT_ID = re.compile(r"^[FTN]\d+$")
BLOCK_ID = re.compile(r"^b\d+$")
RANGE = re.compile(r"^(b\d+)\.\.(b\d+)$")


# 1. Evidence --------------------------------------------------------------------


@dataclass
class Block:
    """One layout item with its exact text. Never edited after evidence is built."""

    id: str
    label: str
    text: str = ""
    page: int | None = None
    # First region, parts per million of the page: left, top, right, bottom.
    box: tuple[int, int, int, int] | None = None
    # Every region the item covers: {"page": n, "bboxPpm": [left, top, width, height]}.
    regions: list[dict] = field(default_factory=list)
    font_size: float | None = None
    level: int | None = None
    inline: list[dict] = field(default_factory=list)
    marker: str | None = None
    # The picture block this text is printed inside; such text belongs to it.
    inside: str | None = None
    image_key: str | None = None
    table: dict | None = None
    clues: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def placeable(self) -> bool:
        return self.inside is None


@dataclass
class Evidence:
    blocks: list[Block]
    # Page sizes in points: {page: {"width": w, "height": h}}.
    pages: dict[int, dict] = field(default_factory=dict)
    # Declared metadata (``document_structure.pdf_metadata``).
    metadata: dict = field(default_factory=dict)
    body_font_size: float | None = None
    manifest: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._index = {block.id: position for position, block in enumerate(self.blocks)}

    def get(self, block_id: str) -> Block | None:
        position = self._index.get(block_id)
        return self.blocks[position] if position is not None else None

    def position(self, block_id: str) -> int | None:
        return self._index.get(block_id)

    def to_json(self) -> dict:
        return {
            "blocks": [asdict(block) for block in self.blocks],
            "pages": {str(page): size for page, size in self.pages.items()},
            "metadata": self.metadata,
            "bodyFontSize": self.body_font_size,
            "manifest": self.manifest,
        }

    @classmethod
    def from_json(cls, data: dict) -> Evidence:
        blocks = []
        for item in data.get("blocks", []):
            box = item.get("box")
            blocks.append(Block(**{**item, "box": tuple(box) if box else None}))
        return cls(
            blocks,
            pages={int(page): size for page, size in data.get("pages", {}).items()},
            metadata=data.get("metadata", {}),
            body_font_size=data.get("bodyFontSize"),
            manifest=data.get("manifest", {}),
        )


# 2. Plan ------------------------------------------------------------------------


class Section(BaseModel):
    heading: str | None = Field(None, description="Block id of the heading; null for unheaded text")
    level: int = Field(1, ge=1, le=6)
    blocks: list[str] = Field(
        default_factory=list,
        description="Block ids, ranges 'bA..bB', figure ids 'F1' and table ids 'T1', in order",
    )


class Part(BaseModel):
    part: str = Field(description="A front part (abstract, summary, keywords, preface, other) "
                      "or back part (references, appendix, availability, acknowledgements, "
                      "notes, glossary, other)")
    heading: str | None = None
    level: int = Field(1, ge=1, le=6)
    blocks: list[str] = Field(default_factory=list)


class Figure(BaseModel):
    id: str = Field(description="F1, F2, …")
    number: str | None = Field(None, description="The printed number, e.g. '3' or 'S2'")
    images: list[str] = Field(default_factory=list, description="Picture block ids")
    caption: list[str] = Field(default_factory=list)
    labels: list[str] = Field(
        default_factory=list, description="Text printed around the image that belongs to it"
    )


class Table(BaseModel):
    id: str = Field(description="T1, T2, …")
    number: str | None = None
    table: list[str] = Field(default_factory=list, description="Table or picture block ids")
    caption: list[str] = Field(default_factory=list)
    footnotes: list[str] = Field(default_factory=list)


class Note(BaseModel):
    id: str = Field(description="N1, N2, …")
    marker: str | None = None
    blocks: list[str] = Field(default_factory=list)


class Drop(BaseModel):
    blocks: list[str]
    reason: str


class Plan(BaseModel):
    type: DocumentType = "unknown"
    title: list[str] = Field(default_factory=list)
    subtitle: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    front: list[Part] = Field(default_factory=list)
    body: list[Section] = Field(default_factory=list)
    back: list[Part] = Field(default_factory=list)
    figures: list[Figure] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    joins: list[list[str]] = Field(
        default_factory=list,
        description="[first, second]: a paragraph continued across a column or page break",
    )
    drop: list[Drop] = Field(default_factory=list)
    by: str = "rules"


def expand(ids: list[str], evidence: Evidence) -> list[str]:
    """Block ids with ranges ``bA..bB`` expanded in evidence order.

    A range covers every block between its ends, including blocks printed inside
    pictures; callers skip those (they are never placed). A malformed or
    backwards range raises ``ValueError``.
    """
    result: list[str] = []
    for item in ids:
        match = RANGE.match(item)
        if not match:
            result.append(item)
            continue
        start, stop = evidence.position(match[1]), evidence.position(match[2])
        if start is None or stop is None or stop < start:
            raise ValueError(f"Invalid block range {item}")
        result.extend(block.id for block in evidence.blocks[start : stop + 1])
    return result


# 3. Check -----------------------------------------------------------------------


@dataclass
class Violation:
    severity: Literal["error", "warning"]
    code: str
    message: str
    blocks: list[str] = field(default_factory=list)


@dataclass
class StructureStatus:
    """Who decided the structure, and whether a model checked it."""

    state: Literal["checked", "draft", "needs-key", "failed", "too-long"]
    by: str = "rules"
    error: str | None = None
    violations: list[Violation] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "state": self.state,
            "by": self.by,
            "error": self.error,
            "violations": [asdict(v) for v in self.violations],
        }


def check(plan: Plan, evidence: Evidence) -> list[Violation]:
    """Every way ``plan`` breaks the shape; errors first. See the plan document.

    ``Violation.blocks`` holds the ids the message is about. Usually those are
    blocks, but they can also be figure or table ids, or ids that do not exist.
    Text printed inside a picture (``Block.inside``) is ignored wherever it
    appears: never counted, never required.
    """
    return _Check(plan, evidence).run()


def _and(items: list[str], limit: int = 12) -> str:
    """'a', 'a and b', 'a, b and c'; a long list ends with 'and N more'."""
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" and {len(items) - limit} more"
    if len(items) > 1:
        return ", ".join(items[:-1]) + f" and {items[-1]}"
    return "".join(items)


def _is(items: list[str]) -> str:
    return "is" if len(items) == 1 else "are"


def _in(places: list[str]) -> str:
    """Where something was put: 'in A, twice in B and in C'."""
    times = {2: "twice", 3: "three times"}
    return _and(
        [
            f"{times.get(count, f'{count} times')} in {place}" if count > 1 else f"in {place}"
            for place, count in Counter(places).items()
        ]
    )


class _Check:
    """Records where the plan puts each block, then judges the plan from that."""

    def __init__(self, plan: Plan, evidence: Evidence) -> None:
        self.plan, self.evidence = plan, evidence
        self.errors: list[Violation] = []
        self.warnings: list[Violation] = []
        self.floats: dict[str, Figure | Table] = {}
        # Every placement of a block or a float id, named in words, in plan order.
        self.homes: dict[str, list[str]] = {}
        self.float_homes: dict[str, list[str]] = {}
        # Each section and part list with everything it covers, for the join rule.
        self.lists: list[list[str]] = []
        self.captions: dict[str, list[str]] = {}

    def error(self, code: str, message: str, blocks: list[str]) -> None:
        self.errors.append(Violation("error", code, message, blocks))

    def warn(self, code: str, message: str, blocks: list[str] | None = None) -> None:
        self.warnings.append(Violation("warning", code, message, blocks or []))

    def run(self) -> list[Violation]:
        plan = self.plan
        for item in [*plan.figures, *plan.tables]:
            if item.id in self.floats:
                self.error(
                    "duplicate",
                    f"The id {item.id} is given to two figures or tables. Give each its own id.",
                    [item.id],
                )
            self.floats[item.id] = item
        title = self.line("the title", plan.title)
        self.line("the subtitle", plan.subtitle)
        self.line("the authors", plan.authors)
        has_abstract = has_references = False
        areas = (("front", plan.front, FRONT_PARTS), ("back", plan.back, BACK_PARTS))
        for area, parts, names in areas:
            for index, part in enumerate(parts, 1):
                if part.part not in names:
                    self.error(
                        "unknown-part",
                        f"{area.capitalize()} part {index} is named '{part.part}'. Name it one "
                        f"of: {', '.join(names)}.",
                        [],
                    )
        for index, part in enumerate(plan.front, 1):
            ids = self.part(f"front part {index}", part.part, part.heading, part.blocks)
            has_abstract = has_abstract or (part.part == "abstract" and self.has_blocks(ids))
        for index, section in enumerate(plan.body, 1):
            detail = f"heading {section.heading}" if section.heading else "no heading"
            self.part(f"body section {index}", detail, section.heading, section.blocks)
        for index, part in enumerate(plan.back, 1):
            ids = self.part(f"back part {index}", part.part, part.heading, part.blocks)
            has_references = has_references or (part.part == "references" and self.has_blocks(ids))
        for figure in plan.figures:
            self.figure(figure)
        for table in plan.tables:
            self.table(table)
        for note in plan.notes:
            self.resolve(f"note {note.id}", note.blocks)
        for drop in plan.drop:
            self.resolve(f"the drop list ({drop.reason})", drop.blocks)
        self.placements()
        self.joins()
        if not title:
            self.warn(
                "no-title",
                "No title is marked. If the document prints one, put its block under title.",
            )
        if plan.type == "paper":
            self.paper(has_abstract, has_references)
        return [*self.errors, *self.warnings]

    # -- Where blocks are placed

    def resolve(self, where: str, items: list[str], *, floats: bool = False) -> list[str]:
        """Expand ``items``, record where each id is placed, return the ids that count.

        With ``floats`` (section and part lists) figure and table ids count too.
        """
        counted: list[str] = []
        covered: list[str] = []
        for item in items:
            for block_id in self.expand_one(where, item):
                block = self.evidence.get(block_id)
                if block is not None:
                    covered.append(block_id)
                    if block.placeable:
                        self.homes.setdefault(block_id, []).append(where)
                        counted.append(block_id)
                elif floats and block_id in self.floats:
                    covered.append(block_id)
                    self.float_homes.setdefault(block_id, []).append(where)
                    counted.append(block_id)
                else:
                    self.unknown(where, block_id)
        if floats:
            self.lists.append(covered)
        return counted

    def expand_one(self, where: str, item: str) -> list[str]:
        malformed = ".." in item and not RANGE.match(item)
        ids: list[str] = []
        try:
            if not malformed:
                ids = expand([item], self.evidence)
        except ValueError:
            malformed = True
        if malformed:
            self.error(
                "bad-range",
                f"In {where}, '{item}' is not a valid range. A range runs forward from one "
                "block to a later one, like b4..b9.",
                [item],
            )
        return ids

    def unknown(self, where: str, item: str) -> None:
        if item in self.floats:
            kind = "figure" if isinstance(self.floats[item], Figure) else "table"
            why = (
                f"is a {kind}, which can only be placed inside the blocks of a section or part"
            )
        elif FLOAT_ID.match(item) and item.startswith("N"):
            why = "is a note; notes are listed under notes and shown after the body, never placed"
        elif FLOAT_ID.match(item):
            why = f"is not defined; add it under {'figures' if item[0] == 'F' else 'tables'}"
        else:
            why = "is not a block of this document"
        self.error("unknown-id", f"In {where}, '{item}' {why}.", [item])

    def line(self, where: str, items: list[str]) -> list[str]:
        ids = self.resolve(where, items)
        self.need_text("heading-not-text", where, ids)
        return ids

    def part(self, name: str, detail: str, heading: str | None, items: list[str]) -> list[str]:
        if heading:
            where = f"the heading of {name}"
            self.need_text("heading-not-text", where, self.resolve(where, [heading]))
        return self.resolve(f"{name} ({detail})", items, floats=True)

    def has_blocks(self, ids: list[str]) -> bool:
        return any(self.evidence.get(block_id) is not None for block_id in ids)

    def need_text(self, code: str, where: str, ids: list[str]) -> None:
        for block_id in ids:
            label = self.evidence.get(block_id).label
            if label not in TEXT_LABELS:
                self.error(
                    code,
                    f"{block_id} is used as {where}, but it is a {label} block, not text.",
                    [block_id],
                )

    # -- Figures and tables

    def figure(self, figure: Figure) -> None:
        name = f"figure {figure.id}"
        images = self.resolve(f"{name} images", figure.images)
        self.captions[figure.id] = self.resolve(f"{name} caption", figure.caption)
        labels = self.resolve(f"{name} labels", figure.labels)
        if not images:
            self.error(
                "figure-without-image",
                f"Figure {figure.id} has no image. Give it the picture it shows, or remove it.",
                [figure.id],
            )
        for block_id in images:
            label = self.evidence.get(block_id).label
            if label not in IMAGE_LABELS:
                self.error(
                    "image-not-picture",
                    f"{block_id} is used as an image of {name}, but it is a {label} block, "
                    "not a picture, table or formula.",
                    [block_id],
                )
        self.need_text("caption-not-text", f"the caption of {name}", self.captions[figure.id])
        self.need_text("caption-not-text", f"a label of {name}", labels)

    def table(self, table: Table) -> None:
        name = f"table {table.id}"
        blocks = self.resolve(f"{name} content", table.table)
        captions = self.resolve(f"{name} caption", table.caption)
        footnotes = self.resolve(f"{name} footnotes", table.footnotes)
        if not blocks:
            self.error(
                "table-without-content",
                f"Table {table.id} has no table block. Give it the table (or the picture of "
                "the table) it shows, or remove it.",
                [table.id],
            )
        for block_id in blocks:
            label = self.evidence.get(block_id).label
            if label not in {"table", "picture"}:
                self.error(
                    "table-without-content",
                    f"{block_id} is used as the content of {name}, but it is a {label} block, "
                    "not a table or a picture of one.",
                    [block_id],
                )
        self.need_text("caption-not-text", f"the caption of {name}", captions)
        self.need_text("caption-not-text", f"a footnote of {name}", footnotes)

    # -- Rules over the whole plan

    def names(self, ids: list[str]) -> str:
        """Block ids for a message; three or more in a row are written as a range."""
        position = self.evidence.position
        runs: list[list[str]] = []
        for block_id in sorted(set(ids), key=lambda value: position(value) or 0):
            if runs and self.contiguous(runs[-1][-1], block_id):
                runs[-1].append(block_id)
            else:
                runs.append([block_id])
        pieces: list[str] = []
        for run in runs:
            pieces.extend(run if len(run) < 3 else [f"{run[0]}..{run[-1]}"])
        return _and(pieces, limit=60)

    def contiguous(self, first: str, second: str) -> bool:
        """Only text printed inside pictures lies between them, so a range covers both."""
        start, stop = self.evidence.position(first), self.evidence.position(second)
        return all(self.evidence.blocks[at].inside for at in range(start + 1, stop))

    def placements(self) -> None:
        groups: dict[tuple[str, ...], list[str]] = {}
        for block_id, places in self.homes.items():
            if len(places) > 1:
                groups.setdefault(tuple(places), []).append(block_id)
        for places, ids in groups.items():
            self.error(
                "duplicate",
                f"{self.names(ids)} {_is(ids)} placed more than once: {_in(places)}. "
                "Keep each block in one place.",
                ids,
            )
        missing = [b.id for b in self.evidence.blocks if b.placeable and b.id not in self.homes]
        if missing:
            self.error(
                "unplaced",
                f"{self.names(missing)} {_is(missing)} not placed anywhere. Put each in a "
                "section, a part, a figure or a table, or list it under drop with a reason.",
                missing,
            )
        for float_id, item in self.floats.items():
            kind = "Figure" if isinstance(item, Figure) else "Table"
            places = self.float_homes.get(float_id, [])
            if not places:
                self.error(
                    "float-not-placed",
                    f"{kind} {float_id} is defined but not placed. Put {float_id} in the "
                    "blocks of the section or part where it belongs, or remove it.",
                    [float_id],
                )
            elif len(places) > 1:
                self.error(
                    "float-placed-twice",
                    f"{kind} {float_id} is placed more than once: {_in(places)}. Place it once.",
                    [float_id],
                )

    def joins(self) -> None:
        located: dict[str, tuple[int, int]] = {}
        for which, covered in enumerate(self.lists):
            for position, block_id in enumerate(covered):
                located.setdefault(block_id, (which, position))
        for join in self.plan.joins:
            if len(join) != 2:
                self.error(
                    "join-not-adjacent",
                    "A join names exactly two blocks, the first part and the part that "
                    f"continues it, but this one names {len(join)}: {', '.join(join) or 'none'}.",
                    list(join),
                )
                continue
            first, second = join
            absent = [block_id for block_id in join if self.evidence.get(block_id) is None]
            if absent:
                self.error(
                    "unknown-id",
                    f"The join of {first} and {second} names {_and(absent)}, which "
                    f"{_is(absent)} not a block of this document.",
                    absent,
                )
            elif all(self.evidence.get(block_id).placeable for block_id in join):
                problem = self.join_problem(first, second, located)
                if problem:
                    self.error(
                        "join-not-adjacent",
                        f"{first} and {second} cannot be joined: {problem}.",
                        [first, second],
                    )

    def join_problem(self, first: str, second: str, located: dict) -> str | None:
        for block_id in (first, second):
            label = self.evidence.get(block_id).label
            if label not in TEXT_LABELS:
                return f"{block_id} is a {label} block, not text"
        for block_id in (first, second):
            if block_id not in located:
                return f"{block_id} is not in the blocks of a section or part"
        (list_first, at), (list_second, to) = located[first], located[second]
        if list_first != list_second:
            return "they are in different sections or parts"
        if to <= at:
            return f"{second} does not come after {first}"
        between = [
            block_id for block_id in self.lists[list_first][at + 1 : to]
            if block_id not in self.floats
            and not ((block := self.evidence.get(block_id)) and not block.placeable)
        ]
        if between:
            return (
                f"{_and(between)} {'lies' if len(between) == 1 else 'lie'} between them "
                "(only figures, tables and text printed inside pictures may)"
            )
        return None

    # -- Warnings

    def paper(self, has_abstract: bool, has_references: bool) -> None:
        if not has_abstract:
            self.warn(
                "no-abstract",
                "No abstract is marked. A paper normally has one; put it in a front part "
                "named abstract.",
            )
        if not has_references:
            self.warn(
                "no-references",
                "No reference list is marked. A paper normally has one; put it in a back "
                "part named references.",
            )
        figures = self.plan.figures
        printed = [f.number.strip() for f in figures if f.number]
        digits = sorted({int(n) for n in printed if n.isascii() and n.isdigit()})
        missing = [str(n) for n in range(1, digits[-1] + 1) if n not in digits] if digits else []
        if missing:
            self.warn(
                "figure-numbers-gap",
                f"Figure numbers go up to {digits[-1]}, but {_and(missing)} {_is(missing)} "
                "missing. Check that no figure was left out or given the wrong number.",
            )
        by_number: dict[str, list[Figure]] = {}
        for figure in figures:
            if figure.number and figure.number.strip():
                by_number.setdefault(figure.number.strip().casefold(), []).append(figure)
            if not self.captions.get(figure.id):
                self.warn(
                    "figure-without-caption", f"Figure {figure.id} has no caption.", [figure.id]
                )
        for same in by_number.values():
            if len(same) > 1:
                ids = [figure.id for figure in same]
                self.warn(
                    "duplicate-figure-number",
                    f"Figures {_and(ids)} have the same number ({same[0].number.strip()}).",
                    ids,
                )


# 4. Assemble --------------------------------------------------------------------


def assemble(plan: Plan, evidence: Evidence, status: StructureStatus) -> ParsedDocument:
    """The stored document, in reading order, from a plan without errors.

    Order: title, authors and front parts; body sections; notes; back parts.
    Every text comes from a block, word for word; only the plan moves them.
    """
    errors = [v.message for v in check(plan, evidence) if v.severity == "error"]
    if errors:
        more = f" (and {len(errors) - 3} more)" if len(errors) > 3 else ""
        raise ValueError("The plan has errors: " + " ".join(errors[:3]) + more)
    return _Assembly(plan, evidence).build(status)


# A caption part that ends a sentence leaves the next one on its own line.
_SENTENCE_END = re.compile(r"[.!?。！？:：][\"'”’)]*$")
# These keep their element without text: the picture is the content.
_VISUAL_KINDS = frozenset({"equation", "figure", "table"})
_SHIFTED = ("start", "end", "numeratorEnd", "denominatorStart")


def _placed(items: list[str], evidence: Evidence) -> list[str]:
    """The ids in a plan list, ranges expanded, without text printed inside pictures."""
    return [
        block_id
        for block_id in expand(items, evidence)
        if (block := evidence.get(block_id)) is None or block.placeable
    ]


def _after_sentence(text: str) -> str:
    return "\n" if _SENTENCE_END.search(text.rstrip()) else " "


def _shifted(span: dict, offset: int) -> dict:
    return {**span, **{key: span[key] + offset for key in _SHIFTED if key in span}}


def _joined(
    blocks: list[Block], separator: str | Callable[[str], str] = " "
) -> tuple[str, list[dict]]:
    """The blocks' texts one after the other, with their inline spans moved to match."""
    text, spans, _ = _joined_parts(blocks, separator)
    return text, spans


def _joined_parts(
    blocks: list[Block], separator: str | Callable[[str], str] = " "
) -> tuple[str, list[dict], dict[str, tuple[int, int]]]:
    """``_joined``, and where each block's text starts and ends in the result."""
    text, spans, glued = "", [], False
    offsets: dict[str, tuple[int, int]] = {}
    for block in blocks:
        if not block.text.strip():
            continue
        if text and not glued:
            text += separator(text) if callable(separator) else separator
        spans.extend(_shifted(span, len(text)) for span in block.inline)
        offsets[block.id] = (len(text), len(text) + len(block.text))
        text += block.text
        # A word hyphenated at the end of one region goes on at the start of the next.
        glued = bool(block.extra.get("joinsNext"))
    return text, spans, offsets


def _regions(
    blocks: list[Block], offsets: dict[str, tuple[int, int]] | None = None,
    role: str | None = None,
) -> list[dict]:
    """Every page region of the blocks; with ``offsets``, the element text each one holds.

    A block of one region gives that region its text span; the regions of a block the
    parser read as one item share the block's span.
    """
    found = []
    for block in blocks:
        for region in block.regions:
            entry = copy.deepcopy(region)
            if offsets and block.id in offsets:
                entry["textStart"], entry["textEnd"] = offsets[block.id]
            if role:
                entry["role"] = role
            if ref := block.extra.get("documentRef"):
                entry["documentRef"] = ref
            found.append(entry)
    return found


def _anchor(blocks: list[Block], offsets: dict[str, tuple[int, int]] | None = None) -> dict:
    """Page and box of the first block; spans keep every region of all of them."""
    first = blocks[0]
    anchor: dict = {}
    if ref := first.extra.get("documentRef"):
        anchor["documentRef"] = ref
    if first.page is not None:
        anchor["page"] = first.page
    if first.box:
        left, top, right, bottom = first.box
        anchor["bboxPpm"] = [left, top, right - left, bottom - top]
    if spans := _regions(blocks, offsets):
        anchor["spans"] = spans
    return anchor


def _union(blocks: list[Block], page: int | None) -> list[int] | None:
    """One box [left, top, width, height] around the blocks that sit on ``page``."""
    boxes = [block.box for block in blocks if block.box and block.page == page]
    if not boxes:
        return None
    left, top = min(box[0] for box in boxes), min(box[1] for box in boxes)
    right, bottom = max(box[2] for box in boxes), max(box[3] for box in boxes)
    return [left, top, right - left, bottom - top]


def table_text(data: dict) -> str:
    """A table's text row by row, its cells in column order: an answer stays on the line
    of the item it answers ("Exits marked | ☒ | ☐ | ☐"), in search as on the page."""
    cells = data.get("table_cells") or []
    if not all(
        isinstance(cell.get(key), int)
        for cell in cells for key in ("start_row_offset_idx", "start_col_offset_idx")
    ):
        # Without grid positions, the layout parser's own order: one cell per line.
        return "\n".join(str(cell.get("text", "")) for cell in cells)
    rows: dict[int, list[tuple[int, str]]] = {}
    for cell in cells:
        rows.setdefault(cell["start_row_offset_idx"], []).append(
            (cell["start_col_offset_idx"], str(cell.get("text", "")).strip())
        )
    return "\n".join(
        " | ".join(text for _, text in sorted(row)) for _, row in sorted(rows.items())
    )


def _grid(blocks: list[Block]) -> dict:
    """Docling table data; a table cut across pages is stacked into one grid."""
    if len(blocks) == 1:
        return blocks[0].table or {}
    cells: list[dict] = []
    rows = columns = 0
    for block in blocks:
        data = block.table or {}
        used = 0
        for cell in data.get("table_cells", []):
            used = max(used, cell.get("end_row_offset_idx", 0))
            cells.append(
                {
                    **cell,
                    **{
                        key: cell[key] + rows
                        for key in ("start_row_offset_idx", "end_row_offset_idx")
                        if key in cell
                    },
                }
            )
        rows += int(data.get("num_rows") or used)
        columns = max(columns, int(data.get("num_cols") or 0))
    return {"table_cells": cells, "num_rows": rows, "num_cols": columns} if cells else {}


def _author_text(block: Block) -> str:
    """An author line without affiliation markers, badges or contact details.

    Raised digits, commas, asterisks and daggers point to affiliations, so they go.
    So do the symbols * ∗ † ‡ § ¶ # ¤ and the correspondence envelope ✉ wherever they
    stand, glyphs that are only a link icon (an ORCID badge), and e-mail addresses:
    no name contains them. A cell of an author grid prints the name on its first
    line and the affiliation and e-mail below; only the name line is kept.
    """
    text = block.text
    drop = {
        index
        for span in block.inline
        if span.get("script") == "super"
        for index in range(max(0, span["start"]), min(len(text), span["end"]))
        if text[index].isdigit() or text[index] in ",*∗†‡"
    }
    for icon in block.extra.get("icons") or ():
        drop.update(range(max(0, icon["start"]), min(len(text), icon["end"])))
    starts = [start for start in block.extra.get("lineStarts") or () if 0 < start < len(text)]
    if starts and "@" in text[starts[0]:]:
        drop.update(range(starts[0], len(text)))
    text = "".join(char for index, char in enumerate(text) if index not in drop)
    text = re.sub(r"[^\s(),;]*@[^\s(),;]+", "", text)
    text = re.sub(r"[*∗†‡§¶#¤✉]", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",(?:\s*,)+", ",", text)
    return text.strip(" ,")


def _add_field(metadata: dict, name: str, value: str, anchor: dict | None = None) -> None:
    """Add a value the plan found, in the shape of the declared metadata, once."""
    text = " ".join(value.split())[:2000]
    if not text:
        return
    choices = metadata.setdefault(name, [])
    if not any(c["value"] == text and c["origin"] == "document" for c in choices):
        choice = {"value": text, "origin": "document", "status": "observed"}
        if anchor:
            choice["anchor"] = anchor
        choices.append(choice)


def _cited(anchor: dict) -> dict:
    return {key: anchor[key] for key in ("page", "bboxPpm", "documentRef") if key in anchor}


def _heading_key(text: str) -> str:
    return re.sub(r"[\W\d_]+", "", text.casefold())


# Drop reasons of the draft whose blocks are the document's own declarations.
METADATA_PLACEMENTS = {"dates": "dates", "identifier line": "identifier", "licence": "rights"}
# "Received 3 May 2021; accepted 9 June 2021": a label, then its value up to the next one.
_DECLARED = re.compile(
    r"(?P<label>received|revised|accepted|published(?:\s+online)?|first\s+published|"
    r"available\s+online|publication\s+date|date\s+of\s+publication|issued|"
    r"出版日期|发布日期|接收日期|修订日期|录用日期)\s*(?:on|in)?\s*[:：]?\s*"
    r"(?P<value>.+?)(?=\s*[;,|·]?\s*(?:received|revised|accepted|published|first\s+published|"
    r"available\s+online|publication\s+date|date\s+of\s+publication|issued|出版日期|"
    r"发布日期|接收日期|修订日期|录用日期)\b|\s*[;,|·]?\s+(?:https?://|www\.|doi\b|10\.\d{4,9}/)"
    r"|\s*$)",
    re.I | re.S,
)
_DATE_NAMES = {
    "received": "received", "接收日期": "received", "revised": "revised", "修订日期": "revised",
    "accepted": "accepted", "录用日期": "accepted", "issued": "issued",
}
_YEAR = re.compile(r"\b(?:1[6-9]|20)\d{2}\b")
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.I)


def printed_declarations(text: str, role: str) -> list[tuple[str, str]]:
    """Metadata fields a printed declaration states, word for word.

    Dates keep their printed form and their label (received, accepted, published …);
    only a value holding a year counts. A DOI is the identifier as printed. A notice
    of rights is kept whole. Nothing is inferred from file timestamps here.
    """
    found: list[tuple[str, str]] = []
    if role == "dates":
        for match in _DECLARED.finditer(text):
            value = match["value"].strip(" ;,.|·")
            if not _YEAR.search(value):
                continue
            label = re.sub(r"\s+", " ", match["label"].casefold())
            found.append((_DATE_NAMES.get(label, "published"), value[:120]))
    if role in {"identifier", "dates", "rights"}:
        found += [("doi", doi.rstrip(".,;)")) for doi in _DOI.findall(text)]
    if role == "rights":
        found.append(("rights", text))
    return found


class _Assembly:
    """Walks the plan once in reading order and turns its blocks into elements."""

    def __init__(self, plan: Plan, evidence: Evidence) -> None:
        self.plan, self.evidence = plan, evidence
        self.elements: list[ParsedBlock] = []
        # Open headings as (level, element index, text), the innermost last.
        self.stack: list[tuple[int, int, str]] = []
        self.floats = {item.id: item for item in [*plan.figures, *plan.tables]}
        self.inside: dict[str, list[Block]] = {}
        for block in evidence.blocks:
            if block.inside:
                self.inside.setdefault(block.inside, []).append(block)
        # A joined block continues the block before it; element_of tells where that went.
        self.continues = {
            second: first
            for first, second in plan.joins
            if all(evidence.get(block_id).placeable for block_id in (first, second))
        }
        self.element_of: dict[str, ParsedBlock] = {}
        self.reference_headings: set[str] = set()
        self.repeated: dict[str, str] = {}

    def build(self, status: StructureStatus) -> ParsedDocument:
        plan = self.plan
        if title := self.blocks(plan.title):
            self.emit("heading", title, "front", level=1, role="title", noEmbed=True)
        if subtitle := self.blocks(plan.subtitle):
            self.emit("heading", subtitle, "front", level=2, role="subtitle", noEmbed=True)
        for block in self.blocks(plan.authors):
            self.author(block)
        for drop in plan.drop:
            if drop.reason in METADATA_PLACEMENTS:
                for block_id in _placed(drop.blocks, self.evidence):
                    self.metadata(block_id, drop.reason)
        for part in plan.front:
            self.part(part, "front")
        for section in plan.body:
            self.section(section)
        # Notes and back matter belong to the whole document, not to the last section.
        self.stack.clear()
        for note in plan.notes:
            self.note(note)
        self.stack.clear()
        for part in plan.back:
            self.part(part, "back")
        split_reference_entries(self.elements)
        document = ParsedDocument(self.elements, manifest=self.manifest(status))
        document.validate()
        return document

    def blocks(self, items: list[str]) -> list[Block]:
        found = (self.evidence.get(block_id) for block_id in _placed(items, self.evidence))
        return [block for block in found if block is not None]

    # -- Elements

    def emit(
        self,
        kind: str,
        blocks: list[Block],
        area: str,
        *,
        text: str | None = None,
        spans: list[dict] | None = None,
        anchor: dict | None = None,
        **payload: object,
    ) -> ParsedBlock | None:
        """Append one element made of ``blocks``; values that are None stay out of the payload."""
        offsets = None
        if text is None:
            text, spans, offsets = _joined_parts(blocks)
        elif len(blocks) == 1 and text == blocks[0].text:
            offsets = {blocks[0].id: (0, len(text))}
        if not text and kind not in _VISUAL_KINDS:
            return None
        first = blocks[0]
        base: dict[str, object] = {"part": area, "blockIds": [block.id for block in blocks]}
        if ref := first.extra.get("documentRef"):
            base["documentRef"] = ref
        if first.font_size:
            base["fontSize"] = first.font_size
        if first.marker:
            base["marker"] = first.marker
        if len(blocks) == 1:
            base.update({k: v for k, v in first.extra.items() if k in {"textSource", "sourceText"}})
        checks = {b.extra["glyphCheck"] for b in blocks if b.extra.get("glyphCheck")}
        if "unverified" in checks:
            base["glyphCheck"] = "unverified"
        if spans:
            base["inlineSpans"] = spans
        payload = {**base, **{key: value for key, value in payload.items() if value is not None}}
        if kind == "heading":
            level = int(payload["level"])
            while self.stack and self.stack[-1][0] >= level:
                self.stack.pop()
        parent = self.stack[-1][1] if self.stack else None
        if kind == "heading":
            self.stack.append((level, len(self.elements), text))
        headings = [entry[2] for entry in self.stack]
        anchor = _anchor(blocks, offsets) if anchor is None else anchor
        page = anchor.get("page")
        locator = f"Page {page}" if page else (" / ".join(headings) or "Source text")
        element = ParsedBlock(text, kind, parent, headings, anchor, locator, payload)
        self.elements.append(element)
        for block in blocks:
            self.element_of[block.id] = element
        return element

    def author(self, block: Block) -> None:
        text = _author_text(block)
        self.emit(
            "metadata",
            [block],
            "front",
            text=text,
            # Offsets of the original line do not fit the tidied one.
            spans=_joined([block])[1] if text == block.text else None,
            role="authors",
            noEmbed=True,
            sourceText=block.text,
        )

    def part(self, part: Part, area: str) -> None:
        heading = self.blocks([part.heading] if part.heading else [])
        if heading and part.part == "references":
            name = _heading_key(heading[0].text)
            if name in self.reference_headings:
                # The bibliography's heading printed again (on its next page): its entries
                # go on in the same list, under the one heading.
                self.repeated[heading[0].id] = "repeated references heading"
                heading = []
            self.reference_headings.add(name)
        if heading:
            self.emit("heading", heading, area, level=part.level, role=part.part, noEmbed=True)
        kind, role = None, part.part
        if area == "front":
            kind = "metadata" if part.part == "keywords" else None
        elif part.part == "references":
            kind, role = "reference", None
        elif part.part == "notes":
            kind, role = "note", "note"
        elif part.part == "appendix":
            role = None
        for block_id in _placed(part.blocks, self.evidence):
            self.place(block_id, area, role, kind)

    def section(self, section: Section) -> None:
        if heading := self.blocks([section.heading] if section.heading else []):
            self.emit("heading", heading, "body", level=section.level, noEmbed=True)
        for block_id in _placed(section.blocks, self.evidence):
            self.place(block_id, "body", None, None)

    def note(self, note: Note) -> None:
        blocks = self.blocks(note.blocks)
        if blocks:
            text, spans = _joined(blocks, _after_sentence)
            self.emit(
                "note", blocks, "notes", text=text, spans=spans, role="note", marker=note.marker
            )

    def place(self, block_id: str, area: str, role: str | None, kind: str | None) -> None:
        block = self.evidence.get(block_id)
        if block is not None:
            self.block(block, area, role, kind)
        elif isinstance(item := self.floats.get(block_id), Figure):
            self.figure(item, area)
        elif item is not None:
            self.table(item, area)

    def block(self, block: Block, area: str, role: str | None, kind: str | None) -> None:
        """One block as the element its label calls for; ``kind`` is set by its part."""
        first = self.continues.get(block.id)
        if first in self.element_of:
            self.continue_element(self.element_of[first], block)
            return
        label = block.label
        if label == "formula":
            self.equation(block, area)
        elif label == "table":
            self.table(Table(id="", table=[block.id]), area)
        elif label == "picture":
            inside = "\n".join(b.text for b in self.inside.get(block.id, []) if b.text.strip())
            self.emit("figure", [block], area, imageKey=block.image_key, figureText=inside or None)
        else:
            kind = kind or {"list_item": "list_item", "code": "code"}.get(label, "paragraph")
            flags = {"noEmbed": True} if kind in {"reference", "metadata"} else {}
            if kind == "reference":
                flags["reference"] = True
            self.emit(kind, [block], area, role=role, **flags)

    def equation(self, block: Block, area: str) -> None:
        """A formula's picture is the formula; its glyphs are its text only when they read
        as one line and every glyph was checked against the print.

        Otherwise the glyphs stay with the element as an unchecked text layer, out of the
        text that is searched, copied and cited, and the element says why.
        """
        layout = block.extra.get("formulaLayout")
        check = block.extra.get("glyphCheck")
        written = bool(block.text.strip())
        if not written:
            transcription, issue = "unavailable", "no text layer"
        elif check == "uncertain":
            transcription, issue = "unreliable", "unreadable glyphs"
        elif layout == "two-dimensional":
            transcription, issue = "unreliable", "two-dimensional layout"
        elif layout != "linear" or check not in {"verified", "decoded"}:
            transcription, issue = "unreliable", "glyphs not checked"
        else:
            transcription, issue = "available", None
        shown = transcription == "available"
        self.emit(
            "equation",
            [block],
            area,
            text=block.text if shown else "",
            spans=block.inline if shown else None,
            imageKey=block.image_key,
            transcription=transcription,
            transcriptionIssue=issue,
            textLayer=None if shown or not written else block.text,
            glyphCheck=check,
            equationNumber=block.extra.get("equationNumber"),
            noEmbed=None if shown else True,
        )

    def metadata(self, block_id: str, reason: str) -> None:
        """Dates, identifiers and notices printed in the document: its own declarations,
        kept as metadata elements (listed with the document's details, not read inline)."""
        for block in self.blocks([block_id]):
            self.emit(
                "metadata", [block], "front", role=METADATA_PLACEMENTS[reason], noEmbed=True
            )

    def continue_element(self, element: ParsedBlock, block: Block) -> None:
        """A paragraph split by a column or page break is one element again."""
        last = self.evidence.get(element.payload["blockIds"][-1])
        # A word hyphenated across the break is one word again.
        gap = "" if last is not None and last.extra.get("joinsNext") else " "
        offset = len(element.text) + len(gap)
        element.text = f"{element.text}{gap}{block.text}"
        if block.inline:
            spans = element.payload.setdefault("inlineSpans", [])
            spans.extend(_shifted(span, offset) for span in block.inline)
        element.payload["blockIds"].append(block.id)
        element.payload.pop("sourceText", None)
        if block.extra.get("glyphCheck") == "unverified":
            element.payload["glyphCheck"] = "unverified"
        if block.regions:
            element.anchor.setdefault("spans", []).extend(
                _regions([block], {block.id: (offset, len(element.text))})
            )
        self.element_of[block.id] = element

    def figure(self, figure: Figure, area: str) -> None:
        images, labels = self.blocks(figure.images), self.blocks(figure.labels)
        captions = self.blocks(figure.caption)
        caption, spans, offsets = _joined_parts(captions, _after_sentence)
        first = images[0]
        around = [*images, *labels]
        anchor = _anchor(around)
        # The caption's own regions, on whatever page it is printed, with its text spans.
        anchor.setdefault("spans", []).extend(_regions(captions, offsets, role="caption"))
        inner = [b for image in images for b in self.inside.get(image.id, [])]
        # The crop covers the picture, the labels printed around it and the text the
        # layout read as printed in it (tick labels, a legend), on its page.
        if first.box and first.page is not None:
            anchor["bboxPpm"] = _union([*around, *inner], first.page)
            anchor["cropSource"] = "plan-figure"
        shown = [*labels, *inner]
        self.emit(
            "figure",
            [*images, *captions, *labels],
            area,
            text=caption,
            spans=spans,
            anchor=anchor,
            number=figure.number,
            caption=caption,
            imageKey=next((b.image_key for b in images if b.image_key), None),
            imageBlocks=[block.id for block in images],
            captionBlocks=[block.id for block in captions],
            figureText="\n".join(b.text for b in shown if b.text.strip()),
        )

    def table(self, table: Table, area: str) -> None:
        blocks = self.blocks(table.table)
        captions, footnotes = self.blocks(table.caption), self.blocks(table.footnotes)
        grids = [block for block in blocks if block.label == "table"]
        pictures = [block for block in blocks if block.label == "picture"]
        data = _grid(grids) if grids else {}
        text = table_text(data)
        caption, spans, offsets = _joined_parts(captions, _after_sentence)
        cells = text or "\n".join(b.text for b in grids if b.text.strip())
        anchor = _anchor(blocks)
        anchor.setdefault("spans", []).extend([
            *_regions(captions, offsets, role="caption"), *_regions(footnotes, role="footnote"),
        ])
        self.emit(
            "table",
            [*blocks, *captions, *footnotes],
            area,
            # Search reads element text only; the caption says what the cells are. The
            # caption comes first, so its inline spans hold for the element text too.
            text="\n\n".join(part for part in (caption, cells) if part),
            spans=spans,
            anchor=anchor,
            number=table.number,
            caption=caption,
            footnotes="\n".join(b.text for b in footnotes if b.text.strip()),
            table=data or None,
            imageKey=pictures[0].image_key if pictures else None,
        )

    # -- Manifest

    def omissions(self) -> list[dict]:
        """One record per source block left out of the document, with its reason.

        Dropped blocks (page furniture, decoration, …), repeated headings, and text
        printed inside a picture that was itself left out. Blocks placed as metadata
        are not omitted: they are elements.
        """
        records = []
        reasons: dict[str, str] = {}
        for drop in self.plan.drop:
            if drop.reason in METADATA_PLACEMENTS:
                continue
            for block_id in expand(drop.blocks, self.evidence):
                reasons.setdefault(block_id, drop.reason)
        reasons.update(self.repeated)
        for picture, inside in self.inside.items():
            if picture in reasons:
                for block in inside:
                    reasons.setdefault(block.id, "printed inside a picture left out: "
                                       + reasons[picture])
        for block in self.evidence.blocks:
            reason = reasons.get(block.id)
            if reason is None or block.id in self.element_of:
                continue
            record = {"block": block.id, "label": block.label, "reason": reason,
                      "chars": len(block.text), "text": block.text[:120]}
            if ref := block.extra.get("documentRef"):
                record["documentRef"] = ref
            if block.page is not None:
                record["page"] = block.page
            if block.box:
                left, top, right, bottom = block.box
                record["bboxPpm"] = [left, top, right - left, bottom - top]
            records.append(record)
        return records

    def accounting(self, omissions: list[dict]) -> dict:
        """Every source block counted once: placed, printed inside a figure, or omitted."""
        omitted = {record["block"] for record in omissions}
        placed = set(self.element_of) - omitted
        shown_inside = {
            block.id for picture, inside in self.inside.items() if picture in self.element_of
            for block in inside
        }
        counts = {
            "blocks": len(self.evidence.blocks),
            "placed": len(placed),
            "insideFigures": len(shown_inside - placed - omitted),
            "omitted": len(omitted),
        }
        counts["unaccounted"] = counts["blocks"] - counts["placed"] - counts["insideFigures"] - (
            counts["omitted"]
        )
        return counts

    def manifest(self, status: StructureStatus) -> dict:
        outline = [
            {
                "ordinal": ordinal,
                "title": element.text,
                "level": element.payload["level"],
                "anchor": dict(element.anchor),
                "status": "observed",
            }
            for ordinal, element in enumerate(self.elements)
            if element.kind == "heading" and element.payload.get("role") != "title"
        ]
        metadata = copy.deepcopy(self.evidence.metadata)
        for element in self.elements:
            if element.payload.get("role") in {"title", "authors"}:
                _add_field(metadata, element.payload["role"], element.text)
            elif element.kind == "metadata":
                for name, value in printed_declarations(element.text, element.payload["role"]):
                    _add_field(metadata, name, value, anchor=_cited(element.anchor))
        manifest: dict[str, object] = {
            "schema": 2,
            "method": "docling",
            "documentType": self.plan.type,
            "structure": status.to_json(),
            "outline": outline,
            "metadata": metadata,
            "bodyFontSize": self.evidence.body_font_size,
            # The file's physical pages, not the last page anything was placed on.
            "pageCount": self.evidence.manifest.get("pageCount") or len(self.evidence.pages),
            "images": "pending",
            "omissions": self.omissions(),
        }
        manifest["accounting"] = self.accounting(manifest["omissions"])
        for key, value in self.evidence.manifest.items():
            manifest.setdefault(key, value)
        return manifest


def extraction_report(document: ParsedDocument) -> dict:
    """What the stored document cannot vouch for, apart from how it was structured.

    ``complete`` only when every check found nothing to report; otherwise ``partial``
    with one warning per kind of problem, counted, with the pages it affects.
    Omitted blocks are listed in the manifest's ``omissions``; they are counted here.
    """
    manifest = document.manifest
    warnings: list[dict] = []

    def warn(kind: str, message: str, elements: list[ParsedBlock], count: int | None = None):
        pages = sorted(
            {e.anchor["page"] for e in elements if isinstance(e.anchor.get("page"), int)}
        )
        total = count if count is not None else len(elements)
        warnings.append({"kind": kind, "message": message, "count": total, "pages": pages[:50]})

    marked: dict[str, list[ParsedBlock]] = {"decoded": [], "uncertain": []}
    glyphs = Counter()
    for element in document.elements:
        spans = element.payload.get("inlineSpans") or []
        for kind in marked:
            found = sum(s["end"] - s["start"] for s in spans if s.get("mark") == kind)
            if found:
                marked[kind].append(element)
                glyphs[kind] += found
    if marked["uncertain"]:
        warn("glyphs-uncertain",
             "Some printed characters could not be read from the PDF's text layer; they are "
             "shown as \ufffd.", marked["uncertain"], glyphs["uncertain"])
    if marked["decoded"]:
        warn("glyphs-decoded",
             "Some characters were named wrongly by the PDF's fonts and were read from their "
             "printed shapes instead.", marked["decoded"], glyphs["decoded"])
    unverified = [e for e in document.elements if e.payload.get("glyphCheck") == "unverified"]
    if unverified:
        warn("glyphs-unverified",
             "Some text keeps the layout parser's reading although the glyph check found "
             "misnamed glyphs in it.", unverified)
    check = manifest.get("glyphCheck")
    if isinstance(check, dict) and check.get("error") or manifest.get("nativeTextWarning"):
        warn("glyphs-unchecked", "The text layer could not be checked against the print.", [], 0)
    equations = [
        e for e in document.elements
        if e.kind == "equation" and e.payload.get("transcription") != "available"
    ]
    if equations:
        warn("equations-image-only",
             "Some equations are kept as images only: their text could not be read faithfully.",
             equations)
    clipped = [
        e for e in document.elements if (e.payload.get("cropCheck") or {}).get("state") == "clipped"
    ]
    if clipped:
        warn("figures-clipped",
             "Some figure images may be incomplete: printed content runs past their edges.",
             clipped)
    missing = int(manifest.get("missingImages") or 0)
    if missing:
        warn("images-missing", "Some figure images could not be rendered.", [], missing)
    forms = [f for f in manifest.get("formFields") or [] if f.get("placed") == "none"]
    if forms:
        warnings.append({
            "kind": "form-unplaced", "count": len(forms),
            "message": "Some form selections or answers could not be placed in the text.",
            "pages": sorted({f["page"] for f in forms})[:50],
        })
    pages, read = manifest.get("pageCount"), manifest.get("layoutPageCount")
    if isinstance(pages, int) and isinstance(read, int) and read < pages:
        warnings.append({
            "kind": "pages-unread", "count": pages - read, "pages": [],
            "message": f"The layout parser read {read} of the file's {pages} pages.",
        })
    accounting = manifest.get("accounting") or {}
    if accounting.get("unaccounted"):
        warnings.append({
            "kind": "blocks-unaccounted", "count": accounting["unaccounted"], "pages": [],
            "message": "Some source blocks were neither placed nor recorded as omitted.",
        })
    omitted = Counter(record["reason"] for record in manifest.get("omissions") or [])
    return {
        "state": "partial" if warnings else "complete",
        "warnings": warnings,
        "omitted": dict(omitted.most_common()),
    }
