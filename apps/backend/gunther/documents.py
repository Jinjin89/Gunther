"""Canonical documents and their derived evidence passages.

The canonical text is never shortened for retrieval. All offsets below are
end-exclusive Unicode code points; an element belongs to exactly one revision.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from gunther.content import ParsedBlock, parse_content, passage_flags

MAX_DOCUMENT_CHARS = 1_000_000
MAX_ELEMENTS = 10_000
CHUNK_CHARS = 600
REFERENCE_LABEL = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[.)]|\((\d+)\))\s*(.+)", re.S)
# "12 Smith J": a bare number, only trusted when the entries count up with it.
BARE_LABEL = re.compile(r"^\s*(\d{1,4})\s+(?=[^\W\d_])(.+)", re.S)
NUMERIC_MENTION = re.compile(r"\[(\d+(?:\s*[,，;–—-]\s*\d+)*)\]")
# "Smith (2020)", "Smith et al., 2020", "Smith and Lee 2020a", "Smith & Lee, 2020".
_NAME = r"[^\W\d_][^\W\d_’'-]*(?:[’'-][^\W\d_]+)*"
AUTHOR_YEAR = re.compile(
    rf"\b(?=[^\W\d_])(?P<name>(?:(?:van|von|de|da|di|le|la|du)\s+)?{_NAME})"
    rf"(?:\s+(?:and|&)\s+{_NAME}|\s+et\s+al\.?)?\s*[,（(]?\s*"
    r"(?P<year>(?:19|20)\d{2}[a-z]?)\b\s*[)）]?"
)
# A raised number that cites: digits, commas and ranges, not an exponent.
SUPERSCRIPT_MENTION = re.compile(r"\d{1,4}(?:\s*[,;–—-]\s*\d{1,4})*")


def _numeric_groups(raw: str, numeric: dict[str, list[int]]) -> list[list[int]]:
    labels: list[str] = []
    for part in re.split(r"[,，;]", raw):
        ends = re.split(r"[–—-]", part.strip())
        if len(ends) == 2 and int(ends[0]) <= int(ends[1]) <= int(ends[0]) + 50:
            labels.extend(str(n) for n in range(int(ends[0]), int(ends[1]) + 1))
        elif len(ends) == 1:
            labels.append(str(int(ends[0])))
    return [numeric.get(label, []) for label in labels]


@dataclass
class DocumentAsset:
    key: str
    data: bytes
    media_type: str = "image/png"
    name: str = "figure.png"


@dataclass
class ParsedDocument:
    elements: list[ParsedBlock]
    assets: list[DocumentAsset] = field(default_factory=list)
    manifest: dict[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        if (
            len(self.elements) > MAX_ELEMENTS
            or sum(len(e.text) for e in self.elements) > MAX_DOCUMENT_CHARS
        ):
            raise ValueError("Document exceeds the canonical representation budget")
        for ordinal, element in enumerate(self.elements):
            if element.parent is not None and not 0 <= element.parent < ordinal:
                raise ValueError("Document parents must precede their children")

    def chunks(self) -> list[ParsedBlock]:
        self.validate()
        blocks: list[ParsedBlock] = []
        first: dict[int, int] = {}
        for ordinal, element in enumerate(self.elements):
            first[ordinal] = len(blocks)
            starts = range(0, len(element.text), CHUNK_CHARS) if element.text else [0]
            for start in starts:
                stop = min(start + CHUNK_CHARS, len(element.text))
                flags = passage_flags(element.text[start:stop], element.kind == "reference")
                # Tables need semantic context too; do not discard numeric rows.
                if element.kind == "table":
                    flags.pop("noEmbed", None)
                payload = {**element.payload, **flags, "elementOrdinal": ordinal}
                anchor = {
                    **element.anchor, **_chunk_regions(element.anchor, start, stop),
                    "elementCharStart": start, "elementCharEnd": stop,
                }
                if isinstance(element.anchor.get("charStart"), int):
                    anchor.update(
                        charStart=element.anchor["charStart"] + start,
                        charEnd=element.anchor["charStart"] + stop,
                    )
                blocks.append(
                    ParsedBlock(
                        element.text[start:stop],
                        element.kind,
                        first.get(element.parent) if element.parent is not None else None,
                        element.headings.copy(),
                        anchor,
                        element.locator,
                        payload,
                    )
                )
        return blocks


def _chunk_regions(anchor: dict, start: int, stop: int) -> dict:
    """The page regions holding a passage's own text, when the element records them.

    A paragraph continued on the next page cites that page for its second half; a
    caption printed on another page than its figure cites the caption's page.
    """
    spans = anchor.get("spans") or []
    held = [
        span for span in spans
        if isinstance(span.get("textStart"), int) and isinstance(span.get("textEnd"), int)
        and span["textStart"] < max(stop, start + 1) and span["textEnd"] > start
    ]
    if not held:
        return {}
    pages = sorted({span["page"] for span in held if isinstance(span.get("page"), int)})
    found: dict = {"textSpans": held, "pages": pages}
    if all(isinstance(span.get("textStart"), int) for span in spans) and pages:
        # A text element: the passage's own first region is where to point.
        found["page"] = held[0].get("page", anchor.get("page"))
        if held[0].get("bboxPpm"):
            found["bboxPpm"] = held[0]["bboxPpm"]
    return found


def text_document(content: str) -> ParsedDocument:
    """Keep paragraph boundaries and reassemble only slices of the same original line."""
    elements: list[ParsedBlock] = []
    mapped: dict[int, int] = {}
    last_end: int | None = None
    for ordinal, block in enumerate(parse_content(content)):
        if block.payload.get("reference"):
            block.kind = "reference"
        elif re.match(r"^\s*(?:[-*•]|\d+[.)])\s", block.text):
            block.kind = "list_item"
        start, stop = block.anchor.get("charStart"), block.anchor.get("charEnd")
        previous = elements[-1] if elements else None
        gap = (
            content[last_end:start]
            if isinstance(last_end, int) and isinstance(start, int)
            else "\n"
        )
        same = (
            previous is not None
            and (
                block.kind == previous.kind
                or (previous.kind == "list_item" and block.kind == "paragraph")
            )
            and previous.kind in {"paragraph", "reference", "list_item"}
            and block.headings == previous.headings
            and "\n" not in gap
        )
        if same:
            previous.text += gap + block.text
            previous.anchor["charEnd"] = stop
            mapped[ordinal] = len(elements) - 1
        else:
            block.parent = mapped.get(block.parent) if block.parent is not None else None
            mapped[ordinal] = len(elements)
            elements.append(block)
        last_end = stop if isinstance(stop, int) else None
    split_reference_entries(elements)
    return ParsedDocument(
        elements, manifest={"schema": 1, "method": "local-text", "images": "unavailable"}
    )


def split_reference_entries(elements: list[ParsedBlock]) -> None:
    """Split merged numeric entries without guessing author-year boundaries."""
    result: list[ParsedBlock] = []
    parents: dict[int, int] = {}
    for ordinal, element in enumerate(elements):
        element.parent = parents.get(element.parent) if element.parent is not None else None
        parents[ordinal] = len(result)
        if element.kind != "reference":
            result.append(element)
            continue
        starts = [m.start() for m in re.finditer(r"(?<!\w)\[\d+\]\s+(?=[A-Z㐀-鿿])", element.text)]
        if len(starts) < 2:
            starts = _numbered_chain(element.text)
        if len(starts) < 2:
            result.append(element)
            continue
        for start, stop in zip(starts, [*starts[1:], len(element.text)], strict=True):
            result.append(
                ParsedBlock(
                    element.text[start:stop].strip(),
                    "reference",
                    element.parent,
                    element.headings.copy(),
                    {**element.anchor, "referenceCharStart": start},
                    element.locator,
                    {**element.payload, "reference": True, "noEmbed": True},
                )
            )
    elements[:] = result


def _numbered_chain(text: str) -> list[int]:
    """Where "1. … 2. … 3. …" entries start, when the text opens with the first and the
    numbers count up one by one; a number inside an entry ("vol. 3. ") breaks no chain."""
    found = [
        (m.start(), int(m[1]))
        for m in re.finditer(r"(?<![\w.])(\d{1,3})\.\s+(?=[^\W\d_])", text)
    ]
    if not found or text[: found[0][0]].strip():
        return []
    chain = [found[0]]
    for start, number in found[1:]:
        if number == chain[-1][1] + 1:
            chain.append((start, number))
    return [start for start, _ in chain]


def reference_data(elements: list[ParsedBlock]) -> tuple[list[dict], list[dict]]:
    """Conservative offline reference resolution. Unknown/ambiguous links stay explicit.

    Entries are numbered by their printed label ("[3]", "3.", "(3)", a list marker, or
    a bare "3 " when the entries count up with it). Body mentions are bracketed
    numbers, raised numbers (a superscript span over digits, not an exponent) and
    author-year citations; each resolves only to entries it names unambiguously.
    """
    entries: list[dict] = []
    numeric: dict[str, list[int]] = {}
    authors: dict[tuple[str, str], list[int]] = {}
    found = [
        (ordinal, element) for ordinal, element in enumerate(elements)
        if element.kind == "reference" or element.payload.get("reference")
    ]
    bare = [BARE_LABEL.match(element.text) for _, element in found]
    numbers = [int(match[1]) for match in bare if match]
    counting = len(numbers) >= 3 and sum(
        1 for a, b in zip(numbers, numbers[1:], strict=False) if b == a + 1
    ) >= 0.8 * (len(numbers) - 1)
    for (ordinal, element), loose in zip(found, bare, strict=True):
        match = REFERENCE_LABEL.match(element.text)
        label = (match[1] or match[2] or match[3]) if match else ""
        raw = match[4] if match else element.text
        display = element.text
        if not match and counting and loose:
            label, raw = loose[1], loose[2]
        if not match and (marker := str(element.payload.get("marker") or "").strip()):
            numeric_marker = re.fullmatch(r"(?:\[(\d+)\]|\((\d+)\)|(\d+)[.)]?)", marker)
            if numeric_marker:
                label = numeric_marker[1] or numeric_marker[2] or numeric_marker[3]
                # The parser keeps a printed list marker apart from the text.
                # Preserve it in the bibliography; element/chunk offsets stay exact.
                display = f"{marker} {element.text}"
        year = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", raw)
        author = re.search(rf"(?:(?:van|von|de|da|di|le|la|du)\s+)?{_NAME}", raw)
        doi = re.search(r"10\.\d{4,9}/[^\s<>]+", raw)
        entry = {
            "element": ordinal,
            "label": label,
            "raw": display,
            "year": year[1] if year else None,
            "doi": doi[0].rstrip(".,;)") if doi else None,
        }
        index = len(entries)
        entries.append(entry)
        if label:
            numeric.setdefault(label, []).append(index)
        if author and year:
            authors.setdefault((_surname(author[0]), year[1]), []).append(index)
    mentions: list[dict] = []
    for ordinal, element in enumerate(elements):
        if element.kind in {"reference", "heading", "equation", "metadata"}:
            continue
        for match in NUMERIC_MENTION.finditer(element.text):
            _mention(mentions, ordinal, match, _numeric_groups(match[1], numeric), "numeric-label")
        if authors:
            for match in AUTHOR_YEAR.finditer(element.text):
                key = (_surname(match["name"]), match["year"])
                if key in authors:
                    _mention(mentions, ordinal, match, [authors[key]], "author-year")
        markers = [
            *element.payload.get("citationMarkers", []),
            *(span for span in element.payload.get("inlineSpans") or []
              if span.get("script") == "super"),
        ]
        taken: set[tuple[int, int]] = set()
        for marker in markers:
            start, stop = marker.get("start"), marker.get("end")
            if (
                not isinstance(start, int)
                or not isinstance(stop, int)
                or not 0 <= start < stop <= len(element.text)
                or (start, stop) in taken
            ):
                continue
            raw = element.text[start:stop]
            match = SUPERSCRIPT_MENTION.fullmatch(raw.strip())
            if not match or not numeric or _exponent(element.text, start, stop):
                continue
            groups = _numeric_groups(match[0], numeric)
            # A raised number that names no entry is a note or an exponent, not a citation.
            if not groups or not all(groups):
                continue
            taken.add((start, stop))
            _mention(
                mentions, ordinal, match, groups, "numeric-superscript", position=(start, stop)
            )
    return entries, mentions


def _surname(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


def _exponent(text: str, start: int, stop: int) -> bool:
    """A raised number that is a power, not a citation: after a digit, a bracket or an
    operator (10³, (x+1)², x^2), after a one- or two-letter unit (m², cm³), or opening
    the text (a note's own marker)."""
    before = text[:start]
    if not before.strip():
        return True
    if before[-1].isspace():
        return False
    if before[-1].isdigit() or before[-1] in ")]}^×·":
        return True
    word = re.search(r"(?<![^\W\d_])([^\W\d_]{1,2})$", before)
    return bool(word and stop - start <= 2)


def _mention(
    mentions: list[dict],
    ordinal: int,
    match: re.Match,
    groups: list[list[int]],
    method: str,
    position: tuple[int, int] | None = None,
) -> None:
    targets = list(dict.fromkeys(index for group in groups for index in group))
    status = (
        "resolved"
        if groups and all(len(group) == 1 for group in groups)
        else ("ambiguous" if any(len(group) > 1 for group in groups) else "unresolved")
    )
    mentions.append(
        {
            "element": ordinal,
            "start": position[0] if position else match.start(),
            "end": position[1] if position else match.end(),
            "raw": match[0],
            "targets": targets,
            "status": status,
            "method": method,
        }
    )
