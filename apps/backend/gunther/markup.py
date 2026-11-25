"""Locate Markdown code, math and diagrams in the original source text."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from xml.etree.ElementTree import ParseError, fromstring

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline, backtick
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.dollarmath.index import math_block_dollar, math_inline_dollar

Span = tuple[int, int]
_NEWLINE = re.compile(r"\r\n?|\n")
_CELL_SEPARATOR = re.compile(r"(?<!\\)\|")
_MATH_DELIMITER = re.compile(r"(?<!\\)(?:\\\\)*\$\$")
_BLOCKS = {"fence", "code_block", "math_block"}
_parse_inline_math = math_inline_dollar(allow_double=True)
_parse_block_math = math_block_dollar(allow_labels=False)


def _double_dollar(state: StateInline, silent: bool) -> bool:
    return state.src.startswith("$$", state.pos) and _parse_inline_math(state, silent)


def _math_block(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    line, count = state.line, len(state.tokens)
    if not _parse_block_math(state, start, end, False):
        return False
    # Two inline formulas with prose between them do not make a display block.
    whole = not _MATH_DELIMITER.search(state.tokens[-1].content)
    if silent or not whole:
        # Paragraph lookahead inspects the emitted token but leaves the state untouched.
        state.line = line
        del state.tokens[count:]
    return whole


def _positioned(rule: Callable[[StateInline, bool], bool]) -> Callable[[StateInline, bool], bool]:
    def parse(state: StateInline, silent: bool) -> bool:
        start, count = state.pos, len(state.tokens)
        matched = rule(state, silent)
        if matched and not silent and len(state.tokens) > count:
            state.tokens[-1].meta["source_span"] = (start, state.pos)
        return matched

    return parse


# Parse blocks first for their line maps. Then scan inline source windows for character positions.
_MARKDOWN = MarkdownIt().enable("table").use(dollarmath_plugin).disable("inline")
_MARKDOWN.inline.ruler.at("backticks", _positioned(backtick))
_MARKDOWN.inline.ruler.at("math_inline", _positioned(_double_dollar))
_MARKDOWN.block.ruler.at(
    "math_block", _math_block,
    {"alt": ["paragraph", "reference", "blockquote", "list"]},
)


@dataclass(frozen=True)
class Block:
    start: int
    end: int
    kind: str
    body: str
    language: str = ""


@dataclass(frozen=True)
class Markup:
    text: str
    blocks: tuple[Block, ...]
    inline: tuple[Span, ...]

    def prose_slice(self, start: int, end: int) -> Markup:
        """Keep inline boundaries when taking a slice of a block-free prose range."""
        inline = tuple(
            (max(left, start) - start, min(right, end) - start)
            for left, right in self.inline if left < end and start < right
        )
        return Markup(self.text[start:end], (), inline)

    def prose_ranges(self, *, include_inline: bool = False) -> Iterator[Span]:
        protected = [(b.start, b.end) for b in self.blocks]
        if not include_inline:
            protected.extend(self.inline)
        start = 0
        for begin, end in sorted(protected):
            if start < begin:
                yield start, begin
            start = max(start, end)
        if start < len(self.text):
            yield start, len(self.text)

    def map_prose(
        self, transform: Callable[[str], str], *,
        include_inline: bool = False, lookbehind: bool = False,
    ) -> str:
        chunks = []
        last = 0
        for start, end in self.prose_ranges(include_inline=include_inline):
            # Spacing after inline markup can depend on its closing delimiter.
            begin = start - 1 if lookbehind and start else start
            rewritten = transform(self.text[begin:end])[start - begin:]
            chunks.extend((self.text[last:start], rewritten))
            last = end
        return "".join((*chunks, self.text[last:]))


def _is_svg(content: str) -> bool:
    if not content.lstrip().startswith("<"):
        return False
    try:
        return fromstring(content).tag in {"svg", "{http://www.w3.org/2000/svg}svg"}
    except (ParseError, UnicodeError):
        return False


def parse_markup(text: str | Markup) -> Markup:
    """Read protected blocks and inline spans, with half-open offsets into ``text``."""
    if isinstance(text, Markup):
        return text
    starts = [0, *(m.end() for m in _NEWLINE.finditer(text)), len(text)]
    env: dict = {}
    blocks: list[Block] = []
    inline: list[Span] = []
    table_rows: set[Span] = set()
    tokens = _MARKDOWN.parse(text, env)
    for index, token in enumerate(tokens):
        if token.map is None:
            continue
        start, end = (starts[line] for line in token.map)
        kind = token.type
        if kind in _BLOCKS:
            info = token.info.split(None, 1)
            blocks.append(Block(start, end, kind, token.content, info[0] if info else ""))
        elif (kind == "html_block" or (
            kind == "inline" and tokens[index - 1].type == "paragraph_open"
            and token.map[1] == token.map[0] + 1
        )) and _is_svg(token.content):
            blocks.append(Block(start, end, "svg", token.content))
        elif kind == "tr_open":
            table_rows.add((start, end))
            # A delimiter in one GFM table cell cannot close a span in another cell.
            cell = start
            for pipe in _CELL_SEPARATOR.finditer(text, start, end):
                inline.extend(_inline_spans(text, cell, pipe.start(), env))
                cell = pipe.end()
            inline.extend(_inline_spans(text, cell, end, env))
        elif kind == "inline" and (start, end) not in table_rows:
            inline.extend(_inline_spans(text, start, end, env))
    return Markup(text, tuple(blocks), tuple(inline))


def _inline_spans(text: str, start: int, end: int, env: dict) -> Iterator[Span]:
    source = text[start:end]
    if "`" not in source and "$$" not in source:
        return
    for token in _MARKDOWN.inline.parse(source, _MARKDOWN, env, []):
        if span := token.meta.get("source_span"):
            yield start + span[0], start + span[1]


def inline_markup(text: str, windows: Iterable[Span]) -> Markup:
    """Read inline markup in labels whose boundaries come from a structured format."""
    inline = tuple(span for start, end in windows for span in _inline_spans(text, start, end, {}))
    return Markup(text, (), inline)


def markdown_lines(content: str | Markup, *, keepends: bool = False) -> list[tuple[str, bool]]:
    """Each source line and whether it belongs to a protected block."""
    markup = parse_markup(content)
    blocks = iter(markup.blocks)
    block = next(blocks, None)
    offset, out = 0, []
    for raw in re.split(r"(?<=\n)|(?<=\r)(?!\n)", markup.text):
        while block is not None and offset >= block.end:
            block = next(blocks, None)
        line = raw if keepends else raw.rstrip("\r\n")
        out.append((line, block is not None and block.start <= offset))
        offset += len(raw)
    return out


def prose_lines(content: str | Markup) -> Iterator[tuple[str, Markup | None]]:
    """Each original line and its markup, with no slice for lines in a protected block."""
    markup = parse_markup(content)
    start = 0
    for line, protected in markdown_lines(markup, keepends=True):
        end = start + len(line)
        yield line, None if protected else markup.prose_slice(start, end)
        start = end
