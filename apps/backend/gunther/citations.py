"""Citation markers and the boundaries of citable text."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


UNSOURCED = "[?]"
CITATION_GROUP = re.compile(r"\[(\d+(?:\s*[,，、]\s*\d+)*)\]")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)|[。！？\n]")
_MARKDOWN_ROW = r"^[ \t]*\|[^\n]*"
MARKDOWN_TABLE = re.compile(rf"(?:{_MARKDOWN_ROW}(?:\n|\Z))+", re.M)
# What the checker adds: [i:3] an inference from source 3, [p:3] partly supported by it,
# [d:3] disputed by it (the look-up found it contradicted).
MARKED = re.compile(r"\[(i|p|d):(\d+)\]")
ANY_CITATION = re.compile(r"\[(?:(i|p|d):)?(\d+)\]")
_ANY_GROUP = re.compile(r"\[(?:(i|p|d):(\d+)|(\d+(?:\s*[,，、]\s*\d+)*))\]")
_MARK = r"\[(?:(?:i|p|d):\d+|\d+(?:\s*[,，、]\s*\d+)*)\]"
_RUN = re.compile(rf"{_MARK}(?:[ \t]*{_MARK})*")
_BARE = re.compile(r"\[(?:(i|p|d):)?(\d+(?:\s*[,，、]\s*\d+)*)\]")
_TRAILING = re.compile(rf"[ \t]*(?:{_MARK}|\[\?\])(?:[ \t]*(?:{_MARK}|\[\?\]))*")
_NO_PROSE = re.compile(r"^[\s|:\-–—*_#>=+`~]*$")


def check_citations(content: str, valid: set[int]) -> tuple[str, list[int]]:
    """Drop citations to sources that do not exist; ``[2, 5]`` becomes ``[2][5]``.

    Numbers are the pool's and never change. Returns the text and the numbers cited,
    in order of first use. ``[i:n]``, ``[p:n]`` and ``[d:n]`` count like ``[n]`` and keep
    their label.
    """

    order: list[int] = []

    def see(number: int) -> bool:
        if number not in valid:
            return False
        if number not in order:
            order.append(number)
        return True

    def replace(match: re.Match[str]) -> str:
        if match.group(1):
            return match.group(0) if see(int(match.group(2))) else ""
        kept = []
        for part in re.split(r"[,，、]", match.group(3)):
            if see(int(part)):
                kept.append(f"[{int(part)}]")
        return "".join(kept)

    text = _ANY_GROUP.sub(replace, content)
    # A citation that vanished can leave a doubled space or a space before punctuation.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.;:!?，。；：！？])", r"\1", text)
    return text, order


def collapse_citations(text: str) -> str:
    """Make each run of adjacent citations clean: ``[2] [3][2]`` becomes ``[2][3]``.

    Markers separated only by spaces form a run; a number is kept once, with the label
    of its first occurrence (``[i:2] [2]`` becomes ``[i:2]``).
    """

    def tidy(run: re.Match[str]) -> str:
        first: dict[int, str] = {}
        for marker in _BARE.finditer(run.group(0)):
            for part in re.split(r"[,，、]", marker.group(2)):
                first.setdefault(int(part), marker.group(1) or "")
        return "".join(f"[{label}:{n}]" if label else f"[{n}]" for n, label in first.items())

    return _RUN.sub(tidy, text)


def renumber_citations(text: str, used: Sequence[int] | Mapping[int, int]) -> str:
    """Show the pool's numbers as 1, 2, 3 in the order the answer first cites them.

    ``used`` is the pool numbers in that order (from ``check_citations``); the source
    list of the answer follows the same order, so a number in the text is the number
    of the source beside it. An explicit map can keep numbers from a saved version.
    """

    place = used if isinstance(used, Mapping) else {ref: n for n, ref in enumerate(used, 1)}

    def renumber(match: re.Match[str]) -> str:
        label = f"{match.group(1)}:" if match.group(1) else ""
        return f"[{label}{place.get(int(match.group(2)), match.group(2))}]"

    return ANY_CITATION.sub(renumber, text)


def leads_in(text: str, limit: int) -> list[tuple[int, int, str]]:
    """The first ``limit`` claims marked as the model's own: (start, end, claim).

    ``end`` is the position of the marker; a claim runs from the end of the previous
    sentence (or marker) to it.
    """

    found: list[tuple[int, int, str]] = []
    floor = 0
    for marker in re.finditer(re.escape(UNSOURCED), text):
        start = max([floor, *(m.end() for m in SENTENCE_END.finditer(text, floor, marker.start()))])
        claim = CITATION_GROUP.sub("", text[start : marker.start()]).strip()
        floor = marker.end()
        if claim and len(found) < limit:
            found.append((start, marker.start(), claim))
    return found


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Where each sentence of an answer is: (start, end) in ``text``.

    A citation group or ``[?]`` right after a sentence end belongs to the sentence
    before it. A Markdown table row stays together so its labels, values and citations
    can be checked as one claim. Empty spans and Markdown rules are left out.
    """

    spans: list[tuple[int, int]] = []
    start = 0

    def add(end: int) -> None:
        prose = re.sub(rf"{_MARK}|\[\?\]", "", text[start:end])
        if not _NO_PROSE.match(re.sub(r"[.!?。！？]", "", prose)):
            spans.append((start, end))

    for match in re.finditer(rf"{_MARKDOWN_ROW}|{SENTENCE_END.pattern}", text, re.M):
        if match.end() <= start:
            continue
        end = match.end()
        if match.group(0) != "\n" and (trailing := _TRAILING.match(text, end)):
            end = trailing.end()
        add(end)
        start = end
    if start < len(text):
        add(len(text))
    return spans


def _marked_unsourced(span: str) -> str:
    """A sentence with its citations taken off and ``[?]`` put at its end, before the stop."""

    clean = re.sub(rf"[ \t]*{_MARK}", "", span)
    tail = re.search(r"[\s.!?。！？]*$", clean)
    at = tail.start() if tail else len(clean)
    return f"{clean[:at]} {UNSOURCED}{clean[at:]}"


def _plain(sentence: str) -> str:
    """A sentence with its citation markers and ``[?]`` taken off, on one line."""

    return " ".join(re.sub(rf"{_MARK}|\[\?\]", "", sentence).split())


def _aligned_notes(before: str, notes: Sequence[str], after: str) -> tuple[str, ...]:
    """The notes for the ``[p:n]`` markers that are left in ``after``.

    ``notes`` go with the markers of ``before`` in order. Later steps only remove
    markers (a number that is not in the pool), and all of one number at once.
    """

    left = [m.group(2) for m in MARKED.finditer(after) if m.group(1) == "p"]
    kept: list[str] = []
    for index, marker in enumerate(m for m in MARKED.finditer(before) if m.group(1) == "p"):
        if len(kept) < len(left) and marker.group(2) == left[len(kept)]:
            kept.append(notes[index] if index < len(notes) else "")
    return tuple(kept)
