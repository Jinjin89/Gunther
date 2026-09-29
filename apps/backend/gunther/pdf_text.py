"""Turn a PDF page's printed lines back into paragraphs and sections.

PDF text arrives one printed line at a time: a sentence is cut wherever the
column ended, and words are hyphenated across lines. Searching, embedding and
quoting all need whole paragraphs, so wrapped lines are joined here, before the
text becomes a source body. Section headings ("3.1 Encoder", "References")
become Markdown headings, so every passage knows which section it is in; list
items stay on lines of their own. Only line breaks, line-end hyphens and
typographic ligatures ("ﬁ") change; every word is kept.
"""

from __future__ import annotations

import re
from statistics import median
from typing import Any

_TERMINAL = re.compile(r"[.!?。！？:：;；]['\"”’)\]]*$")
_LIST_ITEM = re.compile(r"^(?:[•●▪◦‣∙·\-–—*]\s+|\(?\d{1,2}[.)]\s+|\(?[a-z][.)]\s+)")
# "3", "3.2.1", "IV." or "A." then a capitalised title; "2021." is a year, not a section.
_NUMBERING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+|^(?:[IVX]{1,5}|[A-Z])\.\s+")
_NAMED_HEADINGS = frozenset(
    {
        "abstract", "summary", "introduction", "background", "related work", "methods",
        "method", "materials and methods", "results", "discussion", "conclusion",
        "conclusions", "references", "bibliography", "acknowledgements",
        "acknowledgments", "appendix", "keywords",
        "摘要", "引言", "方法", "结果", "讨论", "结论", "参考文献", "致谢", "关键词",
    }
)
REFERENCE_SECTIONS = frozenset({"references", "bibliography", "参考文献"})
_CJK = re.compile(r"[　-鿿＀-￯]")
_LIGATURES = str.maketrans(
    {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
     "\ufb05": "st", "\ufb06": "st"}
)
_SMALL_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or",
     "the", "to", "via", "vs", "with"}
)


def section_name(line: str) -> str:
    """A heading without its number, lower-cased: "7. References" -> "references"."""

    return _NUMBERING.sub("", line).casefold().strip(" .:：")


def looks_like_heading(line: str) -> bool:
    if section_name(line) in _NAMED_HEADINGS and len(line) <= 40:
        return True
    numbering = _NUMBERING.match(line)
    if not numbering or len(line) > 80 or len(line.split()) > 12:
        return False
    title = line[numbering.end():]
    return (
        bool(re.match(r"[A-Z㐀-鿿]", title))
        and not _TERMINAL.search(title)
        and ". " not in title
        and any(character.isalpha() for character in title)
    )


def heading_level(line: str) -> int:
    """Markdown depth: "3" is 2, "3.2" is 3, "3.2.1" and deeper are 4."""

    numbering = _NUMBERING.match(line)
    if numbering and numbering[1]:
        return min(4, 2 + numbering[1].count("."))
    return 2


def _join(left: str, right: str, vocabulary: set[str]) -> str:
    # A line-end hyphen either splits a word ("mod-" "els") or belongs to it
    # ("open-" "source"). The document's own words decide; unknown stays hyphenated.
    if re.search(r"[A-Za-z]-$", left) and re.match(r"[a-z]", right):
        head = re.search(r"([A-Za-z]+)-$", left)
        tail = re.match(r"[a-z]+", right)
        if head and tail and (head[1] + tail[0]).casefold() in vocabulary:
            return left[:-1] + right
        return left + right
    if _CJK.search(left[-1:]) and _CJK.search(right[:1]):
        return left + right
    return f"{left} {right}"


def vocabulary_of(text: str) -> set[str]:
    return {word.casefold() for word in re.findall(r"[A-Za-z]+", text.translate(_LIGATURES))}


def reflow(page: str, vocabulary: set[str] | None = None) -> str:
    """Join the soft-wrapped lines of one page; paragraphs end with a newline.

    ``vocabulary`` is every word of the document, to rejoin hyphenated words.
    """

    lines = [line.strip() for line in page.translate(_LIGATURES).splitlines()]
    widths = [len(line) for line in lines if len(line) > 20]
    # A line well short of the column width, ending a sentence, ends a paragraph.
    full = median(widths) if len(widths) >= 3 else 0
    vocabulary = vocabulary if vocabulary is not None else vocabulary_of(page)
    paragraphs: list[str] = []
    current = ""
    previous = ""
    for line in lines:
        if not line:
            if current:
                paragraphs.append(current)
            current = previous = ""
            continue
        if line.startswith("<!--"):  # Gunther's own page and region markers
            if current:
                paragraphs.append(current)
            paragraphs.append(line)
            current = previous = ""
            continue
        starts_new = (
            not current
            or looks_like_heading(line)
            or looks_like_heading(previous)
            or bool(_LIST_ITEM.match(line))
            or (
                full > 0
                and (
                    # the last line of a paragraph
                    (bool(_TERMINAL.search(previous)) and len(previous) < 0.8 * full)
                    # a title, author or affiliation line (single words are
                    # figure labels, which read best run together)
                    or (
                        len(previous) < 0.6 * full
                        and " " in previous
                        and not re.search(r"[-,]$", previous)
                    )
                )
            )
        )
        if starts_new:
            if current:
                paragraphs.append(current)
            current = line
        else:
            current = _join(current, line, vocabulary)
        previous = line
    if current:
        paragraphs.append(current)
    out = []
    for text in paragraphs:
        if looks_like_heading(text):
            out.append(f"{'#' * heading_level(text)} {text}")
        elif text.startswith("#"):  # a printed "#" is text, not a Markdown heading
            out.append("\\" + text)
        else:
            out.append(text)
    return "\n".join(out)


def _title_case(title: str) -> str:
    """Typeset titles are often ALL CAPS or small caps; read them in title case."""

    if title.upper() != title or not any(c.isalpha() for c in title):
        return title
    # Small caps come apart as "W ORDS"; a lone capital (not A or I) joins the next word.
    title = re.sub(r"\b([B-HJ-Z]) (?=[A-Z]{2,})", r"\1", title)
    words = title.split(" ")
    cased = []
    for index, word in enumerate(words):
        lower = word.lower()
        if any(c.isdigit() for c in word):
            cased.append(word)
        elif index and lower in _SMALL_WORDS and not cased[-1].endswith(":"):
            cased.append(lower)
        else:
            cased.append(lower[:1].upper() + lower[1:])
    return " ".join(cased)


def title_by_font(reader: Any) -> str | None:
    """The largest upright type on the first page: a paper's title, usually.

    The title is the run of text in the largest size, together with smaller
    small-caps letters on the same lines; it ends at the first line in a
    smaller size (the authors). Figure labels in the same size further down
    the page are not part of it.
    """

    parts: list[tuple[float, float, str]] = []

    def visit(text: str, cm: Any, tm: Any, _font: Any, size: float) -> None:
        # Rotated text (arXiv's margin stamp) is never the title.
        if text.strip() and abs(tm[1]) < 0.01 and abs(tm[2]) < 0.01:
            scale = abs(tm[3] * cm[3] or 1)
            parts.append((round(size * scale, 1), round(tm[5] * cm[3] + cm[5], 1), text))

    try:
        reader.pages[0].extract_text(visitor_text=visit)
    except Exception:
        return None
    if not parts:
        return None
    top = max(size for size, _, _ in parts)
    body = median(size for size, _, _ in parts)
    if top < body * 1.15:  # nothing stands out: no typeset title
        return None
    start = next(index for index, (size, _, _) in enumerate(parts) if size >= top - 0.5)
    title_lines = {y for size, y, _ in parts if size >= top - 0.5}
    pieces: list[str] = []
    last_y = None
    for size, y, text in parts[start:]:
        on_title_line = size >= top - 0.5 or (size >= 0.7 * top and y in title_lines)
        if not on_title_line:
            break
        if last_y is not None and y != last_y and pieces and not pieces[-1].endswith(" "):
            pieces.append(" ")
        pieces.append(text.replace("\n", " "))
        last_y = y
    title = re.sub(r"\s+", " ", "".join(pieces).translate(_LIGATURES)).strip()
    title = re.sub(r"\s+([:,;])", r"\1", title)
    return _title_case(title)[:300] or None
