"""Citation markers and the boundaries of citable text."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence

from gunther.markup import Markup, Span, parse_markup

UNSOURCED = "[?]"
SENTENCE_END = re.compile(r"[.!?](?=\s|$)|[。！？]|\r\n?|\n")
_MARKDOWN_ROW = r"^[ \t]*\|[^\r\n]*"
_MARKDOWN_TABLE = re.compile(rf"(?:{_MARKDOWN_ROW}(?:\r\n?|\n|\Z))+", re.M)
_SENTENCE_OR_ROW = re.compile(rf"{_MARKDOWN_ROW}|{SENTENCE_END.pattern}", re.M)
# [i:3] is an inference, [p:3] is partly supported, [d:3] is disputed.
_NUMBERED = r"\[(?:([ipd]):(\d+)|(\d+(?:\s*[,，、]\s*\d+)*))\]"
_CITED = re.compile(_NUMBERED)
_BARE = re.compile(r"\[(\d+(?:\s*[,，、]\s*\d+)*)\]")
_SINGLE = re.compile(r"\[(\d+)\]")
_RUN = re.compile(rf"{_NUMBERED}(?:[ \t]*{_NUMBERED})*")
_OWN = re.compile(re.escape(UNSOURCED))
_MARK = rf"(?:{_NUMBERED}|{_OWN.pattern})"
_MARKERS = re.compile(_MARK)
_TRAILING = re.compile(rf"(?:[ \t]*{_MARK})+")
_CONTINUING = re.compile(rf"(?:[ \t]*(?:(?:\r\n?|\n)[ \t]*)?{_MARK})+")
_NO_PROSE = re.compile(r"^[\x00\s|:\-–—*_#>=+`~.!?。！？]*$")


def _matches(markup: Markup, pattern: re.Pattern[str]) -> Iterator[re.Match[str]]:
    for start, end in markup.prose_ranges():
        yield from pattern.finditer(markup.text, start, end)


def _refs(match: re.Match[str]) -> list[str]:
    return [part.strip() for part in re.split(r"[,，、]", match[2] or match[3])]


def citation_numbers(text: str | Markup, *, bare_only: bool = False) -> list[int]:
    """The source numbers in prose, in order of first use."""
    return list(dict.fromkeys(
        int(ref) for match in _matches(parse_markup(text), _CITED)
        if not bare_only or not match[1] for ref in _refs(match)
    ))


def has_unsourced(text: str | Markup) -> bool:
    if isinstance(text, str) and UNSOURCED not in text:
        return False
    return next(_matches(parse_markup(text), _OWN), None) is not None


def has_checker_marks(text: str) -> bool:
    return any(m[1] or m[0] == UNSOURCED for m in _matches(parse_markup(text), _MARKERS))


def has_markers(text: str) -> bool:
    return next(marker_spans(text), None) is not None


def marker_spans(text: str) -> Iterator[Span]:
    """The offsets of citation and unsourced markers in prose."""
    for match in _matches(parse_markup(text), _MARKERS):
        yield match.span()


def trailing_markers_end(text: str, start: int) -> int:
    for begin, end in parse_markup(text).prose_ranges():
        if begin <= start < end:
            match = _TRAILING.match(text, start, end)
            return match.end() if match else start
    return start


def markdown_tables(text: str | Markup) -> Iterator[re.Match[str]]:
    """Find Markdown pipe tables outside protected blocks, keeping their positions."""
    markup = parse_markup(text)
    for start, end in markup.prose_ranges(include_inline=True):
        yield from _MARKDOWN_TABLE.finditer(markup.text, start, end)


def strip_citations(
    text: str | Markup, *, bare_only: bool = False, include_unsourced: bool = True,
    trim_space: bool = False, replacement: str = "",
) -> str:
    numbered = _BARE.pattern if bare_only else _NUMBERED
    markers = rf"(?:{numbered}|{_OWN.pattern})" if include_unsourced else numbered
    pattern = re.compile(rf"[ \t]*{markers}" if trim_space else markers)
    return parse_markup(text).map_prose(lambda prose: pattern.sub(replacement, prose))


def rewrite_citations(
    text: str | Markup, rewrite: Callable[[str, list[str], str], str], *, trim_space: bool = False,
) -> str:
    """Rewrite each numbered marker from its label, numbers and optionally one preceding space."""
    pattern = re.compile(rf"[ \t]?{_NUMBERED}") if trim_space else _CITED

    def replace(match: re.Match[str]) -> str:
        lead = match[0][:1] if match[0][0] in " \t" else ""
        return rewrite(match[1] or "", _refs(match), lead)

    return parse_markup(text).map_prose(lambda prose: pattern.sub(replace, prose))


def check_citations(content: str | Markup, valid: set[int]) -> tuple[str, list[int]]:
    """Drop nonexistent citations and expand groups, keeping the pool's original numbering."""
    used: dict[int, None] = {}

    def replace(match: re.Match[str]) -> str:
        numbers = [int(part) for part in _refs(match)]
        kept = [number for number in numbers if number in valid]
        used.update(dict.fromkeys(kept))
        if match[1]:
            return match[0] if kept else ""
        return "".join(f"[{number}]" for number in kept)

    def clean(prose: str) -> str:
        prose = _CITED.sub(replace, prose)
        # Clean spacing within a line while keeping its Markdown indentation.
        prose = re.sub(r"(?<=\S)[ \t]{2,}", " ", prose)
        return re.sub(r"(?<=\S)[ \t]+([,.;:!?，。；：！？])", r"\1", prose)

    return parse_markup(content).map_prose(clean, lookbehind=True), list(used)


def collapse_citations(text: str) -> str:
    """Deduplicate adjacent citations, keeping the first label for each number."""

    def tidy(run: re.Match[str]) -> str:
        first: dict[int, str] = {}
        for marker in _CITED.finditer(run[0]):
            for part in _refs(marker):
                first.setdefault(int(part), marker[1] or "")
        return "".join(f"[{label}:{n}]" if label else f"[{n}]" for n, label in first.items())

    return parse_markup(text).map_prose(lambda prose: _RUN.sub(tidy, prose))


def renumber_citations(text: str | Markup, used: Sequence[int] | Mapping[int, int]) -> str:
    """Show the pool's numbers in order of first use, or use a saved version's map."""
    place = used if isinstance(used, Mapping) else {ref: n for n, ref in enumerate(used, 1)}

    def renumber(label: str, refs: list[str], lead: str) -> str:
        prefix = f"{label}:" if label else ""
        return "".join(f"[{prefix}{place.get(int(ref), ref)}]" for ref in refs)

    return rewrite_citations(text, renumber)


def label_citations(text: str | Markup, label: str) -> tuple[str, int]:
    """Apply a checker label to the plain source numbers in a checked sentence."""
    count = 0

    def mark(prose: str) -> str:
        nonlocal count
        marked, added = _SINGLE.subn(rf"[{label}:\1]", prose)
        count += added
        return marked

    return parse_markup(text).map_prose(mark), count


def _masked_inline(markup: Markup) -> str:
    """Keep source offsets while hiding inline punctuation from sentence matching."""
    chunks = []
    last = 0
    for start, end in markup.inline:
        chunks.extend((markup.text[last:start], "\x00" * (end - start)))
        last = end
    return "".join((*chunks, markup.text[last:]))


def leads_in(text: str, limit: int) -> list[tuple[int, int, str]]:
    """The first ``limit`` claims marked as the model's own: (start, marker, claim)."""
    if UNSOURCED not in text or limit <= 0:
        return []
    markup = parse_markup(text)
    visible = _masked_inline(markup)
    found: list[tuple[int, int, str]] = []
    for start, stop in sentence_spans(markup):
        for marker in _OWN.finditer(visible, start, stop):
            if len(found) >= limit:
                return found
            end = marker.start()
            if not _NO_PROSE.fullmatch(_MARKERS.sub("", visible[start:end])):
                claim = strip_citations(markup.prose_slice(start, end), bare_only=True).strip()
                found.append((start, end, claim))
            start = marker.end()
    return found


def sentence_spans(text: str | Markup, *, groups: Sequence[Span] | None = None) -> list[Span]:
    """The source positions of prose sentences, or prose within supplied claim groups."""
    markup = parse_markup(text)
    visible = _masked_inline(markup)
    spans: list[Span] = []

    def add(start: int, end: int) -> None:
        if not _NO_PROSE.fullmatch(_MARKERS.sub("", visible[start:end])):
            spans.append((start, end))

    for begin, limit in markup.prose_ranges(include_inline=True):
        if groups is not None:
            for lower, upper in groups:
                start, end = max(lower, begin), min(upper, limit)
                if start < end:
                    add(start, end)
            continue
        start = begin
        for match in _SENTENCE_OR_ROW.finditer(visible, begin, limit):
            if match.end() <= start:
                continue
            end = match.end()
            # A marker may continue on the next line, but a blank line ends the paragraph.
            after = match.start() if match[0].startswith(("\r", "\n")) else end
            if trailing := _CONTINUING.match(visible, after, limit):
                end = trailing.end()
            add(start, end)
            start = end
        add(start, limit)
    return spans


def _marked_unsourced(text: str | Markup) -> str:
    """Remove a sentence's citations and put ``[?]`` before its closing punctuation."""
    markup = parse_markup(text)
    if not (spans := sentence_spans(markup)):
        return markup.text
    start, end = spans[-1]
    clean = strip_citations(
        markup.prose_slice(start, end), include_unsourced=False, trim_space=True,
    )
    at = len(clean.rstrip(" \t\r\n.!?。！？"))
    return f"{markup.text[:start]}{clean[:at]} {UNSOURCED}{clean[at:]}{markup.text[end:]}"


def _plain(sentence: str | Markup, *, trim_space: bool = False) -> str:
    """A sentence with its citation markers and ``[?]`` taken off, on one line."""
    return " ".join(strip_citations(sentence, trim_space=trim_space).split())


def _aligned_notes(before: str, notes: Sequence[str], after: str) -> tuple[str, ...]:
    """Keep the notes corresponding to the partly supported markers still present."""
    left = [m[2] for m in _matches(parse_markup(after), _CITED) if m[1] == "p"]
    kept: list[str] = []
    previous = (m[2] for m in _matches(parse_markup(before), _CITED) if m[1] == "p")
    for index, number in enumerate(previous):
        if len(kept) < len(left) and number == left[len(kept)]:
            kept.append(notes[index] if index < len(notes) else "")
    return tuple(kept)
