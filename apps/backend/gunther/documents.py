"""Canonical documents and their derived evidence passages.

The canonical text is never shortened for retrieval. All offsets below are
end-exclusive Unicode code points; an element belongs to exactly one revision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gunther.content import ParsedBlock, parse_content, passage_flags

MAX_DOCUMENT_CHARS = 1_000_000
MAX_ELEMENTS = 10_000
CHUNK_CHARS = 600
REFERENCE_LABEL = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)", re.S)
NUMERIC_MENTION = re.compile(r"\[(\d+(?:\s*[,，;–—-]\s*\d+)*)\]")
AUTHOR_YEAR = re.compile(
    r"\b([A-Z][A-Za-z’'-]+)(?:\s+et\s+al\.?)?\s*[,（(]\s*((?:19|20)\d{2}[a-z]?)\s*[)）]?"
)


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
                anchor = {**element.anchor, "elementCharStart": start, "elementCharEnd": stop}
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


def reference_data(elements: list[ParsedBlock]) -> tuple[list[dict], list[dict]]:
    """Conservative offline reference resolution. Unknown/ambiguous links stay explicit."""
    entries: list[dict] = []
    numeric: dict[str, list[int]] = {}
    authors: dict[tuple[str, str], list[int]] = {}
    for ordinal, element in enumerate(elements):
        if element.kind != "reference" and not element.payload.get("reference"):
            continue
        match = REFERENCE_LABEL.match(element.text)
        label = (match[1] or match[2]) if match else ""
        raw = match[3] if match else element.text
        display = element.text
        if not match and (marker := str(element.payload.get("marker") or "").strip()):
            numeric_marker = re.fullmatch(r"(?:\[(\d+)\]|(\d+)[.)]?)", marker)
            if numeric_marker:
                label = numeric_marker[1] or numeric_marker[2]
                # The parser keeps a printed list marker apart from the text.
                # Preserve it in the bibliography; element/chunk offsets stay exact.
                display = f"{marker} {element.text}"
        year = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", raw)
        author = re.search(r"\b([A-Z][A-Za-z’'-]+)", raw)
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
            authors.setdefault((author[1].casefold(), year[1]), []).append(index)
    mentions: list[dict] = []
    for ordinal, element in enumerate(elements):
        if element.kind in {"reference", "heading", "equation"}:
            continue
        for match in NUMERIC_MENTION.finditer(element.text):
            _mention(mentions, ordinal, match, _numeric_groups(match[1], numeric), "numeric-label")
        for match in AUTHOR_YEAR.finditer(element.text):
            _mention(
                mentions,
                ordinal,
                match,
                [authors.get((match[1].casefold(), match[2]), [])],
                "author-year",
            )
        for marker in element.payload.get("citationMarkers", []):
            start, stop = marker.get("start"), marker.get("end")
            if (
                not isinstance(start, int)
                or not isinstance(stop, int)
                or not 0 <= start < stop <= len(element.text)
            ):
                continue
            raw = element.text[start:stop]
            if match := re.fullmatch(r"\d+(?:\s*[,;–—-]\s*\d+)*", raw):
                _mention(
                    mentions,
                    ordinal,
                    match,
                    _numeric_groups(raw, numeric),
                    "numeric-superscript",
                    position=(start, stop),
                )
    return entries, mentions


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
