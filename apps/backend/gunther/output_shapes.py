"""Structural formats and capabilities shared by Studio builds and saved outputs."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal

from gunther import outputs_cards
from gunther.studio import format as fmt

ShapeId = Literal["report", "slides", "cards"]
OutputPipeline = Literal["planned", "direct"]
FENCE = re.compile(r"^\s*(```|~~~)")


def markdown_lines(content: str, *, keepends: bool = False) -> list[tuple[str, bool]]:
    """Each line of a document, and whether it is inside a code fence."""
    fenced, out = False, []
    lines = re.split(r"(?<=\n)|(?<=\r)(?!\n)", content)
    for raw in lines:
        line = raw if keepends else raw.rstrip("\r\n")
        if FENCE.match(line):
            out.append((line, True))
            fenced = not fenced
        else:
            out.append((line, fenced))
    return out


def markdown_spans(
    content: str, *, separator: str | None = None,
) -> tuple[int, list[tuple[int, int]]]:
    """Preamble end and trimmed unit ranges, split at headings or a separator."""
    parts = []
    start = offset = 0
    for line, fenced in markdown_lines(content, keepends=True):
        end = offset + len(line)
        if not fenced and separator is not None and line.strip() == separator:
            parts.append((start, offset))
            start = end
        elif not fenced and separator is None and line.startswith("## "):
            parts.append((start, offset))
            start = offset
        offset = end
    parts.append((start, offset))
    preamble = parts.pop(0)[1] if separator is None else 0
    spans = []
    for start, end in parts:
        raw = content[start:end]
        if raw.strip():
            spans.append((end - len(raw.lstrip()), start + len(raw.rstrip())))
    return preamble, spans


def split_spans(content: str, spans: Callable) -> tuple[str, list[str]]:
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    preamble, ranges = spans(content)
    return content[:preamble].strip(), [content[start:end] for start, end in ranges]


def split_headed(content: str) -> tuple[str, list[str]]:
    return split_spans(content, markdown_spans)


def join_headed(preamble: str, units: Sequence[str]) -> str:
    return "\n\n".join(part for part in (preamble, *units) if part)


def join_slides(_preamble: str, units: Sequence[str]) -> str:
    return "\n\n---\n\n".join(units)


def lend_refs(line: str, rest: str, most: int = 6) -> str:
    """Add the citations that other slides use for the same figures, for checking only."""
    plain = re.sub(r"\s*\[\d+\]", "", line)
    wanted = fmt.numbers_in(plain)
    if not wanted:
        return line
    lent: list[str] = []
    for other in rest.split("\n"):
        cited = re.findall(r"\[(\d+)\]", other)
        if cited and wanted & fmt.numbers_in(re.sub(r"\s*\[\d+\]", "", other)):
            lent += cited
    own = re.findall(r"\[(\d+)\]", line)
    extra = [n for n in dict.fromkeys(lent) if n not in own][:most]
    return line.rstrip() + "".join(f" [{n}]" for n in extra) if extra else line


def prose_check_text(
    unit: str, *, refs: Mapping[int, object] | None = None, rest: str | None = None,
) -> list[str]:
    # The layout reader is also used by assembly, which depends on the shape catalog.
    from gunther.outputs import _marked, check_heading_refs

    marked = _marked(unit)
    body = marked.body
    lines = body.split("\n")
    if rest is not None and marked.layout == "title":
        # The cover's meta line (audience · date) states no claim.
        content = [n for n, line in enumerate(lines) if line.strip()]
        if len(content) >= 3:
            lines[content[2]] = ""
        lines = [lend_refs(line, rest) for line in lines]
    return [check_heading_refs(body, lines, refs)]


@dataclass(frozen=True)
class ShapeSpec:
    id: ShapeId
    default_type: str
    name: str
    what: str
    unit_label: str
    max_parts: int
    pipeline: OutputPipeline
    spans: Callable[[str], tuple[int, list[tuple[int, int]]]]
    join: Callable[[str, Sequence[str]], str]
    format_check: Callable[..., list[str] | list[tuple[int, list[str]]]]
    check_text: Callable[..., list[str]]
    exports: tuple[str, ...]
    header: str = ""
    unit_problem: Callable[[str], str | None] | None = None

    def split(self, content: str) -> tuple[str, list[str]]:
        return split_spans(content, self.spans)

    def preamble(self, type_id: str) -> str:
        return self.header.format(type_id=type_id)


SHAPES = {
    shape.id: shape
    for shape in (
        ShapeSpec(
            "report", "report", "report", "a report", "section", 8, "planned",
            markdown_spans, join_headed, fmt.check_report, prose_check_text,
            ("markdown", "pdf", "docx"),
        ),
        ShapeSpec(
            "slides", "slides", "deck", "a slide deck", "slide", 14, "planned",
            partial(markdown_spans, separator="---"), join_slides, fmt.check_deck,
            prose_check_text, ("markdown", "pdf", "pptx"),
        ),
        ShapeSpec(
            "cards", "flashcards", "card set", "a card set", "card", 40, "direct",
            markdown_spans, join_headed, outputs_cards.check_cards, outputs_cards.check_text,
            ("markdown", "pdf"), header=outputs_cards.HEADER,
            unit_problem=outputs_cards.card_problem,
        ),
    )
}
