"""Structural formats and capabilities shared by Studio builds and saved outputs."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal

from gunther import outputs_audio, outputs_cards, outputs_map
from gunther.studio import format as fmt

ShapeId = Literal["report", "slides", "cards", "map", "audio"]
OutputPipeline = Literal["planned", "direct"]
OutputExport = Literal["markdown", "pdf", "docx", "pptx", "wav"]
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


def line_spans(
    content: str, *, starts: Callable[[str], bool],
) -> tuple[int, list[tuple[int, int]]]:
    spans, offset = [], 0
    for raw, _ in markdown_lines(content, keepends=True):
        if raw.strip() and (spans or starts(raw)):
            spans.append((offset, offset + len(raw.rstrip())))
        offset += len(raw)
    return spans[0][0] if spans else offset, spans


def join_lines(preamble: str, lines: Sequence[str]) -> str:
    return "\n".join(part for part in (preamble, *lines) if part)


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
class OutputUnit:
    text: str
    ancestors: tuple[str, ...] = ()
    label: str | None = None


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
    grammar: Callable[..., list[str] | list[tuple[int, list[str]]]]
    project: Callable[..., list[str]]
    exports: tuple[OutputExport, ...]
    outline: bool = False
    design: bool = False
    header: str = ""
    unit_problem: Callable[[str], str | None] | None = None
    node: Callable[[str], tuple[int, str] | None] | None = None
    batch_checks: bool = False
    speech: Callable[[str], tuple[str, str]] | None = None

    def split(self, content: str) -> tuple[str, list[OutputUnit]]:
        preamble, texts = split_spans(content, self.spans)
        return preamble, self.units(texts)

    def units(self, texts: Sequence[str]) -> list[OutputUnit]:
        result, path = [], []
        for text in texts:
            parsed = self.node(text) if self.node else None
            if parsed is None:
                result.append(OutputUnit(text))
                continue
            depth, label = parsed
            del path[depth:]
            result.append(OutputUnit(text, tuple(path), label))
            path.append(label)
        return result

    def check_text(self, unit: str | OutputUnit, **options) -> list[str]:
        if isinstance(unit, OutputUnit):
            if unit.ancestors:
                options["ancestors"] = unit.ancestors
            unit = unit.text
        return self.project(unit, **options)

    def limit_error(self, count: int, label: str, limit: int) -> str | None:
        if count > limit:
            return f"The limit for {label} is {limit} {self.unit_label}s."
        return None

    def format_check(self, content: str, *, part_limit: tuple[str, int] | None = None, **options):
        problems = self.grammar(content, **options)
        if self.pipeline == "direct":
            label, limit = part_limit or (self.name.capitalize(), self.max_parts)
            if error := self.limit_error(len(self.spans(content)[1]), label, limit):
                return [*problems, error]
        return problems

    def subtree_end(self, parts: Sequence[str], start: int) -> int:
        root = self.node(parts[start]) if self.node else None
        if root is not None:
            for at in range(start + 1, len(parts)):
                parsed = self.node(parts[at])
                if parsed is None or parsed[0] <= root[0]:
                    return at
            return len(parts)
        return start + 1

    def at_depth(self, root: str, units: Sequence[str]) -> list[str]:
        indent = root[:len(root) - len(root.lstrip())] if self.node else ""
        return [indent + unit for unit in units]

    def preamble(self, type_id: str) -> str:
        return self.header.format(type_id=type_id)


SHAPES = {
    shape.id: shape
    for shape in (
        ShapeSpec(
            "report", "report", "report", "a report", "section", 8, "planned",
            markdown_spans, join_headed, fmt.check_report, prose_check_text,
            ("markdown", "pdf", "docx"), outline=True,
        ),
        ShapeSpec(
            "slides", "slides", "deck", "a slide deck", "slide", 14, "planned",
            partial(markdown_spans, separator="---"), join_slides, fmt.check_deck,
            prose_check_text, ("markdown", "pdf", "pptx"), outline=True, design=True,
        ),
        ShapeSpec(
            "cards", "flashcards", "card set", "a card set", "card", 40, "direct",
            markdown_spans, join_headed, outputs_cards.check_cards, outputs_cards.check_text,
            ("markdown", "pdf"), header=outputs_cards.HEADER,
            unit_problem=outputs_cards.card_problem,
        ),
        ShapeSpec(
            "map", "mind_map", "mind map", "a mind map", "node", 60, "direct",
            outputs_map.node_spans, join_lines, outputs_map.check_map,
            outputs_map.check_text, ("markdown", "pdf"), header=outputs_map.HEADER,
            node=outputs_map.node_of,
        ),
        ShapeSpec(
            "audio", "audio_overview", "audio overview", "an audio overview", "line", 120, "direct",
            outputs_audio.spans, join_lines, outputs_audio.check_audio, outputs_audio.check_text,
            ("markdown", "wav"),
            header=outputs_audio.HEADER, unit_problem=outputs_audio.line_problem,
            batch_checks=True, speech=outputs_audio.speech,
        ),
    )
}
