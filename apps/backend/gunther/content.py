"""Lossless evidence blocks for text, OCR pages and timestamped transcripts.

The source body is never modified. Offsets refer to that representation, while
page/region/time anchors refer to the original media when supplied by a parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gunther.pdf_text import REFERENCE_SECTIONS, section_name


@dataclass
class ParsedBlock:
    text: str
    kind: str = "paragraph"
    parent: int | None = None
    headings: list[str] = field(default_factory=list)
    anchor: dict[str, object] = field(default_factory=dict)
    locator: str = "Source text"
    payload: dict[str, object] = field(default_factory=dict)


STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "of",
        "on",
        "or",
        "our",
        "should",
        "that",
        "the",
        "their",
        "these",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "evidence",
        "supports",
        "explain",
        "tell",
        "about",
        "please",
    ]
)
_CJK = re.compile(r"[\u3400-\u9fff]+")
# A block is a passage of whole sentences up to this long; a longer sentence is
# sliced. About 100-150 English words: one idea, and within E5's 512 tokens.
PASSAGE_CHARS = 600
SLICE_CHARS = 480
# Shorter passages stay searchable by keyword but get no vector.
MIN_EMBED_CHARS = 24
MIN_EMBED_WORDS = 6


def passage_flags(text: str, in_references: bool) -> dict[str, object]:
    """What a passage is not good for.

    A reference list entry answers no question, so it is kept out of search
    (the reader still shows it). A number-heavy fragment (a table cell, an axis
    label) is still found by keyword but gets no vector: its embedding would
    resemble everything a little.
    """

    if in_references:
        return {"reference": True, "noEmbed": True}
    letters = sum(character.isalpha() for character in text)
    # Words of two or more letters; two Chinese characters count as a word.
    words = len(re.findall(r"[^\W\d_]{2,}", _CJK.sub(" ", text))) + len(
        "".join(_CJK.findall(text))
    ) // 2
    if (
        len(text) < MIN_EMBED_CHARS
        # A real sentence can be short; fragments without one need more words.
        or words < (3 if re.search(r"[.!?。！？]", text) else MIN_EMBED_WORDS)
        or letters < 0.6 * len(text.replace(" ", ""))
        or _is_label(text)
    ):
        return {"noEmbed": True}
    return {}


def _is_label(text: str) -> bool:
    """Figure labels, table headers, emails and ALL-CAPS lines: text without sentences."""

    if "@" in text or re.search(r"https?://|www\.", text):
        return len(text) < 200
    if _CJK.search(text) or re.search(r"[.!?。！？]", text):
        return False
    tokens = re.findall(r"[^\W_][\w\-/]*", text)
    if not tokens:
        return True
    capitalised = sum(token[0].isupper() or token[0].isdigit() for token in tokens)
    short = sum(len(token) <= 3 for token in tokens)
    return capitalised > 0.4 * len(tokens) or short > 0.5 * len(tokens)


def search_tokens(value: str) -> list[str]:
    """Versioned, identical query/index tokenization; no external dictionary needed.

    CJK bigrams support unsegmented Chinese, including two-character terms. Latin
    identifiers and hyphenated concepts are retained (cell-type is not just cell).
    """
    tokens: list[str] = []
    for word in re.findall(r"[\w]+(?:[-–—][\w]+)*", value.casefold()):
        runs = _CJK.findall(word)
        if runs:
            for run in runs:
                tokens.extend(run[i : i + 2] for i in range(max(1, len(run) - 1)))
            word = _CJK.sub(" ", word)
        for part in word.split():
            part = re.sub(r"[-–—]+", "", part)
            if part not in STOPWORDS and len(part) > 1:
                tokens.append(part)
    return tokens


def parse_content(content: str) -> list[ParsedBlock]:
    blocks: list[ParsedBlock] = []
    stack: list[tuple[int, int, str]] = []
    page: int | None = None
    region: dict[str, object] = {}
    offset = 0
    skip_metadata = content.startswith("# Original file\n")
    metadata_section = skip_metadata
    for raw in content.splitlines(keepends=True):
        line = raw.strip()
        start = offset + len(raw) - len(raw.lstrip())
        offset += len(raw)
        if not line:
            continue
        page_match = re.fullmatch(r"<!-- gunther:page=(\d+) -->", line)
        if page_match:
            page = int(page_match[1])
            region = {}
            continue
        region_match = re.fullmatch(
            r"<!-- gunther:ocr-region=(\d+),(\d+),(\d+),(\d+) unit=ppm "
            r"provider=([\w.-]+)(?: confidence=([\d.]+))? -->",
            line,
        )
        if region_match:
            region = {
                "bboxPpm": [int(region_match[i]) for i in range(1, 5)],
                "provider": region_match[5],
            }
            if region_match[6]:
                region["confidence"] = float(region_match[6])
            continue
        if line.startswith("<!--"):
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level, title = len(heading[1]), heading[2]
            if skip_metadata:
                if title == "Extracted content":
                    skip_metadata = metadata_section = False
                    stack.clear()
                    continue
                metadata_section = title not in {"Your context", "Extracted content"}
                if title in {"Original file", "OCR provenance", "Processing note"}:
                    continue
            if metadata_section:
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else None
            headings = [item[2] for item in stack] + [title]
            blocks.append(
                ParsedBlock(
                    title,
                    "heading",
                    parent,
                    headings,
                    {"charStart": start, "charEnd": offset},
                    title,
                )
            )
            stack.append((level, len(blocks) - 1, title))
            continue
        if metadata_section:
            continue
        anchor: dict[str, object] = dict(region)
        if page is not None:
            anchor["page"] = page
        headings = [item[2] for item in stack]
        locator = f"Page {page}" if page else (headings[-1] if headings else "Source text")
        timestamp = re.match(r"^\[?(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?\]?\s+", line)
        kind = "paragraph"
        if timestamp:
            anchor["startSeconds"] = (
                int(timestamp[1] or 0) * 3600 + int(timestamp[2]) * 60 + int(timestamp[3])
            )
            locator = timestamp[0].strip()
            kind = "transcript"
        elif line.count("|") >= 2:
            kind = "table"
        # Preserve exact substrings. A block is a passage of whole sentences, up to
        # PASSAGE_CHARS: long enough to read and embed on its own, short enough to
        # quote. A sentence longer than SLICE_CHARS is sliced, so a giant paragraph
        # cannot disappear at truncation.
        pieces: list[tuple[int, int]] = []
        for match in re.finditer(r".+?(?:[.!?。！？](?=\s|$)|$)", line):
            for index in range(match.start(), match.end(), SLICE_CHARS):
                pieces.append((index, min(index + SLICE_CHARS, match.end())))
        passages: list[tuple[int, int]] = []
        for piece_start, piece_end in pieces:
            if passages and piece_end - passages[-1][0] <= PASSAGE_CHARS:
                passages[-1] = (passages[-1][0], piece_end)
            else:
                passages.append((piece_start, piece_end))
        in_references = any(section_name(title) in REFERENCE_SECTIONS for title in headings)
        for passage_start, passage_end in passages:
            piece = line[passage_start:passage_end]
            text = piece.strip()
            if not text:
                continue
            char_start = start + passage_start + len(piece) - len(piece.lstrip())
            blocks.append(
                ParsedBlock(
                    text,
                    kind,
                    stack[-1][1] if stack else None,
                    headings.copy(),
                    {**anchor, "charStart": char_start, "charEnd": char_start + len(text)},
                    locator,
                    passage_flags(text, in_references),
                )
            )
    _mark_front_matter(blocks)
    return blocks


def _mark_front_matter(blocks: list[ParsedBlock]) -> None:
    """A paper's title, authors and affiliations, before its abstract, get no vector.

    The paper's own vector (title and abstract) already stands for them; alone,
    they resemble every question a little.
    """

    for index, block in enumerate(blocks[:60]):
        if (block.anchor.get("page") or 1) > 2:
            return
        if block.kind == "heading" and section_name(block.text) in {"abstract", "摘要"}:
            for earlier in blocks[:index]:
                if earlier.kind != "heading":
                    earlier.payload = {**earlier.payload, "noEmbed": True}
            return
